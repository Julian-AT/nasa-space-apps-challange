"""Tests for the D29 window grid and the spherical AEQD (PRD 7, "Window resampler (D29)").

Units and conventions used throughout:

- Window coordinates x (east) and y (north) are metres on the body sphere, measured from the
  window centre. Angles are radians. The radii are the D29 spheres from ``BODY_RADII_M``.
- Longitudes returned by the inverse lie in the half-open range [-pi, pi), where pi is the
  float64 ``math.pi``. A window centre must already be in that range. Every float64 longitude
  is the real angle it denotes, on the true sphere: two longitudes name the same meridian when
  they differ by a whole number of turns of the true 2 pi, which is 2.4e-16 more than
  2 * math.pi. On the Earth sphere that difference is 1.6e-9 m, more than the allowance below
  at offsets under 15 m. The exact references therefore take each float64 longitude at its
  exact value and use mpmath's pi, with no reduction by 2 * math.pi.
- Round trips use PRD 7 Tolerances (rtol 1e-10, atol 1e-12) on the window coordinates in plain
  metres, with no normalisation: inverse then forward must give back each of x and y to within
  1e-12 m + 1e-10 * |value|. On the 5 km, 90 m grid the smallest offsets are 25 m (pixel
  (27, 27) sits at x = -25 m, y = 25 m), where that is 2.5e-9 m.
- Float64 puts a floor under this. A returned longitude near +-pi is resolved only to half an
  ulp of math.pi, 2.2e-16 rad, which is 1.4e-9 m on the Earth sphere, 0.56 of the allowance at
  25 m. Closer than about 14 m to the centre or to an axis, the allowance falls below that
  floor for any float64 (latitude, longitude) pair. Round trips therefore use the D29 pixel
  centres, which are the points where the resampler evaluates the inverse. Tiny offsets, down
  to subnormal ones, also get a round trip at the centre (0, 0), where float64 resolves them.
- Continuous offsets anywhere in the 5 km window, including tiny and near-axis ones, are
  checked one direction at a time against exact references, at the same PRD 7 tolerances
  (rtol 1e-10, atol 1e-12): the inverse (latitude, longitude) in radians, and the forward of
  float64 points in the window in metres, each against the exact image of the same float64
  input. Neither check passes through a float64 rounding of the other direction.
- Distances from the window centre are checked against rho to 1e-9 relative (PRD 7 Tests).

The reference formulas here are written independently of the implementation: the grid with
explicit loops, a forward and an inverse AEQD from 3-D unit vectors evaluated exactly at 40
significant digits with mpmath, and the great-circle distance by the haversine formula. The two
exact references are checked against each other at 40 digits.
"""

from __future__ import annotations

import ast
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

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

# Significant digits of the exact references. Float64 carries about 16.
REFERENCE_DIGITS = 40
with mpmath.workdps(REFERENCE_DIGITS):
    # The true 2 pi minus the float64 2 * math.pi, about 2.449e-16 rad, rounded once.
    TWO_PI_RESIDUAL = float(2 * mpmath.pi - 2 * mpmath.mpf(PI))
# Grid rows and columns checked against the exact reference: both edges, their neighbours, two
# interior lines and the three lines through the centre pixel, where the offsets are smallest
# (25 m) and the absolute tolerance binds. The full 55 x 55 grid is checked against the module
# forward; the exact reference costs about 0.1 ms per point, so it gets these 81 points.
REFERENCE_LINES = (0, 1, 13, 26, 27, 28, 41, 53, 54)

# The exact references must agree with each other far below the 1e-12 m allowance. At 40
# digits each carries rounding of about radius * 1e-40, under 1e-33 m; 1e-25 m leaves room for
# the conditioning of atan2 and sqrt and is still 13 orders below the allowance.
REFERENCE_AGREEMENT_M = 1e-25

