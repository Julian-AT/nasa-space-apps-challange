"""Encoder input windows (T11-01; PRD 7 "Window resampler (D29)", 11.2, 8.13 ``ml``).

A window is 64 x 64 pixels at 78.125 m, exactly 5,000 m, cut by the D29 resampler of
``terraluna_pipeline.geometry`` and nothing else. A best-fit plane is removed over valid pixels
only; the channels are asinh(z / 50 m), slope magnitude / 45 degrees and the valid mask, stored
as float16 with 0.0 in channels 0 and 1 wherever the valid channel is 0.

Tolerances are PRD 7's (rtol 1e-10, atol 1e-12). The plane bound below is derived from the
T3-03 curvature residual before any window is cut; it is never an epsilon fitted to a result.
"""

from __future__ import annotations

import ast
import dataclasses
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from terraluna_ml import dataset
from terraluna_ml.dataset import (
    CHANNELS,
    ELEVATION_SCALE_M,
    INVALID_FILL,
    PIXEL_M,
    SLOPE_SCALE_DEG,
    WINDOW_GRID,
    WINDOW_M,
    WINDOW_PX,
    DatasetError,
    EncoderWindow,
    encode_channels,
    input_encoding_record,
    sample_window,
)
from terraluna_ml.synthetic import (
    NODATA_SENTINEL,
    SyntheticDem,
    fbm_dem,
    hole_dem,
    plane_dem,
    polar_fourfold_dem,
)
from terraluna_pipeline import geometry
from terraluna_pipeline.config import TerrainConfig, load_run_config
from terraluna_pipeline.geometry import (
    BODY_RADII_M,
    WindowGrid,
    aeqd_inverse,
    resample_window,
)
from terraluna_pipeline.terrain import structure_function, terrain_fingerprint

Array = npt.NDArray[np.float64]

RTOL = 1e-10
ATOL = 1e-12
BODIES = ("moon", "mars", "earth")
ML_ROOT = Path(__file__).resolve().parents[1]

# A terrain config on the 78.125 m encoder grid, for comparing plane removal with T3's texture
# variant only. It is a test setting, not the run config: the ranking measures on 90 m.
LAGS_78 = (234.375, 312.5, 468.75, 625.0, 937.5)
ENCODER_GRID_TERRAIN = TerrainConfig(
    analysis_grid_m=78.125,
    lags_m=LAGS_78,
    min_lags_for_H=4,
    min_lag_span_factor=4.0,
    detrend=("none", "plane"),
    min_valid_fraction=0.8,
    resampler="aeqd_sphere",
    window_m=5000.0,
    stride_m=5000.0,
)


def assert_close(actual: Array, expected: Array) -> None:
    np.testing.assert_allclose(actual, expected, rtol=RTOL, atol=ATOL)


def fbm_source(seed: int = 4, body: str = "mars") -> SyntheticDem:
    return fbm_dem(
        seed=seed,
        body=body,  # type: ignore[arg-type]
        hurst=0.75,
        rms_m=40.0,
        center_lat_deg=-20.0,
        center_lon_deg=135.0,
        half_width_m=3000.0,
        pixel_m=20.0,
    )


def hole_source(seed: int = 6, hole_radius_m: float = 600.0) -> SyntheticDem:
    return hole_dem(
        seed=seed,
        body="moon",
        hurst=0.6,
        rms_m=25.0,
        hole_radius_m=hole_radius_m,
        hole_offset_m=(300.0, -200.0),
        center_lat_deg=10.0,
        center_lon_deg=-60.0,
        half_width_m=3000.0,
        pixel_m=20.0,
    )


def polar_source(seed: int = 8) -> SyntheticDem:
    return polar_fourfold_dem(
        seed=seed, body="moon", pole="south", hurst=0.8, rms_m=20.0, half_size_px=360, pixel_m=20.0
    )


@dataclasses.dataclass(frozen=True, kw_only=True)
class RasterSource:
    """A DemSource written here: another raster on a synthetic DEM's lookup (still a mock)."""

    body: geometry.Body
    raster: Array
    nodata: float | None
    lookup: geometry.SourceLookup
    mock: bool = True


