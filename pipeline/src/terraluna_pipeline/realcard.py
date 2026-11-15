"""Real windows for the first evidence card (t4-real T4R-04): read, resample and measure.

This module reads only local caches. It reads the PGDA78 Site01 DEM (with its LOLA return
count map) and one Copernicus GLO-30 tile from ``TERRALUNA_RAW_DIR`` (raw downloads kept
outside every repository, laid out as ``<source>/...`` like the compute server's
``/data/terraluna/raw``), checks each file's SHA-256 against the committed ProductRecord, puts
each window on its D29 analysis grid with the reviewed resampler (geometry.resample_window), and
measures it with the reviewed T3 function (terrain.terrain_fingerprint). Nothing here computes a
distance, a mask threshold or a window choice: the windows are the ones frozen at G0
(docs/ralph/DECISIONS.md G0-02/04/05-RESULT).

Window ids (G0 evidence, outputs/g0/windows-evidence.json):

- ``moon-sps-x{i}-y{j}``: the 5 km window whose south-west corner is the south-west corner of
  the Site01 surf.tif extent plus ``(i, j)`` strides of ``targets.stride_m``, in the product's
  south polar stereographic metres.
- ``earth-utm{zone}-e{ix}-n{iy}``: the 5 km UTM square with south-west corner
  ``(ix, iy) * earth_search.stride_m`` in the zone of the configured search region.

Isotropic summary: ``direction_mean`` (an engineering choice; at G0 both T3 rules agreed to
1e-15 on these windows, and the rule is stated in each comparison's claim limits).
"""

from __future__ import annotations

import functools
import itertools
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt
from affine import Affine
from rasterio.warp import transform as warp_transform  # type: ignore[import-untyped]
from rasterio.windows import Window  # type: ignore[import-untyped]

from terraluna_pipeline.config import RunConfig, TerrainConfig, load_run_config
from terraluna_pipeline.geometry import (
    GeographicLookup,
    PolarStereographicLookup,
    WindowGrid,
    aeqd_inverse,
    body_radius_m,
    resample_window,
    window_grid,
)
from terraluna_pipeline.io import read_geotiff_window, sha256_file
from terraluna_pipeline.schema import ProductRecord
from terraluna_pipeline.terrain import IsotropicRule, TerrainFingerprint, terrain_fingerprint

RAW_DIR_ENV: Final = "TERRALUNA_RAW_DIR"
ISOTROPIC_RULE: Final[IsotropicRule] = "direction_mean"

PGDA78_SURF: Final = "PGDA78/Site01/Site01_final_adj_5mpp_surf.tif"
PGDA78_LDEC: Final = "PGDA78/Site01/Site01_final_adj_5mpp_ldec.tif"
COP30_PREFIX: Final = "COP30/copernicus-dem-30m"

MOON_ID = re.compile(r"^moon-sps-x(?P<i>[0-9]+)-y(?P<j>[0-9]+)$")
EARTH_ID = re.compile(r"^earth-utm(?P<zone>[0-9]+[ns])-e(?P<ix>[0-9]+)-n(?P<iy>[0-9]+)$")

FloatArray = npt.NDArray[np.float64]


class CacheMissingError(FileNotFoundError):
    """A raw input is not in the local cache (reason ``cache_missing``)."""


class RealCardError(ValueError):
    """An input that does not match its record or the frozen decisions."""


def raw_dir() -> Path:
    """The raw-data cache named by TERRALUNA_RAW_DIR; a missing value is cache_missing."""
    value = os.environ.get(RAW_DIR_ENV)
    if not value:
        raise CacheMissingError(f"cache_missing: set {RAW_DIR_ENV} to the raw-data directory")
    path = Path(value)
    if not path.is_dir():
        raise CacheMissingError(f"cache_missing: {RAW_DIR_ENV}={path} is not a directory")
    return path


def _find(raw: Path, source: str, name: str) -> Path:
    candidates = sorted((raw / source).rglob(name)) if (raw / source).is_dir() else []
    if not candidates:
        raise CacheMissingError(f"cache_missing: {name} not found under {raw / source}")
    return candidates[0]


def _transform(raster: Any, path: Path) -> Affine:
    transform = raster.transform
    if transform is None:
        raise RealCardError(f"{path}: no geotransform")
    checked: Affine = transform
    return checked


def _checked(path: Path, expected: str | None) -> str:
    actual = sha256_file(path)
    if expected is not None and actual != expected:
        raise RealCardError(f"{path}: sha256 {actual} differs from the ProductRecord {expected}")
    return actual


@dataclass(frozen=True, eq=False)
class RealWindow:
    """One window on its analysis grid, with what the record writers need."""

    window_id: str
    body: str
    frame: str
    crs: str
    epsg: int | None
    utm_zone: str | None
    ring_m: list[list[float]]
    ring_lonlat: list[list[float]]
    centre_lonlat_deg: tuple[float, float]
    grid: WindowGrid
    elevation_m: FloatArray
    """Resampled elevations, NaN at invalid cells (row 0 at the top, north)."""
    valid: npt.NDArray[np.bool_]
    fingerprint: TerrainFingerprint
    product_ids: list[str]
    input_files: dict[str, str]
    """sha256 of every raw file read, by file name."""
    observed_fraction: float | None
    search_region_id: str | None
    scene_ids: list[str]
    acquisition_dates: list[str]
    grid_index: tuple[int, int]


def _fingerprint(
    values: FloatArray, valid: npt.NDArray[np.bool_], grid: WindowGrid, cfg: TerrainConfig
) -> TerrainFingerprint:
    return terrain_fingerprint(
        np.ma.masked_invalid(values),
        valid=valid,
        grid=grid,
        config=cfg,
        isotropic_rule=ISOTROPIC_RULE,
        coarsening_factor=1.0,
    )


