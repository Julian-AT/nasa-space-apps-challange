"""The read layer: checksums, windowed GeoTIFF reads, HTTP range reads and ProductRecords.

PRD 5 (entry rule), 8.3, 8.11 (``read_product``) and 8.12. Every source story reads through
these functions, so they never guess a value:

- A window is read exactly as stored. It is never padded, resampled, masked or scaled, and a
  window that leaves the raster is refused rather than clipped.
- A value the file does not state is ``None`` (the CRS, the nodata value, the geotransform, the
  band units), never a default such as 0 or an identity transform.
- ``read_product`` records units only as stated by the caller's metadata or by the file, and
  records ``"unknown"`` otherwise.
- ``fetch_range`` and ``fetch_file`` are the only functions here that use the network. They send
  no project or personal identifiers and refuse URLs with credentials or signatures.
  ``fetch_range`` sends a Range header; ``fetch_file`` downloads one whole file below a size limit.
  ``read_remote_geotiff_window`` reads one window of a remote GeoTIFF through GDAL and uses the
  network only through its ``fetch`` argument (``fetch_range`` by default), so only the byte
  ranges GDAL needs are transferred. ``ByteRanges`` keeps those bytes so the window can be
  decoded again offline. It counts the bytes received and logs every range request
  (``RangeExchange``: status, Content-Range, bytes received, retrieval time), and with
  ``with_header`` also reads the COG header (``read_geotiff_header``) through the same ranges.
- ``stac_search_url``, ``parse_stac_item_collection`` and ``fetch_stac_search`` run a STAC API
  item search and follow its ``next`` links; the pages are downloaded with ``fetch_file``.
- ``parse_pds3_label``, ``pds3_image_layout`` and ``pds3_polar_stereographic`` read a PDS3
  detached label (the image layout and the polar stereographic map projection it states), and
  ``fetch_pds3_rows`` reads whole image rows of a remote PDS3 image with one range request.
"""

from __future__ import annotations

import bisect
import dataclasses
import functools
import hashlib
import http.client
import io
import json
import math
import os
import re
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, Any, Final, Protocol, Required, TypedDict

import certifi
import numpy as np
import numpy.typing as npt
import pyproj
import rasterio  # type: ignore[import-untyped]
from affine import Affine
from rasterio.abc import FileContainer  # type: ignore[import-untyped]
from rasterio.errors import NotGeoreferencedWarning  # type: ignore[import-untyped]
from rasterio.windows import Window  # type: ignore[import-untyped]

from terraluna_pipeline.schema import UNKNOWN, Body, ProductRecord, has_url_credentials

# --- Checksums ----------------------------------------------------------------------------------


def sha256_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """The SHA-256 of a file as 64 lowercase hex characters, read in chunks of ``chunk_size``."""
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


# --- Windowed GeoTIFF reads ---------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class RasterWindow:
    """One band of a window, with the metadata the file states for it.

    ``array`` holds the stored values: nodata pixels keep their stored value, and ``scale`` and
    ``offset`` are reported but not applied. ``transform`` is the window's affine transform and
    ``raster_transform`` the whole raster's; both are None when the file has no geotransform.
    ``crs`` is the file's CRS as WKT, or None when the file has none.
    """

    array: npt.NDArray[Any]
    band: int
    col_off: int
    row_off: int
    nodata: float | None
    crs: str | None
    transform: Affine | None
    units: str | None
    scale: float
    offset: float
    raster_width: int
    raster_height: int
    raster_transform: Affine | None


def _whole_pixels(window: Window) -> tuple[int, int, int, int]:
    values = (window.col_off, window.row_off, window.width, window.height)
    if not all(float(value).is_integer() for value in values):
        raise ValueError(f"window {window} must have whole-pixel offsets and sizes")
    col_off, row_off, width, height = (int(value) for value in values)
    return col_off, row_off, width, height


def read_geotiff_window(
    path: Path,
    window: Window | None = None,
    *,
    band: int = 1,
    ranges: ByteRanges | None = None,
) -> RasterWindow:
    """Read ``band`` (1-based) of ``window``, or of the whole raster when ``window`` is None.

    Raises ValueError for a band the file does not have, and for a window that is empty, has
    fractional offsets or sizes, or does not lie wholly inside the raster. When ``ranges`` is
    given, the GeoTIFF is decoded from those stored byte ranges (opened as ``ranges.name``) and
    ``path`` is only used in messages; RangeCacheError is raised, never a zero-filled array, if
    GDAL needs a byte they do not hold.
    """
    if ranges is None:
        return _read_window(path, str(path), window, band, None)
    errors: list[Exception] = []
    container = _SingleFileContainer(ranges.name, ranges.size, ranges.read, errors)
    return _surface_errors(
        errors, lambda: _read_window(ranges.name, str(path), window, band, container)
    )


def _read_window(
    target: Path | str,
    label: str,
    window: Window | None,
    band: int,
    opener: FileContainer | None,
) -> RasterWindow:
    path = label
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with rasterio.open(target, opener=opener) as src:
            if not 1 <= band <= src.count:
                raise ValueError(f"{path} has bands 1 to {src.count}, not band {band}")
            if window is None:
                col_off, row_off, width, height = 0, 0, src.width, src.height
            else:
                col_off, row_off, width, height = _whole_pixels(window)
            if (
                width <= 0
                or height <= 0
                or col_off < 0
                or row_off < 0
                or col_off + width > src.width
                or row_off + height > src.height
            ):
                raise ValueError(
                    f"window (col_off={col_off}, row_off={row_off}, width={width}, "
                    f"height={height}) does not lie inside the {src.width} x {src.height} raster "
                    f"{path}; reads are never clipped or padded"
                )
            exact = Window(col_off, row_off, width, height)
            array = np.asarray(src.read(band, window=exact))
            nodata = src.nodatavals[band - 1]
            unit = src.units[band - 1]
            crs = src.crs.to_wkt() if src.crs is not None else None
            transform: Affine = src.window_transform(exact)
            raster_transform: Affine = src.transform
            gcps, _ = src.gcps
            has_control_points = bool(gcps) or bool(src.rpcs)
            raster_width, raster_height = int(src.width), int(src.height)
            scale, offset = float(src.scales[band - 1]), float(src.offsets[band - 1])
    not_georeferenced = False
    for warning in caught:
        if issubclass(warning.category, NotGeoreferencedWarning):
            not_georeferenced = True
        else:
            warnings.warn_explicit(
                warning.message, warning.category, warning.filename, warning.lineno
            )
    # GDAL reports an identity transform when the file has none; that is not a stated transform.
    if not_georeferenced or (raster_transform.is_identity and has_control_points):
        transform_or_none: Affine | None = None
        raster_transform_or_none: Affine | None = None
    else:
        transform_or_none, raster_transform_or_none = transform, raster_transform
    return RasterWindow(
        array=array,
        band=band,
        col_off=col_off,
        row_off=row_off,
        nodata=None if nodata is None else float(nodata),
        crs=crs,
        transform=transform_or_none,
        units=unit or None,
        scale=scale,
        offset=offset,
        raster_width=raster_width,
        raster_height=raster_height,
        raster_transform=raster_transform_or_none,
    )


def _band_count(path: Path, ranges: ByteRanges | None = None) -> int:
    if ranges is not None:
        errors: list[Exception] = []
        container = _SingleFileContainer(ranges.name, ranges.size, ranges.read, errors)
        return _surface_errors(errors, lambda: _band_count_of(ranges.name, container))
    return _band_count_of(path, None)


def _band_count_of(target: Path | str, opener: FileContainer | None) -> int:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(target, opener=opener) as src:
            return int(src.count)


def read_geotiff_tags(path: Path, *, ranges: ByteRanges | None = None) -> dict[str, str]:
    """The default-domain dataset tags of a GeoTIFF as stated, read from ``ranges`` if given."""

    def read(target: Path | str, opener: FileContainer | None) -> dict[str, str]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with rasterio.open(target, opener=opener) as src:
                return {str(key): str(value) for key, value in src.tags().items()}

    if ranges is None:
        return read(path, None)
    errors: list[Exception] = []
    container = _SingleFileContainer(ranges.name, ranges.size, ranges.read, errors)
    return _surface_errors(errors, lambda: read(ranges.name, container))


@dataclass(frozen=True, eq=False)
class GeoTiffHeader:
    """What a GeoTIFF's header states, read without decoding any pixel.

    ``block_shapes`` are ``(rows, columns)`` per band and ``overviews`` the decimation factors of
    band 1, both as GDAL reports them. ``image_structure`` is the IMAGE_STRUCTURE metadata domain
    (such as ``LAYOUT=COG``, ``COMPRESSION``, ``PREDICTOR``) and ``tags`` the default domain. Per
    band, ``nodata``, ``units`` and ``descriptions`` are None when the file states none. ``crs`` is
    WKT and ``transform`` the raster's affine; both are None when the file has none. Two headers
    are equal when every field is, a NaN nodata value matching a NaN.
    """

    width: int
    height: int
    count: int
    dtypes: tuple[str, ...]
    block_shapes: tuple[tuple[int, int], ...]
    overviews: tuple[int, ...]
    image_structure: dict[str, str]
    tags: dict[str, str]
    crs: str | None
    transform: Affine | None
    nodata: tuple[float | None, ...]
    units: tuple[str | None, ...]
    scales: tuple[float, ...]
    offsets: tuple[float, ...]
    descriptions: tuple[str | None, ...]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GeoTiffHeader):
            return NotImplemented

        def same(x: float | None, y: float | None) -> bool:
            if x is None or y is None:
                return x is y
            return x == y or (math.isnan(x) and math.isnan(y))

        fields = (
            "width",
            "height",
            "count",
            "dtypes",
            "block_shapes",
            "overviews",
            "image_structure",
            "tags",
            "crs",
            "transform",
            "units",
            "scales",
            "offsets",
            "descriptions",
        )
        return (
            all(getattr(self, name) == getattr(other, name) for name in fields)
            and len(self.nodata) == len(other.nodata)
            and all(same(x, y) for x, y in zip(self.nodata, other.nodata, strict=True))
        )


