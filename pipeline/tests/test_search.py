"""Offline tests of search.py (T5). Synthetic inputs here are labelled test inputs only."""

from __future__ import annotations

import itertools
import math

import pytest
from pyproj import Transformer

from terraluna_pipeline.search import (
    BlockedResult,
    Region,
    SearchError,
    SearchReason,
    background_histogram,
    background_percentile,
    combine_status,
    dump,
    earth_grid,
    grid_count,
    pareto_layers,
    parse_regions,
    reference_spread,
    square_inside_polygon,
    terrain_distance,
)

EPSG = 32627
WINDOW, STRIDE = 5000.0, 2500.0


def _region_from_utm(ring_m: list[tuple[float, float]], rid: str = "synthetic-test") -> Region:
    inv = Transformer.from_crs(f"EPSG:{EPSG}", "EPSG:4326", always_xy=True)
    lon, lat = inv.transform([p[0] for p in ring_m], [p[1] for p in ring_m])
    return Region(
        id=rid,
        epsg=EPSG,
        utm_zone="27N",
        polygon_lonlat=[[a, b] for a, b in zip(lon, lat, strict=True)],
        scene_ids=("scene-a",),
        acquisition_dates=("2015-05-30",),
        radar_unavailable=False,
    )


def _rect(x0: float, y0: float, w: float, h: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h), (x0, y0)]


def test_grid_count_formula() -> None:
    assert grid_count(15000, WINDOW, STRIDE) == 5
    assert grid_count(10000, WINDOW, STRIDE) == 3
    assert grid_count(4999, WINDOW, STRIDE) == 0


def test_earth_grid_rectangle_yields_fifteen_windows() -> None:
    eps = 0.01  # the lon/lat round trip of the corners, a centimetre of slack
    region = _region_from_utm(_rect(700000 - eps, 7190000 - eps, 15000 + 2 * eps, 10000 + 2 * eps))
    wins = earth_grid(region, window_m=WINDOW, stride_m=STRIDE)
    assert len(wins) == 15
    for w in wins:
        (x0, y0), (x1, _), (_, y2) = w.ring_m[0], w.ring_m[1], w.ring_m[2]
        assert x1 - x0 == 5000.0 and y2 - y0 == 5000.0
        assert w.epsg == EPSG and w.utm_zone == "27N" and w.body == "earth"
        assert w.region_id == "synthetic-test" and w.scene_ids == ("scene-a",)
        assert w.window_id == f"earth-utm27n-e{w.grid_index[0]}-n{w.grid_index[1]}"
    xs = sorted({w.ring_m[0][0] for w in wins})
    assert [b - a for a, b in itertools.pairwise(xs)] == [2500.0] * 4
    narrow = _region_from_utm(_rect(700000, 7190000, 4999, 10000))
    assert earth_grid(narrow, window_m=WINDOW, stride_m=STRIDE) == []
    again = earth_grid(region, window_m=WINDOW, stride_m=STRIDE)
    assert [w.window_id for w in again] == [w.window_id for w in wins]


def test_earth_grid_refuses_other_bodies() -> None:
    region = _region_from_utm(_rect(700000, 7190000, 15000, 10000))
    with pytest.raises(SearchError):
        earth_grid(region, window_m=WINDOW, stride_m=STRIDE, body="moon")


def test_concave_notch_rejects_crossing_window() -> None:
    # A 10 x 5 km rectangle with a notch reaching into the middle from the top.
    poly = [(0, 0), (10000, 0), (10000, 5000), (5200, 5000), (5000, 2000), (4800, 5000), (0, 5000)]
    assert square_inside_polygon(0, 0, 4700, poly)
    assert not square_inside_polygon(2500, 0, 5000, poly)


def test_parse_regions_blocked_and_invalid() -> None:
    blocked = parse_regions([])
    assert isinstance(blocked, BlockedResult) and blocked.reason == SearchReason.regions_unset
    with pytest.raises(SearchError):
        parse_regions(
            [{"id": "x", "epsg": 1, "utm_zone": "1N", "polygon_lonlat": [], "radar": "x"}]
        )
    ok = parse_regions(
        [
            {
                "id": "t",
                "epsg": 32611,
                "utm_zone": "11N",
                "radar": "unavailable",
                "polygon_lonlat": [],
            }
        ]
    )
    assert not isinstance(ok, BlockedResult) and ok[0].radar_unavailable


def test_terrain_distance_rms_log_nu() -> None:
    a = {270.0: 1.0, 360.0: 2.0, 540.0: None}
    b = {270.0: math.e, 360.0: 2.0, 540.0: 5.0}
    d = terrain_distance(a, b)
    assert d.n_lags == 2 and d.value == pytest.approx(math.sqrt(0.5), abs=1e-15)
    assert terrain_distance(a, a).value == 0.0  # a real zero, identical fingerprints
    assert terrain_distance({270.0: None}, b).reason == SearchReason.no_shared_lags
    assert terrain_distance({270.0: 0.0}, b).reason == SearchReason.nu_zero


def test_pareto_five_points() -> None:
    pts: dict[str, list[float | None]] = {
        "a": [1.0, 5.0],
        "b": [2.0, 2.0],
        "c": [5.0, 1.0],
        "d": [3.0, 3.0],  # dominated by b
        "e": [6.0, 6.0],  # dominated by all
    }
    layers = pareto_layers(pts)
    assert sorted(k for k, v in layers.items() if v == 1) == ["a", "b", "c"]
    assert layers["d"] == 2 and layers["e"] == 3
    assert "x" not in pareto_layers({"x": [None, 1.0]})


def test_reference_spread_needs_two() -> None:
    assert reference_spread([0.3]) is None
    assert reference_spread([0.1, 0.4, 0.2]) == {"min": 0.1, "median": 0.2, "max": 0.4, "n": 3}


def test_background_percentile_linear_within_bin() -> None:
    h = background_histogram([0.5, 1.5, 2.5, 3.5], bins=4)
    assert h["bin_edges"] == [0.0, 0.875, 1.75, 2.625, 3.5] and h["counts"] == [1, 1, 1, 1]
    edges, counts = [0.0, 1.0, 2.0], [2, 2]
    assert background_percentile(-1.0, edges, counts) == 0.0
    assert background_percentile(3.0, edges, counts) == 100.0
    assert background_percentile(0.5, edges, counts) == 25.0
    assert background_percentile(1.5, edges, counts) == 75.0


def test_status_rules_and_deterministic_dump() -> None:
    assert combine_status([], []) == ("eligible", [])
    assert combine_status([], ["worldcover_missing"]) == ("unknown", ["worldcover_missing"])
    status, reasons = combine_status(["land_cover_below_min_fraction"], ["x"])
    assert status == "rejected" and reasons[0] == "land_cover_below_min_fraction"
    assert dump({"b": 1, "a": [1.5]}) == b'{\n  "a": [\n    1.5\n  ],\n  "b": 1\n}\n'