# ---------------------------------------------------------------------------------------------
# Constants (PRD 11.2 and the 8.13 ml block)


def test_the_window_is_64_pixels_of_78_125_m_making_exactly_5000_m() -> None:
    assert WINDOW_PX == 64
    assert PIXEL_M == 78.125
    assert WINDOW_PX * PIXEL_M == 5000.0
    assert WINDOW_M == 5000.0
    run = load_run_config()
    assert run.value("ml.input_px") == WINDOW_PX
    assert run.value("ml.input_m_per_px") == PIXEL_M


def test_the_window_grid_is_the_d29_grid_with_exact_spacing_and_no_margin() -> None:
    assert isinstance(WINDOW_GRID, WindowGrid)
    reference = geometry.window_grid(5000.0, 78.125)
    assert WINDOW_GRID.size == reference.size == 64
    assert WINDOW_GRID.width_m == 5000.0
    assert WINDOW_GRID.spacing_m == 78.125
    assert WINDOW_GRID.uncovered_m == 0.0
    assert WINDOW_GRID.x_m.tobytes() == reference.x_m.tobytes()
    assert WINDOW_GRID.y_m.tobytes() == reference.y_m.tobytes()
    assert np.all(np.diff(WINDOW_GRID.x_m) == 78.125)
    assert np.all(np.diff(WINDOW_GRID.y_m) == -78.125)
    # The pixel cells cover exactly [-2500 m, 2500 m]: nothing outside is part of the window.
    assert WINDOW_GRID.x_m[0] - 78.125 / 2 == -2500.0
    assert WINDOW_GRID.x_m[-1] + 78.125 / 2 == 2500.0


def test_the_channel_constants_and_the_invalid_fill_are_recorded() -> None:
    assert ELEVATION_SCALE_M == 50.0
    assert SLOPE_SCALE_DEG == 45.0
    assert INVALID_FILL == 0.0
    assert CHANNELS == ("asinh_z_over_50m", "slope_over_45deg", "valid")
    record = input_encoding_record()
    assert record["window_px"] == 64
    assert record["pixel_m"] == 78.125
    assert record["window_m"] == 5000.0
    assert record["elevation_scale_m"] == 50.0
    assert record["slope_scale_deg"] == 45.0
    assert record["invalid_fill"] == 0.0
    assert record["channels"] == list(CHANNELS)
    assert record["storage_dtype"] == "float16"
    assert record["resampler"] == "aeqd_sphere"


# ---------------------------------------------------------------------------------------------
# Resampler use


def test_sample_window_calls_the_d29_resampler_once_with_the_encoder_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    real: Callable[..., geometry.ResampledGrid] = vars(dataset)["resample_window"]

    def spy(*args: Any, **kwargs: Any) -> geometry.ResampledGrid:
        calls.append({"args": args, **kwargs})
        return real(*args, **kwargs)

    monkeypatch.setattr(dataset, "resample_window", spy)
    source = fbm_source()
    window = sample_window(source, -20.0, 135.0)
    assert len(calls) == 1
    call = calls[0]
    assert call["args"][0] is source.raster
    assert call["args"][1] is source.lookup
    assert call["grid"].width_m == 5000.0 and call["grid"].spacing_m == 78.125
    assert call["grid"].size == 64
    assert call["body"] == "mars"
    assert call["lat0_rad"] == math.radians(-20.0)
    assert call["lon0_rad"] == math.radians(135.0)
    assert call["nodata"] == NODATA_SENTINEL
    assert vars(dataset)["resample_window"] is spy
    assert real is geometry.resample_window
    assert window.body_radius_m == BODY_RADII_M["mars"]


def test_a_window_has_three_channels_of_64_by_64_and_float16_storage() -> None:
    window = sample_window(fbm_source(), -20.0, 135.0)
    assert isinstance(window, EncoderWindow)
    assert window.channels.shape == (3, 64, 64)
    assert window.channels.dtype == np.float64
    assert window.elevation_m.shape == window.residual_m.shape == window.valid.shape == (64, 64)
    storage = window.storage()
    assert storage.shape == (3, 64, 64)
    assert storage.dtype == np.float16
    assert storage.tobytes() == window.channels.astype(np.float16).tobytes()
    assert (window.body, window.lat0_deg, window.lon0_deg, window.mock) == (
        "mars",
        -20.0,
        135.0,
        True,
    )
    assert not window.channels.flags.writeable