HALF_WINDOW_M = WINDOW_M / 2
# Explicit continuous offsets in the window: the centre, the corners and edge midpoints, tiny
# and subnormal components, and offsets near an axis far from the centre, where the absolute
# part of the tolerance binds.
EDGE_OFFSETS = [
    (0.0, 0.0),
    (-0.0, -0.0),
    (HALF_WINDOW_M, HALF_WINDOW_M),
    (-HALF_WINDOW_M, -HALF_WINDOW_M),
    (HALF_WINDOW_M, -0.0),
    (0.0, -HALF_WINDOW_M),
    (5e-324, 0.0),
    (-1e-300, 1e-300),
    (1e-12, -1e-9),
    (2400.0, 1e-9),
    (-1e-7, -2450.0),
    (1234.5, -3e-6),
    (0.4, 13.9),
    (-13.9, -0.4),
]


# ---------------------------------------------------------------------------------------------
# Independent reference formulas


Vector = tuple[Any, Any, Any]
"""Three mpmath numbers: a vector in the centre frame of :func:`centre_axes`."""


def centre_axes(lat0: float) -> tuple[Vector, Vector, Vector]:
    """East, north and up unit vectors at the window centre, at the working mpmath precision.

    The body-fixed frame is turned about the polar axis so that the centre meridian lies in the
    x-z plane: a point's longitude in this frame is its longitude minus the centre's. Call it
    inside ``mpmath.workdps``.
    """
    phi0 = mpmath.mpf(lat0)
    zero = mpmath.mpf(0)
    east = (zero, mpmath.mpf(1), zero)
    north = (-mpmath.sin(phi0), zero, mpmath.cos(phi0))
    up = (mpmath.cos(phi0), zero, mpmath.sin(phi0))
    return east, north, up


def dot(a: Vector, b: Vector) -> Any:
    return mpmath.fsum(p * q for p, q in zip(a, b, strict=True))


def exact_offset_mp(
    phi: Any, dlam: Any, axes: tuple[Vector, Vector, Vector], radius: Any
) -> tuple[Any, Any]:
    """Forward AEQD of one point on the true sphere, in mpmath numbers: (east, north) metres.

    ``dlam`` is the point's longitude minus the centre's. The point is projected onto the
    centre's east, north and up axes; the azimuth is (east, north) normalised and the distance
    is radius * atan2(|horizontal|, up).
    """
    east, north, up = axes
    point = (
        mpmath.cos(phi) * mpmath.cos(dlam),
        mpmath.cos(phi) * mpmath.sin(dlam),
        mpmath.sin(phi),
    )
    e = dot(east, point)
    n = dot(north, point)
    u = dot(up, point)
    horizontal = mpmath.sqrt(e * e + n * n)
    if horizontal == 0:
        return mpmath.mpf(0), mpmath.mpf(0)
    scale = radius * mpmath.atan2(horizontal, u) / horizontal
    return scale * e, scale * n


def exact_point_mp(
    x: Any, y: Any, axes: tuple[Vector, Vector, Vector], radius: Any
) -> tuple[Any, Any]:
    """Inverse AEQD of one offset on the true sphere, in mpmath numbers: (phi, lam - lam0).

    The point lies a great-circle distance rho = hypot(x, y) from the centre, at azimuth
    (x, y) / rho from north. Its unit vector is cos c * up + sin c * (azimuth in the east-north
    plane), with c = rho / radius, read back as a latitude atan2(z, hypot(x, y)) and a longitude
    difference atan2(y, x) in (-pi, pi]. At rho = 0 it is the centre, whose latitude is read
    back from the up vector the same way, to the working precision.
    """
    east, north, up = axes
    rho = mpmath.sqrt(x * x + y * y)
    if rho == 0:
        return mpmath.atan2(up[2], up[0]), mpmath.mpf(0)
    c = rho / radius
    azimuth = tuple(x / rho * a + y / rho * b for a, b in zip(east, north, strict=True))
    point = tuple(mpmath.cos(c) * a + mpmath.sin(c) * b for a, b in zip(up, azimuth, strict=True))
    phi = mpmath.atan2(point[2], mpmath.sqrt(point[0] ** 2 + point[1] ** 2))
    return phi, mpmath.atan2(point[1], point[0])


