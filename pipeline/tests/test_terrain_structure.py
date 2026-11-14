"""Tests for the terrain structure function nu(L) (PRD 6.3; PRD 7 Validity rules, Tolerances).

nu(L) = sqrt(mean((z(x + L) - z(x))^2)) over every pair of valid cells one lag apart. It is
measured separately along both grid axes and both diagonals, and an isotropic summary is kept
beside the per-direction values (PRD 6.3).

Conventions used throughout:

- Grids are indexed ``[row, column]`` like the D29 window grid: columns run east and rows run
  south, with row 0 at the top. The window y axis (north) therefore points to decreasing rows.
- Directions are unit vectors in the window frame (x east, y north): ``x`` = (1, 0),
  ``y`` = (0, 1), ``diagonal_ne`` = (1, 1) / sqrt 2 and ``diagonal_nw`` = (-1, 1) / sqrt 2.
  A pair is unordered, so the opposite vector gives the same pairs.
- Lags are metres. A lag maps to a whole-cell offset along a direction when the offset's length
  in metres equals the lag to PRD 7's arithmetic tolerance, |length - L| <= 1e-12 + 1e-10 * L.
  On the 90 m grid no diagonal offset (k, k) has the length of a configured lag (270 m would
  need k = 2.12), so the diagonals are unsupported there with ``diagonal_lag_not_on_grid``.
  Lags k * hypot(90, 90) exercise the diagonals.
- Comparisons use PRD 7 Tolerances: rtol 1e-10, atol 1e-12, in metres. Missing values are None,
  never 0; a true 0 (a constant profile) is a supported 0.0.

The references are written independently of the implementation: analytic nu for planes and
sinusoids (the sinusoid with a closed-form finite-window sum and a derived bound), and an
explicit Python loop over every cell pair for masked data.
"""

from __future__ import annotations

import ast
import dataclasses
import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra import numpy as hnp

from terraluna_pipeline import terrain
from terraluna_pipeline.config import load_terrain_config
from terraluna_pipeline.geometry import window_grid
from terraluna_pipeline.terrain import (
    DIRECTION_STEPS,
    DIRECTIONS,
    ISOTROPIC_RULES,
    LAG_ATOL,
    LAG_RTOL,
    DirectionalNu,
    IsotropicNu,
    StructureFunction,
    TerrainError,
    structure_function,
)

pytestmark = pytest.mark.filterwarnings("error")

Array = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]

RTOL = 1e-10
ATOL = 1e-12

# Window-frame steps (east cells, north cells), written here independently of the module.
STEPS: dict[str, tuple[int, int]] = {
    "x": (1, 0),
    "y": (0, 1),
    "diagonal_ne": (1, 1),
    "diagonal_nw": (-1, 1),
}
AXES = ("x", "y")
DIAGONALS = ("diagonal_ne", "diagonal_nw")
UNIT: dict[str, tuple[float, float]] = {
    "x": (1.0, 0.0),
    "y": (0.0, 1.0),
    "diagonal_ne": (1.0 / math.sqrt(2.0), 1.0 / math.sqrt(2.0)),
    "diagonal_nw": (-1.0 / math.sqrt(2.0), 1.0 / math.sqrt(2.0)),
}

CONFIG = load_terrain_config()
SPACING = CONFIG.analysis_grid_m
LAGS = CONFIG.lags_m
WINDOW = CONFIG.window_m
DIAGONAL_CELLS = (3, 4, 6, 8, 12)
DIAGONAL_LAGS = tuple(math.hypot(k * SPACING, k * SPACING) for k in DIAGONAL_CELLS)


def close(actual: float, expected: float, *, rtol: float = RTOL, atol: float = ATOL) -> bool:
    return abs(actual - expected) <= atol + rtol * abs(expected)


def analysis_mesh() -> tuple[Array, Array]:
    """The D29 analysis grid (5 km at 90 m, 55 x 55) as x (east) and y (north) metres."""
    return window_grid(WINDOW, SPACING).mesh_m()


def all_valid(shape: tuple[int, ...]) -> BoolArray:
    return np.ones(shape, dtype=np.bool_)


def entry(result: StructureFunction, direction: str, lag_m: float) -> DirectionalNu:
    index = result.lags_m.index(lag_m)
    item = result.directional[direction][index]  # type: ignore[index]
    assert item.direction == direction
    assert item.lag_m == lag_m
    return item


def iso(result: StructureFunction, lag_m: float) -> IsotropicNu:
    item = result.isotropic[result.lags_m.index(lag_m)]
    assert item.lag_m == lag_m
    return item


