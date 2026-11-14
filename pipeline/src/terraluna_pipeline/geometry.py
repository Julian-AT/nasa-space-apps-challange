"""Body spheres for the D29 window resampler (PRD 7, "Window resampler (D29)").

Terrain windows are resampled on a sphere per body, in float64. The radii below are the only
radii the resampler uses; ``@terraluna/core`` mirrors them for the browser probe (PRD 8.8).
Terrain distances derived from them are metres on the body sphere and are kept apart from radar
and model distances (PRD 6.5, 8.11).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

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
