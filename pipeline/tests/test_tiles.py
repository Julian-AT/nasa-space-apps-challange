"""Offline tests of tiles.py (T5-06). The synthetic DEM is a labelled test input (mock true)."""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from pydantic import TypeAdapter

from terraluna_pipeline.schema import TileLayer
from terraluna_pipeline.tiles import (
    TILE_PX,
    Budget,
    TileError,
    encode_webp,
    hillshade,
    projected_pyramid,
    projected_scheme,
    tile_extent,
    tile_index_for_point,
    tile_layer,
)


def test_tile_extent_round_trip() -> None:
    scheme = projected_scheme((-19000.0, -20000.0, -3000.0, -4000.0), 5.0)
    rng = np.random.default_rng(1)
    for _ in range(200):
        z = int(rng.integers(scheme.zoom_min, scheme.zoom_max + 1))
        px = float(rng.uniform(-19000, -3000))
        py = float(rng.uniform(-20000, -4000))
        x, y = tile_index_for_point(scheme, z, px, py)
        x0, y0, x1, y1 = tile_extent(scheme, z, x, y)
        assert x0 <= px < x1 and y0 < py <= y1
        t = scheme.tile_m(z)
        for v, o in ((x0, scheme.origin_x_m), (y1, scheme.origin_y_m)):
            k = (v - o) / t
            assert abs(k - round(k)) <= 1e-9 * max(1.0, abs(k))


def test_hillshade_flat_and_tilted() -> None:
    flat = np.zeros((5, 5))
    hs = hillshade(flat, 10.0, 10.0, azimuth_deg=315.0, altitude_deg=45.0)
    assert hs[2, 2] == pytest.approx(math.sin(math.radians(45.0)), abs=1e-12)
    # The face whose downslope points at the sun (NW) is brighter.
    nw_facing = np.add.outer(np.arange(5.0), np.arange(5.0)) * 3.0  # higher to the SE
    se_facing = -nw_facing
    hn = hillshade(nw_facing, 10.0, 10.0, azimuth_deg=315.0, altitude_deg=45.0)[2, 2]
    hse = hillshade(se_facing, 10.0, 10.0, azimuth_deg=315.0, altitude_deg=45.0)[2, 2]
    assert hn > hse
    nan = flat.copy()
    nan[2, 2] = np.nan
    assert np.isnan(hillshade(nan, 10.0, 10.0, azimuth_deg=315.0, altitude_deg=45.0)[2, 2])


def test_nodata_is_transparent() -> None:
    shade = np.full((TILE_PX, TILE_PX), 0.5)
    shade[:10, :10] = np.nan
    img = Image.open(io.BytesIO(encode_webp(shade))).convert("RGBA")
    arr = np.asarray(img)
    assert img.size == (TILE_PX, TILE_PX)
    assert arr[0, 0, 3] == 0 and arr[100, 100, 3] == 255


def _synthetic(tmp_path: Path, budget: Budget) -> tuple[Any, Any]:
    n = 2048
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float64)
    dem = np.sin(xx / 50.0) * 20 + np.cos(yy / 70.0) * 15
    dem[:600, :600] = np.nan  # nodata corner -> one all-nodata tile at the finest zoom

    def sample(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        c = np.clip(np.floor(x).astype(int), 0, n - 1)
        r = np.clip(np.floor(n - y).astype(int), 0, n - 1)
        out: np.ndarray = dem[r, c]
        return out

    return projected_pyramid(
        tmp_path / "tiles",
        "synthetic-mock",
        sample,
        (0.0, 0.0, float(n), float(n)),
        1.0,
        budget,
        azimuth_deg=315.0,
        altitude_deg=45.0,
        mock=True,
    )


def test_pyramid_counts_omissions_and_layer_record(tmp_path: Path) -> None:
    scheme, pyr = _synthetic(tmp_path, Budget())
    assert scheme.zoom_max == 2
    per_zoom = {z: 0 for z in range(3)}
    for z, *_ in pyr.written:
        per_zoom[z] += 1
    assert per_zoom == {0: 1, 1: 4, 2: 15}
    assert pyr.omitted == [(2, 0, 0)]
    files = list((tmp_path / "tiles").rglob("*.webp"))
    assert sum(f.stat().st_size for f in files) == pyr.bytes_total
    rec = tile_layer(
        layer="synthetic-mock",
        body="moon",
        crs="synthetic",
        units="m",
        source_product_ids=[],
        scheme=scheme,
        extent_m=(0.0, 0.0, 2048.0, 2048.0),
        pyramid=pyr,
        azimuth_deg=315.0,
        altitude_deg=45.0,
        mock=True,
    )
    TypeAdapter(TileLayer).validate_python(rec)


def test_budget_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(TileError) as err:
        _synthetic(tmp_path, Budget(limit=1000))
    assert err.value.code == "tile_budget_exceeded"