def test_the_window_values_are_the_resampler_values() -> None:
    source = fbm_source()
    window = sample_window(source, -20.0, 135.0)
    reference = resample_window(
        source.raster,
        source.lookup,
        grid=geometry.window_grid(5000.0, 78.125),
        body="mars",
        lat0_rad=math.radians(-20.0),
        lon0_rad=math.radians(135.0),
        nodata=NODATA_SENTINEL,
    )
    assert np.all(reference.valid)
    assert np.array_equal(window.valid, reference.valid)
    assert window.elevation_m.tobytes() == reference.values.tobytes()


# ---------------------------------------------------------------------------------------------
# A plane stays a plane: the bound comes from T3-03 and the source spacing


def curvature_bound(pixel_m: float, b: float, c: float, extent_m: float, radius: float) -> float:
    """T3-03's stated curvature residual for z = a + b x + c y on an equatorial source.

    Copied from the statement in pipeline/tests/test_geometry_resample.py (T3-03, decision 7):
    the source is equirectangular about (0, 0) with true scale on the equator, the plane is in
    the window frame about the same centre, and bilinear interpolation over source cells of
    side h errs by at most

        1.01 * h^2 / 8 * (|c| + 2 |b|) * extent / (3 R^2)

    per window pixel (the 1% covers fifth-order terms). ``extent`` bounds |X| and |Y| over the
    source cells the window reads: half the window plus one source pixel.
    """
    return 1.01 * pixel_m**2 / 8 * (abs(c) + 2 * abs(b)) * extent_m / (3 * radius**2)


def least_squares_factor(grid: WindowGrid) -> float:
    """Bound on max|best-fit plane residual| over max|deviation from a plane| (as in T3-03).

    On a full grid with centred coordinates 1, x and y are orthogonal, so the projection
    P e = mean(e) + x (sum x e / sum x^2) + y (sum y e / sum y^2) gives
    |(I - P) e| <= (2 + max|x| mean|x| / mean x^2 + max|y| mean|y| / mean y^2) max|e|.
    """
    factor = 2.0
    for coordinate in (grid.x_m, grid.y_m):
        centred = coordinate - coordinate.mean()
        factor += float(np.max(np.abs(centred)) * np.mean(np.abs(centred)) / np.mean(centred**2))
    return factor


PLANE_PIXEL_M = 20.0
PLANE_HALF_M = 2600.0


