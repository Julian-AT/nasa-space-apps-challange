"""The D29 window resampler (PRD 7, "Window resampler (D29)").

Terrain windows are resampled on a sphere per body, in float64. The radii below are the only
radii the resampler uses; ``@terraluna/core`` mirrors them for the browser probe (PRD 8.8).
Terrain distances derived from them are metres on the body sphere and are kept apart from radar
and model distances (PRD 6.5, 8.11).

:func:`resample_window` takes each pixel centre of a :class:`WindowGrid` through the inverse
spherical AEQD (:func:`aeqd_inverse`), into a source raster's pixel space by one of three
closed-form lookups (:class:`GeographicLookup`, :class:`PolarStereographicLookup`,
:class:`EquirectangularLookup`), and interpolates it bilinearly on the four neighbouring source
pixels (:func:`bilinear_resample`). Missing data stays NaN beside an explicit boolean mask.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

Body = Literal["moon", "mars", "earth"]
"""The bodies with a resampler sphere. Names are lower case and matched exactly."""

BODY_RADII_M: Final[Mapping[str, float]] = MappingProxyType(
    {
        "moon": 1737400.0,
        "mars": 3396190.0,
        # Mean radius. The sphere changes scale by under 0.5% on a 5 km window (PRD 7).
        "earth": 6371008.8,
    }
)
"""Sphere radius per body in metres, as Python floats (IEEE 754 binary64, numpy float64).

The table is read-only. Use :func:`body_radius_m` to look up a body by name.
"""


class UnknownBodyError(ValueError):
    """Raised when a body has no resampler sphere."""


def body_radius_m(body: str) -> float:
    """Return the D29 sphere radius of ``body`` in metres.

    Only ``moon``, ``mars`` and ``earth`` resolve. Matching is exact: ``"Moon"`` or ``" moon"``
    raise :class:`UnknownBodyError` rather than being normalised.
    """
    try:
        return BODY_RADII_M[body]
    except KeyError:
        known = ", ".join(BODY_RADII_M)
        raise UnknownBodyError(f"no D29 sphere for body {body!r}; known bodies: {known}") from None


class WindowGridError(ValueError):
    """Raised when a window width or pixel spacing cannot define a pixel-centre grid."""


@dataclass(frozen=True, eq=False)
class WindowGrid:
    """Pixel centres of a square analysis window, in metres from the window centre (PRD 7, D29).

    Column ``i`` sits at ``x_i = (i + 0.5) * spacing - width / 2`` (east positive) and row ``j``
    at ``y_j = width / 2 - (j + 0.5) * spacing`` (north positive, row 0 at the top).

    ``size`` is the largest ``n`` with ``n * spacing <= width`` in float64, so every pixel cell
    lies inside the window. The grid is anchored at the top-left corner: when the width is not a
    whole number of cells, a strip ``uncovered_m`` wide is left along the east and south edges.
    At the PRD's 5000 m window and 90 m grid that is 55 cells and a 50 m strip, and the centre
    pixel (27, 27) sits at (-25 m, 25 m).
    """

    width_m: float
    spacing_m: float
    size: int
    x_m: FloatArray
    y_m: FloatArray

    @property
    def uncovered_m(self) -> float:
        """Width of the strip along the east and south edges that no pixel cell covers."""
        return self.width_m - self.size * self.spacing_m

    def mesh_m(self) -> tuple[FloatArray, FloatArray]:
        """Return read-only 2-D ``(x, y)`` arrays indexed ``[row, column]``."""
        x, y = np.meshgrid(self.x_m, self.y_m, indexing="xy")
        return _read_only(x), _read_only(y)


def window_grid(width_m: float, spacing_m: float) -> WindowGrid:
    """Return the D29 pixel-centre grid for a window ``width_m`` wide at ``spacing_m``.

    Raises :class:`WindowGridError` if either length is not finite and positive, or if not even
    one cell fits.
    """
    width = float(width_m)
    spacing = float(spacing_m)
    for name, value in (("width_m", width), ("spacing_m", spacing)):
        if not (math.isfinite(value) and value > 0.0):
            raise WindowGridError(f"{name} must be finite and positive, got {value!r}")
    ratio = width / spacing
    if not math.isfinite(ratio):
        raise WindowGridError(f"width_m / spacing_m is not finite: {width!r} / {spacing!r}")
    size = math.floor(ratio)
    # Settle the count on the stated rule, n * spacing <= width, not on the rounded ratio.
    while size > 0 and size * spacing > width:
        size -= 1
    while (size + 1) * spacing <= width:
        size += 1
    if size < 1:
        raise WindowGridError(f"no {spacing!r} m cell fits in a {width!r} m window")
    centres = (np.arange(size, dtype=np.float64) + 0.5) * spacing
    return WindowGrid(
        width_m=width,
        spacing_m=spacing,
        size=size,
        x_m=_read_only(centres - width / 2),
        y_m=_read_only(width / 2 - centres),
    )


class AeqdDomainError(ValueError):
    """Raised when an AEQD centre, radius, offset or point lies outside the projection's domain."""


LONGITUDE_RANGE_RAD: Final[tuple[float, float]] = (-math.pi, math.pi)
"""Longitudes are radians in the half-open range [-pi, pi), with pi the float64 ``math.pi``.

The inverse returns longitudes in this range, and a window centre must lie in it. The forward
also accepts a point longitude of exactly ``math.pi``.

Each float64 longitude is the real angle it denotes on the true sphere, whose period is the
true 2 pi, 2.4e-16 rad more than ``2 * math.pi``. So ``math.pi`` and ``-math.pi`` are two
meridians 2.4e-16 rad apart, and the range falls that much short of a full turn.
"""

_PI: Final = math.pi
_TWO_PI: Final = 2.0 * math.pi
_HALF_PI: Final = math.pi / 2.0
# The largest float64 below math.pi: the east end of the longitude range.
_PI_BELOW: Final = math.nextafter(math.pi, 0.0)
# 2 pi - 2 * math.pi rounded to float64: the part of a true turn that 2 * math.pi leaves out.
_TWO_PI_LO: Final = 2.4492935982947064e-16
# pi/2 - math.pi / 2 rounded to float64 (a quarter of _TWO_PI_LO, exactly).
_HALF_PI_LO: Final = 6.123233995736766e-17
# Colatitudes and latitude differences up to which the forward uses series (radians).
_SERIES_MAX_RAD: Final = 2.0**-6
# Veltkamp's splitter for float64, 2^27 + 1.
_SPLITTER: Final = 134217729.0