def products_by_id(sources: list[ProductRecord]) -> dict[str, ProductRecord]:
    return {record.product_id: record for record in sources}


def moon_window(
    window_id: str, run: RunConfig, sources: dict[str, ProductRecord], raw: Path
) -> RealWindow:
    """The Site01 window ``moon-sps-x{i}-y{j}`` (G0-02) on its D29 grid."""
    match = MOON_ID.fullmatch(window_id)
    if match is None:
        raise RealCardError(f"{window_id} is not a moon-sps-x<i>-y<j> window id")
    i, j = int(match["i"]), int(match["j"])
    cfg = run.terrain
    surf_record = sources[PGDA78_SURF]
    ldec_record = sources[PGDA78_LDEC]
    surf_path = _find(raw, "pgda78", Path(PGDA78_SURF).name)
    ldec_path = _find(raw, "pgda78", Path(PGDA78_LDEC).name)
    files = {
        surf_path.name: _checked(surf_path, surf_record.checksum),
        ldec_path.name: _checked(ldec_path, ldec_record.checksum),
    }
    surf = read_geotiff_window(surf_path)
    count = read_geotiff_window(ldec_path)
    t = _transform(surf, surf_path)
    if not (t.b == 0 and t.d == 0 and t.a > 0 and t.e < 0):
        raise RealCardError(f"{surf_path}: unexpected geotransform {t}")
    radius = body_radius_m("moon")
    lookup = PolarStereographicLookup(
        pole="south",
        central_meridian_deg=0.0,
        latitude_of_true_scale_deg=-90.0,
        scale_factor=1.0,
        radius_m=radius,
        false_easting_m=0.0,
        false_northing_m=0.0,
        origin_x_m=t.c,
        origin_y_m=t.f,
        pixel_width_m=t.a,
        pixel_height_m=t.e,
    )
    size = float(run.value("targets.window_m"))  # type: ignore[arg-type]
    stride = float(run.value("targets.stride_m"))  # type: ignore[arg-type]
    x0 = t.c
    y0 = t.f + t.e * surf.array.shape[0]
    wx0, wy0 = x0 + i * stride, y0 + j * stride
    ring_m = [
        [wx0, wy0],
        [wx0 + size, wy0],
        [wx0 + size, wy0 + size],
        [wx0, wy0 + size],
        [wx0, wy0],
    ]
    lat0, lon0 = (float(v) for v in lookup.unproject(wx0 + size / 2, wy0 + size / 2))
    grid = window_grid(cfg.window_m, cfg.analysis_grid_m)
    resampled = resample_window(
        surf.array.astype(np.float64),
        lookup,
        grid=grid,
        body="moon",
        lat0_rad=lat0,
        lon0_rad=lon0,
        nodata=surf.nodata,
    )
    valid = np.asarray(resampled.valid, dtype=bool)
    values = np.where(valid, np.asarray(resampled.values, dtype=np.float64), np.nan)
    # Observed fraction: LOLA return count > 0 at the nearest 5 m pixel of each valid cell centre.
    gx, gy = grid.mesh_m()
    lat, lon = aeqd_inverse(gx, gy, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    col, row = lookup.to_pixel(lat, lon)
    r, c = np.rint(row).astype(int), np.rint(col).astype(int)
    inside = (r >= 0) & (r < count.array.shape[0]) & (c >= 0) & (c < count.array.shape[1])
    observed = np.zeros(r.shape, dtype=bool)
    observed[inside] = count.array[r[inside], c[inside]] > 0
    observed_fraction = float(observed[valid].mean()) if valid.any() else None
    ring_lonlat = []
    for px, py in ring_m:
        la, lo = lookup.unproject(px, py)
        ring_lonlat.append([math.degrees(float(lo)), math.degrees(float(la))])
    return RealWindow(
        window_id=window_id,
        body="moon",
        frame="polar_stereographic",
        crs=surf_record.crs_and_frame,
        epsg=None,
        utm_zone=None,
        ring_m=ring_m,
        ring_lonlat=ring_lonlat,
        centre_lonlat_deg=(math.degrees(lon0), math.degrees(lat0)),
        grid=grid,
        elevation_m=values,
        valid=valid,
        fingerprint=_fingerprint(values, valid, grid, cfg),
        product_ids=[PGDA78_LDEC, PGDA78_SURF],
        input_files=files,
        observed_fraction=observed_fraction,
        search_region_id=None,
        scene_ids=[],
        acquisition_dates=[],
        grid_index=(i, j),
    )


def earth_region(run: RunConfig, zone: str) -> dict[str, Any]:
    """The configured search region whose UTM zone is ``zone`` (G0-04)."""
    regions = run.value("earth_search.regions")
    if not isinstance(regions, list) or not regions:
        raise RealCardError("blocked: G0-04 earth_search.regions is empty")
    matches = [
        region
        for region in regions
        if isinstance(region, dict) and str(region.get("utm_zone", "")).lower() == zone
    ]
    if len(matches) != 1:
        raise RealCardError(f"no single configured search region in UTM zone {zone}")
    region: dict[str, Any] = matches[0]
    return region


def cop30_tile_name(lat_deg: float, lon_deg: float) -> str:
    """The GLO-30 tile name holding a point, e.g. Copernicus_DSM_COG_10_N64_00_W017_00_DEM."""
    lat_i, lon_i = math.floor(lat_deg), math.floor(lon_deg)
    ns = "N" if lat_i >= 0 else "S"
    ew = "E" if lon_i >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat_i):02d}_00_{ew}{abs(lon_i):03d}_00_DEM"


