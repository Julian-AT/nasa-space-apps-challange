"""Tests for the D29 source lookups and bilinear resampler (PRD 7, "Window resampler (D29)").

Units and conventions used throughout:

- A source raster is a 2-D array indexed [row, column], row 0 at the top. Its georeferencing is
  a north-up affine geotransform as GDAL and rasterio report it: the origin is the outer corner
  of pixel (row 0, column 0), and the pixel sizes are signed (the height is negative for a
  north-up raster). Every parameter is an explicit argument; none has a default, and none is
  read from a product.
- Pixel coordinates are fractional indices of pixel centres: (column, row) = (0, 0) is the
  centre of the top-left pixel, so the outer corner of the raster sits at (-0.5, -0.5).
- Each lookup form is a closed-form projection followed by that geotransform. ``project`` maps
  (latitude, longitude) in radians to projected coordinates in the raster's own units: degrees
  for the geographic form, and metres from the natural origin, before false easting and
  northing, for the polar stereographic and equirectangular forms on the sphere.
  ``pixel_from_projected`` applies false easting and northing and the geotransform.
  ``to_pixel`` is their composition, ``unproject`` and ``projected_from_pixel`` the inverses,
  and ``from_pixel`` the inverse composition.
- Each step is checked one direction at a time against an independently written 40-digit
  mpmath reference of the same float64 inputs, at PRD 7 tolerances (rtol 1e-10, atol 1e-12)
  in its own units: projected coordinates in degrees or metres, angles in radians and pixel
  coordinates in pixels. The compositions are checked bitwise against their steps rather than
  at atol 1e-12 pixels: a composition carries the float64 rounding of the projected coordinate,
  half an ulp of it over the pixel size, which for a polar raster reaching 3e5 m from the pole
  at 20 m pixels is 1.5e-12 pixels before any arithmetic is done.
- The bilinear resampler is checked against an explicit four-neighbour loop written here, on
  normalised values (|z| <= 100), at the same tolerances. Its masks are checked exactly.
- Plane tests. (1) A plane in pixel coordinates resamples exactly through each lookup form on
  each body sphere, on the 5 km, 90 m analysis grid. (2) A plane in the window x-y frame,
  sampled on an equirectangular source, stays a plane in that frame. The window frame is curved
  in the source frame, so bilinear interpolation leaves a curvature residual, whose bound is
  derived in :func:`curvature_bound` and stated with each test. At the small extent (a 500 m
  window and 1 m source pixels, centred on the equator) the bound is below half of atol, so
  the best-fit residual is held to PRD 7 rtol 1e-10, atol 1e-12 alone. At the 5 km D29 extent
  the stated bound is the allowance, and the test shows that the residual stays under it.
"""

from __future__ import annotations

import dataclasses
import functools
import math
from collections.abc import Callable
from typing import Any

import mpmath  # type: ignore[import-untyped]
import numpy as np
import numpy.typing as npt
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from hypothesis.extra import numpy as hnp

from terraluna_pipeline.config import load_terrain_config
from terraluna_pipeline.geometry import (
    BODY_RADII_M,
    EquirectangularLookup,
    GeographicLookup,
    PolarStereographicLookup,
    Pole,
    ResampledGrid,
    SourceLookupError,
    UnknownBodyError,
    WindowGrid,
    aeqd_inverse,
    bilinear_resample,
    resample_window,
    window_grid,
)

pytestmark = pytest.mark.filterwarnings("error")

Array = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]
Lookup = GeographicLookup | PolarStereographicLookup | EquirectangularLookup

RTOL = 1e-10
ATOL = 1e-12

PI = math.pi
HALF_PI = math.pi / 2
BODIES = tuple(BODY_RADII_M)

TERRAIN = load_terrain_config()
ANALYSIS_GRID = window_grid(TERRAIN.window_m, TERRAIN.analysis_grid_m)

# Significant digits of the exact references. Float64 carries about 16.
DIGITS = 40
with mpmath.workdps(DIGITS):
    # The true 2 pi minus the float64 2 * math.pi, about 2.449e-16 rad, rounded once.
    TWO_PI_RESIDUAL = float(2 * mpmath.pi - 2 * mpmath.mpf(PI))


# ---------------------------------------------------------------------------------------------
# Comparisons


def assert_close(actual: Any, expected: Any) -> None:
    """PRD 7 tolerances in the quantity's own units: rtol 1e-10, atol 1e-12."""
    np.testing.assert_allclose(actual, expected, rtol=RTOL, atol=ATOL)


def assert_angles_close(actual: npt.ArrayLike, expected: npt.ArrayLike) -> None:
    """PRD 7 tolerances on angles in radians, comparing the meridians they name.

    Each actual angle is moved by the whole number of true 2 pi turns that brings it nearest the
    expected one, adding a turn as 2 * math.pi plus ``TWO_PI_RESIDUAL``.
    """
    got = np.asarray(actual, dtype=np.float64)
    want = np.asarray(expected, dtype=np.float64)
    turns = np.round((want - got) / (2 * PI))
    moved = (got + turns * (2 * PI)) + turns * TWO_PI_RESIDUAL
    np.testing.assert_allclose(moved, want, rtol=RTOL, atol=ATOL)


def assert_same_bits(actual: npt.ArrayLike, expected: npt.ArrayLike) -> None:
    np.testing.assert_array_equal(np.asarray(actual), np.asarray(expected), strict=True)


# ---------------------------------------------------------------------------------------------
# Independent reference formulas, at 40 digits. Every float64 input is taken at its exact binary
# value, and only the results are rounded to float64.


def mp(value: float) -> Any:
    return mpmath.mpf(float(value))


def mp_radians(degrees: float) -> Any:
    return mp(degrees) * mpmath.pi / 180


def reduce_angle(angle: Any) -> Any:
    """The representative of an angle in [-pi, pi), moved by whole turns of the true 2 pi."""
    turn = 2 * mpmath.pi
    return angle - turn * mpmath.floor((angle + mpmath.pi) / turn)


Pair = tuple[Any, Any]


def exact_map(
    fn: Callable[[Any, Any], Pair], a: npt.ArrayLike, b: npt.ArrayLike
) -> tuple[Array, Array]:
    """Apply ``fn`` to each pair of exact inputs at 40 digits and round each result once."""
    first, second = np.broadcast_arrays(np.asarray(a, np.float64), np.asarray(b, np.float64))
    out_a = np.empty(first.shape)
    out_b = np.empty(first.shape)
    with mpmath.workdps(DIGITS):
        for index in np.ndindex(first.shape):
            p, q = fn(mp(float(first[index])), mp(float(second[index])))
            out_a[index] = float(p)
            out_b[index] = float(q)
    return out_a, out_b


def geographic_project_mp(phi: Any, lam: Any) -> Pair:
    """Geographic projected coordinates: (longitude, latitude) in degrees."""
    return lam * 180 / mpmath.pi, phi * 180 / mpmath.pi


def geographic_unproject_mp(lon_deg: Any, lat_deg: Any) -> Pair:
    return lat_deg * mpmath.pi / 180, reduce_angle(lon_deg * mpmath.pi / 180)


def geographic_affine_mp(lookup: GeographicLookup, lon_deg: Any, lat_deg: Any) -> Pair:
    """Pixel coordinates in a geographic raster. Columns run east from the origin meridian.

    The difference and its reduction to [0, 360) are exact (``exact=True``), so that a tiny
    longitude next to a multiple of 360 lands on the correct side of the wrap.
    """
    east = mpmath.fsub(lon_deg, mp(lookup.origin_lon_deg), exact=True)
    turns = mpmath.floor(east / 360)
    while mpmath.fsub(east, 360 * turns, exact=True) < 0:
        turns -= 1
    while mpmath.fsub(east, 360 * turns, exact=True) >= 360:
        turns += 1
    east = mpmath.fsub(east, 360 * turns, exact=True)
    half = mpmath.mpf(1) / 2
    column = east / mp(lookup.pixel_width_deg) - half
    row = (lat_deg - mp(lookup.origin_lat_deg)) / mp(lookup.pixel_height_deg) - half
    return column, row


def exact_sum(*terms: Any) -> Any:
    """The exact sum of mpmath numbers, with no rounding."""
    total = mpmath.mpf(0)
    for term in terms:
        total = mpmath.fadd(total, term, exact=True)
    return total


def exact_cell(index: Any, size: float) -> Any:
    """(index + 0.5) * size, exactly."""
    return mpmath.fmul(exact_sum(index, mpmath.mpf(0.5)), mp(size), exact=True)


def geographic_inverse_affine_mp(lookup: GeographicLookup, column: Any, row: Any) -> Pair:
    lon_deg = exact_sum(exact_cell(column, lookup.pixel_width_deg), mp(lookup.origin_lon_deg))
    lat_deg = exact_sum(exact_cell(row, lookup.pixel_height_deg), mp(lookup.origin_lat_deg))
    return lon_deg, lat_deg


def polar_scale_mp(lookup: PolarStereographicLookup) -> Any:
    """Scale k0 at the pole, from the condition that the scale is true on the parallel lat_ts.

    On the projection from the opposite pole, a parallel at latitude phi has map radius
    R k0 t cos(phi), with t = 2 / (1 +- sin phi); true scale needs R cos(phi), so k0 = 1 / t.
    With lat_ts at the pole the scale factor itself is k0.
    """
    if abs(lookup.latitude_of_true_scale_deg) == 90.0:
        return mp(lookup.scale_factor)
    phi_ts = abs(mp_radians(lookup.latitude_of_true_scale_deg))
    return 1 / (2 / (1 + mpmath.sin(phi_ts)))


def polar_project_mp(lookup: PolarStereographicLookup, phi: Any, lam: Any) -> Pair:
    """Stereographic projection of the unit vector from the opposite pole, scaled by R k0.

    The frame is turned so that the central meridian is its first axis. For the north aspect
    the plane touches the north pole and the central meridian points down the map (-y); for
    the south aspect it touches the south pole and the central meridian points up (+y).
    """
    # Next to the opposite pole 1 +- sin(phi) cancels to about 1e-32, so the projection is
    # evaluated at 100 digits there to keep 40 in the result.
    with mpmath.workdps(100):
        scale = mp(lookup.radius_m) * polar_scale_mp(lookup)
        dlam = lam - mp_radians(lookup.central_meridian_deg)
        along = mpmath.cos(phi) * mpmath.cos(dlam)
        across = mpmath.cos(phi) * mpmath.sin(dlam)
        z = mpmath.sin(phi)
        if lookup.pole == "north":
            t = 2 / (1 + z)
            return scale * t * across, -scale * t * along
        t = 2 / (1 - z)
        return scale * t * across, scale * t * along


