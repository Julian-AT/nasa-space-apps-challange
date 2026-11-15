"""WorldCover and CTX eligibility masks in one place (CONTRACTS j; PRD 5, 6.4, D31; T11-03).

Pure functions on in-memory arrays: no network and no file-system access. T5 search and the T11
dataset import them; nothing reimplements them.

WorldCover legend (G0-08: the version T1-07 actually read, ESA WorldCover 10 m 2021 v200). The
codes and names are the provider's Product User Manual legend; the config names
(``earth_search.masks.worldcover_allowed``) map onto them through :data:`CONFIG_NAMES`. A code
outside the legend is an ``unknown_class`` pixel and is never allowed.

Sampling (ANA-05 default): classes are taken by nearest pixel at the D29 pixel centres of the
90 m analysis grid, never bilinearly. The rounding rule is ``floor(c + 0.5)`` on the fractional
pixel-centre coordinate (a point exactly on a boundary between two pixels goes to the pixel with
the larger index). Points outside the raster or on the declared nodata are nodata.

Notes recorded for PRD-04: PRD 6.4 and D31 mask snow, ice and vegetation, while the config list
allows snow_ice, grassland and shrubland. The config list is implemented as given and class 70
pixels are counted separately as ``snow_ice`` so the discrepancy stays visible. Disallowed pixels
inside an accepted window are not turned into nodata (the PRD states no such rule).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal

import numpy as np
import numpy.typing as npt

from terraluna_pipeline.geometry import GeographicLookup, aeqd_inverse, body_radius_m, window_grid

WORLDCOVER_VERSION: Final = "v200"
WORLDCOVER_YEAR: Final = 2021
WORLDCOVER_LEGEND_SOURCE: Final = (
    "ESA WorldCover 10 m 2021 v200 Product User Manual, map legend "
    "(https://esa-worldcover.org/en/data-access)"
)

WORLDCOVER_LEGEND: Final[Mapping[int, str]] = {
    10: "tree_cover",
    20: "shrubland",
    30: "grassland",
    40: "cropland",
    50: "built_up",
    60: "bare_sparse",
    70: "snow_ice",
    80: "water",
    90: "herbaceous_wetland",
    95: "mangroves",
    100: "moss_lichen",
}
"""Class code to config name (the names config ``worldcover_allowed`` uses)."""

CONFIG_NAMES: Final[Mapping[str, int]] = {name: code for code, name in WORLDCOVER_LEGEND.items()}

WATER_CODE: Final = 80
SNOW_ICE_CODE: Final = 70
NODATA_CLASS: Final = -1
"""The sampled value for a point outside the raster or on its nodata."""

WATER_RULES: Final = ("worldcover_class_80",)
"""Water rules this module implements (G0-09)."""


class MaskReason(StrEnum):
    """Reason codes of masks.py (CONTRACTS a.5)."""

    land_cover_below_min_fraction = "land_cover_below_min_fraction"
    land_cover_nodata = "land_cover_nodata"
    unknown_class = "unknown_class"
    worldcover_missing = "worldcover_missing"
    water_rule_unset = "water_rule_unset"
    not_applicable_body = "not_applicable_body"
    ctx_valid_fraction_below_min = "ctx_valid_fraction_below_min"
    ctx_qa_missing = "ctx_qa_missing"
    ctx_qa_threshold_unset = "ctx_qa_threshold_unset"
    ctx_qa_above_threshold = "ctx_qa_above_threshold"
    radar_mask_unavailable = "radar_mask_unavailable"


Status = Literal["eligible", "rejected", "unknown"]


class MaskError(ValueError):
    """Raised for a config value or input the masks cannot use."""


@dataclass(frozen=True)
class LandCoverStatus:
    """Eligibility of one window by land cover (CONTRACTS j).

    ``counts`` holds pixel counts by kind (``allowed``, ``disallowed``, ``unknown_class``,
    ``nodata``) and sums to ``n``. ``snow_ice`` and ``water`` count class 70 and 80 pixels
    separately. ``allowed_fraction`` is None when the status is unknown, never 0.
    """

    status: Status
    reason: MaskReason | None
    allowed_fraction: float | None
    n: int
    counts: Mapping[str, int]
    snow_ice: int
    water: int
    class_counts: Mapping[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CtxQualityStatus:
    """Eligibility of one Mars window by CTX coverage and QA (CONTRACTS j)."""

    status: Status
    reason: MaskReason | None
    valid_fraction: float
    min_valid_fraction: float
    ctx_mola_sd_m: float | None
    threshold_m: float | None


def allowed_codes(allowed: Sequence[str]) -> frozenset[int]:
    """Class codes of the config names in ``allowed``; an unknown name raises MaskError."""
    codes = set()
    for name in allowed:
        if name not in CONFIG_NAMES:
            raise MaskError(f"unknown WorldCover class name {name!r}")
        codes.add(CONFIG_NAMES[name])
    return frozenset(codes)


def nearest_class_sample(
    classes: npt.NDArray[np.integer],
    lookup: GeographicLookup,
    *,
    lat_rad: npt.NDArray[np.float64],
    lon_rad: npt.NDArray[np.float64],
    nodata: int | None,
) -> npt.NDArray[np.int64]:
    """Sample a class raster by nearest pixel at points in radians (rule in the module doc).

    Returns int64 codes with :data:`NODATA_CLASS` outside the raster or on ``nodata``. Every
    other value is a raster value, so sampling never creates a code that is not in the raster.
    """
    grid = np.asarray(classes)
    if grid.ndim != 2 or grid.dtype.kind not in "iu":
        raise MaskError("classes must be a 2-D integer raster")
    column, row = lookup.to_pixel(lat_rad, lon_rad)
    c = np.floor(np.asarray(column) + 0.5)
    r = np.floor(np.asarray(row) + 0.5)
    rows, cols = grid.shape
    inside = (c >= 0) & (c < cols) & (r >= 0) & (r < rows)
    out = np.full(np.shape(c), NODATA_CLASS, dtype=np.int64)
    ci = np.where(inside, c, 0).astype(np.intp)
    ri = np.where(inside, r, 0).astype(np.intp)
    values = grid[ri, ci].astype(np.int64)
    if nodata is not None:
        inside &= values != int(nodata)
    out[inside] = values[inside]
    return out


def sample_window_classes(
    classes: npt.NDArray[np.integer],
    lookup: GeographicLookup,
    *,
    window_m: float,
    analysis_grid_m: float,
    lat0_rad: float,
    lon0_rad: float,
    nodata: int | None,
) -> npt.NDArray[np.int64]:
    """Nearest-class sample at the D29 pixel centres of an Earth window about (lat0, lon0)."""
    grid = window_grid(window_m, analysis_grid_m)
    x, y = grid.mesh_m()
    lat, lon = aeqd_inverse(
        x, y, lat0_rad=lat0_rad, lon0_rad=lon0_rad, radius_m=body_radius_m("earth")
    )
    return nearest_class_sample(classes, lookup, lat_rad=lat, lon_rad=lon, nodata=nodata)


def window_land_cover_status(
    classes: npt.NDArray[np.integer] | None,
    allowed: Sequence[str],
    min_allowed_fraction: float,
    water_rule: str | None,
    *,
    body: str = "earth",
) -> LandCoverStatus:
    """Land-cover status of one window's sampled classes (CONTRACTS j).

    ``classes`` are sampled codes (:data:`NODATA_CLASS` for nodata), or None when the WorldCover
    tile is missing (``worldcover_missing``). The boundary is inclusive: an allowed fraction of
    exactly ``min_allowed_fraction`` is eligible.
    """
    empty: dict[str, int] = {"allowed": 0, "disallowed": 0, "unknown_class": 0, "nodata": 0}
    if body != "earth":
        return LandCoverStatus("unknown", MaskReason.not_applicable_body, None, 0, empty, 0, 0)
    if not (isinstance(min_allowed_fraction, float | int) and 0 < min_allowed_fraction <= 1):
        raise MaskError(f"min_allowed_fraction must lie in (0, 1], got {min_allowed_fraction!r}")
    codes = allowed_codes(allowed)
    if classes is None:
        return LandCoverStatus("unknown", MaskReason.worldcover_missing, None, 0, empty, 0, 0)
    values = np.asarray(classes).ravel()
    n = int(values.size)
    if n == 0:
        raise MaskError("no class samples")
    nodata = int(np.count_nonzero(values == NODATA_CLASS))
    legend = np.isin(values, list(WORLDCOVER_LEGEND))
    unknown_class = int(np.count_nonzero(~legend & (values != NODATA_CLASS)))
    allowed_n = int(np.count_nonzero(np.isin(values, list(codes))))
    disallowed = n - nodata - unknown_class - allowed_n
    counts = {
        "allowed": allowed_n,
        "disallowed": disallowed,
        "unknown_class": unknown_class,
        "nodata": nodata,
    }
    snow = int(np.count_nonzero(values == SNOW_ICE_CODE))
    water = int(np.count_nonzero(values == WATER_CODE))
    uniq, cnt = np.unique(values, return_counts=True)
    class_counts = {int(k): int(v) for k, v in zip(uniq, cnt, strict=True)}
    if water_rule is None:
        return LandCoverStatus(
            "unknown", MaskReason.water_rule_unset, None, n, counts, snow, water, class_counts
        )
    if water_rule not in WATER_RULES:
        raise MaskError(f"unknown water rule {water_rule!r}; known: {', '.join(WATER_RULES)}")
    if WATER_CODE in codes:
        raise MaskError("the water rule masks class 80, so it cannot also be allowed")
    missing = nodata + unknown_class
    if missing / n > 1 - min_allowed_fraction:
        reason = (
            MaskReason.land_cover_nodata if nodata >= unknown_class else MaskReason.unknown_class
        )
        return LandCoverStatus("unknown", reason, None, n, counts, snow, water, class_counts)
    fraction = allowed_n / n
    if fraction >= min_allowed_fraction:
        return LandCoverStatus("eligible", None, fraction, n, counts, snow, water, class_counts)
    return LandCoverStatus(
        "rejected",
        MaskReason.land_cover_below_min_fraction,
        fraction,
        n,
        counts,
        snow,
        water,
        class_counts,
    )


TARGET_MIN_VALID_FRACTION: Final = 0.9
"""PRD 5: a primary Mars target window needs at least 90 % valid CTX coverage."""


def ctx_quality_status(
    valid_fraction: float,
    qa_metrics: Mapping[str, float] | None,
    ctx_mola_sd_threshold_m: float | None,
    *,
    is_target: bool,
    min_valid_fraction: float,
) -> CtxQualityStatus:
    """CTX coverage and QA status of a Mars window (CONTRACTS j; PRD 5, 11.2).

    ``qa_metrics`` holds ``ctx_mola_sd_m`` parsed from qa_metrics.txt, or None if the file is
    missing. ``min_valid_fraction`` is config ``terrain.min_valid_fraction``; target windows
    need at least :data:`TARGET_MIN_VALID_FRACTION`. Uncertain quality is never zero error.
    """
    if not (math.isfinite(valid_fraction) and 0 <= valid_fraction <= 1):
        raise MaskError(f"valid_fraction must lie in [0, 1], got {valid_fraction!r}")
    need = max(TARGET_MIN_VALID_FRACTION, min_valid_fraction) if is_target else min_valid_fraction
    sd = None if qa_metrics is None else qa_metrics.get("ctx_mola_sd_m")

    def status(s: Status, reason: MaskReason | None) -> CtxQualityStatus:
        return CtxQualityStatus(s, reason, valid_fraction, need, sd, ctx_mola_sd_threshold_m)

    if qa_metrics is None or sd is None:
        return status("unknown", MaskReason.ctx_qa_missing)
    if ctx_mola_sd_threshold_m is None:
        return status("unknown", MaskReason.ctx_qa_threshold_unset)
    if sd > ctx_mola_sd_threshold_m:
        return status("rejected", MaskReason.ctx_qa_above_threshold)
    if valid_fraction < need:
        return status("rejected", MaskReason.ctx_valid_fraction_below_min)
    return status("eligible", None)


def parse_ctx_qa_metrics(text: str) -> dict[str, float]:
    """Parse the CTX-MOLA difference statistics from a qa_metrics.txt (USGS ASP output).

    Returns ``ctx_mola_sd_m`` (and mean, median, min, max where stated), all in metres. A file
    without the standard-deviation line gives an empty dict (treated as QA missing).
    """
    keys = {
        "StdDev of difference": "ctx_mola_sd_m",
        "Mean difference": "ctx_mola_mean_m",
        "Median difference": "ctx_mola_median_m",
        "Max difference": "ctx_mola_max_m",
        "Min difference": "ctx_mola_min_m",
    }
    out: dict[str, float] = {}
    for line in text.splitlines():
        head, sep, tail = line.partition(":")
        name = keys.get(head.strip())
        if not sep or name is None or name in out:
            continue
        parts = tail.split()
        if len(parts) >= 2 and parts[1] == "meters":
            try:
                value = float(parts[0])
            except ValueError:
                continue
            if math.isfinite(value):
                out[name] = value
    return out
