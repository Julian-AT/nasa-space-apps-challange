"""Encoder evaluation (PRD 11.6): frozen embeddings, probes, agreement, stability, OOD.

Every statistic is Predicted and the encoder is EXPERIMENTAL. Commands::

    python -m terraluna_ml.evaluate run --manifest M --runs DIR... [random_init] --split val \
        --out outputs/ml/evaluation/val            # also writes freeze.json
    python -m terraluna_ml.evaluate run --manifest M --runs DIR... --split test \
        --out outputs/ml/evaluation/test --operator NAME --confirm READ-TEST-BLOCKS-ONCE
    python -m terraluna_ml.evaluate g5 --metrics FILE --out DIR [--parity FILE]

``run --split val`` evaluates on validation rows (probe hyperparameters are then also chosen on
those rows, so its numbers are optimistic and are a dry run) and writes ``freeze.json``: config
hash, the ``ml.g5`` thresholds as read, data manifest hash, checkpoint SHA-256s and probe
hyperparameters. ``run --split test`` recomputes the freeze from the current inputs, requires it
to equal the stored record, appends to the append-only ``test_read_log.json`` before any test
block is read, and refuses a second read for the same freeze unless ``--allow-reread`` is given
(then flagged in the log). Run arms: main seeds, ``lambda_0``, ``no_phys_head`` and a
``random_init`` arm (same architecture, fixed seed, untrained). Unavailable, with reasons: the
morphology probe (no crater labels), target retrieval (the M0 Earth ranking is not an input of
this build), ImageNet ResNet-18 and DINO ViT-S/8 baselines (LIC-01 weights not fetched), and
held-out body unless a Moon+Earth-only run is supplied.

Outputs in ``--out``: ``evaluation_metrics.json`` (``tests``: MetricRecords of the main run;
``arms``: compact records of every run; run record, freeze hash) and ``evaluation_curves.json``
(examples: top-5 Earth windows per target, rank-disagreement failure cases, OOD examples;
sweep points). Canonical JSON: sorted keys, indent 2, trailing newline.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import platform
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from terraluna_ml import g5 as g5mod
from terraluna_ml import probes, stats
from terraluna_ml.probes import EmbeddingSet, Unavailable
from terraluna_ml.shards import (
    BODIES,
    PHYSICS_TARGETS,
    OpenShards,
    canonical_json,
    open_shards,
    sha256_path,
)
from terraluna_pipeline.schema import (
    MetricBaseline,
    MetricRecord,
    RunRecord,
)

F32 = npt.NDArray[np.float32]
F64 = npt.NDArray[np.float64]
I64 = npt.NDArray[np.int64]

FORMAT: Final = "terraluna-ml-evaluation/1"
OPERATOR_DEFAULT: Final = "orchestrator, delegated by the team"
CONFIRM_TEXT: Final = "READ-TEST-BLOCKS-ONCE"
MAX_TRAIN_ROWS: Final = 20_000
N_PAIRS: Final = 20_000
TOP_K: Final = 20
AGREEMENT_SEED: Final = 11
SUBSAMPLE_SEED: Final = 0
OOD_REG: Final = 1e-3
OOD_PERCENTILE: Final = 99.0
REPO_ROOT: Final = Path(__file__).resolve().parents[3]

REASON_IMAGENET: Final = "LIC-01: ImageNet ResNet-18 / DINO ViT-S/8 weights not fetched"
REASON_RETRIEVAL: Final = "the deterministic M0 Earth ranking of T4 is not an input of this build"
REASON_HELD_OUT: Final = "no Moon+Earth-only run was supplied"

RELIEF_IDX: Final = tuple(range(5))
TEXTURE_IDX: Final = tuple(range(5, 10))


class EvaluationError(RuntimeError):
    """A refused or inconsistent evaluation."""


# --- backbones -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Loaders:
    """How backbones are obtained (replaceable in tests)."""

    load_backbone: Callable[[Path], Any]
    random_init: Callable[[int], Any]


def _default_load_backbone(run_dir: Path) -> Any:
    return importlib.import_module("terraluna_ml.train").load_backbone(run_dir)


def _default_random_init(seed: int) -> Any:
    import torch

    torch.manual_seed(seed)
    return importlib.import_module("terraluna_ml.model").Encoder()


DEFAULT_LOADERS: Final = Loaders(_default_load_backbone, _default_random_init)


@dataclass
class RunSpec:
    key: str
    arm: str
    seed: int
    lambda_max: float | None
    use_phys_head: bool | None
    run_dir: Path | None
    gpu_hours: float | None
    config: dict[str, Any] = field(default_factory=dict)
    checkpoints: dict[str, str] = field(default_factory=dict)


CHECKPOINT_SUFFIXES: Final = (".pt", ".pth", ".ckpt", ".safetensors")


def _read_json(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise EvaluationError(f"{path} is not a JSON object")
    return loaded


def read_run(run_dir: Path) -> RunSpec:
    """Describe a training run directory from its config.json and metrics.json."""
    metrics_path, config_path = run_dir / "metrics.json", run_dir / "config.json"
    for p in (metrics_path, config_path):
        if not p.is_file():
            raise EvaluationError(f"run directory {run_dir} lacks {p.name}")
    metrics, config = _read_json(metrics_path), _read_json(config_path)
    for name in ("seed", "lambda_max", "use_phys_head"):
        if name not in metrics:
            raise EvaluationError(f"{metrics_path} lacks {name}")
    checkpoints = {
        p.name: sha256_path(p)
        for p in sorted(run_dir.iterdir())
        if p.is_file() and p.suffix in CHECKPOINT_SUFFIXES
    }
    if not checkpoints:
        raise EvaluationError(f"run directory {run_dir} has no checkpoint {CHECKPOINT_SUFFIXES}")
    lam, head = float(metrics["lambda_max"]), bool(metrics["use_phys_head"])
    arm = "no_phys_head" if not head else "lambda_0" if lam == 0.0 else f"lambda_{lam:g}"
    gpu = metrics.get("gpu_hours")
    return RunSpec(
        key=f"{arm}/seed{int(metrics['seed'])}",
        arm=arm,
        seed=int(metrics["seed"]),
        lambda_max=lam,
        use_phys_head=head,
        run_dir=run_dir,
        gpu_hours=float(gpu) if isinstance(gpu, int | float) else None,
        config={**config, "_metrics_manifest": metrics.get("data_manifest_sha256")},
        checkpoints=checkpoints,
    )


def state_hash(module: Any) -> str:
    digest = hashlib.sha256()
    for name, tensor in sorted(module.state_dict().items()):
        digest.update(name.encode())
        digest.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def resolve_runs(tokens: Sequence[str], loaders: Loaders) -> tuple[list[RunSpec], dict[str, Any]]:
    """Run specs for run dirs and ``random_init[:seed]`` tokens, and their backbones' hashes."""
    specs: list[RunSpec] = []
    random_hashes: dict[str, Any] = {}
    for token in tokens:
        if token.split(":")[0] == "random_init":
            seed = int(token.split(":")[1]) if ":" in token else 0
            backbone = loaders.random_init(seed)
            h = state_hash(backbone)
            spec = RunSpec(
                key=f"random_init/seed{seed}",
                arm="random_init",
                seed=seed,
                lambda_max=None,
                use_phys_head=None,
                run_dir=None,
                gpu_hours=0.0,
                checkpoints={"random_init_weights": h},
            )
            random_hashes[spec.key] = backbone
            specs.append(spec)
        else:
            specs.append(read_run(Path(token)))
    keys = [s.key for s in specs]
    if len(set(keys)) != len(keys):
        raise EvaluationError(f"duplicate runs: {keys}")
    return specs, random_hashes


