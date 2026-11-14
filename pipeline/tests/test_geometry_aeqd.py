"""Tests for the D29 window grid and the spherical AEQD (PRD 7, "Window resampler (D29)").

Units and conventions used throughout:

- Window coordinates x (east) and y (north) are metres on the body sphere, measured from the
  window centre. Angles are radians. The radii are the D29 spheres from ``BODY_RADII_M``.
- Longitudes returned by the inverse lie in the half-open range [-pi, pi), where pi is the
  float64 ``math.pi``. A window centre must already be in that range.
- PRD 7 Tolerances (rtol 1e-10, atol 1e-12) apply to normalised float64 values. Round trips
  compare window coordinates normalised by the window width W: u = x / W and v = y / W, both in
  [-0.5, 0.5]. At W = 5000 m, atol 1e-12 is 5e-9 m. In plain metres atol 1e-12 m cannot be met
  for small offsets by any float64 (latitude, longitude) pair: a longitude near +-pi is
  resolved only to half an ulp of pi, 2.2e-16 rad, which is 1.4e-9 m on the Earth sphere.
- Distances from the window centre are checked against rho to 1e-9 relative (PRD 7 Tests).

The reference formulas here are written independently of the implementation: the grid with
explicit loops, a forward AEQD from 3-D unit vectors, and the great-circle distance by the
haversine formula.
"""

from __future__ import annotations

import ast
import math
import subprocess
import sys
from pathlib import Path

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


# ---------------------------------------------------------------------------------------------
# Independent reference formulas


def reference_forward(
    lat: Array, lon: Array, lat0: float, lon0: float, radius: float
) -> tuple[Array, Array]:
    """Forward AEQD from 3-D unit vectors, written independently of the implementation.

    The point is projected onto the local east, north and up axes of the centre. The azimuthal
    direction is (east, north) normalised and the distance is radius * atan2(|horizontal|, up).
    """
    lat, lon = np.broadcast_arrays(lat, lon)
    point = np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    up = np.array(
        [math.cos(lat0) * math.cos(lon0), math.cos(lat0) * math.sin(lon0), math.sin(lat0)]
    )
    east = np.array([-math.sin(lon0), math.cos(lon0), 0.0])
    north = np.array(
        [-math.sin(lat0) * math.cos(lon0), -math.sin(lat0) * math.sin(lon0), math.cos(lat0)]
    )
    e = np.tensordot(east, point, axes=1)
    n = np.tensordot(north, point, axes=1)
    u = np.tensordot(up, point, axes=1)
    horizontal = np.hypot(e, n)
    angle = np.arctan2(horizontal, u)
    scale = np.divide(angle, horizontal, out=np.zeros_like(angle), where=horizontal > 0)
    return radius * scale * e, radius * scale * n


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


def assert_window_round_trip(
    x: Array, y: Array, x_back: Array, y_back: Array, width: float = WINDOW_M
) -> None:
    np.testing.assert_allclose(x_back / width, x / width, rtol=RTOL, atol=ATOL)
    np.testing.assert_allclose(y_back / width, y / width, rtol=RTOL, atol=ATOL)


def story_grid() -> tuple[Array, Array]:
    return window_grid(WINDOW_M, GRID_M).mesh_m()


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
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)

    x_back, y_back = aeqd_forward(lat, lon, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    assert x_back.dtype == y_back.dtype == np.float64
    assert_window_round_trip(x, y, x_back, y_back)

    x_ref, y_ref = reference_forward(lat, lon, lat0, lon0, radius)
    assert_window_round_trip(x, y, x_ref, y_ref)


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("lat0", POLES_RAD)
@pytest.mark.parametrize("lon0", [-PI, 0.0, 2.0])
def test_round_trip_on_a_pole_centred_window(body: str, lat0: float, lon0: float) -> None:
    radius = BODY_RADII_M[body]
    x, y = story_grid()
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    x_back, y_back = aeqd_forward(lat, lon, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    assert_window_round_trip(x, y, x_back, y_back)
    x_ref, y_ref = reference_forward(lat, lon, lat0, lon0, radius)
    assert_window_round_trip(x, y, x_ref, y_ref)


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("lat0_deg", STORY_LATITUDES_DEG)
def test_forward_matches_the_vector_reference(body: str, lat0_deg: float) -> None:
    radius = BODY_RADII_M[body]
    lat0 = math.radians(lat0_deg)
    lon0 = 0.65449846949787
    # Points up to about 4 km away in every direction, built without the inverse.
    angle = np.linspace(-PI, PI, 37)
    reach = 4000.0 / radius
    lat = np.clip(lat0 + reach * np.sin(angle), -HALF_PI, HALF_PI)
    lon = lon0 + reach * np.cos(angle)
    x, y = aeqd_forward(lat, lon, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    x_ref, y_ref = reference_forward(lat, lon, lat0, lon0, radius)
    assert_window_round_trip(x_ref, y_ref, x, y)


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


@settings(max_examples=400, deadline=None)
@given(
    body=st.sampled_from(BODIES),
    lat0=st.floats(-HALF_PI, HALF_PI),
    lon0=st.floats(-PI, PI, exclude_max=True),
    u=st.lists(st.floats(-0.5, 0.5), min_size=1, max_size=16),
    v=st.lists(st.floats(-0.5, 0.5), min_size=1, max_size=16),
)
@example(body="moon", lat0=-HALF_PI, lon0=-PI, u=[0.0, 0.5, -0.5], v=[0.0, -0.5, 0.5])
@example(body="earth", lat0=HALF_PI, lon0=0.0, u=[1e-12, -0.5], v=[0.0, 1e-300])
@example(body="earth", lat0=0.0, lon0=-PI, u=[-0.5, 1e-9, 0.5], v=[0.0, 0.0, 0.0])
@example(body="mars", lat0=math.radians(89.99), lon0=math.nextafter(PI, 0.0), u=[0.1], v=[0.5])
def test_round_trip_property(
    body: str, lat0: float, lon0: float, u: list[float], v: list[float]
) -> None:
    """Random centres anywhere on the sphere, random offsets within the 5 km window.

    Longitudes come back in [-pi, pi) and latitudes in [-pi/2, pi/2]; window coordinates
    survive inverse-then-forward to PRD 7 tolerances on x / W and y / W.
    """
    radius = BODY_RADII_M[body]
    size = min(len(u), len(v))
    x = np.array(u[:size]) * WINDOW_M
    y = np.array(v[:size]) * WINDOW_M
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    assert np.all((lon >= -PI) & (lon < PI))
    assert np.all(np.abs(lat) <= HALF_PI)

    x_back, y_back = aeqd_forward(lat, lon, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    assert_window_round_trip(x, y, x_back, y_back)
    x_ref, y_ref = reference_forward(lat, lon, lat0, lon0, radius)
    assert_window_round_trip(x, y, x_ref, y_ref)


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