def _header_of(src: Any) -> GeoTiffHeader:
    """The header of an open rasterio dataset; reads metadata only, never pixel blocks."""
    gcps, _ = src.gcps
    raster_transform: Affine = src.transform
    # As in _read_window: GDAL's identity transform beside control points is not a stated one.
    georeferenced = not (raster_transform.is_identity and (bool(gcps) or bool(src.rpcs)))
    return GeoTiffHeader(
        width=int(src.width),
        height=int(src.height),
        count=int(src.count),
        dtypes=tuple(str(dtype) for dtype in src.dtypes),
        block_shapes=tuple((int(rows), int(cols)) for rows, cols in src.block_shapes),
        overviews=tuple(int(factor) for factor in src.overviews(1)),
        image_structure={
            str(key): str(value) for key, value in src.tags(ns="IMAGE_STRUCTURE").items()
        },
        tags={str(key): str(value) for key, value in src.tags().items()},
        crs=src.crs.to_wkt() if src.crs is not None else None,
        transform=raster_transform if georeferenced else None,
        nodata=tuple(None if value is None else float(value) for value in src.nodatavals),
        units=tuple(unit or None for unit in src.units),
        scales=tuple(float(value) for value in src.scales),
        offsets=tuple(float(value) for value in src.offsets),
        descriptions=tuple(text or None for text in src.descriptions),
    )


def _read_header(target: Path | str, opener: FileContainer | None) -> GeoTiffHeader:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with rasterio.open(target, opener=opener) as src:
            header = _header_of(src)
    not_georeferenced = False
    for warning in caught:
        if issubclass(warning.category, NotGeoreferencedWarning):
            not_georeferenced = True
        else:
            warnings.warn_explicit(
                warning.message, warning.category, warning.filename, warning.lineno
            )
    if not_georeferenced and header.transform is not None:
        return dataclasses.replace(header, transform=None)
    return header


def read_geotiff_header(path: Path, *, ranges: ByteRanges | None = None) -> GeoTiffHeader:
    """The header of a GeoTIFF (size, bands, tiling, overviews, metadata, CRS, nodata, units).

    No pixel block is decoded. When ``ranges`` is given, the header is read from those stored byte
    ranges (opened as ``ranges.name``) and ``path`` is not opened; a byte they do not hold raises
    RangeCacheError, so a header is never read from guessed bytes. A file without a geotransform
    reports ``transform`` None rather than GDAL's identity transform.
    """
    if ranges is None:
        return _read_header(path, None)
    errors: list[Exception] = []
    container = _SingleFileContainer(ranges.name, ranges.size, ranges.read, errors)
    return _surface_errors(errors, lambda: _read_header(ranges.name, container))


# --- read_product (PRD 8.11) --------------------------------------------------------------------


class ProductMetadata(TypedDict, total=False):
    """What the provider states about a product, as passed to ``read_product``.

    A key that is left out becomes ``"unknown"`` (``source_url`` becomes None, since "unknown" is
    not a URL). An explicit None marks a field that does not apply, such as the radar frequency
    of a DEM. The fields read from the file itself (``local_path``, ``checksum``,
    ``crs_and_frame``, ``native_grid``) and the ``verification_status`` are not accepted here.
    """

    body: Required[Body]
    provider: str
    version: str
    source_url: str | None
    acquisition_time: str | None
    frequency_or_wavelength: str | None
    transmit_receive_convention: str | None
    units: str
    calibration_type: str | None
    effective_resolution: str | None
    incidence_source: str | None
    qa_definition: str | None
    license_or_attribution: str


_METADATA_KEYS = ProductMetadata.__required_keys__ | ProductMetadata.__optional_keys__
_SET_BY_READ_PRODUCT = frozenset(
    {"product_id", "local_path", "checksum", "crs_and_frame", "native_grid", "verification_status"}
)
_UNKNOWN_BY_DEFAULT = (
    "provider",
    "version",
    "acquisition_time",
    "frequency_or_wavelength",
    "transmit_receive_convention",
    "calibration_type",
    "effective_resolution",
    "incidence_source",
    "qa_definition",
    "license_or_attribution",
)


def _check_metadata(metadata: ProductMetadata) -> None:
    keys = set(metadata)
    set_here = sorted(keys & _SET_BY_READ_PRODUCT)
    if set_here:
        raise ValueError(
            f"metadata may not set {', '.join(set_here)}: read_product takes them from its "
            "arguments and the file"
        )
    unknown = sorted(keys - _METADATA_KEYS)
    if unknown:
        raise ValueError(f"unknown metadata key(s): {', '.join(unknown)}")
    if "body" not in keys:
        raise ValueError("metadata must state the body (moon, mars or earth)")


_DATA_DIRECTORIES = ("data/raw/", "data/interim/", "data/processed/")
_RUN_FROM_ROOT = (
    "local_path is recorded relative to the repository root, so run from it or pass root"
)


def _local_path(path: Path, root: Path) -> str:
    resolved, base = Path(path).resolve(), Path(root).resolve()
    try:
        relative = resolved.relative_to(base).as_posix()
    except ValueError:
        raise ValueError(f"{path} is not under root {base}; {_RUN_FROM_ROOT}") from None
    if not relative.startswith(_DATA_DIRECTORIES):
        raise ValueError(
            f"{path} is not under data/raw, data/interim or data/processed of root {base}; "
            f"{_RUN_FROM_ROOT}"
        )
    return relative


def _units(metadata: ProductMetadata, bands: Sequence[RasterWindow]) -> str:
    """The stated units: from the metadata, else the file's, else "unknown". Never guessed."""
    declared = {band.units for band in bands}
    stated = metadata.get("units", UNKNOWN)
    if stated != UNKNOWN:
        contradicting = sorted(unit for unit in declared if unit is not None and unit != stated)
        if contradicting:
            raise ValueError(
                f"metadata units {stated!r} contradict the units the file declares: "
                f"{', '.join(contradicting)}"
            )
        return stated
    if len(declared) == 1 and None not in declared:
        (unit,) = declared
        assert unit is not None
        return unit
    return UNKNOWN


def _native_grid(window: RasterWindow) -> str:
    size = f"width={window.raster_width} height={window.raster_height}"
    t = window.raster_transform
    if t is None:
        return f"{size}; no geotransform in the file"
    return f"{size} transform=({t.a!r}, {t.b!r}, {t.c!r}, {t.d!r}, {t.e!r}, {t.f!r})"


def read_product(
    product_id: str,
    local_files: Sequence[Path],
    metadata: ProductMetadata,
    *,
    root: Path | None = None,
    window: Window | None = None,
    ranges: ByteRanges | None = None,
) -> tuple[tuple[RasterWindow, ...], ProductRecord]:
    """Read every band of one GeoTIFF and describe it with a ProductRecord (PRD 8.11).

    A ProductRecord has one ``local_path`` and one ``checksum``, so ``local_files`` holds exactly
    one file; a product made of several files gets one record per file. ``local_path`` is the
    file's path relative to ``root``, the repository root, which defaults to the working
    directory: the Makefile and ``uv run python -m terraluna_pipeline...`` run from the root. A
    file not under ``data/raw``, ``data/interim`` or ``data/processed`` of the root is refused, so
    a run from another directory fails instead of recording a wrong path.

    The checksum, CRS and native grid come from the file. Units come from the metadata or,
    failing that, from units the file declares for every band; otherwise they are "unknown".
    Metadata units that contradict the file are refused.

    The record is PARTIAL: this function decodes the file but does not mask or plot it, which the
    PRD 5 entry rule also requires before a product is VERIFIED.

    When ``ranges`` is given, ``local_files[0]`` is the local file that holds exactly
    ``ranges.data`` (a ValueError names the file if its SHA-256 differs) and the bands, band
    count included, are decoded from ``ranges``. The checksum is then that of the byte ranges
    held locally, not of the whole remote file.
    """
    files = list(local_files)
    if len(files) != 1:
        raise ValueError(
            "a ProductRecord has one local_path and one checksum, so read_product reads exactly "
            f"one file; got {len(files)}"
        )
    _check_metadata(metadata)
    path = Path(files[0])
    local_path = _local_path(path, Path.cwd() if root is None else root)
    checksum = sha256_file(path)
    if ranges is not None and checksum != hashlib.sha256(ranges.data).hexdigest():
        raise ValueError(
            f"{path} does not hold exactly the bytes of the byte ranges for {ranges.name}"
        )
    bands = tuple(
        read_geotiff_window(path, window, band=band, ranges=ranges)
        for band in range(1, _band_count(path, ranges) + 1)
    )
    fields: dict[str, Any] = {name: metadata.get(name, UNKNOWN) for name in _UNKNOWN_BY_DEFAULT}
    fields.update(
        product_id=product_id,
        source_url=metadata.get("source_url"),
        local_path=local_path,
        checksum=checksum,
        body=metadata["body"],
        units=_units(metadata, bands),
        native_grid=_native_grid(bands[0]),
        crs_and_frame=bands[0].crs if bands[0].crs is not None else UNKNOWN,
        verification_status="PARTIAL",
    )
    return bands, ProductRecord.model_validate(fields)


# --- HTTP range reads ---------------------------------------------------------------------------


class RangeReadError(RuntimeError):
    """A byte range could not be read exactly as requested."""


class RangeNotSupportedError(RangeReadError):
    """The server ignored the Range header and answered with the whole file."""


@dataclass(frozen=True)
class RangeResponse:
    """The bytes a server returned for one range request, with what it said about them.

    ``requested_first`` and ``requested_last`` are the inclusive range asked for, and
    ``first_byte`` and ``last_byte`` the range the server's Content-Range states; the last byte
    is smaller only when the file ends first. ``total_size`` is None when the server gives ``*``.
    ``final_url`` differs from ``url`` after a redirect. ``content_type``, ``last_modified`` and
    ``etag`` are the response headers as sent, None if absent.
    """

    url: str
    final_url: str
    requested_first: int
    requested_last: int
    status: int
    content_range: str
    first_byte: int
    last_byte: int
    total_size: int | None
    bytes_received: int
    data: bytes = field(repr=False)
    content_type: str | None = None
    last_modified: str | None = None
    etag: str | None = None


_CONTENT_RANGE = re.compile(r"bytes ([0-9]+)-([0-9]+)/([0-9]+|\*)")
_HTTP_SCHEMES = ("http", "https")


