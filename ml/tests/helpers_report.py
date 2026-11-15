"""Test-only synthetic evaluation inputs: tiny labelled shards, stand-in backbones, run dirs.

Everything here is synthetic and built under ``tmp_path``; nothing is committed as data.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

from terraluna_ml import shards
from terraluna_ml.evaluate import Loaders


class StandIn(nn.Module):
    """A tiny backbone with ``embed(x) -> (n, 256)`` L2-normalised, like the real encoder."""

    def __init__(self, seed: int = 0) -> None:
        super().__init__()
        gen = torch.Generator().manual_seed(seed)
        self.proj = nn.Linear(6, 256, bias=False)
        with torch.no_grad():
            self.proj.weight.copy_(torch.randn(256, 6, generator=gen))

    def embed(self, x: torch.Tensor) -> torch.Tensor:
        feats = torch.stack(
            [
                x[:, 0].mean((1, 2)),
                x[:, 0].std((1, 2)),
                x[:, 1].mean((1, 2)),
                x[:, 1].std((1, 2)),
                x[:, 0].abs().amax((1, 2)),
                x[:, 2].mean((1, 2)),
            ],
            dim=1,
        )
        return nn.functional.normalize(self.proj(feats), dim=1)


def make_shards(directory: Path, *, per: int = 60, mock: bool = True, seed: int = 0) -> Path:
    """Synthetic shards: train/val/test for three bodies plus Earth ``target`` rows."""
    rng = np.random.default_rng(seed)
    plan: list[tuple[str, str]] = [
        (b, s) for b in shards.BODIES for s in ("train", "val", "test") for _ in range(per)
    ]
    plan += [("earth", "target")] * 4
    n = len(plan)
    windows = np.zeros((n, 3, 64, 64), dtype=np.float16)
    raw = np.zeros((n, shards.N_PHYS))
    for i, (body, _split) in enumerate(plan):
        amp = rng.uniform(0.3, 3.0) * (1 + shards.BODIES.index(body))
        z = np.cumsum(np.cumsum(rng.normal(size=(64, 64)), axis=0), axis=1)
        z = amp * (z - z.mean()) / (z.std() + 1e-9)
        windows[i, 0] = np.arcsinh(z)
        windows[i, 1] = np.abs(np.gradient(z)[0]) / 4
        windows[i, 2] = 1.0
        raw[i, :5] = np.log(amp) + np.log(np.arange(1, 6))
        raw[i, 5:10] = np.log(amp) - 0.1 + 0.5 * np.log(np.arange(1, 6))
        raw[i, 10] = 0.5 + 0.1 * amp
    mask = np.ones(raw.shape, dtype=bool)
    mask[3, 2] = False
    raw[~mask] = np.nan
    bodies = [b for b, _ in plan]
    splits = [s for _, s in plan]
    meta: dict[str, object] = {
        "window_id": [f"{b}-w{i:04d}" for i, (b, _s) in enumerate(plan)],
        "body": bodies,
        "body_index": np.asarray([shards.BODIES.index(b) for b in bodies], dtype=np.int8),
        "lat_deg": rng.uniform(-80, 80, n),
        "lon_deg": rng.uniform(-180, 180, n),
        "split": splits,
        "block_id": [f"{b}-{s}-{i % 3}" for i, (b, s) in enumerate(plan)],
        "eval_block": [f"{b}-{s}-{i % 6}" for i, (b, s) in enumerate(plan)],
        "source_id": ["synthetic"] * n,
        "valid_fraction": np.ones(n),
        "analysis_valid_fraction": np.ones(n),
        "mock": np.full(n, mock, dtype=np.bool_),
    }
    for j in range(shards.N_PHYS):
        meta[f"phys_raw_{j:02d}"] = raw[:, j]
        meta[f"phys_mask_{j:02d}"] = mask[:, j]
    shards.write_shards(
        directory,
        windows=windows,
        meta=meta,  # type: ignore[arg-type]
        split_map={"synthetic": True},
        manifest_extra={"seed": seed},
    )
    return directory / shards.MANIFEST_FILE


def make_run(
    root: Path,
    name: str,
    *,
    seed: int,
    lambda_max: float,
    use_phys_head: bool = True,
    manifest_sha: str | None = None,
    gpu_hours: float | None = 0.25,
    train_bodies: list[str] | None = None,
) -> Path:
    run = root / name
    run.mkdir(parents=True)
    config: dict[str, object] = {"name": name, "seed": seed, "lambda_max": lambda_max}
    if train_bodies is not None:
        config["train_bodies"] = train_bodies
    (run / "config.json").write_text(json.dumps(config), encoding="utf-8")
    metrics: dict[str, object] = {
        "name": name,
        "seed": seed,
        "lambda_max": lambda_max,
        "use_phys_head": use_phys_head,
        "data_manifest_sha256": manifest_sha,
        "epochs": [
            {"epoch": e, "loss_total": 3.0 / (e + 1) + 0.01 * seed, "loss_vicreg": 2.0 / (e + 1)}
            for e in range(3)
        ],
    }
    if gpu_hours is not None:
        metrics["gpu_hours"] = gpu_hours
    (run / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (run / "backbone.pt").write_bytes(f"{name}-{seed}".encode())
    return run


def standin_loaders() -> Loaders:
    def load(run_dir: Path) -> StandIn:
        seed = int(json.loads((run_dir / "metrics.json").read_text())["seed"])
        return StandIn(seed + 7 * (1 if "lambda0" in run_dir.name else 0))

    return Loaders(load_backbone=load, random_init=lambda seed: StandIn(1000 + seed))