def cop30_product(base: ProductRecord, tile: str, path: Path, checksum: str) -> ProductRecord:
    """A VERIFIED ProductRecord for a whole GLO-30 tile read from the local cache.

    The descriptive fields are the t1 record's (same collection); the tile, path, checksum and
    grid are this file's. outputs/sources.json holds a PARTIAL record of a byte-range cache for
    the same product id; the whole-file record supersedes it at staging (export stage).
    """
    raster = _read_tile(path)
    t = _transform(raster, path)
    height, width = raster.array.shape
    return ProductRecord.model_validate(
        {
            **base.model_dump(),
            "product_id": f"{COP30_PREFIX}/{tile}/{tile}.tif",
            "source_url": f"https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/{tile}/{tile}.tif",
            "local_path": f"data/raw/copernicus_glo30/{tile}/{tile}.tif",
            "checksum": checksum,
            "native_grid": f"width={width} height={height} transform={tuple(t)[:6]}",
            "verification_status": "VERIFIED",
        }
    )


def earth_window(
    window_id: str, run: RunConfig, sources: dict[str, ProductRecord], raw: Path
) -> tuple[RealWindow, ProductRecord]:
    """The Earth window ``earth-utm{zone}-e{ix}-n{iy}`` (G0-05) on its D29 grid."""
    match = EARTH_ID.fullmatch(window_id)
    if match is None:
        raise RealCardError(f"{window_id} is not an earth-utm<zone>-e<ix>-n<iy> window id")
    zone, ix, iy = match["zone"], int(match["ix"]), int(match["iy"])
    region = earth_region(run, zone)
    epsg = int(region["epsg"])
    cfg = run.terrain
    size = float(run.value("earth_search.window_m"))  # type: ignore[arg-type]
    stride = float(run.value("earth_search.stride_m"))  # type: ignore[arg-type]
    e0, n0 = ix * stride, iy * stride
    ring_m = [[e0, n0], [e0 + size, n0], [e0 + size, n0 + size], [e0, n0 + size], [e0, n0]]
    lons, lats = warp_transform(
        f"EPSG:{epsg}", "EPSG:4326", [p[0] for p in ring_m], [p[1] for p in ring_m]
    )
    ring_lonlat = [[float(lo), float(la)] for lo, la in zip(lons, lats, strict=True)]
    ring_lonlat[-1] = list(ring_lonlat[0])
    clon, clat = warp_transform(f"EPSG:{epsg}", "EPSG:4326", [e0 + size / 2], [n0 + size / 2])
    lon0_deg, lat0_deg = float(clon[0]), float(clat[0])
    tile = cop30_tile_name(lat0_deg, lon0_deg)
    path = _find(raw, "copernicus_glo30", f"{tile}.tif")
    checksum = _tile_sha256(path)
    base = next(
        (record for record in sources.values() if record.product_id.startswith(COP30_PREFIX)),
        None,
    )
    if base is None:
        raise RealCardError("outputs/sources.json holds no Copernicus GLO-30 record to extend")
    product = cop30_product(base, tile, path, checksum)
    dem = _read_tile(path)
    t = _transform(dem, path)
    if not (t.b == 0 and t.d == 0):
        raise RealCardError(f"{path}: rotated geotransform {t}")
    lookup = GeographicLookup(
        origin_lon_deg=t.c, origin_lat_deg=t.f, pixel_width_deg=t.a, pixel_height_deg=t.e
    )
    grid = window_grid(cfg.window_m, cfg.analysis_grid_m)
    resampled = resample_window(
        dem.array.astype(np.float64),
        lookup,
        grid=grid,
        body="earth",
        lat0_rad=math.radians(lat0_deg),
        lon0_rad=math.radians(lon0_deg),
        nodata=dem.nodata,
    )
    valid = np.asarray(resampled.valid, dtype=bool)
    values = np.where(valid, np.asarray(resampled.values, dtype=np.float64), np.nan)
    scene_ids = [str(item) for item in region.get("scene_ids") or []]
    dates = [str(item) for item in region.get("acquisition_dates") or []]
    window = RealWindow(
        window_id=window_id,
        body="earth",
        frame="utm",
        crs=f"EPSG:{epsg}",
        epsg=epsg,
        utm_zone=str(region["utm_zone"]),
        ring_m=ring_m,
        ring_lonlat=ring_lonlat,
        centre_lonlat_deg=(lon0_deg, lat0_deg),
        grid=grid,
        elevation_m=values,
        valid=valid,
        fingerprint=_fingerprint(values, valid, grid, cfg),
        product_ids=[product.product_id],
        input_files={path.name: checksum},
        observed_fraction=None,
        search_region_id=str(region["id"]),
        scene_ids=scene_ids,
        acquisition_dates=dates,
        grid_index=(ix, iy),
    )
    return window, product


@functools.lru_cache(maxsize=4)
def _read_tile(path: Path) -> Any:
    return read_geotiff_window(path)


@functools.lru_cache(maxsize=8)
def _tile_sha256(path: Path) -> str:
    return sha256_file(path)