def polar_unproject_mp(lookup: PolarStereographicLookup, easting: Any, northing: Any) -> Pair:
    """Back-projection onto the sphere: sin phi = (4 - r^2) / (4 + r^2), r = rho / (R k0)."""
    scale = mp(lookup.radius_m) * polar_scale_mp(lookup)
    r = mpmath.sqrt(easting * easting + northing * northing) / scale
    lam0 = mp_radians(lookup.central_meridian_deg)
    phi = mpmath.atan2(4 - r * r, 4 * r)
    if lookup.pole == "north":
        return phi, reduce_angle(lam0 + mpmath.atan2(easting, -northing))
    return -phi, reduce_angle(lam0 + mpmath.atan2(easting, northing))


def equirectangular_project_mp(lookup: EquirectangularLookup, phi: Any, lam: Any) -> Pair:
    radius = mp(lookup.radius_m)
    dlam = reduce_angle(lam - mp_radians(lookup.center_longitude_deg))
    easting = radius * mpmath.cos(mp_radians(lookup.standard_parallel_deg)) * dlam
    northing = radius * (phi - mp_radians(lookup.latitude_of_origin_deg))
    return easting, northing


def equirectangular_unproject_mp(
    lookup: EquirectangularLookup, easting: Any, northing: Any
) -> Pair:
    radius = mp(lookup.radius_m)
    scale = radius * mpmath.cos(mp_radians(lookup.standard_parallel_deg))
    lam = reduce_angle(mp_radians(lookup.center_longitude_deg) + easting / scale)
    return mp_radians(lookup.latitude_of_origin_deg) + northing / radius, lam


ProjectedLookup = PolarStereographicLookup | EquirectangularLookup


def projected_affine_mp(lookup: ProjectedLookup, easting: Any, northing: Any) -> Pair:
    half = mpmath.mpf(1) / 2
    east = exact_sum(easting, mp(lookup.false_easting_m), -mp(lookup.origin_x_m))
    north = exact_sum(northing, mp(lookup.false_northing_m), -mp(lookup.origin_y_m))
    return east / mp(lookup.pixel_width_m) - half, north / mp(lookup.pixel_height_m) - half


def projected_inverse_affine_mp(lookup: ProjectedLookup, column: Any, row: Any) -> Pair:
    easting = exact_sum(
        exact_cell(column, lookup.pixel_width_m),
        mp(lookup.origin_x_m),
        -mp(lookup.false_easting_m),
    )
    northing = exact_sum(
        exact_cell(row, lookup.pixel_height_m),
        mp(lookup.origin_y_m),
        -mp(lookup.false_northing_m),
    )
    return easting, northing


def project_mp(lookup: Lookup, phi: Any, lam: Any) -> Pair:
    if isinstance(lookup, GeographicLookup):
        return geographic_project_mp(phi, lam)
    if isinstance(lookup, PolarStereographicLookup):
        return polar_project_mp(lookup, phi, lam)
    return equirectangular_project_mp(lookup, phi, lam)


def affine_mp(lookup: Lookup, first: Any, second: Any) -> Pair:
    if isinstance(lookup, GeographicLookup):
        return geographic_affine_mp(lookup, first, second)
    return projected_affine_mp(lookup, first, second)


def window_point_mp(x: Any, y: Any, lat0: float, radius: float) -> Pair:
    """Inverse AEQD of one window offset on the true sphere: (phi, lam - lam0), from vectors.

    The body frame is turned so that the centre meridian lies in its x-z plane. The point is
    the great circle from the centre at azimuth (x, y) / rho, a distance rho = hypot(x, y)
    along it.
    """
    phi0 = mp(lat0)
    rho = mpmath.sqrt(x * x + y * y)
    if rho == 0:
        return phi0, mpmath.mpf(0)
    c = rho / mp(radius)
    up = (mpmath.cos(phi0), mpmath.mpf(0), mpmath.sin(phi0))
    east = (mpmath.mpf(0), mpmath.mpf(1), mpmath.mpf(0))
    north = (-mpmath.sin(phi0), mpmath.mpf(0), mpmath.cos(phi0))
    point = [
        mpmath.cos(c) * u + mpmath.sin(c) * (x * e + y * n) / rho
        for u, e, n in zip(up, east, north, strict=True)
    ]
    phi = mpmath.atan2(point[2], mpmath.sqrt(point[0] ** 2 + point[1] ** 2))
    return phi, mpmath.atan2(point[1], point[0])


def exact_window_pixels(
    lookup: Lookup, x: Array, y: Array, lat0: float, lon0: float, radius: float
) -> tuple[Array, Array]:
    """Pixel coordinates of window offsets: exact AEQD inverse, projection and geotransform."""
    column = np.empty(x.shape)
    row = np.empty(x.shape)
    with mpmath.workdps(DIGITS):
        for index in np.ndindex(x.shape):
            phi, dlam = window_point_mp(mp(float(x[index])), mp(float(y[index])), lat0, radius)
            first, second = project_mp(lookup, phi, mp(lon0) + dlam)
            c, r = affine_mp(lookup, first, second)
            column[index] = float(c)
            row[index] = float(r)
    return column, row


# ---------------------------------------------------------------------------------------------
# Lookup parameters are explicit


GEOGRAPHIC: dict[str, Any] = {
    "origin_lon_deg": 77.45,
    "origin_lat_deg": 18.55,
    "pixel_width_deg": 1 / 1200,
    "pixel_height_deg": -1 / 1200,
}
POLAR: dict[str, Any] = {
    "pole": "south",
    "central_meridian_deg": 0.0,
    "latitude_of_true_scale_deg": -90.0,
    "scale_factor": 1.0,
    "radius_m": 1737400.0,
    "false_easting_m": 0.0,
    "false_northing_m": 0.0,
    "origin_x_m": -20000.0,
    "origin_y_m": 20000.0,
    "pixel_width_m": 20.0,
    "pixel_height_m": -20.0,
}
EQUIRECTANGULAR: dict[str, Any] = {
    "center_longitude_deg": 77.5,
    "standard_parallel_deg": 18.4,
    "latitude_of_origin_deg": 0.0,
    "radius_m": 3396190.0,
    "false_easting_m": 0.0,
    "false_northing_m": 0.0,
    "origin_x_m": -6000.0,
    "origin_y_m": 1100000.0,
    "pixel_width_m": 20.0,
    "pixel_height_m": -20.0,
}
LOOKUP_PARAMETERS: dict[type[Lookup], dict[str, Any]] = {
    GeographicLookup: GEOGRAPHIC,
    PolarStereographicLookup: POLAR,
    EquirectangularLookup: EQUIRECTANGULAR,
}
LOOKUP_FIELDS = [(cls, name) for cls, params in LOOKUP_PARAMETERS.items() for name in params]


def field_id(case: Any) -> str:
    return case.__name__ if isinstance(case, type) else str(case)


def test_lookups_declare_exactly_the_georeferencing_parameters_without_defaults() -> None:
    for cls, params in LOOKUP_PARAMETERS.items():
        fields = dataclasses.fields(cls)
        assert [f.name for f in fields] == list(params), cls.__name__
        for f in fields:
            assert f.default is dataclasses.MISSING, (cls.__name__, f.name)
            assert f.default_factory is dataclasses.MISSING, (cls.__name__, f.name)
            assert f.kw_only, (cls.__name__, f.name)


@pytest.mark.parametrize(("cls", "name"), LOOKUP_FIELDS, ids=field_id)
def test_a_missing_georeferencing_parameter_raises(cls: type[Lookup], name: str) -> None:
    params = {key: value for key, value in LOOKUP_PARAMETERS[cls].items() if key != name}
    with pytest.raises(TypeError, match=name):
        cls(**params)


@pytest.mark.parametrize(("cls", "name"), LOOKUP_FIELDS, ids=field_id)
def test_a_parameter_given_as_none_raises(cls: type[Lookup], name: str) -> None:
    params = {**LOOKUP_PARAMETERS[cls], name: None}
    with pytest.raises(SourceLookupError, match=name):
        cls(**params)


@pytest.mark.parametrize(
    ("cls", "name", "value"),
    [
        (GeographicLookup, "origin_lon_deg", math.nan),
        (GeographicLookup, "origin_lon_deg", 400.0),
        (GeographicLookup, "origin_lat_deg", math.inf),
        (GeographicLookup, "pixel_width_deg", 0.0),
        (GeographicLookup, "pixel_width_deg", -1 / 1200),
        (GeographicLookup, "pixel_height_deg", 0.0),
        (GeographicLookup, "pixel_height_deg", True),
        (GeographicLookup, "pixel_height_deg", "0.001"),
        (PolarStereographicLookup, "pole", "North"),
        (PolarStereographicLookup, "pole", "up"),
        (PolarStereographicLookup, "central_meridian_deg", -400.0),
        (PolarStereographicLookup, "latitude_of_true_scale_deg", 80.0),
        (PolarStereographicLookup, "latitude_of_true_scale_deg", 0.0),
        (PolarStereographicLookup, "latitude_of_true_scale_deg", -91.0),
        (PolarStereographicLookup, "scale_factor", 0.0),
        (PolarStereographicLookup, "scale_factor", -1.0),
        (PolarStereographicLookup, "radius_m", 0.0),
        (PolarStereographicLookup, "radius_m", math.inf),
        (PolarStereographicLookup, "false_easting_m", math.nan),
        (PolarStereographicLookup, "pixel_width_m", 0.0),
        (PolarStereographicLookup, "pixel_height_m", -0.0),
        (EquirectangularLookup, "center_longitude_deg", 361.0),
        (EquirectangularLookup, "standard_parallel_deg", 90.0),
        (EquirectangularLookup, "standard_parallel_deg", -90.0),
        (EquirectangularLookup, "latitude_of_origin_deg", 90.5),
        (EquirectangularLookup, "radius_m", -3396190.0),
        (EquirectangularLookup, "false_northing_m", -math.inf),
        (EquirectangularLookup, "origin_y_m", False),
        (EquirectangularLookup, "pixel_width_m", math.nan),
    ],
    ids=field_id,
)
def test_an_invalid_parameter_raises(cls: type[Lookup], name: str, value: Any) -> None:
    params = {**LOOKUP_PARAMETERS[cls], name: value}
    with pytest.raises(SourceLookupError, match=name):
        cls(**params)