def choose_main_arm(specs: Sequence[RunSpec]) -> str:
    """Main arm: the lambda_max>0 physics-head group with most seeds (ties: larger lambda_max)."""
    groups: dict[str, list[RunSpec]] = {}
    for s in specs:
        if s.use_phys_head and s.lambda_max and s.lambda_max > 0:
            groups.setdefault(s.arm, []).append(s)
    if not groups:
        raise EvaluationError("no main run (physics head with lambda_max > 0) was supplied")
    return max(groups, key=lambda a: (len(groups[a]), groups[a][0].lambda_max or 0.0))


# --- embeddings ----------------------------------------------------------------------------------


def pick_device() -> str:
    """CPU unless ``--device`` says otherwise (team rule: no heavy compute on the laptop)."""
    return "cpu"


def embed_rows(
    backbone: Any, shards: OpenShards, rows: I64, *, batch_size: int = 256, device: str = "cpu"
) -> F32:
    import torch

    backbone.eval()
    backbone.to(device)
    out: list[F32] = []
    with torch.no_grad():
        for start in range(0, len(rows), batch_size):
            idx = np.sort(rows[start : start + batch_size])
            if not np.array_equal(idx, rows[start : start + batch_size]):
                raise EvaluationError("rows must be sorted")
            x = torch.from_numpy(np.asarray(shards.windows[idx], dtype=np.float32)).to(device)
            e = backbone.embed(x)
            out.append(e.detach().cpu().numpy().astype(np.float32))
    return np.concatenate(out) if out else np.zeros((0, 256), np.float32)


def _embedding_set(shards: OpenShards, rows: I64, x: F32) -> EmbeddingSet:
    return EmbeddingSet(
        x=x,
        rows=rows,
        body_index=shards.column("body_index")[rows].astype(np.int64),
        eval_block=shards.column("eval_block")[rows].astype(str),
        window_id=shards.column("window_id")[rows].astype(str),
    )


def subsample_train(rows: I64, limit: int, seed: int) -> tuple[I64, dict[str, int]]:
    if len(rows) <= limit:
        chosen = rows
    else:
        chosen = np.sort(np.random.default_rng(seed).choice(rows, size=limit, replace=False))
    return chosen.astype(np.int64), {"n_available": len(rows), "n_used": len(chosen), "seed": seed}


# --- tests ---------------------------------------------------------------------------------------


def _unit(x: F32) -> F64:
    x64 = x.astype(np.float64)
    return x64 / np.maximum(np.linalg.norm(x64, axis=1, keepdims=True), 1e-12)


def cosine_distance_matrix(a: F32, b: F32) -> F64:
    return 1.0 - _unit(a) @ _unit(b).T


def sample_cross_body_pairs(body_index: I64, n_pairs: int, seed: int) -> tuple[I64, I64]:
    """Seeded unordered pairs of eval rows from different bodies; too few possible raises."""
    counts = np.bincount(body_index, minlength=len(BODIES))
    possible = sum(
        int(counts[a]) * int(counts[b])
        for a in range(len(BODIES))
        for b in range(a + 1, len(BODIES))
    )
    if possible < n_pairs:
        raise EvaluationError(f"only {possible} cross-body pairs exist, {n_pairs} requested")
    rng = np.random.default_rng(seed)
    seen: set[tuple[int, int]] = set()
    n = len(body_index)
    while len(seen) < n_pairs:
        i = rng.integers(0, n, size=2 * n_pairs)
        j = rng.integers(0, n, size=2 * n_pairs)
        for a, b in zip(i.tolist(), j.tolist(), strict=True):
            if body_index[a] != body_index[b]:
                seen.add((min(a, b), max(a, b)) if a < b else (b, a))
                if len(seen) == n_pairs:
                    break
    pairs = np.array(sorted(seen), dtype=np.int64)
    # random orientation so "first window" is not always the lower row
    flip = rng.random(len(pairs)) < 0.5
    first = np.where(flip, pairs[:, 1], pairs[:, 0])
    second = np.where(flip, pairs[:, 0], pairs[:, 1])
    return first, second


def _spearman_of(a: F64, b: F64) -> Callable[[I64], float]:
    return lambda i: stats.spearman(a[i], b[i])


def physics_agreement(
    emb: EmbeddingSet,
    raw: F64,
    mask: npt.NDArray[np.bool_],
    *,
    n_pairs: int = N_PAIRS,
    n_resamples: int = stats.N_RESAMPLES,
    seed: int = AGREEMENT_SEED,
    pairs: tuple[I64, I64] | None = None,
) -> dict[str, stats.Interval | Unavailable | int]:
    """Spearman of embedding cosine distance with RMS log-nu distance on cross-body pairs."""
    first, second = pairs or sample_cross_body_pairs(emb.body_index, n_pairs, seed)
    ok10 = mask[:, :10].all(axis=1)
    keep = ok10[first] & ok10[second]
    f, s = first[keep], second[keep]
    dropped = int((~keep).sum())
    unit = _unit(emb.x)
    cos = 1.0 - np.einsum("ij,ij->i", unit[f], unit[s])
    diff2 = (raw[f, :10] - raw[s, :10]) ** 2
    blocks = emb.eval_block[f]
    out: dict[str, stats.Interval | Unavailable | int] = {
        "n_pairs_dropped": dropped,
        "n_pairs": len(f),
    }
    variants = {
        "rms_log_nu_10": diff2.mean(axis=1),
        "rms_log_nu_relief_5": diff2[:, RELIEF_IDX].mean(axis=1),
        "rms_log_nu_texture_5": diff2[:, TEXTURE_IDX].mean(axis=1),
    }
    for name, d in variants.items():
        rms = np.sqrt(d)
        if len(np.unique(blocks)) < 2 or len(f) < 3:
            out[name] = Unavailable("fewer than 2 spatial blocks or 3 valid pairs")
            continue
        out[name] = stats.block_bootstrap(
            blocks,
            _spearman_of(cos, rms),
            n_resamples=n_resamples,
            seed=seed,
        )
    return out


