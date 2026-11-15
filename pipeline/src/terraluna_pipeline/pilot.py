"""The data pilot runner (PRD 5 pilot procedure, 6.1, 8.12, 9 G0): fetch, record and check.

``python -m terraluna_pipeline.pilot {fetch|record|check} <source>`` runs from the repository
root. Network and computation are separate:

- ``fetch`` is the only command that uses the network. It writes only to ``data/raw/<source>/``
  (git-ignored): the provider's files and pages, plus ``fetch.json`` with the retrieval time,
  URLs, HTTP status and byte counts. A file at or over the 200 MB whole-file limit is never
  downloaded: a GeoTIFF is read by window through HTTP range requests, and only the byte
  ranges read are stored (``<file>.ranges``), so ``record`` can decode the window offline.
  Files are staged in a hidden directory there and moved into place only when every download
  succeeded, so a failed fetch leaves an earlier one intact.
  ``fetch.json`` is written last; without it the raw data counts as missing.
- ``record`` reads only ``data/raw/<source>/`` and writes the committed outputs: its
  ``outputs/pilot/`` files and its ProductRecords in ``outputs/sources.json``. It reads no
  clock (retrieval times are copied from ``fetch.json``), so re-running it reproduces the
  outputs byte for byte.
- ``check`` recomputes the outputs in memory, writes nothing and fails if the committed files
  differ, or if the raw data is missing.

Every recorded number comes from a decoded file. A value a file or the provider's
documentation does not state is ``"unknown"`` or null, with the reason in ``field_notes``. This
module records observations only: the shipping mode and the G0 status are a human checkpoint
(PRD 23.6) and are never written here.
"""

from __future__ import annotations

import argparse
import difflib
import functools
import hashlib
import importlib.metadata
import io as stdio
import itertools
import json
import math
import os
import platform
import re
import sys
import tarfile
import tempfile
import urllib.parse
import warnings
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from pathlib import Path
from typing import Any, Final, Literal, Protocol

import numpy as np
import numpy.typing as npt
import pyproj
import rasterio  # type: ignore[import-untyped]
import shapefile  # type: ignore[import-untyped]
from affine import Affine
from PIL import Image, ImageDraw
from pydantic import TypeAdapter
from rasterio.crs import CRS  # type: ignore[import-untyped]
from rasterio.windows import Window  # type: ignore[import-untyped]

from terraluna_pipeline.export import canonical_json
from terraluna_pipeline.io import (
    HTTP_EXCHANGE_BODY_LIMIT_BYTES,
    RANGE_BLOCK_BYTES,
    WHOLE_FILE_LIMIT_BYTES,
    ByteRanges,
    DownloadError,
    FetchedFile,
    FileTooLargeError,
    GeoTiffHeader,
    Pds3ImageLayout,
    Pds3Item,
    Pds3Object,
    Pds3PolarStereographic,
    Pds3Quantity,
    ProductMetadata,
    RangeExchange,
    RangeFetcher,
    RangeNotSupportedError,
    RangeReadError,
    RangeResponse,
    RasterWindow,
    RemoteWindow,
    decode_pds3_rows,
    fetch_file,
    fetch_pds3_rows,
    fetch_range,
    fetch_stac_search,
    parse_pds3_label,
    parse_stac_item_collection,
    pds3_image_layout,
    pds3_polar_stereographic,
    pds3_row_byte_range,
    read_cog_layout,
    read_geotiff_header,
    read_geotiff_tags,
    read_product,
    read_remote_cog_window,
    read_remote_geotiff_window,
    send_http_exchange,
    sha256_file,
    stac_search_url,
)
from terraluna_pipeline.quality import (
    ISIS_NULL_REAL4,
    ISIS_SATURATION_REAL4,
    InvalidReason,
    ValidityMask,
    valid_fraction,
    validity_mask,
)
from terraluna_pipeline.schema import UNKNOWN, ProductRecord, SourcesFile, has_url_credentials

RAW_DIR: Final = Path("data/raw")
SOURCES_PATH: Final = "outputs/sources.json"
FETCH_METADATA: Final = "fetch.json"
COMMAND: Final = "python -m terraluna_pipeline.pilot"
RANGE_WINDOW_PIXELS: Final = 1024
"""The side, in pixels, of the centred window read by range from a file over the whole-file limit.

At PGDA78's 5 m spacing this is 5.12 km, about the 5 km window of PRD 6.1.
"""


class PilotError(RuntimeError):
    """The pilot cannot produce a faithful record from what it has."""


class MissingRawDataError(PilotError):
    """``data/raw/<source>/`` holds no complete fetch."""


class Downloader(Protocol):
    """The download layer: ``io.fetch_file`` in production, a mock in tests."""

    def __call__(
        self, url: str, dest: Path, *, max_bytes: int, magic: Sequence[bytes] = ...
    ) -> FetchedFile: ...


Clock = Callable[[], datetime]


@dataclass(frozen=True)
class Build:
    """What ``record`` writes for one source.

    ``files`` maps repository-relative paths to their bytes. ``records`` replace, in
    outputs/sources.json, every record whose product_id starts with ``record_prefix``.
    """

    files: Mapping[str, bytes]
    records: tuple[ProductRecord, ...]
    record_prefix: str


class Source(Protocol):
    """One pilot source: a network fetch into its raw directory and an offline build.

    ``download`` fetches a whole file below the whole-file limit (``io.fetch_file``) and
    ``read_range`` sends one HTTP range request (``io.fetch_range``), for windowed reads.
    """

    @property
    def name(self) -> str: ...

    def fetch(
        self, raw_dir: Path, download: Downloader, read_range: RangeFetcher, now: Clock
    ) -> None: ...

    def build(self, raw_dir: Path, root: Path) -> Build: ...


def utc_now() -> datetime:
    """The wall clock, used by ``fetch`` only."""
    return datetime.now(UTC)


def utc_timestamp(moment: datetime) -> str:
    """A UTC timestamp to the second, such as ``2026-11-14T19:30:05Z``."""
    if moment.tzinfo is None:
        raise ValueError("the clock must return a timezone-aware datetime")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Shared helpers -----------------------------------------------------------------------------

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _safe_file_name(name: str, where: str) -> str:
    if _SAFE_NAME.fullmatch(name) is None:
        raise PilotError(f"{where} names a file {name!r} that is not a plain file name")
    return name


def _json_number(value: float | None) -> float | str | None:
    """A float for JSON: NaN and infinities become the strings "NaN", "Infinity", "-Infinity"."""
    if value is None:
        return None
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return float(value)


def _write_atomically(path: Path, data: bytes) -> None:
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as out:
            out.write(data)
        temp.chmod(0o644)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _fetch_entry(fetched: FetchedFile, retrieved_at: datetime, **extra: Any) -> dict[str, Any]:
    return {
        **extra,
        "file_name": fetched.path.name,
        "url": fetched.url,
        "final_url": fetched.final_url,
        "http_status": fetched.status,
        "content_type": fetched.content_type,
        "content_length": fetched.content_length,
        "bytes_received": fetched.bytes_received,
        "last_modified": fetched.last_modified,
        "etag": fetched.etag,
        "sha256": fetched.sha256,
        "retrieved_at": utc_timestamp(retrieved_at),
    }


_RANGES_SUFFIX: Final = ".ranges"
"""The local file holding the byte ranges of a file read by window, named after that file."""


def _window_entry(
    remote: RemoteWindow, local_name: str, retrieved_at: datetime, **extra: Any
) -> dict[str, Any]:
    """The fetch.json entry of a GeoTIFF read by window: its byte ranges are in ``local_name``."""
    col_off, row_off, width, height = remote.window
    return {
        **extra,
        "read": "window",
        "file_name": local_name,
        "url": remote.url,
        "final_url": remote.final_url,
        "http_status": remote.status,
        "content_type": remote.content_type,
        "remote_bytes": remote.ranges.size,
        "bytes_received": remote.bytes_received,
        "range_requests": remote.requests,
        "ranges": [[first, last] for first, last in remote.ranges.ranges],
        "window": {"col_off": col_off, "row_off": row_off, "width": width, "height": height},
        "raster_width": remote.raster_width,
        "raster_height": remote.raster_height,
        "last_modified": remote.last_modified,
        "etag": remote.etag,
        "sha256": hashlib.sha256(remote.ranges.data).hexdigest(),
        "retrieved_at": utc_timestamp(retrieved_at),
    }


def _is_window_read(entry: Mapping[str, Any]) -> bool:
    """Whether a fetch.json file entry was read by window; a whole download has no ``read``."""
    read = entry.get("read")
    if read not in (None, "window"):
        raise PilotError(f"fetch.json lists {entry.get('file_name')!r} with read {read!r}")
    return read == "window"


def _provider_name(entry: Mapping[str, Any]) -> str:
    """The file name on the provider's server (a window read stores it under another name)."""
    name = entry["provider_file_name"] if _is_window_read(entry) else entry["file_name"]
    return _safe_file_name(str(name), FETCH_METADATA)


def _load_fetch(raw_dir: Path, name: str) -> dict[str, Any]:
    """Read ``fetch.json`` and confirm every file it lists is present and unchanged."""
    path = raw_dir / FETCH_METADATA
    try:
        metadata = json.loads(path.read_bytes())
    except (OSError, ValueError) as error:
        raise PilotError(f"{path} cannot be read ({error}); run: {COMMAND} fetch {name}") from error
    lists = (
        [metadata.get(key) for key in ("documents", "files")] if isinstance(metadata, dict) else []
    )
    entries = [entry for part in lists if isinstance(part, list) for entry in part]
    if (
        len(lists) != 2
        or not all(isinstance(part, list) for part in lists)
        or not all(
            isinstance(entry, dict) and {"file_name", "bytes_received", "sha256"} <= entry.keys()
            for entry in entries
        )
    ):
        raise PilotError(f"{path} is not a fetch record; run: {COMMAND} fetch {name}")
    for entry in entries:
        file_name = _safe_file_name(str(entry["file_name"]), str(path))
        local = raw_dir / file_name
        if not local.is_file():
            raise PilotError(
                f"{local} is listed in fetch.json but missing; run: {COMMAND} fetch {name}"
            )
        size = local.stat().st_size
        if size != entry["bytes_received"] or sha256_file(local) != entry["sha256"]:
            raise PilotError(
                f"{local} does not match the size and sha256 in fetch.json; "
                f"run: {COMMAND} fetch {name}"
            )
    result: dict[str, Any] = metadata
    return result


def tool_versions() -> dict[str, str]:
    """The versions of the tools that decode and render the pilot data."""
    versions = {
        package: importlib.metadata.version(package)
        for package in ("numpy", "pillow", "pydantic", "pyshp", "rasterio", "terraluna-pipeline")
    }
    versions["gdal"] = str(rasterio.__gdal_version__)
    versions["python"] = platform.python_version()
    return versions


# --- Sources.json -------------------------------------------------------------------------------

_SOURCES = TypeAdapter(SourcesFile)


def upsert_sources(existing: bytes | None, records: Sequence[ProductRecord], prefix: str) -> bytes:
    """The sources.json bytes after replacing this source's records, sorted by product_id.

    Records of other sources are kept as they are. Every record of this source (product_id
    starting with ``prefix``) is replaced, so one the source no longer produces is removed.
    """
    ids = [record.product_id for record in records]
    if len(set(ids)) != len(ids):
        raise PilotError(f"duplicate product_id among the new records: {sorted(ids)}")
    if not all(product_id.startswith(prefix) for product_id in ids):
        raise PilotError(f"every new product_id must start with {prefix!r}")
    current: list[ProductRecord] = []
    if existing is not None:
        try:
            current = _SOURCES.validate_json(existing)
        except ValueError as error:
            raise PilotError(
                f"{SOURCES_PATH} is not a valid list of ProductRecords: {error}"
            ) from error
        current_ids = [record.product_id for record in current]
        if len(set(current_ids)) != len(current_ids):
            raise PilotError(f"{SOURCES_PATH} lists a product_id more than once")
    kept = [record for record in current if not record.product_id.startswith(prefix)]
    merged = sorted([*kept, *records], key=lambda record: record.product_id)
    return canonical_json([record.model_dump(mode="json") for record in merged])


# --- Commands -----------------------------------------------------------------------------------


def _source(name: str, sources: Mapping[str, Source] | None) -> Source:
    registry = SOURCES if sources is None else sources
    if name not in registry:
        raise PilotError(f"unknown source {name!r}; known: {', '.join(sorted(registry))}")
    return registry[name]


def raw_directory(source: Source) -> str:
    """The directory under data/raw/ that holds a source's fetch.

    A source may name it with a ``raw_directory`` attribute (wc: ``worldcover``); otherwise it
    is the source's name.
    """
    return _safe_file_name(str(getattr(source, "raw_directory", source.name)), "a pilot source")


def _raw_dir(root: Path, name: str) -> Path:
    return root / RAW_DIR / name


def _require_raw(root: Path, source: Source) -> Path:
    directory = raw_directory(source)
    raw_dir = _raw_dir(root, directory)
    if not (raw_dir / FETCH_METADATA).is_file():
        raise MissingRawDataError(
            f"data/raw/{directory}/ holds no complete fetch; run: {COMMAND} fetch "
            f"{source.name} first"
        )
    return raw_dir


def fetch(
    name: str,
    *,
    root: Path | None = None,
    sources: Mapping[str, Source] | None = None,
    download: Downloader | None = None,
    read_range: RangeFetcher | None = None,
    now: Clock | None = None,
) -> None:
    """Download ``name``'s files into its data/raw/ directory; the only networked command."""
    source = _source(name, sources)
    raw_dir = _raw_dir(Path.cwd() if root is None else root, raw_directory(source))
    source.fetch(
        raw_dir,
        fetch_file if download is None else download,
        fetch_range if read_range is None else read_range,
        now or utc_now,
    )


_URL_IN_TEXT: Final = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[!#$%&'()*+,./0-9:;=?@A-Z\[\]_a-z~-]+")
"""A URL inside any text: a scheme, ``://`` and the RFC 3986 characters that follow."""


def _credential_url_paths(value: Any, path: str = "$") -> Iterator[str]:
    """The JSON paths in ``value`` of keys or strings holding a URL with credentials or a signature.

    Every URL found in the text is checked with ``schema.has_url_credentials``, so a URL quoted
    inside a sentence counts as much as a URL field.
    """
    if isinstance(value, str):
        if any(has_url_credentials(url) for url in _URL_IN_TEXT.findall(value)):
            yield path
    elif isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if isinstance(key, str) and next(_credential_url_paths(key, child), None):
                yield child
            yield from _credential_url_paths(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _credential_url_paths(item, f"{path}[{index}]")


def _refuse_credential_urls(files: Mapping[str, bytes]) -> None:
    """Refuse outputs that would commit a URL with credentials or a signature (PRD 8.12).

    Provider documents state URLs that fetch never requests (asset hrefs, provider and citation
    links), so the URL guard on requests does not cover them; every JSON output is checked here.
    The URL itself is not repeated, so a secret never reaches a log.
    """
    for relative, data in sorted(files.items()):
        if not relative.endswith(".json"):
            continue
        found = next(_credential_url_paths(json.loads(data)), None)
        if found is not None:
            raise PilotError(
                f"{relative} would hold a URL with credentials or a signature at {found}; "
                "nothing is written, because records keep public URLs only"
            )


def expected_outputs(
    name: str, *, root: Path | None = None, sources: Mapping[str, Source] | None = None
) -> dict[str, bytes]:
    """The committed outputs recomputed from data/raw/<name>/, without writing anything.

    Raises PilotError if a JSON output would hold a URL with credentials or a signature.
    """
    base = Path.cwd() if root is None else root
    source = _source(name, sources)
    build = source.build(_require_raw(base, source), base)
    files = dict(build.files)
    sources_path = base / SOURCES_PATH
    existing = sources_path.read_bytes() if sources_path.is_file() else None
    files[SOURCES_PATH] = upsert_sources(existing, build.records, build.record_prefix)
    _refuse_credential_urls(files)
    return files


def record(
    name: str, *, root: Path | None = None, sources: Mapping[str, Source] | None = None
) -> list[Path]:
    """Write ``name``'s outputs from data/raw/<name>/; returns the paths whose bytes changed."""
    base = Path.cwd() if root is None else root
    changed: list[Path] = []
    for relative, data in sorted(expected_outputs(name, root=base, sources=sources).items()):
        path = base / relative
        if path.is_file() and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(path, data)
        changed.append(path)
    return changed


def _describe_difference(relative: str, committed: bytes, expected: bytes) -> str:
    try:
        old, new = committed.decode("utf-8"), expected.decode("utf-8")
    except UnicodeDecodeError:
        return (
            f"{relative}: differs (committed sha256 {hashlib.sha256(committed).hexdigest()}, "
            f"recomputed {hashlib.sha256(expected).hexdigest()})"
        )
    diff = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            f"{relative} (committed)",
            f"{relative} (recomputed)",
            lineterm="",
        )
    )
    shown = "\n".join(diff[:60]) + ("\n..." if len(diff) > 60 else "")
    return f"{relative}: differs\n{shown}"


def check(
    name: str, *, root: Path | None = None, sources: Mapping[str, Source] | None = None
) -> list[str]:
    """Differences between the committed outputs and a recomputation; empty when identical."""
    base = Path.cwd() if root is None else root
    differences: list[str] = []
    for relative, data in sorted(expected_outputs(name, root=base, sources=sources).items()):
        path = base / relative
        if not path.is_file():
            differences.append(f"{relative}: missing; run: {COMMAND} record {name}")
            continue
        committed = path.read_bytes()
        if committed != data:
            differences.append(_describe_difference(relative, committed, data))
    return differences


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path | None = None,
    sources: Mapping[str, Source] | None = None,
    download: Downloader | None = None,
    read_range: RangeFetcher | None = None,
    now: Clock | None = None,
) -> int:
    registry = SOURCES if sources is None else sources
    parser = argparse.ArgumentParser(
        prog=COMMAND,
        description=(
            "Data pilot (PRD 5, 9 G0). fetch downloads into data/raw/<source>/ and is the only "
            "command that uses the network; record writes the committed outputs from data/raw; "
            "check recomputes them in memory and fails on any difference."
        ),
    )
    parser.add_argument("command", choices=("fetch", "record", "check"))
    parser.add_argument("source", choices=sorted(registry))
    args = parser.parse_args(argv)
    command, name = str(args.command), str(args.source)
    raw = f"data/raw/{raw_directory(registry[name])}/"
    try:
        if command == "fetch":
            fetch(
                name,
                root=root,
                sources=registry,
                download=download,
                read_range=read_range,
                now=now,
            )
            print(f"{name}: fetched into {raw}")
        elif command == "record":
            changed = record(name, root=root, sources=registry)
            base = Path.cwd() if root is None else root
            for path in changed:
                print(f"wrote {path.relative_to(base).as_posix()}")
            print(f"{name}: outputs recorded from {raw} ({len(changed)} changed)")
        else:
            differences = check(name, root=root, sources=registry)
            if differences:
                for difference in differences:
                    print(difference, file=sys.stderr)
                print(
                    f"{name}: committed outputs differ from {raw}; run: {COMMAND} record {name}",
                    file=sys.stderr,
                )
                return 1
            print(f"{name}: committed outputs match {raw}")
    except (PilotError, DownloadError, RangeReadError, OSError, ValueError) as error:
        print(f"{command} {name}: {error}", file=sys.stderr)
        return 1
    return 0


# --- pgda78: PGDA product 78, LOLA 5 m DEMs for south-pole landing sites ------------------------


@dataclass(frozen=True)
class _Role:
    """One Site01 product, identified by the file-name suffix the README defines."""

    key: str
    suffix: str
    units_phrase: str
    """The words in the README's description of the suffix that state the units."""
    units: str
    """The units recorded when ``units_phrase`` is present, in the README's wording."""


_GEOTIFF_ROLES: Final = (
    _Role("height", "_surf.tif", "(meters)", "meters"),
    _Role(
        "lola_return_count",
        "_ldec.tif",
        "number of LOLA returns in each pixel",
        "number of LOLA returns in each pixel",
    ),
    _Role("slope", "_slp.tif", "(degrees)", "degrees"),
    _Role("height_rms_error", "_toterr.tif", "in meters", "meters"),
    _Role("slope_rms_error", "_slperr.tif", "in degrees", "degrees"),
)
_ROI_SUFFIX: Final = "_ROIs.tgz"
_TIFF_MAGIC: Final = (b"II*\x00", b"MM\x00*")
_GZIP_MAGIC: Final = (b"\x1f\x8b",)

_README_ENTRY = re.compile(
    r"^\*(?P<suffix>[_.][A-Za-z0-9_.]+)\s*[:-]\s*(?P<text>\S.*?)\s*$", re.MULTILINE
)
_FRAME = re.compile(r"MOON_ME reference frame of ephemeris [A-Z]+[0-9]+")
_HREF = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)
_CITE = re.compile(r"cite the following reference", re.IGNORECASE)
_DOI = re.compile(r"10\.[0-9]{4,9}/[A-Za-z0-9._;()/:-]+[A-Za-z0-9]")

GREY_LEVELS: Final = 254
"""Palette indices 0 to 253 are a linear grey ramp from the lowest to the highest valid height."""
INVALID_INDEX: Final = 254
ROI_INDEX: Final = 255
INVALID_RGB: Final = (0, 0, 160)
ROI_RGB: Final = (255, 64, 0)
PREVIEW_MAX_SIDE: Final = 640
PREVIEW_MAX_BYTES: Final = 200_000
_ROI_ARCHIVE_MAX_BYTES: Final = 50_000_000


def grey_index(values: npt.NDArray[Any], low: float, high: float) -> npt.NDArray[np.uint8]:
    """Map heights linearly onto the grey levels: ``low`` -> 0 and ``high`` -> GREY_LEVELS - 1.

    Rounds half to even. Values outside [low, high] are clipped, a non-finite value maps to 0
    (callers paint invalid pixels separately), and a flat range maps everything to 0.
    """
    data = np.asarray(values, dtype=np.float64)
    if not high > low:
        return np.zeros(data.shape, dtype=np.uint8)
    finite = np.where(np.isfinite(data), data, low)
    scaled = np.rint((finite - low) / (high - low) * (GREY_LEVELS - 1))
    return np.clip(scaled, 0, GREY_LEVELS - 1).astype(np.uint8)


def _palette() -> list[int]:
    palette: list[int] = []
    for level in range(GREY_LEVELS):
        grey = round(level * 255 / (GREY_LEVELS - 1))
        palette += [grey, grey, grey]
    return palette + list(INVALID_RGB) + list(ROI_RGB)


def _parse_readme(text: str) -> dict[str, str]:
    """The README's file table: suffix (such as ``_surf.tif``) -> description."""
    return {match["suffix"]: match["text"] for match in _README_ENTRY.finditer(text)}


