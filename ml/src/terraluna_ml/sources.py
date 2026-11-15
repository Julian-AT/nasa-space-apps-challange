"""Real DEM sources for the encoder dataset (T11-07): GeoTIFF rasters as :class:`DemSource`.

Georeferencing, nodata and the sphere radius come from each file (rasterio's reading of its
CRS and geotransform, through ``terraluna_pipeline.io.read_geotiff_window``), never from
literals here. The T3 lookup forms are used unchanged:

- Copernicus GLO-30 (EPSG:4326 degrees): :class:`GeographicLookup`;
- CTX DTMs (``eqc`` on the Mars sphere): :class:`EquirectangularLookup`;
- LOLA 80S 20 m/px and PGDA78 Site01 (``stere`` on the Moon sphere):
  :class:`PolarStereographicLookup`.

``crop`` returns a sub-raster covering a set of (lat, lon) points plus a margin, with the
lookup's origin shifted by the crop offset. This is source-read padding only: bilinear
interpolation reads four neighbours, so the resampled values are identical to resampling the
whole raster (a test checks it), while the D29 resampler no longer converts a 900-megapixel
raster per window.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from rasterio.crs import CRS  # type: ignore[import-untyped]

from terraluna_pipeline.geometry import (
    Body,
    EquirectangularLookup,
    GeographicLookup,
    PolarStereographicLookup,
    SourceLookup,
    body_radius_m,
)
from terraluna_pipeline.io import RasterWindow, read_geotiff_window

FloatArray = npt.NDArray[np.float64]


class SourceError(ValueError):
    """A raster whose georeferencing cannot be read into a T3 lookup."""


@dataclass(frozen=True, eq=False)
class RasterDemSource:
    """A real DEM raster (``mock`` is always False) implementing ``dataset.DemSource``."""

    body: Body
    raster: npt.NDArray[Any]
    nodata: float | None
    lookup: SourceLookup
    source_id: str
    path: str
    mock: bool = False

    def crop(self, lat_rad: FloatArray, lon_rad: FloatArray, margin: int = 3) -> RasterDemSource:
        """The sub-raster holding every point (source-read padding only)."""
        column, row = self.lookup.to_pixel(lat_rad, lon_rad)
        rows, cols = self.raster.shape
        c0 = max(math.floor(float(np.min(column))) - margin, 0)
        c1 = min(math.ceil(float(np.max(column))) + margin + 1, cols)
        r0 = max(math.floor(float(np.min(row))) - margin, 0)
        r1 = min(math.ceil(float(np.max(row))) + margin + 1, rows)
        # Points wholly outside the raster: keep a 2 x 2 corner; every point stays outside.
        c0, r0 = min(c0, cols - 2), min(r0, rows - 2)
        c1, r1 = max(c1, c0 + 2), max(r1, r0 + 2)
        sub = np.asarray(self.raster[r0:r1, c0:c1], dtype=np.float64)
        return dataclasses.replace(self, raster=sub, lookup=shifted_lookup(self.lookup, c0, r0))


def shifted_lookup(lookup: SourceLookup, col_off: int, row_off: int) -> SourceLookup:
    """The lookup of a crop starting at pixel (``col_off``, ``row_off``)."""
    if isinstance(lookup, GeographicLookup):
        return dataclasses.replace(
            lookup,
            origin_lon_deg=lookup.origin_lon_deg + col_off * lookup.pixel_width_deg,
            origin_lat_deg=lookup.origin_lat_deg + row_off * lookup.pixel_height_deg,
        )
    if isinstance(lookup, PolarStereographicLookup | EquirectangularLookup):
        return dataclasses.replace(
            lookup,
            origin_x_m=lookup.origin_x_m + col_off * lookup.pixel_width_m,
            origin_y_m=lookup.origin_y_m + row_off * lookup.pixel_height_m,
        )
    raise SourceError(f"unknown lookup {type(lookup).__name__}")


def lookup_from_window(window: RasterWindow, body: Body) -> SourceLookup:
    """Build the T3 lookup from a read window's CRS and geotransform."""
    if window.crs is None or window.transform is None:
        raise SourceError("the raster has no CRS or geotransform")
    t = window.transform
    if t.b != 0 or t.d != 0:
        raise SourceError("rotated geotransforms are not supported")
    crs = CRS.from_wkt(window.crs)
    if crs.is_geographic:
        if body != "earth":
            raise SourceError("a geographic CRS is expected only for Earth products")
        return GeographicLookup(
            origin_lon_deg=float(t.c),
            origin_lat_deg=float(t.f),
            pixel_width_deg=float(t.a),
            pixel_height_deg=float(t.e),
        )
    params = crs.to_dict()
    radius = float(params.get("R", params.get("a", math.nan)))
    if not math.isfinite(radius):
        raise SourceError("projected CRS without a sphere radius")
    if not math.isclose(radius, body_radius_m(body), rel_tol=1e-6):
        raise SourceError(f"CRS radius {radius} differs from the {body} D29 sphere")
    proj = params.get("proj")
    if proj == "stere":
        lat0 = float(params["lat_0"])
        if abs(abs(lat0) - 90.0) > 1e-9:
            raise SourceError("only polar stereographic aspects are supported")
        return PolarStereographicLookup(
            pole="south" if lat0 < 0 else "north",
            central_meridian_deg=float(params.get("lon_0", 0.0)),
            latitude_of_true_scale_deg=float(params.get("lat_ts", lat0)),
            scale_factor=float(params.get("k_0", params.get("k", 1.0))),
            radius_m=radius,
            false_easting_m=float(params.get("x_0", 0.0)),
            false_northing_m=float(params.get("y_0", 0.0)),
            origin_x_m=float(t.c),
            origin_y_m=float(t.f),
            pixel_width_m=float(t.a),
            pixel_height_m=float(t.e),
        )
    if proj == "eqc":
        return EquirectangularLookup(
            center_longitude_deg=float(params.get("lon_0", 0.0)),
            standard_parallel_deg=float(params.get("lat_ts", 0.0)),
            latitude_of_origin_deg=float(params.get("lat_0", 0.0)),
            radius_m=radius,
            false_easting_m=float(params.get("x_0", 0.0)),
            false_northing_m=float(params.get("y_0", 0.0)),
            origin_x_m=float(t.c),
            origin_y_m=float(t.f),
            pixel_width_m=float(t.a),
            pixel_height_m=float(t.e),
        )
    raise SourceError(f"unsupported projection {proj!r}")