def aeqd_inverse(
    x_m: npt.ArrayLike,
    y_m: npt.ArrayLike,
    *,
    lat0_rad: float,
    lon0_rad: float,
    radius_m: float,
) -> tuple[FloatArray, FloatArray]:
    """Map window coordinates to (latitude, longitude) by the inverse spherical AEQD (PRD 7).

    ``x_m`` (east) and ``y_m`` (north) are metres on the sphere of radius ``radius_m``, measured
    from the window centre ``(lat0_rad, lon0_rad)``. Returns float64 ``(lat, lon)`` in radians,
    broadcast to the shape of the offsets, with ``lat`` in [-pi/2, pi/2] and ``lon`` in
    [-pi, pi) (:data:`LONGITUDE_RANGE_RAD`).

    The PRD formula, with rho = sqrt(x^2 + y^2) and c = rho / R::

        S   = cos c * sin phi0 + y * sin c * cos phi0 / rho
        phi = asin(S)
        lam = lam0 + atan2(x * sin c, rho * cos phi0 * cos c - y * sin phi0 * sin c)

    and (phi, lam) = (phi0, lam0) exactly at rho = 0, with no division by zero.

    Evaluation: with e = x / rho, n = y / rho and k = 2 sin^2(c / 2) = 1 - cos c, the PRD's
    asin argument is ``S = sin phi0 + s`` and its atan2 arguments divided by rho are
    ``N = e sin c`` and ``M = cos phi0 + m``, where the increments

        s = n sin c cos phi0 - k sin phi0,   m = -k cos phi0 - n sin c sin phi0

    are small on a window and carry no cancellation. lam = lam0 + atan2(N, M) is the PRD
    longitude. On the unit sphere S = sin phi and H = hypot(N, M) = cos phi, so with
    h = H - cos phi0 = (N^2 + m (2 cos phi0 + m)) / (H + cos phi0)::

        sin(phi - phi0) = s cos phi0 - h sin phi0
        cos(phi - phi0) = 1 + h cos phi0 + s sin phi0
        phi = phi0 + atan2(sin(phi - phi0), cos(phi - phi0))

    which equals asin(S) in real arithmetic. On a window every rounding before the final
    additions to phi0 and lam0 scales a small quantity, so the latitude is within about half an
    ulp of phi plus a relative error of the small increment, whatever the accuracy of the
    platform's sin, cos and atan2. The literal asin(S) amplifies the rounding of S by
    1 / cos phi: centred at +-89.9 deg it was measured against 40-digit mpmath at 1.6e-7 to
    3.9e-7 m position error on the 5 km, 90 m grid (Moon to Earth), failing the PRD 7
    round-trip and distance tolerances. atan2(S, H) on the full S and H keeps their order-one
    rounding: on the Earth sphere at y = 25 m it used up to 1.01 of the PRD 7 round-trip
    allowance in metres (1e-12 m + 1e-10 * |y|) against the exact forward, where the increment
    form uses 0.28. ``@terraluna/core`` must evaluate the same way (PRD 8.8).

    For a long offset that ends next to a pole, the increment is of order one and phi0 plus it
    can round past +-math.pi / 2, so the latitude is clipped to [-pi/2, pi/2]. The exact
    latitude lies within 6.2e-17 of that range and the nearest float64 beyond it is 2.2e-16
    out, so the clip only moves such a value closer to the exact latitude.

    Longitude: lam0 + atan2(N, M) is formed exactly as a sum of two float64 values (Knuth's
    two-sum). It lies in [-2 pi, 2 pi), and when it falls outside [-pi, pi) it turns by the
    true 2 pi, carried as 2 * math.pi plus its residual 2.4e-16 (:data:`LONGITUDE_RANGE_RAD`).
    The result is the float64 in [-pi, pi) nearest the PRD longitude on the true circle. Next
    to the antimeridian, where the range is 2.4e-16 short of a turn, that is ``-math.pi`` or
    the largest float64 below ``math.pi``, whichever is nearer. A turn by 2 * math.pi alone
    would move the point by 2.4e-16 rad, 1.6e-9 m on the Earth sphere.

    Raises :class:`AeqdDomainError` for a centre outside [-pi/2, pi/2] x [-pi, pi), a radius
    that is not finite and positive, a non-finite offset, or rho > pi * R.
    """
    lat0, lon0, radius = _centre(lat0_rad, lon0_rad, radius_m)
    x, y = np.broadcast_arrays(np.asarray(x_m, dtype=np.float64), np.asarray(y_m, dtype=np.float64))
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
        raise AeqdDomainError("window offsets must be finite")
    rho = np.hypot(x, y)
    if np.any(rho > _PI * radius):
        raise AeqdDomainError(f"offsets beyond the antipode (rho > pi * {radius!r} m)")
    at_centre = rho == 0.0
    safe_rho = np.where(at_centre, 1.0, rho)
    east = x / safe_rho
    north = y / safe_rho
    c = rho / radius
    sin_c = np.sin(c)
    k = 2.0 * np.sin(c / 2.0) ** 2
    sin_lat0 = math.sin(lat0)
    cos_lat0 = math.cos(lat0)

    s = north * sin_c * cos_lat0 - k * sin_lat0
    m = -k * cos_lat0 - north * sin_c * sin_lat0
    lon_num = east * sin_c
    lon_den = cos_lat0 + m
    # cos_lat0 >= cos(math.pi / 2) > 0 in float64, so the denominator is positive.
    h = (lon_num * lon_num + m * (2.0 * cos_lat0 + m)) / (np.hypot(lon_num, lon_den) + cos_lat0)
    sin_dlat = s * cos_lat0 - h * sin_lat0
    cos_dlat = 1.0 + h * cos_lat0 + s * sin_lat0
    lat = lat0 + np.arctan2(sin_dlat, cos_dlat)
    lat = np.where(at_centre, lat0, np.clip(lat, -_HALF_PI, _HALF_PI))
    lon_hi, lon_lo = _two_sum(np.full_like(lon_num, lon0), np.arctan2(lon_num, lon_den))
    lon = np.where(at_centre, lon0, _wrap_longitude(lon_hi, lon_lo))
    return _float_array(lat), _float_array(lon)


def aeqd_forward(
    lat_rad: npt.ArrayLike,
    lon_rad: npt.ArrayLike,
    *,
    lat0_rad: float,
    lon0_rad: float,
    radius_m: float,
) -> tuple[FloatArray, FloatArray]:
    """Map (latitude, longitude) to window coordinates by the forward spherical AEQD.

    Written from the forward formulas, not by inverting :func:`aeqd_inverse` (Snyder 1987,
    USGS Professional Paper 1395, azimuthal equidistant projection, spherical form). With
    d = lam - lam0::

        E = cos phi * sin d
        N = cos phi0 * sin phi - sin phi0 * cos phi * cos d
        U = sin phi0 * sin phi + cos phi0 * cos phi * cos d      (cos c)
        c = atan2(hypot(E, N), U)
        x = R * c * E / hypot(E, N),  y = R * c * N / hypot(E, N)

    which is Snyder's x = R k' cos phi sin d, y = R k' (cos phi0 sin phi - sin phi0 cos phi
    cos d) with k' = c / sin c, and c from atan2 rather than acos, which loses precision for
    short distances. The centre maps to (0, 0) exactly.

    Evaluation: N and U are computed from the latitude difference, with hav = 2 sin^2(d / 2)
    = 1 - cos d, as N = sin(phi - phi0) + sin phi0 cos phi hav and U = cos(phi - phi0) -
    cos phi0 cos phi hav. These equal the expressions above in real arithmetic. Written
    directly, N subtracts two order-one products. On the Earth sphere their rounding moved y by
    4e-10 m against the exact reference, 254 times the PRD 7 allowance (1e-12 m + 1e-10 * |y|)
    at y = 6 mm, and it added up to 0.29 of the allowance at the 25 m grid offsets. phi - phi0
    is exact in float64 when the two are within a factor of two (Sterbenz), so on a window
    every rounding here scales a small quantity.

    The longitude difference d is carried exactly, as an unevaluated sum d_hi + d_lo of two
    float64 values (Knuth's two-sum), and sin d and hav enter to first order in d_lo. When
    |d| > pi it turns by the true 2 pi: d_hi moves by 2 * math.pi, which is exact (Sterbenz),
    and d_lo by the residual 2.4e-16 (:data:`LONGITUDE_RANGE_RAD`). Without the residual, a
    point across the antimeridian moved by 1.6e-9 m on the Earth sphere, 12 times the PRD 7
    allowance at 1.27 m. A rounded d would be off by up to half an ulp of pi, 2.2e-16 rad,
    wherever |d| is near pi, which happens next to the antimeridian and on the far side of a
    pole anywhere: there x scales with sin d, and in a window centred within 3.6 km of a pole
    the rounding moved x near 0 by up to 1.14 times the PRD 7 allowance against the exact
    forward.

    N is summed in double-double arithmetic: each product and the final sum are carried as
    unevaluated sums of two float64 values (Dekker's product with Veltkamp's split, Knuth's
    two-sum). hav is 1 - cos d for cos d < 0.5 and 2 sin^2(d / 2) otherwise, exact apart from
    the platform's cos or sin. Within 2^-6 rad of a pole, cos phi and sin phi0 come from Taylor
    series in the colatitude, which is exact as (pi/2 - |phi|) plus the part of pi/2 that
    ``math.pi / 2`` leaves out; for |phi - phi0| <= 2^-6, sin(phi - phi0) comes from its
    series. In a window centred near a pole, N near 0 is the difference of two terms the size
    of the distance to the pole, up to the 2.5 km half width. Rounded in float64, they moved y
    near 0 by up to 1.28 times the PRD 7 allowance against the exact forward; there only the
    platform's cos d or sin(d / 2) is now rounded before the final sum. Farther from a pole the
    platform's cos phi and sin phi0 are used: the two terms are then at most about 120 m (Moon,
    at a colatitude of 2^-6), where float64 rounding is about 1e-13 m.

    ``lat_rad`` must lie in [-pi/2, pi/2] and ``lon_rad`` in [-pi, pi]; the centre as for
    :func:`aeqd_inverse`. Returns float64 metres broadcast to the shape of the inputs.
    """
    lat0, lon0, radius = _centre(lat0_rad, lon0_rad, radius_m)
    lat, lon = np.broadcast_arrays(
        np.asarray(lat_rad, dtype=np.float64), np.asarray(lon_rad, dtype=np.float64)
    )
    if not (np.all(np.isfinite(lat)) and np.all(np.isfinite(lon))):
        raise AeqdDomainError("latitudes and longitudes must be finite")
    if np.any(np.abs(lat) > _HALF_PI):
        raise AeqdDomainError("latitudes must lie in [-pi/2, pi/2]")
    if np.any(np.abs(lon) > _PI):
        raise AeqdDomainError("longitudes must lie in [-pi, pi]")
    d_hi, d_lo = _two_sum(lon, np.full_like(lon, -lon0))
    over = (d_hi > _PI) | ((d_hi == _PI) & (d_lo > 0.0))
    under = (d_hi < -_PI) | ((d_hi == -_PI) & (d_lo < 0.0))
    turns = np.where(over, -1.0, np.where(under, 1.0, 0.0))
    d_hi, d_lo = _two_sum(d_hi + turns * _TWO_PI, d_lo + turns * _TWO_PI_LO)
    sin_d_hi = np.sin(d_hi)
    cos_d_hi = np.cos(d_hi)
    sin_d = sin_d_hi + cos_d_hi * d_lo
    hav_hi, hav_lo = _haversine_dd(d_hi, cos_d_hi)
    hav_lo = hav_lo + sin_d_hi * d_lo
    cos_lat_hi, cos_lat_lo = _cos_lat_dd(lat)
    sin_lat0_hi, sin_lat0_lo = _sin_lat_dd(np.full_like(lat, lat0))
    dlat = lat - lat0
    sin_dlat_hi, sin_dlat_lo = _sin_small_dd(dlat)

    p_hi, p_lo = _dd_mul(cos_lat_hi, cos_lat_lo, hav_hi, hav_lo)
    q_hi, q_lo = _dd_mul(sin_lat0_hi, sin_lat0_lo, p_hi, p_lo)
    n_hi, n_lo = _two_sum(sin_dlat_hi, q_hi)
    north = n_hi + (n_lo + (sin_dlat_lo + q_lo))
    east = cos_lat_hi * sin_d
    up = np.cos(dlat) - math.cos(lat0) * cos_lat_hi * hav_hi
    horizontal = np.hypot(east, north)
    at_centre = horizontal == 0.0
    c = np.arctan2(horizontal, up)
    scale = np.where(at_centre, 0.0, radius * c / np.where(at_centre, 1.0, horizontal))
    return _float_array(scale * east), _float_array(scale * north)


