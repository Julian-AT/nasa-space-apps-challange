"""512 px WebP hillshade tile pyramids for the web maps (PRD 8.4, 8.10, D28; T5-06).

Addressing (an engineering choice, recorded in every TileLayer):

- Moon and Mars layers use the projected metres of the frame the search windows are drawn in
  (Site01 polar stereographic metres; the local AEQD frame of each Mars target). The origin is
  the top-left corner ``(origin_x_m, origin_y_m)`` of the layer extent, ``y_direction`` is
  ``down`` (tile row y grows southward), every tile is 512 x 512 pixels, and zoom ``z`` has
  ``metres_per_pixel_by_zoom[z - zoom_min]``, halving per zoom. Tile (z, x, y) covers
  ``[origin_x + x T, origin_x + (x + 1) T] x [origin_y - (y + 1) T, origin_y - y T]`` with
  ``T = 512 * mpp(z)``. At ``zoom_max`` the pixel is never finer than the source product.
- Earth layers use Web Mercator (EPSG:3857) XYZ tiles at 512 px for MapLibre (D28): origin
  (-20037508.342789244, 20037508.342789244), y down, ``mpp(z) = 2 pi 6378137 / (512 2^z)``.

Hillshade: the normalized Lambertian value ``cos(zen) cos(slope) + sin(zen) sin(slope)
cos(az - aspect)`` clipped at 0, with the sun azimuth (clockwise from grid north) and altitude
as explicit parameters recorded in the layer; a flat plane gives ``sin(altitude)``. Gradients use
ground metres. Nodata cells are fully transparent, never flat terrain. Tiles that are entirely
nodata are omitted and listed, not written. Downsampled zooms average the finer valid cells
(display only; never an analysis input).
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import numpy as np
import numpy.typing as npt

TILE_PX: Final = 512
BUDGET_BYTES: Final = 300_000_000
MERCATOR_HALF: Final = 20037508.342789244
WEBP_QUALITY: Final = 85

FloatArray = npt.NDArray[np.float64]


class TileError(RuntimeError):
    """A machine-readable failure: ``code`` is a reason code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class TileScheme:
    origin_x_m: float
    origin_y_m: float
    zoom_min: int
    zoom_max: int
    mpp_by_zoom: tuple[float, ...]

    def mpp(self, z: int) -> float:
        return self.mpp_by_zoom[z - self.zoom_min]

    def tile_m(self, z: int) -> float:
        return TILE_PX * self.mpp(z)


def projected_scheme(extent_m: Sequence[float], native_mpp: float) -> TileScheme:
    """A scheme for a projected extent: zoom 0 holds the extent in one tile, zoom_max has a
    pixel no finer than ``native_mpp``."""
    x0, y0, x1, y1 = extent_m
    span = max(x1 - x0, y1 - y0)
    zmax = max(0, math.floor(math.log2(span / (TILE_PX * native_mpp))) + 1)
    mpp0 = span / TILE_PX
    mpp = tuple(mpp0 / 2**z for z in range(zmax + 1))
    while len(mpp) > 1 and mpp[-1] < native_mpp:
        mpp = mpp[:-1]
    return TileScheme(x0, y1, 0, len(mpp) - 1, mpp)


def mercator_scheme(zoom_min: int, zoom_max: int) -> TileScheme:
    mpp = tuple(2 * MERCATOR_HALF / (TILE_PX * 2**z) for z in range(zoom_min, zoom_max + 1))
    return TileScheme(-MERCATOR_HALF, MERCATOR_HALF, zoom_min, zoom_max, mpp)


def tile_extent(scheme: TileScheme, z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y) of tile (z, x, y) in the layer's metres."""
    t = scheme.tile_m(z)
    return (
        scheme.origin_x_m + x * t,
        scheme.origin_y_m - (y + 1) * t,
        scheme.origin_x_m + (x + 1) * t,
        scheme.origin_y_m - y * t,
    )


def tile_index_for_point(scheme: TileScheme, z: int, px: float, py: float) -> tuple[int, int]:
    t = scheme.tile_m(z)
    return math.floor((px - scheme.origin_x_m) / t), math.floor((scheme.origin_y_m - py) / t)


