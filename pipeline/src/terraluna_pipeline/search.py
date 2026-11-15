"""Bounded candidate search for the Moon and Mars targets over declared Earth regions (PRD 6.4,
6.5, 12 T5).

Pure parts (tested offline): the window grids (Earth UTM windows inside declared region polygons;
Moon windows in the product's polar stereographic metres; Mars windows in a local AEQD frame over
one CTX DTM), window evaluation from the reviewed T3 :func:`terrain_fingerprint` and the masks of
:mod:`terraluna_pipeline.masks`, the PRD 6.5 step 3 terrain distance (RMS of log nu over the shared
supported lags, relief and texture separately), the Pareto shortlist, reference-set spreads and the
empirical background histogram with the CONTRACTS ``linear_within_bin_v1`` percentile.

The ``compute`` command reads only local raw products (``--raw-root``, default the environment
variable ``TERRALUNA_RAW``) and writes:

- ``outputs/search/summary_<target_id>.json``, ``targets.json``, ``comparisons/<target_id>.json``,
  ``reference/background_<target_id>.json`` (small, committed);
- ``data/processed/windows/<body>.json`` (WindowEntries) and
  ``data/processed/search/<target_id>/comparisons.raw.json`` (git-ignored).

Distances stay separate (D07): no family is merged, summed or weighted. The encoder is not used.
Missing evidence is null with a reason, never 0. Unknown and rejected windows get no distance.

Isotropic summary: ``direction_mean`` (each of the four directions weighted equally, PRD 6.3 says
only "summarized isotropically"); it is an engineering default recorded in DECISIONS (SEARCH-01).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal

import numpy as np
import numpy.typing as npt
import yaml
from pydantic import TypeAdapter

from terraluna_pipeline import provenance
from terraluna_pipeline.config import DEFAULT_RUN_CONFIG, TerrainConfig, load_run_config
from terraluna_pipeline.geometry import (
    EquirectangularLookup,
    GeographicLookup,
    PolarStereographicLookup,
    ResampledGrid,
    aeqd_forward,
    aeqd_inverse,
    body_radius_m,
    resample_window,
    window_grid,
)
from terraluna_pipeline.masks import (
    WORLDCOVER_VERSION,
    WORLDCOVER_YEAR,
    ctx_quality_status,
    parse_ctx_qa_metrics,
    sample_window_classes,
    window_land_cover_status,
)
from terraluna_pipeline.schema import (
    ComparisonRecord,
    ComparisonsFile,
    TargetsFile,
    WindowsFile,
)
from terraluna_pipeline.terrain import DIRECTIONS, IsotropicRule, terrain_fingerprint
from terraluna_pipeline.terrain import TerrainFingerprint as T3Fingerprint

WINDOWS_ADAPTER: Final = TypeAdapter(WindowsFile)
COMPARISONS_ADAPTER: Final = TypeAdapter(ComparisonsFile)
TARGETS_ADAPTER: Final = TypeAdapter(TargetsFile)

ISOTROPIC_RULE: Final[IsotropicRule] = "direction_mean"
SORT_RULE: Final = "pareto layers over terrain_relief, terrain_texture; then window_id"
DISTANCE_UNIT: Final = "RMS of log nu"
FAMILIES: Final = ("terrain_relief", "terrain_texture", "radar_cpr", "model_embedding")
TERRAIN_FAMILIES: Final = ("terrain_relief", "terrain_texture")
VARIANT_OF: Final = {"terrain_relief": "none", "terrain_texture": "plane"}
BACKGROUND_BINS: Final = 20
BACKGROUND_SEED: Final = 0
MAX_COMPARISONS: Final = 500
MARS2020_LANDING_LONLAT: Final = (77.4508, 18.4447)
"""Mars 2020 Perseverance landing site, planetocentric degrees east and north (NASA/JPL)."""

GLO30_NOTE: Final = (
    "Copernicus GLO-30 heights are a digital surface model including vegetation and buildings, "
    "referenced to EGM2008 (PRD 5)"
)
NO_CANDIDATE: Final = (
    "no qualifying candidate among evaluated windows under the stated observables, filters and "
    "uncertainty"
)


class SearchReason(StrEnum):
    """Reason codes of search.py (CONTRACTS a.5)."""

    regions_unset = "regions_unset"
    arcadia_bbox_unset = "arcadia_bbox_unset"
    target_window_unfrozen = "target_window_unfrozen"
    cache_missing = "cache_missing"
    earth_radar_fingerprint_not_built = "earth_radar_fingerprint_not_built"
    search_not_run = "search_not_run"
    permanent_shadow_product_unset = "permanent_shadow_product_unset"
    dem_valid_fraction_below_min = "dem_valid_fraction_below_min"
    coverage_below_min = "coverage_below_min"
    no_shared_lags = "no_shared_lags"
    nu_zero = "nu_zero"
    window_not_eligible = "window_not_eligible"
    reference_set_too_small = "reference_set_too_small"
    spread_unavailable = "spread_unavailable"
    radar_unavailable_for_body = "radar_unavailable_for_body"
    encoder_not_evaluated = "encoder_not_evaluated"
    uncertainty_not_annotated = "uncertainty_not_annotated"
    background_sample_size_unset = "background_sample_size_unset"
    count_map_not_provided = "count_map_not_provided"
    terrain_window_excluded = "terrain_window_excluded"


class SearchError(RuntimeError):
    """A machine-readable failure: ``code`` is a reason code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class BlockedResult:
    """A step that cannot run because a human input is unset."""

    reason: str
    detail: str


# --- Grids --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GridWindow:
    """One generated window before evaluation."""

    window_id: str
    body: Literal["moon", "mars", "earth"]
    frame: Literal["utm", "polar_stereographic", "equirectangular", "aeqd_sphere"]
    crs: str
    epsg: int | None
    utm_zone: str | None
    grid_index: tuple[int, int]
    ring_m: list[list[float]]
    ring_lonlat: list[list[float]]
    centre_lat_deg: float
    centre_lon_deg: float
    region_id: str | None = None
    scene_ids: tuple[str, ...] = ()
    acquisition_dates: tuple[str, ...] = ()
    radar_unavailable: bool = False


@dataclass(frozen=True)
class Region:
    """A declared Earth search region (config earth_search.regions, G0-04)."""

    id: str
    epsg: int
    utm_zone: str
    polygon_lonlat: list[list[float]]
    scene_ids: tuple[str, ...]
    acquisition_dates: tuple[str, ...]
    radar_unavailable: bool


def parse_regions(raw: object) -> list[Region] | BlockedResult:
    """Parse config earth_search.regions; [] or null is blocked with regions_unset."""
    if not raw:
        return BlockedResult(SearchReason.regions_unset, "config earth_search.regions is empty")
    if not isinstance(raw, list):
        raise SearchError("invalid_region_declaration", "earth_search.regions must be a list")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise SearchError("invalid_region_declaration", "a region must be a mapping")
        scenes = tuple(str(s) for s in item.get("scene_ids") or [])
        unavailable = item.get("radar") == "unavailable"
        if not scenes and not unavailable:
            raise SearchError(
                "invalid_region_declaration",
                f"region {item.get('id')!r} has no scene ids and is not radar: unavailable",
            )
        out.append(
            Region(
                id=str(item["id"]),
                epsg=int(item["epsg"]),
                utm_zone=str(item["utm_zone"]),
                polygon_lonlat=[[float(a), float(b)] for a, b in item["polygon_lonlat"]],
                scene_ids=scenes,
                acquisition_dates=tuple(str(d) for d in item.get("acquisition_dates") or []),
                radar_unavailable=unavailable,
            )
        )
    return out


def grid_count(extent_m: float, window_m: float, stride_m: float) -> int:
    """Windows along one axis: floor((extent - window) / stride) + 1, or 0 if none fits."""
    if extent_m < window_m:
        return 0
    return math.floor((extent_m - window_m) / stride_m) + 1


