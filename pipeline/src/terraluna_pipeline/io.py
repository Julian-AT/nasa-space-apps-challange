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
"""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Required, TypedDict

import numpy as np
import numpy.typing as npt
import rasterio  # type: ignore[import-untyped]
from affine import Affine
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


def read_geotiff_window(path: Path, window: Window | None = None, *, band: int = 1) -> RasterWindow:
    """Read ``band`` (1-based) of ``window``, or of the whole raster when ``window`` is None.

    Raises ValueError for a band the file does not have, and for a window that is empty, has
    fractional offsets or sizes, or does not lie wholly inside the raster.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with rasterio.open(path) as src:
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


def _band_count(path: Path) -> int:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(path) as src:
            return int(src.count)


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
    bands = tuple(
        read_geotiff_window(path, window, band=band) for band in range(1, _band_count(path) + 1)
    )
    fields: dict[str, Any] = {name: metadata.get(name, UNKNOWN) for name in _UNKNOWN_BY_DEFAULT}
    fields.update(
        product_id=product_id,
        source_url=metadata.get("source_url"),
        local_path=local_path,
        checksum=sha256_file(path),
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
    ``final_url`` differs from ``url`` after a redirect.
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
    opener = urllib.request.build_opener(_PublicRedirectHandler())
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
    opener = urllib.request.build_opener(_PublicRedirectHandler(DownloadError))
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
