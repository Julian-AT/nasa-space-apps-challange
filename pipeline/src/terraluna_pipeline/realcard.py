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

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt
from affine import Affine
from rasterio.warp import transform as warp_transform  # type: ignore[import-untyped]

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
    raster = read_geotiff_window(path, None)
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
    checksum = _checked(path, None)
    base = next(
        (record for record in sources.values() if record.product_id.startswith(COP30_PREFIX)),
        None,
    )
    if base is None:
        raise RealCardError("outputs/sources.json holds no Copernicus GLO-30 record to extend")
    product = cop30_product(base, tile, path, checksum)
    dem = read_geotiff_window(path)
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