def brute_differences(
    values: Array, valid: BoolArray, east: int, north: int
) -> tuple[list[float], int]:
    """Every difference z(b) - z(a) with b = a + (east, north) cells, both cells valid.

    Also returns how many pairs fit in the grid at all, valid or not.
    """
    rows, cols = values.shape
    differences: list[float] = []
    in_grid = 0
    for r in range(rows):
        for c in range(cols):
            r2, c2 = r - north, c + east
            if 0 <= r2 < rows and 0 <= c2 < cols:
                in_grid += 1
                if valid[r, c] and valid[r2, c2]:
                    differences.append(float(values[r2, c2]) - float(values[r, c]))
    return differences, in_grid


def brute_nu(differences: Sequence[float]) -> float | None:
    if not differences:
        return None
    return math.sqrt(math.fsum(d * d for d in differences) / len(differences))


def run(
    values: npt.ArrayLike,
    valid: npt.ArrayLike,
    lags_m: Sequence[float],
    *,
    spacing_m: float = SPACING,
    isotropic_rule: str = "direction_mean",
) -> StructureFunction:
    return structure_function(
        values,
        valid=valid,
        spacing_m=spacing_m,
        lags_m=lags_m,
        isotropic_rule=isotropic_rule,  # type: ignore[arg-type]
    )


# --- Configuration and conventions -----------------------------------------------------------


def test_config_supplies_the_story_grid_and_lags() -> None:
    assert SPACING == 90.0
    assert LAGS == (270.0, 360.0, 540.0, 720.0, 1080.0)
    assert WINDOW == 5000.0
    assert window_grid(WINDOW, SPACING).size == 55


def test_directions_are_both_axes_and_both_diagonals_in_the_window_frame() -> None:
    assert DIRECTIONS == ("x", "y", "diagonal_ne", "diagonal_nw")
    assert dict(DIRECTION_STEPS) == STEPS
    assert set(ISOTROPIC_RULES) == {"direction_mean", "pooled_pairs"}
    assert (LAG_RTOL, LAG_ATOL) == (RTOL, ATOL)


# --- Planes ----------------------------------------------------------------------------------

PLANES = (
    (100.0, 0.03, -0.07),
    (-2500.0, -0.2, 0.11),
    (100.0, 0.05, 0.05),
    (100.0, 0.08, 0.0),
    (100.0, 0.0, -0.04),
)


@pytest.mark.parametrize(("a", "b", "c"), PLANES)
def test_plane_axis_nu_is_lag_times_gradient_along_the_axis(a: float, b: float, c: float) -> None:
    x, y = analysis_mesh()
    z = a + b * x + c * y
    result = run(z, all_valid(z.shape), LAGS)
    for lag in LAGS:
        k = round(lag / SPACING)
        for direction in AXES:
            ux, uy = UNIT[direction]
            expected = abs(lag) * abs(b * ux + c * uy)
            item = entry(result, direction, lag)
            assert item.reason is None
            assert item.nu_m is not None
            assert close(item.nu_m, expected), (direction, lag, item.nu_m, expected)
            east, north = STEPS[direction]
            assert item.offset_cells == (k * east, k * north)
            assert item.offset_m == k * SPACING
            assert item.pair_count == 55 * (55 - k)


@pytest.mark.parametrize(("a", "b", "c"), PLANES)
def test_plane_diagonal_nu_is_lag_times_gradient_along_the_diagonal(
    a: float, b: float, c: float
) -> None:
    x, y = analysis_mesh()
    z = a + b * x + c * y
    result = run(z, all_valid(z.shape), DIAGONAL_LAGS)
    for k, lag in zip(DIAGONAL_CELLS, DIAGONAL_LAGS, strict=True):
        for direction in DIAGONALS:
            ux, uy = UNIT[direction]
            expected = abs(lag) * abs(b * ux + c * uy)
            item = entry(result, direction, lag)
            assert item.reason is None
            assert item.nu_m is not None
            assert close(item.nu_m, expected), (direction, lag, item.nu_m, expected)
            east, north = STEPS[direction]
            assert item.offset_cells == (k * east, k * north)
            assert item.offset_m == math.hypot(k * SPACING, k * SPACING)
            assert item.pair_count == (55 - k) ** 2
        for direction in AXES:
            item = entry(result, direction, lag)
            assert item.nu_m is None
            assert item.reason == "axis_lag_not_on_grid"


def test_plane_diagonals_tell_northeast_from_northwest() -> None:
    # b != c, both nonzero: the two diagonals differ, so a swapped row sign would show.
    x, y = analysis_mesh()
    b, c = 0.03, -0.07
    z = 100.0 + b * x + c * y
    result = run(z, all_valid(z.shape), DIAGONAL_LAGS[:1])
    lag = DIAGONAL_LAGS[0]
    ne = entry(result, "diagonal_ne", lag).nu_m
    nw = entry(result, "diagonal_nw", lag).nu_m
    assert ne is not None and nw is not None
    assert close(ne, lag * abs(b + c) / math.sqrt(2.0))
    assert close(nw, lag * abs(c - b) / math.sqrt(2.0))
    assert abs(ne - nw) > 1.0