def _check_public_http_url(url: str, function: str = "fetch_range") -> None:
    if urllib.parse.urlsplit(url).scheme.lower() not in _HTTP_SCHEMES:
        raise ValueError(f"{function} reads http and https URLs only, not {url!r}")
    if has_url_credentials(url):
        # The URL is not repeated, so a signature or credential never reaches a log.
        raise ValueError(
            "the URL has credentials or a signature, or percent-encodes a letter, digit or -._~ "
            f"in its query or fragment; {function} reads public URLs"
        )


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows redirects to public http(s) URLs only, before any request is sent to them."""

    def __init__(self, error: type[RuntimeError] | None = None) -> None:
        self._error: type[RuntimeError] = RangeReadError if error is None else error

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        scheme = urllib.parse.urlsplit(newurl).scheme.lower()
        if scheme not in _HTTP_SCHEMES or has_url_credentials(newurl):
            fp.close()
            raise self._error(
                f"{req.full_url}: HTTP {code} redirects to a URL with credentials or a "
                "signature (or a percent-encoded letter, digit or -._~ in its query or "
                "fragment), or to a scheme other than http(s); it was not followed"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@functools.cache
def tls_context() -> ssl.SSLContext:
    """The TLS context of every request: hostname and certificate checks with certifi's CAs.

    The bundle is the Mozilla CA list that certifi ships, pinned by uv.lock, so verification does
    not depend on the platform's OpenSSL file, which can lack current roots (the uv-managed Python
    on macOS reads /etc/ssl/cert.pem). Verification is never disabled.
    """
    return ssl.create_default_context(cafile=certifi.where())


def _opener(redirects: _PublicRedirectHandler) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        redirects, urllib.request.HTTPSHandler(context=tls_context())
    )


def fetch_range(url: str, first: int, last: int, *, timeout: float = 60.0) -> RangeResponse:
    """Fetch bytes ``first`` to ``last`` (inclusive) of ``url`` with an HTTP Range request.

    The reply must be HTTP 206 with a Content-Range starting at ``first``, ending at ``last`` or
    at the end of the file, and with exactly that many bytes of identity-encoded body. A 200
    reply means the server ignored Range: RangeNotSupportedError is raised and the body is not
    read, so a large file is never downloaded by accident. Any other status, a mismatched or
    missing Content-Range, a short body, and DNS or connection failures raise RangeReadError
    naming the URL. Invalid ranges, non-http(s) URLs and URLs with credentials or signatures
    raise ValueError before anything is sent.
    """
    _check_public_http_url(url)
    if first < 0 or last < first:
        raise ValueError(f"invalid byte range {first}-{last}: need 0 <= first <= last")
    requested = f"bytes={first}-{last}"
    request = urllib.request.Request(
        url, headers={"Range": requested, "Accept-Encoding": "identity"}
    )
    opener = _opener(_PublicRedirectHandler())
    try:
        response: http.client.HTTPResponse = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        error.close()
        raise RangeReadError(f"{url}: HTTP {error.code} {error.reason} for {requested}") from error
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        raise RangeReadError(f"{url}: could not connect: {reason}") from error
    with response:
        status = response.status
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type")
        last_modified = response.headers.get("Last-Modified")
        etag = response.headers.get("ETag")
        if status == 200:
            raise RangeNotSupportedError(
                f"{url}: the server ignored the Range header and answered HTTP 200 with the "
                f"whole file for {requested}; the body was not read"
            )
        if status != 206:
            raise RangeReadError(f"{url}: expected HTTP 206 for {requested}, got {status}")
        encoding = response.headers.get("Content-Encoding")
        if encoding is not None and encoding.strip().lower() != "identity":
            raise RangeReadError(
                f"{url}: Content-Encoding {encoding!r}; byte ranges need identity encoding"
            )
        content_range = response.headers.get("Content-Range")
        if content_range is None:
            raise RangeReadError(f"{url}: HTTP 206 without a Content-Range header")
        match = _CONTENT_RANGE.fullmatch(content_range.strip())
        if match is None:
            raise RangeReadError(f"{url}: Content-Range {content_range!r} cannot be parsed")
        first_byte, last_byte = int(match.group(1)), int(match.group(2))
        total_size = None if match.group(3) == "*" else int(match.group(3))
        if first_byte != first or last_byte < first_byte or last_byte > last:
            raise RangeReadError(
                f"{url}: Content-Range {content_range!r} does not match the requested {requested}"
            )
        if total_size is not None and last_byte >= total_size:
            raise RangeReadError(f"{url}: Content-Range {content_range!r} ends past the file")
        if last_byte < last and (total_size is None or last_byte != total_size - 1):
            raise RangeReadError(
                f"{url}: the server returned a shorter range ({content_range!r}) than "
                f"{requested} without reaching the end of the file"
            )
        expected = last_byte - first_byte + 1
        try:
            data = response.read(expected + 1)
        except (OSError, http.client.HTTPException) as error:
            raise RangeReadError(f"{url}: the connection failed while reading: {error}") from error
    if len(data) != expected:
        raise RangeReadError(
            f"{url}: received {len(data)} bytes, but Content-Range {content_range!r} announces "
            f"{expected} bytes"
        )
    return RangeResponse(
        url=url,
        final_url=final_url,
        requested_first=first,
        requested_last=last,
        status=status,
        content_range=content_range,
        first_byte=first_byte,
        last_byte=last_byte,
        total_size=total_size,
        bytes_received=len(data),
        data=data,
        content_type=content_type,
        last_modified=last_modified,
        etag=etag,
    )


# --- Whole-file downloads -----------------------------------------------------------------------

WHOLE_FILE_LIMIT_BYTES = 200_000_000
"""Files of this size or larger are never downloaded whole (PRD 5); read them by range."""

_CHUNK_BYTES = 1 << 20


class DownloadError(RuntimeError):
    """A file could not be downloaded exactly as served."""


class FileTooLargeError(DownloadError):
    """The file is at or over the whole-file limit, so it was not downloaded."""


@dataclass(frozen=True)
class FetchedFile:
    """A downloaded file and what the server said about it.

    ``content_length``, ``last_modified`` and ``etag`` are the headers as sent, None if absent.
    ``sha256`` is the digest of the bytes written to ``path``.
    """

    url: str
    final_url: str
    status: int
    content_type: str | None
    content_length: int | None
    bytes_received: int
    last_modified: str | None
    etag: str | None
    sha256: str
    path: Path


def _too_large(url: str, size: str, max_bytes: int) -> FileTooLargeError:
    return FileTooLargeError(
        f"{url}: the file is {size} bytes, at or over the whole-file limit of {max_bytes} bytes; "
        "it was not downloaded, so use windowed range reads (fetch_range) instead"
    )


def fetch_file(
    url: str,
    dest: Path,
    *,
    max_bytes: int = WHOLE_FILE_LIMIT_BYTES,
    magic: Sequence[bytes] = (),
    timeout: float = 60.0,
) -> FetchedFile:
    """Download ``url`` whole to ``dest``, only if it is smaller than ``max_bytes``.

    One GET with ``Accept-Encoding: identity`` and no Range header. A Content-Length of
    ``max_bytes`` or more raises FileTooLargeError without reading the body; without a
    Content-Length the stream stops with that error once ``max_bytes`` bytes arrive. When
    ``magic`` is given the body must start with one of its byte strings, because some servers
    answer HTTP 200 with an HTML error page. The body goes to a hidden temporary file in
    ``dest.parent`` and replaces ``dest`` (mode 0o644) only after every check passes; on any
    failure the temporary file is removed and an existing ``dest`` is untouched.

    Raises ValueError before any request for a non-http(s) URL, a URL with credentials or a
    signature, or ``max_bytes`` <= 0, and FileNotFoundError when ``dest.parent`` does not exist.
    Every other failure (HTTP errors, connection failures, non-200 status, content encoding,
    wrong size, wrong magic bytes, refused redirects) raises DownloadError naming the URL.
    """
    _check_public_http_url(url, "fetch_file")
    if max_bytes <= 0:
        raise ValueError(f"max_bytes must be positive, got {max_bytes}")
    dest = Path(dest)
    if not dest.parent.is_dir():
        raise FileNotFoundError(f"the directory {dest.parent} for {dest.name} does not exist")
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    opener = _opener(_PublicRedirectHandler(DownloadError))
    try:
        response: http.client.HTTPResponse = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        error.close()
        raise DownloadError(f"{url}: HTTP {error.code} {error.reason}") from error
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        raise DownloadError(f"{url}: could not connect: {reason}") from error
    with response:
        status = response.status
        final_url = response.geturl()
        if status != 200:
            raise DownloadError(f"{url}: expected HTTP 200, got {status}")
        headers = response.headers
        encoding = headers.get("Content-Encoding")
        if encoding is not None and encoding.strip().lower() != "identity":
            raise DownloadError(
                f"{url}: Content-Encoding {encoding!r}; the file must be served unencoded"
            )
        content_type = headers.get("Content-Type")
        raw_length = headers.get("Content-Length")
        content_length: int | None = None
        if raw_length is not None:
            try:
                content_length = int(raw_length.strip())
            except ValueError:
                raise DownloadError(
                    f"{url}: Content-Length {raw_length!r} is not a number"
                ) from None
            if content_length < 0:
                raise DownloadError(f"{url}: Content-Length {raw_length!r} is negative")
            if content_length >= max_bytes:
                raise _too_large(url, str(content_length), max_bytes)
        last_modified = headers.get("Last-Modified")
        etag = headers.get("ETag")
        descriptor, temp_name = tempfile.mkstemp(
            dir=dest.parent, prefix=f".{dest.name}.", suffix=".part"
        )
        temp_path = Path(temp_name)
        try:
            digest = hashlib.sha256()
            head = b""
            wanted_head = max((len(prefix) for prefix in magic), default=0)
            received = 0
            with os.fdopen(descriptor, "wb") as out:
                while True:
                    try:
                        chunk = response.read(_CHUNK_BYTES)
                    except (OSError, http.client.HTTPException) as error:
                        raise DownloadError(
                            f"{url}: the connection failed after {received} bytes: {error}"
                        ) from error
                    if not chunk:
                        break
                    received += len(chunk)
                    if received >= max_bytes:
                        raise _too_large(url, f"at least {received}", max_bytes)
                    if len(head) < wanted_head:
                        head = (head + chunk)[:wanted_head]
                    digest.update(chunk)
                    out.write(chunk)
            if content_length is not None and received != content_length:
                raise DownloadError(
                    f"{url}: received {received} bytes, but Content-Length announces "
                    f"{content_length} bytes"
                )
            if magic and not any(head.startswith(prefix) for prefix in magic):
                raise DownloadError(
                    f"{url}: the body does not start with the expected file signature "
                    f"(Content-Type {content_type!r}); the server may have sent an error page "
                    "instead of the file"
                )
            temp_path.chmod(0o644)
            os.replace(temp_path, dest)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
    return FetchedFile(
        url=url,
        final_url=final_url,
        status=status,
        content_type=content_type,
        content_length=content_length,
        bytes_received=received,
        last_modified=last_modified,
        etag=etag,
        sha256=digest.hexdigest(),
        path=dest,
    )


# --- Stored byte ranges and windowed reads of a remote GeoTIFF ----------------------------------

RANGE_BLOCK_BYTES: Final = 1 << 18
"""The aligned block size, in bytes, of a remote windowed read."""


class RangeFetcher(Protocol):
    """One HTTP range request; ``fetch_range`` satisfies it."""

    def __call__(self, url: str, first: int, last: int) -> RangeResponse: ...


class RangeCacheError(ValueError):
    """A decode asked for bytes that the stored byte ranges do not hold."""


@dataclass(frozen=True)
class ByteRanges:
    """The bytes of one remote file that were actually read, with where they sit in the file.

    ``name`` is the file name GDAL sees (a plain name, no ``/``) and ``size`` the remote file's
    size. ``ranges`` are inclusive ``(first, last)`` byte positions, sorted, disjoint and not
    adjacent (adjacent blocks are merged), each ending inside the file. ``data`` is the bytes of
    every range concatenated in order. A byte outside the ranges is unknown, never zero.
    """

    name: str
    size: int
    ranges: tuple[tuple[int, int], ...]
    data: bytes = field(repr=False)
    _starts: tuple[int, ...] = field(init=False, repr=False, compare=False)
    _offsets: tuple[int, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.name or "/" in self.name or "\\" in self.name or self.name in (".", ".."):
            raise ValueError(
                f"ByteRanges name must be a plain, non-empty file name, got {self.name!r}"
            )
        if self.size <= 0:
            raise ValueError(f"ByteRanges size must be positive, got {self.size}")
        if not self.ranges:
            raise ValueError("ByteRanges needs at least one range")
        previous_last = -2
        offsets: list[int] = []
        total = 0
        for first, last in self.ranges:
            if first < 0 or last < first:
                raise ValueError(f"invalid byte range {first}-{last}: need 0 <= first <= last")
            if last >= self.size:
                raise ValueError(f"byte range {first}-{last} ends past the {self.size}-byte file")
            if first <= previous_last:
                raise ValueError(
                    f"byte range {first}-{last} is unsorted or overlaps the one before"
                )
            if first == previous_last + 1:
                raise ValueError(
                    f"byte range {first}-{last} is adjacent to the one before; merge them"
                )
            previous_last = last
            offsets.append(total)
            total += last - first + 1
        if len(self.data) != total:
            raise ValueError(f"data holds {len(self.data)} bytes, but the ranges cover {total}")
        object.__setattr__(self, "_starts", tuple(first for first, _ in self.ranges))
        object.__setattr__(self, "_offsets", tuple(offsets))

    def read(self, offset: int, length: int) -> bytes:
        """The bytes ``[offset, offset + length)`` clipped to the file size, like a file read.

        At or after the end of the file this is ``b""``. RangeCacheError names the first missing
        byte span, and the file, if a requested byte below ``size`` is not held.
        """
        if offset < 0 or length < 0:
            raise ValueError(f"invalid read of {length} bytes at offset {offset}")
        end = min(offset + length, self.size)
        parts: list[bytes] = []
        position = offset
        while position < end:
            index = bisect.bisect_right(self._starts, position) - 1
            if index >= 0 and self.ranges[index][1] >= position:
                first, last = self.ranges[index]
                stop = min(last + 1, end)
                start_in_data = self._offsets[index] + position - first
                parts.append(self.data[start_in_data : start_in_data + stop - position])
                position = stop
                continue
            following = self._starts[index + 1] if index + 1 < len(self._starts) else self.size
            missing_last = min(following, end) - 1
            raise RangeCacheError(
                f"bytes {position}-{missing_last} of {self.name} are not held in the stored byte "
                "ranges, so they cannot be decoded; they were not read from the remote file"
            )
        return b"".join(parts)


def _surface_errors[T](errors: list[Exception], call: Callable[[], T]) -> T:
    """Run ``call``; an error stored by a GDAL read callback is raised in place of anything else.

    rasterio calls the file's ``read`` from C code, which cannot propagate an exception, so the
    reader stores it and returns no bytes. It is raised here, after rasterio returns or fails.
    """
    try:
        result = call()
    except Exception as error:
        if errors:
            raise errors[0] from error
        raise
    if errors:
        raise errors[0]
    return result


class _RangeFile(io.RawIOBase):
    """A read-only file whose bytes come from ``reader``; ``read`` never raises."""

    def __init__(
        self,
        size: int,
        reader: Callable[[int, int], bytes],
        errors: list[Exception],
    ) -> None:
        super().__init__()
        self._size = size
        self._reader = reader
        self._errors = errors
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        base = {os.SEEK_SET: 0, os.SEEK_CUR: self._position, os.SEEK_END: self._size}.get(whence)
        if base is None:
            raise ValueError(f"invalid whence {whence}")
        self._position = max(0, base + offset)
        return self._position

    def read(self, size: int = -1) -> bytes:
        try:
            length = self._size - self._position if size < 0 else size
            data = self._reader(self._position, max(length, 0))
        except Exception as error:  # GDAL calls this from C, which cannot propagate it.
            self._errors.append(error)
            return b""
        self._position += len(data)
        return data


class _SingleFileContainer(FileContainer):  # type: ignore[misc]
    """A rasterio opener that serves exactly one read-only file and nothing else."""

    def __init__(
        self,
        name: str,
        size: int,
        reader: Callable[[int, int], bytes],
        errors: list[Exception],
    ) -> None:
        self._name = name
        self._size = size
        self._reader = reader
        self._errors = errors

    def _check(self, path: str) -> None:
        if path != self._name:
            raise FileNotFoundError(path)

    def open(self, path: str, mode: str = "r", **kwds: Any) -> _RangeFile:
        self._check(path)
        if "w" in mode or "a" in mode or "+" in mode:
            raise PermissionError(f"{path} is read-only")
        return _RangeFile(self._size, self._reader, self._errors)

    def isfile(self, path: str) -> bool:
        return path == self._name

    def isdir(self, path: str) -> bool:
        return False

    def ls(self, path: str) -> list[str]:
        return []

    def mtime(self, path: str) -> int:
        self._check(path)
        return 0

    def rm(self, path: str) -> None:
        raise PermissionError(f"{path} is read-only")

    def size(self, path: str) -> int:
        self._check(path)
        return self._size


@dataclass(frozen=True)
class RangeExchange:
    """One range request of a windowed read and what the server answered (PRD 9, Performance).

    ``requested_first`` and ``requested_last`` are the inclusive byte range asked for, and
    ``status``, ``content_range`` and ``bytes_received`` the reply's HTTP status, Content-Range
    header and body length. ``retrieved_at`` is the clock's time when the reply had arrived, or
    None when the read was given no clock.
    """

    url: str
    requested_first: int
    requested_last: int
    status: int
    content_range: str
    bytes_received: int
    retrieved_at: datetime | None


class _BlockCache:
    """Aligned blocks of one remote file, each contiguous run of missing blocks fetched once."""

    def __init__(
        self,
        url: str,
        fetch: RangeFetcher,
        block_bytes: int,
        max_bytes: int,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.url = url
        self._fetch = fetch
        self._block = block_bytes
        self._max_bytes = max_bytes
        self._now = now
        self.blocks: dict[int, bytes] = {}
        self.first: RangeResponse | None = None
        self.size: int | None = None
        self.requests = 0
        self.bytes_received = 0
        self.exchanges: list[RangeExchange] = []

    def _request(self, first_block: int, last_block: int) -> None:
        first = first_block * self._block
        last = (last_block + 1) * self._block - 1
        expected = last - first + 1 if self.size is None else min(last, self.size - 1) - first + 1
        if self.bytes_received + expected >= self._max_bytes:
            raise RangeReadError(
                f"{self.url}: reading bytes {first}-{last} would bring the bytes received to "
                f"{self.bytes_received + expected}, at or over the limit of {self._max_bytes}; a "
                "windowed read never downloads the file whole"
            )
        response = self._fetch(self.url, first, last)
        self.requests += 1
        self.bytes_received += response.bytes_received
        self.exchanges.append(
            RangeExchange(
                url=self.url,
                requested_first=first,
                requested_last=last,
                status=response.status,
                content_range=response.content_range,
                bytes_received=response.bytes_received,
                retrieved_at=None if self._now is None else self._now(),
            )
        )
        if response.total_size is None:
            raise RangeReadError(f"{self.url}: the server does not state the file size")
        if self.first is None:
            self.first, self.size = response, response.total_size
        else:
            changed = (
                response.total_size != self.size
                or (self.first.etag is not None and response.etag != self.first.etag)
                or (
                    self.first.last_modified is not None
                    and response.last_modified != self.first.last_modified
                )
            )
            if changed:
                raise RangeReadError(f"{self.url}: the file changed while it was read")
        end = min(last, response.total_size - 1)
        if response.first_byte != first or response.last_byte != end:
            raise RangeReadError(
                f"{self.url}: asked for bytes {first}-{end} but received "
                f"{response.first_byte}-{response.last_byte}"
            )
        for index in range(first_block, last_block + 1):
            start = index * self._block
            if start > end:
                break
            self.blocks[index] = response.data[start - first : start - first + self._block]

    def read(self, offset: int, length: int) -> bytes:
        if self.size is None:
            self._request(0, 0)
        assert self.size is not None
        end = min(offset + length, self.size)
        if offset >= end:
            return b""
        first_block, last_block = offset // self._block, (end - 1) // self._block
        run_start: int | None = None
        for index in range(first_block, last_block + 2):
            missing = index <= last_block and index not in self.blocks
            if missing and run_start is None:
                run_start = index
            elif not missing and run_start is not None:
                self._request(run_start, index - 1)
                run_start = None
        data = b"".join(self.blocks[index] for index in range(first_block, last_block + 1))
        start = offset - first_block * self._block
        return data[start : start + end - offset]

    def byte_ranges(self, name: str) -> ByteRanges:
        assert self.size is not None
        runs: list[list[int]] = []
        for index in sorted(self.blocks):
            if runs and runs[-1][1] == index - 1:
                runs[-1][1] = index
            else:
                runs.append([index, index])
        ranges = tuple(
            (a * self._block, min((b + 1) * self._block, self.size) - 1) for a, b in runs
        )
        data = b"".join(self.blocks[index] for index in sorted(self.blocks))
        return ByteRanges(name=name, size=self.size, ranges=ranges, data=data)


@dataclass(frozen=True)
class RemoteWindow:
    """One window of a remote GeoTIFF, with the evidence of how it was read.

    ``status``, ``final_url``, ``content_type``, ``last_modified`` and ``etag`` come from the
    first range response. ``requests`` is the number of range requests sent and
    ``bytes_received`` the bytes they returned, which equals ``len(ranges.data)``. ``window`` is
    ``(col_off, row_off, width, height)``. ``band`` is the window as decoded. ``exchanges`` lists
    every range request in the order sent, and ``header`` is the file's header when the read was
    asked for it (``with_header``), else None.
    """

    url: str
    final_url: str
    status: int
    content_type: str | None
    last_modified: str | None
    etag: str | None
    requests: int
    bytes_received: int
    ranges: ByteRanges
    window: tuple[int, int, int, int]
    raster_width: int
    raster_height: int
    band: RasterWindow = field(repr=False, compare=False)
    exchanges: tuple[RangeExchange, ...] = ()
    header: GeoTiffHeader | None = field(default=None, compare=False)


def _same_decode(a: RasterWindow, b: RasterWindow) -> bool:
    def same_number(x: float | None, y: float | None) -> bool:
        if x is None or y is None:
            return x is y
        return x == y or (np.isnan(x) and np.isnan(y))

    floating = a.array.dtype.kind in "fc"
    return (
        a.array.dtype == b.array.dtype
        and a.array.shape == b.array.shape
        and bool(np.array_equal(a.array, b.array, equal_nan=floating))
        and same_number(a.nodata, b.nodata)
        and a.crs == b.crs
        and a.transform == b.transform
    )


def read_remote_geotiff_window(
    url: str,
    choose_window: Callable[[int, int], Window],
    *,
    band: int = 1,
    fetch: RangeFetcher = fetch_range,
    block_bytes: int = RANGE_BLOCK_BYTES,
    max_bytes: int = WHOLE_FILE_LIMIT_BYTES,
    with_header: bool = False,
    now: Callable[[], datetime] | None = None,
) -> RemoteWindow:
    """Read ``band`` of one window of a remote GeoTIFF; GDAL's reads are served by range requests.

    ``choose_window(width, height)`` picks the window once the raster size is known. GDAL reads
    through an in-memory cache of aligned ``block_bytes`` blocks; each contiguous run of missing
    blocks is one ``fetch(url, first, last)`` call, the first being block 0, whose Content-Range
    must state the file size. Every response must state the same size, and the same ETag and
    Last-Modified as the first, or the read fails with RangeReadError ("the file changed while it
    was read"). A request that would bring the bytes received to ``max_bytes`` is not sent, so a
    windowed read never downloads the file whole. Errors from ``fetch`` propagate unchanged.

    The window is then decoded again from the fetched bytes alone (``ByteRanges``); any difference
    in values, dtype, nodata, CRS or transform raises RangeReadError. Raises ValueError, before
    any request, for a non-http(s) URL, one with credentials or a signature, ``block_bytes`` or
    ``max_bytes`` <= 0, or a URL whose last path segment is not a plain file name.

    With ``with_header``, the file's header (``read_geotiff_header``: tiling, overviews, metadata
    domains) is read through the same cache before the window, so the stored byte ranges hold it
    too; it is read again from them and must be equal. ``now`` stamps each logged exchange.
    """
    _check_public_http_url(url, "read_remote_geotiff_window")
    if block_bytes <= 0:
        raise ValueError(f"block_bytes must be positive, got {block_bytes}")
    if max_bytes <= 0:
        raise ValueError(f"max_bytes must be positive, got {max_bytes}")
    name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError(f"{url} does not end in a plain file name that GDAL can open")

    cache = _BlockCache(url, fetch, block_bytes, max_bytes, now)
    errors: list[Exception] = []
    cache.read(0, 1)  # block 0 first: it states the file size, which GDAL asks for at once
    assert cache.size is not None and cache.first is not None
    container = _SingleFileContainer(name, cache.size, cache.read, errors)
    header: GeoTiffHeader | None = None

    def read() -> RasterWindow:
        nonlocal header
        if with_header:
            header = _read_header(name, container)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with rasterio.open(name, opener=container) as src:
                width, height = int(src.width), int(src.height)
        return _read_window(name, url, choose_window(width, height), band, container)

    decoded = _surface_errors(errors, read)
    ranges = cache.byte_ranges(name)
    again = read_geotiff_window(Path(url), Window(*_window_of(decoded)), band=band, ranges=ranges)
    if not _same_decode(decoded, again):
        raise RangeReadError(
            f"{url}: decoding the window again from the stored byte ranges gave a different "
            "result; the byte ranges are not a faithful copy of what GDAL read"
        )
    if header is not None and read_geotiff_header(Path(url), ranges=ranges) != header:
        raise RangeReadError(
            f"{url}: reading the header again from the stored byte ranges gave a different "
            "result; the byte ranges are not a faithful copy of what GDAL read"
        )
    first = cache.first
    return RemoteWindow(
        url=url,
        final_url=first.final_url,
        status=first.status,
        content_type=first.content_type,
        last_modified=first.last_modified,
        etag=first.etag,
        requests=cache.requests,
        bytes_received=cache.bytes_received,
        ranges=ranges,
        window=_window_of(decoded),
        raster_width=decoded.raster_width,
        raster_height=decoded.raster_height,
        band=decoded,
        exchanges=tuple(cache.exchanges),
        header=header,
    )


def _window_of(window: RasterWindow) -> tuple[int, int, int, int]:
    height, width = window.array.shape
    return window.col_off, window.row_off, int(width), int(height)


# --- STAC item search (PRD 5 Mars inputs, 8.3) --------------------------------------------------
#
# STAC API Item Search (api.stacspec.org v1.0.0, item-search): GET <api>/search with
# ``collections``, ``bbox`` and ``limit``. A response is a GeoJSON FeatureCollection; the next page
# is the link with ``rel`` ``next``, whose ``method`` defaults to GET, and its absence marks the
# last page. A bbox whose west longitude is greater than its east longitude crosses the
# antimeridian (RFC 7946 5.2).

STAC_PAGE_LIMIT_BYTES: Final = 20_000_000
"""The largest search page downloaded; a page of 100 USGS CTX DTM items is about 1 MB."""

STAC_MAX_PAGES: Final = 100
"""The most pages one search may take before it is refused rather than followed further."""


class StacError(ValueError):
    """A STAC response is not the item collection it should be."""


class FileFetcher(Protocol):
    """A whole-file download with ``io.fetch_file``'s signature."""

    def __call__(
        self, url: str, dest: Path, *, max_bytes: int, magic: Sequence[bytes] = ...
    ) -> FetchedFile: ...


