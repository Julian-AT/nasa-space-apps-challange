"""Encoder training (T12A-04; PRD 11.4, 11.5). EXPERIMENTAL; run logs are not results.

::

    python -m terraluna_ml.train run --manifest <shards>/manifest.json --name main --seed 0 \
        --epochs 6 --windows-per-epoch 6000 --batch-size 128 --threads 4 --stem-stride 2

PRD 11.5 defaults are kept in :class:`RunConfig` (AdamW, lr = 1e-3 * batch / 1024, 2 warm-up
epochs then cosine, weight decay 0.05, batch 1024, bf16, 40 epochs); the reduced CPU run
overrides epochs, windows per epoch, batch size, precision (fp32) and stem stride, and every
override is written to config.json and metrics.json (``deviations_from_prd``).

Batches are body-balanced: each epoch draws an equal third of its windows from each body's
training rows (without replacement where the body has enough). Masked physics targets never
enter the loss. Validation diagnostics per epoch (``head_*``: validation physics-head MSE and
body-head accuracy) are diagnostics, not probe metrics; probe metrics come from evaluate.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
import torch
from pydantic import BaseModel, ConfigDict

from terraluna_ml import losses
from terraluna_ml.model import Encoder, TerralunaModel, parameter_count
from terraluna_ml.shards import BODIES, canonical_json, open_shards, sha256_path
from terraluna_pipeline.config import load_run_config

PRD_DEFAULTS: dict[str, Any] = {
    "epochs": 40,
    "batch_size": 1024,
    "warmup_epochs": 2,
    "weight_decay": 0.05,
    "precision": "bf16",
    "stem_stride": 1,
    "windows_per_epoch": None,
}


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    seed: int
    lambda_max: float = 0.3
    alpha_phys: float = 1.0
    use_vicreg: bool = True
    use_phys_head: bool = True
    use_body_head: bool = True
    bodies_included: tuple[str, ...] = BODIES
    epochs: int = 40
    batch_size: int = 1024
    warmup_epochs: int = 2
    weight_decay: float = 0.05
    base_lr_per_1024: float = 1e-3
    precision: Literal["bf16", "fp32"] = "bf16"
    stem_stride: int = 1
    windows_per_epoch: int | None = None
    sub_crop_m: float | None = None

    @property
    def peak_lr(self) -> float:
        return self.base_lr_per_1024 * self.batch_size / 1024

    def config_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.model_dump(mode="json")).encode()).hexdigest()

    def deviations(self) -> dict[str, dict[str, Any]]:
        dump = self.model_dump(mode="json")
        return {k: {"prd": v, "used": dump[k]} for k, v in PRD_DEFAULTS.items() if dump[k] != v}


def lr_at(step: int, steps_per_epoch: int, config: RunConfig) -> float:
    warm = config.warmup_epochs * steps_per_epoch
    total = config.epochs * steps_per_epoch
    if step < warm:
        return config.peak_lr * (step + 1) / warm
    t = (step - warm) / max(1, total - warm)
    return config.peak_lr * 0.5 * (1.0 + math.cos(math.pi * min(t, 1.0)))


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return os.environ.get("TERRALUNA_GIT_SHA", "unavailable")


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _epoch_indices(
    body: np.ndarray[Any, Any],
    rows: np.ndarray[Any, Any],
    n: int,
    rng: np.random.Generator,
    bodies: Sequence[int],
) -> np.ndarray[Any, Any]:
    per = n // len(bodies)
    picks = []
    for b in bodies:
        pool = rows[body[rows] == b]
        picks.append(rng.choice(pool, size=per, replace=pool.size < per))
    out = np.concatenate(picks)
    rng.shuffle(out)
    return out


def run(
    config: RunConfig, manifest: Path, out_dir: Path, *, threads: int, device: str = "cpu"
) -> Path:
    if config.sub_crop_m is not None:
        raise NotImplementedError("the 250 m sub-crop is not implemented (PRD-08)")
    allowed = load_run_config().value("ml.lambda_adv")
    assert isinstance(allowed, list)
    if config.lambda_max not in allowed:
        raise ValueError(f"lambda_max {config.lambda_max} is not in ml.lambda_adv {allowed}")
    torch.set_num_threads(threads)
    set_seed(config.seed)
    shards = open_shards(manifest)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config.json").write_text(
        canonical_json(config.model_dump(mode="json")), encoding="utf-8"
    )

    body_all = shards.column("body_index").astype(np.int64)
    split = shards.column("split")
    std, mask = shards.physics()
    body_ids = [BODIES.index(b) for b in config.bodies_included]
    train_rows = np.flatnonzero((split == "train") & np.isin(body_all, body_ids))
    val_rows = np.flatnonzero((split == "val") & np.isin(body_all, body_ids))
    rng = np.random.default_rng(config.seed)
    val_rows = np.sort(rng.choice(val_rows, size=min(2000, val_rows.size), replace=False))
    windows = shards.windows
    epoch_n = config.windows_per_epoch or int(train_rows.size)
    steps_per_epoch = epoch_n // config.batch_size
    model = TerralunaModel(stem_stride=config.stem_stride, use_phys_head=config.use_phys_head).to(
        device
    )
    opt = torch.optim.AdamW(model.parameters(), lr=config.peak_lr, weight_decay=config.weight_decay)
    gen = torch.Generator().manual_seed(config.seed)

    def batch(
        rows: np.ndarray[Any, Any],
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        order = np.sort(rows)
        x = torch.from_numpy(np.asarray(windows[order], dtype=np.float32))
        y = torch.from_numpy(np.nan_to_num(std[order], nan=0.0))
        m = torch.from_numpy(mask[order])
        b = torch.from_numpy(body_all[order])
        return x.to(device), y.to(device), m.to(device), b.to(device)

    metrics: dict[str, Any] = {
        "mock": shards.mock,
        "name": config.name,
        "seed": config.seed,
        "lambda_max": config.lambda_max,
        "use_phys_head": config.use_phys_head,
        "train_bodies": list(config.bodies_included),
        "config": config.model_dump(mode="json"),
        "config_hash": config.config_hash(),
        "data_manifest_sha256": shards.data_manifest_sha256,
        "git_sha": git_sha(),
        "hardware": {
            "device": device,
            "device_name": platform.processor() or platform.machine(),
            "torch": torch.__version__,
            "threads": threads,
            "cpu_count": os.cpu_count(),
            "accelerator": "none (CPU only, team rule)" if device == "cpu" else device,
        },
        "precision_used": "fp32",
        "deviations_from_prd": config.deviations(),
        "parameter_count_backbone": parameter_count(model.backbone),
        "n_train_rows": int(train_rows.size),
        "n_val_rows_used": int(val_rows.size),
        "steps_per_epoch": steps_per_epoch,
        "epochs": [],
        "probe_metrics_per_epoch": {
            "status": "Unavailable",
            "reason": "probes run once after training in evaluate.py (reduced scope)",
        },
        "gpu_hours": 0.0,
        "gpu_hours_note": "measured: 0 GPU hours, CPU only",
        "cpu_core_hours": 0.0,
        "wall_clock_s": 0.0,
    }
    started = time.time()
    step = 0
    total_steps = config.epochs * steps_per_epoch
    for epoch in range(config.epochs):
        model.train()
        t0 = time.time()
        idx = _epoch_indices(
            body_all, train_rows, steps_per_epoch * config.batch_size, rng, body_ids
        )
        sums: dict[str, float] = {}
        skipped = 0
        for s in range(steps_per_epoch):
            lr = lr_at(step, steps_per_epoch, config)
            for g in opt.param_groups:
                g["lr"] = lr
            lam = losses.dann_lambda(step / max(1, total_steps - 1), config.lambda_max)
            model.grl_lambda = lam if config.use_body_head else 0.0
            x, y, m, b = batch(idx[s * config.batch_size : (s + 1) * config.batch_size])
            v1, v2 = losses.make_views(x, gen), losses.make_views(x, gen)
            o1, o2 = model(v1), model(v2)
            terms: dict[str, torch.Tensor] = {}
            total = torch.zeros((), device=device)
            if config.use_vicreg:
                vic = losses.vicreg_loss(o1["z"], o2["z"])
                terms.update(vic)
                total = total + vic["vicreg"]
            if config.use_phys_head and config.alpha_phys > 0:
                p1, c1 = losses.masked_physics_mse(o1["phys"], y, m)
                p2, _ = losses.masked_physics_mse(o2["phys"], y, m)
                if c1 == 0:
                    skipped += 1
                phys = 0.5 * (p1 + p2)
                terms["phys"] = phys
                total = total + config.alpha_phys * phys
            if config.use_body_head:
                body_loss = 0.5 * (
                    losses.body_cross_entropy(o1["body"], b)
                    + losses.body_cross_entropy(o2["body"], b)
                )
                terms["body"] = body_loss
                total = total + body_loss  # lambda acts through the gradient reversal
            terms["total"] = total
            opt.zero_grad(set_to_none=True)
            total.backward()  # type: ignore[no-untyped-call]
            opt.step()
            for k, v in terms.items():
                sums[k] = sums.get(k, 0.0) + float(v.detach())
            step += 1
        train_s = time.time() - t0
        model.eval()
        with torch.no_grad():
            se, cnt, correct = 0.0, 0, 0
            for i in range(0, val_rows.size, 256):
                x, y, m, b = batch(val_rows[i : i + 256])
                h = model.backbone(x)
                if model.phys_head is not None:
                    p = model.phys_head(h)
                    mse, c = losses.masked_physics_mse(p, y, m)
                    se += float(mse) * c
                    cnt += c
                correct += int((model.body_head(h).argmax(1) == b).sum())
        entry = {
            "epoch": epoch,
            "train": {k: v / max(1, steps_per_epoch) for k, v in sums.items()},
            "lr_end": lr_at(step - 1, steps_per_epoch, config),
            "lambda_end": model.grl_lambda,
            "mask_skipped_batches": skipped,
            "head_val_phys_mse": se / cnt if cnt else None,
            "head_val_body_acc": correct / val_rows.size if val_rows.size else None,
            "epoch_train_s": round(train_s, 2),
            "throughput_windows_per_s_measured": round(
                steps_per_epoch * config.batch_size / train_s, 2
            ),
        }
        metrics["epochs"].append(entry)
        metrics["wall_clock_s"] = round(time.time() - started, 1)
        metrics["cpu_core_hours"] = round(metrics["wall_clock_s"] * threads / 3600, 4)
        _atomic_write(out_dir / "metrics.json", canonical_json(metrics))
        torch.save(
            {
                "model": model.state_dict(),
                "opt": opt.state_dict(),
                "epoch": epoch,
                "step": step,
                "config_hash": config.config_hash(),
                "data_manifest_sha256": shards.data_manifest_sha256,
            },
            out_dir / "checkpoint.pt",
        )
        print(json.dumps({"run": config.name, "seed": config.seed, **entry}), flush=True)
    torch.save(
        {
            "backbone": model.backbone_state_dict(),
            "stem_stride": config.stem_stride,
            "config_hash": config.config_hash(),
            "data_manifest_sha256": shards.data_manifest_sha256,
        },
        out_dir / "backbone.pt",
    )
    metrics["backbone_sha256"] = sha256_path(out_dir / "backbone.pt")
    metrics["status"] = "completed"
    _atomic_write(out_dir / "metrics.json", canonical_json(metrics))
    return out_dir


def load_backbone(run_dir: Path) -> Encoder:
    """The trained backbone (eval mode, CPU), checked against the run's config.json."""
    run_dir = Path(run_dir)
    config = RunConfig.model_validate_json((run_dir / "config.json").read_text())
    state = torch.load(run_dir / "backbone.pt", map_location="cpu", weights_only=True)
    if state.get("config_hash") != config.config_hash():
        raise ValueError(f"{run_dir}: backbone config hash does not match config.json")
    if not state.get("data_manifest_sha256"):
        raise ValueError(f"{run_dir}: backbone has no data manifest hash")
    enc = Encoder(stem_stride=int(state["stem_stride"]))
    enc.load_state_dict(state["backbone"])
    enc.eval()
    return enc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="terraluna_ml.train")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--manifest", type=Path)
    r.add_argument("--name", required=True)
    r.add_argument("--seed", type=int, required=True)
    r.add_argument("--epochs", type=int, default=40)
    r.add_argument("--batch-size", type=int, default=1024)
    r.add_argument("--windows-per-epoch", type=int)
    r.add_argument("--lambda-max", type=float, default=0.3)
    r.add_argument("--no-phys-head", action="store_true")
    r.add_argument("--no-vicreg", action="store_true")
    r.add_argument("--no-body-head", action="store_true")
    r.add_argument("--stem-stride", type=int, default=1)
    r.add_argument("--precision", choices=("bf16", "fp32"), default="fp32")
    r.add_argument("--threads", type=int, default=os.cpu_count() or 1)
    r.add_argument("--out", type=Path)
    r.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config = RunConfig(
        name=args.name,
        seed=args.seed,
        lambda_max=args.lambda_max,
        alpha_phys=0.0 if args.no_phys_head else 1.0,
        use_vicreg=not args.no_vicreg,
        use_phys_head=not args.no_phys_head,
        use_body_head=not args.no_body_head,
        epochs=args.epochs,
        batch_size=args.batch_size,
        precision=args.precision,
        stem_stride=args.stem_stride,
        windows_per_epoch=args.windows_per_epoch,
    )
    if args.dry_run:
        print(
            canonical_json(
                {
                    "config": config.model_dump(mode="json"),
                    "peak_lr": config.peak_lr,
                    "deviations_from_prd": config.deviations(),
                    "steps": "unknown (no shard manifest)"
                    if args.manifest is None
                    else "from manifest",
                }
            )
        )
        return 0
    if args.manifest is None or not args.manifest.is_file():
        print("a real run needs --manifest pointing at existing shards", file=sys.stderr)
        return 3
    out = args.out or Path("ml/checkpoints") / f"{config.name}_seed{config.seed}"
    run(config, args.manifest, out, threads=args.threads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
