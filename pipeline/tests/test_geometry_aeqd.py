"""Tests for the D29 window grid and the spherical AEQD (PRD 7, "Window resampler (D29)").

Units and conventions used throughout:

- Window coordinates x (east) and y (north) are metres on the body sphere, measured from the
  window centre. Angles are radians. The radii are the D29 spheres from ``BODY_RADII_M``.
- Longitudes returned by the inverse lie in the half-open range [-pi, pi), where pi is the
  float64 ``math.pi``. A window centre must already be in that range. A longitude and the same
  value plus or minus 2 * math.pi name the same meridian, which is the convention
  ``math.degrees`` follows (``math.degrees(math.pi) == 180.0``). Real trigonometry has period
  2 pi, which is 2.4e-16 more than 2 * math.pi, so the exact reference below first moves a
  longitude that lies more than math.pi from the centre by 2 * math.pi, at 40 digits.
- Round trips use PRD 7 Tolerances (rtol 1e-10, atol 1e-12) on the window coordinates in plain
  metres, with no normalisation: inverse then forward must give back each of x and y to within
  1e-12 m + 1e-10 * |value|. On the 5 km, 90 m grid the smallest offsets are 25 m (pixel
  (27, 27) sits at x = -25 m, y = 25 m), where that is 2.5e-9 m.
- Float64 puts a floor under this. A returned longitude near +-pi is resolved only to half an
  ulp of math.pi, 2.2e-16 rad, which is 1.4e-9 m on the Earth sphere, 0.56 of the allowance at
  25 m. Closer than about 14 m to the centre, the allowance falls below that floor for any
  float64 (latitude, longitude) pair. Round trips therefore use the D29 pixel centres, which
  are the points where the resampler evaluates the inverse. Smaller offsets, down to subnormal
  ones, are checked for finite results everywhere, and for the round trip only at the centre
  (0, 0), where float64 resolves them.
- Distances from the window centre are checked against rho to 1e-9 relative (PRD 7 Tests).

The reference formulas here are written independently of the implementation: the grid with
explicit loops, a forward AEQD from 3-D unit vectors evaluated exactly at 40 significant digits
with mpmath, and the great-circle distance by the haversine formula.
"""

from __future__ import annotations

import ast
import math
import subprocess
import sys
from pathlib import Path

import mpmath  # type: ignore[import-untyped]
import numpy as np
import numpy.typing as npt
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from terraluna_pipeline import geometry
from terraluna_pipeline.config import load_terrain_config
from terraluna_pipeline.geometry import (
    BODY_RADII_M,
    AeqdDomainError,
    WindowGrid,
    WindowGridError,
    aeqd_forward,
    aeqd_inverse,
    window_grid,
)

pytestmark = pytest.mark.filterwarnings("error")

Array = npt.NDArray[np.float64]

RTOL = 1e-10
ATOL = 1e-12
DISTANCE_RTOL = 1e-9

PI = math.pi
HALF_PI = math.pi / 2
BODIES = tuple(BODY_RADII_M)
STORY_LATITUDES_DEG = (-89.9, -45.0, 0.0, 45.0, 89.9)
POLES_RAD = (-HALF_PI, HALF_PI)
# Centre longitudes: the prime meridian, a generic value and both sides of the antimeridian.
CENTRE_LONGITUDES_RAD = (0.0, 0.65449846949787, -PI, math.radians(179.99), math.radians(-179.99))

TERRAIN = load_terrain_config()
WINDOW_M = TERRAIN.window_m
GRID_M = TERRAIN.analysis_grid_m
GRID_SIZE = window_grid(WINDOW_M, GRID_M).size

# Significant digits of the exact reference forward. Float64 carries about 16.
REFERENCE_DIGITS = 40
# Grid rows and columns checked against the exact reference: both edges, their neighbours, two
# interior lines and the three lines through the centre pixel, where the offsets are smallest
# (25 m) and the absolute tolerance binds. The full 55 x 55 grid is checked against the module
# forward; the exact reference costs about 0.1 ms per point, so it gets these 81 points.
REFERENCE_LINES = (0, 1, 13, 26, 27, 28, 41, 53, 54)


