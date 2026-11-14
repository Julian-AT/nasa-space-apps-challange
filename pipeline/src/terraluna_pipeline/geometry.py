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
"""

_PI: Final = math.pi
_TWO_PI: Final = 2.0 * math.pi
_HALF_PI: Final = math.pi / 2.0


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

    Longitude: lam0 + atan2(N, M) lies in [-2 pi, 2 pi) and is brought into [-pi, pi) by one
    shift of 2 * math.pi, which is exact in float64 (Sterbenz). A longitude and the same value
    plus or minus 2 * math.pi name the same meridian, as for ``math.degrees``, which maps
    ``math.pi`` to exactly 180.

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
    lon = lon0 + np.arctan2(lon_num, lon_den)
    lon = np.where(lon >= _PI, lon - _TWO_PI, np.where(lon < -_PI, lon + _TWO_PI, lon))
    lon = np.where(at_centre, lon0, lon)
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

    ``lat_rad`` must lie in [-pi/2, pi/2] and ``lon_rad`` in [-pi, pi]; the centre as for
    :func:`aeqd_inverse`. When |d| > pi it is brought into [-pi, pi] as
    (lam - pi) - (lam0 + pi) or (lam + pi) - (lam0 - pi), whose parts are exact near the
    antimeridian. Returns float64 metres broadcast to the shape of the inputs.
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
    d = lon - lon0
    d = np.where(d > _PI, (lon - _PI) - (lon0 + _PI), d)
    d = np.where(d < -_PI, (lon + _PI) - (lon0 - _PI), d)
    sin_lat0 = math.sin(lat0)
    cos_lat0 = math.cos(lat0)
    cos_lat = np.cos(lat)
    dlat = lat - lat0
    hav = 2.0 * np.sin(d / 2.0) ** 2

    east = cos_lat * np.sin(d)
    north = np.sin(dlat) + sin_lat0 * cos_lat * hav
    up = np.cos(dlat) - cos_lat0 * cos_lat * hav
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


def _float_array(values: npt.ArrayLike) -> FloatArray:
    return np.asarray(values, dtype=np.float64)


def _read_only(values: FloatArray) -> FloatArray:
    frozen = np.array(values, dtype=np.float64, copy=True)
    frozen.flags.writeable = False
    return frozen
