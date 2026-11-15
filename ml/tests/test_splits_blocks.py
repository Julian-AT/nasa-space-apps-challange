"""Spatial split maps and the target-leakage buffer (T11-04, T11-05)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from terraluna_ml import build
from terraluna_ml.splits import EmptySplitError, SplitConfig, build_split_map, moon_block
from terraluna_pipeline.geometry import body_radius_m


def _mars() -> dict[str, list[tuple[int, int]]]:
    return {f"d{i}": [(i % 3, 3 * i)] for i in range(12)}


def _earth() -> list[str]:
    return [f"earth:{2 * (i % 4):+.0f}:{4 * i - 100:+.0f}" for i in range(14)]


def test_moon_pole_contact_fails_loudly() -> None:
    with pytest.raises(EmptySplitError, match="moon"):
        build_split_map(
            mars_dtm_tiles=_mars(),
            earth_blocks=_earth(),
            config=SplitConfig(pole_point_counts=True),
        )


def test_edge_only_split_is_complete_and_disjoint() -> None:
    split, dtm_block = build_split_map(
        mars_dtm_tiles=_mars(), earth_blocks=_earth(), config=SplitConfig(seed=3)
    )
    for body in ("moon", "mars", "earth"):
        roles = {r for b, r in split.assignment.items() if split.body_of[b] == body}
        assert {"train", "val", "test"} <= roles
    moon = {b: r for b, r in split.assignment.items() if b.startswith("moon")}
    assert sorted(moon.values()) == ["dropped", "test", "train", "val"]
    assert set(dtm_block) == set(_mars())
    doc = split.as_dict()
    assert (
        doc
        == build_split_map(
            mars_dtm_tiles=_mars(), earth_blocks=_earth(), config=SplitConfig(seed=3)
        )[0].as_dict()
    )


def test_training_block_touching_test_is_dropped() -> None:
    split, _ = build_split_map(mars_dtm_tiles=_mars(), earth_blocks=_earth(), config=SplitConfig())
    for block, reason in split.dropped_reason.items():
        assert split.assignment[block] == "dropped"
        assert "touches" in reason


def test_moon_sector_seam() -> None:
    assert moon_block(179.9) == moon_block(-180.0 + 360.0 - 0.1)
    assert moon_block(-180.0) != moon_block(179.0)


@pytest.mark.parametrize("body", ["moon", "mars"])
def test_leakage_buffer_inside_and_outside(body: str) -> None:
    radius = body_radius_m(body)
    fp = build.Footprint("t", body, 10.0, 179.99, build.WINDOW_HALF_DIAGONAL_M)
    limit = build.LEAKAGE_BUFFER_M + 2 * build.WINDOW_HALF_DIAGONAL_M
    for metres, hit in ((limit - 50.0, True), (limit + 50.0, False)):
        dlat = math.degrees(metres / radius)
        assert (build.leakage_hit([fp], body, 10.0 + dlat, 179.99) is not None) is hit
    # across the longitude seam
    dlon = math.degrees((limit - 100.0) / (radius * math.cos(math.radians(10.0))))
    assert build.leakage_hit([fp], body, 10.0, 179.99 + dlon - 360.0) is not None


def test_leakage_check_fails_on_a_kept_window() -> None:
    fp = build.Footprint("t", "moon", -89.4, -137.0, build.WINDOW_HALF_DIAGONAL_M)
    rows = [
        {"window_id": "w", "body": "moon", "split": "train", "lat_deg": -89.41, "lon_deg": -137.0}
    ]
    assert build.check_leakage([fp], rows) == ["w"]
    rows[0]["split"] = "target"
    assert build.check_leakage([fp], rows) == []


def test_land_cover_status_boundaries() -> None:
    allowed = (20, 30, 60, 70)
    classes = np.array([60] * 90 + [50] * 10, dtype=np.int32)
    assert build.land_cover_status(classes, allowed, 0.9)["status"] == "eligible"
    classes = np.array([60] * 89 + [50] * 11, dtype=np.int32)
    assert (
        build.land_cover_status(classes, allowed, 0.9)["reason"] == "land_cover_below_min_fraction"
    )
    classes = np.array([60] * 60 + [-1] * 40, dtype=np.int32)
    assert build.land_cover_status(classes, allowed, 0.9)["status"] == "unknown"