def plane_deviation_bound(body: str, b: float, c: float, plane: Array) -> float:
    """Largest deviation of a resampled window pixel from the plane, before plane removal.

    The T3-03 curvature residual at the source spacing, plus PRD 7's arithmetic allowance for
    the largest plane value in the window (the source values are rounded float64 too).
    """
    radius = BODY_RADII_M[body]
    extent = WINDOW_M / 2 + PLANE_PIXEL_M
    curvature = curvature_bound(PLANE_PIXEL_M, b, c, extent, radius)
    return curvature + ATOL + RTOL * float(np.max(np.abs(plane)))


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_a_plane_source_leaves_texture_below_the_t3_curvature_bound(body: str, seed: int) -> None:
    source = plane_dem(
        seed=seed,
        body=body,  # type: ignore[arg-type]
        center_lat_deg=0.0,
        center_lon_deg=0.0,
        half_width_m=PLANE_HALF_M,
        pixel_m=PLANE_PIXEL_M,
    )
    a, b, c = (source.parameters[key] for key in ("a_m", "b", "c"))
    x, y = WINDOW_GRID.mesh_m()
    plane = a + b * x + c * y
    deviation = plane_deviation_bound(body, b, c, plane)
    texture_bound = least_squares_factor(WINDOW_GRID) * deviation
    assert texture_bound > 0.0

    window = sample_window(source, 0.0, 0.0)
    assert np.all(window.valid)
    assert window.valid_fraction == 1.0
    assert np.all(np.abs(window.elevation_m - plane) <= deviation)
    assert np.all(np.abs(window.residual_m) <= texture_bound)
    assert np.array_equal(window.channels[0], np.arcsinh(window.residual_m / ELEVATION_SCALE_M))

    # Texture nu of the plane-removed window is bounded by twice the residual bound, and agrees
    # with what terraluna_pipeline.terrain measures on the same window (its exact plane fit).
    ours = structure_function(
        window.residual_m,
        valid=window.valid,
        spacing_m=PIXEL_M,
        lags_m=LAGS_78,
        isotropic_rule="pooled_pairs",
    )
    theirs = terrain_fingerprint(
        window.elevation_m,
        valid=window.valid,
        grid=WINDOW_GRID,
        config=ENCODER_GRID_TERRAIN,
        isotropic_rule="pooled_pairs",
        coarsening_factor=1.0,
    ).variants["plane"]
    assert theirs.surface_m is not None
    assert np.all(np.abs(theirs.surface_m) <= texture_bound)
    for item, nu in zip(ours.isotropic, theirs.nu_m, strict=True):
        assert item.nu_m is not None and nu is not None
        assert item.nu_m <= 2 * texture_bound
        assert abs(item.nu_m - nu) <= 2 * texture_bound
        assert (item.nu_m == 0.0) == (nu == 0.0)
    assert (theirs.hurst.reason == "nu_zero") == any(item.nu_m == 0.0 for item in ours.isotropic)


@pytest.mark.parametrize("body", BODIES)
def test_the_slope_channel_of_a_plane_is_its_tilt_within_the_derived_bound(body: str) -> None:
    source = plane_dem(
        seed=5,
        body=body,  # type: ignore[arg-type]
        center_lat_deg=0.0,
        center_lon_deg=0.0,
        half_width_m=PLANE_HALF_M,
        pixel_m=PLANE_PIXEL_M,
    )
    a, b, c = (source.parameters[key] for key in ("a_m", "b", "c"))
    x, y = WINDOW_GRID.mesh_m()
    deviation = plane_deviation_bound(body, b, c, a + b * x + c * y)
    # A one-sided difference over one pixel errs by at most 2 deviation / spacing per axis (a
    # central one by half that); the slope angle is 1-Lipschitz in the gradient's length.
    gradient_error = math.sqrt(2.0) * 2.0 * deviation / PIXEL_M
    expected = math.degrees(math.atan(math.hypot(b, c))) / SLOPE_SCALE_DEG
    allowed = math.degrees(gradient_error) / SLOPE_SCALE_DEG + ATOL + RTOL * expected
    window = sample_window(source, 0.0, 0.0)
    assert np.all(np.abs(window.channels[1] - expected) <= allowed)
    assert expected > 0.0


# ---------------------------------------------------------------------------------------------
# Plane removal agrees with T3's texture variant on a rough surface


@pytest.mark.parametrize("body", BODIES)
def test_plane_removal_matches_the_terrain_texture_variant(body: str) -> None:
    source = fbm_source(seed=13, body=body)
    window = sample_window(source, -20.0, 135.0)
    texture = terrain_fingerprint(
        window.elevation_m,
        valid=window.valid,
        grid=WINDOW_GRID,
        config=ENCODER_GRID_TERRAIN,
        isotropic_rule="pooled_pairs",
        coarsening_factor=1.0,
    ).variants["plane"]
    assert texture.surface_m is not None and texture.plane is not None
    # Plane removal rounds in proportion to the elevations it subtracts.
    scale = float(np.max(np.abs(window.elevation_m[window.valid])))
    allowed = ATOL + RTOL * scale
    assert np.all(np.abs(window.residual_m - texture.surface_m)[window.valid] <= allowed)
    assert np.array_equal(np.isnan(window.residual_m), ~window.valid)
    assert window.plane is not None
    assert abs(window.plane.slope_east - texture.plane.slope_east) <= allowed / float(
        WINDOW_GRID.x_m[-1]
    )
    assert abs(window.plane.slope_north - texture.plane.slope_north) <= allowed / float(
        WINDOW_GRID.x_m[-1]
    )
    ours = structure_function(
        window.residual_m,
        valid=window.valid,
        spacing_m=PIXEL_M,
        lags_m=LAGS_78,
        isotropic_rule="pooled_pairs",
    )
    for item, nu in zip(ours.isotropic, texture.nu_m, strict=True):
        assert item.nu_m is not None and nu is not None
        assert abs(item.nu_m - nu) <= 2 * allowed
    assert texture.hurst.reason is None