def test_a_scale_factor_and_a_true_scale_latitude_off_the_pole_are_not_both_given() -> None:
    # PROJ uses k_0 only when lat_ts is at the pole; a lookup refuses to choose between them.
    params = {**POLAR, "latitude_of_true_scale_deg": -71.0, "scale_factor": 0.97}
    with pytest.raises(SourceLookupError, match="scale_factor"):
        PolarStereographicLookup(**params)
    PolarStereographicLookup(**{**POLAR, "latitude_of_true_scale_deg": -71.0})
    PolarStereographicLookup(**{**POLAR, "scale_factor": 0.97})


def test_lookups_are_frozen_and_store_floats() -> None:
    lookup = EquirectangularLookup(**{**EQUIRECTANGULAR, "false_easting_m": 0, "radius_m": 3396190})
    assert type(lookup.false_easting_m) is float
    assert type(lookup.radius_m) is float
    with pytest.raises(dataclasses.FrozenInstanceError):
        lookup.radius_m = 1.0  # type: ignore[misc]


def test_source_lookup_error_is_a_value_error() -> None:
    assert issubclass(SourceLookupError, ValueError)


# ---------------------------------------------------------------------------------------------
# Strategies


LATITUDES = st.floats(min_value=-HALF_PI, max_value=HALF_PI)
LONGITUDES = st.floats(min_value=-PI, max_value=PI, exclude_max=True)
POINTS = st.lists(st.tuples(LATITUDES, LONGITUDES), min_size=1, max_size=8)
RADII = st.one_of(st.sampled_from(sorted(BODY_RADII_M.values())), st.floats(1e3, 1e8))
SIGNED_SIZES = st.tuples(st.sampled_from([1.0, -1.0]), st.floats(1e-3, 1e4)).map(
    lambda pair: pair[0] * pair[1]
)
EXAMPLE_POINTS = [
    (0.0, 0.0),
    (-0.0, -PI),
    (HALF_PI, math.nextafter(PI, 0.0)),
    (-HALF_PI, 1e-300),
    (5e-324, -5e-324),
    (1e-9, -2e-12),
    (0.3, 1.2),
]


def columns(points: list[tuple[float, float]]) -> tuple[Array, Array]:
    pairs = np.array(points, dtype=np.float64).reshape(-1, 2)
    return pairs[:, 0].copy(), pairs[:, 1].copy()


@st.composite
def geographic_lookups(draw: st.DrawFn) -> GeographicLookup:
    return GeographicLookup(
        origin_lon_deg=draw(st.floats(-360.0, 360.0)),
        origin_lat_deg=draw(st.floats(-90.0, 90.0)),
        pixel_width_deg=draw(st.floats(1e-6, 10.0)),
        pixel_height_deg=draw(
            st.tuples(st.sampled_from([1.0, -1.0]), st.floats(1e-6, 10.0)).map(
                lambda pair: pair[0] * pair[1]
            )
        ),
    )


@st.composite
def polar_lookups(draw: st.DrawFn) -> PolarStereographicLookup:
    pole: Pole = "north" if draw(st.booleans()) else "south"
    sign = 1.0 if pole == "north" else -1.0
    if draw(st.booleans()):
        lat_ts, scale = sign * 90.0, draw(st.floats(0.5, 1.5))
    else:
        lat_ts, scale = sign * draw(st.floats(1e-3, 90.0)), 1.0
    return PolarStereographicLookup(
        pole=pole,
        central_meridian_deg=draw(st.floats(-360.0, 360.0)),
        latitude_of_true_scale_deg=lat_ts,
        scale_factor=scale,
        radius_m=draw(RADII),
        false_easting_m=draw(st.floats(-1e7, 1e7)),
        false_northing_m=draw(st.floats(-1e7, 1e7)),
        origin_x_m=draw(st.floats(-1e7, 1e7)),
        origin_y_m=draw(st.floats(-1e7, 1e7)),
        pixel_width_m=draw(SIGNED_SIZES),
        pixel_height_m=draw(SIGNED_SIZES),
    )


@st.composite
def equirectangular_lookups(draw: st.DrawFn) -> EquirectangularLookup:
    return EquirectangularLookup(
        center_longitude_deg=draw(st.floats(-360.0, 360.0)),
        standard_parallel_deg=draw(st.floats(-89.9, 89.9)),
        latitude_of_origin_deg=draw(st.floats(-90.0, 90.0)),
        radius_m=draw(RADII),
        false_easting_m=draw(st.floats(-1e7, 1e7)),
        false_northing_m=draw(st.floats(-1e7, 1e7)),
        origin_x_m=draw(st.floats(-1e7, 1e7)),
        origin_y_m=draw(st.floats(-1e7, 1e7)),
        pixel_width_m=draw(SIGNED_SIZES),
        pixel_height_m=draw(SIGNED_SIZES),
    )


SOUTH_POLE_LOOKUP = PolarStereographicLookup(**POLAR)
NORTH_POLE_LOOKUP = PolarStereographicLookup(
    **{**POLAR, "pole": "north", "latitude_of_true_scale_deg": 90.0, "central_meridian_deg": 90.0}
)
NORTH_TRUE_SCALE_LOOKUP = PolarStereographicLookup(
    **{**POLAR, "pole": "north", "latitude_of_true_scale_deg": 71.0, "central_meridian_deg": -45.0}
)
CTX_LOOKUP = EquirectangularLookup(**EQUIRECTANGULAR)
ANTIMERIDIAN_LOOKUP = EquirectangularLookup(**{**EQUIRECTANGULAR, "center_longitude_deg": 179.9})


# ---------------------------------------------------------------------------------------------
# Geographic latitude-longitude form (Copernicus, WorldCover)


@settings(max_examples=300, deadline=None)
@given(points=POINTS)
@example(points=EXAMPLE_POINTS)
def test_geographic_project_matches_exact_degrees(points: list[tuple[float, float]]) -> None:
    lat, lon = columns(points)
    lon_deg, lat_deg = GeographicLookup(**GEOGRAPHIC).project(lat, lon)
    want_lon, want_lat = exact_map(lambda phi, lam: geographic_project_mp(phi, lam), lat, lon)
    assert_close(lon_deg, want_lon)
    assert_close(lat_deg, want_lat)


@settings(max_examples=300, deadline=None)
@given(
    lon_deg=st.lists(st.floats(-1e4, 1e4), min_size=1, max_size=8),
    lat_deg=st.floats(-90.0, 90.0),
)
@example(lon_deg=[0.0, 180.0, -180.0, 360.0, 540.0, -179.99999999999997, 1e-300], lat_deg=90.0)
@example(lon_deg=[179.99999999999997, -720.0], lat_deg=-90.0)
def test_geographic_unproject_matches_exact_radians(lon_deg: list[float], lat_deg: float) -> None:
    lon = np.array(lon_deg)
    lat = np.full_like(lon, lat_deg)
    phi, lam = GeographicLookup(**GEOGRAPHIC).unproject(lon, lat)
    want_phi, want_lam = exact_map(geographic_unproject_mp, lon, lat)
    assert_close(phi, want_phi)
    assert_angles_close(lam, want_lam)
    assert np.all((lam >= -PI) & (lam < PI))
    assert np.all(np.abs(phi) <= HALF_PI)


@settings(max_examples=300, deadline=None)
@given(
    lookup=geographic_lookups(),
    lon_deg=st.lists(st.floats(-180.0, 180.0), min_size=1, max_size=8),
    lat_deg=st.floats(-90.0, 90.0),
)
@example(lookup=GeographicLookup(**GEOGRAPHIC), lon_deg=[77.45, 77.5, -282.55], lat_deg=18.55)
@example(
    # 179.99999999999997 + 180 rounds to 360 with a negative low part: still east of the origin.
    lookup=GeographicLookup(**{**GEOGRAPHIC, "origin_lon_deg": -180.0}),
    lon_deg=[179.99999999999997, -180.0, 180.0],
    lat_deg=-90.0,
)
@example(
    lookup=GeographicLookup(**{**GEOGRAPHIC, "origin_lon_deg": 179.5}),
    lon_deg=[-179.8, 179.7, 179.49999999999997],
    lat_deg=0.0,
)
@example(
    # A subnormal origin: lon - origin is -5e-324, and (lon - origin) / 360 underflows to -0.0,
    # so a turn decided by that quotient alone leaves the point west of the origin.
    lookup=GeographicLookup(
        origin_lon_deg=5e-324, origin_lat_deg=0.0, pixel_width_deg=1.0, pixel_height_deg=1.0
    ),
    lon_deg=[0.0, -1e-320, 360.0 - 1e-13],
    lat_deg=0.0,
)
def test_geographic_pixel_coordinates_match_exact_pixels(
    lookup: GeographicLookup, lon_deg: list[float], lat_deg: float
) -> None:
    lon = np.array(lon_deg)
    lat = np.full_like(lon, lat_deg)
    column, row = lookup.pixel_from_projected(lon, lat)
    want_column, want_row = exact_map(lambda a, b: geographic_affine_mp(lookup, a, b), lon, lat)
    assert_close(column, want_column)
    assert_close(row, want_row)


@settings(max_examples=300, deadline=None)
@given(
    lookup=geographic_lookups(),
    pixels=st.lists(st.tuples(st.floats(-1e6, 1e6), st.floats(-1e6, 1e6)), min_size=1, max_size=8),
)
@example(lookup=GeographicLookup(**GEOGRAPHIC), pixels=[(-0.5, -0.5), (0.0, 0.0), (-93054.5, 0.0)])
def test_geographic_projected_from_pixel_matches_exact_degrees(
    lookup: GeographicLookup, pixels: list[tuple[float, float]]
) -> None:
    column, row = columns(pixels)
    lon_deg, lat_deg = lookup.projected_from_pixel(column, row)
    want_lon, want_lat = exact_map(
        lambda a, b: geographic_inverse_affine_mp(lookup, a, b), column, row
    )
    assert_close(lon_deg, want_lon)
    assert_close(lat_deg, want_lat)