def hillshade(
    dem: FloatArray,
    dx_m: float | FloatArray,
    dy_m: float,
    *,
    azimuth_deg: float,
    altitude_deg: float,
) -> FloatArray:
    """Hillshade in [0, 1] of a north-up grid (row 0 north); NaN where the DEM or a neighbour
    is NaN. ``dx_m`` may vary by row (Web Mercator)."""
    z = np.asarray(dem, dtype=np.float64)
    dzdx = np.full_like(z, np.nan)
    dzdy = np.full_like(z, np.nan)
    dx = np.broadcast_to(np.asarray(dx_m, dtype=np.float64).reshape(-1, 1), z.shape)
    dzdx[:, 1:-1] = (z[:, 2:] - z[:, :-2]) / (2 * dx[:, 1:-1])
    dzdy[1:-1, :] = (z[:-2, :] - z[2:, :]) / (2 * dy_m)  # north minus south
    slope = np.arctan(np.hypot(dzdx, dzdy))
    # Aspect: direction the surface faces (downslope), clockwise from north.
    aspect = np.arctan2(-dzdx, -dzdy)
    zen = math.radians(90.0 - altitude_deg)
    az = math.radians(azimuth_deg)
    hs = math.cos(zen) * np.cos(slope) + math.sin(zen) * np.sin(slope) * np.cos(az - aspect)
    return np.where(np.isfinite(hs) & np.isfinite(z), np.clip(hs, 0.0, 1.0), np.nan)


def block_mean(a: FloatArray, f: int) -> FloatArray:
    """Mean of valid cells in f x f blocks (NaN where a block has none)."""
    if f == 1:
        return a
    h, w = a.shape
    hh, ww = math.ceil(h / f) * f, math.ceil(w / f) * f
    p = np.full((hh, ww), np.nan)
    p[:h, :w] = a
    b = p.reshape(hh // f, f, ww // f, f)
    cnt = np.isfinite(b).sum(axis=(1, 3))
    s = np.nansum(b, axis=(1, 3))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cnt > 0, s / np.maximum(cnt, 1), np.nan)


def encode_webp(shade: FloatArray) -> bytes:
    """Grey hillshade with alpha: alpha 0 at NaN (missing is transparent, never a value)."""
    from PIL import Image

    if shade.shape != (TILE_PX, TILE_PX):
        raise TileError("tile_shape", f"{shade.shape}")
    ok = np.isfinite(shade)
    grey = np.where(ok, np.round(shade * 255.0), 0).astype(np.uint8)
    rgba = np.dstack([grey, grey, grey, np.where(ok, 255, 0).astype(np.uint8)])
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "WEBP", quality=WEBP_QUALITY, method=4, exact=False)
    return buf.getvalue()


@dataclass
class Pyramid:
    layer: str
    written: list[tuple[int, int, int, int]] = field(default_factory=list)  # z, x, y, bytes
    omitted: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def bytes_total(self) -> int:
        return sum(b for *_, b in self.written)


class Budget:
    def __init__(self, limit: int = BUDGET_BYTES) -> None:
        self.limit = limit
        self.used = 0

    def take(self, n: int, what: str) -> None:
        if self.used + n > self.limit:
            raise TileError("tile_budget_exceeded", f"{what}: {self.used + n} > {self.limit} bytes")
        self.used += n


def write_level(
    out_root: Path,
    pyramid: Pyramid,
    z: int,
    shade: FloatArray,
    x0: int,
    y0: int,
    budget: Budget,
    *,
    mock: bool,
) -> None:
    """Cut a level raster whose top-left pixel is the corner of tile (x0, y0) into tiles."""
    if mock and "release" in out_root.parts:
        raise TileError("mock_tiles_in_release", str(out_root))
    h, w = shade.shape
    for ty in range(math.ceil(h / TILE_PX)):
        for tx in range(math.ceil(w / TILE_PX)):
            t = np.full((TILE_PX, TILE_PX), np.nan)
            part = shade[ty * TILE_PX : (ty + 1) * TILE_PX, tx * TILE_PX : (tx + 1) * TILE_PX]
            t[: part.shape[0], : part.shape[1]] = part
            x, y = x0 + tx, y0 + ty
            if not np.isfinite(t).any():
                pyramid.omitted.append((z, x, y))
                continue
            data = encode_webp(t)
            budget.take(len(data), f"{pyramid.layer}/{z}/{x}/{y}")
            p = out_root / pyramid.layer / str(z) / str(x) / f"{y}.webp"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            pyramid.written.append((z, x, y, len(data)))