# ---------------------------------------------------------------------------------------------
# Nodata, the invalid fill and the valid fraction


def touches_nodata(source: SyntheticDem, lat0_deg: float, lon0_deg: float) -> npt.NDArray[np.bool_]:
    """Window pixels whose four source neighbours include a nodata pixel, computed here."""
    x, y = WINDOW_GRID.mesh_m()
    lat, lon = aeqd_inverse(
        x,
        y,
        lat0_rad=math.radians(lat0_deg),
        lon0_rad=math.radians(lon0_deg),
        radius_m=BODY_RADII_M[source.body],
    )
    column, row = source.lookup.to_pixel(lat, lon)
    rows, cols = source.raster.shape
    i = np.minimum(np.floor(column), cols - 2).astype(np.intp)
    j = np.minimum(np.floor(row), rows - 2).astype(np.intp)
    missing = source.raster == NODATA_SENTINEL
    hit: npt.NDArray[np.bool_] = (
        missing[j, i] | missing[j, i + 1] | missing[j + 1, i] | missing[j + 1, i + 1]
    )
    return hit


def test_the_valid_channel_is_zero_wherever_the_source_has_nodata() -> None:
    source = hole_source()
    window = sample_window(source, 10.0, -60.0)
    hit = touches_nodata(source, 10.0, -60.0)
    assert 0 < np.count_nonzero(hit) < hit.size
    assert np.all(window.channels[2][hit] == 0.0)
    assert np.all(window.channels[0][hit] == INVALID_FILL)
    assert np.all(window.channels[1][hit] == INVALID_FILL)
    # A disc-shaped hole inside the source leaves every other pixel valid.
    assert np.array_equal(window.valid, ~hit)
    assert np.array_equal(window.channels[2], window.valid.astype(np.float64))
    assert np.all(np.isnan(window.residual_m[hit]))
    assert np.all(np.isfinite(window.residual_m[~hit]))


def test_the_valid_fraction_is_recorded_and_never_filters() -> None:
    small = sample_window(hole_source(), 10.0, -60.0)
    assert small.valid_fraction == small.valid_cells / 4096
    assert 0.8 < small.valid_fraction < 1.0
    big = sample_window(hole_source(hole_radius_m=2000.0), 10.0, -60.0)
    assert big.valid_fraction == big.valid_cells / 4096
    assert 0.0 < big.valid_fraction < 0.8
    assert big.plane is not None
    assert np.any(big.channels[2] == 1.0)


def test_a_window_outside_the_source_is_all_invalid_and_holds_no_nan() -> None:
    window = sample_window(fbm_source(), 30.0, 135.0)
    assert window.valid_fraction == 0.0
    assert window.valid_cells == 0
    assert window.plane is None
    assert np.all(window.storage() == 0.0)
    assert not np.any(np.isnan(window.storage()))


def test_a_window_partly_outside_the_source_masks_the_outside() -> None:
    source = fbm_source()
    # 2 km east of the source's east edge minus half a window: about 40% outside.
    shift_deg = math.degrees(2000.0 / (BODY_RADII_M["mars"] * math.cos(math.radians(-20.0))))
    window = sample_window(source, -20.0, 135.0 + shift_deg)
    assert 0.0 < window.valid_fraction < 1.0
    assert np.all(window.valid[:, :8])
    assert not np.any(window.valid[:, -8:])
    assert not np.any(np.isnan(window.storage()))