def exact_forward(
    lat: Array, lon: Array, lat0: float, lon0: float, radius: float
) -> tuple[Array, Array]:
    """Forward AEQD from 3-D unit vectors at 40 digits, written independently of the module.

    Every float64 input is taken at its exact binary value (see :func:`exact_offset_mp`). Only
    the resulting metres are rounded to float64, so the reference adds at most half an ulp to
    each coordinate.

    The longitude difference is used as it is, on the true sphere: mpmath's sine and cosine
    have the true period 2 pi, so a point across the antimeridian needs no reduction.
    """
    lat, lon = np.broadcast_arrays(np.asarray(lat, np.float64), np.asarray(lon, np.float64))
    x_out = np.empty(lat.shape)
    y_out = np.empty(lat.shape)
    with mpmath.workdps(REFERENCE_DIGITS):
        lam0 = mpmath.mpf(lon0)
        axes = centre_axes(lat0)
        r = mpmath.mpf(radius)
        for index in np.ndindex(lat.shape):
            dlam = mpmath.mpf(float(lon[index])) - lam0
            e, n = exact_offset_mp(mpmath.mpf(float(lat[index])), dlam, axes, r)
            x_out[index] = float(e)
            y_out[index] = float(n)
    return x_out, y_out


def exact_inverse(
    x: Array, y: Array, lat0: float, lon0: float, radius: float
) -> tuple[Array, Array, Array, Array]:
    """Inverse AEQD from 3-D unit vectors at 40 digits, written independently of the module.

    Every float64 offset is taken at its exact binary value (see :func:`exact_point_mp`).
    Returns float64 ``(lat, lon, dlat, dlon)``: the latitude and longitude, and their exact
    differences from the centre, each rounded once. ``dlon`` lies in (-pi, pi]. ``lon`` is
    lon0 + dlon, moved by one turn of the true 2 pi when it lies outside [-math.pi, math.pi).
    That range is 2.4e-16 short of a full turn, so after rounding ``lon`` can lie just outside
    it; compare it with :func:`assert_angles_close`.
    """
    x, y = np.broadcast_arrays(np.asarray(x, np.float64), np.asarray(y, np.float64))
    lat_out = np.empty(x.shape)
    lon_out = np.empty(x.shape)
    dlat_out = np.empty(x.shape)
    dlon_out = np.empty(x.shape)
    with mpmath.workdps(REFERENCE_DIGITS):
        top = mpmath.mpf(PI)
        phi0 = mpmath.mpf(lat0)
        lam0 = mpmath.mpf(lon0)
        axes = centre_axes(lat0)
        r = mpmath.mpf(radius)
        for index in np.ndindex(x.shape):
            phi, dlam = exact_point_mp(
                mpmath.mpf(float(x[index])), mpmath.mpf(float(y[index])), axes, r
            )
            lam = lam0 + dlam
            if lam >= top:
                lam -= 2 * mpmath.pi
            elif lam < -top:
                lam += 2 * mpmath.pi
            lat_out[index] = float(phi)
            lon_out[index] = float(lam)
            dlat_out[index] = float(phi - phi0)
            dlon_out[index] = float(dlam)
    return lat_out, lon_out, dlat_out, dlon_out


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