def _point_in_polygon(x: float, y: float, poly: Sequence[Sequence[float]]) -> bool:
    """Even-odd rule; points on an edge count as inside."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        # On the segment?
        cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
        if (
            abs(cross) <= 1e-9 * max(1.0, abs(x2 - x1) + abs(y2 - y1))
            and min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9
            and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9
        ):
            return True
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xin:
                inside = not inside
    return inside


def square_inside_polygon(
    x0: float, y0: float, size: float, poly: Sequence[Sequence[float]]
) -> bool:
    """Whether the square [x0, x0+size] x [y0, y0+size] lies inside ``poly`` (simple polygon):
    every corner inside and no polygon vertex strictly inside the square (a concave notch)."""
    corners = [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)]
    if not all(_point_in_polygon(cx, cy, poly) for cx, cy in corners):
        return False
    return not any(x0 < px < x0 + size and y0 < py < y0 + size for px, py in poly)


def earth_grid(
    region: Region, *, window_m: float, stride_m: float, body: str = "earth"
) -> list[GridWindow]:
    """Earth windows of ``window_m`` at ``stride_m`` in the region's UTM zone, kept only if fully
    inside the region polygon. Grid origin is the zone origin, so ids are stable:
    ``earth-utm<zone>-e<ix>-n<iy>`` with (ix, iy) the SW corner in stride units."""
    if body != "earth":
        raise SearchError("body_not_earth", "earth_grid produces Earth windows only")
    from pyproj import Transformer

    fwd = Transformer.from_crs("EPSG:4326", f"EPSG:{region.epsg}", always_xy=True)
    inv = Transformer.from_crs(f"EPSG:{region.epsg}", "EPSG:4326", always_xy=True)
    ring = region.polygon_lonlat
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    xs, ys = fwd.transform([p[0] for p in ring], [p[1] for p in ring])
    poly = list(zip(xs, ys, strict=True))
    ix0, ix1 = math.floor(min(xs) / stride_m), math.ceil(max(xs) / stride_m)
    iy0, iy1 = math.floor(min(ys) / stride_m), math.ceil(max(ys) / stride_m)
    zone = region.utm_zone.lower()
    out = []
    for ix in range(ix0, ix1 + 1):
        for iy in range(iy0, iy1 + 1):
            e0, n0 = ix * stride_m, iy * stride_m
            if not square_inside_polygon(e0, n0, window_m, poly):
                continue
            ring_m = [
                [e0, n0],
                [e0 + window_m, n0],
                [e0 + window_m, n0 + window_m],
                [e0, n0 + window_m],
                [e0, n0],
            ]
            lon, lat = inv.transform([p[0] for p in ring_m], [p[1] for p in ring_m])
            ring_ll = [[float(a), float(b)] for a, b in zip(lon, lat, strict=True)]
            ring_ll[-1] = list(ring_ll[0])
            clon, clat = inv.transform(e0 + window_m / 2, n0 + window_m / 2)
            out.append(
                GridWindow(
                    window_id=f"earth-utm{zone}-e{ix}-n{iy}",
                    body="earth",
                    frame="utm",
                    crs=f"EPSG:{region.epsg}",
                    epsg=region.epsg,
                    utm_zone=region.utm_zone,
                    grid_index=(ix, iy),
                    ring_m=ring_m,
                    ring_lonlat=ring_ll,
                    centre_lat_deg=float(clat),
                    centre_lon_deg=float(clon),
                    region_id=region.id,
                    scene_ids=region.scene_ids,
                    acquisition_dates=region.acquisition_dates,
                    radar_unavailable=region.radar_unavailable,
                )
            )
    return out


def projected_grid(
    *,
    prefix: str,
    body: Literal["moon", "mars"],
    frame: Literal["polar_stereographic", "aeqd_sphere"],
    crs: str,
    extent_m: tuple[float, float, float, float],
    window_m: float,
    stride_m: float,
    unproject: Any,
) -> list[GridWindow]:
    """Windows tiling ``extent_m`` (min_x, min_y, max_x, max_y) from its SW corner; ids
    ``<prefix>-x<i>-y<j>``. ``unproject(x, y)`` returns (lat_rad, lon_rad)."""
    x0, y0, x1, y1 = extent_m
    nx = grid_count(x1 - x0, window_m, stride_m)
    ny = grid_count(y1 - y0, window_m, stride_m)
    out = []
    for i in range(nx):
        for j in range(ny):
            wx, wy = x0 + i * stride_m, y0 + j * stride_m
            ring = [
                [wx, wy],
                [wx + window_m, wy],
                [wx + window_m, wy + window_m],
                [wx, wy + window_m],
                [wx, wy],
            ]
            ll = []
            for px, py in ring:
                la, lo = unproject(px, py)
                ll.append([math.degrees(float(lo)), math.degrees(float(la))])
            la, lo = unproject(wx + window_m / 2, wy + window_m / 2)
            out.append(
                GridWindow(
                    window_id=f"{prefix}-x{i}-y{j}",
                    body=body,
                    frame=frame,
                    crs=crs,
                    epsg=None,
                    utm_zone=None,
                    grid_index=(i, j),
                    ring_m=ring,
                    ring_lonlat=ll,
                    centre_lat_deg=math.degrees(float(la)),
                    centre_lon_deg=math.degrees(float(lo)),
                )
            )
    return out


# --- Window evaluation --------------------------------------------------------------------------


DEM_SINK: dict[str, tuple[npt.NDArray[np.float64], float, str]] = {}
"""window_id -> (resampled elevation in metres, NaN where invalid, analysis grid spacing in
metres, DEM product id): the figure inputs ``compute`` writes to data/processed/dem_windows."""


@dataclass
class Evaluated:
    """A window after evaluation: status, ordered reasons and its fingerprint."""

    grid: GridWindow
    status: Literal["eligible", "rejected", "unknown"]
    reasons: list[str]
    fingerprint: T3Fingerprint | None
    dem_valid_fraction: float
    input_hash: str
    product_ids: list[str]
    land_cover: dict[str, Any] | None = None
    ctx: dict[str, Any] | None = None
    observed_fraction: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def combine_status(
    rejected: Sequence[str], unknown: Sequence[str]
) -> tuple[Literal["eligible", "rejected", "unknown"], list[str]]:
    """A definite rejection wins over an unknown; reasons keep their order, rejections first."""
    if rejected:
        return "rejected", [*rejected, *unknown]
    if unknown:
        return "unknown", list(unknown)
    return "eligible", []


def fingerprint_grid(
    resampled: ResampledGrid, terrain: TerrainConfig, window_m: float
) -> T3Fingerprint:
    """The reviewed T3 fingerprint of a resampled D29 window."""
    grid = window_grid(window_m, terrain.analysis_grid_m)
    return terrain_fingerprint(
        np.ma.masked_invalid(resampled.values),
        valid=resampled.valid,
        grid=grid,
        config=terrain,
        isotropic_rule=ISOTROPIC_RULE,
        coarsening_factor=1.0,
    )


def input_hash(resampled: ResampledGrid, settings: Mapping[str, object]) -> str:
    digest = hashlib.sha256()
    digest.update(np.ascontiguousarray(np.nan_to_num(resampled.values, nan=-1e300)).tobytes())
    digest.update(np.ascontiguousarray(resampled.valid).tobytes())
    digest.update(provenance.canonical_json(dict(settings)))
    return digest.hexdigest()


# --- Distances, Pareto, spreads, background -----------------------------------------------------


@dataclass(frozen=True)
class Distance:
    value: float | None
    reason: str | None
    n_lags: int


def nu_by_lag(fp: T3Fingerprint, detrend: str) -> dict[float, float | None]:
    variant = fp.variants[detrend]  # type: ignore[index]
    return dict(zip(fp.lags_m, variant.nu_m, strict=True))


def terrain_distance(a: Mapping[float, float | None], b: Mapping[float, float | None]) -> Distance:
    """PRD 6.5 step 3: RMS of (ln nu_a - ln nu_b) over the lags supported in both windows."""
    shared = [lag for lag in sorted(a) if a.get(lag) is not None and b.get(lag) is not None]
    if not shared:
        return Distance(None, SearchReason.no_shared_lags, 0)
    if any(a[lag] == 0.0 or b[lag] == 0.0 for lag in shared):
        return Distance(None, SearchReason.nu_zero, len(shared))
    sq = [(math.log(a[lag] or 0.0) - math.log(b[lag] or 0.0)) ** 2 for lag in shared]
    return Distance(math.sqrt(math.fsum(sq) / len(sq)), None, len(shared))


def pareto_layers(points: Mapping[str, Sequence[float | None]]) -> dict[str, int]:
    """Pareto layer (1 = non-dominated) of each id over the given distances (smaller is closer).
    Ids with any missing distance get no layer."""
    remaining: dict[str, list[float]] = {
        k: [float(x) for x in v if x is not None]
        for k, v in points.items()
        if all(x is not None for x in v)
    }
    layers: dict[str, int] = {}
    layer = 1
    while remaining:
        front = [
            k
            for k, p in remaining.items()
            if not any(
                all(q[i] <= p[i] for i in range(len(p))) and any(q[i] < p[i] for i in range(len(p)))
                for kk, q in remaining.items()
                if kk != k
            )
        ]
        for k in front:
            layers[k] = layer
            del remaining[k]
        layer += 1
    return layers


def reference_spread(values: Sequence[float]) -> dict[str, Any] | None:
    """min, median, max and n of reference-set distances; None if fewer than two."""
    if len(values) < 2:
        return None
    s = sorted(values)
    return {"min": s[0], "median": float(np.median(s)), "max": s[-1], "n": len(s)}


def background_histogram(values: Sequence[float], bins: int = BACKGROUND_BINS) -> dict[str, Any]:
    """Equal-width bins from 0 to the largest value (a single-value sample gets one bin)."""
    hi = max(values) if values else 0.0
    if hi <= 0.0:
        hi = 1.0
    edges = [hi * k / bins for k in range(bins + 1)]
    counts = [0] * bins
    for v in values:
        k = min(int(v / hi * bins), bins - 1)
        counts[k] += 1
    return {"bin_edges": edges, "counts": counts, "n": len(values)}


def background_percentile(d: float, edges: Sequence[float], counts: Sequence[int]) -> float:
    """CONTRACTS ``linear_within_bin_v1``: 0 below the first edge, 100 above the last."""
    n = sum(counts)
    if n == 0:
        raise SearchError("background_empty", "no background sample")
    if d <= edges[0]:
        return 0.0
    if d >= edges[-1]:
        return 100.0
    total = 0.0
    for k, c in enumerate(counts):
        lo, hi = edges[k], edges[k + 1]
        if d >= hi:
            total += c
        else:
            total += c * (d - lo) / (hi - lo)
            break
    return 100.0 * total / n


# --- Records -----------------------------------------------------------------------------------


def _variant_record(fp: T3Fingerprint, detrend: str) -> dict[str, Any]:
    v = fp.variants[detrend]  # type: ignore[index]
    lags = fp.lags_m
    tilt = None
    if v.plane is not None:
        tilt = [v.plane.intercept_m, v.plane.slope_east, v.plane.slope_north]
    hurst = v.hurst.H
    hurst_reason: str | None = v.hurst.reason if hurst is None else None
    if hurst is None and hurst_reason is None:
        hurst_reason = v.reason or "unavailable"
    return {
        "detrend": detrend,
        "supported_lags_m": list(fp.supported_lags_m) if v.reason is None else [],
        "unsupported_lags": [
            {"lag_m": item.lag_m, "reason": str(item.reason or v.reason)}
            for item in fp.lags
            if not item.supported or v.reason is not None
        ],
        "nu_m": {repr(lag): nu for lag, nu in zip(lags, v.nu_m, strict=True)},
        "nu_by_direction_m": {
            str(d): {repr(lag): nu for lag, nu in zip(lags, v.directional_nu_m[d], strict=True)}
            for d in DIRECTIONS
        },
        "hurst": hurst,
        "hurst_reason": None if hurst is not None else str(hurst_reason),
        "tilt": tilt,
    }


def window_entry(ev: Evaluated, *, run_id: str, analysis_grid_m: float) -> dict[str, Any]:
    g = ev.grid
    earth = g.body == "earth"
    fp = ev.fingerprint
    status_fields: dict[str, str] = {"dem": "Observed"}
    status_fields["terrain_fingerprint"] = (
        "Derived" if fp is not None and not fp.excluded else "Unavailable"
    )
    status_fields["radar"] = "Unavailable"
    if earth:
        status_fields["land_cover"] = "Observed" if ev.land_cover else "Unavailable"
    if g.body == "moon":
        status_fields["permanent_shadow_fraction"] = "Unavailable"
    if g.body == "mars":
        status_fields["ctx_qa"] = (
            "Observed" if ev.ctx and ev.ctx.get("ctx_mola_sd_m") else ("Unavailable")
        )
    valid_by_obs: dict[str, float | None] = {"dem_90m": ev.dem_valid_fraction}
    if earth:
        valid_by_obs["worldcover_allowed"] = (ev.land_cover or {}).get("allowed_fraction")
    if g.body == "moon":
        oiq = {
            "observed_fraction": ev.observed_fraction,
            "interpolated_fraction": None
            if ev.observed_fraction is None
            else 1.0 - ev.observed_fraction,
            "source": "PGDA78 Site01 ldec LOLA return count > 0, nearest 5 m pixel at 90 m centres",
            "reason": None,
        }
    else:
        oiq = {
            "observed_fraction": None,
            "interpolated_fraction": None,
            "source": None,
            "reason": SearchReason.count_map_not_provided,
        }
    terrain_rec = None
    if fp is not None:
        terrain_rec = {
            "window_id": g.window_id,
            "body": g.body,
            "kind": "terrain",
            "product_ids": ev.product_ids,
            "evidence_label": "Unavailable" if fp.excluded else "Derived",
            "input_hash": ev.input_hash,
            "run_id": run_id,
            "terrain": {
                "analysis_grid_m": fp.analysis_grid_m,
                "coarsening_factor": fp.coarsening_factor,
                "resampler": "aeqd_sphere",
                "valid_fraction": fp.valid_fraction,
                "relief": _variant_record(fp, "none"),
                "texture": _variant_record(fp, "plane"),
            },
            "radar": None,
        }
    return {
        "window": {
            "window_id": g.window_id,
            "body": g.body,
            "polygon": {
                "frame": g.frame,
                "crs": g.crs,
                "epsg": g.epsg,
                "utm_zone": g.utm_zone,
                "ring_m": g.ring_m,
                "ring_lonlat": g.ring_lonlat,
            },
            "area_m2": 25_000_000.0,
            "analysis_grid_m": analysis_grid_m,
            "product_ids": ev.product_ids,
            "valid_fraction_by_observable": valid_by_obs,
            "observed_interpolated_quality": oiq,
            "environment_flags": {
                "search_region_id": g.region_id,
                "scene_ids": list(g.scene_ids),
                "acquisition_dates": list(g.acquisition_dates),
                "snow_or_freeze": "unknown" if earth else None,
                "moisture_state": "unknown" if earth else None,
                "surface_change_since_acquisition": "unknown" if earth else None,
                "permanent_shadow_fraction": None,
                "surface_model_note": GLO30_NOTE if earth else None,
            },
            "evidence_status_by_field": status_fields,
        },
        "terrain": terrain_rec,
        "radar": [],
        "status": ev.status,
        "reasons": list(ev.reasons),
        "grid_index": list(g.grid_index),
        "card_id": None,
    }


def _unavailable(reason: str, unit: str) -> dict[str, Any]:
    return {
        "value": None,
        "unit": unit,
        "evidence_label": "Unavailable",
        "status": "unavailable",
        "reason": reason,
        "reference_spread": None,
        "reference_spread_reason": reason,
        "background_percentile": None,
        "background_percentile_reason": reason,
        "tied": None,
        "tie_group": None,
        "tie_reason": None,
    }


def _uncertainty() -> dict[str, Any]:
    comp = {
        "status": "unavailable",
        "evidence_label": "Unavailable",
        "reason": SearchReason.uncertainty_not_annotated,
        "note": "uncertainty is annotated by T6 (uncertainty annotate); not computed by search",
        "by_family": None,
        "n_requested": None,
        "n_used": None,
        "scenarios": None,
        "rank_frequency": None,
    }
    return {
        k: dict(comp)
        for k in (
            "heterogeneity",
            "measurement_error",
            "statistical_prediction_error",
            "scenario_sensitivity",
        )
    }


# --- Target search -----------------------------------------------------------------------------


@dataclass
class TargetResult:
    target_id: str
    body: Literal["moon", "mars"]
    name: str
    description: str
    product_ids: list[str]
    crs: str
    projection: dict[str, Any]
    windows: list[Evaluated]
    primary_window_id: str | None
    primary_rule: str
    proposals: list[str]
    notes: list[str] = field(default_factory=list)
    tile_source: dict[str, Any] = field(default_factory=dict)


def search_target(
    target: TargetResult,
    earth: Sequence[Evaluated],
    *,
    run_id_for: Any,
    shipping_mode: str | None,
    background_n: int | None,
    seed: int = BACKGROUND_SEED,
) -> dict[str, Any]:
    """Compare the target's primary window with every eligible Earth window (PRD 6.5)."""
    if background_n is None:
        raise SearchError(SearchReason.background_sample_size_unset, "no background sample size")
    by_id = {w.grid.window_id: w for w in target.windows}
    if target.primary_window_id is None or target.primary_window_id not in by_id:
        raise SearchError(SearchReason.target_window_unfrozen, f"{target.target_id}")
    primary = by_id[target.primary_window_id]
    if primary.status != "eligible" or primary.fingerprint is None:
        raise SearchError("primary_window_not_eligible", target.primary_window_id)
    reference = [
        w
        for w in target.windows
        if w.status == "eligible" and w.grid.window_id != primary.grid.window_id
    ]
    radar_reason = (
        SearchReason.radar_unavailable_for_body
        if target.body == "mars"
        else SearchReason.earth_radar_fingerprint_not_built
    )
    nu_p = {fam: nu_by_lag(primary.fingerprint, VARIANT_OF[fam]) for fam in TERRAIN_FAMILIES}

    def dist(w: Evaluated, fam: str) -> Distance:
        assert w.fingerprint is not None
        return terrain_distance(nu_p[fam], nu_by_lag(w.fingerprint, VARIANT_OF[fam]))

    spreads: dict[str, dict[str, Any] | None] = {}
    ref_values: dict[str, list[float]] = {}
    for fam in TERRAIN_FAMILIES:
        vals = [d.value for w in reference if (d := dist(w, fam)).value is not None]
        ref_values[fam] = [float(v) for v in vals]
        spreads[fam] = reference_spread(ref_values[fam])

    eligible = sorted(
        (w for w in earth if w.status == "eligible" and w.fingerprint is not None),
        key=lambda w: w.grid.window_id,
    )
    not_ranked = [
        {"window_id": w.grid.window_id, "status": w.status, "reasons": w.reasons}
        for w in sorted(earth, key=lambda w: w.grid.window_id)
        if w.status != "eligible"
    ]
    distances = {
        w.grid.window_id: {fam: dist(w, fam) for fam in TERRAIN_FAMILIES} for w in eligible
    }

    # Background (6.5 step 7): a seeded sample of eligible windows, never padded.
    rng = np.random.default_rng(seed)
    ids = [w.grid.window_id for w in eligible]
    n_actual = min(background_n, len(ids))
    sample = sorted(rng.choice(ids, size=n_actual, replace=False).tolist()) if n_actual else []
    families_bg: dict[str, Any] = {}
    family_reasons: dict[str, str | None] = {}
    for fam in TERRAIN_FAMILIES:
        vals_f = [float(v) for i in sample if (v := distances[i][fam].value) is not None]
        if vals_f:
            h = background_histogram(vals_f)
            families_bg[fam] = {
                "unit": DISTANCE_UNIT,
                "evidence_label": "Derived",
                **h,
                "percentile_definition": "linear_within_bin_v1",
                "direction": "smaller_is_closer",
            }
            family_reasons[fam] = None
        else:
            families_bg[fam] = None
            family_reasons[fam] = SearchReason.no_shared_lags
    families_bg["radar_cpr"] = None
    family_reasons["radar_cpr"] = radar_reason
    families_bg["model_embedding"] = None
    family_reasons["model_embedding"] = SearchReason.encoder_not_evaluated

    layers = pareto_layers(
        {i: [distances[i][f].value for f in TERRAIN_FAMILIES] for i in distances}
    )
    shortlist = sorted(i for i, layer in layers.items() if layer == 1)
    order = sorted(distances, key=lambda i: (layers.get(i, 10**9), i))
    kept = order[:MAX_COMPARISONS]
    records = []
    for eid in kept:
        w = next(e for e in eligible if e.grid.window_id == eid)
        dbf: dict[str, Any] = {}
        for fam in TERRAIN_FAMILIES:
            d = distances[eid][fam]
            if d.value is None:
                dbf[fam] = _unavailable(str(d.reason), DISTANCE_UNIT)
                continue
            sp = spreads[fam]
            bg = families_bg[fam]
            dbf[fam] = {
                "value": d.value,
                "unit": DISTANCE_UNIT,
                "evidence_label": "Derived",
                "status": "available",
                "reason": None,
                "reference_spread": sp,
                "reference_spread_reason": None if sp else SearchReason.reference_set_too_small,
                "background_percentile": None
                if bg is None
                else background_percentile(d.value, bg["bin_edges"], bg["counts"]),
                "background_percentile_reason": None
                if bg is not None
                else SearchReason.no_shared_lags,
                "tied": None,
                "tie_group": None,
                "tie_reason": SearchReason.spread_unavailable,
            }
        dbf["radar_cpr"] = _unavailable(radar_reason, "CPR Wasserstein-1")
        dbf["model_embedding"] = _unavailable(SearchReason.encoder_not_evaluated, "cosine distance")
        source_ids = sorted(set(target.product_ids) | set(w.product_ids))
        claim = [
            "candidate window identified by this search; not a proven analog",
            GLO30_NOTE,
            "terrain distances only; radar comparison not completed (shipping mode C)"
            if target.body == "moon"
            else "Mars target: terrain only, radar unavailable for this body",
            "the encoder is EXPERIMENTAL and not used in any ranking",
            f"WorldCover {WORLDCOVER_VERSION} {WORLDCOVER_YEAR} land-cover mask, nearest class "
            "at 90 m centres (ANA-05 default)",
        ]
        rec = {
            "target_window_id": primary.grid.window_id,
            "earth_window_id": eid,
            "shipping_mode": shipping_mode,
            "comparable_observables": [
                f for f in TERRAIN_FAMILIES if distances[eid][f].value is not None
            ],
            "excluded_observables_and_reasons": [
                {"observable": "radar_cpr", "reasons": [str(radar_reason)]},
                {"observable": "model_embedding", "reasons": [SearchReason.encoder_not_evaluated]},
            ],
            "distances_by_family": dbf,
            "uncertainty_components": _uncertainty(),
            "sort_rule": SORT_RULE,
            "claim_limits": claim,
            "source_ids": source_ids,
            "run_id": run_id_for(source_ids),
        }
        records.append(ComparisonRecord.model_validate(rec).model_dump(mode="json"))
    sample_sources = sorted(set(target.product_ids) | {p for w in eligible for p in w.product_ids})
    background = {
        "target_id": target.target_id,
        "mock": False,
        "run_id": run_id_for(sample_sources),
        "statement": "empirical background comparison, not a physical truth test",
        "seed": seed,
        "requested_n": background_n,
        "actual_n": n_actual,
        "sample_window_ids": sample,
        "source_ids": sample_sources,
        "families": families_bg,
        "family_reasons": family_reasons,
        "target_window_id": primary.grid.window_id,
    }
    top = [
        {
            "earth_window_id": i,
            "pareto_layer": layers.get(i),
            "terrain_relief": distances[i]["terrain_relief"].value,
            "terrain_texture": distances[i]["terrain_texture"].value,
            "search_region_id": next(e.grid.region_id for e in eligible if e.grid.window_id == i),
        }
        for i in order[:10]
    ]
    return {
        "records": records,
        "dropped": len(order) - len(kept),
        "background": background,
        "shortlist": shortlist,
        "n_compared": len(distances),
        "not_ranked": not_ranked,
        "reference_spreads": spreads,
        "reference_window_ids": sorted(w.grid.window_id for w in reference),
        "top": top,
    }