def all_windows() -> list[EncoderWindow]:
    return [
        sample_window(
            plane_dem(
                seed=1,
                body="earth",
                center_lat_deg=0.0,
                center_lon_deg=0.0,
                half_width_m=PLANE_HALF_M,
                pixel_m=PLANE_PIXEL_M,
            ),
            0.0,
            0.0,
        ),
        sample_window(fbm_source(), -20.0, 135.0),
        sample_window(hole_source(), 10.0, -60.0),
        sample_window(hole_source(hole_radius_m=2000.0), 10.0, -60.0),
        sample_window(polar_source(), -90.0, 0.0),
        sample_window(polar_source(), -89.9, 30.0),
        sample_window(fbm_source(), 30.0, 135.0),
    ]


def test_no_nan_is_stored_in_any_window() -> None:
    for window in all_windows():
        storage = window.storage()
        assert storage.dtype == np.float16
        assert not np.any(np.isnan(storage))
        assert np.all(np.isfinite(storage))
        invalid = storage[2] == 0.0
        assert np.all(storage[0][invalid] == 0.0)
        assert np.all(storage[1][invalid] == 0.0)
        assert set(np.unique(storage[2]).tolist()) <= {0.0, 1.0}


def test_fill_pixel_source_values_do_not_change_any_output() -> None:
    source = hole_source()
    reference = sample_window(source, 10.0, -60.0)
    missing = source.raster == NODATA_SENTINEL
    variants = [
        RasterSource(
            body=source.body,
            raster=np.where(missing, np.nan, source.raster),
            nodata=NODATA_SENTINEL,
            lookup=source.lookup,
        ),
        RasterSource(
            body=source.body,
            raster=np.where(missing, -32768.0, source.raster),
            nodata=-32768.0,
            lookup=source.lookup,
        ),
        RasterSource(
            body=source.body,
            raster=np.where(missing, np.inf, source.raster),
            nodata=None,
            lookup=source.lookup,
        ),
    ]
    for variant in variants:
        window = sample_window(variant, 10.0, -60.0)
        assert window.channels.tobytes() == reference.channels.tobytes()
        assert window.storage().tobytes() == reference.storage().tobytes()
        assert window.valid_fraction == reference.valid_fraction
        assert window.plane == reference.plane


@pytest.mark.parametrize("fill", [np.nan, 0.0, 1e300, -1e300, np.inf, 12345.0])
def test_values_at_invalid_pixels_never_enter_the_encoding(fill: float) -> None:
    window = sample_window(hole_source(), 10.0, -60.0)
    values = np.where(window.valid, window.elevation_m, fill)
    encoded = encode_channels(values, window.valid)
    assert encoded.channels.tobytes() == window.channels.tobytes()
    assert encoded.plane == window.plane
    assert np.array_equal(encoded.valid, window.valid)


def test_a_pixel_needs_valid_neighbours_along_both_axes_for_a_slope() -> None:
    # Slope uses only valid pixels: a valid pixel without a valid neighbour along an axis has no
    # slope, so it is dropped from the valid mask, and the rule is applied until it holds.
    z = np.arange(64.0 * 64.0).reshape(64, 64) * 0.01
    valid = np.zeros((64, 64), dtype=np.bool_)
    valid[10:14, 10:14] = True  # a 4 x 4 block: kept
    valid[30, 5:40] = True  # a one-pixel line along x: no y neighbour, dropped
    valid[50, 50] = True  # an isolated pixel: dropped
    valid[40:42, 40] = True  # a 2 x 1 strip along y: no x neighbour, dropped
    encoded = encode_channels(z, valid)
    expected = np.zeros((64, 64), dtype=np.bool_)
    expected[10:14, 10:14] = True
    assert np.array_equal(encoded.valid, expected)
    assert encoded.plane is not None
    assert np.all(encoded.channels[:, ~expected] == 0.0)


def test_the_slope_rule_drops_pixels_left_without_neighbours_in_turn() -> None:
    z = np.zeros((64, 64))
    valid = np.zeros((64, 64), dtype=np.bool_)
    valid[20, 20:23] = True
    valid[21, 20] = True  # an L: (20, 21) and (20, 22) lack a y neighbour; then (20, 20) lacks x
    encoded = encode_channels(z, valid)
    assert not np.any(encoded.valid)
    assert encoded.plane is None
    assert np.all(encoded.channels == 0.0)