def open_dem(path: Path, body: Body, source_id: str, *, dtype: Any = np.float32) -> RasterDemSource:
    """Read a whole single-band DEM GeoTIFF (stored values, nodata as declared)."""
    window = read_geotiff_window(path)
    if window.scale != 1.0 or window.offset != 0.0:
        raise SourceError(f"{path}: scale/offset {window.scale}/{window.offset} not supported")
    return RasterDemSource(
        body=body,
        raster=np.asarray(window.array, dtype=dtype),
        nodata=window.nodata,
        lookup=lookup_from_window(window, body),
        source_id=source_id,
        path=str(path),
    )


@dataclass(frozen=True, eq=False)
class ClassRaster:
    """A WorldCover class raster crop (uint8 codes) with its geographic lookup."""

    classes: npt.NDArray[np.uint8]
    nodata: float | None
    lookup: GeographicLookup
    path: str

    def sample_nearest(self, lat_rad: FloatArray, lon_rad: FloatArray) -> npt.NDArray[np.int32]:
        """Nearest class at each point; -1 outside the raster or on nodata (never a class).

        Rounding rule: the pixel whose centre is nearest, ``rint`` of the fractional pixel
        coordinate (ties to even), as ANA-05's nearest-class sampling.
        """
        column, row = self.lookup.to_pixel(lat_rad, lon_rad)
        r = np.rint(row).astype(np.int64)
        c = np.rint(column).astype(np.int64)
        h, w = self.classes.shape
        ok = (r >= 0) & (r < h) & (c >= 0) & (c < w)
        out = np.full(np.shape(lat_rad), -1, dtype=np.int32)
        out[ok] = self.classes[r[ok], c[ok]]
        if self.nodata is not None:
            out[out == int(self.nodata)] = -1
        return out


def read_class_crop(path: Path, lat_deg: FloatArray, lon_deg: FloatArray) -> ClassRaster | None:
    """Read the WorldCover crop covering the points, or None if the tile does not cover them."""
    from rasterio.windows import Window  # type: ignore[import-untyped]

    head = read_geotiff_window(path, Window(0, 0, 1, 1))
    t = head.raster_transform
    if t is None:
        raise SourceError(f"{path}: no geotransform")
    c0 = math.floor((float(np.min(lon_deg)) - t.c) / t.a) - 2
    c1 = math.ceil((float(np.max(lon_deg)) - t.c) / t.a) + 2
    r0 = math.floor((float(np.max(lat_deg)) - t.f) / t.e) - 2
    r1 = math.ceil((float(np.min(lat_deg)) - t.f) / t.e) + 2
    c0, r0 = max(c0, 0), max(r0, 0)
    c1, r1 = min(c1, head.raster_width), min(r1, head.raster_height)
    if c1 - c0 < 1 or r1 - r0 < 1:
        return None
    window = read_geotiff_window(path, Window(c0, r0, c1 - c0, r1 - r0))
    tt = window.transform
    assert tt is not None
    return ClassRaster(
        classes=np.asarray(window.array, dtype=np.uint8),
        nodata=window.nodata,
        lookup=GeographicLookup(
            origin_lon_deg=float(tt.c),
            origin_lat_deg=float(tt.f),
            pixel_width_deg=float(tt.a),
            pixel_height_deg=float(tt.e),
        ),
        path=str(path),
    )