# --- Sinusoid --------------------------------------------------------------------------------
#
# For z = A sin(2 pi x / lam) and an x lag L = k * D on a grid with spacing D, each pair gives
#   d_m = 2 A sin(pi L / lam) cos(pi (2 x_m + L) / lam),
#   d_m^2 = 2 A^2 sin^2(pi L / lam) (1 + cos(alpha + m phi)),
# with x_m = x_0 + m D, alpha = 2 pi (2 x_0 + L) / lam and phi = 4 pi D / lam. Every row is the
# same, so the mean over all pairs is the mean over the M = n - k pairs of one row:
#   nu^2 = 2 A^2 sin^2(pi L / lam) (1 + C),   C = (1 / M) sum_{m < M} cos(alpha + m phi)
#        = sin(M phi / 2) cos(alpha + (M - 1) phi / 2) / (M sin(phi / 2)).
# The large-sample limit is nu_inf = sqrt(2) A |sin(pi L / lam)|. From the closed form,
# |C| <= 1 / (M |sin(2 pi D / lam)|), and |sqrt(1 + C) - 1| <= |C|, so the finite window can
# differ from the limit by at most nu_inf / (M |sin(2 pi D / lam)|). That bound is the test's
# window tolerance; PRD 7's arithmetic tolerance is added on top. It is derived, not tuned.

SINUSOIDS = ((12.5, 1000.0), (3.0, 730.0))


def sinusoid_limit(amplitude: float, wavelength: float, lag: float) -> float:
    return math.sqrt(2.0) * amplitude * abs(math.sin(math.pi * lag / wavelength))


def sinusoid_window_bound(pairs_per_row: int, wavelength: float) -> float:
    return 1.0 / (pairs_per_row * abs(math.sin(2.0 * math.pi * SPACING / wavelength)))


def sinusoid_finite(amplitude: float, wavelength: float, lag: float, x0: float, m: int) -> float:
    alpha = 2.0 * math.pi * (2.0 * x0 + lag) / wavelength
    phi = 4.0 * math.pi * SPACING / wavelength
    c = math.sin(m * phi / 2.0) * math.cos(alpha + (m - 1) * phi / 2.0) / (m * math.sin(phi / 2))
    return math.sqrt(2.0 * amplitude**2 * math.sin(math.pi * lag / wavelength) ** 2 * (1.0 + c))


@pytest.mark.parametrize(("amplitude", "wavelength"), SINUSOIDS)
def test_sinusoid_x_nu_matches_the_large_sample_limit_within_the_window_bound(
    amplitude: float, wavelength: float
) -> None:
    x, _ = analysis_mesh()
    z = amplitude * np.sin(2.0 * np.pi * x / wavelength)
    result = run(z, all_valid(z.shape), LAGS)
    for lag in LAGS:
        pairs_per_row = 55 - round(lag / SPACING)
        limit = sinusoid_limit(amplitude, wavelength, lag)
        bound = sinusoid_window_bound(pairs_per_row, wavelength)
        nu = entry(result, "x", lag).nu_m
        assert nu is not None
        assert abs(nu - limit) <= limit * bound + ATOL + RTOL * limit, (lag, nu, limit, bound)


@pytest.mark.parametrize(("amplitude", "wavelength"), SINUSOIDS)
def test_sinusoid_x_nu_matches_the_closed_form_finite_window_sum(
    amplitude: float, wavelength: float
) -> None:
    grid = window_grid(WINDOW, SPACING)
    x, _ = grid.mesh_m()
    z = amplitude * np.sin(2.0 * np.pi * x / wavelength)
    result = run(z, all_valid(z.shape), LAGS)
    for lag in LAGS:
        pairs_per_row = 55 - round(lag / SPACING)
        expected = sinusoid_finite(amplitude, wavelength, lag, float(grid.x_m[0]), pairs_per_row)
        nu = entry(result, "x", lag).nu_m
        assert nu is not None
        assert close(nu, expected), (lag, nu, expected)


@pytest.mark.parametrize(("amplitude", "wavelength"), SINUSOIDS)
def test_sinusoid_y_nu_is_a_supported_zero(amplitude: float, wavelength: float) -> None:
    x, _ = analysis_mesh()
    z = amplitude * np.sin(2.0 * np.pi * x / wavelength)
    result = run(z, all_valid(z.shape), LAGS)
    for lag in LAGS:
        item = entry(result, "y", lag)
        assert item.reason is None
        assert item.nu_m == 0.0
        assert item.pair_count == 55 * (55 - round(lag / SPACING))