def test_encode_channels_rejects_a_valid_pixel_that_is_not_finite() -> None:
    z = np.zeros((64, 64))
    z[3, 3] = np.nan
    with pytest.raises(DatasetError):
        encode_channels(z, np.ones((64, 64), dtype=np.bool_))


def checkerboard(high: float, low: float) -> Array:
    rows, cols = np.indices((64, 64))
    board: Array = np.where((rows + cols) % 2 == 0, high, low)
    return board


@pytest.mark.parametrize(
    "values",
    [
        np.full((64, 64), 1e308),  # the sum behind the mean overflows: NaN residuals
        np.full((64, 64), -1e308),
        checkerboard(1e308, -1e308),  # the mean is finite; sums and differences overflow
        checkerboard(1.7e308, 0.0),
    ],
    ids=["all_high", "all_low", "alternating_sign", "high_and_zero"],
)
def test_finite_elevations_beyond_float64_arithmetic_are_rejected_not_stored(values: Array) -> None:
    # Every valid pixel is finite, so the input check passes; plane removal or the slope then
    # leaves float64. Such a window is refused, so no NaN (or infinite slope) can reach a shard.
    assert np.all(np.isfinite(values))
    with np.errstate(all="ignore"), pytest.raises(DatasetError, match="float64"):
        encode_channels(values, np.ones((64, 64), dtype=np.bool_))


def test_storage_refuses_a_window_whose_channels_are_not_finite() -> None:
    window = sample_window(fbm_source(), -20.0, 135.0)
    for bad in (np.nan, np.inf):
        channels = np.array(window.channels, copy=True)
        channels[0, 5, 5] = bad
        broken = dataclasses.replace(window, channels=channels)
        with pytest.raises(DatasetError, match="finite"):
            broken.storage()


def test_large_but_representable_elevations_still_encode() -> None:
    # 1e300 m is absurd as terrain but leaves every sum, product and difference in range.
    z = 1e300 + np.add.outer(np.arange(64.0), np.arange(64.0))
    encoded = encode_channels(z, np.ones((64, 64), dtype=np.bool_))
    assert np.all(np.isfinite(encoded.channels))
    assert encoded.plane is not None


@pytest.mark.parametrize(("lat", "lon"), [(91.0, 0.0), (math.nan, 0.0), (0.0, math.inf)])
def test_a_window_centre_must_be_a_finite_latitude_and_longitude(lat: float, lon: float) -> None:
    with pytest.raises(DatasetError):
        sample_window(fbm_source(), lat, lon)


# ---------------------------------------------------------------------------------------------
# Determinism


def test_the_same_seed_gives_byte_identical_windows() -> None:
    first = [window.storage().tobytes() for window in all_windows()]
    second = [window.storage().tobytes() for window in all_windows()]
    assert first == second
    again = sample_window(fbm_source(seed=4), -20.0, 135.0)
    other = sample_window(fbm_source(seed=5), -20.0, 135.0)
    assert again.channels.tobytes() == all_windows()[1].channels.tobytes()
    assert other.channels.tobytes() != again.channels.tobytes()


# ---------------------------------------------------------------------------------------------
# Rotation about the pole


def assert_windows_close(actual: EncoderWindow, expected_channels: Array) -> None:
    assert np.array_equal(actual.channels[2], expected_channels[2])
    assert_close(actual.channels[0], expected_channels[0])
    assert_close(actual.channels[1], expected_channels[1])


@pytest.mark.parametrize("k", [1, 2, 3])
def test_a_window_rotated_by_90_degrees_about_the_pole_sees_the_same_terrain(k: int) -> None:
    # The source is 4-fold symmetric about the pole, so the window centred 90 k degrees of
    # longitude further on is the original window rotated with the surface: each window is
    # north-up in its own frame, so the two hold the same values to float error.
    source = polar_source()
    first = sample_window(source, -89.9, 30.0)
    assert first.valid_fraction == 1.0
    turned = sample_window(source, -89.9, 30.0 + 90.0 * k)
    assert_windows_close(turned, first.channels)
    assert float(np.std(first.channels[0])) > 0.0


