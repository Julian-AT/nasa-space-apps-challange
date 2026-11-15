"""Tests for masks.py (CONTRACTS j, T11-03). Synthetic rasters here are labelled test inputs."""

from __future__ import annotations

import math

import numpy as np
import pytest

from terraluna_pipeline.geometry import GeographicLookup
from terraluna_pipeline.masks import (
    NODATA_CLASS,
    WORLDCOVER_LEGEND,
    WORLDCOVER_VERSION,
    WORLDCOVER_YEAR,
    MaskReason,
    ctx_quality_status,
    nearest_class_sample,
    parse_ctx_qa_metrics,
    sample_window_classes,
    window_land_cover_status,
)

ALLOWED = ["bare_sparse", "snow_ice", "grassland", "shrubland"]
WATER = "worldcover_class_80"


def _raster(parts: dict[int, int]) -> np.ndarray:
    values = np.concatenate([np.full(n, code, dtype=np.int64) for code, n in parts.items()])
    assert values.size == 10_000
    return values.reshape(100, 100)


def test_legend_and_version_are_pinned() -> None:
    assert WORLDCOVER_VERSION == "v200" and WORLDCOVER_YEAR == 2021
    assert dict(WORLDCOVER_LEGEND) == {
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


def test_ninety_percent_is_eligible_boundary_inclusive() -> None:
    s = window_land_cover_status(_raster({60: 9000, 50: 1000}), ALLOWED, 0.9, WATER)
    assert s.status == "eligible" and s.reason is None and s.allowed_fraction == 0.9
    assert sum(s.counts.values()) == s.n == 10_000


def test_eighty_nine_percent_is_rejected() -> None:
    s = window_land_cover_status(_raster({60: 8900, 50: 1100}), ALLOWED, 0.9, WATER)
    assert s.status == "rejected" and s.reason == MaskReason.land_cover_below_min_fraction
    assert sum(s.counts.values()) == s.n


def test_forty_percent_nodata_is_unknown_not_zero() -> None:
    s = window_land_cover_status(_raster({60: 6000, NODATA_CLASS: 4000}), ALLOWED, 0.9, WATER)
    assert s.status == "unknown" and s.reason == MaskReason.land_cover_nodata
    assert s.allowed_fraction is None
    assert sum(s.counts.values()) == s.n


def test_unknown_codes_never_count_as_allowed() -> None:
    s = window_land_cover_status(_raster({60: 8000, 77: 2000}), ALLOWED, 0.9, WATER)
    assert s.status == "unknown" and s.reason == MaskReason.unknown_class
    assert s.counts["unknown_class"] == 2000 and s.counts["allowed"] == 8000


def test_water_rule_unset_snow_counted_and_bodies() -> None:
    s = window_land_cover_status(_raster({70: 10_000}), ALLOWED, 0.9, None)
    assert s.status == "unknown" and s.reason == MaskReason.water_rule_unset
    s = window_land_cover_status(_raster({70: 5000, 60: 4000, 80: 1000}), ALLOWED, 0.9, WATER)
    assert s.status == "eligible" and s.snow_ice == 5000 and s.water == 1000
    m = window_land_cover_status(None, ALLOWED, 0.9, WATER, body="moon")
    assert m.status == "unknown" and m.reason == MaskReason.not_applicable_body
    w = window_land_cover_status(None, ALLOWED, 0.9, WATER)
    assert w.status == "unknown" and w.reason == MaskReason.worldcover_missing


def test_nearest_sampling_never_invents_codes() -> None:
    rng = np.random.default_rng(3)
    codes = np.array(list(WORLDCOVER_LEGEND), dtype=np.uint8)
    raster = rng.choice(codes, size=(400, 400)).astype(np.uint8)
    lookup = GeographicLookup(
        origin_lon_deg=-17.0, origin_lat_deg=65.0, pixel_width_deg=1e-4, pixel_height_deg=-1e-4
    )
    got = sample_window_classes(
        raster,
        lookup,
        window_m=1000.0,
        analysis_grid_m=90.0,
        lat0_rad=math.radians(64.98),
        lon0_rad=math.radians(-16.98),
        nodata=0,
    )
    assert set(np.unique(got)) <= set(codes.tolist())
    # Boundary rule floor(c + 0.5): pixel 0 / 1 boundary goes to pixel 1; outside is nodata.
    lat = np.radians(np.array([65.0 - 0.5e-4, 65.0 - 0.5e-4, 66.0]))
    lon = np.radians(np.array([-17.0 + 1.0e-4, -17.0 + 1.5e-4, -16.99]))
    out = nearest_class_sample(raster, lookup, lat_rad=lat, lon_rad=lon, nodata=0)
    assert out[1] == raster[0, 1] and out[2] == NODATA_CLASS


def test_ctx_quality_rules() -> None:
    qa = {"ctx_mola_sd_m": 8.0}
    assert ctx_quality_status(0.9, qa, 10.0, is_target=True, min_valid_fraction=0.8).status == (
        "eligible"
    )
    s = ctx_quality_status(0.89, qa, 10.0, is_target=True, min_valid_fraction=0.8)
    assert s.status == "rejected" and s.reason == MaskReason.ctx_valid_fraction_below_min
    assert ctx_quality_status(0.85, qa, 10.0, is_target=False, min_valid_fraction=0.8).status == (
        "eligible"
    )
    s = ctx_quality_status(0.95, None, 10.0, is_target=False, min_valid_fraction=0.8)
    assert s.status == "unknown" and s.reason == MaskReason.ctx_qa_missing
    s = ctx_quality_status(0.95, qa, None, is_target=False, min_valid_fraction=0.8)
    assert s.status == "unknown" and s.reason == MaskReason.ctx_qa_threshold_unset
    s = ctx_quality_status(
        0.95, {"ctx_mola_sd_m": 12.3}, 10.0, is_target=False, min_valid_fraction=0.8
    )
    assert s.status == "rejected" and s.reason == MaskReason.ctx_qa_above_threshold


def test_parse_qa_metrics() -> None:
    text = (
        "Max difference:       133.702 meters\nMin difference:       -41.7404 meters\n"
        "StdDev of difference: 12.3412 meters\n"
    )
    got = parse_ctx_qa_metrics(text)
    assert got["ctx_mola_sd_m"] == pytest.approx(12.3412)
    assert parse_ctx_qa_metrics("nothing here") == {}