def _centre(lat0_rad: float, lon0_rad: float, radius_m: float) -> tuple[float, float, float]:
    lat0 = float(lat0_rad)
    lon0 = float(lon0_rad)
    radius = float(radius_m)
    if not (math.isfinite(radius) and radius > 0.0):
        raise AeqdDomainError(f"radius_m must be finite and positive, got {radius!r}")
    if not (math.isfinite(lat0) and -_HALF_PI <= lat0 <= _HALF_PI):
        raise AeqdDomainError(f"lat0_rad must lie in [-pi/2, pi/2], got {lat0!r}")
    if not (math.isfinite(lon0) and -_PI <= lon0 < _PI):
        raise AeqdDomainError(f"lon0_rad must lie in [-pi, pi), got {lon0!r}")
    return lat0, lon0, radius


def _two_sum(a: FloatArray, b: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Return (s, e) with s = fl(a + b) and s + e = a + b exactly (Knuth's two-sum)."""
    s = a + b
    b_part = s - a
    return s, (a - (s - b_part)) + (b - b_part)


def _wrap_longitude(a_hi: FloatArray, a_lo: FloatArray) -> FloatArray:
    """The float64 in [-pi, pi) nearest the angle a = a_hi + a_lo on the true circle.

    ``a_hi`` is fl(a) and |a| < 2 pi. Outside [-pi, pi), a turns by the true 2 pi: a_hi by
    2 * math.pi, exact by Sterbenz, and a_lo by the residual. The rounded result b_hi is then
    the nearest float64, and it lies outside the range only next to the antimeridian, inside
    the 2.4e-16 rad that the range falls short of a turn. Below ``-math.pi`` the nearest value
    in range is ``-math.pi``. At or above ``math.pi`` it is the nearer of the largest float64
    below ``math.pi`` and ``-math.pi``, which as an angle there is math.pi plus the residual.
    """
    over = (a_hi > _PI) | ((a_hi == _PI) & (a_lo >= 0.0))
    under = (a_hi < -_PI) | ((a_hi == -_PI) & (a_lo < 0.0))
    turns = np.where(over, -1.0, np.where(under, 1.0, 0.0))
    b_hi, b_lo = _two_sum(a_hi + turns * _TWO_PI, a_lo + turns * _TWO_PI_LO)
    to_east_end = (b_hi - _PI_BELOW) + b_lo
    to_west_end = (_PI - b_hi) + (_TWO_PI_LO - b_lo)
    east = np.where(to_east_end < to_west_end, _PI_BELOW, -_PI)
    wrapped: FloatArray = np.where(b_hi >= _PI, east, np.where(b_hi < -_PI, -_PI, b_hi))
    return wrapped


def _split(a: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Return (hi, lo) with hi + lo = a exactly and 26 significant bits in each (Veltkamp)."""
    t = _SPLITTER * a
    hi = t - (t - a)
    return hi, a - hi


def _two_prod(a: FloatArray, b: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Return (p, e) with p = fl(a * b) and p + e = a * b exactly (Dekker's product)."""
    p = a * b
    a_hi, a_lo = _split(a)
    b_hi, b_lo = _split(b)
    return p, ((a_hi * b_hi - p) + a_hi * b_lo + a_lo * b_hi) + a_lo * b_lo


def _dd_mul(
    a_hi: FloatArray, a_lo: FloatArray, b_hi: FloatArray, b_lo: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """Product of two double-double values, to about 2^-104 relative."""
    p, e = _two_prod(a_hi, b_hi)
    return _two_sum(p, e + (a_hi * b_lo + a_lo * b_hi))


def _haversine_dd(d: FloatArray, cos_d: FloatArray) -> tuple[FloatArray, FloatArray]:
    """1 - cos d as a double-double value, from the platform's cos d or sin(d / 2).

    For cos d < 0.5, 1 - fl(cos d) is taken exactly; otherwise 2 fl(sin(d / 2))^2 is. Either
    way the only rounding is that of the platform function.
    """
    one = np.ones_like(d)
    wide_hi, wide_lo = _two_sum(one, -cos_d)
    half = np.sin(d / 2.0)
    narrow_hi, narrow_lo = _two_prod(half, half)
    wide = cos_d < 0.5
    return (
        np.where(wide, wide_hi, 2.0 * narrow_hi),
        np.where(wide, wide_lo, 2.0 * narrow_lo),
    )


def _sin_series_dd(t_hi: FloatArray, t_lo: FloatArray) -> tuple[FloatArray, FloatArray]:
    """sin t for |t| <= 2^-6 as a double-double value, t = t_hi + t_lo.

    The series stops after t^7; the next term is below 1e-20 of t.
    """
    t2 = t_hi * t_hi
    tail = t_hi * t2 * (-1.0 / 6.0 + t2 * (1.0 / 120.0 - t2 / 5040.0))
    return _two_sum(t_hi, t_lo + tail)


def _colatitude_dd(lat: FloatArray) -> tuple[FloatArray, FloatArray]:
    """pi/2 - |lat| as a double-double value; math.pi / 2 - |lat| is exact for |lat| >= pi/4."""
    return _two_sum(_HALF_PI - np.abs(lat), np.full_like(lat, _HALF_PI_LO))


def _sin_small_dd(t: FloatArray) -> tuple[FloatArray, FloatArray]:
    """sin t as a double-double value: by series for |t| <= 2^-6, else the platform's sin."""
    series_hi, series_lo = _sin_series_dd(t, np.zeros_like(t))
    small = np.abs(t) <= _SERIES_MAX_RAD
    return np.where(small, series_hi, np.sin(t)), np.where(small, series_lo, 0.0)


def _cos_lat_dd(lat: FloatArray) -> tuple[FloatArray, FloatArray]:
    """cos lat as a double-double value: sin of the colatitude by series near a pole."""
    colat_hi, colat_lo = _colatitude_dd(lat)
    series_hi, series_lo = _sin_series_dd(colat_hi, colat_lo)
    polar = colat_hi <= _SERIES_MAX_RAD
    return np.where(polar, series_hi, np.cos(lat)), np.where(polar, series_lo, 0.0)


def _sin_lat_dd(lat: FloatArray) -> tuple[FloatArray, FloatArray]:
    """sin lat as a double-double value: +-(1 - w), w = 1 - cos(colatitude), near a pole.

    The series for w stops after t^8; the next term is below 1e-24.
    """
    colat_hi, colat_lo = _colatitude_dd(lat)
    t2 = colat_hi * colat_hi
    w = t2 * (0.5 - t2 * (1.0 / 24.0 - t2 * (1.0 / 720.0 - t2 / 40320.0)))
    w = w + colat_hi * colat_lo
    near_hi, near_lo = _two_sum(np.ones_like(lat), -w)
    sign = np.where(lat < 0.0, -1.0, 1.0)
    polar = colat_hi <= _SERIES_MAX_RAD
    return np.where(polar, sign * near_hi, np.sin(lat)), np.where(polar, sign * near_lo, 0.0)


def _float_array(values: npt.ArrayLike) -> FloatArray:
    return np.asarray(values, dtype=np.float64)


def _read_only(values: FloatArray) -> FloatArray:
    frozen = np.array(values, dtype=np.float64, copy=True)
    frozen.flags.writeable = False
    return frozen


# ---------------------------------------------------------------------------------------------
# Source lookup (PRD 7, "Window resampler (D29)": source lookup)

BoolArray = npt.NDArray[np.bool_]

# pi / 180 as an unevaluated sum hi + lo of two float64 values.
_DEG_TO_RAD_HI: Final = 0.017453292519943295
_DEG_TO_RAD_LO: Final = 2.9486522708701687e-19
# pi - math.pi rounded to float64 (half of _TWO_PI_LO, exactly).
_PI_LO: Final = 1.2246467991473532e-16
# The float64 just above math.pi / 2: inverse latitudes that round to it are clipped to the pole.
_HALF_PI_ABOVE: Final = math.nextafter(math.pi / 2.0, 4.0)

Pole = Literal["north", "south"]
"""The pole a polar stereographic raster is centred on."""


class SourceLookupError(ValueError):
    """Raised for a missing or invalid georeferencing parameter, or a point outside a form."""


@dataclass(frozen=True, kw_only=True)
class GeographicLookup:
    """Geographic latitude-longitude raster (Copernicus DEM, WorldCover), PRD 7 source lookup.

    The georeferencing is the raster's north-up geotransform in degrees, as GDAL and rasterio
    report it: ``origin_lon_deg`` and ``origin_lat_deg`` locate the outer corner of pixel (row
    0, column 0), and the pixel sizes are signed (the height is negative for a north-up raster).
    Columns run east, so ``pixel_width_deg`` must be positive. Rotated geotransforms are not
    supported. Every parameter is required; none is read from a product.

    Projected coordinates are (longitude, latitude) in degrees. Pixel coordinates are
    fractional indices of pixel centres: (column, row) = (0, 0) is the centre of the top-left
    pixel. A longitude maps to the column east of ``origin_lon_deg`` by its difference reduced
    to [0, 360), so a raster may cross the antimeridian or use the 0 to 360 convention; there
    is no wrap between the first and last column of a raster that spans a full turn.

    This is the only form the browser probe needs (PRD 7, 8.8): ``@terraluna/core`` mirrors
    ``to_pixel``. Evaluation: lon_deg = lon * (180 / pi) and lat_deg = lat * (180 / pi) are
    rounded once (``numpy.degrees``); east = lon_deg - origin_lon_deg is carried exactly as two
    float64 values (Knuth's two-sum) and reduced to [0, 360) by a whole number of 360 degree
    turns, settled by the sign of the exact remainder; then column = east / pixel_width_deg -
    0.5 and row = (lat_deg - origin_lat_deg) / pixel_height_deg - 0.5, the difference again
    carried exactly. The only roundings after the degrees are those of the final sums, the divisions
    and the half-pixel shifts, so a column near 0 keeps its precision next to a large origin.
    """

    origin_lon_deg: float
    origin_lat_deg: float
    pixel_width_deg: float
    pixel_height_deg: float

    def __post_init__(self) -> None:
        _store_real(self, "origin_lon_deg", minimum=-360.0, maximum=360.0)
        _store_real(self, "origin_lat_deg", minimum=-90.0, maximum=90.0)
        if _store_real(self, "pixel_width_deg") <= 0.0:
            raise SourceLookupError("pixel_width_deg must be positive: columns run east")
        _store_nonzero(self, "pixel_height_deg")

    def project(
        self, lat_rad: npt.ArrayLike, lon_rad: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """(longitude, latitude) in degrees of points given in radians."""
        lat, lon = _points(lat_rad, lon_rad)
        return _float_array(np.degrees(lon)), _float_array(np.degrees(lat))

    def unproject(
        self, lon_deg: npt.ArrayLike, lat_deg: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """(latitude, longitude) in radians, the longitude in [-pi, pi), of degrees."""
        lon, lat = _projected(lon_deg, lat_deg)
        if np.any(np.abs(lat) > 90.0):
            raise SourceLookupError("latitudes must lie in [-90, 90] degrees")
        turns = np.round(lon / 360.0)
        reduced_hi, reduced_lo = _two_sum(lon, -360.0 * turns)
        lon_hi, lon_lo = _deg_to_rad(reduced_hi, reduced_lo)
        lat_hi, lat_lo = _deg_to_rad(lat, np.zeros_like(lat))
        latitude = np.clip(lat_hi + lat_lo, -_HALF_PI, _HALF_PI)
        return _float_array(latitude), _float_array(_wrap_longitude(lon_hi, lon_lo))

    def pixel_from_projected(
        self, lon_deg: npt.ArrayLike, lat_deg: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """Pixel coordinates (column, row) of (longitude, latitude) in degrees."""
        lon, lat = _projected(lon_deg, lat_deg)
        east_hi, east_lo = _two_sum(lon, np.full_like(lon, -self.origin_lon_deg))
        # The quotient can be off by one turn (rounding, or underflow to -0.0 for a subnormal
        # difference), so the turn is settled by the sign of the exact remainder.
        turns = np.floor(east_hi / 360.0)
        shifted_hi, shifted_lo = _two_sum(east_hi, -360.0 * turns)
        shifted_lo = shifted_lo + east_lo
        below = (shifted_hi < 0.0) | ((shifted_hi == 0.0) & (shifted_lo < 0.0))
        above = (shifted_hi > 360.0) | ((shifted_hi == 360.0) & (shifted_lo >= 0.0))
        turns = turns - below + above
        shifted_hi, shifted_lo = _two_sum(east_hi, -360.0 * turns)
        east = shifted_hi + (shifted_lo + east_lo)
        north_hi, north_lo = _two_sum(lat, np.full_like(lat, -self.origin_lat_deg))
        column = east / self.pixel_width_deg - 0.5
        row = (north_hi + north_lo) / self.pixel_height_deg - 0.5
        return _float_array(column), _float_array(row)

    def projected_from_pixel(
        self, column: npt.ArrayLike, row: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """(longitude, latitude) in degrees of pixel coordinates, as the geotransform gives them."""
        col, rw = _pixel_pair(column, row)
        lon = _affine_inverse(col, self.pixel_width_deg, self.origin_lon_deg, 0.0)
        lat = _affine_inverse(rw, self.pixel_height_deg, self.origin_lat_deg, 0.0)
        return lon, lat

    def to_pixel(
        self, lat_rad: npt.ArrayLike, lon_rad: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """Pixel coordinates (column, row) of points in radians: project, then geotransform."""
        return self.pixel_from_projected(*self.project(lat_rad, lon_rad))

    def from_pixel(
        self, column: npt.ArrayLike, row: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """(latitude, longitude) in radians of pixel coordinates."""
        return self.unproject(*self.projected_from_pixel(column, row))


@dataclass(frozen=True, kw_only=True)
class PolarStereographicLookup:
    """Polar stereographic raster on the sphere (LOLA, Mini-RF, PGDA), PRD 7 source lookup.

    The projection touches ``pole`` and is taken from the opposite pole, on a sphere of
    ``radius_m`` (the product's sphere, an explicit parameter). With colatitude chi = pi/2 -
    lat for the north aspect and pi/2 + lat for the south::

        rho = 2 R k0 tan(chi / 2)
        E = rho sin(lon - lon0),   N = -rho cos(lon - lon0) (north) or +rho cos(lon - lon0) (south)

    so the central meridian ``central_meridian_deg`` runs down the map from the north pole and
    up the map from the south pole, as in PROJ's ``stere`` with lat_0 = +-90 (Snyder 1987, USGS
    Professional Paper 1395, polar stereographic, spherical form). The scale k0 at the pole is
    ``scale_factor`` when ``latitude_of_true_scale_deg`` is +-90, and (1 + sin |lat_ts|) / 2,
    true scale on the parallel lat_ts, otherwise. A scale factor other than 1 with a true-scale
    latitude away from the pole is rejected: PROJ ignores k_0 then, so the pair is ambiguous.
    The true-scale latitude lies in the pole's hemisphere: (0, 90] north, [-90, 0) south.

    ``project`` returns (E, N) in metres from the pole, before false easting and northing;
    ``pixel_from_projected`` adds ``false_easting_m`` and ``false_northing_m`` and applies the
    north-up geotransform (origin at the outer corner of pixel (0, 0), signed pixel sizes in
    metres). The form is Python-only (PRD 7).

    Evaluation: chi is carried exactly as two float64 values, (pi/2 -+ lat) plus the part of
    pi/2 that ``math.pi / 2`` leaves out, and tan(chi / 2) takes the low part to first order.
    Beyond the equator, where chi > pi/2, it is 1 / tan((pi - chi) / 2) instead, so that the
    tangent is never evaluated near its pole. lon - lon0 is carried as two float64 values, with
    lon0 from degrees in double-double, and its sine and cosine take the low part to first
    order. The opposite pole has no image and raises :class:`SourceLookupError`.
    """

    pole: Pole
    central_meridian_deg: float
    latitude_of_true_scale_deg: float
    scale_factor: float
    radius_m: float
    false_easting_m: float
    false_northing_m: float
    origin_x_m: float
    origin_y_m: float
    pixel_width_m: float
    pixel_height_m: float

    def __post_init__(self) -> None:
        pole: object = self.pole
        if pole is None:
            raise SourceLookupError("pole is required, got None")
        if not (isinstance(pole, str) and pole in ("north", "south")):
            raise SourceLookupError(f"pole must be 'north' or 'south', got {pole!r}")
        _store_real(self, "central_meridian_deg", minimum=-360.0, maximum=360.0)
        lat_ts = _store_real(self, "latitude_of_true_scale_deg")
        if not (0.0 < lat_ts <= 90.0 if pole == "north" else -90.0 <= lat_ts < 0.0):
            hemisphere = "(0, 90]" if pole == "north" else "[-90, 0)"
            raise SourceLookupError(
                f"latitude_of_true_scale_deg must lie in {hemisphere} for the {pole} pole, "
                f"got {lat_ts!r}"
            )
        scale = _store_real(self, "scale_factor")
        if scale <= 0.0:
            raise SourceLookupError(f"scale_factor must be positive, got {scale!r}")
        if abs(lat_ts) != 90.0 and scale != 1.0:
            raise SourceLookupError(
                "scale_factor must be 1 when latitude_of_true_scale_deg is off the pole: give the "
                "scale either by a true-scale latitude or by a scale factor at the pole"
            )
        _store_positive(self, "radius_m")
        _store_projected_affine(self)

    def project(
        self, lat_rad: npt.ArrayLike, lon_rad: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """(E, N) in metres from the pole of points given in radians."""
        lat, lon = _points(lat_rad, lon_rad)
        north = self.pole == "north"
        toward = lat if north else -lat
        if np.any(toward == -_HALF_PI):
            raise SourceLookupError("polar stereographic has no image of the opposite pole")
        half_pi = np.full_like(toward, _HALF_PI)
        near_hi, near_lo = _two_sum(half_pi, -toward)
        near_hi, near_lo = _two_sum(near_hi, near_lo + _HALF_PI_LO)
        far_hi, far_lo = _two_sum(half_pi, toward)
        far_hi, far_lo = _two_sum(far_hi, far_lo + _HALF_PI_LO)
        tangent = np.where(
            toward >= 0.0, _tan_half(near_hi, near_lo), 1.0 / _tan_half(far_hi, far_lo)
        )
        rho = 2.0 * self.radius_m * self._scale_at_pole() * tangent
        lon0_hi, lon0_lo = _degrees_to_rad(self.central_meridian_deg)
        d_hi, d_lo = _two_sum(lon, np.full_like(lon, -lon0_hi))
        d_hi, d_lo = _two_sum(d_hi, d_lo - lon0_lo)
        sin_d_hi = np.sin(d_hi)
        cos_d_hi = np.cos(d_hi)
        sin_d = sin_d_hi + cos_d_hi * d_lo
        cos_d = cos_d_hi - sin_d_hi * d_lo
        northing = -rho * cos_d if north else rho * cos_d
        return _float_array(rho * sin_d), _float_array(northing)

    def unproject(
        self, easting_m: npt.ArrayLike, northing_m: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """(latitude, longitude) in radians of (E, N) from the pole; the pole gets lon0."""
        east, north_m = _projected(easting_m, northing_m)
        rho = np.hypot(east, north_m)
        colatitude = 2.0 * np.arctan(rho / (2.0 * self.radius_m * self._scale_at_pole()))
        lat_hi, lat_lo = _two_sum(np.full_like(rho, _HALF_PI), -colatitude)
        lat = np.clip(lat_hi + (lat_lo + _HALF_PI_LO), -_HALF_PI, _HALF_PI)
        if self.pole == "north":
            angle = np.arctan2(east, -north_m)
        else:
            lat = -lat
            angle = np.arctan2(east, north_m)
        angle = np.where(rho == 0.0, 0.0, angle)
        lon0_hi, lon0_lo = _degrees_to_rad(self.central_meridian_deg)
        lon_hi, lon_lo = _two_sum(np.full_like(angle, lon0_hi), angle)
        lon_hi, lon_lo = _two_sum(lon_hi, lon_lo + lon0_lo)
        return _float_array(lat), _float_array(_wrap_longitude(lon_hi, lon_lo))

    def pixel_from_projected(
        self, easting_m: npt.ArrayLike, northing_m: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """Pixel coordinates (column, row) of (E, N): false easting and northing, geotransform."""
        return _pixel_from_projected(self, easting_m, northing_m)

    def projected_from_pixel(
        self, column: npt.ArrayLike, row: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """(E, N) in metres from the pole of pixel coordinates."""
        return _projected_from_pixel(self, column, row)

    def to_pixel(
        self, lat_rad: npt.ArrayLike, lon_rad: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """Pixel coordinates (column, row) of points in radians: project, then geotransform."""
        return self.pixel_from_projected(*self.project(lat_rad, lon_rad))

    def from_pixel(
        self, column: npt.ArrayLike, row: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """(latitude, longitude) in radians of pixel coordinates."""
        return self.unproject(*self.projected_from_pixel(column, row))

    def _scale_at_pole(self) -> float:
        if abs(self.latitude_of_true_scale_deg) == 90.0:
            return self.scale_factor
        lat_hi, lat_lo = _degrees_to_rad(abs(self.latitude_of_true_scale_deg))
        return (1.0 + (math.sin(lat_hi) + math.cos(lat_hi) * lat_lo)) / 2.0


@dataclass(frozen=True, kw_only=True)
class EquirectangularLookup:
    """Equirectangular raster on the sphere (CTX DTMs), PRD 7 source lookup.

    On a sphere of ``radius_m`` (the product's sphere, an explicit parameter), with lon0 =
    ``center_longitude_deg``, lat_ts = ``standard_parallel_deg`` (the latitude of true scale)
    and lat_0 = ``latitude_of_origin_deg``::

        E = R cos(lat_ts) (lon - lon0),   N = R (lat - lat_0)

    as in PROJ's ``eqc`` (Snyder 1987, equidistant cylindrical, spherical form), with lon - lon0
    reduced to [-pi, pi) on the true circle. ``project`` returns (E, N) in metres before false
    easting and northing; ``pixel_from_projected`` adds ``false_easting_m`` and
    ``false_northing_m`` and applies the north-up geotransform (origin at the outer corner of
    pixel (0, 0), signed pixel sizes in metres). The form is Python-only (PRD 7).

    Evaluation: lon0 and lat_0 come from degrees in double-double, and lon - lon0 and lat - lat_0
    are carried as two float64 values before the final products, so E and N keep their
    precision near 0. ``unproject`` raises :class:`SourceLookupError` for an easting beyond
    pi R cos(lat_ts) or a latitude beyond a pole.
    """

    center_longitude_deg: float
    standard_parallel_deg: float
    latitude_of_origin_deg: float
    radius_m: float
    false_easting_m: float
    false_northing_m: float
    origin_x_m: float
    origin_y_m: float
    pixel_width_m: float
    pixel_height_m: float

    def __post_init__(self) -> None:
        _store_real(self, "center_longitude_deg", minimum=-360.0, maximum=360.0)
        standard = _store_real(self, "standard_parallel_deg")
        if not -90.0 < standard < 90.0:
            raise SourceLookupError(
                f"standard_parallel_deg must lie in (-90, 90), got {standard!r}"
            )
        _store_real(self, "latitude_of_origin_deg", minimum=-90.0, maximum=90.0)
        _store_positive(self, "radius_m")
        _store_projected_affine(self)

    def project(
        self, lat_rad: npt.ArrayLike, lon_rad: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """(E, N) in metres of points given in radians."""
        lat, lon = _points(lat_rad, lon_rad)
        lon0_hi, lon0_lo = _degrees_to_rad(self.center_longitude_deg)
        d_hi, d_lo = _two_sum(lon, np.full_like(lon, -lon0_hi))
        d_hi, d_lo = _two_sum(d_hi, d_lo - lon0_lo)
        # Reduce to [-pi, pi) on the true circle; a turn is 2 * math.pi plus _TWO_PI_LO. The turn
        # is decided against the centre's antimeridian lon0 + pi, formed exactly in degrees
        # before the conversion: for a centre on 180 deg it is exactly lon = 0, where the
        # rounded difference above cannot tell pi - 1e-34 from pi.
        east_hi, east_lo = _two_sum(
            np.asarray(_reduced_degrees(self.center_longitude_deg)), np.asarray(180.0)
        )
        east_hi, east_lo = _deg_to_rad(np.asarray([float(east_hi)]), np.asarray([float(east_lo)]))
        west_hi, west_lo = _two_sum(east_hi, np.asarray([-_TWO_PI]))
        west_lo = west_lo + (east_lo - _TWO_PI_LO)
        over = _at_or_above(lon, float(east_hi[0]), float(east_lo[0]))
        under = ~_at_or_above(lon, float(west_hi[0]), float(west_lo[0]))
        turns = np.where(over, -1.0, np.where(under, 1.0, 0.0))
        d_hi, d_lo = _two_sum(d_hi + turns * _TWO_PI, d_lo + turns * _TWO_PI_LO)
        scale = self._parallel_scale()
        lat0_hi, lat0_lo = _degrees_to_rad(self.latitude_of_origin_deg)
        n_hi, n_lo = _two_sum(lat, np.full_like(lat, -lat0_hi))
        easting = scale * d_hi + scale * d_lo
        northing = self.radius_m * (n_hi + (n_lo - lat0_lo))
        return _float_array(easting), _float_array(northing)

    def unproject(
        self, easting_m: npt.ArrayLike, northing_m: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """(latitude, longitude) in radians, the longitude in [-pi, pi), of (E, N)."""
        east, north = _projected(easting_m, northing_m)
        turn = east / self._parallel_scale()
        if np.any(np.abs(turn) > _PI):
            raise SourceLookupError("eastings must lie within pi R cos(lat_ts) of lon0")
        lon0_hi, lon0_lo = _degrees_to_rad(self.center_longitude_deg)
        lon_hi, lon_lo = _two_sum(np.full_like(turn, lon0_hi), turn)
        lon_hi, lon_lo = _two_sum(lon_hi, lon_lo + lon0_lo)
        lat0_hi, lat0_lo = _degrees_to_rad(self.latitude_of_origin_deg)
        lat_hi, lat_lo = _two_sum(np.full_like(north, lat0_hi), north / self.radius_m)
        lat = lat_hi + (lat_lo + lat0_lo)
        if np.any(np.abs(lat) > _HALF_PI_ABOVE):
            raise SourceLookupError("northings must give latitudes in [-pi/2, pi/2]")
        latitude = np.clip(lat, -_HALF_PI, _HALF_PI)
        return _float_array(latitude), _float_array(_wrap_longitude(lon_hi, lon_lo))

    def pixel_from_projected(
        self, easting_m: npt.ArrayLike, northing_m: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """Pixel coordinates (column, row) of (E, N): false easting and northing, geotransform."""
        return _pixel_from_projected(self, easting_m, northing_m)

    def projected_from_pixel(
        self, column: npt.ArrayLike, row: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """(E, N) in metres of pixel coordinates."""
        return _projected_from_pixel(self, column, row)

    def to_pixel(
        self, lat_rad: npt.ArrayLike, lon_rad: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """Pixel coordinates (column, row) of points in radians: project, then geotransform."""
        return self.pixel_from_projected(*self.project(lat_rad, lon_rad))

    def from_pixel(
        self, column: npt.ArrayLike, row: npt.ArrayLike
    ) -> tuple[FloatArray, FloatArray]:
        """(latitude, longitude) in radians of pixel coordinates."""
        return self.unproject(*self.projected_from_pixel(column, row))

    def _parallel_scale(self) -> float:
        lat_hi, lat_lo = _degrees_to_rad(self.standard_parallel_deg)
        return self.radius_m * (math.cos(lat_hi) - math.sin(lat_hi) * lat_lo)


SourceLookup = GeographicLookup | PolarStereographicLookup | EquirectangularLookup
"""The three closed-form source lookups of PRD 7."""


def _store_real(
    owner: object, name: str, *, minimum: float | None = None, maximum: float | None = None
) -> float:
    """Check that a parameter is a finite real number in range, and store it as a float."""
    value: object = getattr(owner, name)
    if value is None:
        raise SourceLookupError(f"{name} is required, got None")
    if isinstance(value, bool | np.bool_) or not isinstance(
        value, int | float | np.integer | np.floating
    ):
        raise SourceLookupError(f"{name} must be a real number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise SourceLookupError(f"{name} must be finite, got {number!r}")
    if (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
        raise SourceLookupError(f"{name} must lie in [{minimum}, {maximum}], got {number!r}")
    object.__setattr__(owner, name, number)
    return number


def _store_positive(owner: object, name: str) -> float:
    number = _store_real(owner, name)
    if number <= 0.0:
        raise SourceLookupError(f"{name} must be positive, got {number!r}")
    return number


def _store_nonzero(owner: object, name: str) -> float:
    number = _store_real(owner, name)
    if number == 0.0:
        raise SourceLookupError(f"{name} must not be zero")
    return number


def _store_projected_affine(owner: PolarStereographicLookup | EquirectangularLookup) -> None:
    for name in ("false_easting_m", "false_northing_m", "origin_x_m", "origin_y_m"):
        _store_real(owner, name)
    _store_nonzero(owner, "pixel_width_m")
    _store_nonzero(owner, "pixel_height_m")


def _points(lat_rad: npt.ArrayLike, lon_rad: npt.ArrayLike) -> tuple[FloatArray, FloatArray]:
    lat, lon = np.broadcast_arrays(
        np.asarray(lat_rad, dtype=np.float64), np.asarray(lon_rad, dtype=np.float64)
    )
    if not (np.all(np.isfinite(lat)) and np.all(np.isfinite(lon))):
        raise SourceLookupError("latitudes and longitudes must be finite")
    if np.any(np.abs(lat) > _HALF_PI):
        raise SourceLookupError("latitudes must lie in [-pi/2, pi/2]")
    if np.any(np.abs(lon) > _PI):
        raise SourceLookupError("longitudes must lie in [-pi, pi]")
    return lat, lon


def _projected(first: npt.ArrayLike, second: npt.ArrayLike) -> tuple[FloatArray, FloatArray]:
    a, b = np.broadcast_arrays(
        np.asarray(first, dtype=np.float64), np.asarray(second, dtype=np.float64)
    )
    if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b))):
        raise SourceLookupError("projected coordinates must be finite")
    return a, b


def _pixel_pair(column: npt.ArrayLike, row: npt.ArrayLike) -> tuple[FloatArray, FloatArray]:
    a, b = np.broadcast_arrays(
        np.asarray(column, dtype=np.float64), np.asarray(row, dtype=np.float64)
    )
    if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b))):
        raise SourceLookupError("pixel coordinates must be finite")
    return a, b


def _pixel_from_projected(
    lookup: PolarStereographicLookup | EquirectangularLookup,
    easting_m: npt.ArrayLike,
    northing_m: npt.ArrayLike,
) -> tuple[FloatArray, FloatArray]:
    """column = (E + FE - x0) / width - 0.5 and row = (N + FN - y0) / height - 0.5.

    FE - x0 is carried exactly as two float64 values, and so is its sum with E, so the only
    roundings are those of the final sum, the division and the half-pixel shift.
    """
    east, north = _projected(easting_m, northing_m)
    column = _affine_forward(east, lookup.false_easting_m, lookup.origin_x_m, lookup.pixel_width_m)
    row = _affine_forward(north, lookup.false_northing_m, lookup.origin_y_m, lookup.pixel_height_m)
    return column, row


def _projected_from_pixel(
    lookup: PolarStereographicLookup | EquirectangularLookup,
    column: npt.ArrayLike,
    row: npt.ArrayLike,
) -> tuple[FloatArray, FloatArray]:
    """E = (column + 0.5) width + x0 - FE and N = (row + 0.5) height + y0 - FN."""
    col, rw = _pixel_pair(column, row)
    shift_x_hi, shift_x_lo = _two_sum(
        np.asarray(lookup.origin_x_m), np.asarray(-lookup.false_easting_m)
    )
    shift_y_hi, shift_y_lo = _two_sum(
        np.asarray(lookup.origin_y_m), np.asarray(-lookup.false_northing_m)
    )
    east = _affine_inverse(col, lookup.pixel_width_m, float(shift_x_hi), float(shift_x_lo))
    north = _affine_inverse(rw, lookup.pixel_height_m, float(shift_y_hi), float(shift_y_lo))
    return east, north


def _affine_forward(
    coordinate: FloatArray, false_offset: float, origin: float, size: float
) -> FloatArray:
    shift_hi, shift_lo = _two_sum(np.asarray(false_offset), np.asarray(-origin))
    total_hi, total_lo = _two_sum(coordinate, np.full_like(coordinate, float(shift_hi)))
    total = total_hi + (total_lo + float(shift_lo))
    return _float_array(total / size - 0.5)


def _affine_inverse(
    index: FloatArray, size: float, offset_hi: float, offset_lo: float
) -> FloatArray:
    """(index + 0.5) * size + offset, with offset = offset_hi + offset_lo, in double-double."""
    q_hi, q_lo = _two_sum(index, np.full_like(index, 0.5))
    p_hi, p_lo = _two_prod(q_hi, np.full_like(q_hi, size))
    p_lo = p_lo + q_lo * size
    s_hi, s_lo = _two_sum(p_hi, np.full_like(p_hi, offset_hi))
    return _float_array(s_hi + (s_lo + (p_lo + offset_lo)))


def _deg_to_rad(deg_hi: FloatArray, deg_lo: FloatArray) -> tuple[FloatArray, FloatArray]:
    """(deg_hi + deg_lo) * pi / 180 as a normalised double-double value."""
    product, error = _two_prod(deg_hi, np.full_like(deg_hi, _DEG_TO_RAD_HI))
    error = error + (deg_hi * _DEG_TO_RAD_LO + deg_lo * _DEG_TO_RAD_HI)
    return _two_sum(product, error)


def _reduced_degrees(degrees: float) -> float:
    """An angle in degrees reduced to [-180, 180); exact for |degrees| <= 360 (Sterbenz)."""
    if degrees >= 180.0:
        return degrees - 360.0
    if degrees < -180.0:
        return degrees + 360.0
    return degrees


def _degrees_to_rad(degrees: float) -> tuple[float, float]:
    """A parameter in degrees as radians in double-double, first reduced to [-180, 180)."""
    hi, lo = _deg_to_rad(np.asarray([_reduced_degrees(degrees)]), np.zeros(1))
    return float(hi[0]), float(lo[0])


def _at_or_above(values: FloatArray, bound_hi: float, bound_lo: float) -> BoolArray:
    """values >= bound_hi + bound_lo, decided on the exact difference."""
    difference_hi, difference_lo = _two_sum(values, np.full_like(values, -bound_hi))
    above: BoolArray = difference_hi + (difference_lo - bound_lo) >= 0.0
    return above


def _tan_half(angle_hi: FloatArray, angle_lo: FloatArray) -> FloatArray:
    """tan(angle / 2) for angle = angle_hi + angle_lo in [0, pi/2], the low part to first order."""
    t = np.tan(angle_hi / 2.0)
    tangent: FloatArray = t + (angle_lo / 2.0) * (1.0 + t * t)
    return tangent


# ---------------------------------------------------------------------------------------------
# Bilinear interpolation (PRD 7, "Window resampler (D29)": interpolation)


class ResampleError(ValueError):
    """Raised for a source raster, pixel coordinates or nodata value the resampler cannot use."""


@dataclass(frozen=True, eq=False)
class ResampledGrid:
    """A resampled grid: float64 ``values`` with explicit boolean masks beside them.

    ``inside`` marks points that lie in the closed rectangle of source pixel centres. ``valid``
    marks points that are inside and whose four neighbouring source pixels all hold data. Where
    ``valid`` is False the value is NaN: missing data is never represented as 0. The arrays are
    read-only and share one shape, indexed like the pixel coordinates (``[row, column]`` for a
    window grid).
    """

    values: FloatArray
    valid: BoolArray
    inside: BoolArray

    def __post_init__(self) -> None:
        values = np.asarray(self.values)
        valid = np.asarray(self.valid)
        inside = np.asarray(self.inside)
        if values.dtype != np.float64:
            raise ResampleError(f"values must be float64, got {values.dtype}")
        if valid.dtype != np.bool_ or inside.dtype != np.bool_:
            raise ResampleError("valid and inside must be boolean arrays")
        if not values.shape == valid.shape == inside.shape:
            raise ResampleError("values, valid and inside must share one shape")
        if np.any(valid & ~inside):
            raise ResampleError("a valid point must lie inside the source")
        if not np.all(np.isfinite(values[valid])):
            raise ResampleError("valid values must be finite")
        if not np.all(np.isnan(values[~valid])):
            raise ResampleError("values that are not valid must be NaN")
        object.__setattr__(self, "values", _read_only(values))
        object.__setattr__(self, "valid", _read_only_mask(valid))
        object.__setattr__(self, "inside", _read_only_mask(inside))


def bilinear_resample(
    source: npt.ArrayLike,
    column: npt.ArrayLike,
    row: npt.ArrayLike,
    *,
    nodata: float | None,
) -> ResampledGrid:
    """Interpolate ``source`` bilinearly at fractional pixel coordinates (PRD 7).

    ``source`` is a 2-D raster of real numbers indexed ``[row, column]``, at least 2 x 2, or a
    numpy masked array of one (as rasterio's ``read(masked=True)`` returns). ``column`` and
    ``row`` are fractional indices of pixel centres ((0, 0) is the centre of the top-left pixel)
    and broadcast together. ``nodata`` is the product's declared sentinel, or None if it declares
    none; it must be given either way. A source pixel is missing if it is masked, NaN, infinite
    or equal to the sentinel, so a masked pixel, NaN and a declared sentinel behave identically,
    whatever value a mask hides. Zero is data.

    The sentinel is compared in the source's own type, as GDAL's nodata mask band converts it
    to the band's type first. For a float source it is taken as a float64 (GDAL reports nodata
    as a double) and rounded to the source type, so a float32 band stores and matches -9999.1
    as -9999.099609375. Equality is then exact, whereas GDAL's mask band also takes values
    within about 4.8e-7 relative of the sentinel (ARE_REAL_EQUAL); a masked array carries
    whatever mask its reader derived, and that mask is honoured as given. An integer source
    matches an integral sentinel in its range exactly, beyond 2**53 too. A sentinel the source
    type cannot hold, such as 0.5 or 256 for uint8, or a finite value beyond the float type's
    range, raises ResampleError rather than masking nothing. A NaN or infinite sentinel of a
    float source adds no missing pixels.

    A point is inside when 0 <= column <= ncols - 1 and 0 <= row <= nrows - 1. Its four
    neighbours are columns i and i + 1 and rows j and j + 1, with i = min(floor(column),
    ncols - 2) and j = min(floor(row), nrows - 2); on the last centre the weight of the far
    neighbour is 1. The output is nodata when the point is outside or any of the four is missing,
    whatever its weight. Outside is never zero-filled. Nothing else enters: no cubic kernel and
    no averaging (PRD 7). With p = column - i and q = row - j, valid points get, evaluated in
    this order for parity with ``@terraluna/core`` (PRD 8.8)::

        top = (1 - p) z[j, i] + p z[j, i + 1]
        bottom = (1 - p) z[j + 1, i] + p z[j + 1, i + 1]
        value = (1 - q) top + q bottom

    Bilinear interpolation reproduces a plane in pixel coordinates exactly in real arithmetic.
    It is meant for continuous fields such as elevation, not for class rasters.
    """
    masked: BoolArray | None = None
    if np.ma.isMaskedArray(source):
        # np.asarray would drop the mask and pass the data under it off as observations.
        masked = np.ma.getmaskarray(source)
        source = np.ma.getdata(source)
    grid = np.asarray(source)
    if grid.ndim != 2:
        raise ResampleError(f"source must be a 2-D raster, got {grid.ndim} dimensions")
    if grid.dtype.kind not in "iuf":
        raise ResampleError(f"source must hold integers or floats, got {grid.dtype}")
    rows, cols = grid.shape
    if rows < 2 or cols < 2:
        raise ResampleError(f"source must have at least 2 rows and 2 columns, got {grid.shape}")
    at_sentinel = _sentinel_pixels(grid, nodata)
    z = grid.astype(np.float64)
    good = np.isfinite(z)
    if at_sentinel is not None:
        good &= ~at_sentinel
    if masked is not None:
        good &= ~masked
    z = np.where(good, z, np.nan)
    try:
        u, v = np.broadcast_arrays(
            np.asarray(column, dtype=np.float64), np.asarray(row, dtype=np.float64)
        )
    except ValueError as error:
        raise ResampleError(f"column and row do not broadcast: {error}") from None
    if not (np.all(np.isfinite(u)) and np.all(np.isfinite(v))):
        raise ResampleError("pixel coordinates must be finite")
    inside = (u >= 0.0) & (u <= cols - 1) & (v >= 0.0) & (v <= rows - 1)
    u_in = np.where(inside, u, 0.0)
    v_in = np.where(inside, v, 0.0)
    i = np.minimum(np.floor(u_in), cols - 2).astype(np.intp)
    j = np.minimum(np.floor(v_in), rows - 2).astype(np.intp)
    p = u_in - i
    q = v_in - j
    neighbours = good[j, i] & good[j, i + 1] & good[j + 1, i] & good[j + 1, i + 1]
    valid = inside & neighbours
    top = (1.0 - p) * z[j, i] + p * z[j, i + 1]
    bottom = (1.0 - p) * z[j + 1, i] + p * z[j + 1, i + 1]
    value = (1.0 - q) * top + q * bottom
    return ResampledGrid(
        values=_float_array(np.where(valid, value, np.nan)),
        valid=np.asarray(valid, dtype=np.bool_),
        inside=np.asarray(inside, dtype=np.bool_),
    )


def resample_window(
    source: npt.ArrayLike,
    lookup: SourceLookup,
    *,
    grid: WindowGrid,
    body: str,
    lat0_rad: float,
    lon0_rad: float,
    nodata: float | None,
) -> ResampledGrid:
    """Resample a source raster onto a D29 window grid (PRD 7, "Window resampler (D29)").

    Each pixel centre of ``grid`` goes through the inverse spherical AEQD about (``lat0_rad``,
    ``lon0_rad``) on the D29 sphere of ``body`` (:func:`body_radius_m`), then into the source's
    pixel space by ``lookup``, and is interpolated by :func:`bilinear_resample` with the
    declared ``nodata``; if ``source`` is a masked array, its masked pixels are missing too. The
    result is indexed ``[row, column]`` like :meth:`WindowGrid.mesh_m`.
    """
    radius = body_radius_m(body)
    x, y = grid.mesh_m()
    lat, lon = aeqd_inverse(x, y, lat0_rad=lat0_rad, lon0_rad=lon0_rad, radius_m=radius)
    column, row = lookup.to_pixel(lat, lon)
    return bilinear_resample(source, column, row, nodata=nodata)


def _nodata_value(nodata: object) -> int | float | None:
    """The declared sentinel as a Python int (exact) or float, or None if none is declared."""
    if nodata is None:
        return None
    if isinstance(nodata, bool | np.bool_) or not isinstance(
        nodata, int | float | np.integer | np.floating
    ):
        raise ResampleError(f"nodata must be a real number or None, got {nodata!r}")
    if isinstance(nodata, int | np.integer):
        return int(nodata)
    return float(nodata)


def _sentinel_pixels(grid: npt.NDArray[Any], nodata: object) -> BoolArray | None:
    """Where ``grid`` holds the declared sentinel, compared in ``grid``'s own type."""
    sentinel = _nodata_value(nodata)
    if sentinel is None:
        return None
    dtype: np.dtype[Any] = grid.dtype
    if dtype.kind == "f":
        try:
            declared = float(sentinel)  # Only an int too large for a float64 overflows.
        except OverflowError:
            raise ResampleError(
                f"nodata {sentinel!r} lies beyond the range of the {dtype} source"
            ) from None
        if not math.isfinite(declared):
            return None
        # Every float64 fits a wider float type; float() of its maximum would overflow.
        if dtype.itemsize <= 8 and abs(declared) > float(np.finfo(dtype).max):
            raise ResampleError(f"nodata {sentinel!r} lies beyond the range of the {dtype} source")
        return np.asarray(grid == dtype.type(declared), dtype=np.bool_)
    if isinstance(sentinel, float):
        if not sentinel.is_integer():
            raise ResampleError(
                f"nodata {sentinel!r} is not an integer, so no {dtype} pixel holds it"
            )
        sentinel = int(sentinel)
    info = np.iinfo(dtype)
    if not int(info.min) <= sentinel <= int(info.max):
        raise ResampleError(f"nodata {sentinel!r} lies beyond the range of the {dtype} source")
    return np.asarray(grid == dtype.type(sentinel), dtype=np.bool_)


def _read_only_mask(values: BoolArray) -> BoolArray:
    frozen = np.array(values, dtype=np.bool_, copy=True)
    frozen.flags.writeable = False
    return frozen