def test_at_the_pole_a_quarter_turn_of_longitude_rotates_the_window_array() -> None:
    # Centred on the pole, longitude lam0 + 90 degrees maps window pixel (x, y) to the point
    # that lam0 maps (y, -x) to (PRD 7 AEQD with sin phi0 = -1: lam = lam0 + atan2(x, y)).
    # In [row, column] that is window[r, c] -> window[c, n - 1 - r], numpy's rot90 with k = 1.
    # A tilt added here breaks the 4-fold symmetry, so the rotation itself is tested.
    base = polar_source()
    columns, rows = np.meshgrid(
        np.arange(base.raster.shape[1], dtype=np.float64),
        np.arange(base.raster.shape[0], dtype=np.float64),
        indexing="xy",
    )
    east, north = base.lookup.projected_from_pixel(columns, rows)
    tilted = RasterSource(
        body=base.body,
        raster=base.raster + 0.01 * east - 0.004 * north,
        nodata=base.nodata,
        lookup=base.lookup,
    )
    first = sample_window(tilted, -90.0, 0.0)
    turned = sample_window(tilted, -90.0, 90.0)
    assert first.valid_fraction == 1.0
    assert not np.allclose(turned.channels[0], first.channels[0])
    assert_windows_close(turned, np.rot90(first.channels, 1, axes=(1, 2)))
    # On the symmetric surface itself the two quarter turns agree as well.
    plain = sample_window(base, -90.0, 0.0)
    assert_windows_close(sample_window(base, -90.0, 90.0), np.rot90(plain.channels, 1, axes=(1, 2)))


# ---------------------------------------------------------------------------------------------
# The resampler is imported, never duplicated (by path; later modules are not covered here)

CHECKED_FILES = ("dataset.py", "splits.py", "synthetic.py")
FORBIDDEN_DEFINITION = re.compile(r"aeqd|azimuthal|bilinear|interp|resampl", re.IGNORECASE)
FORBIDDEN_MODULES = ("scipy.ndimage", "scipy.interpolate", "rasterio.warp", "pyproj", "cv2")
RESAMPLER_NAMES = frozenset(
    {"resample_window", "bilinear_resample", "aeqd_inverse", "aeqd_forward"}
)


def module_tree(name: str) -> ast.Module | None:
    path = ML_ROOT / "src" / "terraluna_ml" / name
    if not path.exists():
        return None
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_dataset_and_synthetic_exist_for_the_by_path_check() -> None:
    # splits.py arrives with T11-04; it is checked by path as soon as it exists.
    assert module_tree("dataset.py") is not None
    assert module_tree("synthetic.py") is not None


@pytest.mark.parametrize("name", CHECKED_FILES)
def test_no_module_defines_an_aeqd_or_bilinear_function(name: str) -> None:
    tree = module_tree(name)
    if tree is None:
        assert name == "splits.py"
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            assert not FORBIDDEN_DEFINITION.search(node.name), f"{name} defines {node.name}"
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Lambda):
                    assert not FORBIDDEN_DEFINITION.search(target.id)
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(FORBIDDEN_MODULES), f"{name}: {alias.name}"
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert not node.module.startswith(FORBIDDEN_MODULES), f"{name}: {node.module}"
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("interp", "map_coordinates", "griddata", "zoom")


@pytest.mark.parametrize("name", CHECKED_FILES)
def test_resampler_names_come_only_from_terraluna_pipeline_geometry(name: str) -> None:
    tree = module_tree(name)
    if tree is None:
        assert name == "splits.py"
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in RESAMPLER_NAMES:
                    assert node.module == "terraluna_pipeline.geometry", f"{name}: {node.module}"
                    assert alias.asname is None
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            assert node.name not in RESAMPLER_NAMES


def test_dataset_imports_the_resampler_from_terraluna_pipeline_geometry() -> None:
    tree = module_tree("dataset.py")
    assert tree is not None
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "terraluna_pipeline.geometry"
        for alias in node.names
    }
    assert {"resample_window", "window_grid", "body_radius_m"} <= imported
