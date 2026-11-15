"""Synthetic DEM sources for the encoder dataset: mock data only (T11-01, PRD 11.2, 12 T11).

Every DEM built here is a labelled **mock** (``mock=True``, ``label="mock"``). It exists to test
window sampling before real DEMs are read (T11-07) and never reaches a production path. Each
builder is deterministic from an explicit non-negative integer ``seed`` (numpy's PCG64 through
``numpy.random.default_rng``) and records its parameters.

:class:`DemSource` is the one interface a real reader implements later. It supplies the source
raster and its metadata only: the body, a float64 raster indexed ``[row, column]``, the
declared nodata value and the T3 lookup (``terraluna_pipeline.geometry``), built with explicit
georeferencing parameters. A source never evaluates elevation at a latitude and longitude:
every window is cut by the D29 resampler in :func:`terraluna_ml.dataset.sample_window`.

The builders:

- :func:`plane_dem`: ``z = a + b x + c y`` in the window frame about the DEM centre, where
  ``(x, y)`` are the forward AEQD offsets of each source pixel centre (``geometry.aeqd_forward``)
  on an equirectangular source. ``a``, ``b`` and ``c`` are drawn from the seed and recorded.
- :func:`fbm_dem`: a fractional-Brownian surface with Hurst exponent ``hurst`` by spectral
  synthesis on an equirectangular source: Gaussian Fourier coefficients with amplitude
  ``|k|^-(hurst + 1)``, the mean removed and the surface scaled to the requested RMS.
- :func:`hole_dem`: an fBm surface whose pixel centres within a disc hold the nodata sentinel.
- :func:`polar_fourfold_dem`: a polar stereographic source centred on the pole, with the pole on
  the centre node of an odd lattice, holding a smooth fBm surface made exactly 4-fold symmetric
  about the pole (and not mirror symmetric).

Equirectangular sources are centred on (``center_lat_deg``, ``center_lon_deg``), with true scale
at the centre latitude and that latitude as the latitude of origin, so projected metres are
measured from the centre. Every lookup uses the body's D29 sphere (``geometry.body_radius_m``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, Protocol

import numpy as np
import numpy.typing as npt
from terraluna_pipeline.geometry import (
    Body,
    EquirectangularLookup,
    PolarStereographicLookup,
    Pole,
    SourceLookup,
    aeqd_forward,
    body_radius_m,
)

FloatArray = npt.NDArray[np.float64]

MOCK_LABEL: Final = "mock"
"""The label every synthetic DEM carries (PRD 3: mocks are labelled and never released)."""

NODATA_SENTINEL: Final = -9999.0
"""The nodata value every synthetic DEM declares; :func:`hole_dem` writes it into its hole."""

SyntheticName = Literal["plane", "fbm", "hole", "polar_fourfold"]

PLANE_INTERCEPT_RANGE_M: Final = 100.0
"""The plane intercept ``a`` is drawn uniformly from [-100 m, 100 m)."""

PLANE_TILT_RANGE: Final = 0.2
"""The plane tilts ``b`` and ``c`` (metres per metre) are drawn uniformly from [-0.2, 0.2)."""


class SyntheticDemError(ValueError):
    """Raised for a seed, size or parameter a synthetic DEM cannot be built from."""


class DemSource(Protocol):
    """A DEM source raster and its metadata: the only interface window sampling reads.

    A real reader (T11-07) implements these five members and nothing else. ``raster`` is a 2-D
    float64 array indexed ``[row, column]``; ``nodata`` is the declared sentinel or None;
    ``lookup`` maps (latitude, longitude) to the raster's pixel space; ``mock`` is True for
    synthetic or other mock data, which never reaches a production path.
    """

    @property
    def body(self) -> Body: ...

    @property
    def raster(self) -> FloatArray: ...

    @property
    def nodata(self) -> float | None: ...

    @property
    def lookup(self) -> SourceLookup: ...

    @property
    def mock(self) -> bool: ...


@dataclass(frozen=True, eq=False, kw_only=True)
class SyntheticDem:
    """A labelled mock DEM: a :class:`DemSource` with its builder name, seed and parameters."""

    name: SyntheticName
    body: Body
    raster: FloatArray
    nodata: float | None
    lookup: SourceLookup
    seed: int
    parameters: Mapping[str, float]
    mock: Literal[True] = True
    label: Literal["mock"] = MOCK_LABEL

    def __post_init__(self) -> None:
        if self.mock is not True or self.label != MOCK_LABEL:
            raise SyntheticDemError("a synthetic DEM is always labelled mock")
        raster = np.array(self.raster, dtype=np.float64, copy=True)
        if raster.ndim != 2:
            raise SyntheticDemError(f"raster must be 2-D, got {raster.ndim} dimensions")
        raster.flags.writeable = False
        object.__setattr__(self, "raster", raster)
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


def plane_dem(
    *,
    seed: int,
    body: Body,
    center_lat_deg: float,
    center_lon_deg: float,
    half_width_m: float,
    pixel_m: float,
) -> SyntheticDem:
    """A plane in the window frame about the centre, on an equirectangular source.

    ``a`` (m), ``b`` (dz/dx east) and ``c`` (dz/dy north) are drawn from ``seed`` and recorded
    in ``parameters`` as ``a_m``, ``b`` and ``c``.
    """
    rng = _rng(seed)
    radius = body_radius_m(body)
    lookup, size = _equirectangular(radius, center_lat_deg, center_lon_deg, half_width_m, pixel_m)
    a = float(rng.uniform(-PLANE_INTERCEPT_RANGE_M, PLANE_INTERCEPT_RANGE_M))
    b, c = (float(value) for value in rng.uniform(-PLANE_TILT_RANGE, PLANE_TILT_RANGE, size=2))
    columns, rows = _pixel_mesh(size)
    lat, lon = lookup.from_pixel(columns, rows)
    x, y = aeqd_forward(
        lat,
        lon,
        lat0_rad=math.radians(center_lat_deg),
        lon0_rad=math.radians(center_lon_deg),
        radius_m=radius,
    )
    return SyntheticDem(
        name="plane",
        body=body,
        raster=a + b * x + c * y,
        nodata=NODATA_SENTINEL,
        lookup=lookup,
        seed=seed,
        parameters=_parameters(
            center_lat_deg, center_lon_deg, half_width_m, pixel_m, a_m=a, b=b, c=c
        ),
    )


def fbm_dem(
    *,
    seed: int,
    body: Body,
    hurst: float,
    rms_m: float,
    center_lat_deg: float,
    center_lon_deg: float,
    half_width_m: float,
    pixel_m: float,
) -> SyntheticDem:
    """A fractional-Brownian surface with Hurst exponent ``hurst`` and RMS ``rms_m``."""
    rng = _rng(seed)
    radius = body_radius_m(body)
    lookup, size = _equirectangular(radius, center_lat_deg, center_lon_deg, half_width_m, pixel_m)
    surface = _fbm(rng, size, hurst, rms_m)
    return SyntheticDem(
        name="fbm",
        body=body,
        raster=surface,
        nodata=NODATA_SENTINEL,
        lookup=lookup,
        seed=seed,
        parameters=_parameters(
            center_lat_deg, center_lon_deg, half_width_m, pixel_m, hurst=hurst, rms_m=rms_m
        ),
    )


def hole_dem(
    *,
    seed: int,
    body: Body,
    hurst: float,
    rms_m: float,
    hole_radius_m: float,
    hole_offset_m: tuple[float, float],
    center_lat_deg: float,
    center_lon_deg: float,
    half_width_m: float,
    pixel_m: float,
) -> SyntheticDem:
    """An fBm surface with a nodata disc of ``hole_radius_m`` about ``hole_offset_m``.

    ``hole_offset_m`` is (east, north) in projected metres from the centre. Pixel centres at
    most ``hole_radius_m`` from it hold :data:`NODATA_SENTINEL`.
    """
    rng = _rng(seed)
    radius = body_radius_m(body)
    lookup, size = _equirectangular(radius, center_lat_deg, center_lon_deg, half_width_m, pixel_m)
    hole = _positive("hole_radius_m", hole_radius_m)
    east0, north0 = (_finite("hole_offset_m", value) for value in hole_offset_m)
    surface = _fbm(rng, size, hurst, rms_m)
    east, north = lookup.projected_from_pixel(*_pixel_mesh(size))
    inside = np.hypot(east - east0, north - north0) <= hole
    return SyntheticDem(
        name="hole",
        body=body,
        raster=np.where(inside, NODATA_SENTINEL, surface),
        nodata=NODATA_SENTINEL,
        lookup=lookup,
        seed=seed,
        parameters=_parameters(
            center_lat_deg,
            center_lon_deg,
            half_width_m,
            pixel_m,
            hurst=hurst,
            rms_m=rms_m,
            hole_radius_m=hole,
            hole_east_m=east0,
            hole_north_m=north0,
        ),
    )


def polar_fourfold_dem(
    *,
    seed: int,
    body: Body,
    pole: Pole,
    hurst: float,
    rms_m: float,
    half_size_px: int,
    pixel_m: float,
) -> SyntheticDem:
    """A 4-fold symmetric surface on a polar stereographic source centred on ``pole``.

    The lattice is ``2 half_size_px + 1`` pixels square; its centre node is the pole (projected
    (0, 0)), true scale is at the pole (scale factor 1) and the central meridian is 0. With
    offsets u = column - half and v = half - row, the surface takes one value per orbit of
    the quarter turn (u, v) -> (-v, u): the sum, in one fixed order, of a smooth fBm surface
    over the orbit's four nodes. So it is exactly invariant under ``numpy.rot90`` and, for a
    generic seed, not under a reflection.
    """
    rng = _rng(seed)
    radius = body_radius_m(body)
    if isinstance(half_size_px, bool) or not isinstance(half_size_px, int) or half_size_px < 1:
        raise SyntheticDemError(f"half_size_px must be a positive integer, got {half_size_px!r}")
    spacing = _positive("pixel_m", pixel_m)
    size = 2 * half_size_px + 1
    edge = (half_size_px + 0.5) * spacing
    lookup = PolarStereographicLookup(
        pole=pole,
        central_meridian_deg=0.0,
        latitude_of_true_scale_deg=90.0 if pole == "north" else -90.0,
        scale_factor=1.0,
        radius_m=radius,
        false_easting_m=0.0,
        false_northing_m=0.0,
        origin_x_m=-edge,
        origin_y_m=edge,
        pixel_width_m=spacing,
        pixel_height_m=-spacing,
    )
    base = _fbm(rng, size, hurst, rms_m)
    symmetric = _orbit_sum(base, half_size_px)
    symmetric = symmetric - float(np.mean(symmetric))
    symmetric = symmetric * (rms_m / float(np.std(symmetric)))
    return SyntheticDem(
        name="polar_fourfold",
        body=body,
        raster=symmetric,
        nodata=NODATA_SENTINEL,
        lookup=lookup,
        seed=seed,
        parameters={
            "half_size_px": float(half_size_px),
            "pixel_m": spacing,
            "hurst": hurst,
            "rms_m": rms_m,
        },
    )


# --- Helpers ----------------------------------------------------------------------------------


def _rng(seed: object) -> np.random.Generator:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise SyntheticDemError(f"seed must be an explicit non-negative integer, got {seed!r}")
    return np.random.default_rng(seed)


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SyntheticDemError(f"{name} must be a real number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise SyntheticDemError(f"{name} must be finite, got {number!r}")
    return number


def _positive(name: str, value: object) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise SyntheticDemError(f"{name} must be positive, got {number!r}")
    return number


def _equirectangular(
    radius: float, center_lat_deg: float, center_lon_deg: float, half_width_m: float, pixel_m: float
) -> tuple[EquirectangularLookup, int]:
    """An equirectangular lookup centred on the given point and its pixel count per side."""
    half = _positive("half_width_m", half_width_m)
    spacing = _positive("pixel_m", pixel_m)
    count = 2.0 * half / spacing
    if not count.is_integer() or count < 2.0:
        raise SyntheticDemError(
            f"2 * half_width_m / pixel_m must be a whole number of at least 2, got {count!r}"
        )
    lat = _finite("center_lat_deg", center_lat_deg)
    lookup = EquirectangularLookup(
        center_longitude_deg=_finite("center_lon_deg", center_lon_deg),
        standard_parallel_deg=lat,
        latitude_of_origin_deg=lat,
        radius_m=radius,
        false_easting_m=0.0,
        false_northing_m=0.0,
        origin_x_m=-half,
        origin_y_m=half,
        pixel_width_m=spacing,
        pixel_height_m=-spacing,
    )
    return lookup, int(count)


def _pixel_mesh(size: int) -> tuple[FloatArray, FloatArray]:
    """(column, row) pixel-centre indices of a square raster, indexed ``[row, column]``."""
    index = np.arange(size, dtype=np.float64)
    columns, rows = np.meshgrid(index, index, indexing="xy")
    return columns, rows


def _fbm(rng: np.random.Generator, size: int, hurst: float, rms_m: float) -> FloatArray:
    """A zero-mean fBm surface of RMS ``rms_m`` on a ``size`` square lattice."""
    h = _finite("hurst", hurst)
    if not 0.0 < h < 1.0:
        raise SyntheticDemError(f"hurst must lie in (0, 1), got {h!r}")
    rms = _positive("rms_m", rms_m)
    ky = np.fft.fftfreq(size)[:, np.newaxis]
    kx = np.fft.rfftfreq(size)[np.newaxis, :]
    k = np.hypot(kx, ky)
    k[0, 0] = 1.0
    amplitude = k ** -(h + 1.0)
    amplitude[0, 0] = 0.0
    shape = amplitude.shape
    coefficients = (rng.standard_normal(shape) + 1j * rng.standard_normal(shape)) * amplitude
    surface = np.fft.irfft2(coefficients, s=(size, size))
    surface = surface - float(np.mean(surface))
    scaled: FloatArray = surface * (rms / float(np.std(surface)))
    return scaled


def _orbit_sum(base: FloatArray, half: int) -> FloatArray:
    """Sum ``base`` over each orbit of the quarter turn about the centre node, in one order.

    Each node is first turned into the fundamental domain u > 0, v >= 0 (or the centre); the
    four nodes of its orbit are then visited from there by quarter turns, so every node of an
    orbit adds the same four values in the same order and gets the same float64 sum.
    """
    size = base.shape[0]
    index = np.arange(size)
    columns, rows = np.meshgrid(index, index, indexing="xy")
    u = columns - half
    v = half - rows
    canon_u = np.zeros_like(u)
    canon_v = np.zeros_like(v)
    placed = (u == 0) & (v == 0)
    turn_u, turn_v = u, v
    for _ in range(4):
        fits = ~placed & (turn_u > 0) & (turn_v >= 0)
        canon_u = np.where(fits, turn_u, canon_u)
        canon_v = np.where(fits, turn_v, canon_v)
        placed |= fits
        turn_u, turn_v = -turn_v, turn_u
    total = np.zeros(base.shape, dtype=np.float64)
    member_u, member_v = canon_u, canon_v
    for _ in range(4):
        total = total + base[half - member_v, member_u + half]
        member_u, member_v = -member_v, member_u
    return total


def _parameters(
    center_lat_deg: float,
    center_lon_deg: float,
    half_width_m: float,
    pixel_m: float,
    **extra: float,
) -> dict[str, float]:
    return {
        "center_lat_deg": float(center_lat_deg),
        "center_lon_deg": float(center_lon_deg),
        "half_width_m": float(half_width_m),
        "pixel_m": float(pixel_m),
        **{key: float(value) for key, value in extra.items()},
    }