def _decode(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise PilotError(f"{path} is not UTF-8 text: {error}") from error


def _wkt_spheroid(wkt: str) -> dict[str, Any]:
    match = re.search(r'SPHEROID\["([^"]*)",\s*([-+0-9.eE]+),\s*([-+0-9.eE]+)', wkt)
    if match is None:
        return {"name": None, "semi_major_axis_m": None, "inverse_flattening": None}
    return {
        "name": match.group(1),
        "semi_major_axis_m": float(match.group(2)),
        "inverse_flattening": float(match.group(3)),
    }


def _wkt_datum(wkt: str) -> str | None:
    match = re.search(r'DATUM\["([^"]*)"', wkt)
    return None if match is None else match.group(1)


@dataclass(frozen=True)
class _Decoded:
    band: RasterWindow
    record: ProductRecord
    mask: ValidityMask
    tags: dict[str, str]


def _grid(band: RasterWindow, path: Path) -> tuple[Affine, dict[str, float], dict[str, float]]:
    transform = band.raster_transform
    if transform is None:
        raise PilotError(f"{path} has no geotransform; its extent cannot be read from the file")
    if transform.b != 0 or transform.d != 0 or transform.a <= 0 or transform.e >= 0:
        raise PilotError(f"{path} has a rotated or flipped grid {tuple(transform)[:6]}")
    pixel = {"x": float(transform.a), "y": float(-transform.e)}
    extent = {
        "left": float(transform.c),
        "top": float(transform.f),
        "right": float(transform.c + transform.a * band.raster_width),
        "bottom": float(transform.f + transform.e * band.raster_height),
    }
    return transform, pixel, extent


def _same_grid(first: RasterWindow, second: RasterWindow) -> bool:
    return (
        first.raster_width == second.raster_width
        and first.raster_height == second.raster_height
        and first.raster_transform == second.raster_transform
        and first.crs == second.crs
    )


def _shape_record(values: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, float):
            result[key] = _json_number(value)
        elif isinstance(value, (int, str, bool)) or value is None:
            result[key] = value
        else:
            result[key] = str(value)
    return result


@dataclass(frozen=True)
class _RoiLayer:
    summary: dict[str, Any]
    rings: list[list[tuple[float, float]]]
    drawable: bool


@dataclass(frozen=True)
class _RoiArchive:
    members: list[dict[str, Any]]
    layers: list[_RoiLayer]
    error: str | None
    """Why the archive could not be read, or None when it was read."""


_POLYGON_TYPES: Final = frozenset({"POLYGON", "POLYGONZ", "POLYGONM"})


def _read_roi_archive(
    path: Path, height_crs: str | None, extent: Mapping[str, float]
) -> _RoiArchive:
    """List the archive's members and read every shapefile in it, without extracting to disk.

    An archive that cannot be read is reported in ``error`` instead of stopping the record, so
    the record can say why the outlines are missing (T1-03 AC3).
    """
    members: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive.getmembers():
                kind = (
                    "file"
                    if member.isfile()
                    else "symlink"
                    if member.issym()
                    else "hardlink"
                    if member.islnk()
                    else "directory"
                    if member.isdir()
                    else "other"
                )
                members.append(
                    {
                        "name": member.name,
                        "type": kind,
                        "size": int(member.size),
                        "link_target": member.linkname or None,
                    }
                )
                if member.isfile():
                    total += member.size
                    if total > _ROI_ARCHIVE_MAX_BYTES:
                        return _RoiArchive(
                            [],
                            [],
                            f"{path.name} unpacks to more than {_ROI_ARCHIVE_MAX_BYTES} bytes, "
                            "so it was not read",
                        )
                    stream = archive.extractfile(member)
                    if stream is not None:
                        contents[member.name] = stream.read()
    except (tarfile.TarError, OSError, EOFError, zlib.error) as error:
        return _RoiArchive([], [], f"{path.name} is not a readable gzip tar archive: {error}")
    members.sort(key=lambda item: str(item["name"]))
    layers: list[_RoiLayer] = []
    for shp_name in sorted(name for name in contents if name.lower().endswith(".shp")):
        stem = shp_name[: -len(".shp")]
        parts = {ext: contents.get(stem + ext) for ext in (".shp", ".shx", ".dbf", ".prj")}
        layers.append(_read_layer(stem, parts, height_crs, extent))
    return _RoiArchive(members, layers, None)


@dataclass(frozen=True)
class _Shapes:
    shape_type: str
    bbox: list[float]
    fields: list[str]
    summaries: list[dict[str, Any]]
    rings: list[list[tuple[float, float]]]
    inside: bool


def _read_shapes(readers: Mapping[str, stdio.BytesIO], extent: Mapping[str, float]) -> _Shapes:
    with shapefile.Reader(**readers) as reader:
        fields = [field.name for field in reader.fields if field.name != "DeletionFlag"]
        shapes = reader.shapes()
        records = reader.records() if "dbf" in readers else [None] * len(shapes)
        layer_type = str(reader.shapeTypeName)
        layer_bbox = [float(value) for value in reader.bbox]
    rings: list[list[tuple[float, float]]] = []
    summaries: list[dict[str, Any]] = []
    inside = True
    for index, (shape, row) in enumerate(zip(shapes, records, strict=True)):
        points = [(float(x), float(y)) for x, y, *_ in shape.points]
        starts = [*shape.parts, len(points)]
        for first, last in itertools.pairwise(starts):
            rings.append(points[first:last])
        inside = inside and all(
            extent["left"] <= x <= extent["right"] and extent["bottom"] <= y <= extent["top"]
            for x, y in points
        )
        summaries.append(
            {
                "index": index,
                "shape_type": str(shape.shapeTypeName),
                "points": len(points),
                "parts": len(shape.parts),
                "bbox_m": [float(value) for value in shape.bbox] if points else None,
                "record": None if row is None else _shape_record(row.as_dict()),
            }
        )
    return _Shapes(layer_type, layer_bbox, fields, summaries, rings, inside)


def _read_layer(
    stem: str,
    parts: Mapping[str, bytes | None],
    height_crs: str | None,
    extent: Mapping[str, float],
) -> _RoiLayer:
    """Read one shapefile; one that cannot be read is summarised with the error, not raised."""
    readers = {
        key.lstrip("."): stdio.BytesIO(data)
        for key, data in parts.items()
        if data is not None and key != ".prj"
    }
    error: str | None = None
    read: _Shapes | None = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            read = _read_shapes(readers, extent)
        # A damaged shapefile can fail inside pyshp in many ways (its own exceptions,
        # struct.error, ValueError, IndexError, ...); each one becomes the recorded reason.
        except Exception as failure:
            error = (
                f"the shapefile {stem}.shp cannot be read with pyshp "
                f"{importlib.metadata.version('pyshp')}: {type(failure).__name__}: {failure}"
            )
    read_warnings = list(dict.fromkeys(str(warning.message) for warning in caught))
    prj_wkt, crs_matches, comparison = _layer_crs(parts.get(".prj"), height_crs)
    summary = {
        "name": stem,
        "components": sorted(ext for ext, data in parts.items() if data is not None),
        "readable": read is not None,
        "read_error": error,
        "read_warnings": read_warnings,
        "shape_type": None if read is None else read.shape_type,
        "shape_count": None if read is None else len(read.summaries),
        "bbox_m": None if read is None else read.bbox,
        "fields": None if read is None else read.fields,
        "prj_wkt": prj_wkt,
        "crs_matches_height": crs_matches,
        "crs_comparison": comparison,
        "inside_height_extent": None if read is None else read.inside,
        "shapes": [] if read is None else read.summaries,
    }
    drawable = (
        read is not None
        and crs_matches is True
        and read.inside
        and read.shape_type in _POLYGON_TYPES
        and bool(read.rings)
    )
    return _RoiLayer(summary=summary, rings=[] if read is None else read.rings, drawable=drawable)


def _layer_crs(prj: bytes | None, height_crs: str | None) -> tuple[str | None, bool | None, str]:
    """The .prj text, whether it matches the height map CRS, and how that was decided."""
    if prj is None:
        return None, *_compare_crs(None, height_crs)
    try:
        prj_wkt = prj.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        return None, None, f"the .prj is not UTF-8 text ({error}), so its CRS cannot be read"
    return prj_wkt, *_compare_crs(prj_wkt, height_crs)


def _compare_crs(prj_wkt: str | None, height_crs: str | None) -> tuple[bool | None, str]:
    if prj_wkt is None:
        return None, "the shapefile has no .prj, so its CRS is not stated"
    if height_crs is None:
        return None, "the height map states no CRS"
    try:
        prj_params = CRS.from_wkt(prj_wkt).to_dict()
        height_params = CRS.from_wkt(height_crs).to_dict()
    except rasterio.errors.CRSError as error:
        return False, f"the .prj cannot be parsed: {error}"
    if prj_params == height_params:
        return True, (
            "the .prj and the height map WKT texts differ in wording but both resolve to the "
            f"same PROJ parameters ({CRS.from_wkt(height_crs).to_proj4()})"
        )
    return False, (
        f"the .prj states {CRS.from_wkt(prj_wkt).to_proj4()} but the height map states "
        f"{CRS.from_wkt(height_crs).to_proj4()}"
    )


def _render_preview(
    height: _Decoded,
    transform: Affine,
    layers: Sequence[_RoiLayer],
    low: float,
    high: float,
) -> tuple[bytes, dict[str, Any]]:
    array = height.band.array
    rows, cols = array.shape
    step = max(1, math.ceil(max(rows, cols) / PREVIEW_MAX_SIDE))
    sampled = array[::step, ::step]
    valid = height.mask.valid[::step, ::step]
    index = np.full(sampled.shape, INVALID_INDEX, dtype=np.uint8)
    index[valid] = grey_index(sampled[valid], low, high)
    image = Image.frombytes("P", (index.shape[1], index.shape[0]), np.ascontiguousarray(index))
    image.putpalette(_palette())
    inverse = ~transform
    drawn = [layer for layer in layers if layer.drawable]
    draw = ImageDraw.Draw(image)
    for layer in drawn:
        for ring in layer.rings:
            pixels = []
            for x, y in ring:
                col, row = inverse @ (x, y)
                # Preview pixel i shows source pixel i * step, whose centre is at i * step + 0.5.
                pixels.append(((col - 0.5) / step, (row - 0.5) / step))
            if len(pixels) >= 2:
                draw.line([*pixels, pixels[0]], fill=ROI_INDEX, width=1)
    buffer = stdio.BytesIO()
    image.save(buffer, format="PNG", compress_level=9)
    data = buffer.getvalue()
    if len(data) > PREVIEW_MAX_BYTES:
        raise PilotError(f"the preview is {len(data)} bytes, over {PREVIEW_MAX_BYTES} bytes")
    description = {
        "width": index.shape[1],
        "height": index.shape[0],
        "step": step,
        "sampling": (
            f"every {step}th row and column of the decoded height array, from row 0 and column "
            "0; no averaging or resampling"
            if step > 1
            else "every pixel of the decoded height array"
        ),
        "grey_scale": {"black_m": low, "white_m": high, "levels": GREY_LEVELS},
        "invalid_colour_rgb": list(INVALID_RGB),
        "roi_outline_colour_rgb": list(ROI_RGB),
        "roi_outlines": bool(drawn),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    return data, description


@dataclass(frozen=True)
class Pgda78Source:
    """PGDA product 78, Site01 Connecting Ridge (PRD 5 row 5, pilot step 2).

    The file list comes from the provider's pages: the README at data/LOLA_5mpp/ defines what
    each file-name suffix holds, and the data index and the product 78 page link the Site01
    files. Only the height, count, slope and error GeoTIFFs and the ROI archive are fetched;
    never the clones, the point cloud or the 87S mosaic. A file is downloaded whole only below
    ``whole_file_limit_bytes`` (200 MB). A GeoTIFF at or over it is read by window instead: the
    centred ``window_pixels`` square, through HTTP range requests in ``range_block_bytes``
    blocks, and only the byte ranges read are stored. Every GeoTIFF is then decoded on that same
    window, so the record describes the window. An ROI archive over the limit is not fetched,
    because a gzip tar cannot be read by window; the record says so.
    """

    base_url: str = "https://pgda.gsfc.nasa.gov/"
    site: str = "Site01"
    whole_file_limit_bytes: int = WHOLE_FILE_LIMIT_BYTES
    window_pixels: int = RANGE_WINDOW_PIXELS
    range_block_bytes: int = RANGE_BLOCK_BYTES

    @property
    def name(self) -> str:
        return "pgda78"

    @property
    def product(self) -> str:
        return f"PGDA78/{self.site}"

    @property
    def readme_url(self) -> str:
        return urllib.parse.urljoin(self.base_url, "data/LOLA_5mpp/README")

    @property
    def index_url(self) -> str:
        return urllib.parse.urljoin(self.base_url, "data/LOLA_5mpp/")

    @property
    def product_page_url(self) -> str:
        return urllib.parse.urljoin(self.base_url, "products/78")

    @property
    def site_url(self) -> str:
        return urllib.parse.urljoin(self.base_url, f"data/LOLA_5mpp/{self.site}/")

    def _documents(self) -> tuple[tuple[str, str, str], ...]:
        return (
            ("readme", self.readme_url, "README"),
            ("data_index", self.index_url, "LOLA_5mpp_index.html"),
            ("product_page", self.product_page_url, "products_78.html"),
        )

    # -- fetch --

    def _site_links(self, page: str, page_url: str) -> tuple[dict[str, str], set[str]]:
        """Links to files directly in the site folder (name -> URL), and the deeper paths."""
        links: dict[str, str] = {}
        deeper: set[str] = set()
        for match in _HREF.finditer(page):
            href = unescape(next(group for group in match.groups() if group is not None))
            url = urllib.parse.urljoin(page_url, href)
            parts = urllib.parse.urlsplit(url)
            location = urllib.parse.urlunsplit(parts._replace(query="", fragment=""))
            if not location.startswith(self.site_url):
                continue
            if has_url_credentials(url):
                raise PilotError(
                    f"a {self.site} link on {page_url} has credentials or a signature; it was "
                    "not followed"
                )
            relative = location[len(self.site_url) :]
            if not relative:
                continue
            if "/" in relative:
                deeper.add(relative)
                continue
            name = _safe_file_name(relative, page_url)
            url = urllib.parse.urlunsplit(parts._replace(fragment=""))
            if links.get(name, url) != url:
                raise PilotError(f"{page_url} links {name} to two URLs")
            links[name] = url
        return links, deeper

    def fetch(
        self, raw_dir: Path, download: Downloader, read_range: RangeFetcher, now: Clock
    ) -> None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=raw_dir, prefix=".fetch-") as staging_name:
            staging = Path(staging_name)
            documents = []
            for role, url, file_name in self._documents():
                fetched = download(url, staging / file_name, max_bytes=self.whole_file_limit_bytes)
                documents.append(_fetch_entry(fetched, now(), role=role))
            readme = _parse_readme(_decode(staging / "README"))
            missing = [role.suffix for role in _GEOTIFF_ROLES if role.suffix not in readme]
            if missing:
                raise PilotError(
                    f"the README at {self.readme_url} does not describe *{', *'.join(missing)}; "
                    "it may be an error page"
                )
            listed: dict[str, dict[str, Any]] = {}
            subfolder_files: set[str] = set()
            for role, url, file_name in self._documents()[1:]:
                links, deeper = self._site_links(_decode(staging / file_name), url)
                subfolder_files |= deeper
                for name, link in links.items():
                    entry = listed.setdefault(name, {"url": link, "listed_by": []})
                    if entry["url"] != link:
                        raise PilotError(f"the pages link {name} to two URLs")
                    entry["listed_by"].append(role)
            selected: list[tuple[str, str, tuple[bytes, ...]]] = []
            for role_def in _GEOTIFF_ROLES:
                names = [
                    n
                    for n in listed
                    if n.startswith(f"{self.site}_") and n.endswith(role_def.suffix)
                ]
                if len(names) != 1:
                    raise PilotError(
                        f"expected one {self.site} file ending in {role_def.suffix} on "
                        f"{self.index_url} or {self.product_page_url}, found {len(names)}"
                    )
                selected.append((role_def.key, names[0], _TIFF_MAGIC))
            rois = [n for n in listed if n.startswith(f"{self.site}_") and n.endswith(_ROI_SUFFIX)]
            if len(rois) > 1:
                raise PilotError(f"expected at most one {self.site} ROI archive, found {rois}")
            selected += [("roi_shapefiles", name, _GZIP_MAGIC) for name in rois]
            files = []
            roi_not_fetched: dict[str, Any] | None = None
            for role, name, magic in selected:
                url, listed_by = listed[name]["url"], listed[name]["listed_by"]
                try:
                    fetched = download(
                        url, staging / name, max_bytes=self.whole_file_limit_bytes, magic=magic
                    )
                except FileTooLargeError as refused:
                    if role == "roi_shapefiles":
                        roi_not_fetched = {
                            "file_name": name,
                            "url": url,
                            "listed_by": listed_by,
                            "reason": (
                                f"{refused}; a gzip tar archive cannot be read by window, so it "
                                "was not fetched and the ROI outlines are missing"
                            ),
                        }
                        continue
                    remote = read_remote_geotiff_window(
                        url,
                        self._centre_window,
                        fetch=read_range,
                        block_bytes=self.range_block_bytes,
                        max_bytes=self.whole_file_limit_bytes,
                    )
                    local_name = f"{name}{_RANGES_SUFFIX}"
                    (staging / local_name).write_bytes(remote.ranges.data)
                    files.append(
                        _window_entry(
                            remote,
                            local_name,
                            now(),
                            role=role,
                            listed_by=listed_by,
                            provider_file_name=name,
                            whole_file_refused=str(refused),
                        )
                    )
                    continue
                files.append(_fetch_entry(fetched, now(), role=role, listed_by=listed_by))
            chosen = {name for _, name, _ in selected}
            metadata = {
                "source": self.name,
                "product": self.product,
                "command": f"{COMMAND} fetch {self.name}",
                "whole_file_limit_bytes": self.whole_file_limit_bytes,
                "documents": documents,
                "files": files,
                "roi_archive_listed": bool(rois),
                "roi_archive_not_fetched": roi_not_fetched,
                "listed_not_fetched": [
                    {
                        "file_name": name,
                        "url": listed[name]["url"],
                        "listed_by": listed[name]["listed_by"],
                        "reason": "not needed for the pilot record (PRD 5 pilot step 2)",
                    }
                    for name in sorted(set(listed) - chosen)
                ],
                "subfolder_files_not_fetched": {
                    "count": len(subfolder_files),
                    "folders": sorted({path.split("/")[0] + "/" for path in subfolder_files}),
                },
            }
            (raw_dir / FETCH_METADATA).unlink(missing_ok=True)
            for entry in [*documents, *files]:
                os.replace(staging / entry["file_name"], raw_dir / entry["file_name"])
            _write_atomically(raw_dir / FETCH_METADATA, canonical_json(metadata))

    def _centre_window(self, width: int, height: int) -> Window:
        """The centred ``window_pixels`` square (smaller only where the raster is smaller)."""
        side_x, side_y = min(self.window_pixels, width), min(self.window_pixels, height)
        return Window((width - side_x) // 2, (height - side_y) // 2, side_x, side_y)

    # -- build --

    def build(self, raw_dir: Path, root: Path) -> Build:
        fetched = _load_fetch(raw_dir, self.name)
        try:
            return self._build(raw_dir, root, fetched)
        except (KeyError, TypeError) as error:
            raise PilotError(
                f"data/raw/{self.name}/{FETCH_METADATA} lacks an expected entry ({error!r}); "
                f"run: {COMMAND} fetch {self.name}"
            ) from error

    def _build(self, raw_dir: Path, root: Path, fetched: Mapping[str, Any]) -> Build:
        documents = {entry["role"]: entry for entry in fetched["documents"]}
        files = {entry["role"]: entry for entry in fetched["files"]}
        readme_text = _decode(raw_dir / documents["readme"]["file_name"])
        product_text = _decode(raw_dir / documents["product_page"]["file_name"])
        readme = _parse_readme(readme_text)
        missing = [role.suffix for role in _GEOTIFF_ROLES if role.suffix not in readme]
        if missing:
            raise PilotError(
                f"the fetched README does not describe *{', *'.join(missing)}; "
                f"run: {COMMAND} fetch {self.name}"
            )
        attribution = self._attribution(product_text, readme_text)
        names = {role.key: _provider_name(files[role.key]) for role in _GEOTIFF_ROLES}
        window = self._decoded_window(files)

        decoded: dict[str, _Decoded] = {}
        entries: list[dict[str, Any]] = []
        for role in _GEOTIFF_ROLES:
            fetch_entry = files[role.key]
            description = readme[role.suffix]
            units = role.units if role.units_phrase in description else UNKNOWN
            item = self._decode(
                raw_dir, root, role, fetch_entry, units, attribution, names, readme, window
            )
            decoded[role.key] = item
            entries.append(self._file_entry(role, fetch_entry, description, units, item, window))

        height = decoded["height"]
        _, _, extent = _grid(height.band, raw_dir / names["height"])
        # The decoded array's own transform: the window's, or the raster's for a whole read.
        transform = height.band.transform
        assert transform is not None, "_grid refuses a file without a geotransform"
        for entry, role in zip(entries, _GEOTIFF_ROLES, strict=True):
            entry["grid_matches_height"] = _same_grid(decoded[role.key].band, height.band)
        support = self._effective_support(height, decoded["lola_return_count"], names)

        roi, layers, roi_record = self._roi(
            raw_dir,
            root,
            files,
            height,
            extent,
            attribution,
            readme,
            fetched.get("roi_archive_not_fetched"),
        )
        heights = height.band.array[height.mask.valid]
        low, high = float(np.min(heights)), float(np.max(heights))
        preview_bytes, preview = _render_preview(height, transform, layers, low, high)
        preview_path = f"outputs/pilot/{self.name}_{self.site.lower()}_preview.png"
        preview = {"path": preview_path, "source_file": names["height"], **preview}

        records = [item.record for item in decoded.values()]
        verification: dict[str, dict[str, str]] = {}
        for role in _GEOTIFF_ROLES:
            item = decoded[role.key]
            how = (
                f"read by window through HTTP range requests (the centred window "
                f"{window['width']} x {window['height']} pixels; the file is not downloaded)"
                if _is_window_read(files[role.key]) and window is not None
                else "downloaded"
            )
            if role.key == "height" and item.record.units != UNKNOWN:
                record = ProductRecord.model_validate(
                    {**item.record.model_dump(), "verification_status": "VERIFIED"}
                )
                records[_GEOTIFF_ROLES.index(role)] = record
                basis = (
                    f"{how}, decoded, masked with quality.validity_mask and plotted in the "
                    "preview PNG, as the PRD 5 entry rule requires"
                )
            else:
                record = item.record
                basis = (
                    f"{how}, decoded and masked with quality.validity_mask; not plotted, so "
                    "the PRD 5 entry rule is not complete"
                    if role.key != "height"
                    else "the README states no units for the height map"
                )
            verification[record.product_id] = {
                "status": record.verification_status,
                "basis": basis,
            }
        if roi_record is not None:
            records.append(roi_record)
            if roi["status"] == "unreadable":
                parts = [f"downloaded; not read: {roi['reason']}"]
            else:
                every = all(layer["readable"] for layer in roi["shapefiles"])
                parts = [
                    "downloaded and every shapefile read"
                    if every
                    else "downloaded; not every shapefile could be read"
                ]
                if roi["outlines_drawn"]:
                    parts.append("outlines drawn on the preview")
                if roi["outlines_not_drawn_reason"] is not None:
                    parts.append(f"outlines not drawn: {roi['outlines_not_drawn_reason']}")
            verification[roi_record.product_id] = {
                "status": roi_record.verification_status,
                "basis": "; ".join(parts),
            }

        frame = _FRAME.search(readme_text)
        site_name = re.search(rf"^{re.escape(self.site)}:\s*(\S.*?)\s*$", readme_text, re.MULTILINE)
        retrieved = sorted(e["retrieved_at"] for e in [*fetched["documents"], *fetched["files"]])
        result = {
            "record": f"{self.name}_{self.site.lower()}",
            "description": (
                "PGDA product 78 Site01 as fetched and decoded by the T1 pilot (PRD 5 pilot step "
                "2). Observations only: the shipping mode and the G0 status are a human "
                "checkpoint (PRD 23.6) and are not recorded here."
            ),
            "source": self.name,
            "product": self.product,
            "site": {
                "id": self.site,
                "name": None if site_name is None else site_name.group(1),
                "name_source": self.readme_url,
            },
            "frame_stated_by_provider": {
                "text": None if frame is None else frame.group(0),
                "source": self.readme_url,
                "note": "the GeoTIFFs name no frame; see files[].frame_named_in_file",
            },
            "documents": [
                {
                    key: entry[key]
                    for key in (
                        "role",
                        "file_name",
                        "url",
                        "http_status",
                        "content_type",
                        "bytes_received",
                        "sha256",
                        "last_modified",
                        "retrieved_at",
                    )
                }
                | {"local_path": f"data/raw/{self.name}/{entry['file_name']}"}
                for entry in fetched["documents"]
            ],
            "retrieval": {
                "first_retrieved_at": retrieved[0],
                "last_retrieved_at": retrieved[-1],
                "fetch_metadata": f"data/raw/{self.name}/{FETCH_METADATA}",
                "command": fetched["command"],
            },
            "files": entries,
            "listed_not_fetched": fetched["listed_not_fetched"],
            "subfolder_files_not_fetched": fetched["subfolder_files_not_fetched"],
            "decoded_window": window,
            "dem_valid_fraction": entries[0]["valid"]["valid_fraction"],
            "effective_support": support,
            "roi": roi,
            "preview": preview,
            "verification": verification,
            "field_notes": self._field_notes(windowed=window is not None),
            "tool_versions": tool_versions(),
        }
        files_out = {
            f"outputs/pilot/{self.name}_{self.site.lower()}.json": canonical_json(result),
            preview_path: preview_bytes,
        }
        return Build(
            files=files_out,
            records=tuple(sorted(records, key=lambda r: r.product_id)),
            record_prefix=f"{self.product}/",
        )

    def _attribution(self, product_text: str, readme_text: str) -> str:
        cite = _CITE.search(product_text)
        doi = None if cite is None else _DOI.search(product_text, cite.end())
        if doi is None:
            return UNKNOWN
        licence = "licen" in product_text.lower() or "licen" in readme_text.lower()
        return (
            f"Cite doi:{doi.group(0)}, as {self.product_page_url} asks ('cite the following "
            "reference when using any of the products')"
            + ("" if licence else "; neither the product page nor the README states a licence")
        )

    def _metadata(
        self,
        role: _Role,
        url: str,
        units: str,
        attribution: str,
        names: Mapping[str, str],
        readme: Mapping[str, str],
    ) -> ProductMetadata:
        count, toterr, slperr = (
            names["lola_return_count"],
            names["height_rms_error"],
            names["slope_rms_error"],
        )
        qa = {
            "height": (
                f"pixels with no LOLA return in {count} are filled by interpolation (README: "
                f"'{readme['_surf.tif']}'); total Z uncertainty in {toterr}"
            ),
            "lola_return_count": f"README: '{readme['_ldec.tif']}'",
            "slope": f"slope uncertainty in {slperr} (README: '{readme['_slperr.tif']}')",
            "height_rms_error": f"README: '{readme['_toterr.tif']}'",
            "slope_rms_error": f"README: '{readme['_slperr.tif']}'",
        }[role.key]
        metadata: ProductMetadata = {
            "body": "moon",
            "provider": (
                "NASA Goddard Space Flight Center, Planetary Geology, Geophysics and "
                "Geochemistry Laboratory (PGDA), product 78"
            ),
            "source_url": url,
            "frequency_or_wavelength": None,
            "transmit_receive_convention": None,
            "calibration_type": None,
            "incidence_source": None,
            "qa_definition": qa,
            "license_or_attribution": attribution,
        }
        if units != UNKNOWN:
            metadata["units"] = units
        return metadata

    def _decode(
        self,
        raw_dir: Path,
        root: Path,
        role: _Role,
        fetch_entry: Mapping[str, Any],
        units: str,
        attribution: str,
        names: Mapping[str, str],
        readme: Mapping[str, str],
        window: Mapping[str, int] | None,
    ) -> _Decoded:
        path = raw_dir / str(fetch_entry["file_name"])
        metadata = self._metadata(role, str(fetch_entry["url"]), units, attribution, names, readme)
        ranges = self._ranges(path, fetch_entry) if _is_window_read(fetch_entry) else None
        bands, record = read_product(
            f"{self.product}/{_provider_name(fetch_entry)}",
            [path],
            metadata,
            root=root,
            window=None if window is None else Window(**window),
            ranges=ranges,
        )
        if len(bands) != 1:
            raise PilotError(f"{path.name} has {len(bands)} bands; the README describes one map")
        tags = read_geotiff_tags(path, ranges=ranges)
        band = bands[0]
        return _Decoded(
            band=band,
            record=record,
            mask=validity_mask(band.array, nodata=band.nodata),
            tags=tags,
        )

    def _ranges(self, path: Path, entry: Mapping[str, Any]) -> ByteRanges:
        """The byte ranges a window read stored in ``path``, as fetch.json describes them."""
        try:
            return ByteRanges(
                name=_provider_name(entry),
                size=int(entry["remote_bytes"]),
                ranges=tuple((int(first), int(last)) for first, last in entry["ranges"]),
                data=path.read_bytes(),
            )
        except ValueError as error:
            raise PilotError(
                f"data/raw/{self.name}/{FETCH_METADATA} describes the byte ranges in {path.name} "
                f"wrongly ({error}); run: {COMMAND} fetch {self.name}"
            ) from error

    def _decoded_window(self, files: Mapping[str, Mapping[str, Any]]) -> dict[str, int] | None:
        """The window every GeoTIFF is decoded on: None, or the one the window reads share."""
        windows = [
            files[role.key]["window"] for role in _GEOTIFF_ROLES if _is_window_read(files[role.key])
        ]
        if not windows:
            return None
        keys = {"col_off", "row_off", "width", "height"}
        if any(
            not isinstance(window, dict)
            or set(window) != keys
            or not all(type(value) is int for value in window.values())
            for window in windows
        ) or any(window != windows[0] for window in windows):
            raise PilotError(
                f"the GeoTIFFs read by window in data/raw/{self.name}/{FETCH_METADATA} do not "
                f"share one window; run: {COMMAND} fetch {self.name}"
            )
        return {key: int(windows[0][key]) for key in ("col_off", "row_off", "width", "height")}

    def _file_entry(
        self,
        role: _Role,
        fetch_entry: Mapping[str, Any],
        description: str,
        units: str,
        item: _Decoded,
        window: Mapping[str, int] | None,
    ) -> dict[str, Any]:
        band = item.band
        path_name = _provider_name(fetch_entry)
        windowed = _is_window_read(fetch_entry)
        _, pixel, extent = _grid(band, Path(path_name))
        decoded_window = None
        if window is not None:
            t = band.transform
            assert t is not None, "_grid refuses a file without a geotransform"
            decoded_window = {
                **window,
                "extent_m": {
                    "left": float(t.c),
                    "top": float(t.f),
                    "right": float(t.c + t.a * window["width"]),
                    "bottom": float(t.f + t.e * window["height"]),
                },
            }
        range_read = None
        if windowed:
            range_read = {
                "local_path": item.record.local_path,
                "bytes_received": fetch_entry["bytes_received"],
                "local_sha256": item.record.checksum,
                "range_requests": fetch_entry["range_requests"],
                "range_count": len(fetch_entry["ranges"]),
                "whole_file_refused": fetch_entry["whole_file_refused"],
                "sha256_note": (
                    "the whole file was not downloaded, so its SHA-256 is unknown; local_sha256 "
                    "is that of the byte ranges held in local_path"
                ),
            }
        fraction = valid_fraction(item.mask)
        counts = item.mask.reason_counts()
        values = band.array[item.mask.valid]
        wkt = band.crs
        frame_tokens = [
            token
            for token in ("MOON_ME", "DE421")
            if (wkt is not None and token in wkt) or any(token in v for v in item.tags.values())
        ]
        return {
            "role": role.key,
            "file_name": path_name,
            "product_id": item.record.product_id,
            "url": fetch_entry["url"],
            "final_url": fetch_entry["final_url"],
            "listed_by": fetch_entry["listed_by"],
            "local_path": item.record.local_path,
            "read": "window" if windowed else "whole",
            "range_read": range_read,
            "decoded_window": decoded_window,
            "bytes": fetch_entry["remote_bytes"] if windowed else fetch_entry["bytes_received"],
            "sha256": UNKNOWN if windowed else item.record.checksum,
            "http_status": fetch_entry["http_status"],
            "content_type": fetch_entry["content_type"],
            "last_modified": fetch_entry["last_modified"],
            "etag": fetch_entry["etag"],
            "retrieved_at": fetch_entry["retrieved_at"],
            "readme_description": description,
            "units": units,
            "units_source": (
                f"README: '*{role.suffix} : {description}'"
                if units != UNKNOWN
                else f"README describes *{role.suffix} without units"
            ),
            "units_in_file": band.units,
            "dtype": str(band.array.dtype),
            "band_count": 1,
            "width": band.raster_width,
            "height": band.raster_height,
            "crs_wkt": wkt,
            "crs_linear_units": None if wkt is None else CRS.from_wkt(wkt).linear_units,
            "crs_datum": None if wkt is None else _wkt_datum(wkt),
            "crs_spheroid": _wkt_spheroid(wkt or ""),
            "frame_named_in_file": " ".join(frame_tokens) or None,
            "transform": [float(value) for value in tuple(band.raster_transform)[:6]]
            if band.raster_transform is not None
            else None,
            "pixel_size_m": pixel,
            "extent_m": extent,
            "area_or_point": item.tags.get("AREA_OR_POINT"),
            "nodata": _json_number(band.nodata),
            "scale": band.scale,
            "offset": band.offset,
            "valid": {
                "valid_pixels": fraction.valid_pixels,
                "total_pixels": fraction.total_pixels,
                "valid_fraction": fraction.fraction,
                "invalid_by_reason": {
                    reason.label: count
                    for reason, count in counts.items()
                    if reason is not InvalidReason.VALID
                },
                "mask": "quality.validity_mask with the nodata value the file declares",
            },
            "valid_min": float(np.min(values)) if values.size else None,
            "valid_max": float(np.max(values)) if values.size else None,
        }

    def _effective_support(
        self, height: _Decoded, count: _Decoded, names: Mapping[str, str]
    ) -> dict[str, Any]:
        if not _same_grid(height.band, count.band):
            raise PilotError(
                f"the count map {names['lola_return_count']} is on another grid than the height "
                f"map {names['height']}; effective support cannot be read pixel by pixel"
            )
        dem_valid = height.mask.valid
        both = dem_valid & count.mask.valid
        values = np.asarray(count.band.array[both], dtype=np.float64)
        if values.size and (np.any(values < 0) or np.any(values != np.floor(values))):
            raise PilotError(
                f"the count map {names['lola_return_count']} holds negative or non-integral "
                "counts; it cannot be read as LOLA returns per pixel"
            )
        dem_valid_pixels = int(np.count_nonzero(dem_valid))
        if dem_valid_pixels == 0:
            raise PilotError(f"the height map {names['height']} has no valid pixel")
        observed = int(np.count_nonzero(values >= 1))
        interpolated = int(np.count_nonzero(values == 0))
        count_invalid = int(np.count_nonzero(dem_valid & ~count.mask.valid))
        levels, frequencies = np.unique(values, return_counts=True)
        return {
            "count_file": names["lola_return_count"],
            "height_file": names["height"],
            "definition": (
                "over pixels valid in the height map: observed = count map >= 1 LOLA return, "
                "interpolated = count 0 (README: 'with interpolation filling empty pix'), "
                "count_invalid = the count map pixel is invalid by quality.validity_mask"
            ),
            "dem_valid_pixels": dem_valid_pixels,
            "observed_pixels": observed,
            "interpolated_pixels": interpolated,
            "count_invalid_pixels": count_invalid,
            "observed_fraction": observed / dem_valid_pixels,
            "interpolated_fraction": interpolated / dem_valid_pixels,
            "count_invalid_fraction": count_invalid / dem_valid_pixels,
            "count_histogram": {
                str(int(level)): int(frequency)
                for level, frequency in zip(levels, frequencies, strict=True)
            },
            "grid_matches_height": True,
        }

    def _roi(
        self,
        raw_dir: Path,
        root: Path,
        files: Mapping[str, Mapping[str, Any]],
        height: _Decoded,
        extent: Mapping[str, float],
        attribution: str,
        readme: Mapping[str, str],
        not_fetched: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], list[_RoiLayer], ProductRecord | None]:
        entry = files.get("roi_shapefiles")
        if entry is None and not_fetched is not None:
            reason = str(not_fetched["reason"])
            return (
                {
                    "status": "not_fetched",
                    "reason": reason,
                    "archive": {
                        "file_name": not_fetched["file_name"],
                        "url": not_fetched["url"],
                        "listed_by": not_fetched["listed_by"],
                    },
                    "members": [],
                    "shapefiles": [],
                    "outlines_drawn": False,
                    "outlines_not_drawn_reason": reason,
                },
                [],
                None,
            )
        if entry is None:
            reason = (
                f"no {self.site} *{_ROI_SUFFIX} archive is linked from {self.index_url} or "
                f"{self.product_page_url}"
            )
            return (
                {
                    "status": "not_listed",
                    "reason": reason,
                    "archive": None,
                    "members": [],
                    "shapefiles": [],
                    "outlines_drawn": False,
                    "outlines_not_drawn_reason": reason,
                },
                [],
                None,
            )
        path = raw_dir / str(entry["file_name"])
        archive = _read_roi_archive(path, height.band.crs, extent)
        layers = archive.layers
        drawn = any(layer.drawable for layer in layers)
        reasons = []
        for layer in layers:
            if layer.drawable:
                continue
            summary = layer.summary
            if not summary["readable"]:
                reasons.append(
                    f"{summary['name']}: {summary['read_error']}; its outlines are missing"
                )
            elif summary["prj_wkt"] is None:
                reasons.append(
                    f"{summary['name']}: {summary['crs_comparison']}; outlines are never placed "
                    "by assuming a CRS"
                )
            elif summary["crs_matches_height"] is not True:
                reasons.append(
                    f"{summary['name']}: its CRS differs from the height map CRS "
                    f"({summary['crs_comparison']}); outlines are not reprojected"
                )
            elif not summary["inside_height_extent"]:
                reasons.append(f"{summary['name']}: coordinates outside the height map")
            else:
                reasons.append(f"{summary['name']}: not a polygon layer")
        if archive.error is not None:
            not_drawn: str | None = archive.error
        elif not layers:
            not_drawn = "the archive holds no shapefile"
        else:
            not_drawn = "; ".join(reasons) or None
        prj_texts = [layer.summary["prj_wkt"] for layer in layers if layer.summary["prj_wkt"]]
        linear = None
        if len(prj_texts) == 1:
            try:
                linear = CRS.from_wkt(prj_texts[0]).linear_units
            except rasterio.errors.CRSError:
                linear = None
        local_path = f"data/raw/{self.name}/{entry['file_name']}"
        record = ProductRecord.model_validate(
            {
                "product_id": f"{self.product}/{entry['file_name']}",
                "provider": (
                    "NASA Goddard Space Flight Center, Planetary Geology, Geophysics and "
                    "Geochemistry Laboratory (PGDA), product 78"
                ),
                "version": UNKNOWN,
                "source_url": entry["url"],
                "local_path": local_path,
                "checksum": sha256_file(root / local_path),
                "body": "moon",
                "acquisition_time": None,
                "frequency_or_wavelength": None,
                "transmit_receive_convention": None,
                "units": (f"{linear} (coordinate unit stated in the .prj)" if linear else UNKNOWN),
                "calibration_type": None,
                "native_grid": None,
                "effective_resolution": None,
                "crs_and_frame": prj_texts[0] if len(prj_texts) == 1 else UNKNOWN,
                "incidence_source": None,
                "qa_definition": None,
                "license_or_attribution": attribution,
                # FAILED: the archive was downloaded, but reading it did not succeed (schema.py).
                "verification_status": "PARTIAL" if archive.error is None else "FAILED",
            }
        )
        roi = {
            "status": "read" if archive.error is None else "unreadable",
            "reason": archive.error,
            "archive": {
                "file_name": entry["file_name"],
                "url": entry["url"],
                "local_path": local_path,
                "bytes": entry["bytes_received"],
                "sha256": entry["sha256"],
                "retrieved_at": entry["retrieved_at"],
                "readme_description": readme.get(_ROI_SUFFIX),
            },
            "members": archive.members,
            "member_note": "read in memory; nothing is extracted and links are not followed",
            "shapefiles": [layer.summary for layer in layers],
            "outlines_drawn": drawn,
            "outlines_not_drawn_reason": not_drawn,
        }
        return roi, layers, record

    def _field_notes(self, *, windowed: bool) -> dict[str, str]:
        notes = {
            "decoded_window": (
                "null when every GeoTIFF was decoded whole. Otherwise a file was at or over the "
                "whole-file limit and was read by window: every GeoTIFF is decoded on that "
                "centred window, and every count, fraction and the preview describe it only"
            ),
            "version": "unknown: the README and the product 78 page state no version",
            "acquisition_time": (
                "unknown for the GeoTIFFs: the LOLA acquisition times are in the point cloud "
                "(.xyzi RDR IDs), which the pilot does not read; null for the ROI archive"
            ),
            "effective_resolution": (
                "unknown: neither the files nor the README state one; the count map gives the "
                "effective support instead (effective_support)"
            ),
            "frequency_or_wavelength, transmit_receive_convention, calibration_type, "
            "incidence_source": "null: these describe radar observations; PGDA78 is terrain",
            "crs_and_frame": (
                "the CRS WKT as read from each file; the frame is quoted from the README "
                "(frame_stated_by_provider), because the files name none"
            ),
            "nodata": "as declared by the file; 'NaN' is the JSON spelling of a NaN nodata value",
            "roi_archive": (
                "native_grid, effective_resolution and qa_definition are null for vector outlines"
            ),
        }
        if windowed:
            notes["checksum"] = (
                "for a GeoTIFF read by window, the ProductRecord checksum and local_path are "
                "those of the stored byte ranges (files[].range_read), not of the whole file"
            )
        return notes


# --- minirf: the Mini-RF south-polar CPR mosaic, LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0 ------------------

_PDS_MAGIC: Final = (b"PDS_VERSION_ID",)
_ROWS_SUFFIX: Final = ".rows"
"""The local file holding the image rows read by range, named after the image file."""

_MINIRF_CATALOG_FILES: Final = (
    ("dsmap", "dsmap.cat"),
    ("data_set_catalog", "mrflro_5_cdr_mosaic_v1_0_ds.cat"),
    ("instrument_catalog", "mrf_inst.cat"),
)
_QUANTITIES: Final = (
    ("CPR", re.compile(r"circular polarization ratio")),
    ("S1", re.compile(r"\bfirst stokes\b|\bs1\b")),
    ("S2", re.compile(r"\bsecond stokes\b|\bs2\b")),
    ("S3", re.compile(r"\bthird stokes\b|\bs3\b")),
    ("S4", re.compile(r"\bfourth stokes\b|\bs4\b")),
)
_STOKES_QUANTITIES: Final = ("S1", "S2", "S3", "S4")
_COMBINED_LOOK: Final = re.compile(r"This is a combined East and West-looking mosaic\.")
_CPR_DEFINITION: Final = re.compile(r"\(CPR = [^)]*\)")
_STOKES_DEFINITION: Final = re.compile(r"\bS[1-4] = [^.]*")
_TRANSMIT_SENTENCE: Final = re.compile(r"The Mini-RF instrument transmits[^.]*\.")
_S_BAND_SENTENCE: Final = re.compile(r"Mini-RF operated in S-band \([^)]*\)")
_PA_SENTENCE: Final = re.compile(
    r"[A-Z][^.=]*\bMoon Princip\w* Axis \(PA\) coordinate system[^.]*\."
)
_DSMAP_FORMULAS: Final = ("x = (Sample - S0) * Scale", "y = (L0 - Line) * Scale")
_DSMAP_CENTRES: Final = "Integral values of line and sample correspond to the center of a pixel."
_VALUE_RULE_WORDS: Final = ("VALID_MIN", "VALID_MAX", "MISSING", "INVALID")
_HANDEDNESS_WORDS: Final = ("POLARIZATION", "HANDEDNESS", "TRANSMIT")
_BYTE_ORDER: Final = {"<": "little-endian", ">": "big-endian", "|": "single byte"}


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _page_links(page: str, page_url: str) -> list[str]:
    """Every http(s) link on a page, resolved against its URL, without query or fragment."""
    links: list[str] = []
    for match in _HREF.finditer(page):
        href = unescape(next(group for group in match.groups() if group is not None))
        parts = urllib.parse.urlsplit(urllib.parse.urljoin(page_url, href))
        if parts.scheme.lower() in ("http", "https"):
            links.append(urllib.parse.urlunsplit(parts._replace(query="", fragment="")))
    return links


def _public(url: str, page_url: str) -> str:
    if has_url_credentials(url):
        raise PilotError(
            f"a link on {page_url} has credentials or a signature; it was not followed"
        )
    return url


def _child_link(page: str, page_url: str, child: str) -> str:
    """The link on a directory page to ``child`` (a file name, or a folder ending in ``/``)."""
    wanted = urllib.parse.urljoin(page_url, child).lower()
    found = sorted({link for link in _page_links(page, page_url) if link.lower() == wanted})
    if len(found) != 1:
        raise PilotError(f"{page_url} does not link {child} (found {len(found)} links to it)")
    return _public(found[0], page_url)


def _directory_files(page: str, directory_url: str) -> dict[str, str]:
    """The files a directory page links directly inside that directory: name -> URL."""
    files: dict[str, str] = {}
    prefix = directory_url.lower()
    for link in _page_links(page, directory_url):
        if not link.lower().startswith(prefix):
            continue
        rest = urllib.parse.unquote(link[len(directory_url) :])
        if not rest or "/" in rest:
            continue
        name = _safe_file_name(rest, directory_url)
        if files.get(name, link) != link:
            raise PilotError(f"{directory_url} links {name} to two URLs")
        files[name] = _public(link, directory_url)
    return files


def _rows_entry(
    response: RangeResponse, local_name: str, retrieved_at: datetime, **extra: Any
) -> dict[str, Any]:
    """The fetch.json entry of image rows read by one range request, stored in ``local_name``."""
    return {
        **extra,
        "read": "rows",
        "file_name": local_name,
        "url": response.url,
        "final_url": response.final_url,
        "http_status": response.status,
        "requested_range": f"bytes={response.requested_first}-{response.requested_last}",
        "content_range": response.content_range,
        "first_byte": response.first_byte,
        "last_byte": response.last_byte,
        "remote_bytes": response.total_size,
        "bytes_received": response.bytes_received,
        "content_type": response.content_type,
        "last_modified": response.last_modified,
        "etag": response.etag,
        "sha256": hashlib.sha256(response.data).hexdigest(),
        "retrieved_at": utc_timestamp(retrieved_at),
    }


def _all_items(block: Pds3Object) -> list[Pds3Item]:
    """Every statement of a PDS3 block and of the blocks nested in it."""
    items = list(block.items)
    for child in block.children:
        items += _all_items(child)
    return items


def _find_text(block: Pds3Object, key: str) -> str | None:
    for item in _all_items(block):
        if item.key == key and isinstance(item.value, str):
            return item.text()
    return None


def _label_text(block: Pds3Object, key: str) -> str | None:
    item = block.get(key)
    if item is None:
        return None
    return item.text() if isinstance(item.value, str) else item.raw


def _bounds(left: float, bottom: float, right: float, top: float) -> dict[str, float]:
    return {"left": left, "bottom": bottom, "right": right, "top": top}


def _converted_bounds(
    extent: Mapping[str, float], wkt: str, target: pyproj.CRS
) -> tuple[float, float, float, float]:
    """The bounds of ``extent`` (in the CRS ``wkt``) in ``target``, densified along the edges."""
    transformer = pyproj.Transformer.from_crs(pyproj.CRS.from_wkt(wkt), target, always_xy=True)
    left, bottom, right, top = transformer.transform_bounds(
        extent["left"],
        extent["bottom"],
        extent["right"],
        extent["top"],
        densify_pts=21,
        errcheck=True,
    )
    return float(left), float(bottom), float(right), float(top)


@dataclass(frozen=True)
class _SiteWindow:
    """The Site01 extent as T1-03 recorded it and the mosaic window that covers it."""

    site_extent: dict[str, Any]
    bounds: tuple[float, float, float, float]
    window: tuple[int, int, int, int]

    def fetch_fields(self) -> dict[str, Any]:
        return {"site_extent": self.site_extent, "pixel_window": _pixel_window(self.window)}


@dataclass(frozen=True)
class _MosaicDocuments:
    """What the volume's catalog files state for every mosaic label's ProductRecord."""

    s_band: re.Match[str] | None
    pa: re.Match[str] | None
    citation: str | None
    catalog_licence: bool


def _handedness_items(label: Pds3Object) -> list[Pds3Item]:
    return [item for item in _all_items(label) if any(w in item.key for w in _HANDEDNESS_WORDS)]


def _value_rules(label: Pds3Object) -> list[str]:
    """The IMAGE keywords that would make values invalid beyond the special pixels."""
    return [
        item.key
        for item in label.child("IMAGE").items
        if any(word in item.key for word in _VALUE_RULE_WORDS)
    ]


def _incidence_statement(label: Pds3Object) -> str:
    combined = _COMBINED_LOOK.search(_collapse(_label_text(label, "DESCRIPTION") or ""))
    return (
        "per-pixel incidence is not recoverable from the mosaic: the label states no "
        "incidence angle, and the mosaic combines several acquisitions "
        f"({label.require('START_TIME').raw} to {label.require('STOP_TIME').raw})"
        + (" with East- and West-looking passes" if combined else "")
        + ", so each pixel's viewing geometry is not known"
    )


@dataclass(frozen=True)
class _Layer:
    """One mosaic label as read: its mosaic_layers entry and what a window read needs.

    ``window`` is the Site01 window in this layer's own grid, or None when the Site01 extent is
    missing or the layer does not cover it.
    """

    file_name: str
    entry: Mapping[str, Any]
    label: Pds3Object
    layout: Pds3ImageLayout
    projection: Pds3PolarStereographic
    quantity: str
    window: tuple[int, int, int, int] | None
    inventory: dict[str, Any]

    @property
    def grid(self) -> tuple[Any, ...]:
        """What two layers share when they are one mosaic: grid, look and acquisition times."""
        return (
            self.projection.crs.srs,
            tuple(self.projection.transform)[:6],
            self.layout.lines,
            self.layout.line_samples,
            self.inventory["look_direction"],
            self.inventory["combined_east_west"],
            _label_text(self.label, "START_TIME"),
            _label_text(self.label, "STOP_TIME"),
        )


def _pixel_window(window: tuple[int, int, int, int]) -> dict[str, int]:
    col_off, row_off, width, height = window
    return {"col_off": col_off, "row_off": row_off, "width": width, "height": height}


@dataclass(frozen=True)
class MinirfSource:
    """The Mini-RF south-polar CPR mosaic, LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0 (PRD 5 row 2).

    ``fetch`` locates volume ``volume`` through the PDS Geosciences Node landing page and its
    directory pages, and downloads the data set's catalog files, the volume errata and every
    detached label in DATA/SAR/MOSAICS. Then it reads only the image rows that hold the Site01
    window of ``cpr_product`` (the file PRD 5 names as a lead), with one HTTP range request. The
    window is the smallest set of mosaic pixels that covers the Site01 extent T1-03 recorded in
    ``site_record``, converted with pyproj into the projection the label states. Where S1 and
    S4 layers on one grid (same projection, size, look and acquisition times) cover Site01, the
    Site01 window rows of both are read the same way, in that grid. No mosaic image is
    downloaded whole. If the server ignores Range, fetch records that as the blocking limitation
    and fails; if the Site01 extent is missing, no rows are read and the record is PARTIAL with
    that reason. An S4 layer over Site01 without exactly one S1 layer on its grid stops the
    fetch, because its raw statistics could not be recorded faithfully.

    ``build`` checks every PRD 5 layout lead against the label, decodes the window with the
    label's layout and masks it with quality.validity_mask. It records raw CPR statistics, and
    establishes from the labels which Stokes layers exist. Raw S1 and S4 statistics are
    recorded where both were read; otherwise the Stokes observation is recorded as not
    performed, with the reason. No S4-sign verdict and no Stokes validity rule are computed
    here (T2).
    """

    landing_url: str = "https://pds-geosciences.wustl.edu/missions/lro/mrf.htm"
    volume: str = "LROMRF_0005"
    data_set_id: str = "LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0"
    cpr_product: str = "LSZ_XXXXX_3CP_PIU_90S000_V1"
    site_record: str = "outputs/pilot/pgda78_site01.json"
    whole_file_limit_bytes: int = WHOLE_FILE_LIMIT_BYTES

    @property
    def name(self) -> str:
        return "minirf"

    @property
    def record_prefix(self) -> str:
        return f"MINIRF/{self.volume}/"

    @property
    def cpr_label_name(self) -> str:
        return f"{self.cpr_product.lower()}.lbl"

    # -- fetch --

    def _volume_url(self, landing: str) -> str:
        target = "/" + self.volume.lower()
        found = sorted(
            {
                link.rstrip("/") + "/"
                for link in _page_links(landing, self.landing_url)
                if urllib.parse.urlsplit(link).path.lower().rstrip("/").endswith(target)
            }
        )
        if len(found) != 1:
            raise PilotError(
                f"{self.landing_url} links {len(found)} URLs to volume {self.volume}, expected one"
            )
        return _public(found[0], self.landing_url)

    def fetch(
        self, raw_dir: Path, download: Downloader, read_range: RangeFetcher, now: Clock
    ) -> None:
        root = raw_dir.parent.parent.parent
        raw_dir.mkdir(parents=True, exist_ok=True)
        blocked: RangeNotSupportedError | None = None
        with tempfile.TemporaryDirectory(dir=raw_dir, prefix=".fetch-") as staging_name:
            staging = Path(staging_name)
            documents: list[dict[str, Any]] = []

            def document(role: str, url: str, file_name: str, magic: tuple[bytes, ...] = ()) -> str:
                fetched = download(
                    url, staging / file_name, max_bytes=self.whole_file_limit_bytes, magic=magic
                )
                documents.append(_fetch_entry(fetched, now(), role=role))
                return _decode(staging / file_name)

            landing = document("landing_page", self.landing_url, "landing_page.html")
            volume_url = self._volume_url(landing)
            volume_page = document("volume_index", volume_url, "volume_index.html")
            catalog_url = _child_link(volume_page, volume_url, "catalog/")
            data_url = _child_link(volume_page, volume_url, "data/")
            errata_url = _child_link(volume_page, volume_url, "errata.txt")
            catalog_page = document("catalog_index", catalog_url, "catalog_index.html")
            for role, file_name in _MINIRF_CATALOG_FILES:
                url = _child_link(catalog_page, catalog_url, file_name)
                document(role, url, file_name, _PDS_MAGIC)
            document("errata", errata_url, "errata.txt", _PDS_MAGIC)
            data_page = document("data_index", data_url, "data_index.html")
            sar_url = _child_link(data_page, data_url, "sar/")
            sar_page = document("sar_index", sar_url, "sar_index.html")
            mosaics_url = _child_link(sar_page, sar_url, "mosaics/")
            mosaics_page = document("mosaics_index", mosaics_url, "mosaics_index.html")
            listed = _directory_files(mosaics_page, mosaics_url)
            label_links = {n: u for n, u in listed.items() if n.lower().endswith(".lbl")}
            if self.cpr_label_name not in label_links:
                raise PilotError(f"{mosaics_url} does not list {self.cpr_label_name}")
            files: list[dict[str, Any]] = []
            for file_name, url in sorted(label_links.items()):
                fetched = download(
                    url,
                    staging / file_name,
                    max_bytes=self.whole_file_limit_bytes,
                    magic=_PDS_MAGIC,
                )
                files.append(_fetch_entry(fetched, now(), role="mosaic_label"))
            label = self._parse_label(staging / self.cpr_label_name)
            layout = pds3_image_layout(label)
            projection = pds3_polar_stereographic(label)
            image_name, image_url = self._image(layout, listed, mosaics_url, self.cpr_label_name)
            site = self._site_window(root, projection)
            pairs = self._stokes_pairs([self._layer(staging, entry, site) for entry in files])
            window: dict[str, Any]
            if isinstance(site, str):
                window = {"status": "not_located", "reason": site}
            else:
                _, row_off, _, height = site.window
                last_row = row_off + height - 1
                try:
                    response = fetch_pds3_rows(
                        image_url, layout, row_off, last_row, fetch=read_range
                    )
                except RangeNotSupportedError as error:
                    blocked = error
                    window = {
                        "status": "range_not_supported",
                        "reason": (
                            f"{error}; the window was not read and the mosaic was not "
                            "downloaded whole, so no CPR value is recorded"
                        ),
                        **site.fetch_fields(),
                    }
                else:
                    local_name = f"{image_name}{_ROWS_SUFFIX}"
                    (staging / local_name).write_bytes(response.data)
                    files.append(
                        _rows_entry(
                            response,
                            local_name,
                            now(),
                            role="cpr_window_rows",
                            provider_file_name=image_name,
                            rows={"first": row_off, "last": last_row},
                        )
                    )
                    window = {"status": "read", "reason": None, **site.fetch_fields()}
            stokes: dict[str, Any]
            if not pairs:
                stokes = {
                    "status": "not_requested",
                    "reason": "no pair of S1 and S4 layers on one grid covers Site01",
                }
            elif window["status"] != "read":
                stokes = {
                    "status": "not_requested",
                    "reason": (
                        f"the CPR window was not read ({window['status']}), so the S1 and S4 "
                        "windows were not requested"
                    ),
                }
            else:
                # Pair by pair, S1 then S4. A refusal stops the reads; the windows read before
                # it are kept with their HTTP metadata.
                stokes_layers = [layer for pair in pairs for layer in pair]
                stokes = {"status": "read", "reason": None}
                for layer in stokes_layers:
                    try:
                        files.append(
                            self._read_window_rows(
                                layer, listed, mosaics_url, staging, read_range, now
                            )
                        )
                    except RangeNotSupportedError as error:
                        blocked = error
                        kept = stokes_layers.index(layer)
                        stokes = {
                            "status": "range_not_supported",
                            "reason": (
                                f"{error}; {kept} of the {len(stokes_layers)} S1 and S4 windows "
                                "had been read before this answer and are kept, the others were "
                                "not requested, and no mosaic was downloaded whole"
                            ),
                            "refused_label_file_name": layer.file_name,
                        }
                        break
            metadata = {
                "source": self.name,
                "command": f"{COMMAND} fetch {self.name}",
                "landing_url": self.landing_url,
                "volume_url": volume_url,
                "mosaics_url": mosaics_url,
                "whole_file_limit_bytes": self.whole_file_limit_bytes,
                "documents": documents,
                "files": files,
                "image": {"file_name": image_name, "url": image_url},
                "images_not_downloaded": sorted(
                    name for name in listed if name.lower().endswith(".img")
                ),
                "window": window,
                "stokes": stokes,
            }
            (raw_dir / FETCH_METADATA).unlink(missing_ok=True)
            for entry in [*documents, *files]:
                os.replace(staging / entry["file_name"], raw_dir / entry["file_name"])
            _write_atomically(raw_dir / FETCH_METADATA, canonical_json(metadata))
        if blocked is not None:
            raise PilotError(
                f"{blocked}. This is recorded in data/raw/{self.name}/{FETCH_METADATA} as the "
                "blocking limitation; the mosaic was not downloaded whole"
            )

    def _parse_label(self, path: Path) -> Pds3Object:
        try:
            return parse_pds3_label(_decode(path))
        except ValueError as error:
            raise PilotError(f"{path.name} cannot be read as a PDS3 label: {error}") from error

    def _image(
        self,
        layout: Pds3ImageLayout,
        listed: Mapping[str, str],
        mosaics_url: str,
        label_name: str,
    ) -> tuple[str, str]:
        """The listed image file the label's ^IMAGE names (the server's names are lower case)."""
        found = [name for name in listed if name.lower() == layout.file_name.lower()]
        if len(found) != 1:
            raise PilotError(
                f"{mosaics_url} lists {len(found)} files named {layout.file_name} (the image "
                f"{label_name} points to), expected one"
            )
        return found[0], listed[found[0]]

    def _read_window_rows(
        self,
        layer: _Layer,
        listed: Mapping[str, str],
        mosaics_url: str,
        staging: Path,
        read_range: RangeFetcher,
        now: Clock,
    ) -> dict[str, Any]:
        """Range-read the Site01 window rows of a Stokes layer into ``staging``."""
        if layer.window is None:
            raise PilotError(f"{layer.file_name} does not cover Site01, so it has no window")
        name, url = self._image(layer.layout, listed, mosaics_url, layer.file_name)
        _, row_off, _, height = layer.window
        last_row = row_off + height - 1
        response = fetch_pds3_rows(url, layer.layout, row_off, last_row, fetch=read_range)
        local_name = f"{name}{_ROWS_SUFFIX}"
        (staging / local_name).write_bytes(response.data)
        return _rows_entry(
            response,
            local_name,
            now(),
            role="stokes_window_rows",
            quantity=layer.quantity,
            label_file_name=layer.file_name,
            provider_file_name=name,
            rows={"first": row_off, "last": last_row},
            pixel_window=_pixel_window(layer.window),
        )

    def _stokes_pairs(self, layers: Sequence[_Layer]) -> list[tuple[_Layer, _Layer]]:
        """Each S4 layer that covers Site01 with the one S1 layer on its grid, by S4 file name."""
        pairs = []
        for s4 in sorted(
            (layer for layer in layers if layer.quantity == "S4" and layer.window is not None),
            key=lambda layer: layer.file_name,
        ):
            partners = [
                layer
                for layer in layers
                if layer.quantity == "S1" and layer.window is not None and layer.grid == s4.grid
            ]
            if len(partners) != 1:
                raise PilotError(
                    f"{s4.file_name} is an S4 layer that covers Site01, but {len(partners)} S1 "
                    "layers share its grid, look direction and acquisition times, expected one; "
                    "its raw S1 and S4 statistics cannot be recorded faithfully"
                )
            pairs.append((partners[0], s4))
        return pairs

    def _site_window(self, root: Path, projection: Pds3PolarStereographic) -> _SiteWindow | str:
        """The window covering the Site01 extent, or why it cannot be located."""
        path = root / self.site_record
        reason = (
            "{}: T1-03 (pgda78) has not produced the Site01 extent, so the Site01 window "
            "cannot be located in the mosaic"
        )
        if not path.is_file():
            return reason.format(f"{self.site_record} is missing")
        try:
            record = json.loads(path.read_bytes())
            (height,) = [entry for entry in record["files"] if entry.get("role") == "height"]
            extent = {
                key: float(height["extent_m"][key]) for key in ("left", "bottom", "right", "top")
            }
            wkt, file_name = str(height["crs_wkt"]), str(height["file_name"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return reason.format(
                f"{self.site_record} does not state the height map's extent_m and crs_wkt"
            )
        try:
            bounds = _converted_bounds(extent, wkt, projection.crs)
            window = projection.pixel_window(*bounds)
        except (ValueError, pyproj.exceptions.ProjError) as error:
            raise PilotError(
                f"the Site01 extent in {self.site_record} cannot be placed in the mosaic: {error}"
            ) from error
        return _SiteWindow(
            site_extent={
                "source": self.site_record,
                "file_name": file_name,
                "crs_wkt": wkt,
                "extent_m": extent,
            },
            bounds=bounds,
            window=window,
        )

    # -- build --

    def build(self, raw_dir: Path, root: Path) -> Build:
        fetched = _load_fetch(raw_dir, self.name)
        try:
            return self._build(raw_dir, root, fetched)
        except (KeyError, TypeError) as error:
            raise PilotError(
                f"data/raw/{self.name}/{FETCH_METADATA} lacks an expected entry ({error!r}); "
                f"run: {COMMAND} fetch {self.name}"
            ) from error

    def _build(self, raw_dir: Path, root: Path, fetched: Mapping[str, Any]) -> Build:
        documents = {entry["role"]: entry for entry in fetched["documents"]}
        label_entries = {
            entry["file_name"]: entry
            for entry in fetched["files"]
            if entry["role"] == "mosaic_label"
        }
        rows_entry = next(
            (entry for entry in fetched["files"] if entry["role"] == "cpr_window_rows"), None
        )
        texts = {
            role: _decode(raw_dir / documents[role]["file_name"])
            for role in ("dsmap", "data_set_catalog", "instrument_catalog", "errata")
        }
        catalog = self._parse_label(raw_dir / documents["data_set_catalog"]["file_name"])
        label_entry = label_entries[self.cpr_label_name]
        label = self._parse_label(raw_dir / self.cpr_label_name)
        try:
            layout = pds3_image_layout(label)
            projection = pds3_polar_stereographic(label)
        except ValueError as error:
            raise PilotError(f"{self.cpr_label_name}: {error}") from error
        image = fetched["image"]
        site = self._site_window(root, projection)
        window_meta = fetched["window"]
        located_at_fetch = window_meta["status"] != "not_located"
        if isinstance(site, str) == located_at_fetch or (
            not isinstance(site, str)
            and {key: window_meta[key] for key in ("site_extent", "pixel_window")}
            != json.loads(json.dumps(site.fetch_fields()))
        ):
            raise PilotError(
                f"the Site01 window from {self.site_record} differs from the one the fetch used; "
                f"run: {COMMAND} fetch {self.name}"
            )
        if (rows_entry is None) == (window_meta["status"] == "read"):
            raise PilotError(
                f"data/raw/{self.name}/{FETCH_METADATA} lists window rows that do not match its "
                f"window status; run: {COMMAND} fetch {self.name}"
            )
        self._check_stored_values(label, layout, self.cpr_label_name)
        special = self._special_values(layout, self.cpr_label_name)
        layers = [self._layer(raw_dir, entry, site) for _, entry in sorted(label_entries.items())]
        pairs = self._stokes_pairs(layers)
        stokes_rows = self._stokes_rows(fetched, pairs)
        stokes = self._stokes_observation(
            layers,
            pairs,
            fetched["stokes"],
            stokes_rows,
            raw_dir,
            str(fetched["mosaics_url"]),
            site,
        )
        convention = self._convention(label, texts["dsmap"], documents["dsmap"])
        description = _collapse(_label_text(label, "DESCRIPTION") or "")
        combined = _COMBINED_LOOK.search(description)
        units, units_source = self._units(label)
        statistics = (
            self._cpr_statistics(raw_dir, rows_entry, layout, site)
            if rows_entry is not None and not isinstance(site, str)
            else {"status": "not_computed", "reason": window_meta["reason"]}
        )
        leads = self._leads(label, layout, projection, image["file_name"], fetched, combined)
        frequency = label.get("CENTER_FREQUENCY")
        s_band = _S_BAND_SENTENCE.search(_collapse(texts["instrument_catalog"]))
        transmit = _TRANSMIT_SENTENCE.search(_collapse(texts["errata"]))
        handedness_items = _handedness_items(label)
        incidence_items = [item.key for item in _all_items(label) if "INCIDENCE" in item.key]
        look = label.get("LOOK_DIRECTION")
        docs = _MosaicDocuments(
            s_band=s_band,
            pa=_PA_SENTENCE.search(_collapse(texts["data_set_catalog"])),
            citation=_find_text(catalog, "CITATION_DESC"),
            catalog_licence="licen" in texts["data_set_catalog"].lower(),
        )
        read_rows = dict(stokes_rows)
        if rows_entry is not None:
            read_rows[self.cpr_label_name] = rows_entry
        # A Stokes window is decoded only with its pair's other window; one read before a
        # range refusal without its partner is stored as fetched (stokes_observation).
        decoded = {self.cpr_label_name} if rows_entry is not None else set()
        decoded |= {
            layer.file_name
            for pair in pairs
            if all(member.file_name in stokes_rows for member in pair)
            for layer in pair
        }
        records: list[ProductRecord] = []
        verification: dict[str, dict[str, str]] = {}
        for layer in layers:
            fields = self._record_fields(
                layer,
                raw_dir,
                docs,
                read=layer.file_name in read_rows,
                decoded=layer.file_name in decoded,
            )
            label_local = f"data/raw/{self.name}/{layer.file_name}"
            label_record = ProductRecord.model_validate(
                {
                    **fields,
                    "product_id": f"{self.record_prefix}{layer.file_name}",
                    "source_url": layer.entry["url"],
                    "local_path": label_local,
                    "checksum": sha256_file(root / label_local),
                }
            )
            records.append(label_record)
            verification[label_record.product_id] = {
                "status": "PARTIAL",
                "basis": self._label_basis(
                    layer.file_name, layer.file_name in read_rows, layer.file_name in decoded
                ),
            }
            rows = read_rows.get(layer.file_name)
            if rows is None:
                continue
            rows_local = f"data/raw/{self.name}/{rows['file_name']}"
            image_record = ProductRecord.model_validate(
                {
                    **fields,
                    "product_id": f"{self.record_prefix}{rows['provider_file_name']}",
                    "source_url": rows["url"],
                    "local_path": rows_local,
                    "checksum": sha256_file(root / rows_local),
                }
            )
            records.append(image_record)
            verification[image_record.product_id] = {
                "status": "PARTIAL",
                "basis": (
                    (
                        "the Site01 window rows read by one HTTP range request (206), decoded "
                        "with the label's layout and masked with quality.validity_mask"
                        + (
                            ""
                            if layer.file_name == self.cpr_label_name
                            else " (stokes_observation)"
                        )
                        if layer.file_name in decoded
                        else "the Site01 window rows read by one HTTP range request (206) and "
                        "stored as fetched, but not decoded, because the other window of its S1 "
                        "and S4 pair was not read after the server refused a range request "
                        "(stokes_observation.pairs_not_observed)"
                    )
                    + "; not plotted, so the PRD 5 entry rule is not complete"
                ),
            }
        by_id = {item.product_id: item for item in records}
        label_local = f"data/raw/{self.name}/{self.cpr_label_name}"
        range_read: dict[str, Any]
        if rows_entry is not None:
            rows_local = f"data/raw/{self.name}/{rows_entry['file_name']}"
            range_read = {
                "status": "read",
                "reason": None,
                "url": rows_entry["url"],
                "final_url": rows_entry["final_url"],
                "requested_range": rows_entry["requested_range"],
                "http_status": rows_entry["http_status"],
                "content_range": rows_entry["content_range"],
                "byte_ranges": [[rows_entry["first_byte"], rows_entry["last_byte"]]],
                "bytes_received": rows_entry["bytes_received"],
                "remote_bytes": rows_entry["remote_bytes"],
                "content_type": rows_entry["content_type"],
                "last_modified": rows_entry["last_modified"],
                "etag": rows_entry["etag"],
                "retrieved_at": rows_entry["retrieved_at"],
                "local_path": rows_local,
                "local_sha256": by_id[f"{self.record_prefix}{image['file_name']}"].checksum,
                "whole_file_downloaded": False,
                "note": (
                    "whole image rows are read; only the window's columns are decoded into the "
                    "statistics, the other columns are stored but not used"
                ),
            }
        else:
            range_read = {
                "status": (
                    "range_not_supported"
                    if window_meta["status"] == "range_not_supported"
                    else "not_requested"
                ),
                "reason": window_meta["reason"],
                "url": image["url"],
                "whole_file_downloaded": False,
            }
        open_items = self._open_items(layers, pairs, fetched["stokes"], stokes_rows)
        if rows_entry is None:
            open_items.append(
                {
                    "item": "Read the Site01 window of the CPR mosaic",
                    "reason": window_meta["reason"],
                }
            )
        retrieved = sorted(
            entry["retrieved_at"] for entry in [*fetched["documents"], *fetched["files"]]
        )
        incidence = _incidence_statement(label)
        result = {
            "record": "minirf_mosaic",
            "description": (
                "The Mini-RF south-polar CPR mosaic over the Site01 window, as fetched and decoded "
                "by the T1 pilot (PRD 5 rows 1 and 2, pilot step 3). Raw observations only: no "
                "S4-sign verdict (T2-05), no Stokes validity rule (T2-02), and the shipping mode "
                "and the G0 status are a human checkpoint (PRD 23.6) and are not recorded here."
            ),
            "source": self.name,
            "data_set_id": _label_text(label, "DATA_SET_ID"),
            "volume": self.volume,
            "product_id": _label_text(label, "PRODUCT_ID"),
            "located_through": {
                "landing_page": fetched["landing_url"],
                "volume_url": fetched["volume_url"],
                "mosaics_url": fetched["mosaics_url"],
            },
            "documents": [
                {
                    key: entry[key]
                    for key in (
                        "role",
                        "file_name",
                        "url",
                        "http_status",
                        "content_type",
                        "bytes_received",
                        "sha256",
                        "last_modified",
                        "retrieved_at",
                    )
                }
                | {"local_path": f"data/raw/{self.name}/{entry['file_name']}"}
                for entry in fetched["documents"]
            ],
            "retrieval": {
                "first_retrieved_at": retrieved[0],
                "last_retrieved_at": retrieved[-1],
                "fetch_metadata": f"data/raw/{self.name}/{FETCH_METADATA}",
                "command": fetched["command"],
            },
            "label": {
                "file_name": self.cpr_label_name,
                "url": label_entry["url"],
                "local_path": label_local,
                "sha256": by_id[f"{self.record_prefix}{self.cpr_label_name}"].checksum,
                "bytes": label_entry["bytes_received"],
                "http_status": label_entry["http_status"],
                "retrieved_at": label_entry["retrieved_at"],
                "note": _label_text(label, "NOTE"),
                "description": description,
                "product_version_id": _label_text(label, "PRODUCT_VERSION_ID"),
                "product_creation_time": _label_text(label, "PRODUCT_CREATION_TIME"),
                "label_revision_note": _label_text(label, "LABEL_REVISION_NOTE"),
                "software_name": _label_text(label, "SOFTWARE_NAME"),
                "start_time": _label_text(label, "START_TIME"),
                "stop_time": _label_text(label, "STOP_TIME"),
            },
            "data": {
                "file_name": image["file_name"],
                "url": image["url"],
                "remote_bytes": None if rows_entry is None else rows_entry["remote_bytes"],
                "label_file_bytes": layout.offset_bytes + layout.image_bytes,
                "size_matches_label": (
                    None
                    if rows_entry is None
                    else rows_entry["remote_bytes"] == layout.offset_bytes + layout.image_bytes
                ),
                "whole_file_downloaded": False,
                "images_not_downloaded": fetched["images_not_downloaded"],
            },
            "layout": {
                "lines": layout.lines,
                "line_samples": layout.line_samples,
                "bands": layout.bands,
                "sample_type": layout.sample_type,
                "sample_bits": layout.sample_bits,
                "dtype": layout.dtype.str,
                "byte_order": _BYTE_ORDER[layout.dtype.str[0]],
                "offset_bytes": layout.offset_bytes,
                "line_bytes": layout.line_bytes,
                "record_bytes": layout.record_bytes,
                "image_bytes": layout.image_bytes,
                "scaling_factor": layout.scaling_factor,
                "value_offset": layout.value_offset,
                "scaling_applied": False,
                "source": (
                    "the label's ^IMAGE pointer and IMAGE object; PDS3 Standards Reference "
                    "appendix C for the SAMPLE_TYPE byte order"
                ),
            },
            "units": {"value": units, "source": units_source},
            "null_value": special[0],
            "special_values": special[1:],
            "layout_leads": leads,
            "projection": self._projection(
                projection, label.child("IMAGE_MAP_PROJECTION"), convention
            ),
            "window": self._window_record(site, window_meta, projection, rows_entry),
            "range_read": range_read,
            "cpr_statistics": statistics,
            "transmit_handedness": {
                "stated_by_product": (
                    "; ".join(f"{item.key} = {item.raw}" for item in handedness_items)
                    if handedness_items
                    else "not stated"
                ),
                "label_keywords_searched": (
                    "every keyword of the label containing " + ", ".join(_HANDEDNESS_WORDS)
                ),
                "volume_documentation": (
                    []
                    if transmit is None
                    else [{"source": documents["errata"]["url"], "text": transmit.group(0)}]
                ),
                "note": (
                    "PRD 15: sources disagree on the Mini-RF transmit handedness, so the "
                    "ProductRecord stores what the product states; the convention is settled "
                    "at G1, not assumed here"
                ),
            },
            "look_direction": {
                "mixed": True if combined else (False if look is not None else None),
                "label_text": None if combined is None else combined.group(0),
                "look_direction_keyword": None if look is None else look.raw,
                "single_look_mosaics_covering_site01": [
                    layer.file_name
                    for layer in layers
                    if layer.inventory["look_direction"] is not None
                    and layer.quantity == "CPR"
                    and layer.inventory["covers_site01"] is True
                ],
            },
            "incidence": {
                "statement": incidence,
                "label_incidence_keywords": incidence_items,
            },
            "frequency": {
                "label_raw": None if frequency is None else frequency.raw,
                "instrument_catalog_text": None if s_band is None else s_band.group(0),
                "instrument_catalog_source": documents["instrument_catalog"]["url"],
                "note": self._frequency_note(frequency, s_band),
            },
            "mosaic_layers": [layer.inventory for layer in layers],
            "stokes_observation": stokes,
            "open_g0_items": open_items,
            "verification": verification,
            "field_notes": self._field_notes(),
            "tool_versions": {
                **tool_versions(),
                "pyproj": importlib.metadata.version("pyproj"),
                "proj": str(pyproj.proj_version_str),
            },
        }
        return Build(
            files={f"outputs/pilot/{self.name}_mosaic.json": canonical_json(result)},
            records=tuple(sorted(records, key=lambda r: r.product_id)),
            record_prefix=self.record_prefix,
        )

    def _label_basis(self, file_name: str, read: bool, decoded: bool) -> str:
        """Why a label's ProductRecord is PARTIAL, and what was done with it."""
        if file_name == self.cpr_label_name:
            return (
                "downloaded and read as a PDS3 label; every PRD 5 layout lead is checked "
                "against it (layout_leads)"
            )
        if decoded:
            return (
                "downloaded and read as a PDS3 label; its Site01 window was read "
                "(stokes_observation)"
            )
        if read:
            return (
                "downloaded and read as a PDS3 label; its Site01 window rows were read and "
                "stored but not decoded, because the other window of its S1 and S4 pair was "
                "not read (stokes_observation.pairs_not_observed)"
            )
        return (
            "downloaded and read as a PDS3 label to establish the mosaic layers "
            "(mosaic_layers); its image was not read"
        )

    def _units(self, label: Pds3Object) -> tuple[str, str]:
        """The units of a layer's values and where they come from, as its label states them."""
        unit = label.child("IMAGE").get("UNIT")
        if unit is not None:
            value = unit.text() if isinstance(unit.value, str) else unit.raw
            return value, f"the label's IMAGE object: UNIT = {unit.raw}"
        description = _collapse(_label_text(label, "DESCRIPTION") or "")
        cpr = _CPR_DEFINITION.search(description)
        if cpr is not None:
            return (
                f"dimensionless ratio: the label defines {cpr.group(0)} and states no unit",
                "the label's DESCRIPTION; the IMAGE object has no UNIT keyword",
            )
        stokes = _STOKES_DEFINITION.search(description)
        if stokes is not None:
            return UNKNOWN, (
                "the label's IMAGE object has no UNIT keyword, and its DESCRIPTION defines "
                f"'{stokes.group(0)}' without a unit"
            )
        return UNKNOWN, "the label states no UNIT keyword and no definition of its values"

    def _record_fields(
        self, layer: _Layer, raw_dir: Path, docs: _MosaicDocuments, *, read: bool, decoded: bool
    ) -> dict[str, Any]:
        """The ProductRecord fields a mosaic label states, shared by its label and image rows.

        ``read``: the image's Site01 window rows were fetched; ``decoded``: they were also
        decoded and masked.
        """
        label, layout, projection = layer.label, layer.layout, layer.projection
        map_block = label.child("IMAGE_MAP_PROJECTION")
        frequency = label.get("CENTER_FREQUENCY")
        handedness = _handedness_items(label)
        licence = docs.catalog_licence or "licen" in _decode(raw_dir / layer.file_name).lower()
        special = ", ".join(f"{key} = {raw}" for key, raw in layout.special_raw.items())
        declared = (
            f"ISIS special pixels as the label's IMAGE object declares them ({special})"
            if special
            else "the label's IMAGE object declares no special pixels"
        )
        if decoded:
            qa = (
                f"{declared}, masked by quality.validity_mask; the label defines no other "
                "invalid value"
            )
        else:
            rules = _value_rules(label)
            qa = (
                declared
                + (
                    f"; the label also declares {', '.join(rules)}"
                    if rules
                    else "; the label defines no other invalid value"
                )
                + (
                    "; the Site01 window rows were read and stored but not decoded, so nothing "
                    "was masked"
                    if read
                    else "; the image was not read, so nothing was masked"
                )
            )
        return {
            "provider": (
                f"{_label_text(label, 'PRODUCER_INSTITUTION_NAME')} "
                f"({_label_text(label, 'PRODUCER_FULL_NAME')}), as the label states; archived "
                f"at the PDS Geosciences Node, volume {self.volume}"
            ),
            "version": f"PRODUCT_VERSION_ID {label.require('PRODUCT_VERSION_ID').raw} (label)",
            "body": "moon",
            "acquisition_time": (
                f"{label.require('START_TIME').raw} to {label.require('STOP_TIME').raw} (label "
                "START_TIME and STOP_TIME; the mosaic combines several acquisitions)"
            ),
            "frequency_or_wavelength": (
                UNKNOWN
                if frequency is None
                else f"CENTER_FREQUENCY = {frequency.raw}, as the label states it"
                + (
                    ""
                    if docs.s_band is None
                    else f"; the instrument catalog states '{docs.s_band.group(0)}'"
                )
            ),
            "transmit_receive_convention": (
                "; ".join(f"{item.key} = {item.raw}" for item in handedness)
                if handedness
                else UNKNOWN
            ),
            "units": self._units(label)[0],
            "calibration_type": f"DATA_SET_NAME '{_label_text(label, 'DATA_SET_NAME')}' (label)",
            "native_grid": (
                f"{layout.lines} x {layout.line_samples} {layout.sample_type} "
                f"{layout.sample_bits}-bit samples, polar stereographic, MAP_RESOLUTION = "
                f"{map_block.require('MAP_RESOLUTION').raw}, MAP_SCALE = "
                f"{map_block.require('MAP_SCALE').raw}, SAMPLE_PROJECTION_OFFSET = "
                f"{map_block.require('SAMPLE_PROJECTION_OFFSET').raw}, LINE_PROJECTION_OFFSET = "
                f"{map_block.require('LINE_PROJECTION_OFFSET').raw} (label)"
            ),
            "effective_resolution": UNKNOWN,
            "crs_and_frame": (
                f"{projection.crs.srs}, from the label's IMAGE_MAP_PROJECTION "
                f"(COORDINATE_SYSTEM_NAME = {map_block.require('COORDINATE_SYSTEM_NAME').raw}, "
                f"COORDINATE_SYSTEM_TYPE = {map_block.require('COORDINATE_SYSTEM_TYPE').raw})"
                + ("" if docs.pa is None else f"; the data set catalog states '{docs.pa.group(0)}'")
            ),
            "incidence_source": _incidence_statement(label),
            "qa_definition": qa,
            "license_or_attribution": (
                UNKNOWN
                if docs.citation is None
                else f"Cite the data set as its catalog asks (CITATION_DESC): '{docs.citation}'"
                + (
                    ""
                    if licence
                    else "; neither the label nor the data set catalog states a licence"
                )
            ),
            "verification_status": "PARTIAL",
        }

    def _check_stored_values(self, label: Pds3Object, layout: Pds3ImageLayout, name: str) -> None:
        """Refuse a layer whose values need a rule the pilot does not apply."""
        rules = _value_rules(label)
        if rules:
            raise PilotError(
                f"{name} declares {', '.join(rules)}; the pilot applies no value rule beyond the "
                "special pixels, so it cannot record this product faithfully"
            )
        if layout.scaling_factor != 1.0 or layout.value_offset != 0.0:
            raise PilotError(
                f"{name} states SCALING_FACTOR {layout.scaling_factor} and OFFSET "
                f"{layout.value_offset}; the pilot records stored values only"
            )

    def _special_values(self, layout: Pds3ImageLayout, name: str) -> list[dict[str, Any]]:
        """CORE_NULL first, then the saturation codes, each with the mask reason it gets."""
        if "CORE_NULL" not in layout.special_values:
            raise PilotError(f"{name} declares no CORE_NULL")
        result = []
        for key, value in layout.special_values.items():
            if key == "CORE_NULL":
                reason = InvalidReason.NODATA.label
            elif value in ISIS_SATURATION_REAL4:
                reason = InvalidReason.ISIS_SATURATION.label
            else:
                raise PilotError(
                    f"{name} declares {key} = {layout.special_raw[key]}, which is "
                    "not one of the ISIS saturation codes quality.validity_mask masks"
                )
            result.append(
                {
                    "keyword": key,
                    "raw": layout.special_raw[key],
                    "value": value,
                    "mask_reason": reason,
                }
            )
        result.sort(key=lambda item: item["keyword"] != "CORE_NULL")
        return result

    def _layer(self, raw_dir: Path, entry: Mapping[str, Any], site: _SiteWindow | str) -> _Layer:
        """One mosaic label as the labels describe it: quantity, pole, look and coverage."""
        name = str(entry["file_name"])
        label = self._parse_label(raw_dir / name)
        note = _collapse(_label_text(label, "NOTE") or "")
        found = [quantity for quantity, pattern in _QUANTITIES if pattern.search(note.lower())]
        if len(found) != 1:
            raise PilotError(
                f"{name}: the NOTE {note!r} does not name exactly one of CPR and S1 to S4, so the "
                "layers the mosaic provides cannot be established from the labels"
            )
        try:
            layout = pds3_image_layout(label)
            projection = pds3_polar_stereographic(label)
        except ValueError as error:
            raise PilotError(f"{name}: {error}") from error
        description = _collapse(_label_text(label, "DESCRIPTION") or "")
        covers: bool | None = None
        window: tuple[int, int, int, int] | None = None
        if isinstance(site, _SiteWindow):
            try:
                window = projection.pixel_window(
                    *_converted_bounds(
                        site.site_extent["extent_m"], site.site_extent["crs_wkt"], projection.crs
                    )
                )
                covers = True
            except (ValueError, pyproj.exceptions.ProjError):
                covers = False
        look = label.get("LOOK_DIRECTION")
        inventory = {
            "file_name": name,
            "url": entry["url"],
            "sha256": entry["sha256"],
            "product_id": _label_text(label, "PRODUCT_ID"),
            "data_set_id": _label_text(label, "DATA_SET_ID"),
            "note": note,
            "quantity": found[0],
            "center_latitude_deg": projection.center_latitude_deg,
            "look_direction": None if look is None else look.text(),
            "combined_east_west": _COMBINED_LOOK.search(description) is not None,
            "map_resolution_ppd": projection.map_resolution_ppd,
            "lines": layout.lines,
            "line_samples": layout.line_samples,
            "image_file": layout.file_name,
            "covers_site01": covers,
        }
        return _Layer(
            file_name=name,
            entry=entry,
            label=label,
            layout=layout,
            projection=projection,
            quantity=found[0],
            window=window,
            inventory=inventory,
        )

    def _stokes_rows(
        self, fetched: Mapping[str, Any], pairs: Sequence[tuple[_Layer, _Layer]]
    ) -> dict[str, Mapping[str, Any]]:
        """The fetched S1 and S4 window rows by label file name.

        They must be the pairs' layers in the order the fetch reads them (pair by pair, S1 then
        S4): all of them when the stokes status is read, those before the refused one when the
        server refused a range request, and none otherwise.
        """
        entries = [entry for entry in fetched["files"] if entry["role"] == "stokes_window_rows"]
        stokes_meta = fetched["stokes"]
        order = [layer.file_name for pair in pairs for layer in pair]
        expected: list[str] | None = []
        if stokes_meta["status"] == "read":
            expected = order
        elif stokes_meta["status"] == "range_not_supported":
            refused = stokes_meta["refused_label_file_name"]
            expected = order[: order.index(refused)] if refused in order else None
        if expected is None or [str(entry["label_file_name"]) for entry in entries] != expected:
            raise PilotError(
                f"data/raw/{self.name}/{FETCH_METADATA} lists S1 and S4 window rows that do not "
                "match the S1 and S4 layers covering Site01 or its stokes status; "
                f"run: {COMMAND} fetch {self.name}"
            )
        return {str(entry["label_file_name"]): entry for entry in entries}

    def _stokes_observation(
        self,
        layers: Sequence[_Layer],
        pairs: Sequence[tuple[_Layer, _Layer]],
        stokes_meta: Mapping[str, Any],
        rows: Mapping[str, Mapping[str, Any]],
        raw_dir: Path,
        mosaics_url: str,
        site: _SiteWindow | str,
    ) -> dict[str, Any]:
        verdict_owner = (
            "The S4-sign verdict is computed by polarimetry.lunar_s4_sign_check (T2-05) at "
            "integration"
        )
        if not pairs:
            return {
                "status": "not_performed",
                "reason": self._no_pair_reason(layers, mosaics_url, site),
            }
        note = (
            "Raw observations only: S1 and S4 are recorded as stored, with no scaling and no "
            "change of sign, and no S1 and |S4| validity rule is applied (T2-02). "
            f"{verdict_owner}. CPR is never used to infer S4"
        )
        if stokes_meta["status"] == "read":
            return {
                "status": "observed",
                "reason": None,
                "pairs": [self._stokes_pair(raw_dir, s1, s4, rows) for s1, s4 in pairs],
                "note": note,
            }
        reason = f"{stokes_meta['reason']}. CPR is never used to infer S4. {verdict_owner}"
        if stokes_meta["status"] != "range_not_supported":
            return {"status": "not_performed", "reason": reason}
        read = [all(layer.file_name in rows for layer in pair) for pair in pairs]
        return {
            "status": "incomplete" if any(read) else "not_performed",
            "reason": reason,
            "pairs": [
                self._stokes_pair(raw_dir, s1, s4, rows)
                for (s1, s4), complete in zip(pairs, read, strict=True)
                if complete
            ],
            "pairs_not_observed": [
                self._unobserved_pair(pair, rows, stokes_meta)
                for pair, complete in zip(pairs, read, strict=True)
                if not complete
            ],
            "note": (
                f"{note}. A pair is decoded only when both its S1 and S4 windows were read: the "
                "windows of pairs_not_observed that were read are stored as fetched "
                "(windows_read), not decoded, and carry no statistics"
            ),
        }

    def _unobserved_pair(
        self,
        pair: tuple[_Layer, _Layer],
        rows: Mapping[str, Mapping[str, Any]],
        stokes_meta: Mapping[str, Any],
    ) -> dict[str, Any]:
        """An S1 and S4 pair the refusal interrupted: the windows read, and why the others not."""
        s1, s4 = pair
        if s4.window is None:
            raise PilotError(f"{s4.file_name} does not cover Site01, so it has no window")
        refused = stokes_meta["refused_label_file_name"]
        return {
            "pixel_window": _pixel_window(s4.window),
            "s1_label_file_name": s1.file_name,
            "s4_label_file_name": s4.file_name,
            "windows_read": [
                self._window_provenance(layer, rows[layer.file_name])
                for layer in pair
                if layer.file_name in rows
            ],
            "windows_not_read": [
                {
                    "label_file_name": layer.file_name,
                    "reason": (
                        f"the server refused the range request: {stokes_meta['reason']}"
                        if layer.file_name == refused
                        else f"not requested after the server refused the range request for "
                        f"{refused}"
                    ),
                }
                for layer in pair
                if layer.file_name not in rows
            ],
        }

    def _window_provenance(self, layer: _Layer, entry: Mapping[str, Any]) -> dict[str, Any]:
        """Where the Site01 window rows of a Stokes layer came from, and where they are stored."""
        units, units_source = self._units(layer.label)
        return {
            "label_file_name": layer.file_name,
            "label_url": layer.entry["url"],
            "label_sha256": layer.entry["sha256"],
            "image_file_name": entry["provider_file_name"],
            "url": entry["url"],
            "http_status": entry["http_status"],
            "content_range": entry["content_range"],
            "byte_ranges": [[entry["first_byte"], entry["last_byte"]]],
            "bytes_received": entry["bytes_received"],
            "remote_bytes": entry["remote_bytes"],
            "retrieved_at": entry["retrieved_at"],
            "local_path": f"data/raw/{self.name}/{entry['file_name']}",
            "local_sha256": entry["sha256"],
            "units": {"value": units, "source": units_source},
        }

    def _no_pair_reason(
        self, layers: Sequence[_Layer], mosaics_url: str, site: _SiteWindow | str
    ) -> str:
        quantities = sorted({layer.quantity for layer in layers})
        s4 = [layer.file_name for layer in layers if layer.quantity == "S4"]
        tail = (
            "CPR is never used to infer S4. The S4-sign verdict is computed by "
            "polarimetry.lunar_s4_sign_check (T2-05) at integration, from a product with S4"
        )
        listed = f"the {len(layers)} {self.data_set_id} labels listed at {mosaics_url}"
        if s4:
            coverage = (
                "their coverage of Site01 is not established, because the extent is missing"
                if isinstance(site, str)
                else "none of them covers Site01"
            )
            return (
                f"{listed} describe S4 (fourth Stokes parameter) layers ({', '.join(s4)}), but "
                f"{coverage}, so the S4 sign cannot be observed over Site01 from this mosaic. "
                + tail
            )
        stokes = [layer for layer in layers if layer.quantity in _STOKES_QUANTITIES]
        if isinstance(site, str):
            coverage = "their coverage of Site01 is not established, because the extent is missing"
        else:
            covering = [layer.file_name for layer in stokes if layer.window is not None]
            coverage = f"{len(covering)} S1 layers cover Site01 ({', '.join(covering)})"
        return (
            f"{listed} describe {' and '.join(quantities)} layers only: {coverage}, but no label "
            "describes an S4 (fourth Stokes parameter) layer, so the S4 sign cannot be observed "
            f"from this mosaic. {tail}"
        )

    def _stokes_pair(
        self,
        raw_dir: Path,
        s1: _Layer,
        s4: _Layer,
        rows: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Raw S1 and S4 statistics over the Site01 window of one grid."""
        if s4.window is None or s1.window != s4.window:
            raise PilotError(f"{s1.file_name} and {s4.file_name} do not share a Site01 window")
        values: dict[str, npt.NDArray[Any]] = {}
        masks: dict[str, ValidityMask] = {}
        for key, layer in (("s1", s1), ("s4", s4)):
            self._check_stored_values(layer.label, layer.layout, layer.file_name)
            self._special_values(layer.layout, layer.file_name)
            values[key] = self._window_values(
                raw_dir, rows[layer.file_name], layer.layout, s4.window
            )
            masks[key] = validity_mask(values[key], nodata=layer.layout.special_values["CORE_NULL"])
        valid = masks["s1"].valid & masks["s4"].valid
        observed = {key: np.asarray(values[key][valid], dtype=np.float64) for key in ("s1", "s4")}

        def layer_record(key: str, layer: _Layer) -> dict[str, Any]:
            found = observed[key]
            return {
                **self._window_provenance(layer, rows[layer.file_name]),
                "invalid_by_reason": {
                    reason.label: count
                    for reason, count in masks[key].reason_counts().items()
                    if reason is not InvalidReason.VALID
                },
                "min": float(np.min(found)) if found.size else None,
                "max": float(np.max(found)) if found.size else None,
            }

        s4_values = observed["s4"]
        return {
            "pixel_window": _pixel_window(s4.window),
            "s1": layer_record("s1", s1),
            "s4": layer_record("s4", s4),
            "mask": (
                "quality.validity_mask of each layer with nodata = its label's CORE_NULL and the "
                "ISIS saturation codes its label declares as isis_saturation; a pixel counts "
                "when it is valid in both S1 and S4"
            ),
            "values": (
                "the stored samples of the window's columns, unscaled (SCALING_FACTOR 1.0 and "
                "OFFSET 0.0 in both labels); min and max are over the pixels valid in both"
            ),
            "valid_pixels": int(np.count_nonzero(valid)),
            "total_pixels": int(valid.size),
            "s4_positive_pixels": int(np.count_nonzero(s4_values > 0)),
            "s4_negative_pixels": int(np.count_nonzero(s4_values < 0)),
            "s4_zero_pixels": int(np.count_nonzero(s4_values == 0)),
        }

    def _open_items(
        self,
        layers: Sequence[_Layer],
        pairs: Sequence[tuple[_Layer, _Layer]],
        stokes_meta: Mapping[str, Any],
        stokes_rows: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        if pairs and stokes_meta["status"] == "read":
            return []
        if pairs:
            unread = [layer.file_name for pair in pairs for layer in pair]
            unread = [name for name in unread if name not in stokes_rows]
            return [
                {
                    "item": (
                        "Read the Site01 windows of the S1 and S4 mosaic layers that cover Site01 "
                        f"and were not read ({', '.join(unread)}) (stokes_observation)"
                    ),
                    "reason": stokes_meta["reason"],
                }
            ]
        no_s4 = not any(layer.quantity == "S4" for layer in layers)
        return [
            {
                "item": (
                    "Read one LRO-L-MRFLRO-5-CDR-MAP-V1.0 map-projected single-swath product over "
                    "Site01 and observe its S1 and S4 layers (PRD 5 row 1)"
                ),
                "reason": (
                    "the polar mosaic has no S4 layer"
                    if no_s4
                    else "no S4 layer of the polar mosaic covers Site01"
                )
                + " (stokes_observation); reading a single swath is outside T1-04",
            }
        ]

    def _convention(
        self, label: Pds3Object, dsmap: str, entry: Mapping[str, Any]
    ) -> dict[str, Any]:
        """The pixel-to-map conversion that DSMAP.CAT (^DATA_SET_MAP_PROJECTION) states."""
        pointer = _label_text(label.child("IMAGE_MAP_PROJECTION"), "^DATA_SET_MAP_PROJECTION")
        if pointer is None or pointer.lower() != str(entry["file_name"]).lower():
            raise PilotError(
                f"{self.cpr_label_name} points to the map projection file {pointer!r}, not "
                f"{entry['file_name']}"
            )
        text = _collapse(dsmap)
        for statement in (*_DSMAP_FORMULAS, _DSMAP_CENTRES):
            if statement not in text:
                raise PilotError(
                    f"{entry['url']} does not state '{statement}', so the pixel convention of the "
                    "mosaic cannot be confirmed"
                )
        return {
            "source": entry["url"],
            "formulas": list(_DSMAP_FORMULAS),
            "pixel_centres": _DSMAP_CENTRES,
            "keywords": (
                "S0 = SAMPLE_PROJECTION_OFFSET, L0 = LINE_PROJECTION_OFFSET, Scale = MAP_SCALE; "
                "Sample and Line are 1-based, so the 0-based pixel edge coordinate is Sample - 0.5"
            ),
        }

    def _projection(
        self,
        projection: Pds3PolarStereographic,
        block: Pds3Object,
        convention: Mapping[str, Any],
    ) -> dict[str, Any]:
        pole = {
            "col": projection.sample_projection_offset - 0.5,
            "row": projection.line_projection_offset - 0.5,
        }
        centre = {"col": projection.line_samples / 2, "row": projection.lines / 2}
        return {
            "label_keywords": {item.key: item.raw for item in block.items},
            "proj4": projection.crs.srs,
            "crs_wkt": projection.crs.to_wkt(),
            "radius_m": projection.radius_m,
            "map_scale_m": projection.map_scale_m,
            "transform": [float(value) for value in tuple(projection.transform)[:6]],
            "conversion": dict(convention),
            "pole_edge_coordinates": pole,
            "image_centre_edge_coordinates": centre,
            "centred_on_pole": all(abs(pole[key] - centre[key]) < 1e-6 for key in pole),
        }

    def _window_record(
        self,
        site: _SiteWindow | str,
        window_meta: Mapping[str, Any],
        projection: Pds3PolarStereographic,
        rows_entry: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if isinstance(site, str):
            return {"status": "not_located", "reason": site}
        col_off, row_off, width, height = site.window
        t = projection.transform
        left, top = t.c + col_off * t.a, t.f + row_off * t.e
        return {
            "status": "read" if rows_entry is not None else "located",
            "reason": window_meta["reason"],
            "site_extent": site.site_extent,
            "transformation": (
                f"pyproj {importlib.metadata.version('pyproj')} Transformer.from_crs(the site "
                "CRS WKT, the label CRS, always_xy=True).transform_bounds(densify_pts=21, "
                "errcheck=True)"
            ),
            "converted_bounds_m": _bounds(*site.bounds),
            "pixel_window": site.fetch_fields()["pixel_window"],
            "pixel_rule": (
                "the smallest set of whole mosaic pixels covering the converted bounds: the low "
                "edges floored and the high edges ceiled in 0-based pixel edge coordinates"
            ),
            "window_extent_m": _bounds(left, top + height * t.e, left + width * t.a, top),
            "rows": {"first": row_off, "last": row_off + height - 1, "count": height},
        }

    def _window_values(
        self,
        raw_dir: Path,
        entry: Mapping[str, Any],
        layout: Pds3ImageLayout,
        window: tuple[int, int, int, int],
    ) -> npt.NDArray[Any]:
        """The stored samples of ``window`` from the rows ``entry`` lists; its rows must match."""
        col_off, row_off, width, height = window
        rows = entry["rows"]
        expected = pds3_row_byte_range(layout, row_off, row_off + height - 1)
        if (rows["first"], rows["last"]) != (row_off, row_off + height - 1) or (
            entry["first_byte"],
            entry["last_byte"],
        ) != expected:
            raise PilotError(
                f"the rows in data/raw/{self.name}/{entry['file_name']} are not the window's "
                f"rows; run: {COMMAND} fetch {self.name}"
            )
        data = (raw_dir / str(entry["file_name"])).read_bytes()
        values: npt.NDArray[Any] = decode_pds3_rows(data, layout, height)[
            :, col_off : col_off + width
        ]
        return values

    def _cpr_statistics(
        self,
        raw_dir: Path,
        rows_entry: Mapping[str, Any],
        layout: Pds3ImageLayout,
        site: _SiteWindow,
    ) -> dict[str, Any]:
        window = self._window_values(raw_dir, rows_entry, layout, site.window)
        mask = validity_mask(window, nodata=layout.special_values["CORE_NULL"])
        fraction = valid_fraction(mask)
        values = np.asarray(window[mask.valid], dtype=np.float64)
        return {
            "status": "computed",
            "reason": None,
            "values": (
                "the stored samples of the window's columns of the rows read, unscaled "
                "(SCALING_FACTOR 1.0 and OFFSET 0.0 in the label)"
            ),
            "valid_pixels": fraction.valid_pixels,
            "total_pixels": fraction.total_pixels,
            "valid_fraction": fraction.fraction,
            "invalid_by_reason": {
                reason.label: count
                for reason, count in mask.reason_counts().items()
                if reason is not InvalidReason.VALID
            },
            "mask": (
                "quality.validity_mask with nodata = the label's CORE_NULL; the label's four ISIS "
                "saturation codes are isis_saturation"
            ),
            "min": float(np.min(values)) if values.size else None,
            "max": float(np.max(values)) if values.size else None,
            "median": float(np.median(values)) if values.size else None,
            "median_definition": (
                "numpy.median of the valid values in float64: the mean of the two middle values "
                "for an even count"
            ),
            "zero_pixels": int(np.count_nonzero(values == 0)),
            "negative_pixels": int(np.count_nonzero(values < 0)),
            "positive_pixels": int(np.count_nonzero(values > 0)),
            "non_positive_rule": (
                "a CPR of 0 or below is kept as valid and counted, not masked: the label declares "
                "no rule that makes it missing (layout_leads, missing_data_non_positive; PRD 7, F1)"
            ),
        }

    def _leads(
        self,
        label: Pds3Object,
        layout: Pds3ImageLayout,
        projection: Pds3PolarStereographic,
        image_name: str,
        fetched: Mapping[str, Any],
        combined: re.Match[str] | None,
    ) -> list[dict[str, Any]]:
        """Each PRD 5 lead for the mosaic layout, confirmed or contradicted by the label."""
        block = label.child("IMAGE_MAP_PROJECTION")
        raw = {item.key: item.raw for item in [*label.items, *block.items]}
        note = _collapse(_label_text(label, "NOTE") or "")
        start, stop = raw.get("START_TIME", ""), raw.get("STOP_TIME", "")
        null = layout.special_values.get("CORE_NULL")
        mosaics_url = str(fetched["mosaics_url"])
        declared = ", ".join(layout.special_raw) or "no special values"

        def lead(item: str, prd: str, value: str, confirmed: bool, basis: str) -> dict[str, Any]:
            return {
                "item": item,
                "prd_lead": prd,
                "label_value": value,
                "status": "confirmed" if confirmed else "contradicted",
                "basis": basis,
            }

        return [
            lead(
                "file",
                "lsz_xxxxx_3cp_piu_90s000_v1.img, volume LROMRF_0005 of the Mini-RF CDR archive "
                "at PDS Geosciences",
                f"^IMAGE = {raw['^IMAGE']}; listed as {image_name} at {mosaics_url}",
                image_name.lower() == "lsz_xxxxx_3cp_piu_90s000_v1.img"
                and "/lromrf_0005/" in mosaics_url.lower(),
                "the label's ^IMAGE pointer and the volume directory the landing page links",
            ),
            lead(
                "data_set",
                "LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0",
                f"DATA_SET_ID = {raw['DATA_SET_ID']}",
                _label_text(label, "DATA_SET_ID") == "LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0",
                "the label's DATA_SET_ID",
            ),
            lead(
                "size",
                "10,346 x 10,346 pixels",
                f"LINES = {layout.lines}, LINE_SAMPLES = {layout.line_samples}",
                layout.lines == 10346 and layout.line_samples == 10346,
                "the label's IMAGE object",
            ),
            lead(
                "sample_type",
                "Little-endian 32-bit float",
                f"SAMPLE_TYPE = {layout.sample_type}, SAMPLE_BITS = {layout.sample_bits}",
                layout.dtype == np.dtype("<f4"),
                "PDS3 Standards Reference appendix C: PC_REAL is a little-endian IEEE real",
            ),
            lead(
                "values",
                "CPR values",
                f"NOTE = '{note}'",
                "circular polarization ratio" in note.lower(),
                "the label's NOTE",
            ),
            lead(
                "projection",
                "South-polar stereographic",
                f"MAP_PROJECTION_TYPE = {raw['MAP_PROJECTION_TYPE']}, CENTER_LATITUDE = "
                f"{raw['CENTER_LATITUDE']}",
                projection.center_latitude_deg == -90.0,
                "the label's IMAGE_MAP_PROJECTION object",
            ),
            lead(
                "pixels_per_degree",
                "256 pixels per degree",
                f"MAP_RESOLUTION = {raw.get('MAP_RESOLUTION')}",
                projection.map_resolution_ppd == 256.0,
                "the label's IMAGE_MAP_PROJECTION object",
            ),
            lead(
                "pixel_size",
                "about 118.45 m per pixel",
                f"MAP_SCALE = {raw['MAP_SCALE']}",
                round(projection.map_scale_m, 2) == 118.45,
                "the label's MAP_SCALE, converted to metres and rounded to the lead's 2 decimals",
            ),
            lead(
                "projection_centre",
                "projection centre at sample and line 5173.5 (1-based)",
                f"SAMPLE_PROJECTION_OFFSET = {raw['SAMPLE_PROJECTION_OFFSET']}, "
                f"LINE_PROJECTION_OFFSET = {raw['LINE_PROJECTION_OFFSET']}",
                abs(projection.sample_projection_offset - 5173.5) < 1e-6
                and abs(projection.line_projection_offset - 5173.5) < 1e-6,
                "DSMAP.CAT: x = (Sample - S0) * Scale and y = (L0 - Line) * Scale with Sample and "
                "Line 1-based and integral at pixel centres, so the offsets are the pole's "
                "1-based pixel-centre coordinates (compared to 1e-6 pixel)",
            ),
            lead(
                "missing_data_isis_null",
                "ISIS null values near -3.4e38",
                f"CORE_NULL = {layout.special_raw.get('CORE_NULL')} ({null!r})",
                null == ISIS_NULL_REAL4,
                "the label's CORE_NULL bit pattern equals the ISIS 4-byte Null (SpecialPixel.h) "
                "that quality.validity_mask uses",
            ),
            {
                "item": "missing_data_non_positive",
                "prd_lead": "non-positive values treated as missing",
                "label_value": (
                    f"the IMAGE object declares {declared} and no rule for non-positive values"
                ),
                "status": "not_stated_in_label",
                "basis": (
                    "the label does not make non-positive values missing, so a CPR of 0 or below "
                    "is a valid value (PRD 7, F1) and is counted, not masked (cpr_statistics)"
                ),
            },
            lead(
                "acquisitions",
                "East- and west-looking passes combined, 2009 to 2011",
                f"'{combined.group(0) if combined else 'no combined-look statement'}'; "
                f"START_TIME = {start}, STOP_TIME = {stop}",
                combined is not None and start.startswith("2009") and stop.startswith("2011"),
                "the label's DESCRIPTION, START_TIME and STOP_TIME",
            ),
        ]

    def _frequency_note(
        self, frequency: Pds3Item | None, s_band: re.Match[str] | None
    ) -> str | None:
        if frequency is None or s_band is None or not isinstance(frequency.value, Pds3Quantity):
            return None
        if frequency.value.unit.lower() == "ghz" and frequency.value.value > 100:
            return (
                f"the label states {frequency.raw}, but the instrument catalog states "
                f"'{s_band.group(0)}': the two differ by a factor of 1000. Both are quoted as "
                "stated; neither is corrected here"
            )
        return None

    def _field_notes(self) -> dict[str, str]:
        return {
            "version": "PRODUCT_VERSION_ID as the label states it",
            "effective_resolution": (
                "unknown: MAP_SCALE is the nominal sampling of the grid; the mosaic blends several "
                "acquisitions, and neither the label nor the catalog states an effective resolution"
            ),
            "transmit_receive_convention": (
                "unknown in the ProductRecords: the label states no transmit handedness "
                "(transmit_handedness); the volume errata's statement is quoted there, not adopted"
            ),
            "checksum": (
                "for the image, the ProductRecord checksum and local_path are those of the image "
                "rows read by range (range_read), not of the whole file, which was not downloaded"
            ),
            "product_records": (
                "every mosaic label downloaded has a ProductRecord, because each is read to "
                "establish the layers (mosaic_layers, stokes_observation); its fields are those "
                "its own label states. Only the images whose window rows were read have an "
                "image record"
            ),
            "pixel_convention": (
                "the window uses DSMAP.CAT's conversion (projection.conversion). GDAL's PDS driver "
                "with its default settings places the origin of such a label one pixel further "
                "right and down (pinned in test_io_pds3.py); the GDAL documentation warns that "
                "'Due to ambiguities in the PDS specification, the georeferencing of some "
                "products is subtly or grossly incorrect'"
            ),
            "nodata": (
                "special pixels are compared as the label's bit patterns; null_value.value is the "
                "float32 value of CORE_NULL written as a JSON number"
            ),
        }


# --- ctx: USGS ASP CTX DTMs through the Astrogeology STAC API (PRD 5 Mars inputs, 9 G0) ----------

_CTX_PREFIX: Final = "CTX/"
_QA_STD_LINE: Final = re.compile(
    r"^StdDev of difference:[ \t]*(?P<value>[^ \t\r\n]+)[ \t]+(?P<unit>[^ \t\r\n]+)[ \t]*\r?$",
    re.MULTILINE,
)
_QA_DEFINITION: Final = re.compile(r"The qa_metrics\.txt file for each DTM provides[^.]*\.")
_AREOID_SENTENCE: Final = re.compile(
    r"Each CTX DTM gives elevation values above or below the Mars geoid[^.]*\."
)
_JEZERO_LOCATION: Final = re.compile(
    r"Jezero is located at Longitude Latitude [0-9.]+ E, [0-9.]+ N in mc[0-9]+"
)
_HTML_TAG: Final = re.compile(r"<[^>]*>")
_HTML_HIDDEN: Final = re.compile(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>|<!--.*?-->")
"""Markup a browser does not show as text: script and style elements and comments."""
_GAZETTEER_NETWORK: Final = re.compile(r'<option label="([^"]+)" value="([0-9]+)"\s*selected')
_GAZETTEER_SYSTEM_ID: Final = re.compile(
    r'id="selected_coordinate_system" type="hidden" value="([0-9]+)"'
)
_GAZETTEER_SYSTEM_NAME: Final = re.compile(r'id="coordinate_system_name" value="([^"]+)"')
_GAZETTEER_FIELDS: Final = (
    ("northmost_latitude", "Northmost Latitude"),
    ("southmost_latitude", "Southmost Latitude"),
    ("eastmost_longitude", "Eastmost Longitude"),
    ("westmost_longitude", "Westmost Longitude"),
)
_EAST_0_360: Final = "Planetocentric, +East, 0 - 360"
_PROBE_ROLE: Final = (
    "pilot probe of CTX DTM coverage for G0; not a target window (the Jezero and Arcadia "
    "Planitia target windows are frozen at G0 by the team, PRD 5 and 15)"
)
_ITEM_RULE: Final = (
    "the lexicographically first item ID (Python string order, by code point) among the items "
    "the Jezero probe search returned over all its pages"
)


def _stac_page_name(probe: str, number: int) -> str:
    """The local file of page ``number`` (from 1) of a probe's item search."""
    return f"stac_search_{probe}_page{number:03d}.json"


def _page_text(html: str) -> str:
    """The visible text of an HTML page: tags removed, entities decoded, whitespace collapsed.

    Script and style elements and comments are dropped with their content first, so a quote is
    never taken from data a page embeds for scripts, such as JSON-LD with escaped markup.
    """
    return _collapse(unescape(_HTML_TAG.sub(" ", _HTML_HIDDEN.sub(" ", html))))


def _strict_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes().decode("utf-8"), parse_constant=_refuse_json_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise PilotError(f"{path} is not strict JSON ({error})") from error


def _refuse_json_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def _exchange_entry(exchange: RangeExchange) -> dict[str, Any]:
    return {
        "requested_range": f"bytes={exchange.requested_first}-{exchange.requested_last}",
        "http_status": exchange.status,
        "content_range": exchange.content_range,
        "bytes_received": exchange.bytes_received,
        "retrieved_at": (
            None if exchange.retrieved_at is None else utc_timestamp(exchange.retrieved_at)
        ),
    }


def _header_entry(header: GeoTiffHeader) -> dict[str, Any]:
    """The COG header as the file states it, for the record."""
    t = header.transform
    unit: str | None = None
    if header.crs is not None:
        axes = pyproj.CRS.from_wkt(header.crs).axis_info
        units = {axis.unit_name for axis in axes}
        unit = units.pop() if len(units) == 1 else None
    return {
        "width": header.width,
        "height": header.height,
        "count": header.count,
        "dtypes": list(header.dtypes),
        "block_shapes": [list(shape) for shape in header.block_shapes],
        "overviews": list(header.overviews),
        "image_structure": dict(sorted(header.image_structure.items())),
        "tags": dict(sorted(header.tags.items())),
        "crs_wkt": header.crs,
        "transform": None if t is None else [t.a, t.b, t.c, t.d, t.e, t.f],
        "pixel_size": None if t is None else {"x": abs(t.a), "y": abs(t.e), "unit": unit},
        "nodata": [_json_number(value) for value in header.nodata],
        "units": list(header.units),
        "scales": list(header.scales),
        "offsets": list(header.offsets),
        "descriptions": list(header.descriptions),
    }


@dataclass(frozen=True)
class _CtxProbe:
    key: str
    name: str
    bbox: tuple[float, float, float, float]
    basis_role: str
    basis: str


@dataclass(frozen=True)
class CtxSource:
    """USGS ASP CTX DTMs (PRD 5 Mars inputs, 9 G0: one Mars CTX DTM read).

    fetch downloads the STAC collection, the USGS CTX DTM documentation and the two pages the
    probe boxes come from, then runs one item search per probe box and saves every page. The
    item chosen by ``_ITEM_RULE`` has its ``qa_metrics.txt`` downloaded (when the item lists it)
    and the centred ``window_pixels`` square of its ``dtm`` COG read through HTTP range requests,
    header included; only the byte ranges read are stored. record reads only those files.

    The probe boxes are pilot probes, not target windows. The Jezero box is the one the USGS
    ``mosaic_json`` tutorial searches; the Arcadia Planitia box is that feature's extent in the
    IAU Gazetteer of Planetary Nomenclature. record confirms each against the fetched page.
    """

    api_url: str = "https://stac.astrogeology.usgs.gov/api"
    collection: str = "mro_ctx_controlled_usgs_dtms"
    documentation_url: str = "https://stac.astrogeology.usgs.gov/docs/data/mars/ctxdtms/"
    jezero_basis_url: str = "https://stac.astrogeology.usgs.gov/docs/tutorials/mosaic_json/"
    arcadia_basis_url: str = "https://planetarynames.wr.usgs.gov/Feature/348"
    jezero_bbox: tuple[float, float, float, float] = (76.69, 17.41, 78.69, 19.41)
    arcadia_bbox: tuple[float, float, float, float] = (165.86, 33.87, -149.57, 64.17)
    page_limit: int = 100
    window_pixels: int = 256
    range_block_bytes: int = RANGE_BLOCK_BYTES
    whole_file_limit_bytes: int = WHOLE_FILE_LIMIT_BYTES

    @property
    def name(self) -> str:
        return "ctx"

    @property
    def collection_url(self) -> str:
        return f"{self.api_url.rstrip('/')}/collections/{self.collection}"

    def _documents(self) -> tuple[tuple[str, str, str], ...]:
        return (
            ("collection", self.collection_url, "stac_collection.json"),
            ("documentation", self.documentation_url, "ctxdtms_documentation.html"),
            ("jezero_box_basis", self.jezero_basis_url, "usgs_tutorial_mosaic_json.html"),
            ("arcadia_box_basis", self.arcadia_basis_url, "iau_gazetteer_arcadia_planitia.html"),
        )

    def _probes(self) -> tuple[_CtxProbe, ...]:
        return (
            _CtxProbe(
                key="jezero",
                name="Jezero crater",
                bbox=self.jezero_bbox,
                basis_role="jezero_box_basis",
                basis=(
                    "the 2-degree box that the USGS STAC tutorial 'mosaic_json' searches for "
                    "Jezero crater with stac-client --bbox, around the location it states"
                ),
            ),
            _CtxProbe(
                key="arcadia_planitia",
                name="Arcadia Planitia",
                bbox=self.arcadia_bbox,
                basis_role="arcadia_box_basis",
                basis=(
                    "the extent of Arcadia Planitia in the IAU Gazetteer of Planetary "
                    "Nomenclature (Feature 348) for the control network and coordinate system "
                    "the page selects, with the east longitude taken from 0-360 to -180-180; "
                    "west greater than east crosses the antimeridian (RFC 7946 5.2)"
                ),
            ),
        )

    def _search_url(self, probe: _CtxProbe) -> str:
        return stac_search_url(
            self.api_url, collection=self.collection, bbox=probe.bbox, limit=self.page_limit
        )

    def _centre_window(self, width: int, height: int) -> Window:
        """The centred ``window_pixels`` square (smaller only where the raster is smaller)."""
        side_x, side_y = min(self.window_pixels, width), min(self.window_pixels, height)
        return Window((width - side_x) // 2, (height - side_y) // 2, side_x, side_y)

    # -- fetch --

    def fetch(
        self, raw_dir: Path, download: Downloader, read_range: RangeFetcher, now: Clock
    ) -> None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=raw_dir, prefix=".fetch-") as staging_name:
            staging = Path(staging_name)
            documents: list[dict[str, Any]] = []
            for role, url, file_name in self._documents():
                fetched = download(url, staging / file_name, max_bytes=self.whole_file_limit_bytes)
                documents.append(_fetch_entry(fetched, now(), role=role))
            probes: list[dict[str, Any]] = []
            jezero: list[tuple[int, dict[str, Any]]] = []
            for probe in self._probes():
                pages = fetch_stac_search(
                    self._search_url(probe),
                    staging,
                    functools.partial(_stac_page_name, probe.key),
                    download=download,
                )
                for page in pages:
                    documents.append(
                        _fetch_entry(
                            page.fetched,
                            now(),
                            role="stac_search",
                            probe=probe.key,
                            page=page.number,
                            next_url=page.content.next_url,
                        )
                    )
                    if probe.key == "jezero":
                        jezero.extend((page.number, item) for item in page.content.features)
                probes.append(
                    {
                        "key": probe.key,
                        "bbox": list(probe.bbox),
                        "first_url": pages[0].url,
                        "pages": [page.fetched.path.name for page in pages],
                    }
                )
            chosen_id = min((str(item["id"]) for _, item in jezero), default=None)
            files: list[dict[str, Any]] = []
            qa_status = "not_read"
            if chosen_id is not None:
                item = next(item for _, item in jezero if item["id"] == chosen_id)
                assets = item.get("assets")
                if not isinstance(assets, dict):
                    raise PilotError(f"the STAC item {chosen_id} has no assets object")
                self._refuse_signed_assets(chosen_id, assets)
                qa_asset = assets.get("qa_metrics")
                if qa_asset is None:
                    qa_status = "absent"
                else:
                    qa_url = self._asset_href(chosen_id, "qa_metrics", qa_asset)
                    fetched = download(
                        qa_url, staging / "qa_metrics.txt", max_bytes=self.whole_file_limit_bytes
                    )
                    files.append(
                        _fetch_entry(
                            fetched,
                            now(),
                            role="qa_metrics",
                            item_id=chosen_id,
                            asset_key="qa_metrics",
                        )
                    )
                    qa_status = "downloaded"
                dtm_url = self._asset_href(chosen_id, "dtm", assets.get("dtm"))
                provider_name = _safe_file_name(
                    urllib.parse.unquote(urllib.parse.urlsplit(dtm_url).path.rsplit("/", 1)[-1]),
                    dtm_url,
                )
                remote = read_remote_geotiff_window(
                    dtm_url,
                    self._centre_window,
                    fetch=read_range,
                    block_bytes=self.range_block_bytes,
                    max_bytes=self.whole_file_limit_bytes,
                    with_header=True,
                    now=now,
                )
                local_name = f"{provider_name}{_RANGES_SUFFIX}"
                (staging / local_name).write_bytes(remote.ranges.data)
                files.append(
                    _window_entry(
                        remote,
                        local_name,
                        now(),
                        role="dtm_window",
                        item_id=chosen_id,
                        asset_key="dtm",
                        provider_file_name=provider_name,
                        exchanges=[_exchange_entry(exchange) for exchange in remote.exchanges],
                    )
                )
            metadata = {
                "source": self.name,
                "command": f"{COMMAND} fetch {self.name}",
                "api_url": self.api_url,
                "collection": self.collection,
                "page_limit": self.page_limit,
                "whole_file_limit_bytes": self.whole_file_limit_bytes,
                "documents": documents,
                "files": files,
                "probes": probes,
                "choice": {"probe": "jezero", "rule": _ITEM_RULE, "item_id": chosen_id},
                "qa_metrics": {"status": qa_status},
            }
            (raw_dir / FETCH_METADATA).unlink(missing_ok=True)
            for entry in [*documents, *files]:
                os.replace(staging / entry["file_name"], raw_dir / entry["file_name"])
            _write_atomically(raw_dir / FETCH_METADATA, canonical_json(metadata))

    @staticmethod
    def _refuse_signed_assets(item_id: str, assets: Mapping[str, Any]) -> None:
        """Refuse the chosen item if any asset href has credentials or a signature.

        Every asset of the chosen item is recorded, though only dtm and qa_metrics are
        requested, so each href is checked before any asset request and again at record. The
        href is not repeated, so a secret never reaches a log.
        """
        for key, asset in sorted(assets.items()):
            href = asset.get("href") if isinstance(asset, dict) else None
            if isinstance(href, str) and has_url_credentials(href):
                raise PilotError(
                    f"the {key} asset of the STAC item {item_id} has an href with credentials "
                    "or a signature; it is neither requested nor recorded, because records keep "
                    "public URLs only"
                )

    def _asset_href(self, item_id: str, key: str, asset: Any) -> str:
        href = asset.get("href") if isinstance(asset, dict) else None
        if not isinstance(href, str) or not href:
            raise PilotError(f"the STAC item {item_id} lists no {key} asset href")
        return href

    # -- build --

    def build(self, raw_dir: Path, root: Path) -> Build:
        fetched = _load_fetch(raw_dir, self.name)
        try:
            return self._build(raw_dir, root, fetched)
        except (KeyError, TypeError) as error:
            raise PilotError(
                f"data/raw/{self.name}/{FETCH_METADATA} lacks an expected entry ({error!r}); "
                f"run: {COMMAND} fetch {self.name}"
            ) from error

    def _local(self, file_name: str) -> str:
        return f"{RAW_DIR.as_posix()}/{self.name}/{file_name}"

    def _build(self, raw_dir: Path, root: Path, fetched: Mapping[str, Any]) -> Build:
        documents = {
            entry["role"]: entry for entry in fetched["documents"] if entry["role"] != "stac_search"
        }
        files = {entry["role"]: entry for entry in fetched["files"]}
        collection_entry = documents["collection"]
        collection = _strict_json(raw_dir / collection_entry["file_name"])
        if not isinstance(collection, dict):
            raise PilotError(f"{collection_entry['url']} is not a STAC collection object")
        collection_url = str(collection_entry["url"])
        licence = self._stated(collection, "license", collection_url)
        doi = self._stated(collection, "sci:doi", collection_url)
        citation = self._stated(collection, "sci:citation", collection_url)
        attribution = self._attribution(licence["value"], doi["value"], citation["value"])
        provider = self._provider(collection)
        documentation_entry = documents["documentation"]
        documentation = _page_text(_decode(raw_dir / documentation_entry["file_name"]))
        qa_sentence = _QA_DEFINITION.search(documentation)
        areoid_sentence = _AREOID_SENTENCE.search(documentation)

        probes: list[dict[str, Any]] = []
        jezero: list[tuple[int, dict[str, Any]]] = []
        for probe in self._probes():
            probe_record, items = self._probe_record(raw_dir, fetched, probe, documents)
            probes.append(probe_record)
            if probe.key == "jezero":
                jezero = items
        chosen_id = min((str(item["id"]) for _, item in jezero), default=None)
        if fetched["choice"]["item_id"] != chosen_id:
            raise PilotError(
                f"fetch.json chose item {fetched['choice']['item_id']!r}, but the rule gives "
                f"{chosen_id!r} on the saved pages; run: {COMMAND} fetch {self.name}"
            )

        records: list[ProductRecord] = []
        verification: dict[str, dict[str, str]] = {}
        dtm: dict[str, Any] | None = None
        threshold = {
            "applied": False,
            "value": None,
            "reason": (
                "the CTX QA threshold is an open kickoff item (PRD 15: start at 10 m CTX-MOLA "
                "standard deviation); this record quotes qa_metrics.txt and neither sets nor "
                "applies a threshold"
            ),
        }
        definition = {
            "quote": None if qa_sentence is None else qa_sentence.group(0),
            "source": documentation_entry["url"],
        }
        qa: dict[str, Any] = {
            "status": "not_read",
            "reason": "the Jezero probe search returned no item, so no qa_metrics.txt was read",
            "ctx_mola_std_dev": None,
            "ctx_mola_std_dev_absent_reason": None,
            "definition": definition,
            "ctx_qa_threshold": threshold,
        }
        if chosen_id is None:
            chosen: dict[str, Any] = {
                "rule": _ITEM_RULE,
                "probe": "jezero",
                "item_id": None,
                "reason": "the Jezero probe search returned no item, so no DTM was read",
            }
        else:
            page_number, item = next((n, i) for n, i in jezero if i["id"] == chosen_id)
            assets = item["assets"]
            self._refuse_signed_assets(chosen_id, assets)
            chosen = self._chosen_record(chosen_id, page_number, item)
            qa, qa_record, std_line = self._qa(
                raw_dir, fetched, files, chosen_id, assets, provider, attribution, qa
            )
            if qa_record is not None:
                records.append(qa_record)
                verification[qa_record.product_id] = {
                    "status": qa_record.verification_status,
                    "basis": (
                        "downloaded and read as text; the CTX-MOLA standard deviation line is "
                        "quoted in qa_metrics.ctx_mola_std_dev where present. A text file is not "
                        "decoded, masked or plotted, so the PRD 5 entry rule does not apply in full"
                    ),
                }
            dtm, dtm_record, basis = self._dtm(
                raw_dir,
                root,
                files,
                chosen_id,
                assets,
                provider,
                attribution,
                std_line,
                qa["status"],
                areoid_sentence,
                documentation_entry["url"],
            )
            records.append(dtm_record)
            verification[dtm_record.product_id] = {
                "status": dtm_record.verification_status,
                "basis": basis,
            }

        retrieved = sorted(
            entry["retrieved_at"] for entry in [*fetched["documents"], *fetched["files"]]
        )
        result = {
            "record": "ctx_dtm",
            "description": (
                "One USGS ASP CTX DTM found through the Astrogeology STAC API and read by window, "
                "with its qa_metrics.txt, as fetched by the T1 pilot (PRD 5 Mars inputs; 9 G0, "
                "one Mars CTX DTM read). Raw observations only: no CTX QA threshold is applied "
                "(PRD 15), no target window is chosen, and the shipping mode and the G0 status "
                "are a human checkpoint (PRD 23.6) and are not recorded here."
            ),
            "source": self.name,
            "collection": {
                "id": self.collection,
                "url": collection_url,
                "local_path": self._local(collection_entry["file_name"]),
                "sha256": collection_entry["sha256"],
                "stated_id": collection.get("id"),
                "title": collection.get("title"),
                "license": licence,
                "doi": doi,
                "citation": citation,
                "cite_as": self._cite_as(collection),
                "providers": collection.get("providers"),
            },
            "probes": probes,
            "target_windows": {
                "chosen": False,
                "reason": (
                    "no target window is chosen here: the Jezero window near the landing site "
                    "and the Arcadia Planitia window inside the shallow-ice region are frozen at "
                    "G0 by the team (PRD 5 Mars target windows; PRD 15 open items). The probe "
                    "boxes only count CTX DTM coverage."
                ),
            },
            "chosen_item": chosen,
            "dtm": dtm,
            "qa_metrics": qa,
            "documents": [
                {
                    key: entry[key]
                    for key in (
                        "role",
                        "file_name",
                        "url",
                        "final_url",
                        "http_status",
                        "content_type",
                        "bytes_received",
                        "sha256",
                        "last_modified",
                        "retrieved_at",
                    )
                }
                | {"local_path": self._local(entry["file_name"])}
                for entry in fetched["documents"]
                if entry["role"] != "stac_search"
            ],
            "retrieval": {
                "first_retrieved_at": retrieved[0],
                "last_retrieved_at": retrieved[-1],
                "fetch_metadata": self._local(FETCH_METADATA),
                "command": fetched["command"],
            },
            "verification": verification,
            "open_g0_items": [
                "G0 needs the Mars target windows (Jezero near the landing site, Arcadia "
                "Planitia inside the shallow-ice region); none is chosen here (PRD 5, 15)",
                "The CTX QA threshold is not set (PRD 15); qa_metrics.txt is quoted, not judged",
                "The G0 status and the shipping mode are a human checkpoint (PRD 23.6)",
            ],
            "field_notes": self._field_notes(),
            "tool_versions": tool_versions(),
        }
        return Build(
            files={"outputs/pilot/ctx_dtm.json": canonical_json(result)},
            records=tuple(sorted(records, key=lambda r: r.product_id)),
            record_prefix=_CTX_PREFIX,
        )

    # -- build: the collection --

    @staticmethod
    def _stated(collection: Mapping[str, Any], key: str, url: str) -> dict[str, Any]:
        """A collection field as stated, with where; "unknown" when the collection omits it."""
        value = collection.get(key)
        if isinstance(value, str) and value.strip():
            return {"value": value, "stated_by": f"{url} ({key})"}
        return {"value": UNKNOWN, "stated_by": None}

    @staticmethod
    def _attribution(licence: str, doi: str, citation: str) -> str:
        parts = []
        if licence != UNKNOWN:
            parts.append(f"licence {licence} (STAC collection license)")
        if doi != UNKNOWN:
            parts.append(f"cite doi:{doi} (STAC collection sci:doi)")
        if citation != UNKNOWN:
            parts.append(f"citation as stated (sci:citation): '{citation}'")
        return "; ".join(parts) if parts else UNKNOWN

    @staticmethod
    def _provider(collection: Mapping[str, Any]) -> str:
        providers = collection.get("providers")
        named = []
        if isinstance(providers, list):
            for entry in providers:
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    roles = entry.get("roles")
                    role_text = (
                        f" ({', '.join(str(role) for role in roles)})"
                        if isinstance(roles, list) and roles
                        else ""
                    )
                    named.append(f"{entry['name']}{role_text}")
        if not named:
            return UNKNOWN
        return f"{'; '.join(named)}, as the STAC collection's providers state"

    @staticmethod
    def _cite_as(collection: Mapping[str, Any]) -> str | None:
        links = collection.get("links")
        if not isinstance(links, list):
            return None
        hrefs = [
            str(link["href"])
            for link in links
            if isinstance(link, dict) and link.get("rel") == "cite-as" and "href" in link
        ]
        return hrefs[0] if hrefs else None

    # -- build: the probes --

    def _probe_record(
        self,
        raw_dir: Path,
        fetched: Mapping[str, Any],
        probe: _CtxProbe,
        documents: Mapping[str, Mapping[str, Any]],
    ) -> tuple[dict[str, Any], list[tuple[int, dict[str, Any]]]]:
        listed = {entry["key"]: entry for entry in fetched["probes"]}[probe.key]
        pages = sorted(
            (
                entry
                for entry in fetched["documents"]
                if entry["role"] == "stac_search" and entry["probe"] == probe.key
            ),
            key=lambda entry: int(entry["page"]),
        )
        expected = self._search_url(probe)
        if (
            not pages
            or [entry["page"] for entry in pages] != list(range(1, len(pages) + 1))
            or pages[0]["url"] != expected
            or listed["first_url"] != expected
            or listed["bbox"] != list(probe.bbox)
        ):
            raise PilotError(
                f"the saved {probe.key} search pages are not the search this source runs "
                f"({expected}); run: {COMMAND} fetch {self.name}"
            )
        items: list[tuple[int, dict[str, Any]]] = []
        page_records = []
        matched: set[int] = set()
        for index, entry in enumerate(pages):
            content = parse_stac_item_collection(
                (raw_dir / entry["file_name"]).read_bytes(), str(entry["final_url"])
            )
            following = pages[index + 1]["url"] if index + 1 < len(pages) else None
            if content.next_url != entry["next_url"] or content.next_url != following:
                raise PilotError(
                    f"page {entry['page']} of the {probe.key} search: its next link "
                    f"{content.next_url!r} does not lead to the next page saved ({following!r}); "
                    f"run: {COMMAND} fetch {self.name}"
                )
            items.extend((int(entry["page"]), feature) for feature in content.features)
            if content.number_matched is not None:
                matched.add(content.number_matched)
            page_records.append(
                {
                    "number": entry["page"],
                    "url": entry["url"],
                    "final_url": entry["final_url"],
                    "local_path": self._local(entry["file_name"]),
                    "http_status": entry["http_status"],
                    "content_type": entry["content_type"],
                    "bytes_received": entry["bytes_received"],
                    "sha256": entry["sha256"],
                    "retrieved_at": entry["retrieved_at"],
                    "number_returned": len(content.features),
                    "number_matched_stated": content.number_matched,
                    "next_url": content.next_url,
                }
            )
        ids = [str(feature["id"]) for _, feature in items]
        duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
        west, _, east, _ = probe.bbox
        record = {
            "key": probe.key,
            "name": probe.name,
            "role": _PROBE_ROLE,
            "bbox": list(probe.bbox),
            "bbox_order": "west, south, east, north in degrees (STAC API bbox)",
            "bbox_crosses_antimeridian": west > east,
            "basis": self._basis(raw_dir, probe, documents[probe.basis_role]),
            "search": {
                "first_url": expected,
                "collection": self.collection,
                "limit": self.page_limit,
                "pages": page_records,
                "items_found": len(set(ids)),
                "items_found_note": (
                    "distinct item IDs over every page, counted from the saved pages; "
                    "number_matched_stated lists the numberMatched values the pages state"
                ),
                "number_matched_stated": sorted(matched),
                "duplicate_item_ids": duplicates,
                "item_ids": sorted(set(ids)),
            },
        }
        return record, items

    def _basis(self, raw_dir: Path, probe: _CtxProbe, entry: Mapping[str, Any]) -> dict[str, Any]:
        html = _decode(raw_dir / entry["file_name"])
        basis: dict[str, Any] = {
            "text": probe.basis,
            "document": entry["url"],
            "local_path": self._local(entry["file_name"]),
        }
        if probe.key == "jezero":
            text = _page_text(html)
            command = re.search(
                rf"stac-client search \S+ --bbox (\S+) (\S+) (\S+) (\S+) "
                rf"-c {re.escape(self.collection)}(?: --matched)?",
                text,
            )
            location = _JEZERO_LOCATION.search(text)
            quotes = [] if command is None else [command.group(0)]
            if command is not None and location is not None:
                quotes.append(location.group(0))
            confirmed = command is not None and _same_numbers(command.groups(), probe.bbox)
            return basis | {"confirmed": confirmed, "quotes": quotes}
        stated = _gazetteer_extent(html)
        return basis | {
            "confirmed": stated is not None and _gazetteer_confirms(stated, probe.bbox),
            "stated": stated,
        }

    def _chosen_record(
        self, item_id: str, page_number: int, item: Mapping[str, Any]
    ) -> dict[str, Any]:
        properties = item.get("properties")
        props = properties if isinstance(properties, dict) else {}
        assets = item["assets"]
        return {
            "rule": _ITEM_RULE,
            "probe": "jezero",
            "item_id": item_id,
            "found_on_page": page_number,
            "collection": item.get("collection"),
            "bbox": item.get("bbox"),
            "properties": {
                key: props[key]
                for key in (
                    "title",
                    "start_datetime",
                    "end_datetime",
                    "datetime",
                    "gsd",
                    "license",
                    "proj:shape",
                )
                if key in props
            },
            "assets": {
                key: {
                    field_name: asset.get(field_name)
                    for field_name in ("href", "type", "title", "roles")
                }
                for key, asset in sorted(assets.items())
                if isinstance(asset, dict)
            },
        }

    # -- build: qa_metrics.txt --

    def _qa(
        self,
        raw_dir: Path,
        fetched: Mapping[str, Any],
        files: Mapping[str, Mapping[str, Any]],
        item_id: str,
        assets: Mapping[str, Any],
        provider: str,
        attribution: str,
        base: dict[str, Any],
    ) -> tuple[dict[str, Any], ProductRecord | None, str | None]:
        status = fetched["qa_metrics"]["status"]
        if status == "absent":
            if "qa_metrics" in assets or "qa_metrics" in files:
                raise PilotError(
                    f"fetch.json records qa_metrics.txt as absent for {item_id}, but the item "
                    f"lists it; run: {COMMAND} fetch {self.name}"
                )
            return (
                base
                | {
                    "status": "absent",
                    "item_id": item_id,
                    "reason": (
                        f"the STAC item {item_id} lists no qa_metrics asset, so qa_metrics.txt "
                        "is absent for this item"
                    ),
                },
                None,
                None,
            )
        entry = files["qa_metrics"]
        href = self._asset_href(item_id, "qa_metrics", assets.get("qa_metrics"))
        if status != "downloaded" or entry["url"] != href or entry["item_id"] != item_id:
            raise PilotError(
                f"fetch.json's qa_metrics.txt is not the one item {item_id} lists ({href}); "
                f"run: {COMMAND} fetch {self.name}"
            )
        text = _decode(raw_dir / entry["file_name"])
        lines = list(_QA_STD_LINE.finditer(text))
        std: dict[str, Any] | None = None
        absent_reason: str | None = None
        if len(lines) == 1:
            (match,) = lines
            std = {
                "line": match.group(0).rstrip("\r"),
                "line_number": text.count("\n", 0, match.start()) + 1,
                "value": match.group("value"),
                "unit": match.group("unit"),
                "source": self._local(entry["file_name"]),
            }
        elif not lines:
            absent_reason = (
                "qa_metrics.txt has no 'StdDev of difference' line, so the CTX-MOLA standard "
                "deviation is absent for this item"
            )
        else:
            absent_reason = (
                f"qa_metrics.txt has {len(lines)} 'StdDev of difference' lines; none is chosen"
            )
        local_path = self._local(entry["file_name"])
        quote = base["definition"]["quote"]
        record = ProductRecord.model_validate(
            {
                "product_id": f"{_CTX_PREFIX}{self.collection}/{item_id}/qa_metrics.txt",
                "provider": provider,
                "version": UNKNOWN,
                "source_url": href,
                "local_path": local_path,
                "checksum": entry["sha256"],
                "body": "mars",
                "acquisition_time": UNKNOWN,
                "frequency_or_wavelength": None,
                "transmit_receive_convention": None,
                "units": (
                    UNKNOWN
                    if std is None
                    else f"{std['unit']}, as qa_metrics.txt states for the standard deviation"
                ),
                "calibration_type": None,
                "native_grid": None,
                "effective_resolution": None,
                "crs_and_frame": UNKNOWN,
                "incidence_source": None,
                "qa_definition": (
                    UNKNOWN
                    if quote is None
                    else f"CTX-MOLA difference statistics, as the USGS documentation defines "
                    f"the file: '{quote}'; no CTX QA threshold is applied (PRD 15)"
                ),
                "license_or_attribution": attribution,
                "verification_status": "PARTIAL",
            }
        )
        qa = base | {
            "status": "read",
            "item_id": item_id,
            "reason": None,
            "url": entry["url"],
            "final_url": entry["final_url"],
            "local_path": local_path,
            "http_status": entry["http_status"],
            "content_type": entry["content_type"],
            "bytes_received": entry["bytes_received"],
            "sha256": entry["sha256"],
            "retrieved_at": entry["retrieved_at"],
            "text": text,
            "ctx_mola_std_dev": std,
            "ctx_mola_std_dev_absent_reason": absent_reason,
        }
        return qa, record, None if std is None else str(std["line"])

    # -- build: the DTM window --

    def _dtm(
        self,
        raw_dir: Path,
        root: Path,
        files: Mapping[str, Mapping[str, Any]],
        item_id: str,
        assets: Mapping[str, Any],
        provider: str,
        attribution: str,
        std_line: str | None,
        qa_status: str,
        areoid: re.Match[str] | None,
        documentation_url: str,
    ) -> tuple[dict[str, Any], ProductRecord, str]:
        entry = files["dtm_window"]
        dtm_asset = assets.get("dtm")
        href = self._asset_href(item_id, "dtm", dtm_asset)
        if entry["url"] != href or entry["item_id"] != item_id or not _is_window_read(entry):
            raise PilotError(
                f"fetch.json's DTM window is not a window read of the dtm asset of {item_id} "
                f"({href}); run: {COMMAND} fetch {self.name}"
            )
        provider_name = _provider_name(entry)
        path = raw_dir / entry["file_name"]
        ranges = self._ranges(path, entry)
        if ranges.covers_whole_file:
            raise PilotError(
                f"the byte ranges in data/raw/{self.name}/{path.name} hold all {ranges.size} "
                f"bytes of {href}, so it was downloaded whole, not read by window; run: "
                f"{COMMAND} fetch {self.name}"
            )
        header = read_geotiff_header(path, ranges=ranges)
        w = entry["window"]
        window = Window(w["col_off"], w["row_off"], w["width"], w["height"])
        nodata = header.nodata[0] if header.nodata else None
        if nodata is None:
            mask_text = "the file declares no nodata value; non-finite values are masked"
        else:
            mask_text = f"nodata {_json_number(nodata)} as the file declares it"
        if qa_status == "read" and std_line is not None:
            qa_text = f"qa_metrics.txt states the CTX-MOLA difference statistics ('{std_line}')"
        elif qa_status == "read":
            qa_text = "qa_metrics.txt has no standard deviation line"
        else:
            qa_text = "the item lists no qa_metrics.txt"
        metadata: ProductMetadata = {
            "body": "mars",
            "provider": provider,
            "version": UNKNOWN,
            "source_url": href,
            "acquisition_time": UNKNOWN,
            "frequency_or_wavelength": None,
            "transmit_receive_convention": None,
            "calibration_type": None,
            "effective_resolution": UNKNOWN,
            "incidence_source": None,
            "qa_definition": (
                f"{mask_text}, masked with quality.validity_mask; {qa_text}; no CTX QA "
                "threshold is applied (an open item, PRD 15)"
            ),
            "license_or_attribution": attribution,
        }
        product_id = f"{_CTX_PREFIX}{self.collection}/{item_id}/{provider_name}"
        bands, record = read_product(
            product_id, [path], metadata, root=root, window=window, ranges=ranges
        )
        band = bands[0]
        mask = validity_mask(band.array, nodata=band.nodata)
        fraction = valid_fraction(mask)
        values = band.array[mask.valid]
        stac_band = None
        raster_bands = dtm_asset.get("raster:bands") if isinstance(dtm_asset, dict) else None
        if isinstance(raster_bands, list) and raster_bands and isinstance(raster_bands[0], dict):
            stated = raster_bands[0]
            stated_nodata = stated.get("nodata")
            stac_band = {
                "data_type": stated.get("data_type"),
                "nodata": stated_nodata,
                "matches_file": stated.get("data_type") == header.dtypes[0]
                and isinstance(stated_nodata, int | float)
                and not isinstance(stated_nodata, bool)
                and nodata is not None
                and float(stated_nodata) == nodata,
            }
        units_known = record.units != UNKNOWN
        basis = (
            f"the centred {w['width']} x {w['height']} window read through "
            f"{entry['range_requests']} HTTP range requests ({entry['bytes_received']} of the "
            f"{entry['remote_bytes']} bytes; the COG is not downloaded whole), header and window "
            "decoded and masked with quality.validity_mask; not plotted"
            + ("" if units_known else ", and the file states no units")
            + ", so the PRD 5 entry rule is not complete"
        )
        dtm = {
            "asset_key": "dtm",
            "item_id": item_id,
            "product_id": product_id,
            "url": entry["url"],
            "final_url": entry["final_url"],
            "provider_file_name": provider_name,
            "header": _header_entry(header),
            "band": band.band,
            "units_stated_by_file": band.units,
            "nodata_stated_by_file": _json_number(band.nodata),
            "vertical_reference": {
                "geoid_tag": header.tags.get("GEOID"),
                "documentation_quote": None if areoid is None else areoid.group(0),
                "documentation": documentation_url,
            },
            "stac_raster_band": stac_band,
            "window": w,
            "range_read": {
                "url": entry["url"],
                "final_url": entry["final_url"],
                "http_status": entry["http_status"],
                "content_type": entry["content_type"],
                "last_modified": entry["last_modified"],
                "etag": entry["etag"],
                "remote_bytes": entry["remote_bytes"],
                "bytes_received": entry["bytes_received"],
                "range_requests": entry["range_requests"],
                "ranges": entry["ranges"],
                "exchanges": entry["exchanges"],
                "local_path": record.local_path,
                "local_sha256": record.checksum,
                "retrieved_at": entry["retrieved_at"],
                "whole_file_downloaded": ranges.covers_whole_file,
                "sha256_note": (
                    "the whole file was not downloaded, so its SHA-256 is unknown; local_sha256 "
                    "is that of the byte ranges held in local_path (COG header and window tiles)"
                ),
            },
            "valid": {
                "method": (
                    "quality.validity_mask(window, nodata=the file's nodata) and "
                    "quality.valid_fraction"
                ),
                "valid_pixels": fraction.valid_pixels,
                "invalid_pixels": fraction.invalid_pixels,
                "total_pixels": fraction.total_pixels,
                "valid_fraction": fraction.fraction,
                "invalid_by_reason": {
                    reason.label: count
                    for reason, count in mask.reason_counts().items()
                    if reason != InvalidReason.VALID
                },
                "min_stored_valid": float(np.min(values)) if values.size else None,
                "max_stored_valid": float(np.max(values)) if values.size else None,
            },
        }
        return dtm, record, basis

    def _ranges(self, path: Path, entry: Mapping[str, Any]) -> ByteRanges:
        try:
            return ByteRanges(
                name=_provider_name(entry),
                size=int(entry["remote_bytes"]),
                ranges=tuple((int(first), int(last)) for first, last in entry["ranges"]),
                data=path.read_bytes(),
            )
        except ValueError as error:
            raise PilotError(
                f"data/raw/{self.name}/{FETCH_METADATA} describes the byte ranges in {path.name} "
                f"wrongly ({error}); run: {COMMAND} fetch {self.name}"
            ) from error

    @staticmethod
    def _field_notes() -> dict[str, str]:
        return {
            "units": (
                "the DTM COG states no band units, so its ProductRecord units are unknown; "
                "qa_metrics.txt states its differences in the unit it writes after each value"
            ),
            "acquisition_time": (
                "unknown: the STAC item gives start_datetime and end_datetime (chosen_item."
                "properties) without defining them as the CTX image acquisition times"
            ),
            "version": "unknown: neither the STAC collection nor the item states a version",
            "effective_resolution": (
                "unknown: neither the STAC item nor the file states one for this DTM"
            ),
            "crs_and_frame": (
                "the WKT the COG states; the STAC item's proj:wkt2 is not used for the record"
            ),
            "probe_boxes": (
                "the probe boxes count CTX DTM coverage for G0; they are not target windows and "
                "set nothing in config/run.yaml"
            ),
        }


def _same_numbers(texts: Sequence[str], values: Sequence[float]) -> bool:
    try:
        return len(texts) == len(values) and all(
            Decimal(text) == Decimal(repr(float(value)))
            for text, value in zip(texts, values, strict=True)
        )
    except InvalidOperation:
        return False


def _gazetteer_extent(html: str) -> dict[str, str] | None:
    """The extent an IAU Gazetteer feature page shows for its selected network and system.

    The page lists one table row per control network (``geom-<id>``) and coordinate system
    (``cs-<id>``); the selected network is the ``selected`` option and the system the hidden
    ``selected_coordinate_system`` input. None when any of it is missing.
    """
    network = _GAZETTEER_NETWORK.search(html)
    system_id = _GAZETTEER_SYSTEM_ID.search(html)
    system_name = _GAZETTEER_SYSTEM_NAME.search(html)
    if network is None or system_id is None or system_name is None:
        return None
    stated = {
        "control_network": unescape(network.group(1)),
        "control_network_id": network.group(2),
        "coordinate_system": unescape(system_name.group(1)),
        "coordinate_system_id": system_id.group(1),
    }
    for key, label in _GAZETTEER_FIELDS:
        row = re.search(
            rf'<tr class="geometry geom-{network.group(2)} cs-{system_id.group(1)}">\s*'
            rf"<th>{label}</th>\s*<td>\s*([-+0-9.]+)\s*&deg;",
            html,
        )
        if row is None:
            return None
        stated[key] = row.group(1)
    return stated


def _gazetteer_confirms(stated: Mapping[str, str], bbox: Sequence[float]) -> bool:
    """Whether the Gazetteer's 0-360 east extent is ``bbox`` (west, south, east, north)."""
    if stated["coordinate_system"] != _EAST_0_360:
        return False

    def longitude(text: str) -> Decimal:
        value = Decimal(text)
        return value - 360 if value > 180 else value

    try:
        west, south, east, north = (Decimal(repr(float(value))) for value in bbox)
        return (
            longitude(stated["westmost_longitude"]) == west
            and Decimal(stated["southmost_latitude"]) == south
            and longitude(stated["eastmost_longitude"]) == east
            and Decimal(stated["northmost_latitude"]) == north
        )
    except (InvalidOperation, ValueError):
        return False


# --- cop30: Copernicus DEM GLO-30 Public COGs on AWS Open Data (PRD 5 row 11, 8.7, 9 G0) --------

_COP30_PREFIX: Final = "COP30/"
_COP30_TILE: Final = re.compile(r"Copernicus_DSM_COG_10_[NS][0-9]{2}_00_[EW][0-9]{3}_00_DEM")
_TILE_LIST_MAGIC: Final = (b"Copernicus_DSM_COG_",)
_REQUESTED_RANGE: Final = re.compile(r"bytes=([0-9]+)-([0-9]+)")
_LICENCE_LINK: Final = re.compile(
    r'(?is)>\s*Licen[cs]e\s*</h[1-6]>\s*<p>(?:(?!</p>).)*?href\s*=\s*"([^"]+)"'
)
COP30_BUDGET_BYTES: Final = 4_000_000
"""The live-probe DEM budget of PRD 8.7 ("DEM reads ≤ 4 MB"), read as 4,000,000 bytes."""
COP30_VALID_FRACTION_RULE: Final = 0.8
"""PRD 8.7 step 4: a live probe needs a valid fraction of at least 0.8, or it stops."""
_COP30_CANDIDATE_RULE: Final = (
    "the first candidate, in the order listed, whose tile name appears in the bucket's "
    "tileList.txt; a tile absent from it is withheld from the GLO-30 Public release or missing, "
    "and the next candidate is used"
)
_SURFACE_MODEL_CAVEAT: Final = (
    "Copernicus GLO-30 is a digital surface model: its heights include vegetation, buildings "
    "and infrastructure, so terrain statistics from it are not bare-ground statistics unless the "
    "window is masked to bare or sparse cover (PRD 5 row 11, D31); no such mask is applied here"
)


@dataclass(frozen=True)
class _Cop30Quote:
    """A statement read from a saved provider page: its key, the page's role and the pattern."""

    key: str
    role: str
    pattern: re.Pattern[str]


_COP30_QUOTES: Final = (
    _Cop30Quote(
        "readme_dsm",
        "bucket_readme",
        re.compile(r"The Copernicus DEM is a Digital Surface Model \(DSM\)[^.]*\."),
    ),
    _Cop30Quote(
        "readme_withheld_tiles",
        "bucket_readme",
        re.compile(r"GLO-30 Public provides limited worldwide coverage[^.]*\."),
    ),
    _Cop30Quote(
        "readme_source_dataset",
        "bucket_readme",
        re.compile(r"For source data, we used the ([A-Za-z0-9_-]+) dataset for 30 meters"),
    ),
    _Cop30Quote(
        "readme_tile_list",
        "bucket_readme",
        re.compile(r"The list of tiles is saved in each bucket as tileList\.txt ?\."),
    ),
    _Cop30Quote(
        "readme_tile_size",
        "bucket_readme",
        re.compile(
            r"GLO-30 Public The properties of COG files specific to this dataset:"
            r"(?:(?!GLO-90).)*?TIFF tile size: ([0-9]+) \* ([0-9]+) pixels"
        ),
    ),
    _Cop30Quote(
        "readme_licence",
        "bucket_readme",
        re.compile(r"GLO-30 Public and GLO-90 are available on a free basis[^.]*\."),
    ),
    _Cop30Quote(
        "registry_version",
        "registry_entry",
        re.compile(
            r"Data is provided as Cloud Optimized GeoTIFFs and comes from Copernicus DEM "
            r"([0-9]{4}) release\."
        ),
    ),
    _Cop30Quote(
        "registry_licence",
        "registry_entry",
        re.compile(r"GLO-30 Public and GLO-90 are available on a free basis[^.]*\."),
    ),
    _Cop30Quote(
        "collection_dsm",
        "collection_description",
        re.compile(r"The Copernicus DEM is a Digital Surface Model \(DSM\) that represents[^.]*\."),
    ),
    _Cop30Quote(
        "collection_vertical_datum",
        "collection_description",
        re.compile(r"Vertical (EGM[0-9]+) \(EPSG ([0-9]+)\)"),
    ),
    _Cop30Quote(
        "collection_vertical_unit",
        "collection_description",
        re.compile(r"Vertical Unit: ([A-Za-z]+)"),
    ),
    _Cop30Quote(
        "collection_licence",
        "collection_description",
        re.compile(r"The GLO-30 and GLO-90 datasets are available worldwide with a free license\."),
    ),
    _Cop30Quote(
        "collection_notice",
        "collection_description",
        re.compile(
            r"distributing the Copernicus WorldDEM-30 \(GLO-30\), the User shall inform the "
            r"General Public of the source by using the following notice: "
            r"(©[^;]*; all rights reserved)"
        ),
    ),
    _Cop30Quote(
        "collection_acquisition",
        "collection_description",
        re.compile(r"Data were acquired by the TanDEM-X mission between [0-9]{4} and [0-9]{4}\."),
    ),
)


def cop30_tile_name(latitude: float, longitude: float) -> str:
    """The GLO-30 tile holding a point: the 1-degree cell whose south-west corner is floored.

    The bucket readme names tiles ``Copernicus_DSM_COG_10_<northing>_<easting>_DEM``, where 10 is
    the resolution in arc seconds and the northing and easting are whole degrees with ``_00``
    (``N64_00``, ``W017_00``) at the cell's south and west edges.
    """
    if not (math.isfinite(latitude) and math.isfinite(longitude)) or not (
        -90.0 <= latitude < 90.0 and -180.0 <= longitude < 180.0
    ):
        raise PilotError(
            f"({latitude}, {longitude}) is not a latitude in [-90, 90) and a longitude in "
            "[-180, 180), so it lies in no GLO-30 tile"
        )
    south, west = math.floor(latitude), math.floor(longitude)
    northing = f"{'N' if south >= 0 else 'S'}{abs(south):02d}_00"
    easting = f"{'E' if west >= 0 else 'W'}{abs(west):03d}_00"
    return f"Copernicus_DSM_COG_10_{northing}_{easting}_DEM"


@dataclass(frozen=True)
class Cop30Candidate:
    """A tile the cop30 source may read, the window centre in it, and where the centre is from."""

    key: str
    name: str
    latitude: float
    longitude: float
    basis: str

    @property
    def tile(self) -> str:
        return cop30_tile_name(self.latitude, self.longitude)


_HOLUHRAUN: Final = Cop30Candidate(
    key="holuhraun",
    name="Holuhraun, Iceland",
    latitude=64.85,
    longitude=-16.83,
    basis=(
        "the coordinates the English Wikipedia article 'Holuhraun' states (64.85° N, 16.83° W; "
        "https://en.wikipedia.org/wiki/Holuhraun, read on 2026-11-14 when this source was "
        "written). The pilot does not download that page (Wikipedia answers Python's default "
        "HTTP client with HTTP 403), so the centre is a stated choice of the pilot, not an "
        "observation; PRD 5 names Holuhraun as the first Earth region"
    ),
)
_N65_W017: Final = Cop30Candidate(
    key="fallback_n65_w017",
    name="the GLO-30 tile north of the Holuhraun tile",
    latitude=65.5,
    longitude=-16.5,
    basis=(
        "the centre of the 1-degree cell N65 W017, the next cell north of the Holuhraun tile; "
        "the documented fallback, read only when the Holuhraun tile is absent from tileList.txt"
    ),
)


@dataclass(frozen=True)
class Cop30ProbeWindow:
    """The live-probe window in one GLO-30 tile (PRD 5 Earth windows, 8.7 footprint).

    ``utm_bounds`` and ``geographic_bounds`` are (west, south, east, north), in metres of
    ``utm_epsg`` and in degrees of EPSG:4326. The pixel window holds every pixel whose cell, as
    the file's geotransform defines it, intersects the geographic bounds.
    """

    utm_epsg: int
    centre_utm: tuple[float, float]
    utm_bounds: tuple[float, float, float, float]
    geographic_bounds: tuple[float, float, float, float]
    col_off: int
    row_off: int
    width: int
    height: int

    @property
    def window(self) -> Window:
        return Window(self.col_off, self.row_off, self.width, self.height)

    def pixel_window(self) -> dict[str, int]:
        return {
            "col_off": self.col_off,
            "row_off": self.row_off,
            "width": self.width,
            "height": self.height,
        }


def cop30_probe_window(
    header: GeoTiffHeader,
    latitude: float,
    longitude: float,
    side_m: float,
    *,
    densify_points: int = 21,
) -> Cop30ProbeWindow:
    """The ``side_m`` square around a point, in the local UTM zone, as a pixel window of a tile.

    PRD 5: Earth windows are computed in the local UTM zone with pyproj. The zone is the
    standard 6-degree zone of the longitude (north or south by the latitude); the Norway and
    Svalbard exceptions are not applied. The square's edges, densified to ``densify_points``
    points each, are transformed to EPSG:4326 and bounded. Raises PilotError when the tile's CRS
    as read is not EPSG:4326, when it has no north-up geotransform, or when the window leaves
    the tile, because the pilot reads one tile.
    """
    if header.crs is None:
        raise PilotError("the tile states no CRS, so the probe window cannot be placed in it")
    crs = pyproj.CRS.from_wkt(header.crs)
    if crs.to_epsg() != 4326:
        raise PilotError(
            f"the tile's CRS as read is {crs.name!r}, not EPSG:4326; the probe window is "
            "computed for EPSG:4326 tiles only"
        )
    t = header.transform
    if t is None or t.b != 0 or t.d != 0 or t.a <= 0 or t.e >= 0:
        raise PilotError("the tile has no north-up geotransform, so the window cannot be placed")
    if not -80.0 <= latitude <= 84.0 or not -180.0 <= longitude < 180.0:
        raise PilotError(f"({latitude}, {longitude}) lies outside the UTM zones")
    zone = min(math.floor((longitude + 180.0) / 6.0) + 1, 60)
    epsg = (32600 if latitude >= 0 else 32700) + zone
    forward = pyproj.Transformer.from_crs(4326, epsg, always_xy=True)
    x, y = (float(value) for value in forward.transform(longitude, latitude))
    half = side_m / 2.0
    utm_bounds = (x - half, y - half, x + half, y + half)
    back = pyproj.Transformer.from_crs(epsg, 4326, always_xy=True)
    west, south, east, north = (
        float(value) for value in back.transform_bounds(*utm_bounds, densify_pts=densify_points)
    )
    col_off = math.floor((west - t.c) / t.a)
    col_end = math.ceil((east - t.c) / t.a)
    row_off = math.floor((t.f - north) / -t.e)
    row_end = math.ceil((t.f - south) / -t.e)
    if col_off < 0 or row_off < 0 or col_end > header.width or row_end > header.height:
        raise PilotError(
            f"the {side_m:g} m window around ({latitude}, {longitude}) leaves the "
            f"{header.width} x {header.height} tile (pixel columns {col_off} to {col_end - 1}, "
            f"rows {row_off} to {row_end - 1}); the pilot reads one tile"
        )
    return Cop30ProbeWindow(
        utm_epsg=epsg,
        centre_utm=(x, y),
        utm_bounds=utm_bounds,
        geographic_bounds=(west, south, east, north),
        col_off=col_off,
        row_off=row_off,
        width=col_end - col_off,
        height=row_end - row_off,
    )


def _phased_exchange_entry(exchange: RangeExchange) -> dict[str, Any]:
    return {**_exchange_entry(exchange), "phase": exchange.phase}


def _tile_names(path: Path) -> tuple[list[str], int, int]:
    """The tile names tileList.txt lists, its line count and the lines that are not tile names."""
    lines = [line.strip() for line in _decode(path).splitlines()]
    names = [line for line in lines if _COP30_TILE.fullmatch(line)]
    others = sum(1 for line in lines if line and _COP30_TILE.fullmatch(line) is None)
    return names, len(lines), others


@dataclass(frozen=True)
class Cop30Source:
    """Copernicus DEM GLO-30 Public (PRD 5 row 11; 9 G0: one Copernicus COG read).

    fetch downloads the bucket's tileList.txt, its readme, the AWS registry entry and the
    Copernicus DEM collection description (the licence page the registry entry links), picks
    the first candidate tile tileList.txt lists, and reads the live-probe window (PRD 8.7) of
    that tile's COG through HTTP range requests, header and layout included; only the byte
    ranges read are stored, with the HTTP metadata of every request. The bucket is read
    anonymously over HTTPS: no account, key, token or signature. record reads only those files.
    """

    bucket: str = "copernicus-dem-30m"
    region: str = "eu-central-1"
    endpoint: str | None = None
    registry_url: str = "https://registry.opendata.aws/copernicus-dem/"
    collection_url: str = (
        "https://dataspace.copernicus.eu/explore-data/data-collections/"
        "copernicus-contributing-missions/collections-description/COP-DEM"
    )
    candidates: tuple[Cop30Candidate, ...] = (_HOLUHRAUN, _N65_W017)
    window_side_m: float = 5000.0
    densify_points: int = 21
    budget_bytes: int = COP30_BUDGET_BYTES
    range_block_bytes: int = RANGE_BLOCK_BYTES
    whole_file_limit_bytes: int = WHOLE_FILE_LIMIT_BYTES

    @property
    def name(self) -> str:
        return "cop30"

    @property
    def base_url(self) -> str:
        """The bucket's S3 virtual-hosted-style HTTPS endpoint in its region (or a test stub)."""
        if self.endpoint is not None:
            return self.endpoint.rstrip("/")
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com"

    @property
    def tile_list_url(self) -> str:
        return f"{self.base_url}/tileList.txt"

    @property
    def readme_url(self) -> str:
        return f"{self.base_url}/readme.html"

    def tile_url(self, tile: str) -> str:
        return f"{self.base_url}/{tile}/{tile}.tif"

    def s3_uri(self, tile: str) -> str:
        return f"s3://{self.bucket}/{tile}/{tile}.tif"

    def _documents(self) -> tuple[tuple[str, str, str], ...]:
        return (
            ("tile_list", self.tile_list_url, "tileList.txt"),
            ("bucket_readme", self.readme_url, "readme.html"),
            ("registry_entry", self.registry_url, "aws_registry_copernicus_dem.html"),
            ("collection_description", self.collection_url, "cdse_cop_dem_collection.html"),
        )

    def _choose(self, listed: Sequence[str]) -> Cop30Candidate | None:
        present = set(listed)
        return next((c for c in self.candidates if c.tile in present), None)

    def _probe(self, header: GeoTiffHeader, candidate: Cop30Candidate) -> Cop30ProbeWindow:
        return cop30_probe_window(
            header,
            candidate.latitude,
            candidate.longitude,
            self.window_side_m,
            densify_points=self.densify_points,
        )

    # -- fetch --

    def fetch(
        self, raw_dir: Path, download: Downloader, read_range: RangeFetcher, now: Clock
    ) -> None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=raw_dir, prefix=".fetch-") as staging_name:
            staging = Path(staging_name)
            documents: list[dict[str, Any]] = []
            for role, url, file_name in self._documents():
                magic = _TILE_LIST_MAGIC if role == "tile_list" else ()
                fetched = download(
                    url, staging / file_name, max_bytes=self.whole_file_limit_bytes, magic=magic
                )
                documents.append(_fetch_entry(fetched, now(), role=role))
            listed, _, _ = _tile_names(staging / "tileList.txt")
            chosen = self._choose(listed)
            files: list[dict[str, Any]] = []
            if chosen is not None:
                tile = chosen.tile
                provider_name = f"{tile}.tif"
                remote = read_remote_cog_window(
                    self.tile_url(tile),
                    lambda header: self._probe(header, chosen).window,
                    fetch=read_range,
                    block_bytes=self.range_block_bytes,
                    max_bytes=self.whole_file_limit_bytes,
                    now=now,
                )
                local_name = f"{provider_name}{_RANGES_SUFFIX}"
                (staging / local_name).write_bytes(remote.ranges.data)
                files.append(
                    _window_entry(
                        remote,
                        local_name,
                        now(),
                        role="dem_window",
                        candidate=chosen.key,
                        tile=tile,
                        s3_uri=self.s3_uri(tile),
                        provider_file_name=provider_name,
                        range_block_bytes=self.range_block_bytes,
                        exchanges=[_phased_exchange_entry(e) for e in remote.exchanges],
                    )
                )
            metadata = {
                "source": self.name,
                "command": f"{COMMAND} fetch {self.name}",
                "bucket": self.bucket,
                "region": self.region,
                "base_url": self.base_url,
                "whole_file_limit_bytes": self.whole_file_limit_bytes,
                "documents": documents,
                "files": files,
                "choice": {
                    "rule": _COP30_CANDIDATE_RULE,
                    "candidates": [candidate.key for candidate in self.candidates],
                    "chosen": None if chosen is None else chosen.key,
                },
            }
            (raw_dir / FETCH_METADATA).unlink(missing_ok=True)
            for entry in [*documents, *files]:
                os.replace(staging / entry["file_name"], raw_dir / entry["file_name"])
            _write_atomically(raw_dir / FETCH_METADATA, canonical_json(metadata))

    # -- build --

    def build(self, raw_dir: Path, root: Path) -> Build:
        fetched = _load_fetch(raw_dir, self.name)
        try:
            return self._build(raw_dir, root, fetched)
        except (KeyError, TypeError) as error:
            raise PilotError(
                f"data/raw/{self.name}/{FETCH_METADATA} lacks an expected entry ({error!r}); "
                f"run: {COMMAND} fetch {self.name}"
            ) from error

    def _local(self, file_name: str) -> str:
        return f"{RAW_DIR.as_posix()}/{self.name}/{file_name}"

    def _refetch(self, what: str) -> PilotError:
        return PilotError(f"{what}; run: {COMMAND} fetch {self.name}")

    def _build(self, raw_dir: Path, root: Path, fetched: Mapping[str, Any]) -> Build:
        documents = {entry["role"]: entry for entry in fetched["documents"]}
        for role, url, file_name in self._documents():
            entry = documents[role]
            if entry["url"] != url or entry["file_name"] != file_name:
                raise self._refetch(f"fetch.json's {role} is not {url}")
        quotes = self._quotes(raw_dir, documents)
        links = self._licence_links(raw_dir, documents)

        list_entry = documents["tile_list"]
        listed, line_count, others = _tile_names(raw_dir / list_entry["file_name"])
        chosen = self._choose(listed)
        choice = fetched["choice"]
        if choice["candidates"] != [candidate.key for candidate in self.candidates] or (
            choice["chosen"] != (None if chosen is None else chosen.key)
        ):
            raise self._refetch(
                f"fetch.json chose {choice['chosen']!r}, but the rule gives "
                f"{None if chosen is None else chosen.key!r} on the saved tileList.txt"
            )
        tile_choice = self._tile_choice(set(listed), chosen)

        records: list[ProductRecord] = []
        verification: dict[str, dict[str, str]] = {}
        attribution = self._attribution(quotes, links)
        dem: dict[str, Any] | None = None
        if chosen is not None:
            dem, product, basis = self._dem(raw_dir, root, fetched, chosen, quotes, attribution)
            records.append(product)
            verification[product.product_id] = {
                "status": product.verification_status,
                "basis": basis,
            }
        elif fetched["files"]:
            raise self._refetch("fetch.json lists a tile window, but no candidate tile is listed")

        retrieved = sorted(
            entry["retrieved_at"] for entry in [*fetched["documents"], *fetched["files"]]
        )
        result = {
            "record": "cop30_range_read",
            "description": (
                "One window of one Copernicus DEM GLO-30 Public COG read by HTTP range requests "
                "from the public AWS bucket, server-side, as fetched by the T1 pilot (PRD 5 row "
                "11; 8.7 live-probe footprint and budget; 9 G0, one Copernicus COG read). Raw "
                "observations only: this read sends no Origin header and is not the "
                "browser-origin CORS check (T1-07), cop30_mode is chosen by the team at G0, and "
                "the shipping mode and the G0 status are a human checkpoint (PRD 23.6)."
            ),
            "source": self.name,
            "bucket": {
                "s3_uri": f"s3://{self.bucket}/",
                "region": self.region,
                "https_base_url": self.base_url,
                "url_rule": (
                    "https://<bucket>.s3.<region>.amazonaws.com/<tile>/<tile>.tif, the S3 "
                    "virtual-hosted-style URL of s3://<bucket>/<tile>/<tile>.tif"
                ),
                "credentials": (
                    "none: anonymous HTTPS GET requests with a Range header; no account, key, "
                    "token or signature"
                ),
            },
            "tile_list": {
                "url": list_entry["url"],
                "local_path": self._local(list_entry["file_name"]),
                "http_status": list_entry["http_status"],
                "bytes_received": list_entry["bytes_received"],
                "sha256": list_entry["sha256"],
                "last_modified": list_entry["last_modified"],
                "retrieved_at": list_entry["retrieved_at"],
                "lines": line_count,
                "tiles_listed": len(set(listed)),
                "lines_not_tile_names": others,
                "readme_statement": quotes["readme_tile_list"],
                "withheld_tiles_statement": quotes["readme_withheld_tiles"],
            },
            "tile_choice": tile_choice,
            "dem": dem,
            "surface_model_caveat": {
                "caveat": _SURFACE_MODEL_CAVEAT,
                "quotes": [
                    quotes[key]
                    for key in ("readme_dsm", "collection_dsm")
                    if quotes[key]["quote"] is not None
                ],
            },
            "licence": {
                "license_or_attribution": attribution,
                "collection_licence": quotes["collection_licence"],
                "collection_notice": quotes["collection_notice"],
                "registry_licence": quotes["registry_licence"] | links["registry_entry"],
                "readme_licence": quotes["readme_licence"] | links["bucket_readme"],
                "note": (
                    "the AWS registry entry links its licence statement to the Copernicus DEM "
                    "collection description, which fetch saved; the readme's licence link is "
                    "recorded as stated and not fetched"
                ),
            },
            "version": {
                "value": self._version(quotes),
                "quote": quotes["registry_version"],
                "file_states": None,
            },
            "acquisition": quotes["collection_acquisition"],
            "documents": [
                {
                    key: entry[key]
                    for key in (
                        "role",
                        "file_name",
                        "url",
                        "final_url",
                        "http_status",
                        "content_type",
                        "bytes_received",
                        "sha256",
                        "last_modified",
                        "retrieved_at",
                    )
                }
                | {"local_path": self._local(entry["file_name"])}
                for entry in fetched["documents"]
            ],
            "retrieval": {
                "first_retrieved_at": retrieved[0],
                "last_retrieved_at": retrieved[-1],
                "fetch_metadata": self._local(FETCH_METADATA),
                "command": fetched["command"],
            },
            "verification": verification,
            "open_g0_items": [
                "G0 needs a Copernicus COG read from a browser origin with the CORS result "
                "recorded (PRD 9); this read is server-side and sends no Origin header. The "
                "Origin-header check is T1-07, and cop30_mode is chosen by the team at G0",
                "The effective support of GLO-30 (PRD 5 row 11, R16) is not established here",
                "No WorldCover or other bare-ground mask is applied (D31)",
                "The G0 status and the shipping mode are a human checkpoint (PRD 23.6)",
            ],
            "field_notes": self._field_notes(),
            "tool_versions": tool_versions(),
        }
        return Build(
            files={"outputs/pilot/cop30_range_read.json": canonical_json(result)},
            records=tuple(records),
            record_prefix=_COP30_PREFIX,
        )

    # -- build: provider statements --

    def _quotes(
        self, raw_dir: Path, documents: Mapping[str, Mapping[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        texts = {
            role: _page_text(_decode(raw_dir / documents[role]["file_name"]))
            for role in ("bucket_readme", "registry_entry", "collection_description")
        }
        quotes: dict[str, dict[str, Any]] = {}
        for spec in _COP30_QUOTES:
            match = spec.pattern.search(texts[spec.role])
            entry = documents[spec.role]
            quotes[spec.key] = {
                "quote": None if match is None else match.group(0),
                "groups": [] if match is None else list(match.groups()),
                "document": entry["url"],
                "local_path": self._local(entry["file_name"]),
            }
        return quotes

    def _licence_links(
        self, raw_dir: Path, documents: Mapping[str, Mapping[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        links: dict[str, dict[str, Any]] = {}
        for role in ("bucket_readme", "registry_entry"):
            entry = documents[role]
            html = _decode(raw_dir / entry["file_name"])
            match = _LICENCE_LINK.search(html)
            href = None if match is None else urllib.parse.urljoin(entry["final_url"], match[1])
            links[role] = {
                "link": href,
                "links_to_collection_description": href == self.collection_url,
            }
        return links

    def _attribution(
        self, quotes: Mapping[str, Mapping[str, Any]], links: Mapping[str, Mapping[str, Any]]
    ) -> str:
        licence = quotes["collection_licence"]["quote"]
        notice = quotes["collection_notice"]["groups"]
        parts = []
        if licence is not None:
            parts.append(
                f"licence as the Copernicus DEM collection description states: '{licence}'"
            )
        if notice:
            parts.append(f"required notice for GLO-30, as it states: '{notice[0]}'")
        if not parts:
            return UNKNOWN
        linked = links["registry_entry"]["links_to_collection_description"]
        where = f"source: {self.collection_url}" + (
            ", the page the AWS registry entry links as the licence" if linked else ""
        )
        return f"{'; '.join(parts)} ({where})"

    @staticmethod
    def _version(quotes: Mapping[str, Mapping[str, Any]]) -> str:
        groups = quotes["registry_version"]["groups"]
        if not groups:
            return UNKNOWN
        return f"Copernicus DEM {groups[0]} release, as the AWS registry entry states"

    def _provider(self, quotes: Mapping[str, Mapping[str, Any]]) -> str:
        groups = quotes["readme_source_dataset"]["groups"]
        dataset = f" (source dataset {groups[0]}, as the bucket readme states)" if groups else ""
        return (
            f"Copernicus DEM GLO-30 Public{dataset}; AWS Open Data bucket s3://{self.bucket} "
            f"({self.region})"
        )

    def _tile_choice(self, listed: set[str], chosen: Cop30Candidate | None) -> dict[str, Any]:
        candidates = []
        seen_chosen = False
        for candidate in self.candidates:
            present = candidate.tile in listed
            if candidate == chosen:
                status, seen_chosen = "chosen", True
            elif not present:
                status = "absent"
            else:
                status = "not_needed" if seen_chosen else "absent"
            candidates.append(
                {
                    "key": candidate.key,
                    "name": candidate.name,
                    "tile": candidate.tile,
                    "centre": {"latitude": candidate.latitude, "longitude": candidate.longitude},
                    "centre_basis": candidate.basis,
                    "listed_in_tile_list": present,
                    "status": status,
                }
            )
        first = self.candidates[0]
        reason: str | None = None
        if chosen is None:
            tiles = ", ".join(candidate.tile for candidate in self.candidates)
            reason = (
                f"none of the candidate tiles ({tiles}) is listed in tileList.txt (withheld from "
                "the GLO-30 Public release or missing), so no tile was read"
            )
        elif chosen != first:
            reason = (
                f"{first.tile} is not listed in tileList.txt (withheld from the GLO-30 Public "
                f"release or missing), so the next documented candidate, {chosen.tile}, was read"
            )
        return {
            "rule": _COP30_CANDIDATE_RULE,
            "candidates": candidates,
            "chosen": None if chosen is None else chosen.key,
            "chosen_tile": None if chosen is None else chosen.tile,
            "fallback_used": chosen is not None and chosen != first,
            "reason": reason,
        }

    # -- build: the tile window --

    def _ranges(self, path: Path, entry: Mapping[str, Any]) -> ByteRanges:
        try:
            return ByteRanges(
                name=_provider_name(entry),
                size=int(entry["remote_bytes"]),
                ranges=tuple((int(first), int(last)) for first, last in entry["ranges"]),
                data=path.read_bytes(),
            )
        except ValueError as error:
            raise self._refetch(
                f"data/raw/{self.name}/{FETCH_METADATA} describes the byte ranges in {path.name} "
                f"wrongly ({error})"
            ) from error

    def _exchanges(self, entry: Mapping[str, Any], ranges: ByteRanges) -> dict[str, Any]:
        exchanges = entry["exchanges"]
        requested = 0
        phases: dict[str, dict[str, int]] = {
            phase: {"requests": 0, "bytes_received": 0} for phase in ("header", "window")
        }
        for exchange in exchanges:
            match = _REQUESTED_RANGE.fullmatch(str(exchange["requested_range"]))
            if match is None or exchange["phase"] not in phases:
                raise self._refetch("fetch.json's range request log cannot be read")
            requested += int(match[2]) - int(match[1]) + 1
            phases[exchange["phase"]]["requests"] += 1
            phases[exchange["phase"]]["bytes_received"] += int(exchange["bytes_received"])
        received = sum(int(exchange["bytes_received"]) for exchange in exchanges)
        if (
            not exchanges
            or len(exchanges) != entry["range_requests"]
            or received != entry["bytes_received"]
            or received != len(ranges.data)
            or exchanges[0]["http_status"] != entry["http_status"]
        ):
            raise self._refetch(
                "fetch.json's range request log does not match the stored byte ranges"
            )
        return {
            "http_status": exchanges[0]["http_status"],
            "content_range": exchanges[0]["content_range"],
            "bytes_requested": requested,
            "bytes_by_phase": phases,
            "phase_definition": (
                "header: the requests sent before the window's pixels were decoded (the first "
                "block, which states the object size, the COG header and the layout); window: "
                "the requests sent while the window was decoded"
            ),
        }

    def _dem(
        self,
        raw_dir: Path,
        root: Path,
        fetched: Mapping[str, Any],
        chosen: Cop30Candidate,
        quotes: Mapping[str, Mapping[str, Any]],
        attribution: str,
    ) -> tuple[dict[str, Any], ProductRecord, str]:
        (entry,) = fetched["files"]
        tile = chosen.tile
        url = self.tile_url(tile)
        if (
            entry["role"] != "dem_window"
            or entry["tile"] != tile
            or entry["candidate"] != chosen.key
            or entry["url"] != url
            or not _is_window_read(entry)
            or _provider_name(entry) != f"{tile}.tif"
        ):
            raise self._refetch(f"fetch.json's tile window is not a window read of {url}")
        path = raw_dir / entry["file_name"]
        ranges = self._ranges(path, entry)
        if ranges.covers_whole_file:
            raise self._refetch(
                f"the byte ranges in data/raw/{self.name}/{path.name} hold all {ranges.size} "
                f"bytes of {url}, so the tile was downloaded whole, not read by window"
            )
        header = read_geotiff_header(path, ranges=ranges)
        probe = self._probe(header, chosen)
        if entry["window"] != probe.pixel_window():
            raise self._refetch(
                f"fetch.json's window {entry['window']} is not the probe window "
                f"{probe.pixel_window()} that the saved header gives"
            )
        layout = read_cog_layout(path, probe.window, ranges=ranges)
        tiles = [
            {
                "row": block.row,
                "col": block.col,
                "offset": block.offset,
                "size": block.size,
                "held_in_ranges": (
                    None
                    if block.offset is None or block.size is None
                    else block.size > 0
                    and ranges.holds(block.offset, block.offset + block.size - 1)
                ),
            }
            for block in layout.blocks
        ]
        exchange_summary = self._exchanges(entry, ranges)

        band_units = header.units[0] if header.units else None
        nodata = header.nodata[0] if header.nodata else None
        unit_groups = quotes["collection_vertical_unit"]["groups"]
        if band_units is None and unit_groups:
            units = (
                f"{unit_groups[0]}, as the Copernicus DEM collection description states "
                f"('{quotes['collection_vertical_unit']['quote']}'); the COG states no band units"
            )
        else:
            units = UNKNOWN
        if nodata is None:
            mask_text = "the COG declares no nodata value, so only non-finite values are masked"
        else:
            mask_text = f"nodata {_json_number(nodata)} as the file declares it"
        metadata: ProductMetadata = {
            "body": "earth",
            "provider": self._provider(quotes),
            "version": self._version(quotes),
            "source_url": url,
            "acquisition_time": UNKNOWN,
            "frequency_or_wavelength": None,
            "transmit_receive_convention": None,
            "calibration_type": None,
            "effective_resolution": UNKNOWN,
            "incidence_source": None,
            "qa_definition": (
                f"{mask_text} (quality.validity_mask); no WorldCover or other bare-ground mask is "
                "applied (D31) and the Copernicus DEM quality layers are not read"
            ),
            "license_or_attribution": attribution,
        }
        if units != UNKNOWN:
            metadata["units"] = units
        product_id = f"{_COP30_PREFIX}{self.bucket}/{tile}/{tile}.tif"
        bands, record = read_product(
            product_id, [path], metadata, root=root, window=probe.window, ranges=ranges
        )
        band = bands[0]
        mask = validity_mask(band.array, nodata=band.nodata)
        fraction = valid_fraction(mask)
        values = band.array[mask.valid]
        crs = pyproj.CRS.from_wkt(header.crs) if header.crs is not None else None
        vertical = (
            [sub.name for sub in crs.sub_crs_list if sub.is_vertical]
            if crs is not None and crs.is_compound
            else []
        )
        datum_groups = quotes["collection_vertical_datum"]["groups"]
        west, south, east, north = probe.utm_bounds
        g_west, g_south, g_east, g_north = probe.geographic_bounds
        total = int(entry["bytes_received"])
        default_budget = self.budget_bytes == COP30_BUDGET_BYTES
        dem = {
            "product_id": product_id,
            "tile": tile,
            "candidate": chosen.key,
            "window": {
                "role": (
                    "the live-probe footprint of PRD 8.7 (a 5 km x 5 km window) at a stated "
                    "centre; a pilot probe of the read path, not a target or search window"
                ),
                "side_m": self.window_side_m,
                "centre": {"latitude": chosen.latitude, "longitude": chosen.longitude},
                "centre_basis": chosen.basis,
                "utm_crs": f"EPSG:{probe.utm_epsg}",
                "utm_zone_rule": (
                    "the standard 6-degree UTM zone of the centre longitude, north or south by "
                    "the centre latitude; the Norway and Svalbard exceptions are not applied"
                ),
                "centre_utm_m": {"x": probe.centre_utm[0], "y": probe.centre_utm[1]},
                "utm_bounds_m": {"west": west, "south": south, "east": east, "north": north},
                "geographic_bounds_deg": {
                    "west": g_west,
                    "south": g_south,
                    "east": g_east,
                    "north": g_north,
                },
                "densify_points": self.densify_points,
                "pixel_window": probe.pixel_window(),
                "definition": (
                    "the square of side_m metres centred on the centre in the UTM CRS (PRD 5: "
                    "Earth windows are computed in the local UTM zone with pyproj); its edges, "
                    "densified to densify_points points each, are transformed to EPSG:4326 and "
                    "bounded, and the pixel window holds every pixel whose cell, as the COG's "
                    "geotransform defines it, intersects that bounding box"
                ),
            },
            "range_read": {
                "url": url,
                "s3_uri": entry["s3_uri"],
                "final_url": entry["final_url"],
                "http_status": exchange_summary["http_status"],
                "content_range": exchange_summary["content_range"],
                "content_type": entry["content_type"],
                "etag": entry["etag"],
                "last_modified": entry["last_modified"],
                "object_size_bytes": entry["remote_bytes"],
                "range_requests": entry["range_requests"],
                "bytes_requested": exchange_summary["bytes_requested"],
                "bytes_received": total,
                "bytes_by_phase": exchange_summary["bytes_by_phase"],
                "phase_definition": exchange_summary["phase_definition"],
                "range_block_bytes": entry["range_block_bytes"],
                "ranges": entry["ranges"],
                "exchanges": entry["exchanges"],
                "whole_file_downloaded": ranges.covers_whole_file,
                "local_path": record.local_path,
                "local_sha256": record.checksum,
                "sha256_note": (
                    "the tile was not downloaded whole, so its SHA-256 is unknown; local_sha256 "
                    "is that of the fetched byte ranges held in local_path, at the positions "
                    "listed in ranges"
                ),
                "retrieved_at": entry["retrieved_at"],
            },
            "live_probe_budget": {
                "rule": "PRD 8.7: the first live probe reads at most 4 MB of DEM",
                "limit_bytes": self.budget_bytes,
                "limit_definition": (
                    "'DEM reads ≤ 4 MB' read as 4,000,000 bytes"
                    if default_budget
                    else f"{self.budget_bytes} bytes, as set for this run"
                ),
                "bytes_fetched": total,
                "within_budget": total <= self.budget_bytes,
                "tiles_touched": len(layout.blocks),
                "touched_tile_bytes": layout.stored_bytes,
                "reader": (
                    f"io.read_remote_cog_window with {entry['range_block_bytes']}-byte aligned "
                    "blocks, so bytes_fetched also holds the header, the block alignment and any "
                    "other bytes GDAL read; touched_tile_bytes are the touched tiles' sizes as "
                    "the TIFF tile index states them, null when it states no size for some "
                    "touched tile"
                ),
                "note": (
                    "an observation of this server-side read, not a browser measurement; an "
                    "over-budget read is recorded as over budget and does not fail the command"
                ),
            },
            "cog": {
                "header": _header_entry(header),
                "internal_tile_shape": {
                    "rows": layout.block_shape[0],
                    "columns": layout.block_shape[1],
                },
                "tile_grid": {"across": layout.blocks_across, "down": layout.blocks_down},
                "overviews": [
                    {
                        "level": overview.level,
                        "factor": overview.factor,
                        "width": overview.width,
                        "height": overview.height,
                        "block_shape": list(overview.block_shape),
                    }
                    for overview in layout.overviews
                ],
                "tiles_touched": {
                    "count": len(layout.blocks),
                    "tiles": tiles,
                    "stored_bytes": layout.stored_bytes,
                    "definition": (
                        "the internal COG tiles of band 1 that the pixel window intersects, with "
                        "the byte offset and size the TIFF tile index states (GDAL TIFF "
                        "metadata BLOCK_OFFSET and BLOCK_SIZE); held_in_ranges says whether "
                        "every byte of the tile is in the stored byte ranges. GDAL states no "
                        "offset or size for a tile the file does not store (a sparse file) or "
                        "whose tile index entry it cannot read; such a tile has offset, size "
                        "and held_in_ranges null, and stored_bytes is then null, not a sum over "
                        "the other tiles"
                    ),
                },
                "readme_tile_size": self._readme_tile_size(quotes, layout.block_shape),
            },
            "crs": {
                "wkt": header.crs,
                "name": None if crs is None else crs.name,
                "epsg_identified": None if crs is None else crs.to_epsg(),
                "is_geographic": None if crs is None else crs.is_geographic,
                "stated_by": "the COG's GeoTIFF keys, as GDAL reads them",
            },
            "pixel_size": _header_entry(header)["pixel_size"],
            "vertical_datum": {
                "stated_by_file": ", ".join(vertical) if vertical else UNKNOWN,
                "file_note": (
                    "the COG's CRS is a compound CRS with a vertical part"
                    if vertical
                    else "the COG's CRS has no vertical part, so the file does not state the "
                    "vertical datum"
                ),
                "product_documentation": (
                    None
                    if not datum_groups
                    else {
                        "quote": quotes["collection_vertical_datum"]["quote"],
                        "datum": datum_groups[0],
                        "epsg": int(datum_groups[1]),
                        "document": quotes["collection_vertical_datum"]["document"],
                        "local_path": quotes["collection_vertical_datum"]["local_path"],
                    }
                ),
            },
            "nodata": {
                "stated_by_file": _json_number(nodata),
                "note": mask_text,
            },
            "units": {
                "stated_by_file": band_units,
                "product_documentation": (
                    None
                    if not unit_groups
                    else {
                        "quote": quotes["collection_vertical_unit"]["quote"],
                        "unit": unit_groups[0],
                        "document": quotes["collection_vertical_unit"]["document"],
                        "local_path": quotes["collection_vertical_unit"]["local_path"],
                    }
                ),
                "record_units": record.units,
            },
            "values": {
                "method": (
                    "quality.validity_mask(window, nodata=the file's nodata) and "
                    "quality.valid_fraction over the stored values; min and max over valid pixels"
                ),
                "valid_pixels": fraction.valid_pixels,
                "invalid_pixels": fraction.invalid_pixels,
                "total_pixels": fraction.total_pixels,
                "valid_fraction": fraction.fraction,
                "invalid_by_reason": {
                    reason.label: count
                    for reason, count in mask.reason_counts().items()
                    if reason != InvalidReason.VALID
                },
                "elevation_min": float(np.min(values)) if values.size else None,
                "elevation_max": float(np.max(values)) if values.size else None,
                "elevation_units": record.units,
                "live_probe_quality_rule": {
                    "rule": "PRD 8.7 step 4: a live probe needs a valid fraction of at least 0.8",
                    "threshold": COP30_VALID_FRACTION_RULE,
                    "met": fraction.fraction >= COP30_VALID_FRACTION_RULE,
                },
            },
        }
        basis = (
            f"the {self.window_side_m:g} m probe window ({probe.width} x {probe.height} pixels) "
            f"read through {entry['range_requests']} HTTP range requests ({total} of the "
            f"{entry['remote_bytes']} bytes; the COG is not downloaded whole), header, layout and "
            "window decoded and masked with quality.validity_mask; not plotted, so the PRD 5 "
            "entry rule is not complete"
        )
        return dem, record, basis

    @staticmethod
    def _readme_tile_size(
        quotes: Mapping[str, Mapping[str, Any]], block_shape: tuple[int, int]
    ) -> dict[str, Any]:
        stated = quotes["readme_tile_size"]
        groups = stated["groups"]
        if not groups:
            return {"quote": None, "rows": None, "columns": None, "matches_file": None}
        # The readme writes "width * height"; GDAL's block shape is (rows, columns).
        columns, rows = int(groups[0]), int(groups[1])
        return {
            "quote": stated["quote"],
            "rows": rows,
            "columns": columns,
            "matches_file": (rows, columns) == block_shape,
            "document": stated["document"],
        }

    @staticmethod
    def _field_notes() -> dict[str, str]:
        return {
            "units": (
                "the COG states no band units; the record's units are those the Copernicus DEM "
                "collection description states for the vertical axis, else unknown"
            ),
            "vertical_datum": (
                "read from the file's CRS where it has a vertical part, else unknown for the "
                "file; the collection description's statement is quoted beside it"
            ),
            "version": (
                "the release the AWS registry entry states; the COG states no version, so the "
                "release of this tile is not read from the file"
            ),
            "acquisition_time": (
                "unknown for this tile: the COG states no acquisition time; acquisition quotes "
                "the collection-level statement of the collection description"
            ),
            "effective_resolution": (
                "unknown: the COG states none and this record derives none (PRD 5 row 11, R16)"
            ),
            "crs_and_frame": "the horizontal CRS WKT the COG states",
            "centre": (
                "the window centre is a stated pilot choice with its basis in tile_choice; it "
                "is not a target window and sets nothing in config/run.yaml"
            ),
        }


# --- wc: ESA WorldCover, one window around the T1-06 area -------------------------------------

_WORLDCOVER_PREFIX: Final = "WorldCover/"
WORLDCOVER_MAX_WINDOW_PIXELS: Final = 512
"""The largest side, in pixels, of the WorldCover window this story reads (T1-07)."""
_WORLDCOVER_TILE: Final = re.compile(r"[NS][0-9]{2}[EW][0-9]{3}")
_GEOJSON_MAGIC: Final = (b"{",)
_LEGEND_LINE: Final = re.compile(r"([0-9]+)\s+(\S.*?)\s*")
_WORLDCOVER_YEAR_REASON: Final = (
    "v200 (2021) is pinned: the bucket holds two releases, v100 (2020) and v200 (2021), and the "
    "pilot reads the later one. PRD 5 row 12 asks for one pinned year; the pin is a pilot choice. "
    "Neither year is the date of a radar acquisition (the Holuhraun UAVSAR scene of PRD 5 is from "
    "May 2015), so the map states land cover in 2021, not on any radar date"
)
_WORLDCOVER_TILE_RULE: Final = (
    "the tile of the pinned release's tiling grid (esa_worldcover_grid.geojson, saved by fetch) "
    "whose 3 x 3 degree square holds the T1-06 window centre, west and south edges inclusive; "
    "the T1-06 window's geographic bounds must lie inside it, because the pilot reads one tile"
)
_WORLDCOVER_WINDOW_RULE: Final = (
    "area_pixel_window holds every map pixel whose cell, as the COG's geotransform defines it, "
    "intersects the T1-06 window's geographic bounds. The read window takes at most "
    "max_window_pixels columns and rows of it: centred on the T1-06 centre's pixel (offset by "
    "half the window side, rounded down) and shifted, if needed, to stay inside "
    "area_pixel_window. fraction_of_area_window is the pixel count read over the pixel count of "
    "area_pixel_window"
)
_BARE_GROUND_CAVEAT: Final = (
    "a pixel of the bare/sparse vegetation class does not establish dry bare mineral soil on the "
    "radar acquisition date (PRD 5 row 12): the class is the map's label for the pinned year, "
    "and says nothing about moisture, snow on the radar date or surface change since"
)


def parse_worldcover_legend(text: str) -> list[dict[str, Any]]:
    """The class codes and names of a WorldCover ``legend`` tag, in the order it states them.

    Each non-blank line is a code, whitespace and the name. Raises PilotError on any other line
    or a repeated code, so a name is never guessed.
    """
    legend: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _LEGEND_LINE.fullmatch(line.strip())
        if match is None:
            raise PilotError(f"the legend line {line!r} is not a class code and a name")
        legend.append({"code": int(match[1]), "name": match[2]})
    codes = [entry["code"] for entry in legend]
    if len(set(codes)) != len(codes):
        raise PilotError("the legend states a class code more than once")
    return legend


def worldcover_grid_tiles(data: Any) -> dict[str, tuple[float, float, float, float]]:
    """The tiles of a WorldCover tiling grid GeoJSON: ``ll_tile`` -> (west, south, east, north).

    Every feature must be a Polygon whose ring is an axis-aligned rectangle (the grid's 3 x 3
    degree squares) with a tile name such as ``N63W018``; anything else raises PilotError.
    """
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise PilotError("the WorldCover grid is not a GeoJSON FeatureCollection")
    tiles: dict[str, tuple[float, float, float, float]] = {}
    for feature in data["features"]:
        try:
            name = feature["properties"]["ll_tile"]
            geometry = feature["geometry"]
            (ring,) = geometry["coordinates"]
            points = [(float(x), float(y)) for x, y in ring]
        except (KeyError, TypeError, ValueError) as error:
            raise PilotError(f"a WorldCover grid feature cannot be read ({error!r})") from error
        if not isinstance(name, str) or _WORLDCOVER_TILE.fullmatch(name) is None:
            raise PilotError(f"the WorldCover grid names a tile {name!r}")
        xs, ys = {x for x, _ in points}, {y for _, y in points}
        corners = {(x, y) for x in xs for y in ys}
        if (
            geometry.get("type") != "Polygon"
            or len(xs) != 2
            or len(ys) != 2
            or points[0] != points[-1]
            or set(points) != corners
        ):
            raise PilotError(f"the grid tile {name} is not an axis-aligned rectangle")
        if name in tiles:
            raise PilotError(f"the WorldCover grid lists {name} more than once")
        tiles[name] = (min(xs), min(ys), max(xs), max(ys))
    return tiles


@dataclass(frozen=True)
class _WcArea:
    """The T1-06 window this source follows, as its committed record states it."""

    centre: tuple[float, float]
    """(latitude, longitude)"""
    bounds: tuple[float, float, float, float]
    """(west, south, east, north) in degrees of EPSG:4326"""
    cop30_tile: str
    cop30_product_id: str

    def entry(self, source: str) -> dict[str, Any]:
        west, south, east, north = self.bounds
        return {
            "source": source,
            "centre": {"latitude": self.centre[0], "longitude": self.centre[1]},
            "geographic_bounds_deg": {"west": west, "south": south, "east": east, "north": north},
            "cop30_tile": self.cop30_tile,
            "cop30_product_id": self.cop30_product_id,
        }


@dataclass(frozen=True)
class WorldcoverWindow:
    """The window read from one WorldCover map, inside the pixels of the T1-06 area."""

    col_off: int
    row_off: int
    width: int
    height: int
    area: tuple[int, int, int, int]
    """(col_off, row_off, width, height) of every pixel touching the area's bounds"""

    @property
    def window(self) -> Window:
        return Window(self.col_off, self.row_off, self.width, self.height)

    def pixel_window(self) -> dict[str, int]:
        return {
            "col_off": self.col_off,
            "row_off": self.row_off,
            "width": self.width,
            "height": self.height,
        }


def worldcover_window(
    header: GeoTiffHeader,
    centre: tuple[float, float],
    bounds: tuple[float, float, float, float],
    max_pixels: int,
) -> WorldcoverWindow:
    """The window of at most ``max_pixels`` per side around ``centre`` inside ``bounds``.

    ``centre`` is (latitude, longitude) and ``bounds`` (west, south, east, north), both in
    EPSG:4326. Raises PilotError if the map is not a north-up EPSG:4326 raster or the area
    leaves it.
    """
    if max_pixels < 1:
        raise PilotError(f"max_pixels must be at least 1, got {max_pixels}")
    if header.crs is None or pyproj.CRS.from_wkt(header.crs).to_epsg() != 4326:
        raise PilotError("the WorldCover map's CRS as read is not EPSG:4326")
    t = header.transform
    if t is None or t.b != 0 or t.d != 0 or t.a <= 0 or t.e >= 0:
        raise PilotError("the WorldCover map has no north-up geotransform")
    west, south, east, north = bounds
    col0, col1 = math.floor((west - t.c) / t.a), math.ceil((east - t.c) / t.a)
    row0, row1 = math.floor((t.f - north) / -t.e), math.ceil((t.f - south) / -t.e)
    if col0 < 0 or row0 < 0 or col1 > header.width or row1 > header.height:
        raise PilotError(
            f"the T1-06 area (pixel columns {col0} to {col1 - 1}, rows {row0} to {row1 - 1}) "
            f"leaves the {header.width} x {header.height} WorldCover map; the pilot reads one tile"
        )
    latitude, longitude = centre
    centre_col = math.floor((longitude - t.c) / t.a)
    centre_row = math.floor((t.f - latitude) / -t.e)
    width, height = min(max_pixels, col1 - col0), min(max_pixels, row1 - row0)
    col_off = min(max(centre_col - width // 2, col0), col1 - width)
    row_off = min(max(centre_row - height // 2, row0), row1 - height)
    return WorldcoverWindow(
        col_off=col_off,
        row_off=row_off,
        width=width,
        height=height,
        area=(col0, row0, col1 - col0, row1 - row0),
    )


@dataclass(frozen=True)
class WorldcoverSource:
    """ESA WorldCover, the pinned year (PRD 5 row 12; 8.7 step 4; 15 architecture review).

    fetch downloads the bucket readme and the pinned release's tiling grid, finds the tile
    holding the T1-06 window (read from the committed outputs/pilot/cop30_range_read.json) and
    reads a window of at most ``max_window_pixels`` per side of that tile's land cover map
    through HTTP range requests; only the byte ranges read are stored, with every request's
    HTTP metadata. The bucket is read anonymously over HTTPS. record reads only those files and
    the T1-06 record, and refuses a T1-06 window that differs from the one fetch used.
    """

    bucket: str = "esa-worldcover"
    region: str = "eu-central-1"
    endpoint: str | None = None
    version: str = "v200"
    year: int = 2021
    area_record: str = "outputs/pilot/cop30_range_read.json"
    max_window_pixels: int = WORLDCOVER_MAX_WINDOW_PIXELS
    range_block_bytes: int = RANGE_BLOCK_BYTES
    whole_file_limit_bytes: int = WHOLE_FILE_LIMIT_BYTES

    @property
    def name(self) -> str:
        return "wc"

    @property
    def raw_directory(self) -> str:
        return "worldcover"

    @property
    def base_url(self) -> str:
        """The bucket's S3 virtual-hosted-style HTTPS endpoint in its region (or a test stub)."""
        if self.endpoint is not None:
            return self.endpoint.rstrip("/")
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com"

    def map_key(self, tile: str) -> str:
        return (
            f"{self.version}/{self.year}/map/"
            f"ESA_WorldCover_10m_{self.year}_{self.version}_{tile}_Map.tif"
        )

    def map_url(self, tile: str) -> str:
        return f"{self.base_url}/{self.map_key(tile)}"

    def _documents(self) -> tuple[tuple[str, str, str], ...]:
        return (
            ("bucket_readme", f"{self.base_url}/readme.html", "readme.html"),
            (
                "tile_grid",
                f"{self.base_url}/{self.version}/{self.year}/esa_worldcover_grid.geojson",
                f"esa_worldcover_grid_{self.version}_{self.year}.geojson",
            ),
        )

    def _local(self, file_name: str) -> str:
        return f"{RAW_DIR.as_posix()}/{self.raw_directory}/{file_name}"

    def _refetch(self, what: str) -> PilotError:
        return PilotError(f"{what}; run: {COMMAND} fetch {self.name}")

    def _area(self, root: Path) -> _WcArea:
        path = root / self.area_record
        try:
            dem = json.loads(path.read_bytes())["dem"]
            window = dem["window"]
            bounds = window["geographic_bounds_deg"]
            return _WcArea(
                centre=(
                    float(window["centre"]["latitude"]),
                    float(window["centre"]["longitude"]),
                ),
                bounds=(
                    float(bounds["west"]),
                    float(bounds["south"]),
                    float(bounds["east"]),
                    float(bounds["north"]),
                ),
                cop30_tile=str(dem["tile"]),
                cop30_product_id=str(dem["product_id"]),
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise PilotError(
                f"{self.area_record} does not state the T1-06 window (dem.window.centre and "
                f"geographic_bounds_deg, dem.tile, dem.product_id): {error!r}; run: {COMMAND} "
                "record cop30 first"
            ) from error

    def _tile(self, grid_path: Path, area: _WcArea) -> tuple[str, tuple[float, ...], int]:
        tiles = worldcover_grid_tiles(_strict_json(grid_path))
        latitude, longitude = area.centre
        holding = [
            name
            for name, (west, south, east, north) in tiles.items()
            if west <= longitude < east and south <= latitude < north
        ]
        if len(holding) != 1:
            raise PilotError(
                f"no tile of the WorldCover grid holds the T1-06 centre ({latitude}, {longitude})"
                if not holding
                else f"several WorldCover grid tiles hold the T1-06 centre: {sorted(holding)}"
            )
        (tile,) = holding
        west, south, east, north = tiles[tile]
        a_west, a_south, a_east, a_north = area.bounds
        if not (west <= a_west and a_east <= east and south <= a_south and a_north <= north):
            raise PilotError(f"the T1-06 window leaves the WorldCover grid tile {tile}")
        return tile, tiles[tile], len(tiles)

    # -- fetch --

    def fetch(
        self, raw_dir: Path, download: Downloader, read_range: RangeFetcher, now: Clock
    ) -> None:
        root = raw_dir.parent.parent.parent
        area = self._area(root)
        raw_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=raw_dir, prefix=".fetch-") as staging_name:
            staging = Path(staging_name)
            documents: list[dict[str, Any]] = []
            for role, url, file_name in self._documents():
                magic = _GEOJSON_MAGIC if role == "tile_grid" else ()
                fetched = download(
                    url, staging / file_name, max_bytes=self.whole_file_limit_bytes, magic=magic
                )
                documents.append(_fetch_entry(fetched, now(), role=role))
            tile, _, _ = self._tile(staging / documents[1]["file_name"], area)
            provider_name = self.map_key(tile).rsplit("/", 1)[1]
            remote = read_remote_cog_window(
                self.map_url(tile),
                lambda header: (
                    worldcover_window(
                        header, area.centre, area.bounds, self.max_window_pixels
                    ).window
                ),
                fetch=read_range,
                block_bytes=self.range_block_bytes,
                max_bytes=self.whole_file_limit_bytes,
                now=now,
            )
            local_name = f"{provider_name}{_RANGES_SUFFIX}"
            (staging / local_name).write_bytes(remote.ranges.data)
            files = [
                _window_entry(
                    remote,
                    local_name,
                    now(),
                    role="landcover_window",
                    tile=tile,
                    s3_uri=f"s3://{self.bucket}/{self.map_key(tile)}",
                    provider_file_name=provider_name,
                    range_block_bytes=self.range_block_bytes,
                    exchanges=[_phased_exchange_entry(e) for e in remote.exchanges],
                )
            ]
            metadata = {
                "source": self.name,
                "command": f"{COMMAND} fetch {self.name}",
                "bucket": self.bucket,
                "region": self.region,
                "base_url": self.base_url,
                "version": self.version,
                "year": self.year,
                "area": area.entry(self.area_record),
                "max_window_pixels": self.max_window_pixels,
                "whole_file_limit_bytes": self.whole_file_limit_bytes,
                "documents": documents,
                "files": files,
            }
            (raw_dir / FETCH_METADATA).unlink(missing_ok=True)
            for entry in [*documents, *files]:
                os.replace(staging / entry["file_name"], raw_dir / entry["file_name"])
            _write_atomically(raw_dir / FETCH_METADATA, canonical_json(metadata))

    # -- build --

    def build(self, raw_dir: Path, root: Path) -> Build:
        fetched = _load_fetch(raw_dir, self.name)
        try:
            return self._build(raw_dir, root, fetched)
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, PilotError):
                raise
            raise self._refetch(
                f"data/raw/{self.raw_directory}/{FETCH_METADATA} lacks an expected entry or "
                f"value ({error!r})"
            ) from error

    def _build(self, raw_dir: Path, root: Path, fetched: Mapping[str, Any]) -> Build:
        documents = {entry["role"]: entry for entry in fetched["documents"]}
        for role, url, file_name in self._documents():
            if documents[role]["url"] != url or documents[role]["file_name"] != file_name:
                raise self._refetch(f"fetch.json's {role} is not {url}")
        if (fetched["version"], fetched["year"]) != (self.version, self.year) or fetched[
            "max_window_pixels"
        ] != self.max_window_pixels:
            raise self._refetch("fetch.json was fetched for another release or window size")
        area = self._area(root)
        if fetched["area"] != area.entry(self.area_record):
            raise self._refetch(
                f"the T1-06 window in {self.area_record} differs from the one the fetch used"
            )
        grid_entry = documents["tile_grid"]
        tile, tile_bounds, tiles_in_grid = self._tile(raw_dir / grid_entry["file_name"], area)

        (entry,) = fetched["files"]
        url = self.map_url(tile)
        provider_name = self.map_key(tile).rsplit("/", 1)[1]
        if (
            entry["role"] != "landcover_window"
            or entry["tile"] != tile
            or entry["url"] != url
            or not _is_window_read(entry)
            or _provider_name(entry) != provider_name
        ):
            raise self._refetch(f"fetch.json's map window is not a window read of {url}")
        path = raw_dir / entry["file_name"]
        ranges = ByteRanges(
            name=provider_name,
            size=int(entry["remote_bytes"]),
            ranges=tuple((int(first), int(last)) for first, last in entry["ranges"]),
            data=path.read_bytes(),
        )
        if ranges.covers_whole_file:
            raise self._refetch(
                f"the byte ranges in {path.name} hold every byte of {url}, so the map was "
                "downloaded whole, not read by window"
            )
        header = read_geotiff_header(path, ranges=ranges)
        chosen = worldcover_window(header, area.centre, area.bounds, self.max_window_pixels)
        if entry["window"] != chosen.pixel_window():
            raise self._refetch(
                f"fetch.json's window {entry['window']} is not the window {chosen.pixel_window()} "
                "that the saved header and the T1-06 area give"
            )
        exchanges = entry["exchanges"]
        received = sum(int(exchange["bytes_received"]) for exchange in exchanges)
        requested = 0
        for exchange in exchanges:
            match = _REQUESTED_RANGE.fullmatch(str(exchange["requested_range"]))
            if match is None:
                raise self._refetch("fetch.json's range request log cannot be read")
            requested += int(match[2]) - int(match[1]) + 1
        if (
            not exchanges
            or len(exchanges) != entry["range_requests"]
            or received != entry["bytes_received"]
            or received != len(ranges.data)
        ):
            raise self._refetch("fetch.json's range request log does not match the stored ranges")

        tags = header.tags
        legend_text = tags.get("legend")
        legend = [] if legend_text is None else parse_worldcover_legend(legend_text)
        names = {item["code"]: item["name"] for item in legend}
        licence, copyright_ = tags.get("license"), tags.get("copyright")
        attribution_parts = []
        if licence is not None:
            attribution_parts.append(f"licence as the map file states: '{licence}'")
        if copyright_ is not None:
            attribution_parts.append(f"attribution as the map file states: '{copyright_}'")
        attribution = "; ".join(attribution_parts) if attribution_parts else UNKNOWN
        time_start, time_end = tags.get("time_start"), tags.get("time_end")
        acquisition = (
            f"{time_start} to {time_end} (time_start and time_end as the map file states; the "
            "map's reference year, not a per-pixel observation time)"
            if time_start is not None and time_end is not None
            else UNKNOWN
        )
        product_version = tags.get("product_version")
        version = f"{self.version} {self.year}" + (
            f", product_version {product_version} as the map file states"
            if product_version is not None
            else ""
        )
        nodata = header.nodata[0] if header.nodata else None
        metadata: ProductMetadata = {
            "body": "earth",
            "provider": (
                f"ESA WorldCover; AWS Open Data bucket s3://{self.bucket} ({self.region})"
            ),
            "version": version,
            "source_url": url,
            "acquisition_time": acquisition,
            "frequency_or_wavelength": None,
            "transmit_receive_convention": None,
            "units": (
                "land cover class codes (dimensionless), named by the legend the map file states"
                if legend
                else UNKNOWN
            ),
            "calibration_type": None,
            "effective_resolution": UNKNOWN,
            "incidence_source": None,
            "qa_definition": (
                (
                    f"nodata {_json_number(nodata)} as the map file declares it"
                    if nodata is not None
                    else "the map file declares no nodata value"
                )
                + "; the InputQuality layer is not read, and a bare/sparse vegetation pixel does "
                "not establish dry bare mineral soil on the radar date (PRD 5 row 12)"
            ),
            "license_or_attribution": attribution,
        }
        product_id = f"{_WORLDCOVER_PREFIX}{self.bucket}/{self.map_key(tile)}"
        bands, product = read_product(
            product_id, [path], metadata, root=root, window=chosen.window, ranges=ranges
        )
        values = bands[0].array
        if nodata is not None:
            is_nodata = values == np.asarray(nodata).astype(values.dtype)
        else:
            is_nodata = np.zeros(values.shape, dtype=bool)
        codes, counts = np.unique(values[~is_nodata], return_counts=True)
        present = [
            {
                "code": int(code),
                "name": names.get(int(code)),
                "in_legend": int(code) in names,
                "pixels": int(count),
            }
            for code, count in zip(codes, counts, strict=True)
        ]
        layout = read_cog_layout(path, chosen.window, ranges=ranges)
        crs = pyproj.CRS.from_wkt(header.crs) if header.crs is not None else None
        t = header.transform
        assert t is not None
        col_off, row_off = chosen.col_off, chosen.row_off
        a_col, a_row, a_width, a_height = chosen.area
        w_west, w_north = t @ (col_off, row_off)
        w_east, w_south = t @ (col_off + chosen.width, row_off + chosen.height)
        retrieved = sorted(e["retrieved_at"] for e in [*fetched["documents"], *fetched["files"]])
        west, south, east, north = tile_bounds
        result = {
            "record": "worldcover_window",
            "description": (
                "One window of one ESA WorldCover land cover map read by HTTP range requests "
                "from the public AWS bucket, server-side, around the T1-06 Copernicus GLO-30 "
                "probe window (PRD 5 row 12; 8.7 step 4; 15 architecture review: WorldCover COG "
                "paths). Raw observations only: no eligibility mask is applied, the CORS "
                "evidence is outputs/pilot/cors_check.json, and the G0 status is a human "
                "checkpoint (PRD 23.6)."
            ),
            "source": self.name,
            "year_pin": {
                "version": self.version,
                "year": self.year,
                "reason": _WORLDCOVER_YEAR_REASON,
                "file_product_version": product_version,
                "file_algorithm_version": tags.get("algorithm_version"),
                "file_time_start": time_start,
                "file_time_end": time_end,
            },
            "area": area.entry(self.area_record),
            "tile_choice": {
                "rule": _WORLDCOVER_TILE_RULE,
                "grid": self._local(grid_entry["file_name"]),
                "grid_url": grid_entry["url"],
                "tiles_in_grid": tiles_in_grid,
                "tile": tile,
                "tile_bounds_deg": {"west": west, "south": south, "east": east, "north": north},
                "product_tile_tag": tags.get("product_tile"),
                "product_tile_matches": tags.get("product_tile") == tile,
                "url_rule": (
                    "https://<bucket>.s3.<region>.amazonaws.com/<version>/<year>/map/"
                    "ESA_WorldCover_10m_<year>_<version>_<tile>_Map.tif, the key layout the "
                    "bucket lists under <version>/<year>/map/"
                ),
            },
            "window": {
                "max_window_pixels": self.max_window_pixels,
                "pixel_window": chosen.pixel_window(),
                "area_pixel_window": {
                    "col_off": a_col,
                    "row_off": a_row,
                    "width": a_width,
                    "height": a_height,
                },
                "fraction_of_area_window": chosen.width * chosen.height / (a_width * a_height),
                "geographic_bounds_deg": {
                    "west": float(w_west),
                    "south": float(w_south),
                    "east": float(w_east),
                    "north": float(w_north),
                },
                "rule": _WORLDCOVER_WINDOW_RULE,
            },
            "range_read": {
                "url": url,
                "s3_uri": entry["s3_uri"],
                "final_url": entry["final_url"],
                "http_status": exchanges[0]["http_status"],
                "content_range": exchanges[0]["content_range"],
                "content_type": entry["content_type"],
                "etag": entry["etag"],
                "last_modified": entry["last_modified"],
                "object_size_bytes": entry["remote_bytes"],
                "range_requests": entry["range_requests"],
                "bytes_requested": requested,
                "bytes_received": received,
                "range_block_bytes": entry["range_block_bytes"],
                "ranges": entry["ranges"],
                "exchanges": exchanges,
                "whole_file_downloaded": ranges.covers_whole_file,
                "internal_tiles_touched": len(layout.blocks),
                "local_path": product.local_path,
                "local_sha256": product.checksum,
                "sha256_note": (
                    "the map was not downloaded whole, so its SHA-256 is unknown; local_sha256 is "
                    "that of the fetched byte ranges held in local_path, at the positions listed "
                    "in ranges"
                ),
                "retrieved_at": entry["retrieved_at"],
            },
            "header": _header_entry(header),
            "crs": {
                "wkt": header.crs,
                "name": None if crs is None else crs.name,
                "epsg_identified": None if crs is None else crs.to_epsg(),
                "stated_by": "the COG's GeoTIFF keys, as GDAL reads them",
            },
            "classes": {
                "method": (
                    "numpy.unique over the decoded window's pixels that are not the declared "
                    "nodata value; names only as the map file's legend tag states them, null "
                    "for a code the legend does not state"
                ),
                "legend_as_stated": legend_text,
                "legend": legend,
                "present": present,
                "nodata_value": None if nodata is None else int(nodata),
                "nodata_pixels": int(np.sum(is_nodata)),
                "total_pixels": int(values.size),
                "bare_ground_caveat": _BARE_GROUND_CAVEAT,
            },
            "licence": {
                "license_or_attribution": attribution,
                "file_license": licence,
                "file_copyright": copyright_,
            },
            "documents": [
                {
                    key: doc[key]
                    for key in (
                        "role",
                        "file_name",
                        "url",
                        "final_url",
                        "http_status",
                        "content_type",
                        "bytes_received",
                        "sha256",
                        "last_modified",
                        "retrieved_at",
                    )
                }
                | {"local_path": self._local(doc["file_name"])}
                for doc in fetched["documents"]
            ],
            "retrieval": {
                "first_retrieved_at": retrieved[0],
                "last_retrieved_at": retrieved[-1],
                "fetch_metadata": self._local(FETCH_METADATA),
                "command": fetched["command"],
            },
            "verification": {
                product.product_id: {
                    "status": product.verification_status,
                    "basis": (
                        f"a {chosen.width} x {chosen.height} pixel window read through "
                        f"{entry['range_requests']} HTTP range requests ({received} of the "
                        f"{entry['remote_bytes']} bytes; the map is not downloaded whole), "
                        "header and window decoded and class pixels counted; not plotted, so "
                        "the PRD 5 entry rule is not complete"
                    ),
                }
            },
            "open_g0_items": [
                "Whether the WorldCover COGs are CORS-readable from a browser (PRD 8.7 step 4) "
                "is recorded as header evidence in outputs/pilot/cors_check.json, not here",
                "No snow, water or bare-ground eligibility mask is derived (PRD 5 row 12, D31)",
                "The G0 status and the shipping mode are a human checkpoint (PRD 23.6)",
            ],
            "field_notes": {
                "acquisition_time": (
                    "the map file's time_start and time_end: the reference year of the map"
                ),
                "effective_resolution": "unknown: the file states none and none is derived",
                "units": "class codes; their names are the legend tag of the map file",
                "window": (
                    "a window of at most max_window_pixels per side inside the T1-06 area, so "
                    "it covers fraction_of_area_window of it, not all of it"
                ),
            },
            "tool_versions": tool_versions(),
        }
        return Build(
            files={"outputs/pilot/worldcover_window.json": canonical_json(result)},
            records=(product,),
            record_prefix=_WORLDCOVER_PREFIX,
        )


# --- cors: Origin-header requests to the GLO-30 and WorldCover buckets --------------------------

CORS_HEADERS_THAT_MATTER: Final = (
    "access-control-allow-origin",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-expose-headers",
    "access-control-max-age",
    "vary",
    "content-range",
    "accept-ranges",
    "content-length",
)
"""The response headers the CORS record reports for every request, lower case, in this order."""

CorsEvidence = Literal["direct_read", "proxy_needed", "inconclusive"]

_CORS_STATEMENT: Final = (
    "This is a server-side request that sends an Origin header (Python http.client through "
    "io.send_http_exchange), not a real browser fetch. The header evidence implies but does not "
    "replace a browser run: a browser applies its own CORS checks, caching and preflight rules, "
    "which this record does not observe."
)
_COP30_MODE_NOTE: Final = (
    "cop30_mode (direct or proxy) is chosen by the team at G0 (PRD 9, PRD 15 architecture "
    "review); this record is evidence for that choice and does not set it, and T1-07 writes it "
    "nowhere, config/run.yaml included"
)
_CORS_RULE: Final = (
    "Per URL, over every origin tested. direct_read: the preflight answers 2xx with "
    "Access-Control-Allow-Origin equal to the origin or *, Access-Control-Allow-Methods listing "
    "GET or *, and Access-Control-Allow-Headers listing range or *; and the Range GET answers "
    "206 with Access-Control-Allow-Origin equal to the origin or * and "
    "Access-Control-Expose-Headers listing Content-Range and Accept-Ranges or being *. "
    "proxy_needed: for every origin the Range GET's response has no single "
    "Access-Control-Allow-Origin equal to the origin or *, so a browser's CORS check fails and "
    "script cannot read the response (Fetch standard, CORS check). inconclusive: anything else. "
    "A missing header counts as absent, never as allowing. The top-level value is the GLO-30 "
    "URL's, which bears on cop30_mode; the WorldCover URL's value bears on the live WorldCover "
    "check (PRD 8.7 step 4)."
)
_CORS_ORIGIN_REASONS: Final = {
    "neutral": (
        "a neutral HTTPS origin reserved for documentation (example.org), so no project or "
        "personal name is sent"
    ),
    "localhost": (
        "a local development origin, as a browser on a developer's machine would send it"
    ),
}
_XML_ERROR_MESSAGE: Final = re.compile(r"<Error>.*?<Message>([^<]*)</Message>", re.DOTALL)
_CORS_TARGETS: Final = (
    ("glo30", "outputs/pilot/cop30_range_read.json", ("dem", "range_read", "url")),
    ("worldcover", "outputs/pilot/worldcover_window.json", ("range_read", "url")),
)


def _tokens(values: Sequence[str], *, lower: bool) -> list[str]:
    tokens = [token.strip() for value in values for token in value.split(",")]
    return [token.lower() if lower else token for token in tokens if token]


def _headers_that_matter(headers: Sequence[Sequence[str]]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for name in CORS_HEADERS_THAT_MATTER:
        values = [str(value) for key, value in headers if str(key).lower() == name]
        found[name] = {"state": "present" if values else "absent", "values": values}
    return found


def _allows_origin(matter: Mapping[str, Mapping[str, Any]], origin: str) -> bool:
    values = matter["access-control-allow-origin"]["values"]
    return len(values) == 1 and values[0].strip() in ("*", origin)


def cors_preflight_observations(
    status: int, matter: Mapping[str, Mapping[str, Any]], origin: str
) -> dict[str, bool]:
    """What a preflight response allows for a GET with a range header from ``origin``."""
    methods = _tokens(matter["access-control-allow-methods"]["values"], lower=False)
    headers = _tokens(matter["access-control-allow-headers"]["values"], lower=True)
    return {
        "ok_status": 200 <= status <= 299,
        "allows_origin": _allows_origin(matter, origin),
        "allows_get": "GET" in methods or "*" in methods,
        "allows_range_header": "range" in headers or "*" in headers,
    }


def cors_range_get_observations(
    status: int, matter: Mapping[str, Mapping[str, Any]], origin: str
) -> dict[str, bool]:
    """What a Range GET response with ``origin`` shows: 206, the origin check, exposed headers."""
    exposed = _tokens(matter["access-control-expose-headers"]["values"], lower=True)
    return {
        "returned_206": status == 206,
        "allows_origin": _allows_origin(matter, origin),
        "expose_headers_lists_content_range": "content-range" in exposed,
        "expose_headers_lists_accept_ranges": "accept-ranges" in exposed,
        "expose_headers_wildcard": "*" in exposed,
    }


def cors_evidence(origins: Sequence[Mapping[str, Any]]) -> tuple[CorsEvidence, list[str]]:
    """The evidence value for one URL and its reasons, from each origin's observations.

    ``origins`` holds, per origin, ``origin``, ``preflight`` and ``range_get`` entries with
    ``status`` and ``observations``.
    """
    reasons: list[str] = []
    direct = True
    blocked = 0
    for item in origins:
        origin = item["origin"]
        pre = item["preflight"]["observations"]
        get = item["range_get"]["observations"]
        pre_status, get_status = item["preflight"]["status"], item["range_get"]["status"]
        if not pre["ok_status"]:
            reasons.append(f"{origin}: the preflight answered HTTP {pre_status}, not 2xx")
        for key, text in (
            ("allows_origin", "no Access-Control-Allow-Origin equal to the origin or *"),
            ("allows_get", "no Access-Control-Allow-Methods listing GET or *"),
            ("allows_range_header", "no Access-Control-Allow-Headers listing range or *"),
        ):
            if not pre[key]:
                reasons.append(f"{origin}: the preflight response has {text}")
        if not get["returned_206"]:
            reasons.append(f"{origin}: the Range GET answered HTTP {get_status}, not 206")
        if not get["allows_origin"]:
            blocked += 1
            reasons.append(
                f"{origin}: the Range GET response has no Access-Control-Allow-Origin equal to "
                "the origin or *, so a browser's CORS check would fail"
            )
        wildcard = get["expose_headers_wildcard"]
        for key, header in (
            ("expose_headers_lists_content_range", "Content-Range"),
            ("expose_headers_lists_accept_ranges", "Accept-Ranges"),
        ):
            if not (get[key] or wildcard):
                reasons.append(
                    f"{origin}: Access-Control-Expose-Headers on the Range GET does not list "
                    f"{header} and is not *"
                )
        direct = direct and all(pre.values()) and get["returned_206"] and get["allows_origin"]
        direct = direct and (
            wildcard
            or (
                get["expose_headers_lists_content_range"]
                and get["expose_headers_lists_accept_ranges"]
            )
        )
    if origins and blocked == len(origins):
        return "proxy_needed", reasons
    if origins and direct:
        return "direct_read", [
            "for every origin tested, the preflight and the Range GET carry every header the "
            "rule requires"
        ]
    return "inconclusive", reasons or ["no origin was tested"]


@dataclass(frozen=True)
class CorsSource:
    """The Origin-header check of the GLO-30 and WorldCover buckets (PRD 8.7 step 2, 9 G0, 15).

    fetch reads the tile URLs the T1-06 and wc records committed, then sends to each, for each
    stated origin, an OPTIONS preflight (Origin, Access-Control-Request-Method: GET,
    Access-Control-Request-Headers: range) and a GET with ``Range: bytes=0-1023`` and Origin.
    It saves every status line, response header list, body and request time under
    data/raw/cors/. record reads only those files and the two records. Nothing here chooses
    cop30_mode.
    """

    origins: tuple[tuple[str, str], ...] = (
        ("neutral", "https://example.org"),
        ("localhost", "http://localhost:3000"),
    )
    range_header: str = "bytes=0-1023"
    max_body_bytes: int = HTTP_EXCHANGE_BODY_LIMIT_BYTES

    @property
    def name(self) -> str:
        return "cors"

    @property
    def origin_reasons(self) -> dict[str, str]:
        return dict(_CORS_ORIGIN_REASONS)

    def _requests(self, origin: str) -> tuple[tuple[str, str, dict[str, str]], ...]:
        return (
            (
                "preflight",
                "OPTIONS",
                {
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "range",
                },
            ),
            ("range_get", "GET", {"Origin": origin, "Range": self.range_header}),
        )

    def _urls(self, root: Path) -> list[tuple[str, str, str]]:
        urls = []
        for key, record, path in _CORS_TARGETS:
            try:
                value: Any = json.loads((root / record).read_bytes())
                for part in path:
                    value = value[part]
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise PilotError(
                    f"{record} does not state the {key} tile URL at {'.'.join(path)} "
                    f"({error!r}); record that source first"
                ) from error
            if not isinstance(value, str):
                raise PilotError(f"{record} states no {key} tile URL at {'.'.join(path)}")
            urls.append((key, value, f"{record}: {'.'.join(path)}"))
        return urls

    # -- fetch --

    def fetch(
        self, raw_dir: Path, download: Downloader, read_range: RangeFetcher, now: Clock
    ) -> None:
        urls = self._urls(raw_dir.parent.parent.parent)
        raw_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=raw_dir, prefix=".fetch-") as staging_name:
            staging = Path(staging_name)
            files: list[dict[str, Any]] = []
            for key, url, _ in urls:
                for origin_key, origin in self.origins:
                    for kind, method, headers in self._requests(origin):
                        requested_at = now()
                        exchange = send_http_exchange(
                            url, method, headers, max_body_bytes=self.max_body_bytes
                        )
                        retrieved_at = now()
                        file_name = f"{key}_{origin_key}_{kind}.body"
                        (staging / file_name).write_bytes(exchange.body)
                        files.append(
                            {
                                "url_key": key,
                                "url": url,
                                "origin_key": origin_key,
                                "origin": origin,
                                "kind": kind,
                                "method": method,
                                "request_headers": [list(h) for h in exchange.request_headers],
                                "status_line": exchange.status_line,
                                "http_version": exchange.http_version,
                                "status": exchange.status,
                                "reason": exchange.reason,
                                "response_headers": [list(h) for h in exchange.headers],
                                "file_name": file_name,
                                "bytes_received": len(exchange.body),
                                "body_truncated": exchange.body_truncated,
                                "sha256": hashlib.sha256(exchange.body).hexdigest(),
                                "requested_at": utc_timestamp(requested_at),
                                "retrieved_at": utc_timestamp(retrieved_at),
                            }
                        )
            metadata = {
                "source": self.name,
                "command": f"{COMMAND} fetch {self.name}",
                "urls": [{"key": key, "url": url, "from": where} for key, url, where in urls],
                "origins": [{"key": key, "origin": origin} for key, origin in self.origins],
                "max_body_bytes": self.max_body_bytes,
                "documents": [],
                "files": files,
            }
            (raw_dir / FETCH_METADATA).unlink(missing_ok=True)
            for entry in files:
                os.replace(staging / entry["file_name"], raw_dir / entry["file_name"])
            _write_atomically(raw_dir / FETCH_METADATA, canonical_json(metadata))

    # -- build --

    def build(self, raw_dir: Path, root: Path) -> Build:
        fetched = _load_fetch(raw_dir, self.name)
        try:
            return self._build(raw_dir, root, fetched)
        except (KeyError, TypeError) as error:
            raise PilotError(
                f"data/raw/{self.name}/{FETCH_METADATA} lacks an expected entry ({error!r}); "
                f"run: {COMMAND} fetch {self.name}"
            ) from error

    @staticmethod
    def _error_message(body: bytes) -> str | None:
        """The ``<Message>`` of an XML error body, such as S3 sends; None for any other body."""
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return None
        match = _XML_ERROR_MESSAGE.search(text)
        return None if match is None else unescape(match[1])

    def _build(self, raw_dir: Path, root: Path, fetched: Mapping[str, Any]) -> Build:
        refetch = f"; run: {COMMAND} fetch {self.name}"
        urls = self._urls(root)
        stated = [(item["key"], item["url"], item["from"]) for item in fetched["urls"]]
        for (key, url, where), before in itertools.zip_longest(urls, stated):
            if before is None or (key, url, where) != tuple(before):
                raise PilotError(
                    f"the {key} URL now stated by {where.split(':')[0]} differs from the URL the "
                    f"fetch used{refetch}"
                )
        if fetched["origins"] != [{"key": k, "origin": o} for k, o in self.origins]:
            raise PilotError(f"fetch.json was fetched with other origins{refetch}")
        entries = iter(fetched["files"])
        results: list[dict[str, Any]] = []
        for key, url, where in urls:
            per_origin: list[dict[str, Any]] = []
            for origin_key, origin in self.origins:
                item: dict[str, Any] = {"origin_key": origin_key, "origin": origin}
                for kind, method, headers in self._requests(origin):
                    entry = next(entries, None)
                    if entry is None or (
                        entry["url_key"],
                        entry["url"],
                        entry["origin"],
                        entry["kind"],
                        entry["method"],
                    ) != (key, url, origin, kind, method):
                        raise PilotError(
                            f"fetch.json does not list the {kind} of {url} from {origin} in "
                            f"order{refetch}"
                        )
                    sent = dict(tuple(pair) for pair in entry["request_headers"])
                    if any(sent.get(name) != value for name, value in headers.items()):
                        raise PilotError(
                            f"fetch.json's {kind} of {url} from {origin} did not send the "
                            f"stated headers{refetch}"
                        )
                    matter = _headers_that_matter(entry["response_headers"])
                    status = int(entry["status"])
                    observe = (
                        cors_preflight_observations
                        if kind == "preflight"
                        else cors_range_get_observations
                    )
                    item[kind] = {
                        "method": method,
                        "request_headers": entry["request_headers"],
                        "status_line": entry["status_line"],
                        "status": status,
                        "headers_that_matter": matter,
                        "response_headers": entry["response_headers"],
                        "observations": observe(status, matter, origin),
                        "body": {
                            "local_path": f"{RAW_DIR.as_posix()}/{self.name}/{entry['file_name']}",
                            "bytes": entry["bytes_received"],
                            "sha256": entry["sha256"],
                            "truncated": entry["body_truncated"],
                            "error_message": self._error_message(
                                (raw_dir / entry["file_name"]).read_bytes()
                            ),
                        },
                        "requested_at": entry["requested_at"],
                        "retrieved_at": entry["retrieved_at"],
                    }
                per_origin.append(item)
            value, reasons = cors_evidence(per_origin)
            results.append(
                {
                    "key": key,
                    "url": url,
                    "url_from": where,
                    "cors_evidence_supports": value,
                    "reasons": reasons,
                    "origins": per_origin,
                }
            )
        if next(entries, None) is not None:
            raise PilotError(f"fetch.json lists more requests than the check sends{refetch}")
        glo30 = results[0]
        times = sorted(entry["retrieved_at"] for entry in fetched["files"])
        result = {
            "record": "cors_check",
            "description": (
                "Origin-header requests to one Copernicus GLO-30 tile and one ESA WorldCover map "
                "(the tiles the T1-06 and wc records read), with the response headers that "
                "decide whether a browser may read them cross-origin (PRD 8.7 step 2 and 4; 9 "
                "G0; 15 architecture review). Raw observations and a derived evidence value "
                "only: the G0 status and cop30_mode are human decisions (PRD 23.6)."
            ),
            "source": self.name,
            "method": {
                "statement": _CORS_STATEMENT,
                "client": (
                    "io.send_http_exchange: Host, Accept-Encoding: identity and the request "
                    "headers listed per request; no User-Agent, cookie, credential or project "
                    "identifier; redirects are not followed"
                ),
                "origins": [
                    {"key": key, "origin": origin, "why": _CORS_ORIGIN_REASONS[key]}
                    for key, origin in self.origins
                ],
                "requests": {
                    "preflight": (
                        "OPTIONS with Origin, Access-Control-Request-Method: GET and "
                        "Access-Control-Request-Headers: range"
                    ),
                    "range_get": f"GET with Origin and Range: {self.range_header}",
                },
                "headers_that_matter": list(CORS_HEADERS_THAT_MATTER),
                "absent_header": (
                    "a header the response does not carry is recorded with state absent and no "
                    "values, and never counts as allowing"
                ),
                "error_message": (
                    "body.error_message quotes the <Message> of an XML <Error> body as the "
                    "server sent it, else null; the body itself is at body.local_path"
                ),
            },
            "cors_evidence_supports": glo30["cors_evidence_supports"],
            "cors_evidence_reasons": glo30["reasons"],
            "cors_evidence_scope": (
                "the GLO-30 tile URL, which bears on cop30_mode; each URL's own value is under "
                "urls[].cors_evidence_supports"
            ),
            "cop30_mode_note": _COP30_MODE_NOTE,
            "derivation_rule": _CORS_RULE,
            "browser_run": (
                "not performed by this record; G0 asks for a Copernicus COG read from a browser "
                "origin with the CORS result recorded (PRD 9), which this header evidence "
                "implies but does not replace"
            ),
            "urls": results,
            "retrieval": {
                "first_retrieved_at": times[0] if times else None,
                "last_retrieved_at": times[-1] if times else None,
                "fetch_metadata": f"{RAW_DIR.as_posix()}/{self.name}/{FETCH_METADATA}",
                "command": fetched["command"],
            },
            "tool_versions": tool_versions(),
        }
        return Build(
            files={"outputs/pilot/cors_check.json": canonical_json(result)},
            records=(),
            record_prefix="CORS/",
        )


SOURCES: Mapping[str, Source] = {
    "pgda78": Pgda78Source(),
    "minirf": MinirfSource(),
    "ctx": CtxSource(),
    "cop30": Cop30Source(),
    "wc": WorldcoverSource(),
    "cors": CorsSource(),
}
"""The registered pilot sources, by the name used on the command line."""


if __name__ == "__main__":
    raise SystemExit(main())