def test_sinusoid_converges_to_the_large_sample_limit_as_the_window_grows() -> None:
    amplitude, wavelength = SINUSOIDS[0]
    deviations: list[float] = []
    for columns in (55, 550, 5500, 55000):
        grid = window_grid(columns * SPACING, SPACING)
        z = amplitude * np.sin(2.0 * np.pi * grid.x_m / wavelength)[np.newaxis, :]
        result = run(z, all_valid(z.shape), LAGS[:1])
        lag = LAGS[0]
        limit = sinusoid_limit(amplitude, wavelength, lag)
        bound = sinusoid_window_bound(columns - round(lag / SPACING), wavelength)
        nu = entry(result, "x", lag).nu_m
        assert nu is not None
        assert abs(nu - limit) <= limit * bound + ATOL + RTOL * limit
        deviations.append(abs(nu - limit) / limit)
        # A single row has no pairs along y: that direction is unsupported, never 0.
        assert entry(result, "y", lag).reason == "lag_exceeds_grid"
        assert entry(result, "y", lag).nu_m is None
    assert deviations[-1] <= sinusoid_window_bound(55000 - 3, wavelength) < 1e-4


# --- Diagonal lag rule -----------------------------------------------------------------------


@pytest.mark.parametrize("lag", [270.0, 360.0])
def test_configured_lags_are_not_on_the_diagonal_grid_at_90_m(lag: float) -> None:
    x, y = analysis_mesh()
    z = 100.0 + 0.03 * x - 0.07 * y
    result = run(z, all_valid(z.shape), [lag])
    for direction in DIAGONALS:
        item = entry(result, direction, lag)
        assert item.reason == "diagonal_lag_not_on_grid"
        assert item.nu_m is None
        assert item.offset_cells is None
        assert item.offset_m is None
        assert item.pair_count == 0
    k = round(lag / SPACING)
    assert entry(result, "x", lag).offset_cells == (k, 0)
    assert entry(result, "y", lag).offset_cells == (0, k)


def test_lag_on_grid_tolerance_is_prd_7_arithmetic_tolerance() -> None:
    x, y = analysis_mesh()
    z = 100.0 + 0.03 * x - 0.07 * y
    diagonal = math.hypot(3 * SPACING, 3 * SPACING)
    axis = 3 * SPACING
    inside = 0.5e-10
    outside = 2e-10
    lags = sorted(
        [
            axis * (1 + inside),
            axis * (1 + outside),
            diagonal * (1 - inside),
            diagonal * (1 + outside),
        ]
    )
    result = run(z, all_valid(z.shape), lags)
    assert entry(result, "x", axis * (1 + inside)).offset_cells == (3, 0)
    assert entry(result, "x", axis * (1 + outside)).reason == "axis_lag_not_on_grid"
    assert entry(result, "diagonal_nw", diagonal * (1 - inside)).offset_cells == (-3, 3)
    assert entry(result, "diagonal_ne", diagonal * (1 + outside)).reason == (
        "diagonal_lag_not_on_grid"
    )


def test_lag_shorter_than_half_a_cell_is_not_on_the_grid() -> None:
    z = np.arange(16.0).reshape(4, 4)
    result = run(z, all_valid(z.shape), [0.4 * SPACING])
    for direction in AXES:
        assert entry(result, direction, 0.4 * SPACING).reason == "axis_lag_not_on_grid"
    for direction in DIAGONALS:
        assert entry(result, direction, 0.4 * SPACING).reason == "diagonal_lag_not_on_grid"


# --- Nodata ----------------------------------------------------------------------------------


def rough_field(shape: tuple[int, int] = (55, 55)) -> Array:
    rng = np.random.default_rng(20261114)
    rows, cols = np.indices(shape, dtype=np.float64)
    return 40.0 * rng.standard_normal(shape) + 0.3 * cols - 0.2 * rows + 1500.0