def save_dem_cache(window: RealWindow, out_dir: Path) -> Path:
    """data/processed/dem_windows/<window_id>.npz for the card figures (figures.py)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{window.window_id}.npz"
    with target.open("wb") as handle:
        np.savez(
            handle,
            elevation_m=window.elevation_m,
            spacing_m=np.float64(window.grid.spacing_m),
            product_id=np.str_(window.product_ids[-1]),
        )
    return target


def load_run(config_path: Path) -> RunConfig:
    return load_run_config(config_path)


# --- Records ----------------------------------------------------------------------------------

PGDA78_OBSERVED_SOURCE: Final = (
    "PGDA78 Site01 LDEC map (number of LOLA returns per 5 m pixel) at the nearest 5 m pixel of "
    "each valid 90 m analysis-cell centre; a count above 0 is observed, otherwise interpolated"
)


def window_entry(
    window: RealWindow,
    products: list[ProductRecord],
    config_path: Path,
    card_id: str | None,
) -> Any:
    """The WindowEntry of a real window, through fingerprints.py (T4L-01)."""
    from terraluna_pipeline import fingerprints
    from terraluna_pipeline.schema import ObservedInterpolatedQuality, WindowPolygon

    polygon = WindowPolygon(
        frame=window.frame,  # type: ignore[arg-type]
        crs=window.crs,
        epsg=window.epsg,
        utm_zone=window.utm_zone,
        ring_m=window.ring_m,
        ring_lonlat=window.ring_lonlat,
    )
    digest = fingerprints.input_hash(
        arrays={"elevation_m": window.elevation_m, "valid": window.valid},
        rings={"ring_m": window.ring_m, "ring_lonlat": window.ring_lonlat},
        settings={
            "isotropic_rule": ISOTROPIC_RULE,
            "analysis_grid_m": window.grid.spacing_m,
            "window_m": window.grid.width_m,
            "resampler": "aeqd_sphere",
            "input_files": dict(sorted(window.input_files.items())),
        },
    )
    terrain = fingerprints.terrain_record(
        window.fingerprint,
        window_id=window.window_id,
        body=window.body,  # type: ignore[arg-type]
        products=products,
        config_path=config_path,
        input_hash=digest,
    )
    if window.body == "moon":
        observed = window.observed_fraction
        quality = ObservedInterpolatedQuality(
            observed_fraction=observed,
            interpolated_fraction=None if observed is None else 1.0 - observed,
            source=PGDA78_OBSERVED_SOURCE,
            reason=None if observed is not None else "no_valid_cells",
        )
    else:
        quality = ObservedInterpolatedQuality(
            observed_fraction=None,
            interpolated_fraction=None,
            source=None,
            reason="observed_interpolated_map_not_provided",
        )
    excluded = window.fingerprint.excluded_reason
    return fingerprints.build_window_record(
        window_id=window.window_id,
        body=window.body,  # type: ignore[arg-type]
        polygon=polygon,
        area_m2=window.grid.width_m**2,
        analysis_grid_m=window.grid.spacing_m,
        product_ids=[p.product_id for p in products],
        terrain=terrain,
        radar=[],
        status="eligible" if excluded is None else "rejected",
        reasons=[] if excluded is None else [str(excluded)],
        search_region_id=window.search_region_id,
        scene_ids=window.scene_ids,
        acquisition_dates=window.acquisition_dates,
        permanent_shadow_fraction=None,
        observed_interpolated_quality=quality,
        grid_index=list(window.grid_index),
        card_id=card_id,
    )


# --- The slice build: one target window, one Earth window -------------------------------------

TARGET_ID: Final = "moon-cr-site01-w000"
"""The Site01 (Connecting Ridge) target, named as in the PRD 8.4 manifest example."""

TARGET_NAME: Final = "Connecting Ridge, PGDA78 Site01"

SOURCES_FRAGMENT: Final = "outputs/realcard/sources.json"
"""The whole-file ProductRecords this slice reads beyond outputs/sources.json (export stage
merges every outputs/**/sources.json by product_id)."""

DEM_CACHE_DIR: Final = "data/processed/dem_windows"
WINDOWS_DIR: Final = "data/processed/windows"
COMPARISONS_DIR: Final = "outputs/comparisons"
TARGETS_PATH: Final = "outputs/search/targets.json"


class BlockedError(RuntimeError):
    """A missing human decision or input; the command exits 3 naming it (CONTRACTS f)."""


def _sources(root: Path) -> dict[str, ProductRecord]:
    import json

    path = root / "outputs" / "sources.json"
    if not path.is_file():
        raise BlockedError("blocked: outputs/sources.json (t1-pilot) is missing")
    return products_by_id(
        [
            ProductRecord.model_validate_json(json.dumps(item))
            for item in json.loads(path.read_text())
        ]
    )


def _claim_limits(moon: RealWindow, earth: RealWindow, mode: str) -> list[str]:
    observed = moon.observed_fraction
    limits = [
        "A single candidate pair: one Earth window was compared with the target window, so "
        "this is a pilot result, not a ranking and not independent evidence.",
        f"Shipping mode {mode}: radar comparison not completed; only terrain distances are "
        "reported (PRD 4).",
        "The Earth window was fixed at G0 (G0-05) as the eligible window containing the decoded "
        "UAVSAR ROI centre, without ranking by similarity.",
        "Both windows are resampled on a sphere (D29 aeqd_sphere); on Earth this approximates "
        "the WGS84 ellipsoid over a 5 km window.",
        "The nu values use the direction_mean isotropic summary of T3.",
        "Copernicus GLO-30 is a surface model (DSM) of one epoch; snow, moisture and surface "
        "change since acquisition were not checked on the Earth window.",
        "The WorldCover land-cover eligibility of the Earth window was checked at G0 "
        "(outputs/g0/windows-evidence.json) and is not recomputed in this record.",
        "PGDA78 error spatial correlation is not propagated: the uncertainty components "
        "(clones, scenarios) have not been run.",
    ]
    if observed is not None:
        limits.append(
            f"PGDA78 Site01 interpolation: {observed:.4f} of the target window's valid 90 m "
            "cells sit on a 5 m pixel with at least one LOLA return; the rest of the 5 m DEM "
            "there is interpolated."
        )
    return limits


def _target_record(
    run_id_value: str, products: list[ProductRecord], mode: str, moon: RealWindow
) -> Any:
    from terraluna_pipeline.schema import TargetRecord

    surf = products[-1]
    return TargetRecord.model_validate(
        {
            "target_id": TARGET_ID,
            "mock": False,
            "run_id": run_id_value,
            "body": "moon",
            "name": TARGET_NAME,
            "description": (
                "PGDA78 LOLA 5 m/pixel DEM of Site01 near the lunar south pole (Connecting "
                "Ridge); target window frozen at G0 (G0-02)."
            ),
            "product_ids": [p.product_id for p in products],
            "crs": surf.crs_and_frame,
            "projection": {
                "kind": "polar_stereographic",
                "params": {
                    "latitude_of_origin_deg": -90.0,
                    "central_meridian_deg": 0.0,
                    "sphere_radius_m": body_radius_m("moon"),
                },
            },
            "footprint": None,
            "primary_window_id": moon.window_id,
            "window_grid": {
                "window_m": moon.grid.width_m,
                "stride_m": 2500.0,
                "n_generated": None,
                "n_eligible": None,
            },
            "reference_window_ids": [],
            "shortlist": None,
            "shortlist_reason": "search_not_run",
            "shipping_mode": mode,
            "tile_layers": [],
        }
    )


def build(root: Path, target_window_id: str, earth_window_id: str) -> list[Path]:
    """Build the slice records for one pair and return the paths written.

    Writes data/processed/windows/<body>.json, data/processed/dem_windows/<id>.npz,
    outputs/comparisons/<target_id>.json, outputs/realcard/sources.json and, if absent,
    outputs/search/targets.json. Then ``export cards`` builds the card.
    """
    from terraluna_pipeline import export, matching, provenance

    config_path = root / "config" / "run.yaml"
    run = load_run_config(config_path)
    mode = run.value("run.shipping_mode")
    primary = run.value("targets.moon.primary_window_id")
    missing = [
        name
        for name, value in (("G0-01 run.shipping_mode", mode), ("G0-02 primary_window_id", primary))
        if value is None
    ]
    if missing:
        raise BlockedError("blocked: " + ", ".join(missing))
    if target_window_id != primary:
        raise RealCardError(
            f"target {target_window_id} is not the frozen primary window {primary} (G0-02)"
        )
    raw = raw_dir()
    sources = _sources(root)
    moon = moon_window(target_window_id, run, sources, raw)
    earth, cop30 = earth_window(earth_window_id, run, sources, raw)
    card_id = f"{moon.window_id}__{earth.window_id}"
    moon_products = [sources[PGDA78_LDEC], sources[PGDA78_SURF]]
    moon_entry = window_entry(moon, moon_products, config_path, card_id)
    earth_entry = window_entry(earth, [cop30], config_path, card_id)

    settings = matching.QualifySettings.from_config(config_path)
    target_summary = matching.WindowSummary.from_entry(moon_entry)
    earth_summary = matching.WindowSummary.from_entry(earth_entry)
    eligibility: dict[str, matching.QualifyResult] = {
        observable: matching.qualify_pair(
            None if observable == "radar_cpr" else sources[PGDA78_SURF],
            None if observable == "radar_cpr" else cop30,
            observable,
            mode,  # type: ignore[arg-type]
            target_summary,
            earth_summary,
            settings=settings,
            config_path=config_path,
        )
        for observable in matching.OBSERVABLES
    }
    all_products = [*moon_products, cop30]
    comparison = matching.compare_windows(
        moon_entry,
        earth_entry,
        eligibility,
        shipping_mode=mode,  # type: ignore[arg-type]
        source_ids=all_products,
        claim_limits=_claim_limits(moon, earth, str(mode)),
        config_path=config_path,
    )
    written: list[Path] = []
    for path, writer, value in (
        (root / WINDOWS_DIR / "moon.json", export.write_windows, [moon_entry]),
        (root / WINDOWS_DIR / "earth.json", export.write_windows, [earth_entry]),
        (root / COMPARISONS_DIR / f"{TARGET_ID}.json", export.write_comparisons, [comparison]),
        (root / SOURCES_FRAGMENT, export.write_sources, [cop30]),
    ):
        writer(path, value)
        written.append(path)
    targets = root / TARGETS_PATH
    if not targets.is_file():
        record = _target_record(
            provenance.run_id(config_path, moon_products), moon_products, str(mode), moon
        )
        export.write_targets(targets, [record])
        written.append(targets)
    for window in (moon, earth):
        written.append(save_dem_cache(window, root / DEM_CACHE_DIR))
    return written


# --- Region slice search: every eligible window of the configured Earth regions ---------------
#
# A small, honest stand-in for the full t5 search over the two G0-04 regions: every 5 km UTM
# square of the stride grid whose four corners lie inside the region polygon is generated, put
# on its D29 grid and measured by T3; the WorldCover rule is the config's (allowed classes and
# min_allowed_fraction, water = class 80), sampled at the nearest class of each 90 m cell centre
# (ANA-05 default). Every eligible window is compared with the target, and the shortlist is the
# first Pareto layer over the terrain families (ANA-03: no single order), computed by
# matching.pareto_shortlist with the encoder excluded while G5 is not PASS.

WORLDCOVER_PREFIX: Final = "WorldCover/esa-worldcover/v200/2021/map"
WORLDCOVER_CODES: Final = {
    "tree_cover": 10,
    "shrubland": 20,
    "grassland": 30,
    "cropland": 40,
    "built_up": 50,
    "bare_sparse": 60,
    "snow_ice": 70,
    "permanent_water": 80,
    "herbaceous_wetland": 90,
    "mangroves": 95,
    "moss_lichen": 100,
}
WORLDCOVER_WATER: Final = 80


def _inside(polygon: list[list[float]], lon: float, lat: float) -> bool:
    """Ray casting on the (lon, lat) polygon; the regions are small and far from the poles."""
    inside = False
    for (x1, y1), (x2, y2) in itertools.pairwise(polygon):
        if (y1 > lat) != (y2 > lat):
            x = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x:
                inside = not inside
    return inside


def region_window_ids(run: RunConfig, region: dict[str, Any]) -> list[str]:
    """Every window id of a region: UTM stride squares with all four corners inside."""
    epsg = int(region["epsg"])
    zone = str(region["utm_zone"]).lower()
    size = float(run.value("earth_search.window_m"))  # type: ignore[arg-type]
    stride = float(run.value("earth_search.stride_m"))  # type: ignore[arg-type]
    polygon = [[float(a), float(b)] for a, b in region["polygon_lonlat"]]
    xs, ys = warp_transform(
        "EPSG:4326", f"EPSG:{epsg}", [p[0] for p in polygon], [p[1] for p in polygon]
    )
    ids: list[str] = []
    for ix in range(math.floor(min(xs) / stride), math.ceil(max(xs) / stride) + 1):
        for iy in range(math.floor(min(ys) / stride), math.ceil(max(ys) / stride) + 1):
            e0, n0 = ix * stride, iy * stride
            corners = [(e0, n0), (e0 + size, n0), (e0 + size, n0 + size), (e0, n0 + size)]
            lons, lats = warp_transform(
                f"EPSG:{epsg}", "EPSG:4326", [c[0] for c in corners], [c[1] for c in corners]
            )
            if all(_inside(polygon, lo, la) for lo, la in zip(lons, lats, strict=True)):
                ids.append(f"earth-utm{zone}-e{ix}-n{iy}")
    return ids


def worldcover_tile_name(lat_deg: float, lon_deg: float) -> str:
    """The WorldCover v200 3-degree tile holding a point, e.g. ..._N63W018_Map."""
    lat_i = 3 * math.floor(lat_deg / 3)
    lon_i = 3 * math.floor(lon_deg / 3)
    ns = "N" if lat_i >= 0 else "S"
    ew = "E" if lon_i >= 0 else "W"
    return f"ESA_WorldCover_10m_2021_v200_{ns}{abs(lat_i):02d}{ew}{abs(lon_i):03d}_Map"


def worldcover_product(base: ProductRecord, tile: str, path: Path, checksum: str) -> ProductRecord:
    """A VERIFIED ProductRecord for a whole WorldCover tile read from the local cache."""
    head = read_geotiff_window(path, Window(0, 0, 1, 1))
    t = head.raster_transform
    return ProductRecord.model_validate(
        {
            **base.model_dump(),
            "product_id": f"{WORLDCOVER_PREFIX}/{tile}.tif",
            "source_url": f"https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/{tile}.tif",
            "local_path": f"data/raw/esa_worldcover/v200/2021/map/{tile}.tif",
            "checksum": checksum,
            "native_grid": (
                f"width={head.raster_width} height={head.raster_height} "
                f"transform={tuple(t)[:6] if t is not None else None}"
            ),
            "verification_status": "VERIFIED",
        }
    )


@dataclass(frozen=True)
class LandCover:
    allowed_fraction: float
    water_fraction: float
    snow_ice_fraction: float
    nodata_fraction: float
    unknown_fraction: float
    reasons: tuple[str, ...]


def land_cover(window: RealWindow, path: Path, allowed: list[str], min_allowed: float) -> LandCover:
    """WorldCover classes at the nearest 10 m pixel of each 90 m cell centre (ANA-05)."""
    head = read_geotiff_window(path, Window(0, 0, 1, 1))
    t = head.raster_transform
    if t is None:
        raise RealCardError(f"{path}: no geotransform")
    lon_deg, lat_deg = window.centre_lonlat_deg
    gx, gy = window.grid.mesh_m()
    lat, lon = aeqd_inverse(
        gx,
        gy,
        lat0_rad=math.radians(lat_deg),
        lon0_rad=math.radians(lon_deg),
        radius_m=body_radius_m("earth"),
    )
    lat_d, lon_d = np.degrees(lat), np.degrees(lon)
    c0 = max(math.floor((float(lon_d.min()) - t.c) / t.a) - 2, 0)
    c1 = min(math.ceil((float(lon_d.max()) - t.c) / t.a) + 2, head.raster_width)
    r0 = max(math.floor((float(lat_d.max()) - t.f) / t.e) - 2, 0)
    r1 = min(math.ceil((float(lat_d.min()) - t.f) / t.e) + 2, head.raster_height)
    sub = read_geotiff_window(path, Window(c0, r0, c1 - c0, r1 - r0))
    st = _transform(sub, path)
    lookup = GeographicLookup(
        origin_lon_deg=st.c, origin_lat_deg=st.f, pixel_width_deg=st.a, pixel_height_deg=st.e
    )
    col, row = lookup.to_pixel(lat, lon)
    r, c = np.rint(row).astype(int), np.rint(col).astype(int)
    ok = (r >= 0) & (r < sub.array.shape[0]) & (c >= 0) & (c < sub.array.shape[1])
    classes = np.full(r.shape, -1, dtype=int)
    classes[ok] = sub.array[r[ok], c[ok]]
    n = classes.size
    codes = {WORLDCOVER_CODES[name] for name in allowed}
    nodata = sub.nodata
    nodata_mask = (classes == -1) | ((classes == int(nodata)) if nodata is not None else False)
    unknown = ~np.isin(classes, list(WORLDCOVER_CODES.values())) & ~nodata_mask
    allowed_fraction = float(np.isin(classes, list(codes)).sum() / n)
    result_reasons: list[str] = []
    if float(nodata_mask.sum() / n) > 1 - min_allowed:
        result_reasons.append("land_cover_nodata")
    if float(unknown.sum() / n) > 1 - min_allowed:
        result_reasons.append("unknown_class")
    if allowed_fraction < min_allowed:
        result_reasons.append("land_cover_below_min_fraction")
    return LandCover(
        allowed_fraction=allowed_fraction,
        water_fraction=float((classes == WORLDCOVER_WATER).sum() / n),
        snow_ice_fraction=float((classes == WORLDCOVER_CODES["snow_ice"]).sum() / n),
        nodata_fraction=float(nodata_mask.sum() / n),
        unknown_fraction=float(unknown.sum() / n),
        reasons=tuple(result_reasons),
    )


SEARCH_SORT_RULE: Final = (
    "first Pareto layer over terrain_relief, terrain_texture (smaller is closer; encoder "
    "excluded while G5 is not PASS); then window_id"
)


def search(root: Path, also_card: Sequence[str] = ()) -> list[Path]:
    """Compare every eligible window of the configured Earth regions with the target window.

    Writes data/processed/windows/{moon,earth}.json, outputs/comparisons/<target_id>.json,
    outputs/realcard/sources.json and outputs/search/targets.json with the shortlist.
    """
    from terraluna_pipeline import export, matching, provenance
    from terraluna_pipeline.schema import TargetRecord

    config_path = root / "config" / "run.yaml"
    run = load_run_config(config_path)
    mode = run.value("run.shipping_mode")
    primary = run.value("targets.moon.primary_window_id")
    regions = run.value("earth_search.regions")
    missing = [
        name
        for name, value in (
            ("G0-01 run.shipping_mode", mode),
            ("G0-02 primary_window_id", primary),
            ("G0-04 earth_search.regions", regions or None),
        )
        if value is None
    ]
    if missing:
        raise BlockedError("blocked: " + ", ".join(missing))
    allowed = run.value("earth_search.masks.worldcover_allowed")
    min_allowed = float(run.value("earth_search.masks.min_allowed_fraction"))  # type: ignore[arg-type]
    raw = raw_dir()
    sources = _sources(root)
    moon = moon_window(str(primary), run, sources, raw)
    moon_products = [sources[PGDA78_LDEC], sources[PGDA78_SURF]]
    wc_base = next(
        (r for r in sources.values() if r.product_id.startswith(WORLDCOVER_PREFIX)), None
    )
    settings = matching.QualifySettings.from_config(config_path)
    extra_products: dict[str, ProductRecord] = {}
    earth_entries: list[Any] = []
    comparisons: list[Any] = []
    windows_by_id: dict[str, RealWindow] = {}
    assert isinstance(regions, list)
    moon_entry = window_entry(moon, moon_products, config_path, None)
    target_summary = matching.WindowSummary.from_entry(moon_entry)
    for region in regions:
        for window_id in region_window_ids(run, region):
            earth, cop30 = earth_window(window_id, run, sources, raw)
            extra_products[cop30.product_id] = cop30
            products = [cop30]
            reasons: list[str] = []
            cover_fractions: dict[str, float | None] = {
                "worldcover_allowed": None,
                "worldcover_snow_ice": None,
                "worldcover_water": None,
            }
            window_limits: list[str] = []
            lon, lat = earth.centre_lonlat_deg
            tile = worldcover_tile_name(lat, lon)
            try:
                wc_path = _find(raw, "esa_worldcover", f"{tile}.tif")
            except CacheMissingError:
                wc_path = None
            if wc_path is None or wc_base is None:
                reasons.append("worldcover_missing")
            else:
                wc = worldcover_product(wc_base, tile, wc_path, _tile_sha256(wc_path))
                extra_products[wc.product_id] = wc
                products.append(wc)
                cover = land_cover(earth, wc_path, list(allowed), min_allowed)  # type: ignore[call-overload]
                reasons.extend(cover.reasons)
                cover_fractions = {
                    "worldcover_allowed": cover.allowed_fraction,
                    "worldcover_snow_ice": cover.snow_ice_fraction,
                    "worldcover_water": cover.water_fraction,
                }
                window_limits.append(
                    "WorldCover 2021 at the Earth window's 90 m cell centres: allowed classes "
                    f"{cover.allowed_fraction:.4f}, of which snow_ice "
                    f"{cover.snow_ice_fraction:.4f} "
                    "(allowed by config earth_search.masks; PRD-04 reports snow and ice "
                    f"separately), permanent water {cover.water_fraction:.4f}."
                )
            if earth.fingerprint.excluded_reason is not None:
                reasons.insert(0, str(earth.fingerprint.excluded_reason))
            entry = window_entry(earth, products, config_path, None)
            status = (
                "eligible"
                if not reasons
                else ("unknown" if reasons == ["worldcover_missing"] else "rejected")
            )
            dumped = entry.model_dump()
            dumped["status"], dumped["reasons"] = status, reasons
            dumped["window"]["valid_fraction_by_observable"].update(cover_fractions)
            for key in cover_fractions:
                dumped["window"]["evidence_status_by_field"][key] = (
                    "Unavailable" if cover_fractions[key] is None else "Derived"
                )
            entry = type(entry).model_validate(dumped)
            earth_entries.append(entry)
            windows_by_id[window_id] = earth
            if status != "eligible":
                continue
            earth_summary = matching.WindowSummary.from_entry(entry)
            eligibility: dict[str, matching.QualifyResult] = {
                observable: matching.qualify_pair(
                    None if observable == "radar_cpr" else sources[PGDA78_SURF],
                    None if observable == "radar_cpr" else cop30,
                    observable,
                    mode,  # type: ignore[arg-type]
                    target_summary,
                    earth_summary,
                    settings=settings,
                    config_path=config_path,
                )
                for observable in matching.OBSERVABLES
            }
            comparisons.append(
                matching.compare_windows(
                    moon_entry,
                    entry,
                    eligibility,
                    shipping_mode=mode,  # type: ignore[arg-type]
                    source_ids=[*moon_products, *products],
                    claim_limits=[
                        *_search_claim_limits(moon, str(mode), region),
                        *window_limits,
                        *(
                            [
                                "This Earth window is the G0-05 first-card window (the eligible "
                                "window containing the decoded UAVSAR ROI centre), chosen at G0 "
                                "without ranking by similarity."
                            ]
                            if window_id in also_card
                            else []
                        ),
                    ],
                    config_path=config_path,
                )
            )
    shortlist_ids = _first_pareto_layer(comparisons)
    compared = {c.earth_window_id for c in comparisons}
    for wid in also_card:
        if wid not in compared:
            raise RealCardError(f"--also-card {wid} is not an eligible compared window")
    card_ids = {wid: f"{moon.window_id}__{wid}" for wid in sorted({*shortlist_ids, *also_card})}
    earth_entries = [
        type(e).model_validate({**e.model_dump(), "card_id": card_ids.get(e.window.window_id)})
        for e in earth_entries
    ]
    moon_entry = window_entry(moon, moon_products, config_path, None)
    comparisons = [
        type(c).model_validate({**c.model_dump(), "sort_rule": SEARCH_SORT_RULE})
        for c in sorted(
            comparisons, key=lambda c: (c.earth_window_id not in card_ids, c.earth_window_id)
        )
    ]
    target = _target_record(
        provenance.run_id(config_path, moon_products), moon_products, str(mode), moon
    )
    target = TargetRecord.model_validate(
        {
            **target.model_dump(),
            "shortlist": {
                "earth_window_ids": sorted(shortlist_ids),
                "rule": SEARCH_SORT_RULE,
                "n_compared": len(comparisons),
            },
            "shortlist_reason": None,
        }
    )
    written: list[Path] = []
    for path, writer, value in (
        (root / WINDOWS_DIR / "moon.json", export.write_windows, [moon_entry]),
        (root / WINDOWS_DIR / "earth.json", export.write_windows, earth_entries),
        (root / COMPARISONS_DIR / f"{TARGET_ID}.json", export.write_comparisons, comparisons),
        (
            root / SOURCES_FRAGMENT,
            export.write_sources,
            [extra_products[k] for k in sorted(extra_products)],
        ),
        (root / TARGETS_PATH, export.write_targets, [target]),
    ):
        writer(path, value)
        written.append(path)
    written.append(save_dem_cache(moon, root / DEM_CACHE_DIR))
    for window_id in card_ids:
        written.append(save_dem_cache(windows_by_id[window_id], root / DEM_CACHE_DIR))
    return written


def _first_pareto_layer(comparisons: list[Any]) -> list[str]:
    """Earth window ids of the comparisons no other comparison dominates on the terrain
    families (no worse on both, strictly better on one); matching.pareto_shortlist is used
    when it exists."""
    from terraluna_pipeline import matching

    pareto = getattr(matching, "pareto_shortlist", None)
    if pareto is not None:
        result = pareto(comparisons, "NOT_RUN")
        ids = getattr(result, "shortlist", None) or getattr(result, "earth_window_ids", None)
        if ids is not None:
            return sorted(str(i) for i in ids)
    families = ("terrain_relief", "terrain_texture")
    points = []
    for c in comparisons:
        values = [c.distances_by_family[f].value for f in families]
        if all(v is not None for v in values):
            points.append((c.earth_window_id, values))
    layer = [
        wid
        for wid, v in points
        if not any(
            all(o[i] <= v[i] for i in range(2)) and any(o[i] < v[i] for i in range(2))
            for _, o in points
        )
    ]
    return sorted(layer)


def _search_claim_limits(moon: RealWindow, mode: str, region: dict[str, Any]) -> list[str]:
    limits = _claim_limits(moon, moon, mode)[1:]
    limits[1] = (
        f"Earth windows: every 5 km UTM square of the {region['id']} search region (G0-04) "
        "whose corners lie inside the region polygon; eligibility from the T3 valid fraction "
        "and the config WorldCover rule at the nearest class of each 90 m cell centre."
    )
    limits[5] = (
        "This is a slice search over the configured regions, not the full t5 search; the "
        "shortlist is the first Pareto layer of the windows compared."
    )
    return limits


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="python -m terraluna_pipeline.realcard")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    commands = parser.add_subparsers(dest="command", required=True)
    build_cmd = commands.add_parser("build", help="build the slice records of one pair")
    build_cmd.add_argument("--target", required=True)
    build_cmd.add_argument("--earth", required=True)
    search_cmd = commands.add_parser(
        "search", help="compare every eligible window of the Earth regions"
    )
    search_cmd.add_argument(
        "--also-card",
        action="append",
        default=[],
        help="an Earth window that gets a card besides the shortlist (G0-05)",
    )
    args = parser.parse_args(argv)
    try:
        paths = (
            build(args.root, args.target, args.earth)
            if args.command == "build"
            else search(args.root, args.also_card)
        )
        for path in paths:
            print(f"wrote {path}")
    except (BlockedError, CacheMissingError) as error:
        print(str(error), file=sys.stderr)
        return 3
    except RealCardError as error:
        print(f"build: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