@dataclass(frozen=True)
class OodModel:
    mean: F64
    precision: F64
    threshold: float
    regularisation: float
    percentile: float
    n_train: int


def fit_ood(train_x: F32, reg: float = OOD_REG, percentile: float = OOD_PERCENTILE) -> OodModel:
    x = train_x.astype(np.float64)
    mean = x.mean(axis=0)
    cov = np.atleast_2d(np.cov(x, rowvar=False))
    cov = cov + reg * (np.trace(cov) / cov.shape[0] + 1e-12) * np.eye(cov.shape[0])
    model = OodModel(mean, np.linalg.inv(cov), 0.0, reg, percentile, len(x))
    threshold = float(np.percentile(mahalanobis(model, train_x), percentile))
    return OodModel(mean, model.precision, threshold, reg, percentile, len(x))


def mahalanobis(model: OodModel, x: F32) -> F64:
    d = x.astype(np.float64) - model.mean
    quad: F64 = np.einsum("ij,jk,ik->i", d, model.precision, d)
    return np.sqrt(np.maximum(quad, 0.0))


def seed_stability(
    targets: dict[int, F32],
    candidates: dict[int, F32],
    cand_blocks: npt.NDArray[np.str_],
    *,
    k: int = TOP_K,
    n_resamples: int = stats.N_RESAMPLES,
    seed: int = 0,
) -> dict[str, Any] | Unavailable:
    """Pairwise Jaccard@k of each target's top-k Earth candidates across seeds."""
    seeds = sorted(targets)
    if len(seeds) < 2:
        return Unavailable("fewer than two main seeds were supplied")
    n_targets, n_cand = targets[seeds[0]].shape[0], candidates[seeds[0]].shape[0]
    if n_targets == 0:
        return Unavailable("no target windows in the shard set")
    if n_cand < 2 * k:
        return Unavailable(f"only {n_cand} Earth candidate windows, fewer than {2 * k}")
    dist = {s: cosine_distance_matrix(targets[s], candidates[s]) for s in seeds}
    pairs = [(a, b) for i, a in enumerate(seeds) for b in seeds[i + 1 :]]

    def mean_jaccard(cols: I64) -> tuple[float, F64]:
        tops = {s: np.argsort(dist[s][:, cols], axis=1, kind="stable")[:, :k] for s in seeds}
        per = np.zeros((n_targets, len(pairs)))
        for pi, (a, b) in enumerate(pairs):
            for t in range(n_targets):
                per[t, pi] = stats.jaccard_at_k(tops[a][t].tolist(), tops[b][t].tolist(), k)
        return float(per.mean()), per

    all_cols = np.arange(n_cand, dtype=np.int64)
    mean, per = mean_jaccard(all_cols)
    uniq = np.unique(cand_blocks)
    if len(uniq) < 2:
        interval: stats.Interval | Unavailable = Unavailable("fewer than 2 Earth candidate blocks")
    else:
        rng = np.random.default_rng(seed)
        block_cols = {b: np.flatnonzero(cand_blocks == b) for b in uniq}
        vals = []
        for _ in range(n_resamples):
            drawn = np.unique(rng.integers(0, len(uniq), size=len(uniq)))
            cols = np.concatenate([block_cols[uniq[i]] for i in drawn])
            if len(cols) < k:
                continue
            vals.append(mean_jaccard(cols)[0])
        lo, hi = np.percentile(vals, stats.CI_PERCENT)
        interval = stats.Interval(
            mean, float(lo), float(hi), len(uniq), n_resamples, len(vals), seed
        )
    return {
        "mean": mean,
        "min": float(per.mean(axis=0).min()) if False else float(per.min()),
        "per_pair_mean": {f"{a}-{b}": float(per[:, i].mean()) for i, (a, b) in enumerate(pairs)},
        "random_expectation": stats.random_jaccard_expectation(n_cand, k),
        "n_targets": n_targets,
        "n_blocks": len(uniq),
        "n_candidates": n_cand,
        "n_seeds": len(seeds),
        "interval": interval,
    }


# --- metric records ------------------------------------------------------------------------------


def _record(
    test_id: str,
    measure: str,
    target: str | None,
    body: str | None,
    result: stats.Interval | Unavailable,
    n_resamples: int,
    baselines: Sequence[MetricBaseline] = (),
) -> MetricRecord:
    if isinstance(result, Unavailable):
        return MetricRecord(
            test_id=test_id,  # type: ignore[arg-type]
            measure=measure,
            target=target,
            body=body,  # type: ignore[arg-type]
            value=None,
            ci_low=None,
            ci_high=None,
            n_blocks=None,
            n_bootstrap=None,
            unavailable_reason=result.reason,
            evidence_label="Predicted",
            baselines=list(baselines),
        )
    return MetricRecord(
        test_id=test_id,  # type: ignore[arg-type]
        measure=measure,
        target=target,
        body=body,  # type: ignore[arg-type]
        value=result.estimate,
        ci_low=result.ci_low,
        ci_high=result.ci_high,
        n_blocks=result.n_blocks,
        n_bootstrap=result.n_valid_resamples,
        unavailable_reason=None,
        evidence_label="Predicted",
        baselines=list(baselines),
    )