def assert_angles_close(actual: Array, expected: Array) -> None:
    """PRD 7 tolerances on angles in radians, comparing the same meridian.

    Each actual angle is first moved by the whole number of turns of the true 2 pi that brings
    it nearest the expected one, so that angles on either side of the antimeridian compare as
    the meridians they name. A turn is added as 2 * math.pi plus ``TWO_PI_RESIDUAL``. Ranges
    are asserted separately.
    """
    turns = np.round((expected - actual) / (2 * PI))
    moved = (actual + turns * (2 * PI)) + turns * TWO_PI_RESIDUAL
    np.testing.assert_allclose(moved, expected, rtol=RTOL, atol=ATOL)


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
# The exact references


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize(
    "lat0",
    [-HALF_PI, math.radians(-89.9), -0.7, 0.0, 1e-300, 1.0233922226196999, 1.4, HALF_PI],
)
def test_exact_references_invert_each_other(body: str, lat0: float) -> None:
    """The exact inverse, then the exact forward, at 40 digits gives back each offset.

    The centre frame removes the centre longitude, so the references do not depend on it.
    """
    radius = BODY_RADII_M[body]
    rng = np.random.default_rng(20261114)
    offsets = [*EDGE_OFFSETS, *rng.uniform(-HALF_WINDOW_M, HALF_WINDOW_M, (32, 2)).tolist()]
    with mpmath.workdps(REFERENCE_DIGITS):
        axes = centre_axes(lat0)
        r = mpmath.mpf(radius)
        for x, y in offsets:
            phi, dlam = exact_point_mp(mpmath.mpf(x), mpmath.mpf(y), axes, r)
            assert -mpmath.pi / 2 <= phi <= mpmath.pi / 2
            assert -mpmath.pi < dlam <= mpmath.pi
            x_back, y_back = exact_offset_mp(phi, dlam, axes, r)
            assert abs(x_back - x) <= REFERENCE_AGREEMENT_M
            assert abs(y_back - y) <= REFERENCE_AGREEMENT_M


@pytest.mark.parametrize("body", BODIES)
def test_exact_inverse_points_north_and_east(body: str) -> None:
    """Orientation of the exact references: north along the meridian, east along the equator."""
    radius = BODY_RADII_M[body]
    with mpmath.workdps(REFERENCE_DIGITS):
        r = mpmath.mpf(radius)
        tolerance = mpmath.mpf(10) ** (8 - REFERENCE_DIGITS)
        for lat0 in (-0.7, 0.0, 1.4):
            axes = centre_axes(lat0)
            for y in (-2500.0, -25.0, 1e-9, 2500.0):
                phi, dlam = exact_point_mp(mpmath.mpf(0), mpmath.mpf(y), axes, r)
                assert abs(phi - (mpmath.mpf(lat0) + y / r)) <= tolerance
                assert dlam == 0
        axes = centre_axes(0.0)
        for x in (-2500.0, -25.0, 1e-9, 2500.0):
            phi, dlam = exact_point_mp(mpmath.mpf(x), mpmath.mpf(0), axes, r)
            assert phi == 0
            assert abs(dlam - x / r) <= tolerance


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


@pytest.mark.parametrize("body", BODIES)
def test_long_offsets_that_reach_a_pole_keep_the_latitude_in_range(body: str) -> None:
    """Offsets straight north or south to a pole, from centres at all latitudes.

    The latitude increment is then of order one, and the centre latitude plus it can round past
    +-math.pi / 2. The inverse must still return latitudes in [-pi/2, pi/2].
    """
    radius = BODY_RADII_M[body]
    jitter = np.array([0.0, 1e-9, -1e-9, 1e-6, -1e-6, 1e-3, -1e-3, 1.0, -1.0])
    for lat0 in np.linspace(-HALF_PI, HALF_PI, 81)[1:-1]:
        to_north = (HALF_PI - lat0) * radius + jitter
        to_south = -(HALF_PI + lat0) * radius - jitter
        y = np.concatenate([to_north, to_south])
        x = np.concatenate([jitter, jitter]) * 1e-3
        inside = np.hypot(x, y) <= PI * radius
        lat, lon = aeqd_inverse(
            x[inside], y[inside], lat0_rad=float(lat0), lon0_rad=0.5, radius_m=radius
        )
        assert np.all(np.abs(lat) <= HALF_PI)
        assert np.all(np.isfinite(lon))


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