@dataclass(frozen=True)
class StacItemCollection:
    """One page of an item search as stated: its features, counts and next link.

    ``number_matched`` and ``number_returned`` are the page's ``numberMatched`` and
    ``numberReturned``, None when absent. ``next_url`` is the ``next`` link resolved against the
    page URL, None on the last page.
    """

    features: tuple[dict[str, Any], ...] = field(repr=False)
    number_matched: int | None
    number_returned: int | None
    next_url: str | None

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(str(feature["id"]) for feature in self.features)


@dataclass(frozen=True)
class StacPage:
    """One downloaded search page: its 1-based number, the URL requested and its content."""

    number: int
    url: str
    fetched: FetchedFile
    content: StacItemCollection


def _bbox_text(bbox: Sequence[float]) -> str:
    values = [float(value) for value in bbox]
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        raise ValueError(f"bbox must be four finite numbers (west, south, east, north), got {bbox}")
    west, south, east, north = values
    if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
        raise ValueError(f"bbox longitudes must lie in -180 to 180, got {bbox}")
    if not (-90.0 <= south <= north <= 90.0):
        raise ValueError(f"bbox latitudes must satisfy -90 <= south <= north <= 90, got {bbox}")
    return ",".join(repr(value) for value in values)


def stac_search_url(api_url: str, *, collection: str, bbox: Sequence[float], limit: int) -> str:
    """The GET item-search URL for ``collection`` over ``bbox`` with ``limit`` items per page.

    ``bbox`` is (west, south, east, north) in degrees; west greater than east crosses the
    antimeridian, as RFC 7946 5.2 defines. The numbers are written in Python's shortest
    round-trip form, so 76.69 stays ``76.69``.
    """
    if not collection:
        raise ValueError("a STAC search needs a collection")
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")
    query = urllib.parse.urlencode(
        {"collections": collection, "bbox": _bbox_text(bbox), "limit": str(limit)}
    )
    return f"{api_url.rstrip('/')}/search?{query}"


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def _count(body: dict[str, Any], key: str, where: str) -> int | None:
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StacError(f"{where}: {key} {value!r} is not a non-negative integer")
    return value