def test_geographic_columns_wrap_east_of_the_origin_across_the_antimeridian() -> None:
    lookup = GeographicLookup(**{**GEOGRAPHIC, "origin_lon_deg": 179.9, "pixel_width_deg": 0.01})
    lat = np.zeros(3)
    lon = np.radians(np.array([179.95, -179.95, 179.85]))
    column, _ = lookup.to_pixel(lat, lon)
    # 0.05 deg and 0.15 deg east of the origin meridian, and 0.05 deg west of it, which lies
    # 359.95 deg to the east: far outside a raster narrower than a full turn. The expected
    # columns come from decimal degrees; float64 radians of 179.95 deg are off by up to
    # 3e-14 deg, 3e-12 columns, hence atol 1e-9 here.
    np.testing.assert_allclose(column, [4.5, 14.5, 35994.5], rtol=RTOL, atol=1e-9)


def test_a_geographic_raster_in_the_0_to_360_convention_takes_western_longitudes() -> None:
    lookup = GeographicLookup(**{**GEOGRAPHIC, "origin_lon_deg": 0.0, "pixel_width_deg": 1.0})
    column, _ = lookup.to_pixel(np.zeros(2), np.radians(np.array([-10.0, 10.0])))
    np.testing.assert_allclose(column, [349.5, 9.5], rtol=RTOL, atol=1e-9)


def test_a_geographic_latitude_beyond_a_pole_raises() -> None:
    lookup = GeographicLookup(**GEOGRAPHIC)
    with pytest.raises(SourceLookupError):
        lookup.unproject(np.array([0.0]), np.array([90.000001]))
    with pytest.raises(SourceLookupError):
        lookup.from_pixel(np.array([0.0]), np.array([-100000.0]))


# ---------------------------------------------------------------------------------------------
# Polar stereographic form on the sphere (LOLA, Mini-RF, PGDA)


def polar_points(
    lookup: PolarStereographicLookup, points: list[tuple[float, float]]
) -> tuple[Array, Array]:
    """Points with the opposite pole left out, mirrored into the lookup's hemisphere."""
    lat, lon = columns(points)
    lat = np.where(lat == -HALF_PI, -math.nextafter(HALF_PI, 0.0), lat)
    return (lat if lookup.pole == "north" else -lat), lon


POLAR_EXAMPLES = [
    (HALF_PI, 0.0),
    (math.nextafter(HALF_PI, 0.0), 1.0),
    (HALF_PI - 1e-9, -2.0),
    (1.4, math.radians(90.0)),
    (1.4, math.radians(90.0) + PI / 2),
    (0.0, 3.0),
    (-1.5, -0.2),
    (-math.nextafter(HALF_PI, 0.0), 0.1),
]


@settings(max_examples=300, deadline=None)
@given(lookup=polar_lookups(), points=POINTS)
@example(lookup=NORTH_POLE_LOOKUP, points=POLAR_EXAMPLES)
@example(lookup=SOUTH_POLE_LOOKUP, points=POLAR_EXAMPLES)
@example(lookup=NORTH_TRUE_SCALE_LOOKUP, points=POLAR_EXAMPLES)
def test_polar_project_matches_the_exact_stereographic_projection(
    lookup: PolarStereographicLookup, points: list[tuple[float, float]]
) -> None:
    lat, lon = polar_points(lookup, points)
    easting, northing = lookup.project(lat, lon)
    want_e, want_n = exact_map(lambda phi, lam: polar_project_mp(lookup, phi, lam), lat, lon)
    assert_close(easting, want_e)
    assert_close(northing, want_n)


@settings(max_examples=300, deadline=None)
@given(
    lookup=polar_lookups(),
    projected=st.lists(
        st.tuples(st.floats(-1e7, 1e7), st.floats(-1e7, 1e7)), min_size=1, max_size=8
    ),
)
@example(
    lookup=SOUTH_POLE_LOOKUP,
    projected=[(0.0, 0.0), (-0.0, 1e-9), (1e-300, -0.0), (12000.0, -3.0), (5e-324, 4e6)],
)
@example(lookup=NORTH_POLE_LOOKUP, projected=[(0.0, 0.0), (0.0, -1e-6), (3e5, 1e-7)])
def test_polar_unproject_matches_the_exact_back_projection(
    lookup: PolarStereographicLookup, projected: list[tuple[float, float]]
) -> None:
    easting, northing = columns(projected)
    phi, lam = lookup.unproject(easting, northing)
    want_phi, want_lam = exact_map(lambda e, n: polar_unproject_mp(lookup, e, n), easting, northing)
    assert_close(phi, want_phi)
    assert_angles_close(lam, want_lam)
    assert np.all((lam >= -PI) & (lam < PI))


def test_polar_pole_maps_to_the_natural_origin_and_back_to_the_central_meridian() -> None:
    for lookup, pole in ((NORTH_POLE_LOOKUP, HALF_PI), (SOUTH_POLE_LOOKUP, -HALF_PI)):
        easting, northing = lookup.project(np.array([pole]), np.array([0.7]))
        # math.pi / 2 lies 6.1e-17 rad short of the pole, 1.1e-10 m on the Moon: that is the
        # map position of the float64 pole, and the exact reference agrees.
        want_e, want_n = exact_map(
            functools.partial(polar_project_mp, lookup), np.array([pole]), np.array([0.7])
        )
        assert_close(easting, want_e)
        assert_close(northing, want_n)
        phi, lam = lookup.unproject(np.array([0.0]), np.array([0.0]))
        assert phi[0] == pole
        assert_angles_close(lam, math.radians(lookup.central_meridian_deg))


@pytest.mark.parametrize("lookup", [NORTH_POLE_LOOKUP, SOUTH_POLE_LOOKUP], ids=["north", "south"])
def test_polar_axes_follow_the_central_meridian(lookup: PolarStereographicLookup) -> None:
    lam0 = math.radians(lookup.central_meridian_deg)
    lat = 1.4 if lookup.pole == "north" else -1.4
    easting, northing = lookup.project(np.array([lat, lat]), np.array([lam0, lam0 + PI / 2]))
    # On the central meridian: x = 0, y < 0 for the north aspect and y > 0 for the south.
    assert abs(easting[0]) < 1e-6
    assert northing[0] < 0.0 if lookup.pole == "north" else northing[0] > 0.0
    # 90 deg east of it: x > 0, y = 0.
    assert easting[1] > 0.0
    assert abs(northing[1]) < 1e-6


@settings(max_examples=200, deadline=None)
@given(lookup=polar_lookups(), lon=LONGITUDES)
def test_polar_scale_is_true_on_the_latitude_of_true_scale(
    lookup: PolarStereographicLookup, lon: float
) -> None:
    north = lookup.pole == "north"
    if abs(lookup.latitude_of_true_scale_deg) == 90.0:
        # At the pole the map scale is the scale factor: a point a colatitude t away lies
        # k0 R t (1 + t^2 / 12 + ...) from the origin, and t^2 / 12 < 1e-13 here.
        lat = HALF_PI - 2.0**-20 if north else 2.0**-20 - HALF_PI
        with mpmath.workdps(DIGITS):
            colatitude = float(mpmath.pi / 2 - abs(mp(lat)))
        want = lookup.scale_factor * lookup.radius_m * colatitude
    else:
        # The parallel lat_ts keeps its length on the sphere: map radius R cos(lat_ts).
        lat = math.radians(lookup.latitude_of_true_scale_deg)
        want = lookup.radius_m * math.cos(lat)
    easting, northing = lookup.project(np.array([lat]), np.array([lon]))
    np.testing.assert_allclose(np.hypot(easting, northing), want, rtol=RTOL, atol=ATOL)


def test_polar_projection_of_the_opposite_pole_raises() -> None:
    with pytest.raises(SourceLookupError, match="opposite pole"):
        NORTH_POLE_LOOKUP.project(np.array([-HALF_PI]), np.array([0.0]))
    with pytest.raises(SourceLookupError, match="opposite pole"):
        SOUTH_POLE_LOOKUP.to_pixel(np.array([0.0, HALF_PI]), np.array([0.0, 0.0]))


# ---------------------------------------------------------------------------------------------
# Equirectangular form on the sphere (CTX)


@settings(max_examples=300, deadline=None)
@given(lookup=equirectangular_lookups(), points=POINTS)
@example(lookup=CTX_LOOKUP, points=EXAMPLE_POINTS)
@example(
    lookup=CTX_LOOKUP,
    points=[(math.radians(18.4), math.radians(77.5)), (0.0, math.radians(77.5) - PI)],
)
@example(lookup=ANTIMERIDIAN_LOOKUP, points=[(0.1, -PI), (0.1, math.radians(-179.95)), (0.1, 3.14)])
@example(
    # lon0 = -180 deg and lon = math.pi: lon - lon0 lies 1.2e-16 rad short of a true turn, so
    # the easting is -1.2e-16 R cos(lat_ts) only if the turn is the true 2 pi.
    lookup=EquirectangularLookup(**{**EQUIRECTANGULAR, "center_longitude_deg": -180.0}),
    points=[(0.3, PI), (0.3, -PI), (-0.2, 3.0)],
)
@example(
    # Points on the float64 latitude of origin: the northing is R times the 1e-17 rad by which
    # math.radians(18.4) misses 18.4 deg, so lat_0 must be carried in double-double.
    lookup=EquirectangularLookup(**{**EQUIRECTANGULAR, "latitude_of_origin_deg": 18.4}),
    points=[
        (math.radians(18.4), math.radians(77.5)),
        (math.nextafter(math.radians(18.4), 1.0), 1.0),
    ],
)
def test_equirectangular_project_matches_exact_metres(
    lookup: EquirectangularLookup, points: list[tuple[float, float]]
) -> None:
    lat, lon = columns(points)
    easting, northing = lookup.project(lat, lon)
    want_e, want_n = exact_map(
        lambda phi, lam: equirectangular_project_mp(lookup, phi, lam), lat, lon
    )
    assert_close(easting, want_e)
    assert_close(northing, want_n)


