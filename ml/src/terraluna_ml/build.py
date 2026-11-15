"""Real encoder dataset build (T11-08 at reduced, recorded scope; PRD 11.2, 11.5).

Run on the CPU data server (8 cores, 94 GB RAM)::

    uv run python -m terraluna_ml.build --raw /data/terraluna/raw --out <dir> --seed 0

Inputs (each with its PROVENANCE.json under ``--raw``): LOLA 80S 20 m/px (``lola_pgda90``,
Moon), CTX DTMs (``ctx_dtm``, Mars, each with its ``qa_metrics.txt``), Copernicus GLO-30
(``copernicus_glo30``, Earth) masked with ESA WorldCover v200 (``esa_worldcover``), and the
PGDA78 Site01 product for the Moon target window (``pgda78``).

Every window goes through ``dataset.sample_window`` (the D29 resampler) and its physics targets
through ``terraluna_pipeline.terrain.terrain_fingerprint`` on the 90 m analysis grid (55 x 55,
``geometry.window_grid(5000, 90)``), resampled by the same D29 call. Rules applied, in order,
each rejected window keeping its reason (``rejections.parquet``):

1. block interior: the four window corners lie in the window's split block (Moon sector, Earth
   2-degree block), so no window straddles two splits; Mars blocks are whole DTM groups;
2. ``terrain.min_valid_fraction`` on the 90 m grid (``below_min_valid_fraction``);
3. Earth: WorldCover eligibility, nearest class at the 90 m D29 centres (ANA-05), allowed
   classes and minimum fraction from config ``earth_search.masks``, water rule class 80 (G0-09),
   unknown or nodata beyond ``1 - min_allowed_fraction`` is ``land_cover_unknown``;
4. Mars: DTMs whose ``qa_metrics`` CTX-MOLA standard deviation exceeds ``--ctx-qa-max-std-m``
   (G0-06 accepted default 10 m) are dropped whole and listed;
5. target leakage (PRD 11.2, 11.10): windows within 10 km of a target footprint are excluded
   (great-circle distance between centres below 10 km plus both half-diagonals, or for a target
   bbox the distance to the bbox below 10 km plus the window half-diagonal). The build checks
   the kept set again as its last step and refuses to write on failure.

The counts are the reduced scope of this run (the PRD 11.2 request is 600,000 windows; this
build's actual counts are recorded in the manifest, never the requested ones).
"""

from __future__ import annotations

import argparse
import datetime
import math
import multiprocessing as mp
import os
import platform
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

import numpy as np
import numpy.typing as npt
import pyarrow as pa
import pyarrow.parquet as pq

from terraluna_ml import shards
from terraluna_ml.dataset import WINDOW_GRID, input_encoding_record, sample_window
from terraluna_ml.sources import ClassRaster, RasterDemSource, open_dem, read_class_crop
from terraluna_ml.splits import (
    SplitConfig,
    build_split_map,
    earth_block,
    moon_block,
    tile_key,
)
from terraluna_pipeline.config import TerrainConfig, load_run_config
from terraluna_pipeline.geometry import aeqd_inverse, body_radius_m, resample_window, window_grid
from terraluna_pipeline.terrain import terrain_fingerprint

ISOTROPIC_RULE: Final = "direction_mean"
"""The isotropic summary used for the targets (terrain.py offers two; recorded)."""
LEAKAGE_BUFFER_M = 10_000.0
WINDOW_HALF_DIAGONAL_M = 2500.0 * math.sqrt(2.0)
WORLDCOVER_CODES = {
    "tree": 10,
    "shrubland": 20,
    "grassland": 30,
    "cropland": 40,
    "built_up": 50,
    "bare_sparse": 60,
    "snow_ice": 70,
    "water": 80,
    "herbaceous_wetland": 90,
    "mangroves": 95,
    "moss_lichen": 100,
}
GRID90 = window_grid(5000.0, 90.0)
VAL_TEST_FACTOR = 1.0
"""Candidate density in validation and test blocks relative to training blocks."""


@dataclass(frozen=True)
class Footprint:
    name: str
    body: str
    lat_deg: float | None = None
    lon_deg: float | None = None
    half_diagonal_m: float = 0.0
    bbox: tuple[float, float, float, float] | None = None  # lon_min, lat_min, lon_max, lat_max