def parse_stac_item_collection(data: bytes, page_url: str) -> StacItemCollection:
    """Parse one item-search page, refusing anything that is not a FeatureCollection of items.

    Strict JSON only (no NaN or Infinity). Every feature must be a ``Feature`` with a non-empty
    string ``id``. A stated ``numberReturned`` must equal the number of features. At most one
    ``next`` link is allowed; it must be a GET (the default) to an http(s) URL with no ``body``,
    since a POST continuation would need the request body merged in. Every refusal is a
    StacError naming ``page_url``.
    """
    try:
        body = json.loads(data.decode("utf-8"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise StacError(f"{page_url}: the response is not strict JSON: {error}") from None
    if not isinstance(body, dict) or body.get("type") != "FeatureCollection":
        raise StacError(f"{page_url}: the response is not a FeatureCollection")
    features = body.get("features")
    if not isinstance(features, list):
        raise StacError(f"{page_url}: the FeatureCollection has no features list")
    for index, item in enumerate(features):
        if not isinstance(item, dict) or item.get("type") != "Feature":
            raise StacError(f"{page_url}: features[{index}] is not a Feature")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise StacError(f"{page_url}: features[{index}] has no non-empty string id")
    number_matched = _count(body, "numberMatched", page_url)
    number_returned = _count(body, "numberReturned", page_url)
    if number_returned is not None and number_returned != len(features):
        raise StacError(
            f"{page_url}: numberReturned is {number_returned}, but the page holds "
            f"{len(features)} features"
        )
    links = body.get("links", [])
    if not isinstance(links, list) or not all(isinstance(link, dict) for link in links):
        raise StacError(f"{page_url}: links is not a list of link objects")
    nexts = [link for link in links if link.get("rel") == "next"]
    if len(nexts) > 1:
        raise StacError(f"{page_url}: the page has {len(nexts)} next links")
    next_url: str | None = None
    if nexts:
        link = nexts[0]
        method = link.get("method") or "GET"
        if not isinstance(method, str) or method.upper() != "GET":
            raise StacError(
                f"{page_url}: the next link uses {method!r}; only GET continuations are "
                "followed (a POST continuation needs its body merged into the request)"
            )
        if "body" in link:
            raise StacError(f"{page_url}: the next link carries a body, which GET cannot send")
        href = link.get("href")
        if not isinstance(href, str) or not href:
            raise StacError(f"{page_url}: the next link has no href")
        next_url = urllib.parse.urljoin(page_url, href)
        if urllib.parse.urlsplit(next_url).scheme.lower() not in _HTTP_SCHEMES:
            raise StacError(f"{page_url}: the next link {next_url!r} is not an http(s) URL")
    return StacItemCollection(
        features=tuple(features),
        number_matched=number_matched,
        number_returned=number_returned,
        next_url=next_url,
    )


def fetch_stac_search(
    first_url: str,
    dest_dir: Path,
    file_name: Callable[[int], str],
    *,
    download: FileFetcher = fetch_file,
    max_pages: int = STAC_MAX_PAGES,
    max_bytes: int = STAC_PAGE_LIMIT_BYTES,
) -> tuple[StacPage, ...]:
    """Download every page of an item search, following ``next`` links until there is none.

    Page ``n`` (from 1) is saved as ``dest_dir / file_name(n)`` with ``download``
    (``fetch_file``: a whole GET below ``max_bytes``, no credentials or signed URLs), then parsed
    with ``parse_stac_item_collection`` against the URL the server finally answered from. A next
    link that repeats a page already requested, or a search that still has a next link after
    ``max_pages`` pages, raises DownloadError naming the URL; download failures propagate as
    DownloadError naming the URL, and a malformed page as StacError naming it.
    """
    if max_pages <= 0:
        raise ValueError(f"max_pages must be positive, got {max_pages}")
    pages: list[StacPage] = []
    requested: set[str] = set()
    url = first_url
    while True:
        if len(pages) == max_pages:
            raise DownloadError(
                f"{first_url}: the search has more than {max_pages} pages; the next page "
                f"{url} was not requested"
            )
        if url in requested:
            raise DownloadError(f"{url}: the next link repeats a page already requested")
        requested.add(url)
        number = len(pages) + 1
        fetched = download(url, Path(dest_dir) / file_name(number), max_bytes=max_bytes)
        content = parse_stac_item_collection(fetched.path.read_bytes(), fetched.final_url)
        pages.append(StacPage(number=number, url=url, fetched=fetched, content=content))
        if content.next_url is None:
            return tuple(pages)
        url = content.next_url


# --- PDS3 labels --------------------------------------------------------------------------------
#
# Object Description Language as used by PDS3 detached labels (PDS3 Standards Reference, chapter 12
# and appendix C for the SAMPLE_TYPE names): ``KEY = value`` statements, OBJECT/GROUP blocks,
# strings, numbers with units, sequences and sets. Nothing is guessed: anything the parser does
# not understand is a ValueError with the line number.

Pds3Scalar = str | int | float


@dataclass(frozen=True)
class Pds3Quantity:
    """A number with the unit the label gives it, for example ``1737.4 <km>``."""

    value: int | float
    unit: str


Pds3Value = Pds3Scalar | Pds3Quantity | tuple["Pds3Value", ...]


@dataclass(frozen=True)
class Pds3Item:
    """One ``KEY = value`` statement; ``raw`` is the value text as written, for quoting."""

    key: str
    value: Pds3Value
    raw: str

    def text(self) -> str:
        """A string value with each run of whitespace (newlines included) as one space."""
        if not isinstance(self.value, str):
            raise TypeError(f"{self.key} is not a string: {self.raw!r}")
        return re.sub(r"\s+", " ", self.value)


@dataclass(frozen=True)
class Pds3Object:
    """The ROOT of a label or one OBJECT or GROUP block, with its statements and sub-blocks."""

    kind: str
    name: str
    items: tuple[Pds3Item, ...]
    children: tuple[Pds3Object, ...]

    def _where(self) -> str:
        return "the label root" if self.kind == "ROOT" else f"{self.kind} {self.name}"

    def get(self, key: str) -> Pds3Item | None:
        """The statement of this block with that key (not its children), or None."""
        found = [item for item in self.items if item.key == key]
        if len(found) > 1:
            raise ValueError(f"{key} is stated {len(found)} times in {self._where()}")
        return found[0] if found else None

    def require(self, key: str) -> Pds3Item:
        item = self.get(key)
        if item is None:
            raise ValueError(f"{key} is missing from {self._where()}")
        return item

    def child(self, name: str) -> Pds3Object:
        found = [child for child in self.children if child.name == name]
        if len(found) != 1:
            raise ValueError(
                f"expected exactly one block named {name} in {self._where()}, found {len(found)}"
            )
        return found[0]


_PDS3_INT = re.compile(r"[+-]?[0-9]+")
_PDS3_REAL = re.compile(r"[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+(?=[eE]))(?:[eE][+-]?[0-9]+)?")
_PDS3_BASED = re.compile(r"([0-9]+)#([+-]?[0-9A-Za-z]+)#")
_PDS3_BLOCK_END = {"END_OBJECT": "OBJECT", "END_GROUP": "GROUP"}
_PDS3_DELIMITERS = " \t\n\r,(){}<>=\"'/"


class _Pds3Parser:
    def __init__(self, text: str) -> None:
        self.text = text.replace("\r\n", "\n")
        self.pos = 0

    def line(self, pos: int | None = None) -> int:
        return self.text.count("\n", 0, self.pos if pos is None else pos) + 1

    def fail(self, message: str, pos: int | None = None) -> ValueError:
        return ValueError(f"PDS3 label line {self.line(pos)}: {message}")

    def skip(self, *, newlines: bool = True) -> None:
        """Skip whitespace and comments, and newlines too unless ``newlines`` is False."""
        text = self.text
        while self.pos < len(text):
            char = text[self.pos]
            if char == "\n" and not newlines:
                return
            if char.isspace():
                self.pos += 1
            elif text.startswith("/*", self.pos):
                end = text.find("*/", self.pos + 2)
                if end < 0:
                    raise self.fail("unterminated comment")
                self.pos = end + 2
            else:
                return

    def parse(self) -> Pds3Object:
        stack: list[tuple[str, str, int, list[Pds3Item], list[Pds3Object]]] = [
            ("ROOT", "", 1, [], [])
        ]
        while True:
            self.skip()
            if self.pos >= len(self.text):
                last = len(self.text.rstrip())
                if len(stack) > 1:
                    kind, name, opened, _, _ = stack[-1]
                    raise self.fail(f"unclosed {kind} {name} (opened on line {opened})", last)
                raise self.fail("the label has no END statement", last)
            start = self.pos
            while self.pos < len(self.text) and self.text[self.pos] not in " \t\n\r=/":
                self.pos += 1
            key = self.text[start : self.pos]
            if not key:
                raise self.fail(f"expected a keyword, found {self.text[start]!r}", start)
            self.skip(newlines=False)
            has_equals = self.pos < len(self.text) and self.text[self.pos] == "="
            if key == "END" and not has_equals:
                self.expect_statement_end()
                break
            if key in _PDS3_BLOCK_END and not has_equals:
                self.expect_statement_end()
                self.close(stack, key, None, start)
                continue
            if not has_equals:
                raise self.fail(f"the statement {key!r} has no '='", start)
            self.pos += 1
            if key in ("OBJECT", "GROUP") or key in _PDS3_BLOCK_END:
                name_item = self.parse_item(key)
                found = name_item.value
                if not isinstance(found, str) or not found:
                    raise self.fail(f"{key} needs a name, found {name_item.raw!r}", start)
                name = found
                if key in _PDS3_BLOCK_END:
                    self.close(stack, key, name, start)
                else:
                    stack.append((key, name, self.line(start), [], []))
                continue
            stack[-1][3].append(self.parse_item(key))
        if len(stack) > 1:
            kind, name, opened, _, _ = stack[-1]
            raise self.fail(f"unclosed {kind} {name} (opened on line {opened})")
        return Pds3Object("ROOT", "", tuple(stack[0][3]), tuple(stack[0][4]))

    def close(
        self,
        stack: list[tuple[str, str, int, list[Pds3Item], list[Pds3Object]]],
        keyword: str,
        name: str | None,
        start: int,
    ) -> None:
        kind = _PDS3_BLOCK_END[keyword]
        top_kind, top_name, _, items, children = stack[-1]
        if top_kind == "ROOT":
            raise self.fail(f"{keyword} without an open {kind}", start)
        if top_kind != kind or (name is not None and name != top_name):
            raise self.fail(
                f"{keyword} {name or ''} does not close the open {top_kind} {top_name}", start
            )
        stack.pop()
        stack[-1][4].append(Pds3Object(top_kind, top_name, tuple(items), tuple(children)))

    def expect_statement_end(self) -> None:
        self.skip(newlines=False)
        if self.pos < len(self.text) and self.text[self.pos] != "\n":
            raise self.fail(f"unexpected text after the statement: {self.rest_of_line()!r}")

    def rest_of_line(self) -> str:
        end = self.text.find("\n", self.pos)
        return self.text[self.pos : len(self.text) if end < 0 else end].strip()

    def parse_item(self, key: str) -> Pds3Item:
        """Parse the value after ``=`` and the end of its statement."""
        self.skip()
        start = self.pos
        if start >= len(self.text):
            raise self.fail(f"{key} has no value", start)
        value = self.parse_value()
        raw = self.text[start : self.pos].strip()
        self.expect_statement_end()
        return Pds3Item(key=key, value=value, raw=raw)

    def parse_value(self) -> Pds3Value:
        self.skip()
        if self.pos >= len(self.text):
            raise self.fail("a value is missing", len(self.text))
        char = self.text[self.pos]
        if char in "\"'":
            return self.parse_string(char)
        if char in "({":
            return self.parse_sequence(char)
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] not in _PDS3_DELIMITERS:
            self.pos += 1
        token = self.text[start : self.pos]
        if not token:
            raise self.fail(f"a value is missing before {self.text[start]!r}", start)
        scalar = self.scalar(token, start)
        probe = self.pos
        while probe < len(self.text) and self.text[probe] in " \t":
            probe += 1
        if probe < len(self.text) and self.text[probe] == "<":
            if isinstance(scalar, str):
                raise self.fail(f"a unit follows {token!r}, which is not a number", start)
            end = self.text.find(">", probe)
            newline = self.text.find("\n", probe)
            if end < 0 or 0 <= newline < end:
                raise self.fail("unterminated unit", probe)
            unit = self.text[probe + 1 : end].strip()
            if not unit:
                raise self.fail("empty unit", probe)
            self.pos = end + 1
            return Pds3Quantity(scalar, unit)
        return scalar

    def scalar(self, token: str, start: int) -> Pds3Scalar:
        based = _PDS3_BASED.fullmatch(token)
        if based is not None:
            radix = int(based.group(1))
            try:
                if not 2 <= radix <= 16:
                    raise ValueError(radix)
                return int(based.group(2), radix)
            except ValueError:
                raise self.fail(f"{token!r} is not a valid based integer", start) from None
        if "#" in token:
            raise self.fail(f"{token!r} is not a valid based integer", start)
        if _PDS3_INT.fullmatch(token):
            return int(token)
        if _PDS3_REAL.fullmatch(token):
            return float(token)
        return token

    def parse_string(self, quote: str) -> str:
        start = self.pos
        end = self.text.find(quote, start + 1)
        if end < 0:
            raise self.fail("unterminated string", start)
        self.pos = end + 1
        return self.text[start + 1 : end]

    def parse_sequence(self, opener: str) -> tuple[Pds3Value, ...]:
        start = self.pos
        closer, name = (")", "sequence") if opener == "(" else ("}", "set")
        self.pos += 1
        elements: list[Pds3Value] = []
        expecting_value = False
        while True:
            self.skip()
            if self.pos >= len(self.text):
                raise self.fail(f"unterminated {name}", start)
            if self.text[self.pos] == closer:
                if expecting_value:
                    raise self.fail(f"a value is missing at the end of the {name}", start)
                self.pos += 1
                return tuple(elements)
            elements.append(self.parse_value())
            self.skip()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1
                expecting_value = True
            elif self.pos < len(self.text) and self.text[self.pos] == closer:
                expecting_value = False
            else:
                raise self.fail(
                    f"malformed or unterminated {name}: expected ',' or {closer!r}", start
                )


def parse_pds3_label(text: str) -> Pds3Object:
    """Parse a PDS3 label into its ROOT object; parsing stops at the ``END`` line.

    CRLF and LF line endings are both accepted; ``\\r\\n`` becomes ``\\n`` in string values and
    in ``raw``. ValueError names the line of anything it cannot parse.
    """
    return _Pds3Parser(text).parse()


# --- PDS3 image layout --------------------------------------------------------------------------


@dataclass(frozen=True)
class Pds3ImageLayout:
    """Where the pixels of a detached PDS3 image are and how they are stored.

    ``special_values`` decode the label's CORE_* bit patterns in the image's own dtype, and
    ``special_raw`` keeps the text as written. ``scaling_factor`` and ``value_offset`` are reported
    and never applied. ``file_name`` is the image file named by ``^IMAGE``.
    """

    file_name: str
    offset_bytes: int
    lines: int
    line_samples: int
    bands: int
    sample_type: str
    sample_bits: int
    dtype: np.dtype[Any]
    line_bytes: int
    image_bytes: int
    record_bytes: int | None
    scaling_factor: float
    value_offset: float
    special_values: dict[str, float]
    special_raw: dict[str, str]


# PDS3 Standards Reference, appendix C (Internal Representation of Data Types): the names (with
# their aliases) and bit widths of the types this reader decodes. VAX_REAL, VAXG_REAL, complex and
# the other types are refused.
_PDS3_SAMPLE_TYPES: Final[dict[str, tuple[str, str, tuple[int, ...]]]] = {
    **dict.fromkeys(("PC_REAL",), ("<", "f", (32, 64))),
    **dict.fromkeys(("IEEE_REAL", "FLOAT", "REAL", "MAC_REAL", "SUN_REAL"), (">", "f", (32, 64))),
    **dict.fromkeys(("LSB_INTEGER", "PC_INTEGER", "VAX_INTEGER"), ("<", "i", (8, 16, 32))),
    **dict.fromkeys(
        ("MSB_INTEGER", "INTEGER", "MAC_INTEGER", "SUN_INTEGER"), (">", "i", (8, 16, 32))
    ),
    **dict.fromkeys(
        ("LSB_UNSIGNED_INTEGER", "PC_UNSIGNED_INTEGER", "VAX_UNSIGNED_INTEGER"),
        ("<", "u", (8, 16, 32)),
    ),
    **dict.fromkeys(
        (
            "MSB_UNSIGNED_INTEGER",
            "UNSIGNED_INTEGER",
            "MAC_UNSIGNED_INTEGER",
            "SUN_UNSIGNED_INTEGER",
        ),
        (">", "u", (8, 16, 32)),
    ),
}
_PDS3_SPECIAL_KEYS: Final = (
    "CORE_NULL",
    "CORE_LOW_REPR_SATURATION",
    "CORE_LOW_INSTR_SATURATION",
    "CORE_HIGH_REPR_SATURATION",
    "CORE_HIGH_INSTR_SATURATION",
)


def _pds3_number(item: Pds3Item) -> int | float:
    value = item.value
    if isinstance(value, Pds3Quantity):
        value = value.value
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{item.key} must be a number, found {item.raw!r}")
    return value


def _pds3_count(item: Pds3Item) -> int:
    value = item.value
    if isinstance(value, Pds3Quantity):
        value = value.value
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{item.key} must be a positive integer, found {item.raw!r}")
    return value


def _pds3_zero(image: Pds3Object, key: str) -> None:
    item = image.get(key)
    if item is not None and _pds3_number(item) != 0:
        raise ValueError(f"{key} is {item.raw}; line prefix and suffix bytes are not supported")


def _pds3_pointer(root: Pds3Object) -> tuple[str, int]:
    """The file name and byte offset ``^IMAGE`` names; attached labels are refused."""
    item = root.require("^IMAGE")
    value = item.value
    if isinstance(value, str):
        return value, 0
    if isinstance(value, int | Pds3Quantity) and not isinstance(value, bool):
        raise ValueError(f"^IMAGE = {item.raw}: attached labels are not supported")
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        number = value[1]
        if isinstance(number, int) and not isinstance(number, bool) and number >= 1:
            record_bytes = root.require("RECORD_BYTES")
            return value[0], (number - 1) * _pds3_count(record_bytes)
        if (
            isinstance(number, Pds3Quantity)
            and number.unit.upper() == "BYTES"
            and isinstance(number.value, int)
            and number.value >= 1
        ):
            return value[0], number.value - 1
    raise ValueError(f"^IMAGE = {item.raw} is not a supported pointer form")


def _pds3_special(item: Pds3Item, dtype: np.dtype[Any]) -> float:
    """A CORE_* value: a based integer is a bit pattern in the image's width, else as written."""
    value = item.value
    if isinstance(value, Pds3Quantity):
        value = value.value
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{item.key} must be a number, found {item.raw!r}")
    if _PDS3_BASED.fullmatch(item.raw) is None:
        return float(value)
    bits = dtype.itemsize * 8
    if not 0 <= value < 2**bits:
        raise ValueError(f"{item.key} = {item.raw} does not fit the image's {bits} bits")
    native = np.dtype(f"{dtype.kind}{dtype.itemsize}")
    pattern = np.array([value], dtype=np.dtype(f"u{dtype.itemsize}"))
    return float(pattern.view(native)[0])


def pds3_image_layout(label: Pds3Object) -> Pds3ImageLayout:
    """The layout of the detached image a PDS3 label describes; refuses what it cannot decode.

    Refused (ValueError): attached labels, more than one band, line prefix or suffix bytes,
    unknown or VAX_REAL sample types, SAMPLE_BITS the type does not allow, and unreadable
    CORE_* values. A missing BANDS is the standard's default of 1.
    """
    file_name, offset = _pds3_pointer(label)
    image = label.child("IMAGE")
    lines = _pds3_count(image.require("LINES"))
    line_samples = _pds3_count(image.require("LINE_SAMPLES"))
    bands_item = image.get("BANDS")
    bands = 1 if bands_item is None else _pds3_count(bands_item)
    if bands != 1:
        raise ValueError(f"BANDS = {bands}; only single-band images are supported")
    _pds3_zero(image, "LINE_PREFIX_BYTES")
    _pds3_zero(image, "LINE_SUFFIX_BYTES")
    type_item = image.require("SAMPLE_TYPE")
    if not isinstance(type_item.value, str) or type_item.value not in _PDS3_SAMPLE_TYPES:
        raise ValueError(f"SAMPLE_TYPE {type_item.raw} is not supported")
    sample_type = type_item.value
    order, kind, widths = _PDS3_SAMPLE_TYPES[sample_type]
    sample_bits = _pds3_count(image.require("SAMPLE_BITS"))
    if sample_bits not in widths:
        raise ValueError(
            f"SAMPLE_BITS = {sample_bits} is not allowed for SAMPLE_TYPE {sample_type}"
        )
    dtype: np.dtype[Any] = np.dtype(f"{order}{kind}{sample_bits // 8}")
    scaling = image.get("SCALING_FACTOR")
    value_offset = image.get("OFFSET")
    record_item = label.get("RECORD_BYTES")
    special_values: dict[str, float] = {}
    special_raw: dict[str, str] = {}
    for key in _PDS3_SPECIAL_KEYS:
        item = image.get(key)
        if item is not None:
            special_values[key] = _pds3_special(item, dtype)
            special_raw[key] = item.raw
    line_bytes = line_samples * sample_bits // 8
    return Pds3ImageLayout(
        file_name=file_name,
        offset_bytes=offset,
        lines=lines,
        line_samples=line_samples,
        bands=bands,
        sample_type=sample_type,
        sample_bits=sample_bits,
        dtype=dtype,
        line_bytes=line_bytes,
        image_bytes=lines * line_bytes,
        record_bytes=None if record_item is None else _pds3_count(record_item),
        scaling_factor=1.0 if scaling is None else float(_pds3_number(scaling)),
        value_offset=0.0 if value_offset is None else float(_pds3_number(value_offset)),
        special_values=special_values,
        special_raw=special_raw,
    )


def pds3_row_byte_range(layout: Pds3ImageLayout, first_row: int, last_row: int) -> tuple[int, int]:
    """The inclusive byte range of rows ``first_row`` to ``last_row`` (0-based, inclusive)."""
    if not 0 <= first_row <= last_row < layout.lines:
        raise ValueError(
            f"rows {first_row} to {last_row} are not within the image's rows 0 to "
            f"{layout.lines - 1}"
        )
    first = layout.offset_bytes + first_row * layout.line_bytes
    return first, layout.offset_bytes + (last_row + 1) * layout.line_bytes - 1


def decode_pds3_rows(data: bytes, layout: Pds3ImageLayout, row_count: int) -> npt.NDArray[Any]:
    """Whole rows as stored: a ``(row_count, line_samples)`` array with no scaling or masking."""
    if row_count < 1:
        raise ValueError(f"row_count must be at least 1, got {row_count}")
    expected = row_count * layout.line_bytes
    if len(data) != expected:
        raise ValueError(
            f"{row_count} rows of {layout.line_bytes} bytes need {expected} bytes, got {len(data)}"
        )
    array: npt.NDArray[Any] = np.frombuffer(data, dtype=layout.dtype).reshape(
        row_count, layout.line_samples
    )
    return array.copy()


# --- PDS3 polar stereographic projection --------------------------------------------------------

_PDS3_LENGTH_M: Final = {"km": 1000.0, "m": 1.0}
_PDS3_SCALE_M: Final = {
    "km/pix": 1000.0,
    "km/pixel": 1000.0,
    "m/pix": 1.0,
    "m/pixel": 1.0,
    "meter/pixel": 1.0,
    "meters/pixel": 1.0,
}


def _pds3_quantity(item: Pds3Item, units: dict[str, float]) -> float:
    value = item.value
    if not isinstance(value, Pds3Quantity) or value.unit.lower() not in units:
        raise ValueError(f"{item.key} = {item.raw} must have one of the units {sorted(units)}")
    return float(value.value) * units[value.unit.lower()]


def _pds3_text(item: Pds3Item) -> str:
    return item.text().strip().upper() if isinstance(item.value, str) else item.raw


@dataclass(frozen=True)
class Pds3PolarStereographic:
    """The polar stereographic map projection a PDS3 label states, on a sphere.

    ``transform`` maps 0-based pixel edge coordinates (column, row) to map metres:
    ``x = (Sample - S0) * scale`` and ``y = (L0 - Line) * scale`` with 1-based Sample and Line at
    pixel centres, S0 = SAMPLE_PROJECTION_OFFSET and L0 = LINE_PROJECTION_OFFSET.
    """

    center_latitude_deg: float
    center_longitude_deg: float
    radius_m: float
    map_scale_m: float
    map_resolution_ppd: float | None
    line_projection_offset: float
    sample_projection_offset: float
    lines: int
    line_samples: int
    crs: pyproj.CRS
    transform: Affine

    def pixel_window(
        self, left: float, bottom: float, right: float, top: float
    ) -> tuple[int, int, int, int]:
        """The smallest whole-pixel ``(col_off, row_off, width, height)`` covering map bounds.

        The low edge is floored and the high edge ceiled in 0-based edge coordinates. ValueError
        for non-finite or empty bounds and for a window that leaves the image; it never clips.
        """
        if not all(math.isfinite(v) for v in (left, bottom, right, top)):
            raise ValueError("the map bounds must be finite")
        if not (left < right and bottom < top):
            raise ValueError(
                f"empty map bounds: left={left} right={right} bottom={bottom} top={top}"
            )
        inverse = ~self.transform
        u0, v_top = inverse @ (left, top)
        u1, v_bottom = inverse @ (right, bottom)
        col_off, col_end = math.floor(u0), math.ceil(u1)
        row_off, row_end = math.floor(v_top), math.ceil(v_bottom)
        if col_off < 0 or row_off < 0 or col_end > self.line_samples or row_end > self.lines:
            raise ValueError(
                f"the map bounds cover columns {col_off} to {col_end} and rows {row_off} to "
                f"{row_end}, which leave the image of {self.line_samples} x {self.lines} pixels"
            )
        return col_off, row_off, col_end - col_off, row_end - row_off


def pds3_polar_stereographic(label: Pds3Object) -> Pds3PolarStereographic:
    """The polar stereographic projection of a PDS3 label; ValueError for anything else."""
    projection = label.child("IMAGE_MAP_PROJECTION")
    image = label.child("IMAGE")
    kind = _pds3_text(projection.require("MAP_PROJECTION_TYPE"))
    if kind != "POLAR STEREOGRAPHIC":
        raise ValueError(f"MAP_PROJECTION_TYPE {kind!r} is not POLAR STEREOGRAPHIC")
    for key, required in (
        ("POSITIVE_LONGITUDE_DIRECTION", "EAST"),
        ("COORDINATE_SYSTEM_NAME", "PLANETOCENTRIC"),
    ):
        found = _pds3_text(projection.require(key))
        if found != required:
            raise ValueError(f"{key} is {found!r}; only {required} is supported")
    rotation = projection.get("MAP_PROJECTION_ROTATION")
    if rotation is not None and _pds3_number(rotation) != 0:
        raise ValueError(f"MAP_PROJECTION_ROTATION = {rotation.raw}; only 0 is supported")
    radii = {
        key: _pds3_quantity(projection.require(key), _PDS3_LENGTH_M)
        for key in ("A_AXIS_RADIUS", "B_AXIS_RADIUS", "C_AXIS_RADIUS")
    }
    if len(set(radii.values())) != 1:
        raise ValueError(f"the A, B and C axis RADIUS values differ ({radii}); a sphere is needed")
    radius = radii["A_AXIS_RADIUS"]
    scale = _pds3_quantity(projection.require("MAP_SCALE"), _PDS3_SCALE_M)
    if not scale > 0:
        raise ValueError(f"MAP_SCALE must be positive, got {projection.require('MAP_SCALE').raw}")
    latitude = float(_pds3_number(projection.require("CENTER_LATITUDE")))
    if latitude not in (-90.0, 90.0):
        raise ValueError(f"CENTER_LATITUDE = {latitude}; only -90 and 90 are supported")
    longitude = float(_pds3_number(projection.require("CENTER_LONGITUDE")))
    resolution_item = projection.get("MAP_RESOLUTION")
    resolution = (
        None
        if resolution_item is None
        else _pds3_quantity(resolution_item, {"pix/deg": 1.0, "pixel/deg": 1.0})
    )
    line_offset = float(_pds3_number(projection.require("LINE_PROJECTION_OFFSET")))
    sample_offset = float(_pds3_number(projection.require("SAMPLE_PROJECTION_OFFSET")))
    crs = pyproj.CRS.from_dict(
        {
            "proj": "stere",
            "lat_0": latitude,
            "lat_ts": latitude,
            "lon_0": longitude,
            "x_0": 0,
            "y_0": 0,
            "R": radius,
            "units": "m",
        }
    )
    return Pds3PolarStereographic(
        center_latitude_deg=latitude,
        center_longitude_deg=longitude,
        radius_m=radius,
        map_scale_m=scale,
        map_resolution_ppd=resolution,
        line_projection_offset=line_offset,
        sample_projection_offset=sample_offset,
        lines=_pds3_count(image.require("LINES")),
        line_samples=_pds3_count(image.require("LINE_SAMPLES")),
        crs=crs,
        transform=Affine(
            scale, 0.0, (0.5 - sample_offset) * scale, 0.0, -scale, (line_offset - 0.5) * scale
        ),
    )


# --- PDS3 row range reads -----------------------------------------------------------------------


def fetch_pds3_rows(
    url: str,
    layout: Pds3ImageLayout,
    first_row: int,
    last_row: int,
    *,
    fetch: RangeFetcher = fetch_range,
) -> RangeResponse:
    """Fetch whole rows ``first_row`` to ``last_row`` (inclusive) with one range request.

    The response must state the file size, and it must equal the size the label's layout implies
    (``offset_bytes + image_bytes``); it must also hold exactly the requested bytes. Otherwise
    RangeReadError names the URL. Errors from ``fetch`` propagate unchanged.
    """
    _check_public_http_url(url, "fetch_pds3_rows")
    first, last = pds3_row_byte_range(layout, first_row, last_row)
    response = fetch(url, first, last)
    expected_size = layout.offset_bytes + layout.image_bytes
    if response.total_size is None or response.total_size != expected_size:
        raise RangeReadError(
            f"{url}: the file size the server states ({response.total_size}) differs from the "
            f"label's layout ({expected_size} bytes)"
        )
    if (
        response.first_byte != first
        or response.last_byte != last
        or len(response.data) != last - first + 1
    ):
        raise RangeReadError(
            f"{url}: the response holds bytes {response.first_byte}-{response.last_byte} "
            f"({len(response.data)} received), not the requested {first}-{last}"
        )
    return response
