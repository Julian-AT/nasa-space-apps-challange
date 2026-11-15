"""Encoder input windows (T11-01; PRD 7 "Window resampler (D29)", 11.2, 8.13 ``ml``).

:func:`sample_window` cuts one 64 x 64 window at 78.125 m per pixel, exactly 5,000 m on a side,
centred on (``lat0``, ``lon0``) of a :class:`terraluna_ml.synthetic.DemSource`. The window is
resampled by ``terraluna_pipeline.geometry.resample_window`` (the D29 resampler: inverse
spherical AEQD on the body's sphere, the source's T3 lookup and bilinear interpolation) and by
nothing else. :func:`encode_channels` then turns the window into the three encoder channels.
The browser probe mirrors these rules in ``@terraluna/core`` (t10 T10-07, CONTRACTS).

Rules (decisions recorded for a human where the PRD is silent)

- **Valid pixels.** A pixel starts valid where the resampler says so (inside the source and all
  four source neighbours hold data). A slope needs a valid neighbour along each axis, so a
  valid pixel without one is dropped, and the rule is applied again until it holds. Everything
  below uses the remaining valid pixels only; values at other pixels never enter.
- **Plane removal.** The least-squares plane ``z = a + b x + c y`` over the valid pixels, in
  the D29 window frame (x east, y north, metres from the centre), in float64 from centred sums.
  The residual is channel 0's elevation. No plane exists with no valid pixel; then every
  channel is 0.0.
- **Slope.** Slope magnitude is taken from the resampled elevations before plane removal, so
  the window's mean tilt stays visible to the encoder: the relief targets of PRD 11.2 include
  it, and channel 0 does not. Per axis the difference is central where both neighbours are
  valid and one-sided where only one is; slope = degrees(atan(hypot(dz/dx, dz/dy))).
- **Invalid fill.** Channels 0 and 1 hold :data:`INVALID_FILL` (0.0) wherever the valid channel
  is 0. This is a network-input fill, not an elevation or a slope; no NaN is stored.
- **Finite arithmetic.** Finite valid elevations so large that plane removal or the slope leaves
  float64 (near 1e308 m) are refused with :class:`DatasetError`, and ``storage()`` refuses any
  non-finite channel, so neither NaN nor an infinite slope reaches a shard.
- **Valid fraction.** ``valid_cells / 4096`` is recorded with every window and is not a filter
  here; the PRD 11.2 quality filter applies it later.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

from terraluna_ml.synthetic import DemSource
from terraluna_pipeline.geometry import (
    Body,
    ResampledGrid,
    WindowGrid,
    body_radius_m,
    resample_window,
    window_grid,
)

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]
Float16Array = npt.NDArray[np.float16]

# --- Constants (PRD 11.2 and the 8.13 ml block: ml.input_px, ml.input_m_per_px) -------------
WINDOW_PX: Final = 64
"""Pixels per side of an encoder window (``ml.input_px``)."""
PIXEL_M: Final = 78.125
"""Pixel spacing in metres (``ml.input_m_per_px``)."""
WINDOW_M: Final = 5000.0
"""The analysed window, WINDOW_PX * PIXEL_M = 5,000 m exactly. Nothing outside it is part of
the window. There is no context crop (decision PRD-08) and no edge margin: the resampler reads
the source raster directly. Any read padding a real reader adds around a window (T11-07) is
source-read padding only and is never counted as the window."""
ELEVATION_SCALE_M: Final = 50.0
"""Channel 0 is asinh(z / 50 m) of the plane-removed elevation."""
SLOPE_SCALE_DEG: Final = 45.0
"""Channel 1 is slope magnitude / 45 degrees."""
INVALID_FILL: Final = 0.0
"""Channels 0 and 1 where the valid channel is 0: a network-input fill, not an elevation."""
CHANNELS: Final = ("asinh_z_over_50m", "slope_over_45deg", "valid")
"""Channel names, in storage order."""
STORAGE_DTYPE: Final = np.float16
"""Shards store windows as float16 (PRD 11.2)."""
RESAMPLER: Final = "aeqd_sphere"
"""The D29 resampler (``terrain.resampler`` in config/run.yaml)."""

WINDOW_GRID: Final[WindowGrid] = window_grid(WINDOW_M, PIXEL_M)
"""The D29 pixel-centre grid of an encoder window."""

if WINDOW_GRID.size != WINDOW_PX or WINDOW_PX * PIXEL_M != WINDOW_M:
    raise RuntimeError("the encoder window constants disagree")


class DatasetError(ValueError):
    """Raised for a window centre or window values the encoder input cannot be built from."""


@dataclass(frozen=True, slots=True)
class PlaneCoefficients:
    """The removed plane ``z = intercept_m + slope_east * x + slope_north * y``.

    x and y are metres east and north of the window centre; slopes are metres per metre.
    """

    intercept_m: float
    slope_east: float
    slope_north: float


@dataclass(frozen=True, eq=False)
class EncodedChannels:
    """The three encoder channels of one window, in float64, and what they were built from.

    ``channels`` is ``(3, 64, 64)`` in :data:`CHANNELS` order. ``residual_m`` is the
    plane-removed elevation, NaN where ``valid`` is False. ``plane`` is None when no pixel is
    valid. All arrays are read-only.
    """

    channels: FloatArray
    residual_m: FloatArray
    valid: BoolArray
    plane: PlaneCoefficients | None


@dataclass(frozen=True, eq=False)
class EncoderWindow:
    """One encoder input window and its record (PRD 11.2).

    ``elevation_m`` holds the resampler's values (NaN where it found no data);
    ``resampled_valid_cells`` counts the pixels it found valid and ``valid_cells`` those left
    valid by the slope rule, so ``valid_fraction = valid_cells / 4096``. ``mock`` is the
    source's label and travels with the window.
    """

    body: Body
    body_radius_m: float
    lat0_deg: float
    lon0_deg: float
    mock: bool
    elevation_m: FloatArray
    channels: FloatArray
    residual_m: FloatArray
    valid: BoolArray
    plane: PlaneCoefficients | None
    resampled_valid_cells: int
    valid_cells: int
    valid_fraction: float

    def storage(self) -> Float16Array:
        """The window as stored in a shard: ``(3, 64, 64)`` float16, with no NaN.

        Raises :class:`DatasetError` if a channel is not finite after the cast, so no NaN or
        infinity is ever stored.
        """
        stored: Float16Array = self.channels.astype(STORAGE_DTYPE)
        if not np.all(np.isfinite(stored)):
            raise DatasetError("a stored channel value is not finite")
        return stored


def sample_window(source: DemSource, lat0: float, lon0: float) -> EncoderWindow:
    """Cut and encode the window centred on (``lat0``, ``lon0``) in degrees.

    The centre is converted to radians once (a longitude outside [-180, 180) is first reduced
    into it). Raises :class:`DatasetError` for a centre that is not a finite latitude in
    [-90, 90] and a finite longitude.
    """
    lat_deg, lon_deg = _centre_degrees(lat0, lon0)
    radius = body_radius_m(source.body)
    resampled: ResampledGrid = resample_window(
        source.raster,
        source.lookup,
        grid=WINDOW_GRID,
        body=source.body,
        lat0_rad=math.radians(lat_deg),
        lon0_rad=math.radians(lon_deg),
        nodata=source.nodata,
    )
    encoded = encode_channels(resampled.values, resampled.valid)
    valid_cells = int(np.count_nonzero(encoded.valid))
    return EncoderWindow(
        body=source.body,
        body_radius_m=radius,
        lat0_deg=float(lat0),
        lon0_deg=float(lon0),
        mock=bool(source.mock),
        elevation_m=resampled.values,
        channels=encoded.channels,
        residual_m=encoded.residual_m,
        valid=encoded.valid,
        plane=encoded.plane,
        resampled_valid_cells=int(np.count_nonzero(resampled.valid)),
        valid_cells=valid_cells,
        valid_fraction=valid_cells / encoded.valid.size,
    )


def encode_channels(values: npt.ArrayLike, valid: npt.ArrayLike) -> EncodedChannels:
    """Build the three encoder channels from window elevations and their valid mask.

    ``values`` are metres on :data:`WINDOW_GRID` (``[row, column]``, row 0 at the top) and
    ``valid`` marks the pixels holding data; a valid pixel must be finite, and whatever an
    invalid pixel holds never enters. The module docstring states the rules.
    """
    z = np.asarray(values, dtype=np.float64)
    mask = np.asarray(valid)
    shape = (WINDOW_PX, WINDOW_PX)
    if z.shape != shape or mask.shape != shape:
        raise DatasetError(f"values and valid must be {shape}, got {z.shape} and {mask.shape}")
    if mask.dtype != np.bool_:
        raise DatasetError(f"valid must be a boolean array, got {mask.dtype}")
    if not np.all(np.isfinite(z[mask])):
        raise DatasetError("a valid pixel must hold a finite elevation")
    filled = np.where(mask, z, 0.0)  # only selected values below; invalid ones never enter
    use = _slope_support(mask)

    channels = np.full((len(CHANNELS), *shape), INVALID_FILL, dtype=np.float64)
    residual = np.full(shape, np.nan, dtype=np.float64)
    with np.errstate(all="ignore"):  # an overflow is refused just below, never passed on
        plane = _plane_fit(filled, use)
        gradient = _gradient(filled, use)
    if plane is None:
        use = np.zeros(shape, dtype=np.bool_)
    else:
        coefficients, fitted = plane
        if not (
            all(math.isfinite(value) for value in dataclasses.astuple(coefficients))
            and np.all(np.isfinite(fitted[use]))
            and all(np.all(np.isfinite(part[use])) for part in gradient)
        ):
            raise DatasetError(
                "the valid elevations overflow float64 plane removal or slope arithmetic"
            )
        residual[use] = fitted[use]
        slope_deg = np.degrees(np.arctan(np.hypot(*gradient)))
        channels[0][use] = np.arcsinh(residual[use] / ELEVATION_SCALE_M)
        channels[1][use] = slope_deg[use] / SLOPE_SCALE_DEG
        channels[2][use] = 1.0
    return EncodedChannels(
        channels=_read_only(channels),
        residual_m=_read_only(residual),
        valid=_read_only(use),
        plane=None if plane is None else coefficients,
    )


def input_encoding_record() -> dict[str, object]:
    """The input encoding for the data manifest (T11-05), from the constants above."""
    return {
        "window_px": WINDOW_PX,
        "pixel_m": PIXEL_M,
        "window_m": WINDOW_M,
        "resampler": RESAMPLER,
        "channels": list(CHANNELS),
        "elevation_scale_m": ELEVATION_SCALE_M,
        "slope_scale_deg": SLOPE_SCALE_DEG,
        "invalid_fill": INVALID_FILL,
        "storage_dtype": np.dtype(STORAGE_DTYPE).name,
        "plane_removal": "least squares over valid pixels, window frame",
        "slope_source": "resampled elevation before plane removal",
        "valid_rule": "resampler valid, then a valid neighbour along each axis, to a fixed point",
    }


# --- Helpers ----------------------------------------------------------------------------------


def _centre_degrees(lat0: float, lon0: float) -> tuple[float, float]:
    for name, value in (("lat0", lat0), ("lon0", lon0)):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise DatasetError(f"{name} must be a real number of degrees, got {value!r}")
    lat, lon = float(lat0), float(lon0)
    if not (math.isfinite(lat) and -90.0 <= lat <= 90.0):
        raise DatasetError(f"lat0 must be a finite latitude in [-90, 90] degrees, got {lat!r}")
    if not math.isfinite(lon):
        raise DatasetError(f"lon0 must be a finite longitude in degrees, got {lon!r}")
    if not -180.0 <= lon < 180.0:
        lon = (lon + 180.0) % 360.0 - 180.0
    return lat, lon


def _neighbours(mask: BoolArray) -> tuple[BoolArray, BoolArray, BoolArray, BoolArray]:
    """Whether each pixel's east, west, north and south neighbour is in ``mask``."""
    east = np.zeros_like(mask)
    west = np.zeros_like(mask)
    north = np.zeros_like(mask)
    south = np.zeros_like(mask)
    east[:, :-1] = mask[:, 1:]
    west[:, 1:] = mask[:, :-1]
    north[1:, :] = mask[:-1, :]
    south[:-1, :] = mask[1:, :]
    return east, west, north, south


