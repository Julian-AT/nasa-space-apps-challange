"""Encoder dataset shards: the on-disk contract between the dataset build and training (T11-05).

A shard directory holds

- ``windows.npy``: float16 ``(n, 3, 64, 64)`` encoder inputs in :data:`dataset.CHANNELS` order,
  row ``i`` described by row ``i`` of the metadata; no NaN (invalid pixels hold 0.0 in channels
  0 and 1 and 0 in the valid channel, the T11-01 fill rule);
- ``meta.parquet``: one row per window (columns in :data:`META_COLUMNS`);
- ``split_map.json``: the spatial block assignment (``terraluna_ml.splits``);
- ``manifest.json``: counts, seeds, the input encoding, the physics-target names, the
  training-only target standardisation and the SHA-256 of every other file.

``data_manifest_sha256`` is the SHA-256 of ``manifest.json`` as written. :func:`open_shards`
verifies every recorded hash before returning anything and raises :class:`ShardError` on a
mismatch. Physics targets are stored raw (``phys_raw_*``, NaN where Unavailable) with a boolean
mask (``phys_mask_*``); standardised values (``phys_std_*``) use statistics fitted on the
training split only and are NaN wherever the mask is False. Missing is never zero.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.parquet as pq

FORMAT: Final = "terraluna-ml-shards/1"
WINDOWS_FILE: Final = "windows.npy"
META_FILE: Final = "meta.parquet"
SPLIT_MAP_FILE: Final = "split_map.json"
MANIFEST_FILE: Final = "manifest.json"

BODIES: Final = ("moon", "mars", "earth")
"""Body order of the 8.13 ``ml.bodies`` block; ``body_index`` is the position in it."""

LAGS_M: Final = (270, 360, 540, 720, 1080)
PHYSICS_TARGETS: Final = (
    *(f"log_nu_relief_{lag}" for lag in LAGS_M),
    *(f"log_nu_texture_{lag}" for lag in LAGS_M),
    "H_relief",
)
"""The 11 physics targets (PRD 11.2): ln nu(L) in metres for relief (raw) and texture (plane
removed) at the five configured lags, then the relief Hurst exponent (decision PRD-06)."""

N_PHYS: Final = len(PHYSICS_TARGETS)

SPLITS: Final = ("train", "val", "test", "target")
"""``target`` rows are target windows (never trained on, never in a probe set)."""

META_COLUMNS: Final = (
    "row",
    "window_id",
    "body",
    "body_index",
    "lat_deg",
    "lon_deg",
    "split",
    "block_id",
    "eval_block",
    "source_id",
    "valid_fraction",
    "analysis_valid_fraction",
    *(f"phys_raw_{i:02d}" for i in range(N_PHYS)),
    *(f"phys_mask_{i:02d}" for i in range(N_PHYS)),
    *(f"phys_std_{i:02d}" for i in range(N_PHYS)),
    "mock",
)


class ShardError(ValueError):
    """A shard directory that is missing, inconsistent or fails its recorded hashes."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(document: object) -> str:
    """Sorted keys, 2-space indent, LF and a trailing newline."""
    return (
        json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )


@dataclass(frozen=True)
class Standardisation:
    """Per-target mean and standard deviation fitted on training rows with a valid target."""

    mean: tuple[float, ...]
    std: tuple[float, ...]
    n_train: tuple[int, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "fit_on": "train split only, valid (unmasked) entries only",
            "targets": list(PHYSICS_TARGETS),
            "mean": list(self.mean),
            "std": list(self.std),
            "n_train": list(self.n_train),
        }


def fit_standardisation(
    raw: npt.NDArray[np.float64], mask: npt.NDArray[np.bool_], split: Sequence[str]
) -> Standardisation:
    """Fit target statistics on training rows only (val and test never enter)."""
    train = np.asarray([s == "train" for s in split], dtype=np.bool_)
    means: list[float] = []
    stds: list[float] = []
    counts: list[int] = []
    for j in range(N_PHYS):
        use = train & mask[:, j]
        n = int(np.count_nonzero(use))
        if n < 2:
            raise ShardError(f"target {PHYSICS_TARGETS[j]} has {n} valid training rows")
        values = raw[use, j]
        means.append(float(np.mean(values)))
        sd = float(np.std(values))
        if not sd > 0.0:
            raise ShardError(f"target {PHYSICS_TARGETS[j]} has zero training variance")
        stds.append(sd)
        counts.append(n)
    return Standardisation(tuple(means), tuple(stds), tuple(counts))


def standardise(
    raw: npt.NDArray[np.float64], mask: npt.NDArray[np.bool_], stats: Standardisation
) -> npt.NDArray[np.float32]:
    out = (raw - np.asarray(stats.mean)) / np.asarray(stats.std)
    out = np.where(mask, out, np.nan)
    return out.astype(np.float32)


