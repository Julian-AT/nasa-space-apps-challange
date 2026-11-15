"""Test-only mock shard directories in the ``terraluna_ml.shards`` format (mock=True)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from terraluna_ml import shards


def make_mock_shards(directory: Path, *, per_body_split: int = 8, seed: int = 0) -> Path:
    """Write a small labelled-mock shard set and return its manifest path.

    Windows are random smooth surfaces; physics targets are simple functions of them so that
    a model can learn something. Every row carries mock=True.
    """
    rng = np.random.default_rng(seed)
    rows: list[tuple[str, str]] = [
        (body, split)
        for body in shards.BODIES
        for split in ("train", "val", "test")
        for _ in range(per_body_split)
    ]
    n = len(rows)
    windows = np.zeros((n, 3, 64, 64), dtype=np.float16)
    raw = np.full((n, shards.N_PHYS), np.nan)
    for i, (body, _split) in enumerate(rows):
        amp = rng.uniform(0.2, 2.0) * (1 + shards.BODIES.index(body))
        z = np.cumsum(np.cumsum(rng.normal(size=(64, 64)), axis=0), axis=1)
        z = amp * (z - z.mean()) / (z.std() + 1e-9)
        windows[i, 0] = np.arcsinh(z).astype(np.float16)
        windows[i, 1] = np.abs(np.gradient(z)[0]).astype(np.float16) / 4
        windows[i, 2] = 1.0
        base = np.log(amp)
        raw[i, :5] = base + np.log(np.arange(1, 6))
        raw[i, 5:10] = base - 0.1 + 0.5 * np.log(np.arange(1, 6))
        raw[i, 10] = 0.5 + 0.1 * amp
    raw[0, 3] = np.nan  # one Unavailable target
    mask = np.isfinite(raw)
    meta: dict[str, object] = {
        "window_id": [f"mock-{b}-{i:04d}" for i, (b, _s) in enumerate(rows)],
        "body": [b for b, _s in rows],
        "body_index": np.asarray([shards.BODIES.index(b) for b, _s in rows], dtype=np.int8),
        "lat_deg": rng.uniform(-80, 80, n),
        "lon_deg": rng.uniform(-180, 180, n),
        "split": [s for _b, s in rows],
        "block_id": [f"mock-{b}-{s}-{i % 2}" for i, (b, s) in enumerate(rows)],
        "eval_block": [f"mock-{b}-{s}-{i % 4}" for i, (b, s) in enumerate(rows)],
        "source_id": ["mock"] * n,
        "valid_fraction": np.ones(n),
        "analysis_valid_fraction": np.ones(n),
        "mock": np.ones(n, dtype=np.bool_),
    }
    for j in range(shards.N_PHYS):
        meta[f"phys_raw_{j:02d}"] = raw[:, j]
        meta[f"phys_mask_{j:02d}"] = mask[:, j]
    shards.write_shards(
        directory,
        windows=windows,
        meta=meta,  # type: ignore[arg-type]
        split_map={"mock": True, "rule": "test helper"},
        manifest_extra={"seed": seed, "data_kind": "mock"},
    )
    return directory / shards.MANIFEST_FILE