# ---------------------------------------------------------------------------------------------
# Independent reference formulas


def exact_forward(
    lat: Array, lon: Array, lat0: float, lon0: float, radius: float
) -> tuple[Array, Array]:
    """Forward AEQD from 3-D unit vectors at 40 digits, written independently of the module.

    Every float64 input is taken at its exact binary value. The point is projected onto the
    centre's east, north and up axes in a body-fixed frame; the azimuth is (east, north)
    normalised and the distance is radius * atan2(|horizontal|, up). Only the resulting metres
    are rounded to float64, so the reference adds at most half an ulp to each coordinate.

    A longitude more than math.pi from the centre is moved by 2 * math.pi first, which undoes
    the inverse's wrap into [-pi, pi) under the module's convention (see the module docstring).
    """
    lat, lon = np.broadcast_arrays(np.asarray(lat, np.float64), np.asarray(lon, np.float64))
    x_out = np.empty(lat.shape)
    y_out = np.empty(lat.shape)
    with mpmath.workdps(REFERENCE_DIGITS):
        pi = mpmath.mpf(PI)
        phi0 = mpmath.mpf(lat0)
        lam0 = mpmath.mpf(lon0)
        up = (
            mpmath.cos(phi0) * mpmath.cos(lam0),
            mpmath.cos(phi0) * mpmath.sin(lam0),
            mpmath.sin(phi0),
        )
        east = (-mpmath.sin(lam0), mpmath.cos(lam0), mpmath.mpf(0))
        north = (
            -mpmath.sin(phi0) * mpmath.cos(lam0),
            -mpmath.sin(phi0) * mpmath.sin(lam0),
            mpmath.cos(phi0),
        )
        for index in np.ndindex(lat.shape):
            phi = mpmath.mpf(float(lat[index]))
            lam = mpmath.mpf(float(lon[index]))
            if lam - lam0 > pi:
                lam -= 2 * pi
            elif lam - lam0 < -pi:
                lam += 2 * pi
            point = (
                mpmath.cos(phi) * mpmath.cos(lam),
                mpmath.cos(phi) * mpmath.sin(lam),
                mpmath.sin(phi),
            )
            e = mpmath.fsum(a * b for a, b in zip(east, point, strict=True))
            n = mpmath.fsum(a * b for a, b in zip(north, point, strict=True))
            u = mpmath.fsum(a * b for a, b in zip(up, point, strict=True))
            horizontal = mpmath.sqrt(e * e + n * n)
            if horizontal == 0:
                x_out[index] = 0.0
                y_out[index] = 0.0
                continue
            scale = mpmath.mpf(radius) * mpmath.atan2(horizontal, u) / horizontal
            x_out[index] = float(scale * e)
            y_out[index] = float(scale * n)
    return x_out, y_out


def haversine_m(lat: Array, lon: Array, lat0: float, lon0: float, radius: float) -> Array:
    """Great-circle distance on the sphere by the haversine formula."""
    h = np.sin((lat - lat0) / 2) ** 2 + math.cos(lat0) * np.cos(lat) * np.sin((lon - lon0) / 2) ** 2
    distance: Array = 2 * radius * np.arcsin(np.sqrt(h))
    return distance


def prd_latitude_asin(x: Array, y: Array, lat0: float, radius: float) -> Array:
    """The PRD 7 latitude expression, evaluated literally with asin (rho > 0 only)."""
    rho = np.hypot(x, y)
    c = rho / radius
    lat: Array = np.arcsin(np.cos(c) * math.sin(lat0) + y * np.sin(c) * math.cos(lat0) / rho)
    return lat


def prd_longitude(x: Array, y: Array, lat0: float, lon0: float, radius: float) -> Array:
    """The PRD 7 longitude expression, before any range normalisation (rho > 0 only)."""
    rho = np.hypot(x, y)
    c = rho / radius
    lon: Array = lon0 + np.arctan2(
        x * np.sin(c), rho * math.cos(lat0) * np.cos(c) - y * math.sin(lat0) * np.sin(c)
    )
    return lon