def hole_mask(shape: tuple[int, int] = (55, 55)) -> BoolArray:
    """An interior hole plus a short strip on the top edge (rows 18:29, columns 13:26 at 55)."""
    rows, cols = shape
    valid = all_valid(shape)
    valid[rows // 3 : rows // 3 + rows // 5, cols // 4 : cols // 4 + cols // 4] = False
    valid[0, cols - 5 :] = False
    assert 0 < np.count_nonzero(~valid) < valid.size
    return valid


ALL_LAGS = tuple(sorted(LAGS + DIAGONAL_LAGS))


def assert_matches_brute_force(result: StructureFunction, values: Array, valid: BoolArray) -> None:
    for lag in result.lags_m:
        for direction in DIRECTIONS:
            item = entry(result, direction, lag)
            if item.offset_cells is None:
                continue
            east, north = item.offset_cells
            differences, in_grid = brute_differences(values, valid, east, north)
            assert item.pair_count == len(differences)
            expected = brute_nu(differences)
            if expected is None:
                assert item.nu_m is None
                assert item.reason == ("lag_exceeds_grid" if in_grid == 0 else "no_valid_pairs")
            else:
                assert item.nu_m is not None
                assert item.reason is None
                assert close(item.nu_m, expected), (direction, lag, item.nu_m, expected)


def test_hole_gives_nu_of_the_data_with_the_hole_pairs_removed_by_hand() -> None:
    z = rough_field()
    valid = hole_mask()
    result = run(z, valid, ALL_LAGS)
    assert_matches_brute_force(result, z, valid)
    for lag in ALL_LAGS:
        for direction in DIRECTIONS:
            item = entry(result, direction, lag)
            if item.offset_cells is not None:
                assert item.reason is None
                assert item.pair_count > 0


@pytest.mark.parametrize("filler", [0.0, math.nan, math.inf, -math.inf, 1e30, -7.0])
def test_masked_cells_never_enter_whatever_they_hold(filler: float) -> None:
    z = rough_field()
    valid = hole_mask()
    reference = run(z, valid, ALL_LAGS)
    filled = np.where(valid, z, filler)
    assert run(filled, valid, ALL_LAGS) == reference


def test_masked_cells_are_not_zeros() -> None:
    # Treating the hole as valid zeros changes nu, so the test above can tell the two apart.
    z = rough_field()
    valid = hole_mask()
    zero_filled = np.where(valid, z, 0.0)
    masked = run(zero_filled, valid, LAGS)
    as_zeros = run(zero_filled, all_valid(z.shape), LAGS)
    for lag in LAGS:
        for direction in AXES:
            assert entry(masked, direction, lag).nu_m != entry(as_zeros, direction, lag).nu_m


def test_numpy_masked_array_mask_counts_as_nodata() -> None:
    z = rough_field()
    valid = hole_mask()
    explicit = run(z, valid, ALL_LAGS)
    garbage = np.where(valid, z, 0.0)
    masked = np.ma.masked_array(garbage, mask=~valid)
    assert run(masked, all_valid(z.shape), ALL_LAGS) == explicit
    # The two masks combine: a cell is used only if both say it is valid.
    both = hole_mask()
    both[:, 40:] = False
    assert run(masked, both, ALL_LAGS) == run(z, valid & both, ALL_LAGS)


def test_checkerboard_has_no_valid_pairs_at_odd_axis_offsets() -> None:
    z = rough_field()
    rows, cols = np.indices(z.shape)
    valid = (rows + cols) % 2 == 0
    result = run(z, valid, ALL_LAGS)
    assert_matches_brute_force(result, z, valid)
    for lag in LAGS:
        k = round(lag / SPACING)
        for direction in AXES:
            item = entry(result, direction, lag)
            if k % 2:
                assert item.reason == "no_valid_pairs"
                assert item.nu_m is None
                assert item.pair_count == 0
            else:
                assert item.reason is None
                assert item.nu_m is not None
    for lag in DIAGONAL_LAGS:
        for direction in DIAGONALS:
            assert entry(result, direction, lag).reason is None


def test_single_valid_column_leaves_x_unsupported_not_zero() -> None:
    z = rough_field()
    valid = np.zeros(z.shape, dtype=np.bool_)
    valid[:, 17] = True
    result = run(z, valid, ALL_LAGS)
    assert_matches_brute_force(result, z, valid)
    for lag in LAGS:
        assert entry(result, "x", lag).reason == "no_valid_pairs"
        assert entry(result, "x", lag).nu_m is None
        assert entry(result, "y", lag).nu_m is not None
        summary = iso(result, lag)
        assert summary.directions == ("y",)
        assert summary.nu_m == entry(result, "y", lag).nu_m
    for lag in DIAGONAL_LAGS:
        for direction in DIAGONALS:
            assert entry(result, direction, lag).reason == "no_valid_pairs"


@pytest.mark.parametrize("rule", ["direction_mean", "pooled_pairs"])
def test_all_nodata_window_is_unsupported_everywhere(rule: str) -> None:
    z = np.full((55, 55), np.nan)
    valid = np.zeros(z.shape, dtype=np.bool_)
    result = run(z, valid, ALL_LAGS, isotropic_rule=rule)
    for lag in ALL_LAGS:
        for direction in DIRECTIONS:
            item = entry(result, direction, lag)
            assert item.nu_m is None
            assert item.pair_count == 0
            assert item.reason in {
                "no_valid_pairs",
                "axis_lag_not_on_grid",
                "diagonal_lag_not_on_grid",
            }
        summary = iso(result, lag)
        assert summary.nu_m is None
        assert summary.reason == "no_supported_direction"
        assert summary.directions == ()
        assert summary.pair_count == 0


def test_lag_beyond_the_grid_is_unsupported() -> None:
    z = rough_field((10, 10))
    valid = all_valid(z.shape)
    inside, beyond = 9 * SPACING, 10 * SPACING
    d_inside, d_beyond = math.hypot(inside, inside), math.hypot(beyond, beyond)
    result = run(z, valid, [inside, beyond, d_inside, d_beyond])
    assert_matches_brute_force(result, z, valid)
    for direction in AXES:
        assert entry(result, direction, inside).pair_count == 10
        assert entry(result, direction, beyond).reason == "lag_exceeds_grid"
        assert entry(result, direction, beyond).nu_m is None
    for direction in DIAGONALS:
        assert entry(result, direction, d_inside).pair_count == 1
        assert entry(result, direction, d_beyond).reason == "lag_exceeds_grid"


def test_off_grid_reason_takes_precedence_over_missing_pairs() -> None:
    z = np.full((4, 4), np.nan)
    valid = np.zeros(z.shape, dtype=np.bool_)
    result = run(z, valid, [1.5 * SPACING, 100 * SPACING])
    for direction in AXES:
        assert entry(result, direction, 1.5 * SPACING).reason == "axis_lag_not_on_grid"
        assert entry(result, direction, 100 * SPACING).reason == "lag_exceeds_grid"


def test_constant_window_gives_supported_zeros() -> None:
    z = np.full((55, 55), 1737.25)
    result = run(z, all_valid(z.shape), ALL_LAGS, isotropic_rule="pooled_pairs")
    for lag in ALL_LAGS:
        for direction in DIRECTIONS:
            item = entry(result, direction, lag)
            if item.offset_cells is not None:
                assert item.nu_m == 0.0
                assert item.reason is None
        assert iso(result, lag).nu_m == 0.0
        assert iso(result, lag).reason is None


@pytest.mark.parametrize("scale", [1e-200, 1e-300, 1e200, 1e300])
def test_tiny_and_huge_relief_neither_underflows_nor_overflows(scale: float) -> None:
    x, y = analysis_mesh()
    b, c = 0.03, -0.07
    z = scale * (b * x + c * y)
    result = run(z, all_valid(z.shape), LAGS)
    for lag in LAGS:
        nu_x = entry(result, "x", lag).nu_m
        assert nu_x is not None
        assert close(nu_x / scale, lag * abs(b), atol=0.0)
        summary = iso(result, lag).nu_m
        assert summary is not None
        assert close(summary / scale, lag * math.sqrt((b * b + c * c) / 2.0), atol=0.0)


def test_difference_beyond_float64_raises() -> None:
    z = np.array([[-1e308, 1e308]])
    with pytest.raises(TerrainError, match="overflow"):
        run(z, all_valid(z.shape), [SPACING])


# --- Isotropic summary -----------------------------------------------------------------------


@pytest.mark.parametrize("rule", ["direction_mean", "pooled_pairs"])
def test_isotropic_rule_is_recorded_and_leaves_per_direction_values_unchanged(rule: str) -> None:
    z = rough_field()
    valid = hole_mask()
    result = run(z, valid, ALL_LAGS, isotropic_rule=rule)
    assert result.isotropic_rule == rule
    assert all(summary.rule == rule for summary in result.isotropic)
    other = "pooled_pairs" if rule == "direction_mean" else "direction_mean"
    assert dict(result.directional) == dict(
        run(z, valid, ALL_LAGS, isotropic_rule=other).directional
    )
    assert_matches_brute_force(result, z, valid)
    # Each direction on its own, through a lag list with only that lag, gives the same entry.
    for lag in ALL_LAGS:
        alone = run(z, valid, [lag], isotropic_rule=rule)
        for direction in DIRECTIONS:
            assert entry(alone, direction, lag) == entry(result, direction, lag)


def test_direction_mean_is_the_rms_over_supported_directions() -> None:
    z = rough_field((20, 40))
    valid = hole_mask((20, 40))
    result = run(z, valid, ALL_LAGS, isotropic_rule="direction_mean")
    for lag in ALL_LAGS:
        used = [entry(result, d, lag) for d in DIRECTIONS if entry(result, d, lag).nu_m is not None]
        summary = iso(result, lag)
        assert summary.directions == tuple(item.direction for item in used)
        assert summary.pair_count == sum(item.pair_count for item in used)
        values = [item.nu_m for item in used if item.nu_m is not None]
        expected = math.sqrt(math.fsum(v * v for v in values) / len(values))
        assert summary.nu_m is not None
        assert close(summary.nu_m, expected)


def test_pooled_pairs_is_nu_over_all_pairs_of_supported_directions() -> None:
    z = rough_field((20, 40))
    valid = hole_mask((20, 40))
    result = run(z, valid, ALL_LAGS, isotropic_rule="pooled_pairs")
    for lag in ALL_LAGS:
        pooled: list[float] = []
        used: list[str] = []
        for direction in DIRECTIONS:
            item = entry(result, direction, lag)
            if item.offset_cells is None:
                continue
            differences, _ = brute_differences(z, valid, *item.offset_cells)
            if differences:
                used.append(direction)
                pooled.extend(differences)
        summary = iso(result, lag)
        assert summary.directions == tuple(used)
        assert summary.pair_count == len(pooled)
        expected = brute_nu(pooled)
        assert expected is not None and summary.nu_m is not None
        assert close(summary.nu_m, expected)


def test_the_two_isotropic_rules_differ_when_pair_counts_differ() -> None:
    # A 20 x 40 plane: x has more pairs than y and a different nu, so the weights matter.
    rows, cols = np.indices((20, 40), dtype=np.float64)
    z = 2.0 * cols + 5.0 * rows
    valid = all_valid(z.shape)
    lag = 3 * SPACING
    mean = iso(run(z, valid, [lag], isotropic_rule="direction_mean"), lag).nu_m
    pooled = iso(run(z, valid, [lag], isotropic_rule="pooled_pairs"), lag).nu_m
    assert mean is not None and pooled is not None
    # direction_mean: sqrt((6^2 + 15^2) / 2); pooled: weights 20 * 37 and 40 * 17.
    assert close(mean, math.sqrt((6.0**2 + 15.0**2) / 2.0))
    assert close(pooled, math.sqrt((740 * 6.0**2 + 680 * 15.0**2) / 1420))
    assert abs(mean - pooled) > 0.1


def test_plane_isotropic_summary_uses_the_axes_on_the_90_m_grid() -> None:
    x, y = analysis_mesh()
    b, c = 0.03, -0.07
    z = 100.0 + b * x + c * y
    for rule in ISOTROPIC_RULES:
        result = run(z, all_valid(z.shape), LAGS, isotropic_rule=rule)
        for lag in LAGS:
            summary = iso(result, lag)
            assert summary.directions == ("x", "y")
            assert summary.nu_m is not None
            assert close(summary.nu_m, lag * math.sqrt((b * b + c * c) / 2.0))


# --- Inputs, outputs and determinism ---------------------------------------------------------


GOOD = np.arange(25.0).reshape(5, 5)
GOOD_VALID = all_valid(GOOD.shape)


@pytest.mark.parametrize(
    ("values", "valid"),
    [
        (np.arange(5.0), all_valid((5,))),
        (np.zeros((2, 2, 2)), all_valid((2, 2, 2))),
        (np.zeros((0, 5)), all_valid((0, 5))),
        (GOOD, all_valid((5, 4))),
        (GOOD, np.ones((5, 5), dtype=np.int64)),
        (GOOD > 3, GOOD_VALID),
        (GOOD + 1j, GOOD_VALID),
        (GOOD.astype(object), GOOD_VALID),
        (np.where(GOOD == 7, np.nan, GOOD), GOOD_VALID),
        (np.where(GOOD == 7, np.inf, GOOD), GOOD_VALID),
    ],
)
def test_bad_grids_raise(values: npt.ArrayLike, valid: npt.ArrayLike) -> None:
    with pytest.raises(TerrainError):
        run(values, valid, [SPACING])


@pytest.mark.parametrize("spacing", [0.0, -90.0, math.nan, math.inf, True])
def test_bad_spacing_raises(spacing: float) -> None:
    with pytest.raises(TerrainError):
        run(GOOD, GOOD_VALID, [SPACING], spacing_m=spacing)


@pytest.mark.parametrize(
    "lags",
    [[], [0.0], [-90.0], [math.nan], [math.inf], [180.0, 90.0], [90.0, 90.0], [True], ["90"]],
)
def test_bad_lags_raise(lags: list[float]) -> None:
    with pytest.raises(TerrainError):
        run(GOOD, GOOD_VALID, lags)


def test_lag_too_many_cells_for_float64_raises() -> None:
    with pytest.raises(TerrainError):
        run(GOOD, GOOD_VALID, [1e300], spacing_m=1e-300)


def test_unknown_isotropic_rule_raises() -> None:
    with pytest.raises(TerrainError):
        run(GOOD, GOOD_VALID, [SPACING], isotropic_rule="mean")


def test_integer_elevations_are_accepted_as_float64() -> None:
    z = np.arange(25, dtype=np.int16).reshape(5, 5)
    assert run(z, GOOD_VALID, [SPACING]) == run(z.astype(np.float64), GOOD_VALID, [SPACING])


def test_inputs_are_not_modified_and_outputs_are_frozen() -> None:
    z = rough_field()
    valid = hole_mask()
    z_copy, valid_copy = z.copy(), valid.copy()
    result = run(z, valid, ALL_LAGS)
    np.testing.assert_array_equal(z, z_copy)
    np.testing.assert_array_equal(valid, valid_copy)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.isotropic_rule = "pooled_pairs"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry(result, "x", LAGS[0]).nu_m = 0.0  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.directional["x"] = ()  # type: ignore[index]
    assert result.lags_m == ALL_LAGS
    assert result.spacing_m == SPACING
    assert tuple(result.directional) == DIRECTIONS


def test_repeated_runs_are_identical() -> None:
    z = rough_field()
    valid = hole_mask()
    first = run(z, valid, ALL_LAGS)
    assert all(run(z, valid, ALL_LAGS) == first for _ in range(3))


def test_terrain_module_uses_no_randomness() -> None:
    tree = ast.parse(Path(terrain.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {node.module or ""} | {alias.name for alias in node.names}
        elif isinstance(node, ast.Attribute):
            names = {node.attr}
        else:
            continue
        assert not names & {"random", "secrets", "numpy.random"}, ast.dump(node)


# --- Property: any small masked grid against the explicit pair loop ---------------------------


@st.composite
def masked_grids(
    draw: st.DrawFn,
) -> tuple[Array, BoolArray, float, list[tuple[float, str, int]]]:
    rows = draw(st.integers(1, 9))
    cols = draw(st.integers(1, 9))
    values = draw(
        hnp.arrays(
            np.float64,
            (rows, cols),
            elements=st.floats(-1e3, 1e3, allow_nan=False, allow_infinity=False),
        )
    )
    valid = draw(hnp.arrays(np.bool_, (rows, cols)))
    spacing = draw(st.sampled_from([0.5, 1.0, 25.0, 78.125, 90.0]))
    axis = draw(st.sets(st.integers(1, 10), min_size=1, max_size=4))
    diagonal = draw(st.sets(st.integers(1, 10), max_size=4))
    lags = [(k * spacing, "axis", k) for k in axis]
    lags += [(math.hypot(k * spacing, k * spacing), "diagonal", k) for k in diagonal]
    lags.sort()
    return np.where(valid, values, np.nan), valid, spacing, lags


@settings(max_examples=300, deadline=None)
@given(case=masked_grids(), rule=st.sampled_from(["direction_mean", "pooled_pairs"]))
def test_property_matches_the_explicit_pair_loop(
    case: tuple[Array, BoolArray, float, list[tuple[float, str, int]]], rule: str
) -> None:
    values, valid, spacing, lags = case
    result = run(values, valid, [lag for lag, _, _ in lags], spacing_m=spacing, isotropic_rule=rule)
    for lag, kind, k in lags:
        nus: list[float] = []
        pooled: list[float] = []
        used: list[str] = []
        for direction in DIRECTIONS:
            item = entry(result, direction, lag)
            on_grid = (direction in AXES) == (kind == "axis")
            if not on_grid:
                assert (
                    item.reason == f"{'axis' if direction in AXES else 'diagonal'}_lag_not_on_grid"
                )
                assert item.nu_m is None and item.offset_cells is None and item.pair_count == 0
                continue
            east, north = STEPS[direction]
            assert item.offset_cells == (k * east, k * north)
            differences, in_grid = brute_differences(values, valid, k * east, k * north)
            expected = brute_nu(differences)
            assert item.pair_count == len(differences)
            if expected is None:
                assert item.nu_m is None
                assert item.reason == ("lag_exceeds_grid" if in_grid == 0 else "no_valid_pairs")
            else:
                assert item.reason is None and item.nu_m is not None
                assert close(item.nu_m, expected)
                nus.append(item.nu_m)
                pooled.extend(differences)
                used.append(direction)
        summary = iso(result, lag)
        assert summary.directions == tuple(used)
        if not used:
            assert summary.nu_m is None and summary.reason == "no_supported_direction"
            continue
        if rule == "direction_mean":
            want = math.sqrt(math.fsum(v * v for v in nus) / len(nus))
        else:
            pooled_nu = brute_nu(pooled)
            assert pooled_nu is not None
            want = pooled_nu
        assert summary.nu_m is not None and summary.reason is None
        assert close(summary.nu_m, want)