# --- Deterministic writers ----------------------------------------------------------------------


def dump(value: object) -> bytes:
    """Sorted keys, 2-space indent, LF, trailing newline."""
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def write_bytes(path: Path, data: bytes) -> str:
    if "release" in path.parts:
        raise SearchError("refuse_release_path", str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Real-data readers (compute) ---------------------------------------------------------------


def _read(path: Path, window: Any = None) -> Any:
    from terraluna_pipeline.io import read_geotiff_window

    return read_geotiff_window(path, window)


class Sources:
    """Product ids and checksums for run_id: the committed outputs/sources.json checksum where
    the product is recorded there, otherwise the SHA-256 of the file read."""

    def __init__(self, repo: Path) -> None:
        self.known: dict[str, str | None] = {}
        p = repo / "outputs" / "sources.json"
        if p.exists():
            for rec in json.loads(p.read_text()):
                self.known[rec["product_id"]] = rec.get("checksum")
        self.used: dict[str, str | None] = {}
        self.files: dict[str, dict[str, Any]] = {}
        self.bytes_read = 0

    def add(self, product_id: str, path: Path) -> str:
        if product_id not in self.used:
            from terraluna_pipeline.io import sha256_file

            digest = sha256_file(path)
            size = path.stat().st_size
            self.bytes_read += size
            self.files[product_id] = {"sha256": digest, "bytes": size, "file": path.name}
            self.used[product_id] = self.known.get(product_id, digest)
        return product_id


def _geo_lookup(t: Any) -> GeographicLookup:
    return GeographicLookup(
        origin_lon_deg=t.c, origin_lat_deg=t.f, pixel_width_deg=t.a, pixel_height_deg=t.e
    )


def evaluate_earth(
    raw: Path,
    regions: Sequence[Region],
    cfg: Mapping[str, Any],
    terrain: TerrainConfig,
    sources: Sources,
) -> list[Evaluated]:
    from rasterio.windows import Window  # type: ignore[import-untyped]

    es = cfg["earth_search"]
    masks = es["masks"]
    window_m, stride_m = float(es["window_m"]), float(es["stride_m"])
    out: list[Evaluated] = []
    for region in regions:
        windows = earth_grid(region, window_m=window_m, stride_m=stride_m)
        lons = [p[0] for p in region.polygon_lonlat]
        lats = [p[1] for p in region.polygon_lonlat]
        # GLO-30 1x1 degree tiles covering the region (plus a margin), mosaicked by window.
        dems = []
        for lat in range(math.floor(min(lats) - 0.05), math.floor(max(lats) + 0.05) + 1):
            for lon in range(math.floor(min(lons) - 0.05), math.floor(max(lons) + 0.05) + 1):
                ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
                ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
                name = f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"
                p = raw / "copernicus_glo30" / name / f"{name}.tif"
                if p.exists():
                    d = _read(p)
                    pid = sources.add(f"COP30/copernicus-dem-30m/{name}/{name}.tif", p)
                    dems.append(
                        (pid, d.array.astype(np.float64), _geo_lookup(d.transform), d.nodata)
                    )
        # WorldCover 3x3 degree tile.
        wl = 3 * math.floor(min(lats) / 3)
        wo = 3 * math.floor(min(lons) / 3)
        ns = f"N{wl:02d}" if wl >= 0 else f"S{-wl:02d}"
        ew = f"E{wo:03d}" if wo >= 0 else f"W{-wo:03d}"
        wname = f"ESA_WorldCover_10m_2021_v200_{ns}{ew}_Map.tif"
        wpath = raw / "esa_worldcover" / "v200" / "2021" / "map" / wname
        wc = None
        wc_pid = None
        if wpath.exists():
            head = _read(wpath, Window(0, 0, 1, 1))
            t = head.raster_transform
            c0 = max(math.floor((min(lons) - 0.1 - t.c) / t.a), 0)
            c1 = math.ceil((max(lons) + 0.1 - t.c) / t.a)
            r0 = max(math.floor((max(lats) + 0.1 - t.f) / t.e), 0)
            r1 = math.ceil((min(lats) - 0.1 - t.f) / t.e)
            sub = _read(wpath, Window(c0, r0, c1 - c0, r1 - r0))
            wc = (sub.array, _geo_lookup(sub.transform), sub.nodata)
            wc_pid = sources.add(f"WorldCover/esa-worldcover/v200/2021/map/{wname}", wpath)
        for g in windows:
            lat0, lon0 = math.radians(g.centre_lat_deg), math.radians(g.centre_lon_deg)
            grid = window_grid(window_m, terrain.analysis_grid_m)
            merged_v: npt.NDArray[np.float64] | None = None
            merged_ok: npt.NDArray[np.bool_] | None = None
            pids = []
            for pid, arr, lk, nod in dems:
                try:
                    rg = resample_window(
                        arr, lk, grid=grid, body="earth", lat0_rad=lat0, lon0_rad=lon0, nodata=nod
                    )
                except Exception:  # point outside a form's domain: no data from this tile
                    continue
                if not rg.inside.any():
                    continue
                pids.append(pid)
                if merged_v is None:
                    merged_v, merged_ok = rg.values.copy(), rg.valid.copy()
                else:
                    assert merged_ok is not None
                    take = rg.valid & ~merged_ok
                    merged_v[take] = rg.values[take]
                    merged_ok |= rg.valid
            if merged_v is None or merged_ok is None:
                merged_v = np.full((grid.size, grid.size), np.nan)
                merged_ok = np.zeros((grid.size, grid.size), dtype=bool)
            rg = ResampledGrid(values=merged_v, valid=merged_ok, inside=merged_ok.copy())
            if pids:
                DEM_SINK[g.window_id] = (rg.values, terrain.analysis_grid_m, pids[0])
            dem_valid = float(merged_ok.mean())
            fp = fingerprint_grid(rg, terrain, window_m)
            classes = None
            if wc is not None:
                classes = sample_window_classes(
                    wc[0],
                    wc[1],
                    window_m=window_m,
                    analysis_grid_m=terrain.analysis_grid_m,
                    lat0_rad=lat0,
                    lon0_rad=lon0,
                    nodata=None if wc[2] is None else int(wc[2]),
                )
            lc = window_land_cover_status(
                classes,
                list(masks["worldcover_allowed"]),
                float(masks["min_allowed_fraction"]),
                masks.get("water"),
            )
            rejected: list[str] = []
            unknown: list[str] = []
            if dem_valid < terrain.min_valid_fraction:
                unknown.append(SearchReason.dem_valid_fraction_below_min)
            if lc.status == "rejected":
                rejected.append(str(lc.reason))
            elif lc.status == "unknown":
                unknown.append(str(lc.reason))
            status, reasons = combine_status(rejected, unknown)
            products = sorted(pids + ([wc_pid] if wc_pid else []))
            out.append(
                Evaluated(
                    grid=g,
                    status=status,
                    reasons=reasons,
                    fingerprint=fp,
                    dem_valid_fraction=dem_valid,
                    input_hash=input_hash(rg, {"window": g.window_id, "rule": ISOTROPIC_RULE}),
                    product_ids=products,
                    land_cover={
                        "status": lc.status,
                        "reason": lc.reason,
                        "allowed_fraction": lc.allowed_fraction,
                        "snow_ice_pixels": lc.snow_ice,
                        "water_pixels": lc.water,
                        "n": lc.n,
                        "counts": dict(lc.counts),
                    },
                )
            )
    return out


def evaluate_moon(
    raw: Path, cfg: Mapping[str, Any], terrain: TerrainConfig, sources: Sources
) -> TargetResult:
    import rasterio  # type: ignore[import-untyped]

    d = raw / "pgda78" / "Site01"
    surf_p = d / "Site01_final_adj_5mpp_surf.tif"
    cnt_p = d / "Site01_final_adj_5mpp_ldec.tif"
    if not surf_p.exists():
        raise SearchError(SearchReason.cache_missing, str(surf_p))
    surf = _read(surf_p)
    cnt = _read(cnt_p)
    with rasterio.open(surf_p) as s:
        crs = s.crs.to_wkt()
    pids = [
        sources.add("PGDA78/Site01/Site01_final_adj_5mpp_surf.tif", surf_p),
        sources.add("PGDA78/Site01/Site01_final_adj_5mpp_ldec.tif", cnt_p),
    ]
    t = surf.transform
    radius = body_radius_m("moon")
    lk = PolarStereographicLookup(
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
    x0, y1 = t.c, t.f
    x1 = x0 + t.a * surf.array.shape[1]
    y0 = y1 + t.e * surf.array.shape[0]
    window_m, stride_m = float(cfg["targets"]["window_m"]), float(cfg["targets"]["stride_m"])
    grid_windows = projected_grid(
        prefix="moon-sps",
        body="moon",
        frame="polar_stereographic",
        crs=crs,
        extent_m=(x0, y0, x1, y1),
        window_m=window_m,
        stride_m=stride_m,
        unproject=lambda x, y: lk.unproject(x, y),
    )
    arr = surf.array.astype(np.float64)
    grid = window_grid(window_m, terrain.analysis_grid_m)
    gx, gy = grid.mesh_m()
    out = []
    for g in grid_windows:
        lat0, lon0 = math.radians(g.centre_lat_deg), math.radians(g.centre_lon_deg)
        rg = resample_window(
            arr, lk, grid=grid, body="moon", lat0_rad=lat0, lon0_rad=lon0, nodata=surf.nodata
        )
        la, lo = aeqd_inverse(gx, gy, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
        col, row = lk.to_pixel(la, lo)
        r = np.floor(row + 0.5).astype(int)
        c = np.floor(col + 0.5).astype(int)
        ok = (r >= 0) & (r < cnt.array.shape[0]) & (c >= 0) & (c < cnt.array.shape[1])
        observed = np.zeros(r.shape, bool)
        observed[ok] = cnt.array[r[ok], c[ok]] > 0
        DEM_SINK[g.window_id] = (rg.values, terrain.analysis_grid_m, pids[0])
        valid = rg.valid
        obs = float(observed[valid].mean()) if valid.any() else None
        dem_valid = float(valid.mean())
        fp = fingerprint_grid(rg, terrain, window_m)
        rejected = (
            [] if dem_valid >= terrain.min_valid_fraction else [SearchReason.coverage_below_min]
        )
        status, reasons = combine_status(rejected, [])
        out.append(
            Evaluated(
                grid=g,
                status=status,
                reasons=reasons,
                fingerprint=fp,
                dem_valid_fraction=dem_valid,
                input_hash=input_hash(rg, {"window": g.window_id, "rule": ISOTROPIC_RULE}),
                product_ids=sorted(pids),
                observed_fraction=obs,
            )
        )
    primary = cfg["targets"]["moon"].get("primary_window_id")
    return TargetResult(
        target_id="moon-cr-site01-w000",
        body="moon",
        name="Connecting Ridge (PGDA78 Site01)",
        description="Candidate Artemis region at Connecting Ridge (PGDA78 Site01, 5 m LOLA DEM)",
        product_ids=sorted(pids),
        crs=crs,
        projection={
            "kind": "polar_stereographic",
            "params": {"pole": "south", "central_meridian_deg": 0.0, "radius_m": radius},
        },
        windows=out,
        primary_window_id=primary,
        primary_rule="eligible window containing the Site01 ROI-union centroid (PRD 6.1; G0-02)",
        tile_source={
            "layer": "moon-site01-hillshade",
            "extent": (x0, y0, x1, y1),
            "native_mpp": float(t.a),
            "crs": crs,
            "units": "metres, Site01 south polar stereographic (product frame)",
            "sample": lambda x, y: _sample_projected(arr, lk, surf.nodata, x, y),
        },
        proposals=["moon-sps-x2-y2"],
        notes=[
            "permanent_shadow_fraction is null: no permanent-shadow product is named (G0-10, "
            "reason permanent_shadow_product_unset)",
            "observed fraction: LOLA return count > 0 at the nearest 5 m pixel of each 90 m centre",
        ],
    )


@dataclass(frozen=True)
class CtxProduct:
    name: str
    dtm: Path
    qa: Path | None
    bounds_lonlat: tuple[float, float, float, float]
    sd_m: float | None


def list_ctx(raw: Path) -> list[CtxProduct]:
    import rasterio

    out = []
    for d in sorted((raw / "ctx_dtm").iterdir()):
        if not d.is_dir():
            continue
        tifs = sorted(p for p in d.glob("*.tif") if p.name.endswith(("_dtm.tif", "-DEM.tif")))
        if not tifs:
            continue
        with rasterio.open(tifs[0]) as s:
            b = s.bounds
            c = s.crs.to_dict()
        radius = float(c.get("R", 3396190.0))
        k = radius * math.cos(math.radians(float(c.get("lat_ts", 0.0))))
        lon0 = float(c.get("lon_0", 0.0))
        w = math.degrees(b.left / k) + lon0
        e = math.degrees(b.right / k) + lon0
        s_ = math.degrees(b.bottom / radius) + float(c.get("lat_0", 0.0))
        n = math.degrees(b.top / radius) + float(c.get("lat_0", 0.0))
        qa = d / "qa_metrics.txt"
        sd = parse_ctx_qa_metrics(qa.read_text()).get("ctx_mola_sd_m") if qa.exists() else None
        out.append(CtxProduct(d.name, tifs[0], qa if qa.exists() else None, (w, s_, e, n), sd))
    return out


def _bbox_distance_deg(b: tuple[float, float, float, float], lon: float, lat: float) -> float:
    dx = max(b[0] - lon, 0.0, lon - b[2]) * math.cos(math.radians(lat))
    dy = max(b[1] - lat, 0.0, lat - b[3])
    return math.hypot(dx, dy)


def choose_ctx(
    products: Sequence[CtxProduct],
    bbox: Sequence[float],
    threshold_m: float,
    anchor_lonlat: tuple[float, float],
) -> CtxProduct | None:
    """The CTX DTM inside ``bbox`` that passes the QA threshold and lies nearest the anchor
    (ties broken by lower CTX-MOLA SD, then name)."""
    lo0, la0, lo1, la1 = bbox
    inside = [
        p
        for p in products
        if p.sd_m is not None
        and p.sd_m <= threshold_m
        and p.bounds_lonlat[0] >= lo0
        and p.bounds_lonlat[2] <= lo1
        and p.bounds_lonlat[1] >= la0
        and p.bounds_lonlat[3] <= la1
    ]
    if not inside:
        return None
    return min(
        inside,
        key=lambda p: (
            round(_bbox_distance_deg(p.bounds_lonlat, *anchor_lonlat), 9),
            p.sd_m,
            p.name,
        ),
    )


def evaluate_mars(
    raw: Path,
    cfg: Mapping[str, Any],
    terrain: TerrainConfig,
    sources: Sources,
    entry: Mapping[str, Any],
    products: Sequence[CtxProduct],
) -> TargetResult | BlockedResult:
    import rasterio

    name = str(entry["name"])
    bbox = entry.get("bbox")
    if bbox is None:
        return BlockedResult(SearchReason.arcadia_bbox_unset, f"targets.mars.{name}.bbox is null")
    threshold = cfg["targets"].get("ctx_mola_sd_threshold_m")
    if threshold is None:
        return BlockedResult("ctx_qa_threshold_unset", "targets.ctx_mola_sd_threshold_m is null")
    if name == "jezero":
        anchor = MARS2020_LANDING_LONLAT
        rule = (
            "CTX DTM in the Jezero bbox passing CTX QA nearest the Mars 2020 landing site; primary "
            "= eligible window nearest the landing site (PRD 5 crater-floor rule, G0-02 addition)"
        )
    else:
        anchor = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        rule = (
            "CTX DTM in the Arcadia bbox passing CTX QA nearest the bbox centre; primary = "
            "eligible window nearest the DTM centre (PRD 5 window inside the region, G0-02 "
            "addition)"
        )
    chosen = choose_ctx(products, bbox, float(threshold), anchor)
    if chosen is None:
        return BlockedResult("no_ctx_product_passes_qa", f"no CTX DTM in {bbox} passes QA")
    d = _read(chosen.dtm)
    with rasterio.open(chosen.dtm) as s:
        c = s.crs.to_dict()
        crs = s.crs.to_wkt()
    t = d.transform
    radius = float(c.get("R", 3396190.0))
    lk = EquirectangularLookup(
        center_longitude_deg=float(c.get("lon_0", 0.0)),
        standard_parallel_deg=float(c.get("lat_ts", 0.0)),
        latitude_of_origin_deg=float(c.get("lat_0", 0.0)),
        radius_m=radius,
        false_easting_m=float(c.get("x_0", 0.0)),
        false_northing_m=float(c.get("y_0", 0.0)),
        origin_x_m=t.c,
        origin_y_m=t.f,
        pixel_width_m=t.a,
        pixel_height_m=t.e,
    )
    rel = chosen.dtm.relative_to(raw / "ctx_dtm")
    pids = [sources.add(f"CTX/mro_ctx_controlled_usgs_dtms/{rel.as_posix()}", chosen.dtm)]
    if chosen.qa is not None:
        sources.add(f"CTX/mro_ctx_controlled_usgs_dtms/{chosen.name}/qa_metrics.txt", chosen.qa)
        pids.append(f"CTX/mro_ctx_controlled_usgs_dtms/{chosen.name}/qa_metrics.txt")
    # Local AEQD frame about the DTM centre (true metres on the D29 Mars sphere).
    b = chosen.bounds_lonlat
    clat, clon = math.radians((b[1] + b[3]) / 2), math.radians((b[0] + b[2]) / 2)
    mr = body_radius_m("mars")
    arr = d.array.astype(np.float64)
    rows, cols = arr.shape
    # Product corners into the local frame.
    corner_cols = np.array([0, cols - 1, cols - 1, 0], dtype=float)
    corner_rows = np.array([0, 0, rows - 1, rows - 1], dtype=float)
    la, lo = lk.from_pixel(corner_cols, corner_rows)
    fx, fy = aeqd_forward(la, lo, lat0_rad=clat, lon0_rad=clon, radius_m=mr)
    window_m, stride_m = float(cfg["targets"]["window_m"]), float(cfg["targets"]["stride_m"])
    ex = (
        math.floor(float(np.min(fx)) / stride_m) * stride_m,
        math.floor(float(np.min(fy)) / stride_m) * stride_m,
        math.ceil(float(np.max(fx)) / stride_m) * stride_m,
        math.ceil(float(np.max(fy)) / stride_m) * stride_m,
    )
    short = "-".join(chosen.name.split("_")[:2]).lower()
    aeqd_crs = f"+proj=aeqd +lat_0={math.degrees(clat)!r} +lon_0={math.degrees(clon)!r} +R={mr!r}"
    candidates = projected_grid(
        prefix=f"mars-{name}-{short}",
        body="mars",
        frame="aeqd_sphere",
        crs=aeqd_crs,
        extent_m=ex,
        window_m=window_m,
        stride_m=stride_m,
        unproject=lambda x, y: aeqd_inverse(
            np.asarray(x), np.asarray(y), lat0_rad=clat, lon0_rad=clon, radius_m=mr
        ),
    )
    qa = {"ctx_mola_sd_m": chosen.sd_m} if chosen.sd_m is not None else None
    grid = window_grid(window_m, terrain.analysis_grid_m)
    out = []
    for g in candidates:
        lat0, lon0 = math.radians(g.centre_lat_deg), math.radians(g.centre_lon_deg)
        # Generated only if the window centre falls on a valid DTM pixel (product footprint).
        ccol, crow = lk.to_pixel(np.asarray(lat0), np.asarray(lon0))
        ci, ri = math.floor(float(ccol) + 0.5), math.floor(float(crow) + 0.5)
        if (
            not (0 <= ri < rows and 0 <= ci < cols)
            or not np.isfinite(arr[ri, ci])
            or (d.nodata is not None and arr[ri, ci] == d.nodata)
        ):
            continue
        rg = resample_window(
            arr, lk, grid=grid, body="mars", lat0_rad=lat0, lon0_rad=lon0, nodata=d.nodata
        )
        DEM_SINK[g.window_id] = (
            rg.values,
            terrain.analysis_grid_m,
            pids[0],
        )
        dem_valid = float(rg.valid.mean())
        fp = fingerprint_grid(rg, terrain, window_m)
        q = ctx_quality_status(
            dem_valid,
            qa,
            float(threshold),
            is_target=False,
            min_valid_fraction=terrain.min_valid_fraction,
        )
        rejected = [str(q.reason)] if q.status == "rejected" else []
        unknown = [str(q.reason)] if q.status == "unknown" else []
        status, reasons = combine_status(rejected, unknown)
        out.append(
            Evaluated(
                grid=g,
                status=status,
                reasons=reasons,
                fingerprint=fp,
                dem_valid_fraction=dem_valid,
                input_hash=input_hash(rg, {"window": g.window_id, "rule": ISOTROPIC_RULE}),
                product_ids=sorted(pids),
                ctx={"ctx_mola_sd_m": chosen.sd_m, "threshold_m": threshold, "status": q.status},
            )
        )
    # Primary proposal: target windows need >= 0.9 coverage (PRD 5).
    if name == "jezero":
        alon, alat = MARS2020_LANDING_LONLAT
    else:
        alon, alat = math.degrees(clon), math.degrees(clat)
    target_ok = [
        w
        for w in out
        if w.status == "eligible"
        and ctx_quality_status(
            w.dem_valid_fraction,
            qa,
            float(threshold),
            is_target=True,
            min_valid_fraction=terrain.min_valid_fraction,
        ).status
        == "eligible"
    ]

    def ground(w: Evaluated) -> float:
        dlat = math.radians(w.grid.centre_lat_deg - alat)
        dlon = math.radians(w.grid.centre_lon_deg - alon) * math.cos(math.radians(alat))
        return math.hypot(dlat, dlon) * mr

    proposals = [
        w.grid.window_id for w in sorted(target_ok, key=lambda w: (ground(w), w.grid.window_id))[:5]
    ]
    configured = entry.get("primary_window_id")
    if configured is not None and configured not in {w.grid.window_id for w in out}:
        raise SearchError("primary_window_not_in_grid", str(configured))
    return TargetResult(
        target_id=f"mars-{name}-w000",
        body="mars",
        name="Jezero crater" if name == "jezero" else "Arcadia Planitia",
        description=(
            "Mars 2020 landing region in Jezero crater (CTX DTM, terrain only)"
            if name == "jezero"
            else "Arcadia Planitia shallow-ice candidate region (CTX DTM, terrain only)"
        ),
        product_ids=sorted(pids),
        crs=crs,
        projection={
            "kind": "equirectangular",
            "params": {
                "center_longitude_deg": float(c.get("lon_0", 0.0)),
                "standard_parallel_deg": float(c.get("lat_ts", 0.0)),
                "radius_m": radius,
            },
        },
        windows=out,
        primary_window_id=configured,
        primary_rule=rule,
        tile_source={
            "layer": f"mars-{name}-hillshade",
            "extent": (
                float(np.min(fx)),
                float(np.min(fy)),
                float(np.max(fx)),
                float(np.max(fy)),
            ),
            "native_mpp": float(t.a),
            "crs": aeqd_crs,
            "units": "metres, local AEQD frame of the target windows",
            "sample": lambda x, y: _sample_aeqd(arr, lk, d.nodata, clat, clon, mr, x, y),
        },
        proposals=proposals,
        notes=[
            f"CTX DTM {chosen.name}, CTX-MOLA SD {chosen.sd_m} m <= threshold {threshold} m "
            "(G0-06)",
            "windows are 5 km squares in a local AEQD frame about the DTM centre; a window is "
            "generated only if its centre falls on a valid DTM pixel",
            "Mars targets are terrain only, with no published error clones (PRD 5)",
        ],
    )


# --- compute ------------------------------------------------------------------------------------


def _sample_projected(
    arr: npt.NDArray[np.float64], lk: Any, nodata: float | None, x: Any, y: Any
) -> npt.NDArray[np.float64]:
    from terraluna_pipeline.geometry import bilinear_resample

    col, row = lk.pixel_from_projected(x, y)
    return bilinear_resample(arr, col, row, nodata=nodata).values


def _sample_aeqd(
    arr: npt.NDArray[np.float64],
    lk: Any,
    nodata: float | None,
    clat: float,
    clon: float,
    radius: float,
    x: Any,
    y: Any,
) -> npt.NDArray[np.float64]:
    from terraluna_pipeline.geometry import bilinear_resample

    la, lo = aeqd_inverse(x, y, lat0_rad=clat, lon0_rad=clon, radius_m=radius)
    col, row = lk.to_pixel(la, lo)
    return bilinear_resample(arr, col, row, nodata=nodata).values


def build_tiles(
    targets: Sequence[TargetResult],
    regions: Sequence[Region],
    raw: Path,
    processed: Path,
    sources: Sources,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], int]:
    """Hillshade pyramids for every target and Earth region (tiles.py addressing)."""
    from terraluna_pipeline import tiles as T
    from terraluna_pipeline.geometry import bilinear_resample

    az, alt = 315.0, 45.0
    out_root = processed / "tiles"
    budget = T.Budget()
    by_target: dict[str, list[dict[str, Any]]] = {}
    for t in targets:
        src = t.tile_source
        scheme, pyr = T.projected_pyramid(
            out_root,
            src["layer"],
            src["sample"],
            src["extent"],
            src["native_mpp"],
            budget,
            azimuth_deg=az,
            altitude_deg=alt,
        )
        by_target[t.target_id] = [
            T.tile_layer(
                layer=src["layer"],
                body=t.body,
                crs=src["crs"],
                units=src["units"],
                source_product_ids=[p for p in t.product_ids if not p.endswith(".txt")],
                scheme=scheme,
                extent_m=src["extent"],
                pyramid=pyr,
                azimuth_deg=az,
                altitude_deg=alt,
            )
        ]
    earth_layers = []
    for region in regions:
        lons = [p[0] for p in region.polygon_lonlat]
        lats = [p[1] for p in region.polygon_lonlat]
        bbox = (min(lons) - 0.02, min(lats) - 0.02, max(lons) + 0.02, max(lats) + 0.02)
        dems = []
        for lat in range(math.floor(bbox[1]), math.floor(bbox[3]) + 1):
            for lon in range(math.floor(bbox[0]), math.floor(bbox[2]) + 1):
                ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
                ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
                name = f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"
                p = raw / "copernicus_glo30" / name / f"{name}.tif"
                if p.exists():
                    d = _read(p)
                    pid = sources.add(f"COP30/copernicus-dem-30m/{name}/{name}.tif", p)
                    dems.append(
                        (pid, d.array.astype(np.float64), _geo_lookup(d.transform), d.nodata)
                    )

        def sample(lon: Any, lat: Any, dems: Any = dems) -> npt.NDArray[np.float64]:
            out = np.full(np.shape(lon), np.nan)
            for _pid, arr, lk, nod in dems:
                col, row = lk.pixel_from_projected(lon, lat)
                v = bilinear_resample(arr, col, row, nodata=nod)
                take = v.valid & ~np.isfinite(out)
                out[take] = v.values[take]
            return out

        lat_c = math.radians((bbox[1] + bbox[3]) / 2)
        zmax = max(
            z for z in range(0, 20) if 2 * T.MERCATOR_HALF / (512 * 2**z) * math.cos(lat_c) >= 30.0
        )
        layer = f"earth-{region.id}-hillshade"
        scheme, pyr, ext = T.mercator_pyramid(
            out_root, layer, sample, bbox, zmax - 3, zmax, budget, azimuth_deg=az, altitude_deg=alt
        )
        earth_layers.append(
            T.tile_layer(
                layer=layer,
                body="earth",
                crs="EPSG:3857",
                units="metres, Web Mercator (XYZ tiles at 512 px)",
                source_product_ids=[d[0] for d in dems],
                scheme=scheme,
                extent_m=ext,
                pyramid=pyr,
                azimuth_deg=az,
                altitude_deg=alt,
            )
        )
    return by_target, earth_layers, budget.used


def product_records(raw: Path, repo: Path, sources: Sources) -> list[dict[str, Any]]:
    """ProductRecords for products read here that outputs/sources.json does not list yet.

    Each is a copy of the recorded product of the same family with the product-specific
    fields (id, checksum, path, URL, grid, CRS, QA statement) replaced by what the file
    states; fields it does not state keep the family's recorded wording.
    """
    import rasterio

    from terraluna_pipeline.schema import ProductRecord

    known = json.loads((repo / "outputs" / "sources.json").read_text())

    def template(prefix: str, suffix: str) -> dict[str, Any]:
        return dict(
            next(
                r
                for r in known
                if r["product_id"].startswith(prefix) and r["product_id"].endswith(suffix)
            )
        )

    out = []
    for pid, info in sorted(sources.files.items()):
        if pid in sources.known:
            continue
        if pid.startswith("COP30/"):
            rec = template("COP30/", ".tif")
            name = pid.split("/")[2]
            path = raw / "copernicus_glo30" / name / f"{name}.tif"
            rec["source_url"] = (
                f"https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/{name}/{name}.tif"
            )
            rec["local_path"] = f"data/raw/copernicus_glo30/{name}/{name}.tif"
        elif pid.startswith("WorldCover/"):
            rec = template("WorldCover/", ".tif")
            fname = pid.split("/")[-1]
            path = raw / "esa_worldcover" / "v200" / "2021" / "map" / fname
            rec["source_url"] = (
                f"https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/{fname}"
            )
            rec["local_path"] = f"data/raw/esa_worldcover/v200/2021/map/{fname}"
        elif pid.startswith("CTX/") and pid.endswith("qa_metrics.txt"):
            rec = template("CTX/", "qa_metrics.txt")
            item = pid.split("/")[2]
            path = raw / "ctx_dtm" / item / "qa_metrics.txt"
            rec["source_url"] = (
                "https://astrogeo-ard.s3-us-west-2.amazonaws.com/mars/mro/ctx/controlled/usgs/"
                f"{item}/qa_metrics.txt"
            )
            rec["local_path"] = f"data/raw/ctx_dtm/{item}/qa_metrics.txt"
            rec["qa_definition"] = rec["qa_definition"].replace(
                "no CTX QA threshold is applied (PRD 15)",
                "the search applies the G0-06 threshold of 10 m CTX-MOLA SD",
            )
        elif pid.startswith("CTX/"):
            rec = template("CTX/", "_dtm.tif")
            item, fname = pid.split("/")[2], pid.split("/")[3]
            path = raw / "ctx_dtm" / item / fname
            rec["source_url"] = (
                "https://astrogeo-ard.s3-us-west-2.amazonaws.com/mars/mro/ctx/controlled/usgs/"
                f"{item}/{fname}"
            )
            rec["local_path"] = f"data/raw/ctx_dtm/{item}/{fname}"
            qa = raw / "ctx_dtm" / item / "qa_metrics.txt"
            sd = parse_ctx_qa_metrics(qa.read_text()).get("ctx_mola_sd_m") if qa.exists() else None
            with rasterio.open(path) as src:
                nod = src.nodata
            rec["qa_definition"] = (
                f"nodata {nod} as the file declares it; qa_metrics.txt states the CTX-MOLA "
                f"difference statistics ('StdDev of difference: {sd} meters'); the search applies "
                "the G0-06 threshold of 10 m"
            )
        else:
            continue
        rec["product_id"] = pid
        rec["checksum"] = info["sha256"]
        if path.suffix == ".tif":
            with rasterio.open(path) as src:
                t = src.transform
                rec["native_grid"] = (
                    f"width={src.width} height={src.height} "
                    f"transform=({t.a!r}, {t.b!r}, {t.c!r}, {t.d!r}, {t.e!r}, {t.f!r})"
                )
                rec["crs_and_frame"] = src.crs.to_wkt() if src.crs else "unknown"
        out.append(ProductRecord.model_validate(rec).model_dump(mode="json"))
    return out


def _target_record(
    t: TargetResult, res: Mapping[str, Any] | None, *, run_id: str, mode: str | None
) -> dict[str, Any]:
    by_id = {w.grid.window_id: w for w in t.windows}
    fp = None
    if t.primary_window_id in by_id:
        g = by_id[t.primary_window_id].grid
        fp = {
            "frame": g.frame,
            "crs": g.crs,
            "epsg": None,
            "utm_zone": None,
            "ring_m": g.ring_m,
            "ring_lonlat": g.ring_lonlat,
        }
    shortlist = None
    reason: str | None = SearchReason.search_not_run
    if res is not None:
        shortlist = {
            "earth_window_ids": res["shortlist"],
            "rule": "Pareto set over terrain_relief and terrain_texture RMS log-nu distances "
            "(PRD 6.5 step 4)",
            "n_compared": res["n_compared"],
        }
        reason = None
    return {
        "target_id": t.target_id,
        "mock": False,
        "run_id": run_id,
        "body": t.body,
        "name": t.name,
        "description": t.description,
        "product_ids": t.product_ids,
        "crs": t.crs,
        "projection": t.projection,
        "footprint": fp,
        "primary_window_id": t.primary_window_id,
        "window_grid": {
            "window_m": 5000.0,
            "stride_m": 2500.0,
            "n_generated": len(t.windows),
            "n_eligible": sum(w.status == "eligible" for w in t.windows),
        },
        "reference_window_ids": [] if res is None else res["reference_window_ids"],
        "shortlist": shortlist,
        "shortlist_reason": reason,
        "shipping_mode": mode,
        "tile_layers": [],
    }


def _universe(windows: Sequence[Evaluated]) -> dict[str, Any]:
    rej: Counter[str] = Counter()
    unk: Counter[str] = Counter()
    for w in windows:
        if w.status == "rejected":
            rej.update([w.reasons[0]])
        elif w.status == "unknown":
            unk.update([w.reasons[0]])
    n = len(windows)
    e = sum(w.status == "eligible" for w in windows)
    r = sum(w.status == "rejected" for w in windows)
    u = sum(w.status == "unknown" for w in windows)
    assert n == e + r + u
    return {
        "generated": n,
        "eligible": e,
        "rejected": r,
        "unknown": u,
        "rejected_by_reason": dict(sorted(rej.items())),
        "unknown_by_reason": dict(sorted(unk.items())),
    }


def compute(raw: Path, repo: Path, config_path: Path, processed: Path) -> int:
    t0 = time.monotonic()
    cfg = yaml.safe_load(config_path.read_text())
    terrain = load_run_config(config_path).terrain
    out_dir = repo / "outputs" / "search"
    blocked: list[dict[str, str]] = []
    sources = Sources(repo)
    regions = parse_regions(cfg["earth_search"].get("regions"))
    if isinstance(regions, BlockedResult):
        write_bytes(
            out_dir / "blocked.json", dump([{"reason": regions.reason, "detail": regions.detail}])
        )
        return 3
    if cfg["earth_search"]["masks"].get("water") is None:
        write_bytes(out_dir / "blocked.json", dump([{"reason": "water_rule_unset", "detail": ""}]))
        return 3
    bg_n = cfg.get("uncertainty", {}).get("background_sample_size")
    if bg_n is None:
        raise SearchError(SearchReason.background_sample_size_unset, "uncertainty")
    earth = evaluate_earth(raw, regions, cfg, terrain, sources)
    targets: list[TargetResult] = [evaluate_moon(raw, cfg, terrain, sources)]
    ctx = list_ctx(raw)
    for entry in cfg["targets"]["mars"]:
        r = evaluate_mars(raw, cfg, terrain, sources, entry, ctx)
        if isinstance(r, BlockedResult):
            blocked.append(
                {"target": f"mars-{entry['name']}-w000", "reason": r.reason, "detail": r.detail}
            )
        else:
            targets.append(r)

    def run_id_for(product_ids: Iterable[str]) -> str:
        return provenance.run_id(
            config_path, [(p, sources.used.get(p)) for p in sorted(set(product_ids))]
        )

    mode = cfg["run"].get("shipping_mode")
    target_records = []
    summaries = {}
    earth_universe = _universe(earth)
    earth_regions = sorted({w.grid.region_id for w in earth if w.grid.region_id})
    for t in targets:
        res = None
        if t.primary_window_id is None:
            blocked.append(
                {
                    "target": t.target_id,
                    "reason": SearchReason.target_window_unfrozen,
                    "detail": f"proposed by rule ({t.primary_rule}): {t.proposals}",
                }
            )
        else:
            res = search_target(
                t, earth, run_id_for=run_id_for, shipping_mode=mode, background_n=int(bg_n)
            )
        tid = t.target_id
        rid = run_id_for(t.product_ids)
        target_records.append(_target_record(t, res, run_id=rid, mode=mode))
        summary: dict[str, Any] = {
            "target_id": tid,
            "mock": False,
            "run_id": rid,
            "config_sha256": provenance.config_version(config_path),
            "primary_window_id": t.primary_window_id,
            "primary_rule": t.primary_rule,
            "primary_proposals": t.proposals,
            "target_universe": _universe(t.windows),
            "earth_universe": earth_universe,
            "universe_region_ids": earth_regions,
            "isotropic_rule": ISOTROPIC_RULE,
            "notes": t.notes,
        }
        if res is not None:
            COMPARISONS_ADAPTER.validate_python(res["records"])
            raw_bytes = dump(res["records"])
            raw_sha = write_bytes(processed / "search" / tid / "comparisons.raw.json", raw_bytes)
            comp_sha = write_bytes(out_dir / "comparisons" / f"{tid}.json", raw_bytes)
            bg_bytes = dump(res["background"])
            if len(bg_bytes) >= 1_000_000:
                raise SearchError("background_too_large", tid)
            write_bytes(out_dir / "reference" / f"background_{tid}.json", bg_bytes)
            summary.update(
                {
                    "shortlist": res["shortlist"],
                    "shortlist_size": len(res["shortlist"]),
                    "n_compared": res["n_compared"],
                    "comparisons_dropped": res["dropped"],
                    "comparisons_path": f"outputs/search/comparisons/{tid}.json",
                    "comparisons_sha256": comp_sha,
                    "comparisons_raw_sha256": raw_sha,
                    "reference_set_size": len(res["reference_window_ids"]),
                    "reference_spreads": res["reference_spreads"],
                    "background": {
                        "requested_n": res["background"]["requested_n"],
                        "actual_n": res["background"]["actual_n"],
                        "seed": res["background"]["seed"],
                    },
                    "top_candidates": res["top"],
                    "not_ranked": res["not_ranked"],
                    "claim_limits": [] if res["shortlist"] else [NO_CANDIDATE],
                }
            )
        summaries[tid] = (summary, t, res)

    # windows/<body>.json: every target window plus every compared Earth window.
    windows_by_body: dict[str, list[dict[str, Any]]] = {"moon": [], "mars": [], "earth": []}
    for t in targets:
        for w in t.windows:
            windows_by_body[t.body].append(
                window_entry(
                    w, run_id=run_id_for(w.product_ids), analysis_grid_m=terrain.analysis_grid_m
                )
            )
    for w in sorted(earth, key=lambda w: w.grid.window_id):
        windows_by_body["earth"].append(
            window_entry(
                w, run_id=run_id_for(w.product_ids), analysis_grid_m=terrain.analysis_grid_m
            )
        )
    window_hashes = {}
    for body, rows in windows_by_body.items():
        rows.sort(key=lambda e: e["window"]["window_id"])
        WINDOWS_ADAPTER.validate_python(rows)
        data = dump(rows)
        window_hashes[body] = {
            "path": f"data/processed/windows/{body}.json",
            "sha256": write_bytes(processed / "windows" / f"{body}.json", data),
            "bytes": len(data),
            "n": len(rows),
        }
    elapsed = time.monotonic() - t0
    for tid, (summary, t, _res) in summaries.items():
        summary["windows_files"] = {t.body: window_hashes[t.body], "earth": window_hashes["earth"]}
        summary["windows_processed"] = len(t.windows) + len(earth)
        summary["windows_failed"] = 0
        summary["bytes_read"] = sources.bytes_read
        summary["elapsed_s_total_run"] = round(elapsed, 1)
        write_bytes(out_dir / f"summary_{tid}.json", dump(summary))
    dem_dir = processed / "dem_windows"
    dem_dir.mkdir(parents=True, exist_ok=True)
    for window_id, (values, spacing, product_id) in sorted(DEM_SINK.items()):
        np.savez(
            dem_dir / f"{window_id}.npz",
            elevation_m=values,
            spacing_m=np.float64(spacing),
            product_id=np.str_(product_id),
        )
    tiles_by_target, earth_layers, tile_bytes = build_tiles(
        targets, regions, raw, processed, sources
    )
    for rec in target_records:
        rec["tile_layers"] = tiles_by_target.get(rec["target_id"], []) + earth_layers
    TARGETS_ADAPTER.validate_python(target_records)
    write_bytes(out_dir / "targets.json", dump(target_records))
    write_bytes(
        out_dir / "inputs.json",
        dump(
            {
                "products": sources.files,
                "run_id_checksums": sources.used,
                "tiles": {"bytes_total": tile_bytes, "budget_bytes": 300_000_000},
            }
        ),
    )
    write_bytes(out_dir / "sources.json", dump(product_records(raw, repo, sources)))
    write_bytes(out_dir / "blocked.json", dump(blocked))
    return 0


# --- verify -------------------------------------------------------------------------------------


def verify(out_dir: Path) -> list[str]:
    """Internal consistency of committed summaries (no network)."""
    errors: list[str] = []
    for p in sorted(out_dir.glob("summary_*.json")):
        s = json.loads(p.read_text())
        for key in ("target_universe", "earth_universe"):
            u = s[key]
            if u["generated"] != u["eligible"] + u["rejected"] + u["unknown"]:
                errors.append(f"{p.name}: universe_identity_failed ({key})")
        if s.get("bytes_read") is None or s["bytes_read"] < 0:
            errors.append(f"{p.name}: bytes_read_invalid")
        cp = s.get("comparisons_path")
        if cp:
            f = out_dir.parent.parent / cp
            if not f.exists() or sha256_bytes(f.read_bytes()) != s["comparisons_sha256"]:
                errors.append(f"{p.name}: comparisons_hash_mismatch")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m terraluna_pipeline.search")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("compute", help="run the search from local raw products")
    c.add_argument(
        "--raw-root", type=Path, default=Path(os.environ.get("TERRALUNA_RAW", "data/raw"))
    )
    c.add_argument("--repo", type=Path, default=Path("."))
    c.add_argument("--config", type=Path, default=DEFAULT_RUN_CONFIG)
    c.add_argument("--processed", type=Path, default=Path("data/processed"))
    v = sub.add_parser("verify", help="check committed summaries offline")
    v.add_argument("dir", type=Path, nargs="?", default=Path("outputs/search"))
    args = parser.parse_args(argv)
    if args.command == "compute":
        if not args.raw_root.exists():
            print(f"{SearchReason.cache_missing}: {args.raw_root}", file=sys.stderr)
            return 3
        return compute(args.raw_root, args.repo, args.config, args.processed)
    errors = verify(args.dir)
    for e in errors:
        print(e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