# Earth centres just east of -pi, where the grid column at x = -25 m lies across the
# antimeridian. An inverse that turned the longitude by 2 * math.pi instead of the true 2 pi
# broke the metre allowance there against the true-sphere exact forward, in 168 of 400 random
# such centres (up to 1.19 of it). These three used 1.19, 1.14 and 1.19.
ANTIMERIDIAN_ROUND_TRIP_CENTRES = (
    (-0.011837329784017126, -3.1415899962182103),
    (0.04040611687057235, -3.14159228984276),
    (0.046406515416638275, -3.1415893941058504),
)


@pytest.mark.parametrize(("lat0", "lon0"), ANTIMERIDIAN_ROUND_TRIP_CENTRES)
def test_round_trip_in_windows_across_the_antimeridian(lat0: float, lon0: float) -> None:
    radius = BODY_RADII_M["earth"]
    x, y = story_grid()
    assert_grid_round_trip(x, y, lat0=lat0, lon0=lon0, radius=radius, exact=False)
    middle = GRID_SIZE // 2
    assert_grid_round_trip(
        x[:, middle], y[:, middle], lat0=lat0, lon0=lon0, radius=radius, exact=True
    )


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


# Float64 points in windows centred within 3.6 km of a pole, with one window coordinate near
# zero, where the forward used more than the PRD 7 allowance in metres against the exact
# forward of the same point. Found by random search over 1.6 million such points, made with
# the inverse from offsets near an axis. In the first three, y is near 0 and the north
# component cancelled two terms the size of the distance to the pole (about 2.3 km), each
# rounded in float64 (1.01 to 1.28 of the allowance). In the last four, x is near 0 on the far
# side of the pole, where the longitude difference is near pi and its float64 rounding moved x
# (1.00 to 1.14).
POLE_WINDOW_FORWARD_CASES = (
    ("mars", 1.570710853615426, -0.8427156657011828, 1.5700849164604616, -2.293074961503627),
    ("mars", 1.570750837979072, 0.3324403830824384, 1.5700593207005185, 1.8414763809954393),
    ("earth", 1.5707906137131071, 2.3674157549596337, 1.5704538732734863, -2.3616567934097894),
    ("mars", -1.5707848499783608, 1.0701547618635285, -1.5700748830794446, -2.071437891726265),
    ("mars", 1.5707878527140378, -1.5230723398079535, 1.5701081767882408, 1.6185203137818398),
    ("earth", 1.5707544735957888, -1.806008123688206, 1.570449753057113, 1.3355845299015874),
    ("moon", 1.57070528617204, 0.9340656968768715, 1.5695620602473925, -2.207526956712922),
)