def hand_grid(width: float, spacing: float) -> tuple[list[float], list[float]]:
    """Pixel centres from the PRD formula with explicit loops.

    The count is the largest n with n * spacing <= width, so every pixel cell lies inside the
    window.
    """
    count = 0
    while (count + 1) * spacing <= width:
        count += 1
    xs = [(i + 0.5) * spacing - width / 2 for i in range(count)]
    ys = [width / 2 - (j + 0.5) * spacing for j in range(count)]
    return xs, ys


def assert_metres_close(
    x_actual: Array, y_actual: Array, x_expected: Array, y_expected: Array
) -> None:
    """PRD 7 tolerances on window coordinates in plain metres: atol 1e-12 m, rtol 1e-10."""
    np.testing.assert_allclose(x_actual, x_expected, rtol=RTOL, atol=ATOL)
    np.testing.assert_allclose(y_actual, y_expected, rtol=RTOL, atol=ATOL)


def story_grid() -> tuple[Array, Array]:
    return window_grid(WINDOW_M, GRID_M).mesh_m()


def reference_grid() -> tuple[Array, Array]:
    """The grid points on ``REFERENCE_LINES`` (rows and columns), for the exact reference."""
    x, y = story_grid()
    lines = np.array(REFERENCE_LINES)
    return x[np.ix_(lines, lines)], y[np.ix_(lines, lines)]