def _baseline(
    name: str,
    result: stats.Interval | Unavailable | None,
    paired: stats.Interval | Unavailable | None = None,
    reason: str | None = None,
) -> MetricBaseline:
    if result is None or isinstance(result, Unavailable):
        return MetricBaseline(
            name=name,
            value=None,
            ci_low=None,
            ci_high=None,
            paired_diff_ci_low=None,
            paired_diff_ci_high=None,
            unavailable_reason=reason or (result.reason if result else "not computed"),
        )
    pd = paired if isinstance(paired, stats.Interval) else None
    return MetricBaseline(
        name=name,
        value=result.estimate,
        ci_low=result.ci_low,
        ci_high=result.ci_high,
        paired_diff_ci_low=pd.ci_low if pd else None,
        paired_diff_ci_high=pd.ci_high if pd else None,
        unavailable_reason=None if pd or paired is None else getattr(paired, "reason", None),
    )


def _point_baseline(name: str, value: float, reason: str) -> MetricBaseline:
    return MetricBaseline(
        name=name,
        value=value,
        ci_low=None,
        ci_high=None,
        paired_diff_ci_low=None,
        paired_diff_ci_high=None,
        unavailable_reason=reason,
    )


# --- per-run evaluation --------------------------------------------------------------------------


@dataclass
class RunResult:
    spec: RunSpec
    train: EmbeddingSet
    val: EmbeddingSet
    ev: EmbeddingSet
    target: EmbeddingSet
    probe: probes.PhysicsProbe
    body: probes.BodyProbe
    body_ba: stats.Interval | Unavailable
    agreement: dict[str, stats.Interval | Unavailable | int]
    ood: OodModel
    ood_scores_eval: F64
    seconds: dict[str, float]
    parameter_count: int | None = None
    architecture: str | None = None


def _targets_arrays(
    raw: F64, mask: npt.NDArray[np.bool_], rows: I64
) -> tuple[F64, npt.NDArray[np.bool_]]:
    return raw[rows], mask[rows]