@settings(max_examples=300, deadline=None)
@given(
    lookup=equirectangular_lookups(),
    fractions=st.lists(
        st.tuples(st.floats(-0.999, 0.999), st.floats(-0.999, 0.999)), min_size=1, max_size=8
    ),
)
@example(lookup=CTX_LOOKUP, fractions=[(0.0, 0.0), (1e-300, -5e-324), (0.5, -0.25)])
def test_equirectangular_unproject_matches_exact_radians(
    lookup: EquirectangularLookup, fractions: list[tuple[float, float]]
) -> None:
    # Projected coordinates spread over the projection's domain: |lon - lon0| < pi and
    # |lat| < pi / 2.
    u, v = columns(fractions)
    radius = lookup.radius_m
    lat_origin = math.radians(lookup.latitude_of_origin_deg)
    easting = u * PI * radius * math.cos(math.radians(lookup.standard_parallel_deg))
    low, high = (-HALF_PI - lat_origin) * radius, (HALF_PI - lat_origin) * radius
    northing = (low + high) / 2 + v * (high - low) / 2
    phi, lam = lookup.unproject(easting, northing)
    want_phi, want_lam = exact_map(
        lambda e, n: equirectangular_unproject_mp(lookup, e, n), easting, northing
    )
    assert_close(phi, want_phi)
    assert_angles_close(lam, want_lam)
    assert np.all((lam >= -PI) & (lam < PI))


def test_equirectangular_longitudes_are_taken_across_the_antimeridian_from_lon0() -> None:
    lookup = ANTIMERIDIAN_LOOKUP
    easting, _ = lookup.project(np.zeros(2), np.radians(np.array([-179.95, 179.85])))
    scale = lookup.radius_m * math.cos(math.radians(lookup.standard_parallel_deg))
    np.testing.assert_allclose(easting, scale * np.radians([0.15, -0.05]), rtol=1e-9)


def test_equirectangular_points_outside_the_projection_domain_raise() -> None:
    radius = CTX_LOOKUP.radius_m
    with pytest.raises(SourceLookupError):
        CTX_LOOKUP.unproject(np.array([0.0]), np.array([2 * radius * PI]))
    with pytest.raises(SourceLookupError):
        CTX_LOOKUP.unproject(np.array([4 * radius]), np.array([0.0]))


# ---------------------------------------------------------------------------------------------
# Geotransform of the projected forms, and the compositions


@settings(max_examples=300, deadline=None)
@given(
    lookup=st.one_of(polar_lookups(), equirectangular_lookups()),
    projected=st.lists(
        st.tuples(st.floats(-1e7, 1e7), st.floats(-1e7, 1e7)), min_size=1, max_size=8
    ),
)
@example(lookup=CTX_LOOKUP, projected=[(-5990.0, 1099990.0), (-6000.0, 1100000.0), (0.0, 0.0)])
@example(lookup=SOUTH_POLE_LOOKUP, projected=[(-19990.0, 19990.0), (-19990.000000000004, 0.0)])
@example(
    # Large false offsets and origins next to each other: E + FE rounds at 1e-9 m, 1e-7 pixels
    # at 1 cm, so the sum must be carried exactly (plain float64 misses by up to 1800 times).
    lookup=EquirectangularLookup(
        **{
            **EQUIRECTANGULAR,
            "false_easting_m": 10000000.3,
            "origin_x_m": 10000000.4,
            "false_northing_m": 2000000.0,
            "origin_y_m": 1999999.9,
            "pixel_width_m": 0.01,
            "pixel_height_m": -0.01,
        }
    ),
    projected=[(0.1, -0.1 + 1e-7), (0.123456789, -0.0987654321), (0.0999999, -0.1)],
)
def test_projected_pixel_coordinates_match_exact_pixels(
    lookup: ProjectedLookup, projected: list[tuple[float, float]]
) -> None:
    easting, northing = columns(projected)
    column, row = lookup.pixel_from_projected(easting, northing)
    want_column, want_row = exact_map(
        lambda e, n: projected_affine_mp(lookup, e, n), easting, northing
    )
    assert_close(column, want_column)
    assert_close(row, want_row)


@settings(max_examples=300, deadline=None)
@given(
    lookup=st.one_of(polar_lookups(), equirectangular_lookups()),
    pixels=st.lists(st.tuples(st.floats(-1e6, 1e6), st.floats(-1e6, 1e6)), min_size=1, max_size=8),
)
@example(lookup=CTX_LOOKUP, pixels=[(-0.5, -0.5), (0.0, 0.0), (299.5, -55000.5)])
@example(
    # Pixels whose metres nearly cancel a large origin: (column + 0.5) * size rounds at 1e-10 m,
    # so the product must be carried exactly (plain float64 misses by 50 and 330 times).
    lookup=EquirectangularLookup(
        **{
            **EQUIRECTANGULAR,
            "origin_x_m": 1234567.891,
            "pixel_width_m": 0.37,
            "origin_y_m": -7654321.123,
            "pixel_height_m": -0.53,
        }
    ),
    pixels=[(-3336670.475675676, -14442115.826415094)],
)
def test_projected_from_pixel_matches_exact_metres(
    lookup: ProjectedLookup, pixels: list[tuple[float, float]]
) -> None:
    column, row = columns(pixels)
    easting, northing = lookup.projected_from_pixel(column, row)
    want_e, want_n = exact_map(lambda c, r: projected_inverse_affine_mp(lookup, c, r), column, row)
    assert_close(easting, want_e)
    assert_close(northing, want_n)


@settings(max_examples=100, deadline=None)
@given(
    lookup=st.one_of(geographic_lookups(), polar_lookups(), equirectangular_lookups()),
    points=POINTS,
)
def test_to_pixel_is_the_projection_then_the_geotransform(
    lookup: Lookup, points: list[tuple[float, float]]
) -> None:
    lat, lon = columns(points)
    if isinstance(lookup, PolarStereographicLookup):
        lat, lon = polar_points(lookup, points)
    column, row = lookup.to_pixel(lat, lon)
    want_column, want_row = lookup.pixel_from_projected(*lookup.project(lat, lon))
    assert_same_bits(column, want_column)
    assert_same_bits(row, want_row)


@pytest.mark.parametrize(
    "lookup",
    [GeographicLookup(**GEOGRAPHIC), SOUTH_POLE_LOOKUP, NORTH_TRUE_SCALE_LOOKUP, CTX_LOOKUP],
    ids=["geographic", "south", "north-true-scale", "equirectangular"],
)
def test_from_pixel_is_the_inverse_geotransform_then_the_inverse_projection(
    lookup: Lookup,
) -> None:
    column = np.array([0.0, 17.25, 299.0, -0.5])
    row = np.array([0.0, 3.5, 150.125, 299.0])
    lat, lon = lookup.from_pixel(column, row)
    want_lat, want_lon = lookup.unproject(*lookup.projected_from_pixel(column, row))
    assert_same_bits(lat, want_lat)
    assert_same_bits(lon, want_lon)
    # And the forward composition takes the points back to the same pixels. The round trip
    # passes through float64 angles, whose rounding (2.2e-16 rad at most here) moves a point by
    # up to about 4e-10 m, 2e-11 pixels; 1e-9 pixels leaves room for that floor.
    back_column, back_row = lookup.to_pixel(lat, lon)
    np.testing.assert_allclose(back_column, column, rtol=RTOL, atol=1e-9)
    np.testing.assert_allclose(back_row, row, rtol=RTOL, atol=1e-9)


@pytest.mark.parametrize(
    "lookup", [GeographicLookup(**GEOGRAPHIC), SOUTH_POLE_LOOKUP, CTX_LOOKUP], ids=field_id
)
def test_lookups_keep_the_input_shape_and_return_float64(lookup: Lookup) -> None:
    lat = np.full((2, 3), -1.5 if lookup is SOUTH_POLE_LOOKUP else 0.32)
    lon = np.linspace(1.35, 1.36, 6).reshape(2, 3)
    for out in (*lookup.project(lat, lon), *lookup.to_pixel(lat, lon)):
        assert out.shape == (2, 3)
        assert out.dtype == np.float64


@pytest.mark.parametrize(
    "lookup", [GeographicLookup(**GEOGRAPHIC), SOUTH_POLE_LOOKUP, CTX_LOOKUP], ids=field_id
)
def test_lookups_reject_points_outside_the_sphere_or_not_finite(lookup: Lookup) -> None:
    for lat, lon in ((math.nan, 0.0), (0.0, math.inf), (HALF_PI + 1e-9, 0.0), (0.0, 4.0)):
        with pytest.raises(SourceLookupError):
            lookup.to_pixel(np.array([lat]), np.array([lon]))
    with pytest.raises(SourceLookupError):
        lookup.from_pixel(np.array([math.nan]), np.array([0.0]))


# ---------------------------------------------------------------------------------------------
# Bilinear interpolation on the four neighbouring source pixels


def loop_bilinear(
    source: Array, source_valid: BoolArray, column: Array, row: Array
) -> tuple[Array, BoolArray, BoolArray]:
    """Bilinear interpolation written with explicit loops, independent of the module.

    A point is inside if it lies in the closed rectangle of pixel centres. Its four neighbours
    are the columns i, i + 1 and rows j, j + 1 with i = floor(column), clamped so that i + 1
    is the last column at the east edge (and the same for rows). The output is nodata if any of
    the four is nodata, whatever its weight.
    """
    rows, cols = source.shape
    values = np.full(column.shape, np.nan)
    valid = np.zeros(column.shape, dtype=bool)
    inside = np.zeros(column.shape, dtype=bool)
    for index in np.ndindex(column.shape):
        u = float(column[index])
        v = float(row[index])
        if not (0.0 <= u <= cols - 1 and 0.0 <= v <= rows - 1):
            continue
        inside[index] = True
        i = min(math.floor(u), cols - 2)
        j = min(math.floor(v), rows - 2)
        cells = [(j, i), (j, i + 1), (j + 1, i), (j + 1, i + 1)]
        if not all(source_valid[cell] for cell in cells):
            continue
        p = u - i
        q = v - j
        top = source[j, i] + p * (source[j, i + 1] - source[j, i])
        bottom = source[j + 1, i] + p * (source[j + 1, i + 1] - source[j + 1, i])
        values[index] = top + q * (bottom - top)
        valid[index] = True
    return values, valid, inside