def write_shards(
    out_dir: Path,
    *,
    windows: npt.NDArray[np.float16],
    meta: Mapping[str, Sequence[Any] | npt.NDArray[Any]],
    split_map: Mapping[str, object],
    manifest_extra: Mapping[str, object],
) -> str:
    """Write one shard directory and return ``data_manifest_sha256``.

    ``meta`` must hold every column of :data:`META_COLUMNS` except ``row`` and the
    ``phys_std_*`` columns, which are computed here from the training split.
    """
    if windows.dtype != np.float16 or windows.ndim != 4 or windows.shape[1:] != (3, 64, 64):
        raise ShardError(
            f"windows must be float16 (n, 3, 64, 64), got {windows.dtype} {windows.shape}"
        )
    if not np.all(np.isfinite(windows)):
        raise ShardError("a window holds a non-finite value")
    n = windows.shape[0]
    raw = np.column_stack(
        [np.asarray(meta[f"phys_raw_{i:02d}"], dtype=np.float64) for i in range(N_PHYS)]
    )
    mask = np.column_stack(
        [np.asarray(meta[f"phys_mask_{i:02d}"], dtype=np.bool_) for i in range(N_PHYS)]
    )
    if np.any(np.isfinite(raw) != mask):
        raise ShardError("phys_raw must be finite exactly where phys_mask is True")
    split = [str(s) for s in meta["split"]]
    if any(s not in SPLITS for s in split):
        raise ShardError("unknown split value")
    mock = any(bool(m) for m in meta["mock"])
    stats = fit_standardisation(raw, mask, split)
    std = standardise(raw, mask, stats)

    out_dir.mkdir(parents=True, exist_ok=True)
    columns: dict[str, Any] = {"row": np.arange(n, dtype=np.int64)}
    for name in META_COLUMNS:
        if name == "row":
            continue
        if name.startswith("phys_std_"):
            columns[name] = std[:, int(name[-2:])]
            continue
        values = meta[name]
        if len(values) != n:
            raise ShardError(f"column {name} has {len(values)} rows, windows have {n}")
        columns[name] = values
    table = pa.table({name: columns[name] for name in META_COLUMNS})
    np.save(out_dir / WINDOWS_FILE, windows, allow_pickle=False)
    pq.write_table(table, out_dir / META_FILE, compression="zstd")
    (out_dir / SPLIT_MAP_FILE).write_text(canonical_json(split_map), encoding="utf-8")

    counts: dict[str, dict[str, int]] = {}
    for body, s in zip(meta["body"], split, strict=True):
        counts.setdefault(str(body), {}).setdefault(s, 0)
        counts[str(body)][s] += 1
    manifest = {
        "format": FORMAT,
        "mock": mock,
        "n_windows": n,
        "counts_by_body_split": counts,
        "physics_targets": list(PHYSICS_TARGETS),
        "physics_target_log": "natural log of nu in metres",
        "bodies": list(BODIES),
        "target_standardisation": stats.as_dict(),
        "files": {
            WINDOWS_FILE: {
                "sha256": sha256_path(out_dir / WINDOWS_FILE),
                "shape": list(windows.shape),
                "dtype": "float16",
            },
            META_FILE: {"sha256": sha256_path(out_dir / META_FILE)},
            SPLIT_MAP_FILE: {"sha256": sha256_path(out_dir / SPLIT_MAP_FILE)},
        },
        **dict(manifest_extra),
    }
    text = canonical_json(manifest)
    (out_dir / MANIFEST_FILE).write_text(text, encoding="utf-8")
    return sha256_bytes(text.encode("utf-8"))


@dataclass(frozen=True)
class OpenShards:
    """A verified shard directory. ``windows`` is a read-only memory map."""

    directory: Path
    windows: npt.NDArray[np.float16]
    meta: pa.Table
    split_map: dict[str, Any]
    manifest: dict[str, Any]
    data_manifest_sha256: str

    @property
    def mock(self) -> bool:
        return bool(self.manifest["mock"])

    def column(self, name: str) -> npt.NDArray[Any]:
        return np.asarray(self.meta.column(name).to_numpy(zero_copy_only=False))

    def physics(self) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.bool_]]:
        """Standardised targets ``(n, 11)`` (NaN where masked) and the mask ``(n, 11)``."""
        std = np.column_stack([self.column(f"phys_std_{i:02d}") for i in range(N_PHYS)]).astype(
            np.float32
        )
        mask = np.column_stack([self.column(f"phys_mask_{i:02d}") for i in range(N_PHYS)]).astype(
            np.bool_
        )
        return std, mask

    def physics_raw(self) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.bool_]]:
        raw = np.column_stack([self.column(f"phys_raw_{i:02d}") for i in range(N_PHYS)]).astype(
            np.float64
        )
        mask = np.column_stack([self.column(f"phys_mask_{i:02d}") for i in range(N_PHYS)]).astype(
            np.bool_
        )
        return raw, mask

    def rows(self, split: str) -> npt.NDArray[np.int64]:
        return np.flatnonzero(self.column("split") == split).astype(np.int64)


def open_shards(manifest_path: Path) -> OpenShards:
    """Open a shard directory from its manifest, verifying every recorded SHA-256."""
    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        raise ShardError(f"no shard manifest at {manifest_path}")
    text = manifest_path.read_bytes()
    manifest = json.loads(text)
    if manifest.get("format") != FORMAT:
        raise ShardError(f"unknown shard format {manifest.get('format')!r}")
    directory = manifest_path.parent
    for name, record in manifest["files"].items():
        path = directory / name
        if not path.is_file():
            raise ShardError(f"missing shard file {path}")
        if sha256_path(path) != record["sha256"]:
            raise ShardError(f"SHA-256 mismatch for {path}")
    windows = np.load(directory / WINDOWS_FILE, mmap_mode="r", allow_pickle=False)
    meta = pq.read_table(directory / META_FILE)
    if windows.shape[0] != meta.num_rows or windows.shape[0] != manifest["n_windows"]:
        raise ShardError("windows, metadata and manifest disagree on the row count")
    split_map = json.loads((directory / SPLIT_MAP_FILE).read_text(encoding="utf-8"))
    return OpenShards(
        directory=directory,
        windows=windows,
        meta=meta,
        split_map=split_map,
        manifest=manifest,
        data_manifest_sha256=sha256_bytes(text),
    )