def projected_pyramid(
    out_root: Path,
    layer: str,
    sample: Callable[[FloatArray, FloatArray], FloatArray],
    extent_m: Sequence[float],
    native_mpp: float,
    budget: Budget,
    *,
    azimuth_deg: float,
    altitude_deg: float,
    mock: bool = False,
) -> tuple[TileScheme, Pyramid]:
    """Render a projected-metres pyramid: ``sample(x, y)`` returns elevations (NaN = nodata) at
    pixel centres of the finest level; coarser levels average it."""
    scheme = projected_scheme(extent_m, native_mpp)
    mpp = scheme.mpp(scheme.zoom_max)
    x0, y0, x1, y1 = extent_m
    nx = math.ceil((x1 - x0) / mpp)
    ny = math.ceil((y1 - y0) / mpp)
    xs = scheme.origin_x_m + (np.arange(nx) + 0.5) * mpp
    ys = scheme.origin_y_m - (np.arange(ny) + 0.5) * mpp
    gx, gy = np.meshgrid(xs, ys)
    fine = sample(gx, gy)
    pyramid = Pyramid(layer)
    for z in range(scheme.zoom_max, scheme.zoom_min - 1, -1):
        f = 2 ** (scheme.zoom_max - z)
        dem = block_mean(fine, f)
        shade = hillshade(
            dem, scheme.mpp(z), scheme.mpp(z), azimuth_deg=azimuth_deg, altitude_deg=altitude_deg
        )
        write_level(out_root, pyramid, z, shade, 0, 0, budget, mock=mock)
    return scheme, pyramid


def mercator_pyramid(
    out_root: Path,
    layer: str,
    sample_lonlat: Callable[[FloatArray, FloatArray], FloatArray],
    bbox_lonlat: Sequence[float],
    zoom_min: int,
    zoom_max: int,
    budget: Budget,
    *,
    azimuth_deg: float,
    altitude_deg: float,
    mock: bool = False,
) -> tuple[TileScheme, Pyramid, tuple[float, float, float, float]]:
    scheme = mercator_scheme(zoom_min, zoom_max)
    lo0, la0, lo1, la1 = bbox_lonlat

    def merc(lon: float, lat: float) -> tuple[float, float]:
        x = math.radians(lon) * 6378137.0
        y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * 6378137.0
        return x, y

    mx0, my0 = merc(lo0, la0)
    mx1, my1 = merc(lo1, la1)
    pyramid = Pyramid(layer)
    for z in range(zoom_min, zoom_max + 1):
        tx0, ty0 = tile_index_for_point(scheme, z, mx0, my1)
        tx1, ty1 = tile_index_for_point(scheme, z, mx1, my0)
        mpp = scheme.mpp(z)
        nx = (tx1 - tx0 + 1) * TILE_PX
        ny = (ty1 - ty0 + 1) * TILE_PX
        ox, oy = tile_extent(scheme, z, tx0, ty0)[0], tile_extent(scheme, z, tx0, ty0)[3]
        xs = ox + (np.arange(nx) + 0.5) * mpp
        ys = oy - (np.arange(ny) + 0.5) * mpp
        lon = np.degrees(xs / 6378137.0)
        lat = np.degrees(2 * np.arctan(np.exp(ys / 6378137.0)) - math.pi / 2)
        glon, glat = np.meshgrid(lon, lat)
        inside = (glon >= lo0) & (glon <= lo1) & (glat >= la0) & (glat <= la1)
        dem = np.where(inside, sample_lonlat(glon, glat), np.nan)
        ground = mpp * np.cos(np.radians(lat))
        shade = hillshade(
            dem, ground, float(np.mean(ground)), azimuth_deg=azimuth_deg, altitude_deg=altitude_deg
        )
        write_level(out_root, pyramid, z, shade, tx0, ty0, budget, mock=mock)
    return scheme, pyramid, (mx0, my0, mx1, my1)


def tile_layer(
    *,
    layer: str,
    body: Literal["moon", "mars", "earth"],
    crs: str,
    units: str,
    source_product_ids: list[str],
    scheme: TileScheme,
    extent_m: Sequence[float],
    pyramid: Pyramid,
    azimuth_deg: float,
    altitude_deg: float,
    mock: bool = False,
) -> dict[str, Any]:
    """The t0b TileLayer record for a written pyramid."""
    return {
        "layer": layer,
        "body": body,
        "crs": crs,
        "units": units,
        "evidence_label": "Derived",
        "source_product_ids": sorted(source_product_ids),
        "origin_x_m": scheme.origin_x_m,
        "origin_y_m": scheme.origin_y_m,
        "y_direction": "down",
        "tile_px": TILE_PX,
        "metres_per_pixel_by_zoom": list(scheme.mpp_by_zoom),
        "zoom_min": scheme.zoom_min,
        "zoom_max": scheme.zoom_max,
        "extent_m": [float(v) for v in extent_m],
        "omitted_tiles": [list(t) for t in sorted(pyramid.omitted)],
        "legend": {"kind": "numeric", "min": 0.0, "max": 1.0, "scale": "cividis", "unit": None},
        "hillshade": {"azimuth_deg": azimuth_deg, "altitude_deg": altitude_deg},
        "encoding": {"format": "webp", "lossless": False, "quality": WEBP_QUALITY},
        "bytes_total": pyramid.bytes_total,
        "mock": mock,
    }