def _slope_support(mask: BoolArray) -> BoolArray:
    """Drop valid pixels without a valid neighbour along each axis, until none is left."""
    use = mask.copy()
    while True:
        east, west, north, south = _neighbours(use)
        kept = use & (east | west) & (north | south)
        if np.array_equal(kept, use):
            return use
        use = kept


def _shifted(z: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
    """The east, west, north and south neighbour values (0.0 beyond the edge, never used)."""
    east = np.zeros_like(z)
    west = np.zeros_like(z)
    north = np.zeros_like(z)
    south = np.zeros_like(z)
    east[:, :-1] = z[:, 1:]
    west[:, 1:] = z[:, :-1]
    north[1:, :] = z[:-1, :]
    south[:-1, :] = z[1:, :]
    return east, west, north, south


def _difference(
    z: FloatArray,
    ahead: FloatArray,
    behind: FloatArray,
    has_ahead: BoolArray,
    has_behind: BoolArray,
) -> FloatArray:
    """dz per metre toward ``ahead``: central where both neighbours are valid, else one-sided."""
    central = (ahead - behind) / (2.0 * PIXEL_M)
    forward = (ahead - z) / PIXEL_M
    backward = (z - behind) / PIXEL_M
    result: FloatArray = np.where(
        has_ahead & has_behind, central, np.where(has_ahead, forward, backward)
    )
    return result


def _gradient(z: FloatArray, use: BoolArray) -> tuple[FloatArray, FloatArray]:
    """(dz/dx, dz/dy) in metres per metre, x east and y north, from pixels in ``use`` only."""
    has_east, has_west, has_north, has_south = _neighbours(use)
    east, west, north, south = _shifted(z)
    dz_dx = _difference(z, east, west, has_east, has_west)
    dz_dy = _difference(z, north, south, has_north, has_south)
    return dz_dx, dz_dy


def _plane_fit(z: FloatArray, use: BoolArray) -> tuple[PlaneCoefficients, FloatArray] | None:
    """The least-squares plane over ``use`` and the residual grid, or None if undetermined.

    Centred sums: with means over the valid pixels and d = value - mean, the slopes solve
    [[Sxx, Sxy], [Sxy, Syy]] (b, c) = (Sxz, Syz) and the residual is dz - b dx - c dy.
    """
    if not np.any(use):
        return None
    x, y = WINDOW_GRID.mesh_m()
    xs, ys, zs = x[use], y[use], z[use]
    x_mean, y_mean, z_mean = float(np.mean(xs)), float(np.mean(ys)), float(np.mean(zs))
    dx, dy, dz = xs - x_mean, ys - y_mean, zs - z_mean
    sxx, syy, sxy = float(dx @ dx), float(dy @ dy), float(dx @ dy)
    sxz, syz = float(dx @ dz), float(dy @ dz)
    det = sxx * syy - sxy * sxy
    if not det > 0.0:
        return None
    slope_east = (sxz * syy - syz * sxy) / det
    slope_north = (syz * sxx - sxz * sxy) / det
    residual = np.full(z.shape, np.nan, dtype=np.float64)
    residual[use] = dz - slope_east * dx - slope_north * dy
    intercept = z_mean - slope_east * x_mean - slope_north * y_mean
    return PlaneCoefficients(intercept, slope_east, slope_north), residual


def _read_only[T: np.generic](values: npt.NDArray[T]) -> npt.NDArray[T]:
    frozen = np.array(values, copy=True)
    frozen.flags.writeable = False
    return frozen