def great_circle_m(lat1: float, lon1: float, lat2: float, lon2: float, radius: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    dp = p2 - p1
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def footprint_distance_m(fp: Footprint, lat: float, lon: float) -> float:
    """Distance from a window centre to the footprint edge allowance (see module rule 5)."""
    radius = body_radius_m(fp.body)
    if fp.bbox is not None:
        lon_min, lat_min, lon_max, lat_max = fp.bbox
        lo = ((lon - lon_min + 180.0) % 360.0) - 180.0 + lon_min
        clat = min(max(lat, lat_min), lat_max)
        clon = min(max(lo, lon_min), lon_max)
        return great_circle_m(lat, lo, clat, clon, radius)
    assert fp.lat_deg is not None and fp.lon_deg is not None
    return great_circle_m(lat, lon, fp.lat_deg, fp.lon_deg, radius) - fp.half_diagonal_m


def leakage_hit(footprints: Sequence[Footprint], body: str, lat: float, lon: float) -> str | None:
    for fp in footprints:
        if fp.body != body:
            continue
        if footprint_distance_m(fp, lat, lon) < LEAKAGE_BUFFER_M + WINDOW_HALF_DIAGONAL_M:
            return fp.name
    return None


def check_leakage(footprints: Sequence[Footprint], rows: Sequence[dict[str, Any]]) -> list[str]:
    """Window ids of kept non-target rows that violate the buffer (empty means pass)."""
    return [
        str(r["window_id"])
        for r in rows
        if r["split"] != "target" and leakage_hit(footprints, r["body"], r["lat_deg"], r["lon_deg"])
    ]


# --- Per-window processing (workers) ----------------------------------------------------------

_LOLA: RasterDemSource | None = None
_CTX: dict[str, Any] = {}


@dataclass(frozen=True)
class Context:
    terrain: TerrainConfig
    allowed_codes: tuple[int, ...]
    min_allowed_fraction: float
    footprints: tuple[Footprint, ...]
    worldcover_dir: str
    seed: int


def _points(grid: Any, lat0: float, lon0: float, body: str) -> tuple[Any, Any]:
    x, y = grid.mesh_m()
    return aeqd_inverse(
        x, y, lat0_rad=math.radians(lat0), lon0_rad=math.radians(lon0), radius_m=body_radius_m(body)
    )


def _corners(lat0: float, lon0: float, body: str) -> tuple[Any, Any]:
    x = np.array([-2500.0, 2500.0, -2500.0, 2500.0])
    y = np.array([-2500.0, -2500.0, 2500.0, 2500.0])
    lat, lon = aeqd_inverse(
        x, y, lat0_rad=math.radians(lat0), lon0_rad=math.radians(lon0), radius_m=body_radius_m(body)
    )
    return np.degrees(lat), np.degrees(lon)


def _worldcover_tile(directory: str, lat: float, lon: float) -> Path:
    la = int(math.floor(lat / 3.0) * 3)
    lo = int(math.floor(lon / 3.0) * 3)
    ns = "N" if la >= 0 else "S"
    ew = "E" if lo >= 0 else "W"
    return (
        Path(directory) / f"ESA_WorldCover_10m_2021_v200_{ns}{abs(la):02d}{ew}{abs(lo):03d}_Map.tif"
    )


def land_cover_status(
    classes: npt.NDArray[np.int32], allowed: Sequence[int], min_fraction: float
) -> dict[str, Any]:
    """WorldCover window status (T11-03 rules; masks.py is not yet merged, see notes)."""
    n = int(classes.size)
    legend = set(WORLDCOVER_CODES.values())
    unknown = int(np.count_nonzero(~np.isin(classes, list(legend))))
    n_allowed = int(np.count_nonzero(np.isin(classes, list(allowed))))
    water = int(np.count_nonzero(classes == WORLDCOVER_CODES["water"]))
    snow = int(np.count_nonzero(classes == WORLDCOVER_CODES["snow_ice"]))
    if unknown > (1.0 - min_fraction) * n:
        status, reason = "unknown", "land_cover_unknown"
    elif n_allowed >= min_fraction * n:
        status, reason = "eligible", None
    else:
        status, reason = "rejected", "land_cover_below_min_fraction"
    return {
        "status": status,
        "reason": reason,
        "n": n,
        "allowed_fraction": n_allowed / n,
        "unknown": unknown,
        "water": water,
        "snow_ice": snow,
    }


def process_window(
    source: RasterDemSource,
    lat0: float,
    lon0: float,
    *,
    ctx: Context,
    block: str,
    split: str,
    window_id: str,
    corner_block: Any = None,
) -> tuple[dict[str, Any], npt.NDArray[np.float16] | None]:
    body = source.body
    rec: dict[str, Any] = {
        "window_id": window_id,
        "body": body,
        "lat_deg": float(lat0),
        "lon_deg": float(lon0),
        "split": split,
        "block_id": block,
        "source_id": source.source_id,
        "reason": None,
    }
    if corner_block is not None:
        clat, clon = _corners(lat0, lon0, body)
        if any(corner_block(float(a), float(b)) != block for a, b in zip(clat, clon, strict=True)):
            rec["reason"] = "straddles_block_boundary"
            return rec, None
    if split != "target":
        hit = leakage_hit(ctx.footprints, body, lat0, lon0)
        if hit is not None:
            rec["reason"] = f"target_leakage:{hit}"
            return rec, None
    lat64, lon64 = _points(WINDOW_GRID, lat0, lon0, body)
    lat90, lon90 = _points(GRID90, lat0, lon0, body)
    crop = source.crop(
        np.concatenate([lat64.ravel(), lat90.ravel()]),
        np.concatenate([lon64.ravel(), lon90.ravel()]),
    )
    resampled = resample_window(
        crop.raster,
        crop.lookup,
        grid=GRID90,
        body=body,
        lat0_rad=math.radians(lat0),
        lon0_rad=math.radians(lon0),
        nodata=crop.nodata,
    )
    fp = terrain_fingerprint(
        np.ma.masked_invalid(resampled.values),
        valid=resampled.valid,
        grid=GRID90,
        config=ctx.terrain,
        isotropic_rule=ISOTROPIC_RULE,
        coarsening_factor=1.0,
    )
    rec["analysis_valid_fraction"] = float(fp.valid_fraction)
    if fp.excluded:
        rec["reason"] = str(fp.excluded_reason)
        return rec, None
    if body == "earth":
        tile = _worldcover_tile(ctx.worldcover_dir, lat0, lon0)
        classes: npt.NDArray[np.int32]
        lat_d, lon_d = np.degrees(lat90), np.degrees(lon90)
        crop_wc: ClassRaster | None = (
            read_class_crop(tile, lat_d, lon_d) if tile.is_file() else None
        )
        if crop_wc is None:
            rec["reason"] = "worldcover_missing"
            return rec, None
        classes = crop_wc.sample_nearest(lat90, lon90)
        lc = land_cover_status(classes, ctx.allowed_codes, ctx.min_allowed_fraction)
        rec["land_cover"] = lc
        if lc["status"] != "eligible":
            rec["reason"] = str(lc["reason"])
            return rec, None
    relief = fp.variants["none"]
    texture = fp.variants["plane"]
    values: list[float] = []
    for variant in (relief, texture):
        for nu in variant.nu_m:
            values.append(math.log(nu) if nu is not None and nu > 0.0 else math.nan)
    h = relief.hurst.H
    values.append(float(h) if h is not None else math.nan)
    rec["phys_raw"] = values
    rec["phys_reasons"] = [
        None if math.isfinite(v) else "unsupported_or_zero_nu_or_H_unavailable" for v in values
    ]
    window = sample_window(crop, lat0, lon0)
    rec["valid_fraction"] = float(window.valid_fraction)
    return rec, window.storage()


def _task_moon(args: tuple[Context, list[tuple[str, float, float, str]]]) -> list[Any]:
    ctx, items = args
    assert _LOLA is not None
    out = []
    for window_id, lat, lon, split in items:
        block = moon_block(lon)
        out.append(
            process_window(
                _LOLA,
                lat,
                lon,
                ctx=ctx,
                block=block,
                split=split,
                window_id=window_id,
                corner_block=lambda a, b: moon_block(b),
            )
        )
    return out


def _valid_centres(
    source: RasterDemSource, n: int, rng: np.random.Generator, margin_px: int
) -> list[tuple[float, float]]:
    raster = source.raster
    good = np.isfinite(raster)
    if source.nodata is not None:
        good &= raster != np.float32(source.nodata)
    h, w = raster.shape
    if h <= 2 * margin_px or w <= 2 * margin_px:
        return []
    inner = good[margin_px : h - margin_px, margin_px : w - margin_px]
    idx = np.flatnonzero(inner)
    if idx.size == 0:
        return []
    pick = rng.choice(idx, size=min(n, idx.size), replace=False)
    rr, cc = np.unravel_index(pick, inner.shape)
    lat, lon = source.lookup.from_pixel(cc + margin_px, rr + margin_px)
    return [(float(np.degrees(a)), float(np.degrees(b))) for a, b in zip(lat, lon, strict=True)]


def _task_raster(args: tuple[Context, dict[str, Any]]) -> list[Any]:
    ctx, job = args
    source = open_dem(Path(job["path"]), job["body"], job["source_id"])
    rng = np.random.default_rng([ctx.seed, int(job["index"])])
    pixel = abs(float(getattr(source.lookup, "pixel_width_m", 30.0)))
    margin = math.ceil(2600.0 / pixel) if job["body"] == "mars" else 90
    good = np.isfinite(source.raster)
    if source.nodata is not None:
        good &= source.raster != np.float32(source.nodata)
    valid_km2 = (
        float(np.count_nonzero(good)) * (pixel / 1000.0) ** 2 if job["body"] == "mars" else None
    )
    n = int(job["n"]) if valid_km2 is None else math.ceil(valid_km2 / 25.0 * float(job["density"]))
    out = []
    for k, (lat, lon) in enumerate(_valid_centres(source, n, rng, margin)):
        if job["body"] == "earth":
            block = earth_block(lat, lon)
            if block != job["block"]:
                continue
            split = job["roles"].get(block, "dropped")
            corner = earth_block
        else:
            block = job["block"]
            split = job["split"]
            corner = None
        if split == "dropped":
            continue
        wid = f"{job['body']}-{job['short']}-{job['index']:04d}-{k:05d}"
        out.append(
            process_window(
                source,
                lat,
                lon,
                ctx=ctx,
                block=block,
                split=split,
                window_id=wid,
                corner_block=corner,
            )
        )
    return out


# --- Planning -----------------------------------------------------------------------------------


def ctx_qa_std(directory: Path) -> float | None:
    path = directory / "qa_metrics.txt"
    if not path.is_file():
        return None
    m = re.search(r"StdDev of difference:\s*([0-9.eE+-]+)", path.read_text(errors="replace"))
    return float(m.group(1)) if m else None


def _git_sha(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return os.environ.get("TERRALUNA_GIT_SHA", "unavailable")


def moon_target(raw: Path, ctx: Context) -> tuple[Footprint, RasterDemSource, float, float]:
    """The frozen Moon target window (G0-02: moon-sps-x2-y2 on PGDA78 Site01)."""
    path = raw / "pgda78/Site01/Site01_final_adj_5mpp_surf.tif"
    src = open_dem(path, "moon", "pgda78:Site01_final_adj_5mpp_surf")
    lk = src.lookup
    x0 = lk.origin_x_m  # type: ignore[union-attr]
    y_top = lk.origin_y_m  # type: ignore[union-attr]
    y0 = y_top + lk.pixel_height_m * src.raster.shape[0]  # type: ignore[union-attr]
    stride, size = 2500.0, 5000.0
    cx, cy = x0 + 2 * stride + size / 2, y0 + 2 * stride + size / 2
    lat, lon = lk.unproject(np.array([cx]), np.array([cy]))
    lat_d, lon_d = float(np.degrees(lat[0])), float(np.degrees(lon[0]))
    fp = Footprint("moon-sps-x2-y2", "moon", lat_d, lon_d, WINDOW_HALF_DIAGONAL_M)
    return fp, src, lat_d, lon_d


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="terraluna_ml.build", description=__doc__.split("\n")[0])
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/run.yaml"))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument(
        "--moon", type=int, nargs=3, default=[6000, 2000, 3000], metavar=("TRAIN", "VAL", "TEST")
    )
    parser.add_argument("--earth-per-tile", type=int, default=520)
    parser.add_argument(
        "--mars-density", type=float, default=4.0, help="candidates per 25 km2 of valid DTM"
    )
    parser.add_argument("--ctx-qa-max-std-m", type=float, default=10.0)
    parser.add_argument(
        "--limit-sources", type=int, default=0, help="debug: first N sources per body"
    )
    args = parser.parse_args(argv)
    started = time.time()
    if args.out.exists() and any(args.out.iterdir()):
        print(f"refusing to overwrite non-empty {args.out}", file=sys.stderr)
        return 1

    run = load_run_config(args.config)
    masks = run.value("earth_search.masks")
    assert isinstance(masks, dict)
    allowed = tuple(WORLDCOVER_CODES[name] for name in masks["worldcover_allowed"])
    if masks.get("water") != "worldcover_class_80":
        print("blocked: earth_search.masks.water is not worldcover_class_80", file=sys.stderr)
        return 3
    raw: Path = args.raw
    jezero = None
    for t in cast(list[dict[str, Any]], run.value("targets.mars") or []):
        if t.get("bbox"):
            b = t["bbox"]
            jezero = Footprint(f"mars-{t['name']}-bbox", "mars", bbox=(b[0], b[1], b[2], b[3]))
    ctx0 = Context(
        terrain=run.terrain,
        allowed_codes=allowed,
        min_allowed_fraction=float(masks["min_allowed_fraction"]),
        footprints=(),
        worldcover_dir=str(raw / "esa_worldcover/v200/2021/map"),
        seed=args.seed,
    )
    moon_fp, site01, tlat, tlon = moon_target(raw, ctx0)
    footprints = tuple(f for f in (moon_fp, jezero) if f is not None)
    unset_footprints = [
        f"mars-{t['name']}: bbox and primary_window_id unset, leakage not checkable"
        for t in cast(list[dict[str, Any]], run.value("targets.mars") or [])
        if not t.get("bbox") and not t.get("primary_window_id")
    ]
    ctx = Context(**{**ctx0.__dict__, "footprints": footprints})

    # Mars DTMs after the CTX QA filter.
    ctx_dirs = sorted(
        p for p in (raw / "ctx_dtm").iterdir() if p.is_dir() and list(p.glob("*_dtm.tif"))
    )
    mars_jobs: list[dict[str, Any]] = []
    ctx_dropped: dict[str, str] = {}
    import rasterio  # type: ignore[import-untyped]

    mars_tiles: dict[str, list[tuple[int, int]]] = {}
    for d in ctx_dirs:
        std = ctx_qa_std(d)
        if std is None:
            ctx_dropped[d.name] = "qa_metrics missing"
            continue
        if std > args.ctx_qa_max_std_m:
            ctx_dropped[d.name] = f"ctx_mola_std {std:.3f} m > {args.ctx_qa_max_std_m} m"
            continue
        tif = next(d.glob("*_dtm.tif"))
        with rasterio.open(tif) as s:
            b = s.bounds
            R = float(s.crs.to_dict().get("R"))
        lat_c = math.degrees(((b.top + b.bottom) / 2) / R)
        lon_c = math.degrees(((b.left + b.right) / 2) / R)
        corners = [
            (math.degrees(y / R), math.degrees(x / R))
            for x in (b.left, b.right)
            for y in (b.bottom, b.top)
        ]
        tiles = [tile_key(lat_c, lon_c, 10.0)] + [tile_key(a, c, 10.0) for a, c in corners]
        mars_tiles[d.name] = list(dict.fromkeys(tiles))
        mars_jobs.append(
            {
                "path": str(tif),
                "body": "mars",
                "source_id": f"ctx_dtm:{d.name}",
                "short": d.name[:22],
                "std": std,
            }
        )
    if args.limit_sources:
        mars_jobs = mars_jobs[: args.limit_sources]
        mars_tiles = {
            k: v for k, v in mars_tiles.items() if any(k in j["source_id"] for j in mars_jobs)
        }

    cop_dirs = sorted(p for p in (raw / "copernicus_glo30").iterdir() if p.is_dir())
    earth_jobs: list[dict[str, Any]] = []
    earth_blocks: set[str] = set()
    lat: int | npt.NDArray[np.float64]
    lon: int | npt.NDArray[np.float64]
    for d in cop_dirs:
        m = re.search(r"_([NS])(\d\d)_00_([EW])(\d\d\d)_00_DEM$", d.name)
        if not m:
            continue
        lat = int(m.group(2)) * (1 if m.group(1) == "N" else -1)
        lon = int(m.group(4)) * (1 if m.group(3) == "E" else -1)
        if not _worldcover_tile(ctx.worldcover_dir, lat + 0.5, lon + 0.5).is_file():
            continue  # no WorldCover tile here: every window would be worldcover_missing
        tif = d / f"{d.name}.tif"
        block = earth_block(lat + 0.5, lon + 0.5)
        earth_blocks.add(block)
        earth_jobs.append(
            {
                "path": str(tif),
                "body": "earth",
                "source_id": f"copernicus_glo30:{d.name}",
                "short": d.name[22:37],
                "block": block,
                "n": args.earth_per_tile,
            }
        )
    if args.limit_sources:
        earth_jobs = earth_jobs[: args.limit_sources]
        earth_blocks = {j["block"] for j in earth_jobs}

    split_config = SplitConfig(seed=args.seed)
    split_map, dtm_block = build_split_map(
        mars_dtm_tiles=mars_tiles, earth_blocks=sorted(earth_blocks), config=split_config
    )
    roles = split_map.assignment
    for i, job in enumerate(mars_jobs):
        name = job["source_id"].split(":", 1)[1]
        job["block"] = dtm_block[name]
        job["split"] = roles[job["block"]]
        job["density"] = args.mars_density * (1.0 if job["split"] == "train" else VAL_TEST_FACTOR)
        job["index"] = 1000 + i
    for i, job in enumerate(earth_jobs):
        job["roles"] = roles
        job["index"] = 5000 + i
        if roles[job["block"]] != "train":
            job["n"] = int(job["n"] * VAL_TEST_FACTOR)
    mars_jobs = [j for j in mars_jobs if j["split"] != "dropped"]
    earth_jobs = [j for j in earth_jobs if roles[j["block"]] != "dropped"]

    # Moon candidates: uniform in area over the 80S cap, per sector role.
    global _LOLA
    lola_path = raw / "lola_pgda90/LOLA_20mpp/LDEM_80S_20MPP_ADJ.TIF"
    print(f"reading {lola_path}", flush=True)
    _LOLA = open_dem(lola_path, "moon", "lola_pgda90:LDEM_80S_20MPP_ADJ")
    rng = np.random.default_rng([args.seed, 7])
    quota = dict(zip(("train", "val", "test"), args.moon, strict=True))
    moon_items: list[tuple[str, float, float, str]] = []
    half = _LOLA.raster.shape[0] * abs(_LOLA.lookup.pixel_width_m) / 2  # type: ignore[union-attr]
    r_max = half - 4000.0
    counts: Counter[str] = Counter()
    k = 0
    while any(counts[s] < quota[s] for s in quota) and k < 10 * sum(quota.values()):
        r = r_max * math.sqrt(float(rng.uniform()))
        th = float(rng.uniform(0, 2 * math.pi))
        x, y = r * math.cos(th), r * math.sin(th)
        lat, lon = _LOLA.lookup.unproject(np.array([x]), np.array([y]))
        lat_d, lon_d = float(np.degrees(lat[0])), float(np.degrees(lon[0]))
        role = roles[moon_block(lon_d)]
        k += 1
        if role == "dropped" or counts[role] >= quota[role]:
            continue
        counts[role] += 1
        moon_items.append((f"moon-lola80s-{k:06d}", lat_d, lon_d, role))
    moon_chunks = [moon_items[i : i + 200] for i in range(0, len(moon_items), 200)]

    print(
        f"jobs: moon {len(moon_items)} windows, mars {len(mars_jobs)} DTMs, "
        f"earth {len(earth_jobs)} tiles; workers {args.workers}",
        flush=True,
    )
    results: list[tuple[dict[str, Any], Any]] = []
    with mp.get_context("fork").Pool(args.workers) as pool:
        jobs_r = [
            (ctx, j)
            for j in sorted(mars_jobs + earth_jobs, key=lambda j: -os.path.getsize(j["path"]))
        ]
        it_r = pool.imap_unordered(_task_raster, jobs_r)
        it_m = pool.imap_unordered(_task_moon, [(ctx, c) for c in moon_chunks])
        done = 0
        for part in it_m:
            results.extend(part)
            done += 1  # noqa: SIM113
            if done % 10 == 0:
                print(
                    f"moon chunks {done}/{len(moon_chunks)} {time.time() - started:.0f}s",
                    flush=True,
                )
        done = 0
        for part in it_r:
            results.extend(part)
            done += 1  # noqa: SIM113
            if done % 10 == 0:
                print(f"raster jobs {done}/{len(jobs_r)} {time.time() - started:.0f}s", flush=True)

    # Target row(s): never trained on, never in a probe set.
    tgt_rec, tgt_win = process_window(
        site01,
        tlat,
        tlon,
        ctx=ctx,
        block="target:moon-sps-x2-y2",
        split="target",
        window_id="moon-sps-x2-y2",
    )
    results.append((tgt_rec, tgt_win))

    kept = [(r, w) for r, w in results if r["reason"] is None and w is not None]
    rejected = [r for r, w in results if r["reason"] is not None]
    kept.sort(key=lambda rw: (shards.BODIES.index(rw[0]["body"]), rw[0]["window_id"]))
    ids = [r["window_id"] for r, _ in kept]
    if len(set(ids)) != len(ids):
        print("duplicate window ids", file=sys.stderr)
        return 1
    bad = check_leakage(footprints, [r for r, _ in kept])
    if bad:
        print(f"leakage check failed for {len(bad)} windows, nothing written", file=sys.stderr)
        return 1

    windows = np.stack([w for _, w in kept]).astype(np.float16)
    recs = [r for r, _ in kept]
    n = len(recs)
    eval_blocks = []
    for r in recs:
        if r["body"] == "moon" and r["split"] != "target":
            band = min(int((-r["lat_deg"] - 80.0) / (10.0 / 3.0)), 2)
            third = int(((r["lon_deg"] + 180.0) % 90.0) // 30.0)
            eval_blocks.append(f"{r['block_id']}:lat{band}:lon{third}")
        else:
            eval_blocks.append(r["block_id"])
    meta: dict[str, Any] = {
        "window_id": ids,
        "body": [r["body"] for r in recs],
        "body_index": np.asarray([shards.BODIES.index(r["body"]) for r in recs], dtype=np.int8),
        "lat_deg": np.asarray([r["lat_deg"] for r in recs]),
        "lon_deg": np.asarray([r["lon_deg"] for r in recs]),
        "split": [r["split"] for r in recs],
        "block_id": [r["block_id"] for r in recs],
        "eval_block": eval_blocks,
        "source_id": [r["source_id"] for r in recs],
        "valid_fraction": np.asarray([r["valid_fraction"] for r in recs]),
        "analysis_valid_fraction": np.asarray([r["analysis_valid_fraction"] for r in recs]),
        "mock": np.zeros(n, dtype=np.bool_),
    }
    raw_phys = np.asarray([r["phys_raw"] for r in recs], dtype=np.float64)
    for j in range(shards.N_PHYS):
        meta[f"phys_raw_{j:02d}"] = raw_phys[:, j]
        meta[f"phys_mask_{j:02d}"] = np.isfinite(raw_phys[:, j])

    args.out.mkdir(parents=True, exist_ok=True)
    rej_table = pa.table(
        {
            "window_id": [r["window_id"] for r in rejected],
            "body": [r["body"] for r in rejected],
            "split": [r["split"] for r in rejected],
            "block_id": [r["block_id"] for r in rejected],
            "reason": [r["reason"] for r in rejected],
            "lat_deg": [r["lat_deg"] for r in rejected],
            "lon_deg": [r["lon_deg"] for r in rejected],
        }
    )
    pq.write_table(rej_table, args.out / "rejections.parquet", compression="zstd")
    rejection_counts: dict[str, dict[str, int]] = {}
    for r in rejected:
        reason = (
            r["reason"].split(":")[0] if r["reason"].startswith("target_leakage") else r["reason"]
        )
        rejection_counts.setdefault(r["body"], {}).setdefault(reason, 0)
        rejection_counts[r["body"]][reason] += 1
    mask_counts = {
        b: [
            int(
                np.count_nonzero(
                    ~np.isfinite(raw_phys[[i for i, r in enumerate(recs) if r["body"] == b], j])
                )
            )
            for j in range(shards.N_PHYS)
        ]
        for b in shards.BODIES
    }
    elapsed = time.time() - started
    extra = {
        "data_kind": "real",
        "production_eligible": True,
        "seed": args.seed,
        "git_sha": _git_sha(Path.cwd()),
        "command": "python -m terraluna_ml.build "
        + " ".join(sys.argv[1:] if argv is None else list(argv)),
        "generated_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hardware": {
            "host_cpus": os.cpu_count(),
            "workers": args.workers,
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "wall_clock_s": round(elapsed, 1),
        "cpu_hours_upper_bound": round(elapsed * args.workers / 3600.0, 3),
        "input_encoding": input_encoding_record(),
        "physics": {
            "grid": "geometry.window_grid(5000, 90): 55 x 55 at 90 m, same D29 resampler",
            "isotropic_rule": ISOTROPIC_RULE,
            "h_variant": "relief (PRD-06)",
            "terrain_config": str(run.terrain),
            "masked_reason": (
                "lag unsupported, nu zero, or H Unavailable (terrain.py rules); "
                "masked count per body and target in masked_counts"
            ),
            "masked_counts": mask_counts,
        },
        "requested_prd_counts": {
            "moon": 200000,
            "mars": 200000,
            "earth": 200000,
            "note": "PRD 11.2 request; this run's reduced scope is in counts_by_body_split",
        },
        "sampling": {
            "moon": {
                "product": str(lola_path.relative_to(raw)),
                "candidates_per_role": dict(quota),
                "rule": "uniform in projected area within the 80S cap less 4 km",
            },
            "mars": {
                "density_per_25km2_train": args.mars_density,
                "density_val_test": args.mars_density * VAL_TEST_FACTOR,
                "ctx_qa_max_std_m": args.ctx_qa_max_std_m,
                "dtms_used": len(mars_jobs),
                "dtms_dropped": dict(sorted(ctx_dropped.items())),
            },
            "earth": {
                "tiles_used": len(earth_jobs),
                "per_tile_train": args.earth_per_tile,
                "per_tile_val_test": int(args.earth_per_tile * VAL_TEST_FACTOR),
                "worldcover": "v200 2021 map, nearest class at the 90 m D29 centres (ANA-05)",
                "allowed": list(masks["worldcover_allowed"]),
                "min_allowed_fraction": masks["min_allowed_fraction"],
                "water": masks["water"],
            },
        },
        "leakage": {
            "buffer_m": LEAKAGE_BUFFER_M,
            "rule": (
                "exclude if centre distance < 10 km + window half-diagonal + target half-diagonal "
                "(bbox: distance to bbox < 10 km + window half-diagonal); "
                "applied to every split; checked again before writing"
            ),
            "footprints": [f.__dict__ for f in footprints],
            "unset": unset_footprints,
            "check": "pass",
        },
        "rejections": rejection_counts,
        "eval_block_rule": (
            "bootstrap unit: Mars DTM-group block, Earth 2-degree block, "
            "Moon sector x 3 latitude bands x 3 longitude thirds (engineering choice)"
        ),
        "split_map_sha256": split_map.as_dict()["content_sha256"],
        "targets": [
            {
                "window_id": "moon-sps-x2-y2",
                "product": "PGDA78 Site01 5 m surf",
                "lat_deg": tlat,
                "lon_deg": tlon,
            }
        ],
    }
    sha = shards.write_shards(
        args.out, windows=windows, meta=meta, split_map=split_map.as_dict(), manifest_extra=extra
    )
    print(
        f"wrote {n} windows to {args.out}; rejected {len(rejected)}; "
        f"data_manifest_sha256 {sha}; {elapsed:.0f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