def evaluate_run(
    spec: RunSpec,
    backbone: Any,
    shards: OpenShards,
    train_rows: I64,
    val_rows: I64,
    eval_rows: I64,
    target_rows: I64,
    *,
    device: str,
    n_pairs: int,
    n_resamples: int,
    pairs_cache: dict[str, tuple[I64, I64]],
) -> RunResult:
    raw, mask = shards.physics_raw()
    seconds: dict[str, float] = {}
    t0 = time.perf_counter()
    sets = {
        name: _embedding_set(shards, rows, embed_rows(backbone, shards, rows, device=device))
        for name, rows in (
            ("train", train_rows),
            ("val", val_rows),
            ("ev", eval_rows),
            ("target", target_rows),
        )
    }
    seconds["extract_embeddings"] = time.perf_counter() - t0
    tr, va, ev = sets["train"], sets["val"], sets["ev"]
    t0 = time.perf_counter()
    probe = probes.fit_physics_probe(
        tr,
        *_targets_arrays(raw, mask, train_rows),
        va,
        *_targets_arrays(raw, mask, val_rows),
        ev,
        *_targets_arrays(raw, mask, eval_rows),
        PHYSICS_TARGETS,
    )
    seconds["physics_probe"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    body = probes.fit_body_probe(tr, va, ev)
    body_ba = probes.body_balanced_accuracy(body, n_resamples=n_resamples)
    seconds["body_probe"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    if "pairs" not in pairs_cache:
        first, second = sample_cross_body_pairs(ev.body_index, n_pairs, AGREEMENT_SEED)
        pairs_cache["pairs"] = (first, second)
    agreement = physics_agreement(
        ev,
        *_targets_arrays(raw, mask, eval_rows),
        n_resamples=n_resamples,
        pairs=pairs_cache["pairs"],
    )
    seconds["physics_agreement"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    ood = fit_ood(tr.x)
    scores = mahalanobis(ood, ev.x)
    seconds["ood"] = time.perf_counter() - t0
    n_params = None
    try:
        n_params = int(sum(p.numel() for p in backbone.parameters()))
    except Exception:  # a stand-in without parameters
        n_params = None
    return RunResult(
        spec,
        tr,
        va,
        ev,
        sets["target"],
        probe,
        body,
        body_ba,
        agreement,
        ood,
        scores,
        seconds,
        n_params,
        type(backbone).__name__,
    )


def physics_records(
    r: RunResult, n_resamples: int, random_init: RunResult | None
) -> list[MetricRecord]:
    out: list[MetricRecord] = []
    for target in PHYSICS_TARGETS:
        for body_name, bi in (("pooled", None), *((b, i) for i, b in enumerate(BODIES))):
            res = probes.physics_r2(r.probe, target, body=bi, n_resamples=n_resamples)
            baselines: list[MetricBaseline] = []
            if body_name == "pooled":
                if random_init is not None:
                    rr = probes.physics_r2(random_init.probe, target, n_resamples=n_resamples)
                    paired = probes.paired_physics_r2_diff(
                        r.probe, random_init.probe, target, n_resamples=n_resamples
                    )
                    baselines.append(_baseline("random_init", rr, paired))
                else:
                    baselines.append(_baseline("random_init", None, reason="no random_init arm"))
                for name in ("imagenet_resnet18", "dino_vits8"):
                    baselines.append(_baseline(name, None, reason=REASON_IMAGENET))
            out.append(
                _record("physics_probe", "r2", target, body_name, res, n_resamples, baselines)
            )
    return out


def record_set(
    r: RunResult,
    n_resamples: int,
    *,
    random_init: RunResult | None,
    lambda0: RunResult | None,
    seeds: dict[str, Any] | Unavailable,
    held_out: list[MetricRecord],
) -> list[MetricRecord]:
    out = physics_records(r, n_resamples, random_init)
    morph_bl = [
        _baseline(n, None, reason=REASON_IMAGENET) for n in ("imagenet_resnet18", "dino_vits8")
    ] + [_baseline("fingerprint_only_ridge", None, reason=probes.MORPHOLOGY_UNAVAILABLE_REASON)]
    out.append(
        _record(
            "morphology_probe",
            "r2_gain_over_fingerprint_ridge",
            None,
            None,
            probes.morphology_probe(),
            n_resamples,
            morph_bl,
        )
    )
    body_bl = [
        _baseline(
            "lambda_0", lambda0.body_ba, reason="no lambda_0 run" if lambda0 is None else None
        )
        if lambda0 is not None
        else _baseline("lambda_0", None, reason="no lambda_0 run was supplied"),
        _baseline("random_init", random_init.body_ba, None)
        if random_init is not None
        else _baseline("random_init", None, reason="no random_init arm"),
    ]
    body_bl.append(_point_baseline("chance", r.body.chance, "1 over the number of training bodies"))
    out.append(
        _record(
            "body_invariance", "balanced_accuracy", None, "pooled", r.body_ba, n_resamples, body_bl
        )
    )
    for name in ("rms_log_nu_10", "rms_log_nu_relief_5", "rms_log_nu_texture_5"):
        res = r.agreement[name]
        assert not isinstance(res, int)
        bl = []
        if random_init is not None:
            rb = random_init.agreement[name]
            assert not isinstance(rb, int)
            bl.append(_baseline("random_init", rb))
        out.append(
            _record("physics_agreement", "spearman_rho", name, "pooled", res, n_resamples, bl)
        )
    out.append(
        _record(
            "target_retrieval",
            "jaccard_at_20",
            None,
            "earth",
            Unavailable(REASON_RETRIEVAL),
            n_resamples,
            [_baseline("random_ranking", None, reason=REASON_RETRIEVAL)],
        )
    )
    if isinstance(seeds, Unavailable):
        out.append(
            _record(
                "seed_stability", "jaccard_at_20_mean", "earth_top20", "earth", seeds, n_resamples
            )
        )
    else:
        iv = seeds["interval"]
        res2 = iv if isinstance(iv, stats.Interval) else Unavailable(iv.reason)
        out.append(
            _record(
                "seed_stability",
                "jaccard_at_20_mean",
                "earth_top20",
                "earth",
                res2,
                n_resamples,
                [
                    _point_baseline(
                        "random_ranking",
                        seeds["random_expectation"],
                        "exact hypergeometric expectation",
                    )
                ],
            )
        )
    out.extend(
        held_out
        or [_record("held_out_body", "r2", None, "mars", Unavailable(REASON_HELD_OUT), n_resamples)]
    )
    out.extend(ood_records(r, n_resamples))
    return out


def ood_records(r: RunResult, n_resamples: int) -> list[MetricRecord]:
    flags = (r.ood_scores_eval > r.ood.threshold).astype(np.float64)
    recs = []
    for body_name, bi in (("pooled", None), *((b, i) for i, b in enumerate(BODIES))):
        use = np.ones(len(flags), bool) if bi is None else r.ev.body_index == bi
        if use.sum() == 0:
            res: stats.Interval | Unavailable = Unavailable(
                f"no {body_name} windows in the evaluated split"
            )
        elif len(np.unique(r.ev.eval_block[use])) < 2:
            res = Unavailable("fewer than 2 spatial blocks")
        else:
            res = stats.block_bootstrap_sums(
                r.ev.eval_block[use],
                np.column_stack([np.ones(int(use.sum())), flags[use]]),
                lambda t: float(t[1] / t[0]),
                n_resamples=n_resamples,
            )
        recs.append(
            _record(
                "out_of_distribution",
                "fraction_above_train_p99",
                "mahalanobis",
                body_name,
                res,
                n_resamples,
                [
                    _point_baseline(
                        "training_expectation", 0.01, "99th percentile of training distances"
                    )
                ],
            )
        )
    return recs


def held_out_records(
    held: RunResult | None, main: RunResult, shards: OpenShards, n_resamples: int
) -> list[MetricRecord]:
    """Mars R2 of a Moon+Earth-only run vs the main run, probes fitted on Moon+Earth rows."""
    if held is None:
        return []
    mars = BODIES.index("mars")
    out = []
    raw, mask = shards.physics_raw()

    def fit(r: RunResult) -> probes.PhysicsProbe:
        keep = r.train.body_index != mars
        keep_v = r.val.body_index != mars
        sel = r.ev.body_index == mars
        tr, va, te = r.train.take(keep), r.val.take(keep_v), r.ev.take(sel)
        return probes.fit_physics_probe(
            tr,
            *_targets_arrays(raw, mask, tr.rows),
            va,
            *_targets_arrays(raw, mask, va.rows),
            te,
            *_targets_arrays(raw, mask, te.rows),
            PHYSICS_TARGETS,
        )

    ph, pm = fit(held), fit(main)
    for target in PHYSICS_TARGETS:
        res = probes.physics_r2(ph, target, n_resamples=n_resamples)
        paired = probes.paired_physics_r2_diff(ph, pm, target, n_resamples=n_resamples)
        main_res = probes.physics_r2(pm, target, n_resamples=n_resamples)
        out.append(
            _record(
                "held_out_body",
                "r2",
                target,
                "mars",
                res,
                n_resamples,
                [_baseline("main_run", main_res, paired)],
            )
        )
    return out


def is_held_out_run(spec: RunSpec) -> bool:
    bodies = spec.config.get("train_bodies") or spec.config.get("bodies")
    return isinstance(bodies, list) and "mars" not in bodies and {"moon", "earth"} <= set(bodies)


# --- examples ------------------------------------------------------------------------------------


def build_examples(
    r: RunResult,
    shards: OpenShards,
    raw: F64,
    mask: npt.NDArray[np.bool_],
    top_n: int = 5,
    n_fail: int = 20,
) -> dict[str, Any]:
    earth = BODIES.index("earth")
    cand_sel = r.ev.body_index == earth
    cand = r.ev.take(cand_sel)
    tgt = r.target
    out: dict[str, Any] = {
        "model": "EXPERIMENTAL",
        "evidence_label": "Predicted",
        "distance_family": "encoder",
        "n_targets": len(tgt),
        "n_earth_candidates": len(cand),
        "top5_by_target": [],
        "failure_cases": [],
        "failure_sort_rule": (
            "descending |encoder rank - RMS log-nu rank|, "
            "ties by Earth window_id then target window_id"
        ),
        "failure_basis": (
            "RMS distance over the 10 log-nu values (not the deterministic M0 ranking)"
        ),
        "ood_examples": [],
    }
    if len(tgt) == 0 or len(cand) == 0:
        return out
    dist = cosine_distance_matrix(tgt.x, cand.x)
    lat, lon = shards.column("lat_deg"), shards.column("lon_deg")
    ok_c = mask[cand.rows, :10].all(axis=1)
    cases: list[tuple[int, str, str, int, int, float, float]] = []
    for t in range(len(tgt)):
        order = np.lexsort((cand.window_id, dist[t]))
        out["top5_by_target"].append(
            {
                "target_window_id": str(tgt.window_id[t]),
                "evidence_label": "Predicted",
                "distance_family": "encoder",
                "windows": [
                    {
                        "rank": rank + 1,
                        "window_id": str(cand.window_id[ci]),
                        "cosine_distance": float(dist[t, ci]),
                        "lat_deg": float(lat[cand.rows[ci]]),
                        "lon_deg": float(lon[cand.rows[ci]]),
                    }
                    for rank, ci in enumerate(order[:top_n])
                ],
                "shortfall": None
                if len(order) >= top_n
                else f"only {len(order)} Earth test windows exist",
            }
        )
        if not mask[tgt.rows[t], :10].all():
            continue
        idx = np.flatnonzero(ok_c)
        if len(idx) < 2:
            continue
        rms = np.sqrt(((raw[cand.rows[idx], :10] - raw[tgt.rows[t], :10]) ** 2).mean(axis=1))
        enc_order = idx[np.lexsort((cand.window_id[idx], dist[t, idx]))]
        phys_order = idx[np.lexsort((cand.window_id[idx], rms))]
        enc_rank = {int(c): k for k, c in enumerate(enc_order)}
        phys_rank = {int(c): k for k, c in enumerate(phys_order)}
        rms_by = dict(zip(idx.tolist(), rms.tolist(), strict=True))
        for c in idx.tolist():
            cases.append(
                (
                    abs(enc_rank[c] - phys_rank[c]),
                    str(cand.window_id[c]),
                    str(tgt.window_id[t]),
                    enc_rank[c] + 1,
                    phys_rank[c] + 1,
                    float(dist[t, c]),
                    float(rms_by[c]),
                )
            )
    cases.sort(key=lambda c: (-c[0], c[1], c[2]))
    out["failure_cases"] = [
        {
            "window_id": c[1],
            "target_window_id": c[2],
            "rank_disagreement": c[0],
            "encoder_rank": c[3],
            "rms_log_nu_rank": c[4],
            "encoder_cosine_distance": c[5],
            "rms_log_nu_distance": c[6],
            "evidence_label": "Predicted",
            "distance_family": "encoder",
        }
        for c in cases[:n_fail]
    ]
    out["failure_shortfall"] = (
        None if len(cases) >= n_fail else f"only {len(cases)} (target, window) pairs available"
    )
    flagged = np.flatnonzero(r.ood_scores_eval > r.ood.threshold)
    flagged = flagged[np.lexsort((r.ev.window_id[flagged], -r.ood_scores_eval[flagged]))][:50]
    out["ood_examples"] = [
        {
            "window_id": str(r.ev.window_id[i]),
            "body": BODIES[int(r.ev.body_index[i])],
            "mahalanobis": float(r.ood_scores_eval[i]),
            "threshold": r.ood.threshold,
            "label": "Predicted-model diagnostic, not an observation",
        }
        for i in flagged
    ]
    return out


# --- freeze, read log, run record ----------------------------------------------------------------


def _sha(path: Path) -> str:
    return sha256_path(path)


def git_sha(root: Path = REPO_ROOT) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def hardware_string() -> str:
    import torch

    mps = torch.backends.mps.is_available()
    cuda = torch.cuda.is_available()
    return (
        f"{platform.platform()}; {platform.machine()}; {platform.processor() or 'cpu'}; "
        f"accelerator={'mps' if mps else 'cuda' if cuda else 'none'}"
    )


def probe_hyperparameters(max_train: int, n_pairs: int, n_resamples: int) -> dict[str, Any]:
    return {
        "ridge_alphas": list(probes.RIDGE_ALPHAS),
        "logistic_cs": list(probes.LOGISTIC_CS),
        "standardisation": "StandardScaler fitted on training embeddings only",
        "n_bootstrap": n_resamples,
        "n_agreement_pairs": n_pairs,
        "max_train_rows": max_train,
        "train_subsample_seed": SUBSAMPLE_SEED,
        "agreement_seed": AGREEMENT_SEED,
        "ood_regularisation": OOD_REG,
        "ood_percentile": OOD_PERCENTILE,
        "top_k": TOP_K,
    }


def build_freeze(
    config_path: Path,
    thresholds: g5mod.G5Thresholds,
    shards: OpenShards,
    specs: Sequence[RunSpec],
    hyper: dict[str, Any],
) -> dict[str, Any]:
    return {
        "format": "terraluna-ml-freeze/1",
        "config_sha256": _sha(config_path),
        "ml_g5_thresholds": thresholds.as_dict(),
        "data_manifest_sha256": shards.data_manifest_sha256,
        "split_map_sha256": shards.manifest["files"]["split_map.json"]["sha256"],
        "checkpoint_sha256s": {s.key: dict(sorted(s.checkpoints.items())) for s in specs},
        "probe_hyperparameters": hyper,
    }


def freeze_hash(freeze: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(freeze).encode("utf-8")).hexdigest()


def append_read_log(log_path: Path, entry: dict[str, Any], *, allow_reread: bool) -> None:
    log: list[dict[str, Any]] = []
    if log_path.is_file():
        loaded = json.loads(log_path.read_text(encoding="utf-8"))
        log = list(loaded)
    prior = [e for e in log if e.get("freeze_sha256") == entry["freeze_sha256"]]
    if prior and not allow_reread:
        raise EvaluationError(
            "the test blocks were already read for this freeze "
            f"({prior[0]['timestamp_utc']}); pass --allow-reread to read again (it will be flagged)"
        )
    entry = {**entry, "reread": bool(prior)}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(canonical_json([*log, entry]), encoding="utf-8")


# --- orchestration -------------------------------------------------------------------------------


def run_evaluation(
    *,
    manifest: Path,
    run_tokens: Sequence[str],
    split: str,
    out_dir: Path,
    config_path: Path,
    loaders: Loaders = DEFAULT_LOADERS,
    operator: str | None = None,
    confirm: str | None = None,
    allow_reread: bool = False,
    read_log: Path | None = None,
    freeze_path: Path | None = None,
    uv_lock: Path | None = None,
    max_train: int = MAX_TRAIN_ROWS,
    n_pairs: int = N_PAIRS,
    n_resamples: int = stats.N_RESAMPLES,
    device: str | None = None,
    command_line: str = "",
) -> dict[str, Any]:
    if split not in ("val", "test"):
        raise EvaluationError("split must be val or test")
    wall0 = time.perf_counter()
    thresholds = g5mod.load_thresholds(config_path)
    shards = open_shards(manifest)
    specs, random_modules = resolve_runs(run_tokens, loaders)
    main_arm = choose_main_arm(specs)
    for s in specs:
        recorded = s.config.get("_metrics_manifest")
        if s.run_dir is not None and recorded not in (None, shards.data_manifest_sha256):
            raise EvaluationError(
                f"{s.key} was trained on data manifest {recorded}, "
                f"not {shards.data_manifest_sha256}"
            )
    hyper = probe_hyperparameters(max_train, n_pairs, n_resamples)
    freeze = build_freeze(config_path, thresholds, shards, specs, hyper)
    fhash = freeze_hash(freeze)
    out_dir.mkdir(parents=True, exist_ok=True)
    if split == "val":
        (out_dir / "freeze.json").write_text(canonical_json(freeze), encoding="utf-8")
    else:
        if confirm != CONFIRM_TEXT:
            raise EvaluationError(f"reading test blocks needs --confirm {CONFIRM_TEXT}")
        stored_path = freeze_path or out_dir.parent / "val" / "freeze.json"
        if not stored_path.is_file():
            raise EvaluationError(f"no freeze record at {stored_path}; run --split val first")
        stored = _read_json(stored_path)
        if canonical_json(stored) != canonical_json(freeze):
            differing = sorted(
                k for k in set(stored) | set(freeze) if stored.get(k) != freeze.get(k)
            )
            raise EvaluationError(f"inputs changed since the freeze: {differing}")
        # The log entry is written before any test block is read.
        append_read_log(
            read_log or out_dir.parent.parent / "test_read_log.json",
            {
                "timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "operator": operator or OPERATOR_DEFAULT,
                "freeze_sha256": fhash,
                "git_sha": git_sha(),
                "command_line": command_line,
            },
            allow_reread=allow_reread,
        )
    dev = device or pick_device()
    train_rows, subsample = subsample_train(shards.rows("train"), max_train, SUBSAMPLE_SEED)
    val_rows = shards.rows("val")
    eval_rows = shards.rows(split)
    target_rows = shards.rows("target")
    cache: dict[str, tuple[I64, I64]] = {}
    results: dict[str, RunResult] = {}
    for s in specs:
        if s.arm == "random_init":
            backbone = random_modules[s.key]
        else:
            assert s.run_dir is not None
            backbone = loaders.load_backbone(s.run_dir)
        results[s.key] = evaluate_run(
            s,
            backbone,
            shards,
            train_rows,
            val_rows,
            eval_rows,
            target_rows,
            device=dev,
            n_pairs=n_pairs,
            n_resamples=n_resamples,
            pairs_cache=cache,
        )
    main_runs = sorted((s for s in specs if s.arm == main_arm), key=lambda s: s.seed)
    main = results[main_runs[0].key]

    def same_seed(arm: str) -> RunResult | None:
        cands = sorted(
            (s for s in specs if s.arm == arm), key=lambda s: (s.seed != main.spec.seed, s.seed)
        )
        return results[cands[0].key] if cands else None

    raw, mask = shards.physics_raw()
    earth = BODIES.index("earth")
    seeds_res: dict[str, Any] | Unavailable
    earth_sel = main.ev.body_index == earth
    if len(main_runs) >= 2:
        seeds_res = seed_stability(
            {s.seed: results[s.key].target.x for s in main_runs},
            {s.seed: results[s.key].ev.take(earth_sel).x for s in main_runs},
            main.ev.eval_block[earth_sel],
            n_resamples=n_resamples,
        )
    else:
        seeds_res = Unavailable("fewer than two main seeds were supplied")
    held_spec = next((s for s in specs if is_held_out_run(s)), None)
    held_records = held_out_records(
        results[held_spec.key] if held_spec else None, main, shards, n_resamples
    )
    tests = record_set(
        main,
        n_resamples,
        random_init=same_seed("random_init"),
        lambda0=same_seed("lambda_0"),
        seeds=seeds_res,
        held_out=held_records,
    )
    arms: dict[str, Any] = {}
    for s in specs:
        r = results[s.key]
        arm = arms.setdefault(
            s.arm,
            {"lambda_max": s.lambda_max, "use_phys_head": s.use_phys_head, "runs": {}},
        )
        arm["runs"][s.key] = {
            "seed": s.seed,
            "gpu_hours": s.gpu_hours,
            "checkpoint_sha256s": s.checkpoints,
            "records": [t.model_dump(mode="json") for t in compact_records(r, n_resamples)],
            "alphas": r.probe.alphas,
            "logistic_c": r.body.c,
            "seconds": r.seconds,
        }

    checkpoint_hashes = sorted({h for s in specs for h in s.checkpoints.values()})
    gpu = [s.gpu_hours for s in specs if s.arm != "random_init"]
    run_record = RunRecord(
        config_hash=_sha(config_path),
        data_manifest_sha256=shards.data_manifest_sha256,
        split_map_sha256=shards.manifest["files"]["split_map.json"]["sha256"],
        checkpoint_sha256s=checkpoint_hashes,
        git_sha=git_sha(),
        uv_lock_sha256=_sha(uv_lock or REPO_ROOT / "uv.lock"),
        hardware=hardware_string(),
        gpu_hours_total=float(sum(g for g in gpu if g is not None))
        if any(g is not None for g in gpu)
        else None,
        gpu_hours_partial=any(g is None for g in gpu) or not gpu,
    )
    import torch

    counts = shards.manifest.get("counts_by_body_split", {})
    source_ids = sorted({str(x) for x in shards.column("source_id")})
    ex = build_examples(main, shards, raw, mask)
    document: dict[str, Any] = {
        "format": FORMAT,
        "mock": shards.mock,
        "evaluated_split": split,
        "model_id": "terraluna-encoder-experimental",
        "status": "EXPERIMENTAL",
        "evidence_label": "Predicted",
        "main_arm": main_arm,
        "main_run": main.spec.key,
        "main_selection": (
            "lambda_max>0 physics-head group with most seeds; point values from its lowest seed"
        ),
        "tests": [t.model_dump(mode="json") for t in tests],
        "arms": arms,
        "details": {
            "agreement": {
                "n_pairs_used": main.agreement["n_pairs"],
                "n_pairs_dropped_masked": main.agreement["n_pairs_dropped"],
                "pair_seed": AGREEMENT_SEED,
            },
            "seed_stability": None
            if isinstance(seeds_res, Unavailable)
            else {k: v for k, v in seeds_res.items() if k != "interval"},
            "ood": {
                "threshold": main.ood.threshold,
                "regularisation": main.ood.regularisation,
                "percentile": main.ood.percentile,
                "n_train": main.ood.n_train,
            },
            "train_subsample": subsample,
            "probe_alpha_selection": "validation rows"
            + (
                " (the same rows as the evaluated split: optimistic dry run)"
                if split == "val"
                else ""
            ),
            "block_unit": "eval_block",
            "target_exclusion": (
                "enforced by the dataset build (split=='target' rows never enter a probe set); "
                "not re-verified here"
            ),
        },
        "run_record": json.loads(run_record.model_dump_json()),
        "freeze": {
            "sha256": fhash,
            "written_before_test_read": True,
            "path": str(freeze_path or out_dir.parent / "val" / "freeze.json")
            if split == "test"
            else str(out_dir / "freeze.json"),
        },
        "ml_g5_thresholds_at_freeze": freeze["ml_g5_thresholds"],
        "environment": {
            "python": sys.version.split()[0],
            "torch": torch.__version__,
            "device": dev,
        },
        "wall_clock_s": {
            **{
                f"{s.key}:{k}": round(v, 3)
                for s in specs
                for k, v in results[s.key].seconds.items()
            },
            "total": round(time.perf_counter() - wall0, 3),
        },
        "model": {
            "parameter_count": main.parameter_count,
            "architecture": main.architecture,
            "seeds": sorted({s.seed for s in main_runs}),
            "config": {k: v for k, v in main.spec.config.items() if not k.startswith("_")},
        },
        "training_data": {
            "counts_by_body_split": counts,
            "product_ids": source_ids,
            "bodies": list(BODIES),
        },
        "unavailable": {
            "morphology_probe": probes.MORPHOLOGY_UNAVAILABLE_REASON,
            "target_retrieval": REASON_RETRIEVAL,
            "imagenet_resnet18_and_dino_vits8": REASON_IMAGENET,
            "held_out_body": None if held_records else REASON_HELD_OUT,
        },
    }
    curves = {
        "format": FORMAT,
        "evaluated_split": split,
        "mock": shards.mock,
        "examples": ex,
        "sweep": [
            {
                "arm": a,
                "lambda_max": v["lambda_max"],
                "use_phys_head": v["use_phys_head"],
                "runs": sorted(v["runs"]),
            }
            for a, v in sorted(arms.items())
        ],
    }
    # validate every record before writing anything
    for t in document["tests"]:
        MetricRecord.model_validate(t)
    (out_dir / "evaluation_metrics.json").write_text(canonical_json(document), encoding="utf-8")
    (out_dir / "evaluation_curves.json").write_text(canonical_json(curves), encoding="utf-8")
    return document


def compact_records(r: RunResult, n_resamples: int) -> list[MetricRecord]:
    """The records kept for every run: pooled physics R2, body accuracy, agreement and OOD."""
    recs = [
        _record(
            "physics_probe",
            "r2",
            t,
            "pooled",
            probes.physics_r2(r.probe, t, n_resamples=n_resamples),
            n_resamples,
        )
        for t in PHYSICS_TARGETS
    ]
    recs.append(
        _record("body_invariance", "balanced_accuracy", None, "pooled", r.body_ba, n_resamples)
    )
    for name in ("rms_log_nu_10", "rms_log_nu_relief_5", "rms_log_nu_texture_5"):
        res = r.agreement[name]
        assert not isinstance(res, int)
        recs.append(_record("physics_agreement", "spearman_rho", name, "pooled", res, n_resamples))
    return [*recs, *ood_records(r, n_resamples)]


# --- CLI -----------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None, *, loaders: Loaders = DEFAULT_LOADERS) -> int:
    parser = argparse.ArgumentParser(prog="python -m terraluna_ml.evaluate")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="evaluate runs on val or test blocks")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--runs", nargs="+", required=True, help="run dirs or random_init[:seed]")
    run.add_argument("--split", choices=("val", "test"), required=True)
    run.add_argument("--out", type=Path, default=None)
    run.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "run.yaml")
    run.add_argument("--operator", default=None)
    run.add_argument("--confirm", default=None)
    run.add_argument("--allow-reread", action="store_true")
    run.add_argument("--freeze", type=Path, default=None)
    run.add_argument("--read-log", type=Path, default=None)
    run.add_argument("--max-train", type=int, default=MAX_TRAIN_ROWS)
    run.add_argument("--device", default=None, help="default cpu")
    run.add_argument("--threads", type=int, default=None, help="torch CPU threads")
    run.add_argument("--n-pairs", type=int, default=N_PAIRS)
    run.add_argument("--n-resamples", type=int, default=stats.N_RESAMPLES)
    g5p = sub.add_parser("g5", help="compute the G5 conditions from evaluation_metrics.json")
    g5p.add_argument("--metrics", type=Path, required=True)
    g5p.add_argument("--out", type=Path, required=True)
    g5p.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "run.yaml")
    g5p.add_argument("--parity", type=Path, default=None)
    g5p.add_argument("--gate-out", type=Path, default=Path("outputs/gates/G5.json"))
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "run":
            out = args.out or Path("outputs/ml/evaluation") / args.split
            if args.threads:
                import torch

                torch.set_num_threads(args.threads)
            run_evaluation(
                manifest=args.manifest,
                run_tokens=args.runs,
                split=args.split,
                out_dir=out,
                config_path=args.config,
                loaders=loaders,
                operator=args.operator,
                confirm=args.confirm,
                allow_reread=args.allow_reread,
                read_log=args.read_log,
                freeze_path=args.freeze,
                max_train=args.max_train,
                n_pairs=args.n_pairs,
                n_resamples=args.n_resamples,
                device=args.device,
                command_line=" ".join(sys.argv if argv is None else ["evaluate", *argv]),
            )
            print(f"wrote {out}/evaluation_metrics.json")
            return 0
        metrics = _read_json(args.metrics)
        parity = _read_json(args.parity) if args.parity else None
        thresholds = g5mod.load_thresholds(args.config)
        ckpts = sorted(
            {
                h
                for per_run in metrics.get("run_record", {}).get("checkpoint_sha256s", [])
                for h in [per_run]
            }
        )
        gate = g5mod.write_g5(
            metrics,
            thresholds,
            args.out,
            args.gate_out,
            parity=parity,
            checkpoint_sha256s=ckpts,
            run_id=f"sha256:{metrics['freeze']['sha256']}" if "freeze" in metrics else None,
        )
        print(f"G5 {gate.status}: {gate.result}")
        return 0
    except (
        EvaluationError,
        g5mod.G5ThresholdError,
        ValueError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