def assert_grid_round_trip(
    x: Array, y: Array, *, lat0: float, lon0: float, radius: float, exact: bool
) -> None:
    """Inverse, then forward, recovers (x, y) in metres to PRD 7 tolerances.

    Checks the module forward and, with ``exact``, the 40-digit reference forward.
    """
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    x_back, y_back = aeqd_forward(lat, lon, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    assert x_back.dtype == y_back.dtype == np.float64
    assert_metres_close(x_back, y_back, x, y)
    if exact:
        x_ref, y_ref = exact_forward(lat, lon, lat0, lon0, radius)
        assert_metres_close(x_ref, y_ref, x, y)


# ---------------------------------------------------------------------------------------------
# Window grid


def test_grid_matches_hand_computed_centres_for_5000_by_90() -> None:
    grid = window_grid(5000.0, 90.0)
    xs, ys = hand_grid(5000.0, 90.0)

    # 5000 / 90 is about 55.6: 55 whole cells fit, leaving a 50 m strip uncovered.
    assert len(xs) == 55
    assert grid.size == 55
    assert grid.uncovered_m == 50.0
    np.testing.assert_array_equal(grid.x_m, np.array(xs))
    np.testing.assert_array_equal(grid.y_m, np.array(ys))

    x, y = grid.mesh_m()
    assert x.shape == y.shape == (55, 55)
    # Corners (row, column), row 0 at the top. Hand-computed: (0.5 * 90) - 2500 = -2455,
    # (54.5 * 90) - 2500 = 2405, 2500 - (0.5 * 90) = 2455 and 2500 - (54.5 * 90) = -2405.
    assert (x[0, 0], y[0, 0]) == (-2455.0, 2455.0)
    assert (x[0, 54], y[0, 54]) == (2405.0, 2455.0)
    assert (x[54, 0], y[54, 0]) == (-2455.0, -2405.0)
    assert (x[54, 54], y[54, 54]) == (2405.0, -2405.0)
    # Centre pixel (27, 27): (27.5 * 90) - 2500 = -25 and 2500 - (27.5 * 90) = 25.
    assert (x[27, 27], y[27, 27]) == (-25.0, 25.0)


@pytest.mark.parametrize(
    ("width", "spacing", "size", "first", "last"),
    [
        (5000.0, 90.0, 55, -2455.0, 2405.0),
        (4500.0, 90.0, 50, -2205.0, 2205.0),
        (5000.0, 100.0, 50, -2450.0, 2450.0),
        (1000.0, 300.0, 3, -350.0, 250.0),
        (90.0, 90.0, 1, 0.0, 0.0),
    ],
)
def test_grid_count_and_ends(
    width: float, spacing: float, size: int, first: float, last: float
) -> None:
    grid = window_grid(width, spacing)
    xs, ys = hand_grid(width, spacing)
    assert grid.size == size == len(xs)
    assert grid.uncovered_m == width - size * spacing
    assert (grid.x_m[0], grid.x_m[-1]) == (first, last)
    assert (grid.y_m[0], grid.y_m[-1]) == (-first, -last)
    np.testing.assert_array_equal(grid.x_m, np.array(xs))
    np.testing.assert_array_equal(grid.y_m, np.array(ys))


def test_grid_cells_lie_inside_the_window_anchored_at_the_top_left() -> None:
    grid = window_grid(5000.0, 90.0)
    half = grid.spacing_m / 2
    assert grid.x_m[0] - half == -2500.0
    assert grid.y_m[0] + half == 2500.0
    assert grid.x_m[-1] + half <= 2500.0
    assert grid.y_m[-1] - half >= -2500.0
    assert np.all(np.diff(grid.x_m) == 90.0)
    assert np.all(np.diff(grid.y_m) == -90.0)


def test_grid_mesh_indexes_rows_by_y_and_columns_by_x() -> None:
    grid = window_grid(1000.0, 300.0)
    x, y = grid.mesh_m()
    for j in range(grid.size):
        for i in range(grid.size):
            assert x[j, i] == grid.x_m[i]
            assert y[j, i] == grid.y_m[j]


def test_grid_is_float64_and_read_only() -> None:
    grid = window_grid(5000.0, 90.0)
    assert isinstance(grid, WindowGrid)
    for values in (grid.x_m, grid.y_m, *grid.mesh_m()):
        assert values.dtype == np.float64
        with pytest.raises(ValueError):
            values[0] = 1.0
    assert type(grid.width_m) is float
    assert type(grid.spacing_m) is float
    assert type(grid.size) is int


@pytest.mark.parametrize(
    ("width", "spacing"),
    [
        (0.0, 90.0),
        (-5000.0, 90.0),
        (5000.0, 0.0),
        (5000.0, -90.0),
        (math.nan, 90.0),
        (5000.0, math.nan),
        (math.inf, 90.0),
        (5000.0, math.inf),
        (80.0, 90.0),
    ],
)
def test_grid_rejects_invalid_parameters(width: float, spacing: float) -> None:
    with pytest.raises(WindowGridError):
        window_grid(width, spacing)


def test_grid_errors_are_value_errors() -> None:
    assert issubclass(WindowGridError, ValueError)
    assert issubclass(AeqdDomainError, ValueError)


# ---------------------------------------------------------------------------------------------
# Inverse AEQD at the window centre


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("lat0", [*POLES_RAD, *(math.radians(d) for d in STORY_LATITUDES_DEG)])
@pytest.mark.parametrize("lon0", [-PI, -2.0, 0.0, 1.0, math.nextafter(PI, 0.0)])
def test_inverse_returns_the_centre_exactly_at_rho_zero(
    body: str, lat0: float, lon0: float
) -> None:
    radius = BODY_RADII_M[body]
    x = np.array([0.0, -0.0, 0.0, -0.0, 45.0])
    y = np.array([0.0, 0.0, -0.0, -0.0, -45.0])
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    assert lat.dtype == lon.dtype == np.float64
    assert np.all(np.isfinite(lat))
    assert np.all(np.isfinite(lon))
    assert np.all(lat[:4] == lat0)
    assert np.all(lon[:4] == lon0)


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("lat0", POLES_RAD)
@pytest.mark.parametrize("lon0", [-PI, 0.0, 2.0])
def test_pole_centred_window_has_no_nan(body: str, lat0: float, lon0: float) -> None:
    radius = BODY_RADII_M[body]
    x, y = story_grid()
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    assert np.all(np.isfinite(lat))
    assert np.all(np.isfinite(lon))
    assert np.all(np.abs(lat) <= HALF_PI)
    centre_lat, centre_lon = aeqd_inverse(0.0, 0.0, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    assert (centre_lat.item(), centre_lon.item()) == (lat0, lon0)


# ---------------------------------------------------------------------------------------------
# The inverse follows the PRD expressions


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("lat0_deg", STORY_LATITUDES_DEG)
@pytest.mark.parametrize("lon0", CENTRE_LONGITUDES_RAD)
def test_inverse_matches_the_prd_expressions(body: str, lat0_deg: float, lon0: float) -> None:
    radius = BODY_RADII_M[body]
    lat0 = math.radians(lat0_deg)
    x, y = story_grid()
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)

    # Latitude: the PRD's asin expression, to PRD 7 tolerances in radians.
    np.testing.assert_allclose(lat, prd_latitude_asin(x, y, lat0, radius), rtol=RTOL, atol=ATOL)
    # Longitude: the PRD's atan2 expression, equal up to a whole turn.
    turns = (lon - prd_longitude(x, y, lat0, lon0, radius)) / (2 * PI)
    np.testing.assert_allclose(turns, np.round(turns), rtol=0.0, atol=ATOL)


# ---------------------------------------------------------------------------------------------
# Round trips: inverse, then forward


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("lat0_deg", STORY_LATITUDES_DEG)
@pytest.mark.parametrize("lon0", CENTRE_LONGITUDES_RAD)
def test_round_trip_on_the_window_grid(body: str, lat0_deg: float, lon0: float) -> None:
    radius = BODY_RADII_M[body]
    lat0 = math.radians(lat0_deg)
    x, y = story_grid()
    assert_grid_round_trip(x, y, lat0=lat0, lon0=lon0, radius=radius, exact=False)
    x, y = reference_grid()
    assert_grid_round_trip(x, y, lat0=lat0, lon0=lon0, radius=radius, exact=True)


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("lat0", POLES_RAD)
@pytest.mark.parametrize("lon0", [-PI, 0.0, 2.0])
def test_round_trip_on_a_pole_centred_window(body: str, lat0: float, lon0: float) -> None:
    radius = BODY_RADII_M[body]
    x, y = story_grid()
    assert_grid_round_trip(x, y, lat0=lat0, lon0=lon0, radius=radius, exact=False)
    x, y = reference_grid()
    assert_grid_round_trip(x, y, lat0=lat0, lon0=lon0, radius=radius, exact=True)


# Earth centres where the round trip broke the metre allowance while the inverse latitude came
# from order-one sin and cos values and the forward y from a difference of order-one products.
# Found by random search over 2.4 million centres with |lat0| in [1.0, 1.07] rad, where a
# latitude's ulp is 2.2e-16 rad, so the allowance at y = 25 m (2.5e-9 m) is under two ulps.
# The first five used 1.06 to 1.12 of it through the module forward at row 27. The last used
# 1.01 of it at (x, y) = (1955, 25) m against the exact reference, from the inverse alone.
ORDER_ONE_ROUNDING_CENTRES = (
    (1.0233922226196999, -2.3583296722334133),
    (1.0174834226785296, 1.8291693112611698),
    (1.0242115398115987, -1.9434293942504273),
    (1.0134364985476998, 0.18244481513276095),
    (-1.0407524042164307, -2.013019339623809),
    (-1.0149308119477685, 2.6707139144457486),
)


@pytest.mark.parametrize(("lat0", "lon0"), ORDER_ONE_ROUNDING_CENTRES)
def test_round_trip_where_order_one_rounding_exceeded_the_allowance(
    lat0: float, lon0: float
) -> None:
    radius = BODY_RADII_M["earth"]
    x, y = story_grid()
    assert_grid_round_trip(x, y, lat0=lat0, lon0=lon0, radius=radius, exact=False)
    # The row and the column through the centre pixel hold the 25 m offsets.
    middle = GRID_SIZE // 2
    x_lines = np.concatenate([x[middle, :], x[:, middle]])
    y_lines = np.concatenate([y[middle, :], y[:, middle]])
    assert_grid_round_trip(x_lines, y_lines, lat0=lat0, lon0=lon0, radius=radius, exact=True)


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("lat0_deg", STORY_LATITUDES_DEG)
@pytest.mark.parametrize("lon0", [0.65449846949787, -PI])
def test_forward_matches_the_exact_reference(body: str, lat0_deg: float, lon0: float) -> None:
    radius = BODY_RADII_M[body]
    lat0 = math.radians(lat0_deg)
    # Points 4 km away in every direction, built without the inverse, plus points 0.5 to 4 km
    # away just off due east and due west. Those have |y| from millimetres to tens of metres,
    # where the absolute part of the tolerance binds, so order-one rounding in y shows there.
    tilt = np.array([-1e-2, -1e-4, -1e-6, 1e-6, 1e-4, 1e-2])
    angle = np.concatenate([np.linspace(-PI, PI, 37), tilt, PI + tilt])
    reach = np.concatenate([np.full(37, 4000.0), np.tile([500.0, 2000.0, 4000.0], 4)]) / radius
    lat = np.clip(lat0 + reach * np.sin(angle), -HALF_PI, HALF_PI)
    lon = lon0 + reach * np.cos(angle)
    lon = np.where(lon < -PI, lon + 2 * PI, lon)
    x, y = aeqd_forward(lat, lon, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    x_ref, y_ref = exact_forward(lat, lon, lat0, lon0, radius)
    assert_metres_close(x, y, x_ref, y_ref)


def test_forward_returns_the_origin_at_the_centre() -> None:
    for body in BODIES:
        for lat0 in (*POLES_RAD, -0.3, 0.0, 1.2):
            x, y = aeqd_forward(lat0, 1.0, lat0_rad=lat0, lon0_rad=1.0, radius_m=BODY_RADII_M[body])
            assert (x.item(), y.item()) == (0.0, 0.0)


@pytest.mark.parametrize("body", BODIES)
def test_forward_puts_north_on_plus_y_and_east_on_plus_x(body: str) -> None:
    radius = BODY_RADII_M[body]
    step = 1000.0 / radius
    lat = np.array([0.4 + step, 0.4 - step, 0.4, 0.4])
    lon = np.array([1.0, 1.0, 1.0 + step, 1.0 - step])
    x, y = aeqd_forward(lat, lon, lat0_rad=0.4, lon0_rad=1.0, radius_m=radius)
    assert y[0] > 0 and abs(x[0]) < 1e-6
    assert y[1] < 0 and abs(x[1]) < 1e-6
    assert x[2] > 0
    assert x[3] < 0


# ---------------------------------------------------------------------------------------------
# Distances from the window centre


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("lat0", [*POLES_RAD, *(math.radians(d) for d in STORY_LATITUDES_DEG)])
@pytest.mark.parametrize("lon0", CENTRE_LONGITUDES_RAD)
def test_great_circle_distance_from_the_centre_equals_rho(
    body: str, lat0: float, lon0: float
) -> None:
    radius = BODY_RADII_M[body]
    x, y = story_grid()
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    distance = haversine_m(lat, lon, lat0, lon0, radius)
    np.testing.assert_allclose(distance, np.hypot(x, y), rtol=DISTANCE_RTOL, atol=0.0)


# ---------------------------------------------------------------------------------------------
# Longitude range


def test_longitudes_are_normalised_to_minus_pi_inclusive_pi_exclusive() -> None:
    radius = BODY_RADII_M["earth"]
    x, y = story_grid()
    for lon0 in (-PI, math.nextafter(PI, 0.0), math.radians(179.99), math.radians(-179.99)):
        _, lon = aeqd_inverse(x, y, lat0_rad=0.0, lon0_rad=lon0, radius_m=radius)
        assert np.all(lon >= -PI)
        assert np.all(lon < PI)


def test_a_window_on_the_antimeridian_wraps_west_points_to_the_east_end() -> None:
    radius = BODY_RADII_M["moon"]
    x = np.array([-1000.0, 1000.0])
    y = np.array([0.0, 0.0])
    _, lon = aeqd_inverse(x, y, lat0_rad=0.0, lon0_rad=-PI, radius_m=radius)
    # West of -pi wraps to just below +pi; east stays just above -pi.
    assert lon[0] > 3.14
    assert lon[1] < -3.14
    np.testing.assert_allclose(lon[0] - 2 * PI, -PI - 1000.0 / radius, rtol=RTOL, atol=ATOL)
    np.testing.assert_allclose(lon[1], -PI + 1000.0 / radius, rtol=RTOL, atol=ATOL)


# ---------------------------------------------------------------------------------------------
# Property test


PIXELS = st.lists(
    st.tuples(st.integers(0, GRID_SIZE - 1), st.integers(0, GRID_SIZE - 1)),
    min_size=1,
    max_size=16,
)


@settings(max_examples=400, deadline=None)
@given(
    body=st.sampled_from(BODIES),
    lat0=st.floats(-HALF_PI, HALF_PI),
    lon0=st.floats(-PI, PI, exclude_max=True),
    pixels=PIXELS,
)
@example(body="moon", lat0=-HALF_PI, lon0=-PI, pixels=[(0, 0), (27, 27), (54, 54), (0, 54)])
@example(body="earth", lat0=0.0, lon0=-PI, pixels=[(27, 27), (27, 0), (0, 27), (54, 28)])
@example(body="earth", lat0=HALF_PI, lon0=0.0, pixels=[(27, 27), (26, 28), (0, 54)])
@example(
    body="mars", lat0=math.radians(89.99), lon0=math.nextafter(PI, 0.0), pixels=[(27, 28), (0, 0)]
)
def test_round_trip_property(
    body: str, lat0: float, lon0: float, pixels: list[tuple[int, int]]
) -> None:
    """Random centres anywhere on the sphere, random pixel centres of the 5 km window.

    Each pixel is (row, column) on the configured D29 grid. Longitudes come back in [-pi, pi)
    and latitudes in [-pi/2, pi/2], and the window coordinates survive inverse then forward,
    through both the module and the exact reference, to PRD 7 tolerances in plain metres.
    """
    radius = BODY_RADII_M[body]
    grid = window_grid(WINDOW_M, GRID_M)
    x = np.array([grid.x_m[column] for _, column in pixels])
    y = np.array([grid.y_m[row] for row, _ in pixels])
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    assert np.all((lon >= -PI) & (lon < PI))
    assert np.all(np.abs(lat) <= HALF_PI)
    assert_grid_round_trip(x, y, lat0=lat0, lon0=lon0, radius=radius, exact=True)


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize(
    ("lat0", "lon0"),
    [(-HALF_PI, -PI), (HALF_PI, 0.0), (0.0, -PI), (math.radians(45.0), math.nextafter(PI, 0.0))],
)
def test_tiny_offsets_give_finite_coordinates(body: str, lat0: float, lon0: float) -> None:
    """Offsets far below the float64 floor of the module docstring, down to subnormal ones."""
    radius = BODY_RADII_M[body]
    x = np.array([5e-324, -1e-300, 1e-12, 0.0, -1e-9, 3e-6])
    y = np.array([0.0, 1e-300, -1e-12, 5e-324, 1e-9, -7e-7])
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    assert np.all(np.isfinite(lat))
    assert np.all(np.isfinite(lon))
    assert np.all(np.abs(lat) <= HALF_PI)
    assert np.all((lon >= -PI) & (lon < PI))
    x_back, y_back = aeqd_forward(lat, lon, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    assert np.all(np.isfinite(x_back))
    assert np.all(np.isfinite(y_back))


@pytest.mark.parametrize("body", BODIES)
def test_tiny_offsets_round_trip_at_the_origin_centre(body: str) -> None:
    """At (0, 0) float64 resolves tiny angles, so tiny offsets meet PRD 7 tolerances in metres."""
    radius = BODY_RADII_M[body]
    x = np.array([5e-324, -1e-300, 1e-12, 0.0, -1e-9, 3e-6])
    y = np.array([0.0, 1e-300, -1e-12, 5e-324, 1e-9, -7e-7])
    assert_grid_round_trip(x, y, lat0=0.0, lon0=0.0, radius=radius, exact=True)


# ---------------------------------------------------------------------------------------------
# Domain checks


@pytest.mark.parametrize(
    ("lat0", "lon0", "radius"),
    [
        (math.nextafter(HALF_PI, 2.0), 0.0, 1737400.0),
        (math.nextafter(-HALF_PI, -2.0), 0.0, 1737400.0),
        (math.nan, 0.0, 1737400.0),
        (0.0, PI, 1737400.0),
        (0.0, math.nextafter(-PI, -4.0), 1737400.0),
        (0.0, math.inf, 1737400.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, -1737400.0),
        (0.0, 0.0, math.nan),
        (0.0, 0.0, math.inf),
    ],
)
def test_invalid_centre_or_radius_raises(lat0: float, lon0: float, radius: float) -> None:
    with pytest.raises(AeqdDomainError):
        aeqd_inverse(10.0, 10.0, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    with pytest.raises(AeqdDomainError):
        aeqd_forward(0.0, 0.0, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)


@pytest.mark.parametrize(("x", "y"), [(math.nan, 0.0), (0.0, math.inf), (-math.inf, 1.0)])
def test_inverse_rejects_non_finite_offsets(x: float, y: float) -> None:
    with pytest.raises(AeqdDomainError):
        aeqd_inverse(
            np.array([0.0, x]), np.array([0.0, y]), lat0_rad=0.0, lon0_rad=0.0, radius_m=1737400.0
        )


def test_inverse_rejects_offsets_beyond_the_antipode() -> None:
    radius = 1737400.0
    with pytest.raises(AeqdDomainError):
        aeqd_inverse(PI * radius * 1.01, 0.0, lat0_rad=0.0, lon0_rad=0.0, radius_m=radius)


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (math.nextafter(HALF_PI, 2.0), 0.0),
        (math.nan, 0.0),
        (0.0, math.nan),
        (0.0, math.nextafter(PI, 4.0)),
        (0.0, math.nextafter(-PI, -4.0)),
    ],
)
def test_forward_rejects_points_outside_the_coordinate_ranges(lat: float, lon: float) -> None:
    with pytest.raises(AeqdDomainError):
        aeqd_forward(lat, lon, lat0_rad=0.0, lon0_rad=0.0, radius_m=1737400.0)


def test_forward_accepts_plus_pi_as_a_point_longitude() -> None:
    x, y = aeqd_forward(0.0, PI, lat0_rad=0.0, lon0_rad=-PI, radius_m=1737400.0)
    assert abs(x.item()) < 1e-6
    assert y.item() == 0.0


# ---------------------------------------------------------------------------------------------
# Terrain distances stay separate from radar and model distances


GEOMETRY_IMPORT_ALLOWLIST = frozenset(
    {
        "__future__",
        "collections.abc",
        "dataclasses",
        "math",
        "types",
        "typing",
        "numpy",
        "numpy.typing",
    }
)


def test_geometry_imports_only_the_standard_library_and_numpy() -> None:
    source = Path(geometry.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "geometry.py must not use relative imports"
            assert node.module is not None
            imported.add(node.module)
    assert imported <= GEOMETRY_IMPORT_ALLOWLIST


def test_importing_geometry_loads_no_other_terraluna_module() -> None:
    code = (
        "import sys\n"
        "import terraluna_pipeline.geometry\n"
        "print(','.join(sorted(m for m in sys.modules if m.startswith('terraluna'))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "terraluna_pipeline,terraluna_pipeline.geometry"