def footprint(shape: tuple[int, int], u: float, v: float) -> set[tuple[int, int]]:
    """The four source pixels (row, column) a point inside the source interpolates from."""
    rows, cols = shape
    i = min(math.floor(u), cols - 2)
    j = min(math.floor(v), rows - 2)
    return {(j, i), (j, i + 1), (j + 1, i), (j + 1, i + 1)}


@st.composite
def resample_cases(draw: st.DrawFn) -> tuple[Array, BoolArray, Array, Array]:
    """A small source, a nodata mask and points inside, outside and on the edges."""
    rows = draw(st.integers(2, 9))
    cols = draw(st.integers(2, 9))
    source = draw(hnp.arrays(np.float64, (rows, cols), elements=st.floats(-100.0, 100.0)))
    mask = draw(hnp.arrays(np.bool_, (rows, cols), elements=st.booleans()))
    coordinate_u = st.one_of(
        st.floats(-1.5, cols + 0.5),
        st.integers(-1, cols).map(float),
        st.sampled_from(
            [0.0, -1e-12, cols - 1.0, cols - 1 + 1e-12, math.nextafter(cols - 1.0, 0.0)]
        ),
    )
    coordinate_v = st.one_of(
        st.floats(-1.5, rows + 0.5),
        st.integers(-1, rows).map(float),
        st.sampled_from(
            [0.0, -1e-12, rows - 1.0, rows - 1 + 1e-12, math.nextafter(rows - 1.0, 0.0)]
        ),
    )
    points = draw(st.lists(st.tuples(coordinate_u, coordinate_v), min_size=1, max_size=12))
    u, v = columns(points)
    return source, ~mask, u, v


@settings(max_examples=400, deadline=None)
@given(case=resample_cases())
def test_bilinear_matches_an_explicit_four_neighbour_loop(
    case: tuple[Array, BoolArray, Array, Array],
) -> None:
    source, source_valid, u, v = case
    with_nan = np.where(source_valid, source, np.nan)
    result = bilinear_resample(with_nan, u, v, nodata=None)
    values, valid, inside = loop_bilinear(source, source_valid, u, v)
    assert_same_bits(result.valid, valid)
    assert_same_bits(result.inside, inside)
    assert np.all(np.isnan(result.values[~valid]))
    assert_close(result.values[valid], values[valid])


@settings(max_examples=300, deadline=None)
@given(
    shape=st.tuples(st.integers(2, 40), st.integers(2, 40)),
    coefficients=st.tuples(st.floats(-1.0, 1.0), st.floats(-1.0, 1.0), st.floats(-1.0, 1.0)),
    fractions=st.lists(
        st.tuples(st.floats(0.0, 1.0), st.floats(0.0, 1.0)), min_size=1, max_size=12
    ),
)
def test_bilinear_reproduces_a_plane_in_pixel_coordinates(
    shape: tuple[int, int],
    coefficients: tuple[float, float, float],
    fractions: list[tuple[float, float]],
) -> None:
    rows, cols = shape
    a, b, c = coefficients
    # Normalised: the plane changes by at most 1 across the raster.
    b, c = b / cols, c / rows
    row_index, column_index = np.mgrid[0:rows, 0:cols].astype(np.float64)
    source = a + b * column_index + c * row_index
    fu, fv = columns(fractions)
    u, v = fu * (cols - 1), fv * (rows - 1)
    result = bilinear_resample(source, u, v, nodata=None)
    assert np.all(result.valid)
    assert_close(result.values, a + b * u + c * v)


def test_bilinear_reproduces_the_cross_term_and_returns_pixel_values_at_centres() -> None:
    row_index, column_index = np.mgrid[0:4, 0:5].astype(np.float64)
    source = 0.25 + column_index * row_index / 12.0
    u = np.array([0.5, 1.25, 3.75, 4.0, 2.0, 0.0])
    v = np.array([0.5, 2.5, 0.125, 3.0, 1.0, 0.0])
    result = bilinear_resample(source, u, v, nodata=None)
    assert_close(result.values, 0.25 + u * v / 12.0)
    # At a pixel centre the value is the pixel's own, bit for bit, including the last centre.
    centres = bilinear_resample(source, column_index, row_index, nodata=None)
    assert_same_bits(centres.values, source)


def test_output_depends_only_on_the_four_neighbours() -> None:
    # No cubic kernel and no averaging: changing any pixel outside the footprint, even to
    # nodata, leaves the output unchanged.
    rng = np.random.default_rng(20261114)
    source = rng.uniform(-50.0, 50.0, size=(8, 9))
    u, v = 3.3, 4.6
    base = bilinear_resample(source, np.array([u]), np.array([v]), nodata=None)
    near = footprint(source.shape, u, v)
    for cell in np.ndindex(source.shape):
        if cell in near:
            continue
        for replacement in (1e6, np.nan):
            changed = source.copy()
            changed[cell] = replacement
            result = bilinear_resample(changed, np.array([u]), np.array([v]), nodata=None)
            assert_same_bits(result.values, base.values)
            assert_same_bits(result.valid, base.valid)


def test_points_outside_the_source_are_nodata_never_zero() -> None:
    source = np.arange(12, dtype=np.float64).reshape(3, 4) - 5.0
    u = np.array([0.0, 3.0, -1e-12, 3 + 1e-12, 1.0, 1.0, -7.0, 2.5])
    v = np.array([0.0, 2.0, 1.0, 1.0, -1e-12, 2 + 1e-12, 1.0, 1e9])
    result = bilinear_resample(source, u, v, nodata=None)
    assert_same_bits(result.inside, np.array([True, True] + [False] * 6))
    assert_same_bits(result.valid, result.inside)
    assert_same_bits(result.values[:2], np.array([-5.0, 6.0]))
    assert np.all(np.isnan(result.values[2:]))


def test_a_nodata_pixel_masks_a_point_on_a_centre_even_with_zero_weight() -> None:
    # The footprint of a point at column k is (k, k + 1), or (k - 1, k) on the last column, so a
    # nodata neighbour with zero weight still masks the output (documented convention).
    source = np.ones((3, 4))
    source[1, 2] = np.nan
    result = bilinear_resample(
        source, np.array([1.0, 3.0, 0.0]), np.array([1.0, 1.0, 0.0]), nodata=None
    )
    assert_same_bits(result.valid, np.array([False, False, True]))


@settings(max_examples=300, deadline=None)
@given(case=resample_cases(), data=st.data())
def test_one_nodata_pixel_masks_exactly_the_points_whose_footprint_includes_it(
    case: tuple[Array, BoolArray, Array, Array], data: st.DataObject
) -> None:
    source, _, u, v = case
    rows, cols = source.shape
    bad = (data.draw(st.integers(0, rows - 1)), data.draw(st.integers(0, cols - 1)))
    with_hole = source.copy()
    with_hole[bad] = np.nan
    result = bilinear_resample(with_hole, u, v, nodata=None)
    expected = np.array(
        [
            0.0 <= ui <= cols - 1
            and 0.0 <= vi <= rows - 1
            and bad not in footprint((rows, cols), ui, vi)
            for ui, vi in zip(u.tolist(), v.tolist(), strict=True)
        ]
    )
    assert_same_bits(result.valid, expected)
    full = bilinear_resample(source, u, v, nodata=None)
    assert_same_bits(result.values[expected], full.values[expected])


SENTINELS = (-9999.0, -32768.0, -3.4028234663852886e38, 1e30)


@settings(max_examples=300, deadline=None)
@given(case=resample_cases(), sentinel=st.sampled_from(SENTINELS))
def test_nan_and_a_declared_sentinel_behave_identically(
    case: tuple[Array, BoolArray, Array, Array], sentinel: float
) -> None:
    source, source_valid, u, v = case
    with_nan = bilinear_resample(np.where(source_valid, source, np.nan), u, v, nodata=None)
    with_sentinel = bilinear_resample(
        np.where(source_valid, source, sentinel), u, v, nodata=sentinel
    )
    assert_same_bits(with_sentinel.values, with_nan.values)
    assert_same_bits(with_sentinel.valid, with_nan.valid)
    assert_same_bits(with_sentinel.inside, with_nan.inside)


def test_an_all_nodata_source_gives_an_all_nodata_output() -> None:
    u = np.array([0.0, 1.5, 2.0, 0.25])
    v = np.array([0.0, 0.5, 1.0, 0.75])
    for source, nodata in ((np.full((2, 3), np.nan), None), (np.full((2, 3), -9999.0), -9999.0)):
        result = bilinear_resample(source, u, v, nodata=nodata)
        assert not np.any(result.valid)
        assert np.all(result.inside)
        assert np.all(np.isnan(result.values))


def test_zero_is_valid_data_and_infinities_are_nodata() -> None:
    source = np.zeros((2, 2))
    result = bilinear_resample(source, np.array([0.5]), np.array([0.5]), nodata=None)
    assert_same_bits(result.valid, np.array([True]))
    assert_same_bits(result.values, np.array([0.0]))
    for bad in (np.inf, -np.inf):
        source[0, 1] = bad
        result = bilinear_resample(source, np.array([0.5]), np.array([0.5]), nodata=-9999.0)
        assert_same_bits(result.valid, np.array([False]))
        assert np.isnan(result.values[0])


def test_an_integer_source_with_a_sentinel_matches_the_float_source_with_nan() -> None:
    source = np.array([[100, -32768, 300], [400, 500, 600], [700, 800, -32768]], dtype=np.int16)
    u = np.array([0.5, 1.5, 0.25, 1.75, 2.0])
    v = np.array([0.5, 0.5, 1.5, 1.5, 0.0])
    as_float = np.where(source == -32768, np.nan, source.astype(np.float64))
    from_int = bilinear_resample(source, u, v, nodata=-32768)
    from_float = bilinear_resample(as_float, u, v, nodata=None)
    assert_same_bits(from_int.values, from_float.values)
    assert_same_bits(from_int.valid, np.array([False, False, True, False, False]))