@pytest.mark.parametrize(("body", "lat0", "lon0", "lat", "lon"), POLE_WINDOW_FORWARD_CASES)
def test_forward_near_an_axis_in_a_pole_window(
    body: str, lat0: float, lon0: float, lat: float, lon: float
) -> None:
    radius = BODY_RADII_M[body]
    x, y = aeqd_forward(lat, lon, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    x_ref, y_ref = exact_forward(np.array(lat), np.array(lon), lat0, lon0, radius)
    assert_metres_close(x, y, x_ref, y_ref)


# Points across the antimeridian from the centre, as (lat0, lon0, lat, lon), near the equator,
# at mid-latitudes and close to a pole, where the longitude difference lies beyond +-math.pi.
# The first is the counterexample of review finding codex2-r3-1: on the Earth sphere the points
# are 1.27 m apart, and a longitude difference reduced by 2 * math.pi instead of the true 2 pi
# moves x by 1.6e-9 m, 12 times the PRD 7 allowance there.
ANTIMERIDIAN_FORWARD_CASES = (
    (0.0, -PI + 1e-7, 0.0, PI - 1e-7),
    (0.0, PI - 1e-7, 0.0, -PI + 1e-7),
    (0.0, -PI, 1e-9, math.nextafter(PI, 0.0)),
    (0.0, math.nextafter(PI, 0.0), -1e-9, -PI),
    (0.0, -PI, 0.0, PI),
    (-0.9, -PI + 3e-8, -0.9 + 2e-10, PI - 5e-8),
    (0.9, 3.141592, 0.9 - 1e-12, -3.141592),
    (1.5707, -PI + 2e-5, 1.5706, PI - 4e-5),
    (-1.5706, PI - 1e-6, -1.57065, -PI + 1e-6),
)


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize(("lat0", "lon0", "lat", "lon"), ANTIMERIDIAN_FORWARD_CASES)
def test_forward_across_the_antimeridian_uses_the_true_period(
    body: str, lat0: float, lon0: float, lat: float, lon: float
) -> None:
    radius = BODY_RADII_M[body]
    x, y = aeqd_forward(lat, lon, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    x_ref, y_ref = exact_forward(np.array(lat), np.array(lon), lat0, lon0, radius)
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


def nearest_in_range_longitude(lam: Any) -> float:
    """The float64 in [-math.pi, math.pi) nearest the real angle ``lam`` on the true circle.

    ``lam`` is an mpmath number. The range is 2.4e-16 rad short of a full turn, so next to the
    antimeridian the nearest value can be ``-math.pi`` or the largest float64 below math.pi.
    Call it inside ``mpmath.workdps``.
    """
    two_pi = 2 * mpmath.pi
    candidates = {-PI, math.nextafter(PI, 0.0)}
    for turns in (-1, 0, 1):
        value = float(lam + turns * two_pi)
        for candidate in (math.nextafter(value, -math.inf), value, math.nextafter(value, math.inf)):
            if -PI <= candidate < PI:
                candidates.add(candidate)

    def distance(candidate: float) -> Any:
        gap = mpmath.mpf(candidate) - lam
        return abs(gap - two_pi * mpmath.nint(gap / two_pi))

    return min(candidates, key=distance)


# Window centres at and next to the antimeridian, and east-west offsets small enough that the
# float64 rounding of the returned longitude, not the accuracy of the increment, decides it.
ANTIMERIDIAN_CENTRE_LONGITUDES = (
    -PI,
    math.nextafter(-PI, 0.0),
    -PI + 1e-9,
    math.nextafter(PI, 0.0),
    math.nextafter(math.nextafter(PI, 0.0), 0.0),
    PI - 1e-9,
)
ANTIMERIDIAN_OFFSETS_M = (1e-10, 3e-10, 1e-9, 2e-9, 2.5e-9, 7e-9, 1e-6, 1e-3, 0.4, 13.9, 25.0)


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("lat0", [0.0, 0.8, -1.3])
@pytest.mark.parametrize("lon0", ANTIMERIDIAN_CENTRE_LONGITUDES)
def test_inverse_longitude_is_the_nearest_in_range_value_on_the_true_circle(
    body: str, lat0: float, lon0: float
) -> None:
    """Across the antimeridian the inverse returns the in-range float64 nearest the exact
    longitude, turning by the true 2 pi, not by 2 * math.pi."""
    radius = BODY_RADII_M[body]
    offsets = np.array(ANTIMERIDIAN_OFFSETS_M)
    x = np.concatenate([offsets, -offsets])
    y = np.zeros_like(x)
    _, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    with mpmath.workdps(REFERENCE_DIGITS):
        axes = centre_axes(lat0)
        r = mpmath.mpf(radius)
        expected = []
        for offset in x:
            _, dlam = exact_point_mp(mpmath.mpf(float(offset)), mpmath.mpf(0), axes, r)
            expected.append(nearest_in_range_longitude(mpmath.mpf(lon0) + dlam))
    assert lon.tolist() == expected


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
@example(
    body="earth",
    lat0=ANTIMERIDIAN_ROUND_TRIP_CENTRES[0][0],
    lon0=ANTIMERIDIAN_ROUND_TRIP_CENTRES[0][1],
    pixels=[(27, 27), (1, 27), (54, 27)],
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
# Continuous offsets, one direction at a time, against the exact references


# Offsets (x, y) anywhere in the 5 km window. The float strategy also draws zeros of both signs,
# subnormal and tiny components, so offsets near an axis and near the centre are included.
WINDOW_OFFSETS = st.lists(
    st.tuples(st.floats(-HALF_WINDOW_M, HALF_WINDOW_M), st.floats(-HALF_WINDOW_M, HALF_WINDOW_M)),
    min_size=1,
    max_size=16,
)


# Offsets in windows centred within 3.6 km of a pole, near an axis, from the random search behind
# POLE_WINDOW_FORWARD_CASES.
POLE_WINDOW_CENTRE = ("mars", 1.570710853615426, -0.8427156657011828)
POLE_WINDOW_OFFSETS = [
    (-2398.5830825075955, -1.8012333257273263e-06),
    (2498.24057541666, -2.290330149218011e-11),
    (-8.340917595628587e-13, -2489.1373815315906),
    (-4.78632402549149e-13, 2365.8677597764836),
    (9.929872235192163e-13, 2302.5886777845335),
]


def stepped_points(
    steps: list[tuple[float, float]], lat0: float, lon0: float, radius: float
) -> tuple[Array, Array]:
    """Float64 points stepped from the centre by (north, east) metres, built without the inverse.

    Each step moves along the coordinate lines: the latitude by north / radius, clipped to
    [-pi/2, pi/2], and the longitude by east / radius, wrapped into [-pi, pi].
    """
    north = np.array([step[0] for step in steps])
    east = np.array([step[1] for step in steps])
    lat = np.clip(lat0 + north / radius, -HALF_PI, HALF_PI)
    lon = lon0 + east / radius
    lon = np.where(lon > PI, lon - 2 * PI, np.where(lon < -PI, lon + 2 * PI, lon))
    return lat, lon


@settings(max_examples=400, deadline=None)
@given(
    body=st.sampled_from(BODIES),
    lat0=st.floats(-HALF_PI, HALF_PI),
    lon0=st.floats(-PI, PI, exclude_max=True),
    offsets=WINDOW_OFFSETS,
)
@example(body="moon", lat0=-HALF_PI, lon0=-PI, offsets=EDGE_OFFSETS)
@example(body="earth", lat0=HALF_PI, lon0=0.0, offsets=EDGE_OFFSETS)
@example(body="earth", lat0=0.0, lon0=-PI, offsets=EDGE_OFFSETS)
@example(body="earth", lat0=0.0, lon0=math.nextafter(PI, 0.0), offsets=EDGE_OFFSETS)
@example(body="mars", lat0=math.radians(89.99), lon0=2.0, offsets=EDGE_OFFSETS)
@example(body="earth", lat0=1.0233922226196999, lon0=-2.3583296722334133, offsets=EDGE_OFFSETS)
@example(body="moon", lat0=1e-300, lon0=-1e-300, offsets=EDGE_OFFSETS)
@example(
    body=POLE_WINDOW_CENTRE[0],
    lat0=POLE_WINDOW_CENTRE[1],
    lon0=POLE_WINDOW_CENTRE[2],
    offsets=POLE_WINDOW_OFFSETS,
)
def test_inverse_matches_the_exact_inverse_at_continuous_offsets(
    body: str, lat0: float, lon0: float, offsets: list[tuple[float, float]]
) -> None:
    """The inverse alone, at random centres and continuous offsets anywhere in the window.

    The module's (latitude, longitude) must match the exact inverse of the same float64 offset
    to PRD 7 tolerances in radians, as values and as differences from the centre, with the
    longitude compared on the same meridian (:func:`assert_angles_close`).
    """
    radius = BODY_RADII_M[body]
    x = np.array([offset[0] for offset in offsets])
    y = np.array([offset[1] for offset in offsets])
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    assert lat.dtype == lon.dtype == np.float64
    assert np.all((lon >= -PI) & (lon < PI))
    assert np.all(np.abs(lat) <= HALF_PI)

    lat_ref, lon_ref, dlat_ref, dlon_ref = exact_inverse(x, y, lat0, lon0, radius)
    np.testing.assert_allclose(lat, lat_ref, rtol=RTOL, atol=ATOL)
    assert_angles_close(lon, lon_ref)
    np.testing.assert_allclose(lat - lat0, dlat_ref, rtol=RTOL, atol=ATOL)
    assert_angles_close(lon - lon0, dlon_ref)


@settings(max_examples=400, deadline=None)
@given(
    body=st.sampled_from(BODIES),
    lat0=st.floats(-HALF_PI, HALF_PI),
    lon0=st.floats(-PI, PI, exclude_max=True),
    offsets=WINDOW_OFFSETS,
    steps=WINDOW_OFFSETS,
)
@example(body="moon", lat0=-HALF_PI, lon0=-PI, offsets=EDGE_OFFSETS, steps=EDGE_OFFSETS)
@example(body="earth", lat0=HALF_PI, lon0=0.0, offsets=EDGE_OFFSETS, steps=EDGE_OFFSETS)
@example(body="earth", lat0=0.0, lon0=-PI, offsets=EDGE_OFFSETS, steps=EDGE_OFFSETS)
@example(
    body="earth",
    lat0=0.0,
    lon0=math.nextafter(PI, 0.0),
    offsets=EDGE_OFFSETS,
    steps=EDGE_OFFSETS,
)
@example(body="mars", lat0=math.radians(89.99), lon0=2.0, offsets=EDGE_OFFSETS, steps=EDGE_OFFSETS)
@example(
    body="earth",
    lat0=1.0233922226196999,
    lon0=-2.3583296722334133,
    offsets=EDGE_OFFSETS,
    steps=EDGE_OFFSETS,
)
@example(body="moon", lat0=1e-300, lon0=-1e-300, offsets=EDGE_OFFSETS, steps=EDGE_OFFSETS)
@example(
    body=POLE_WINDOW_CENTRE[0],
    lat0=POLE_WINDOW_CENTRE[1],
    lon0=POLE_WINDOW_CENTRE[2],
    offsets=POLE_WINDOW_OFFSETS,
    steps=EDGE_OFFSETS,
)
def test_forward_matches_the_exact_forward_at_continuous_offsets(
    body: str,
    lat0: float,
    lon0: float,
    offsets: list[tuple[float, float]],
    steps: list[tuple[float, float]],
) -> None:
    """The forward alone, at float64 points anywhere in the window around random centres.

    The points are the module inverse of continuous offsets, which are the float64 points the
    forward meets after the inverse, and points stepped from the centre without the inverse
    (:func:`stepped_points`). The module's (x, y) must match the exact forward of the same
    float64 point to PRD 7 tolerances in plain metres.
    """
    radius = BODY_RADII_M[body]
    x = np.array([offset[0] for offset in offsets])
    y = np.array([offset[1] for offset in offsets])
    lat_inv, lon_inv = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    lat_step, lon_step = stepped_points(steps, lat0, lon0, radius)
    lat = np.concatenate([lat_inv, lat_step])
    lon = np.concatenate([lon_inv, lon_step])

    x_out, y_out = aeqd_forward(lat, lon, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    assert x_out.dtype == y_out.dtype == np.float64
    x_ref, y_ref = exact_forward(lat, lon, lat0, lon0, radius)
    assert_metres_close(x_out, y_out, x_ref, y_ref)


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