def test_output_is_float64_with_a_boolean_mask_and_nan_where_missing() -> None:
    source = np.array([[1.0, 2.0, np.nan], [4.0, 0.0, 6.0]], dtype=np.float32)
    u = np.array([[0.5, 1.5], [9.0, 0.0]])
    v = np.array([[0.5, 0.5], [0.0, 1.0]])
    result = bilinear_resample(source, u, v, nodata=None)
    assert isinstance(result, ResampledGrid)
    assert result.values.dtype == np.float64
    assert result.valid.dtype == np.bool_
    assert result.inside.dtype == np.bool_
    assert result.values.shape == result.valid.shape == result.inside.shape == (2, 2)
    assert_same_bits(result.valid, np.array([[True, False], [False, True]]))
    assert_same_bits(np.isnan(result.values), ~result.valid)
    assert not np.any(result.values[~result.valid] == 0.0)
    for array in (result.values, result.valid, result.inside):
        assert not array.flags.writeable


def test_resampled_grid_rejects_inconsistent_masks() -> None:
    values = np.array([1.0, np.nan])
    with pytest.raises(ValueError):
        ResampledGrid(values=values, valid=np.array([True, True]), inside=np.array([True, True]))
    with pytest.raises(ValueError):
        ResampledGrid(values=values, valid=np.array([True, False]), inside=np.array([False, True]))
    with pytest.raises(ValueError):
        ResampledGrid(
            values=np.array([1.0, 0.0]),
            valid=np.array([True, False]),
            inside=np.array([True, True]),
        )


@pytest.mark.parametrize(
    ("source", "u", "v", "nodata"),
    [
        (np.ones(4), [0.5], [0.5], None),
        (np.ones((1, 4)), [0.5], [0.0], None),
        (np.ones((4, 1)), [0.0], [0.5], None),
        (np.ones((2, 2, 2)), [0.5], [0.5], None),
        (np.ones((2, 2), dtype=bool), [0.5], [0.5], None),
        (np.ones((2, 2), dtype=complex), [0.5], [0.5], None),
        (np.ones((2, 2)), [math.nan], [0.5], None),
        (np.ones((2, 2)), [0.5], [math.inf], None),
        (np.ones((2, 2)), [0.5, 0.5], [0.5, 0.5, 0.5], None),
        (np.ones((2, 2)), [0.5], [0.5], True),
        (np.ones((2, 2)), [0.5], [0.5], "-9999"),
    ],
)
def test_bilinear_rejects_invalid_inputs(source: Any, u: Any, v: Any, nodata: Any) -> None:
    with pytest.raises(ValueError):
        bilinear_resample(source, np.asarray(u), np.asarray(v), nodata=nodata)


def test_nodata_must_be_declared() -> None:
    with pytest.raises(TypeError, match="nodata"):
        bilinear_resample(np.ones((2, 2)), np.array([0.5]), np.array([0.5]))  # type: ignore[call-arg]


# ---------------------------------------------------------------------------------------------
# The D29 window resampler: AEQD inverse, source lookup and bilinear interpolation


# Half the side of each window source: covers the 5 km window in any orientation (its half
# diagonal is 3536 m) with a margin.
SOURCE_HALF_M = 3700.0


def geographic_source_for(
    lat0: float, lon0: float, radius: float
) -> tuple[GeographicLookup, tuple[int, int]]:
    """A geographic raster at 3 arcsec around the window, its origin off the pixel lattice."""
    pixel = 1 / 1200
    half_lat = math.degrees(SOURCE_HALF_M / radius)
    half_lon = math.degrees(SOURCE_HALF_M / (radius * math.cos(lat0)))
    lookup = GeographicLookup(
        origin_lon_deg=math.degrees(lon0) - half_lon - 0.37 * pixel,
        origin_lat_deg=math.degrees(lat0) + half_lat + 0.21 * pixel,
        pixel_width_deg=pixel,
        pixel_height_deg=-pixel,
    )
    return lookup, (math.ceil(2 * half_lat / pixel) + 2, math.ceil(2 * half_lon / pixel) + 2)


def polar_source_for(
    lat0: float, lon0: float, radius: float
) -> tuple[PolarStereographicLookup, tuple[int, int]]:
    """A south polar raster at 20 m around the window (true scale at the pole)."""
    pixel = 20.0
    rho = 2 * radius * math.tan((HALF_PI + lat0) / 2)
    centre_e, centre_n = rho * math.sin(lon0), rho * math.cos(lon0)
    lookup = PolarStereographicLookup(
        pole="south",
        central_meridian_deg=0.0,
        latitude_of_true_scale_deg=-90.0,
        scale_factor=1.0,
        radius_m=radius,
        false_easting_m=0.0,
        false_northing_m=0.0,
        origin_x_m=centre_e - SOURCE_HALF_M - 0.37 * pixel,
        origin_y_m=centre_n + SOURCE_HALF_M + 0.21 * pixel,
        pixel_width_m=pixel,
        pixel_height_m=-pixel,
    )
    size = math.ceil(2 * SOURCE_HALF_M / pixel) + 2
    return lookup, (size, size)


def equirectangular_source_for(
    lat0: float, lon0: float, radius: float
) -> tuple[EquirectangularLookup, tuple[int, int]]:
    """A CTX-like equirectangular raster at 20 m around the window."""
    pixel = 20.0
    lon_c, lat_ts = 77.5, 18.4
    scale = radius * math.cos(math.radians(lat_ts))
    centre_e, centre_n = scale * (lon0 - math.radians(lon_c)), radius * lat0
    half_e = SOURCE_HALF_M * scale / (radius * math.cos(lat0))
    lookup = EquirectangularLookup(
        center_longitude_deg=lon_c,
        standard_parallel_deg=lat_ts,
        latitude_of_origin_deg=0.0,
        radius_m=radius,
        false_easting_m=0.0,
        false_northing_m=0.0,
        origin_x_m=centre_e - half_e - 0.37 * pixel,
        origin_y_m=centre_n + SOURCE_HALF_M + 0.21 * pixel,
        pixel_width_m=pixel,
        pixel_height_m=-pixel,
    )
    return lookup, (math.ceil(2 * SOURCE_HALF_M / pixel) + 2, math.ceil(2 * half_e / pixel) + 2)


SourceBuilder = Callable[[float, float, float], tuple[Lookup, tuple[int, int]]]
WINDOW_CASES: list[tuple[str, SourceBuilder, float, float]] = [
    ("geographic", geographic_source_for, math.radians(18.4), math.radians(77.6)),
    ("geographic-antimeridian", geographic_source_for, math.radians(-12.3), math.radians(179.99)),
    ("polar-south", polar_source_for, math.radians(-89.6), math.radians(30.0)),
    ("equirectangular", equirectangular_source_for, math.radians(18.45), math.radians(77.62)),
]


def reference_lines(size: int) -> list[int]:
    middle = size // 2
    return [0, 1, size // 4, middle - 1, middle, middle + 1, 3 * size // 4, size - 2, size - 1]


def pixel_plane(shape: tuple[int, int]) -> tuple[Array, tuple[float, float, float]]:
    """A normalised plane in pixel coordinates: z = a + b column + c row, |change| <= 1."""
    rows, cols = shape
    a, b, c = 0.3, 0.7 / cols, -0.4 / rows
    row_index, column_index = np.mgrid[0:rows, 0:cols].astype(np.float64)
    return a + b * column_index + c * row_index, (a, b, c)


@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize(
    ("name", "builder", "lat0", "lon0"), WINDOW_CASES, ids=[case[0] for case in WINDOW_CASES]
)
def test_a_plane_in_the_source_resamples_exactly_through_each_lookup(
    name: str, builder: SourceBuilder, lat0: float, lon0: float, body: str
) -> None:
    radius = BODY_RADII_M[body]
    lookup, shape = builder(lat0, lon0, radius)
    source, (a, b, c) = pixel_plane(shape)
    result = resample_window(
        source, lookup, grid=ANALYSIS_GRID, body=body, lat0_rad=lat0, lon0_rad=lon0, nodata=None
    )
    assert result.values.shape == (ANALYSIS_GRID.size, ANALYSIS_GRID.size)
    assert np.all(result.valid), name
    # Every pixel against the plane at the module's own pixel coordinates: bilinear is exact.
    x, y = ANALYSIS_GRID.mesh_m()
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
    u, v = lookup.to_pixel(lat, lon)
    assert_close(result.values, a + b * u + c * v)
    # The reference lines against the plane at exact pixel coordinates of each window pixel.
    lines = np.array(reference_lines(ANALYSIS_GRID.size))
    xs, ys = x[np.ix_(lines, lines)], y[np.ix_(lines, lines)]
    exact_u, exact_v = exact_window_pixels(lookup, xs, ys, lat0, lon0, radius)
    assert_close(result.values[np.ix_(lines, lines)], a + b * exact_u + c * exact_v)


def test_resample_window_is_the_aeqd_inverse_then_the_lookup_then_bilinear() -> None:
    lat0, lon0 = math.radians(-89.6), math.radians(30.0)
    lookup, shape = polar_source_for(lat0, lon0, BODY_RADII_M["moon"])
    source, _ = pixel_plane(shape)
    source[100:110, 50:200] = -9999.0
    result = resample_window(
        source,
        lookup,
        grid=ANALYSIS_GRID,
        body="moon",
        lat0_rad=lat0,
        lon0_rad=lon0,
        nodata=-9999.0,
    )
    x, y = ANALYSIS_GRID.mesh_m()
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=BODY_RADII_M["moon"])
    direct = bilinear_resample(source, *lookup.to_pixel(lat, lon), nodata=-9999.0)
    assert_same_bits(result.values, direct.values)
    assert_same_bits(result.valid, direct.valid)
    assert_same_bits(result.inside, direct.inside)
    assert not np.all(result.valid)


def test_resample_window_uses_the_d29_body_sphere() -> None:
    lat0, lon0 = math.radians(18.45), math.radians(77.62)
    lookup, shape = equirectangular_source_for(lat0, lon0, BODY_RADII_M["mars"])
    source, _ = pixel_plane(shape)
    common: dict[str, Any] = {
        "grid": ANALYSIS_GRID,
        "lat0_rad": lat0,
        "lon0_rad": lon0,
        "nodata": None,
    }
    with pytest.raises(UnknownBodyError):
        resample_window(source, lookup, body="Mars", **common)
    mars = resample_window(source, lookup, body="mars", **common)
    moon = resample_window(source, lookup, body="moon", **common)
    assert not np.allclose(mars.values, moon.values)


def test_resample_window_requires_a_declared_nodata() -> None:
    lookup, shape = equirectangular_source_for(0.32, 1.355, BODY_RADII_M["mars"])
    with pytest.raises(TypeError, match="nodata"):
        resample_window(  # type: ignore[call-arg]
            np.ones(shape), lookup, grid=ANALYSIS_GRID, body="mars", lat0_rad=0.32, lon0_rad=1.355
        )


def test_window_pixels_beyond_the_source_are_nodata_and_never_zero() -> None:
    lat0, lon0 = math.radians(18.45), math.radians(77.62)
    lookup, (rows, cols) = equirectangular_source_for(lat0, lon0, BODY_RADII_M["mars"])
    source, _ = pixel_plane((rows, cols // 2))
    result = resample_window(
        source, lookup, grid=ANALYSIS_GRID, body="mars", lat0_rad=lat0, lon0_rad=lon0, nodata=None
    )
    x, _ = ANALYSIS_GRID.mesh_m()
    # The source keeps only its western half, whose last pixel centre lies a few metres east of
    # the window centre.
    assert np.all(result.valid[x < -100.0])
    assert not np.any(result.inside[x > 100.0])
    assert np.all(np.isnan(result.values[~result.valid]))
    assert_same_bits(result.valid, result.inside)


def test_one_nodata_source_pixel_masks_exactly_its_footprint_in_a_window() -> None:
    body = "mars"
    lat0, lon0 = math.radians(18.45), math.radians(77.62)
    radius = BODY_RADII_M[body]
    lookup, shape = equirectangular_source_for(lat0, lon0, radius)
    source, _ = pixel_plane(shape)
    x, y = ANALYSIS_GRID.mesh_m()
    exact_u, exact_v = exact_window_pixels(lookup, x, y, lat0, lon0, radius)
    # The fixture keeps every window pixel well away from a source pixel boundary, so the floor
    # of each exact coordinate is that of the module's.
    for coordinate in (exact_u, exact_v):
        fraction = coordinate - np.floor(coordinate)
        assert np.all((fraction > 1e-6) & (fraction < 1 - 1e-6))
    centre = (ANALYSIS_GRID.size // 2, ANALYSIS_GRID.size // 2)
    bad = (math.floor(exact_v[centre]), math.floor(exact_u[centre]) + 1)
    with_hole = source.copy()
    with_hole[bad] = np.nan
    result = resample_window(
        with_hole, lookup, grid=ANALYSIS_GRID, body=body, lat0_rad=lat0, lon0_rad=lon0, nodata=None
    )
    expected = np.array(
        [
            bad not in footprint(shape, float(u), float(v))
            for u, v in zip(exact_u.ravel(), exact_v.ravel(), strict=True)
        ]
    ).reshape(x.shape)
    assert 1 <= np.count_nonzero(~expected) <= 4
    assert_same_bits(result.valid, expected)
    # The same with a declared sentinel, and with every source pixel missing.
    sentinel = source.copy()
    sentinel[bad] = -32768.0
    same = resample_window(
        sentinel,
        lookup,
        grid=ANALYSIS_GRID,
        body=body,
        lat0_rad=lat0,
        lon0_rad=lon0,
        nodata=-32768.0,
    )
    assert_same_bits(same.values, result.values)
    assert_same_bits(same.valid, result.valid)
    empty = resample_window(
        np.full(shape, np.nan),
        lookup,
        grid=ANALYSIS_GRID,
        body=body,
        lat0_rad=lat0,
        lon0_rad=lon0,
        nodata=None,
    )
    assert not np.any(empty.valid)
    assert np.all(np.isnan(empty.values))


# ---------------------------------------------------------------------------------------------
# A plane in the window x-y frame stays a plane


def window_frame_offsets(lat: Array, lon: Array, radius: float) -> tuple[Array, Array]:
    """Forward AEQD about (0, 0) from 3-D unit vectors in float64, written for this test.

    At this centre the up axis is the x axis of the body frame, east is y and north is z. The
    offsets are radius * c along the azimuth, with c = atan2(|horizontal|, up). No point here
    is the centre itself.
    """
    east = np.cos(lat) * np.sin(lon)
    north = np.sin(lat)
    up = np.cos(lat) * np.cos(lon)
    horizontal = np.hypot(east, north)
    scale = radius * np.arctan2(horizontal, up) / horizontal
    return scale * east, scale * north


def curvature_bound(pixel_m: float, b: float, c: float, extent_m: float, radius: float) -> float:
    """Bound on bilinear interpolation error for z = a + b x + c y on an equatorial source.

    The source is equirectangular about (0, 0) with true scale on the equator, so source metres
    are X = R lon and Y = R lat. The window frame about the same centre is, to fifth order,

        x = X - X Y^2 / (3 R^2),    y = Y + Y X^2 / (6 R^2)

    (both maps are isometric at the centre and odd, so there is no second-order term). The
    plane in the window frame is then f(X, Y) = a + b x + c y with

        f_XX = c Y / (3 R^2),    f_YY = -2 b X / (3 R^2).

    Linear interpolation over a cell of side h errs by at most h^2 / 8 max|f''| along a line,
    and bilinear interpolation, which reproduces the cross term XY, by at most the sum over the
    two directions. With |X|, |Y| <= extent over the cells used:

        |error| <= h^2 / 8 * (|c| + 2 |b|) * extent / (3 R^2)

    The fifth-order terms change f'' by a relative (extent / R)^2, under 3e-6 on the 5 km Moon
    window; the bound carries a 1% margin for them.
    """
    return 1.01 * pixel_m**2 / 8 * (abs(c) + 2 * abs(b)) * extent_m / (3 * radius**2)


def least_squares_factor(grid: WindowGrid) -> float:
    """Bound on max|residual of the best-fit plane| over max|deviation from a plane|.

    The fit projects onto (1, x, y). On a full grid with centred coordinates these are
    orthogonal, so P e = mean(e) + x (sum x e / sum x^2) + y (sum y e / sum y^2), and
    |(I - P) e| <= (2 + max|x| mean|x| / mean x^2 + max|y| mean|y| / mean y^2) max|e|.
    """
    factor = 2.0
    for coordinate in (grid.x_m, grid.y_m):
        centred = coordinate - coordinate.mean()
        factor += float(np.max(np.abs(centred)) * np.mean(np.abs(centred)) / np.mean(centred**2))
    return factor


def best_fit_residual(grid: WindowGrid, values: Array) -> Array:
    x, y = grid.mesh_m()
    design = np.column_stack([np.ones(x.size), x.ravel(), y.ravel()])
    coefficients, *_ = np.linalg.lstsq(design, values.ravel(), rcond=None)
    residual: Array = (values.ravel() - design @ coefficients).reshape(values.shape)
    return residual


def window_plane_source(
    radius: float, pixel_m: float, half_m: float, a: float, b: float, c: float
) -> tuple[Array, EquirectangularLookup]:
    """An equirectangular source about (0, 0) sampling z = a + b x + c y of the window frame."""
    lookup = EquirectangularLookup(
        center_longitude_deg=0.0,
        standard_parallel_deg=0.0,
        latitude_of_origin_deg=0.0,
        radius_m=radius,
        false_easting_m=0.0,
        false_northing_m=0.0,
        origin_x_m=-half_m,
        origin_y_m=half_m,
        pixel_width_m=pixel_m,
        pixel_height_m=-pixel_m,
    )
    size = round(2 * half_m / pixel_m)
    centres = (np.arange(size) + 0.5) * pixel_m
    big_x, big_y = np.meshgrid(centres - half_m, half_m - centres, indexing="xy")
    x, y = window_frame_offsets(big_y / radius, big_x / radius, radius)
    return a + b * x + c * y, lookup


SMALL_GRID = window_grid(500.0, 10.0)


@pytest.mark.parametrize("body", BODIES)
def test_a_plane_in_the_window_frame_stays_a_plane_at_small_extent(body: str) -> None:
    radius = BODY_RADII_M[body]
    pixel_m, half_m = 1.0, 300.0
    a, b, c = 0.3, 0.7 / 250.0, -0.4 / 250.0
    bound = curvature_bound(pixel_m, b, c, SMALL_GRID.width_m / 2 + pixel_m, radius)
    # Stated curvature residual allowed for the best-fit plane: below half of atol, so the PRD
    # 7 tolerance governs alone (Moon 1.2e-13, Mars 3.3e-14, Earth 9.3e-15).
    assert least_squares_factor(SMALL_GRID) * bound < ATOL / 2
    source, lookup = window_plane_source(radius, pixel_m, half_m, a, b, c)
    result = resample_window(
        source, lookup, grid=SMALL_GRID, body=body, lat0_rad=0.0, lon0_rad=0.0, nodata=None
    )
    assert np.all(result.valid)
    residual = best_fit_residual(SMALL_GRID, result.values)
    assert_close(result.values, result.values - residual)
    x, y = SMALL_GRID.mesh_m()
    assert_close(result.values, a + b * x + c * y)


@pytest.mark.parametrize("body", BODIES)
def test_the_curvature_residual_stays_within_its_stated_bound_at_the_d29_extent(body: str) -> None:
    radius = BODY_RADII_M[body]
    pixel_m, half_m = 25.0, 2600.0
    a, b, c = 0.3, 0.7 / 2500.0, -0.4 / 2500.0
    # Stated allowance: the curvature bound (Moon 1.6e-11, Mars 4.1e-12, Earth 1.2e-12) plus the
    # PRD 7 arithmetic tolerance.
    bound = curvature_bound(pixel_m, b, c, ANALYSIS_GRID.width_m / 2 + pixel_m, radius)
    source, lookup = window_plane_source(radius, pixel_m, half_m, a, b, c)
    result = resample_window(
        source, lookup, grid=ANALYSIS_GRID, body=body, lat0_rad=0.0, lon0_rad=0.0, nodata=None
    )
    assert np.all(result.valid)
    x, y = ANALYSIS_GRID.mesh_m()
    plane = a + b * x + c * y
    arithmetic = ATOL + RTOL * np.abs(plane)
    deviation = np.abs(result.values - plane)
    assert np.all(deviation <= bound + arithmetic)
    # The deviation is real curvature, not rounding: on this grid it reaches 0.91 of the bound.
    assert np.max(deviation) > bound / 2
    residual = best_fit_residual(ANALYSIS_GRID, result.values)
    assert np.all(np.abs(residual) <= least_squares_factor(ANALYSIS_GRID) * bound + arithmetic)
