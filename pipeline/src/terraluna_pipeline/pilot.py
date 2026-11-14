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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from typing import Any, Final, Protocol

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
    RANGE_BLOCK_BYTES,
    WHOLE_FILE_LIMIT_BYTES,
    ByteRanges,
    DownloadError,
    FetchedFile,
    FileTooLargeError,
    Pds3ImageLayout,
    Pds3Item,
    Pds3Object,
    Pds3PolarStereographic,
    Pds3Quantity,
    ProductMetadata,
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
    parse_pds3_label,
    pds3_image_layout,
    pds3_polar_stereographic,
    pds3_row_byte_range,
    read_geotiff_tags,
    read_product,
    read_remote_geotiff_window,
    sha256_file,
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


def _raw_dir(root: Path, name: str) -> Path:
    return root / RAW_DIR / name


def _require_raw(root: Path, name: str) -> Path:
    raw_dir = _raw_dir(root, name)
    if not (raw_dir / FETCH_METADATA).is_file():
        raise MissingRawDataError(
            f"data/raw/{name}/ holds no complete fetch; run: {COMMAND} fetch {name} first"
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
    """Download ``name``'s files into data/raw/<name>/; the only command using the network."""
    source = _source(name, sources)
    raw_dir = _raw_dir(Path.cwd() if root is None else root, name)
    source.fetch(
        raw_dir,
        fetch_file if download is None else download,
        fetch_range if read_range is None else read_range,
        now or utc_now,
    )


def expected_outputs(
    name: str, *, root: Path | None = None, sources: Mapping[str, Source] | None = None
) -> dict[str, bytes]:
    """The committed outputs recomputed from data/raw/<name>/, without writing anything."""
    base = Path.cwd() if root is None else root
    source = _source(name, sources)
    build = source.build(_require_raw(base, name), base)
    files = dict(build.files)
    sources_path = base / SOURCES_PATH
    existing = sources_path.read_bytes() if sources_path.is_file() else None
    files[SOURCES_PATH] = upsert_sources(existing, build.records, build.record_prefix)
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
            print(f"{name}: fetched into data/raw/{name}/")
        elif command == "record":
            changed = record(name, root=root, sources=registry)
            base = Path.cwd() if root is None else root
            for path in changed:
                print(f"wrote {path.relative_to(base).as_posix()}")
            print(f"{name}: outputs recorded from data/raw/{name}/ ({len(changed)} changed)")
        else:
            differences = check(name, root=root, sources=registry)
            if differences:
                for difference in differences:
                    print(difference, file=sys.stderr)
                print(
                    f"{name}: committed outputs differ from data/raw/{name}/; "
                    f"run: {COMMAND} record {name}",
                    file=sys.stderr,
                )
                return 1
            print(f"{name}: committed outputs match data/raw/{name}/")
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
_TRANSMIT_SENTENCE: Final = re.compile(r"The Mini-RF instrument transmits[^.]*\.")
_S_BAND_SENTENCE: Final = re.compile(r"Mini-RF operated in S-band \([^)]*\)[^.]*\.")
_PA_SENTENCE: Final = re.compile(r"[^.]*\bMoon Princip\w* Axis \(PA\) coordinate system[^.]*\.")
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
        col_off, row_off, width, height = self.window
        return {
            "site_extent": self.site_extent,
            "pixel_window": {
                "col_off": col_off,
                "row_off": row_off,
                "width": width,
                "height": height,
            },
        }


@dataclass(frozen=True)
class MinirfSource:
    """The Mini-RF south-polar CPR mosaic, LRO-L-MRFLRO-5-CDR-MOSAIC-V1.0 (PRD 5 row 2).

    ``fetch`` locates volume ``volume`` through the PDS Geosciences Node landing page and its
    directory pages, and downloads the data set's catalog files, the volume errata and every
    detached label in DATA/SAR/MOSAICS. Then it reads only the image rows that hold the Site01
    window of ``cpr_product`` (the file PRD 5 names as a lead), with one HTTP range request. The
    window is the smallest set of mosaic pixels that covers the Site01 extent T1-03 recorded in
    ``site_record``, converted with pyproj into the projection the label states. No mosaic image
    is downloaded whole. If the server ignores Range, fetch records that as the blocking
    limitation and fails; if the Site01 extent is missing, no rows are read and the record is
    PARTIAL with that reason.

    ``build`` checks every PRD 5 layout lead against the label, decodes the window with the
    label's layout and masks it with quality.validity_mask. It records raw CPR statistics only,
    and establishes from the labels which Stokes layers exist. The mosaic has no S4 layer, so
    the Stokes observation is recorded as not performed; an S4 label stops the record instead
    of being ignored. No S4-sign verdict and no Stokes validity rule are computed here (T2).
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
            image_name, image_url = self._image(layout, listed, mosaics_url)
            site = self._site_window(root, projection)
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
        self, layout: Pds3ImageLayout, listed: Mapping[str, str], mosaics_url: str
    ) -> tuple[str, str]:
        """The listed image file the label's ^IMAGE names (the server's names are lower case)."""
        found = [name for name in listed if name.lower() == layout.file_name.lower()]
        if len(found) != 1:
            raise PilotError(
                f"{mosaics_url} lists {len(found)} files named {layout.file_name} (the image "
                f"{self.cpr_label_name} points to), expected one"
            )
        return found[0], listed[found[0]]

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
        rows_entry = next((e for e in fetched["files"] if e.get("read") == "rows"), None)
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
        image_block = label.child("IMAGE")
        rules = [
            item.key for item in image_block.items if any(w in item.key for w in _VALUE_RULE_WORDS)
        ]
        if rules:
            raise PilotError(
                f"{self.cpr_label_name} declares {', '.join(rules)}; the pilot applies no value "
                "rule beyond the special pixels, so it cannot record this product faithfully"
            )
        if layout.scaling_factor != 1.0 or layout.value_offset != 0.0:
            raise PilotError(
                f"{self.cpr_label_name} states SCALING_FACTOR {layout.scaling_factor} and OFFSET "
                f"{layout.value_offset}; the pilot records stored values only"
            )
        special = self._special_values(layout)
        layers = [self._layer(raw_dir, entry, site) for _, entry in sorted(label_entries.items())]
        stokes = self._stokes_observation(layers, str(fetched["mosaics_url"]), site)
        convention = self._convention(label, texts["dsmap"], documents["dsmap"])
        description = _collapse(_label_text(label, "DESCRIPTION") or "")
        combined = _COMBINED_LOOK.search(description)
        cpr_definition = _CPR_DEFINITION.search(description)
        unit_item = image_block.get("UNIT")
        if unit_item is not None:
            units = unit_item.text() if isinstance(unit_item.value, str) else unit_item.raw
            units_source = f"the label's IMAGE object: UNIT = {unit_item.raw}"
        elif cpr_definition is not None:
            units = (
                f"dimensionless ratio: the label defines {cpr_definition.group(0)} and states "
                "no unit"
            )
            units_source = "the label's DESCRIPTION; the IMAGE object has no UNIT keyword"
        else:
            units, units_source = UNKNOWN, "the label states neither a unit nor a CPR definition"
        statistics = (
            self._cpr_statistics(raw_dir, rows_entry, layout, site)
            if rows_entry is not None and not isinstance(site, str)
            else {"status": "not_computed", "reason": window_meta["reason"]}
        )
        leads = self._leads(label, layout, projection, image["file_name"], fetched, combined)
        frequency = label.get("CENTER_FREQUENCY")
        s_band = _S_BAND_SENTENCE.search(_collapse(texts["instrument_catalog"]))
        transmit = _TRANSMIT_SENTENCE.search(_collapse(texts["errata"]))
        pa = _PA_SENTENCE.search(_collapse(texts["data_set_catalog"]))
        handedness_items = [
            item for item in _all_items(label) if any(w in item.key for w in _HANDEDNESS_WORDS)
        ]
        incidence_items = [item.key for item in _all_items(label) if "INCIDENCE" in item.key]
        look = label.get("LOOK_DIRECTION")
        citation = _find_text(catalog, "CITATION_DESC")
        licence = (
            "licen" in texts["data_set_catalog"].lower()
            or "licen" in _decode(raw_dir / self.cpr_label_name).lower()
        )
        attribution = (
            UNKNOWN
            if citation is None
            else f"Cite the data set as its catalog asks (CITATION_DESC): '{citation}'"
            + ("" if licence else "; neither the label nor the data set catalog states a licence")
        )
        incidence = (
            "per-pixel incidence is not recoverable from the mosaic: the label states no "
            "incidence angle, and the mosaic combines several acquisitions "
            f"({label.require('START_TIME').raw} to {label.require('STOP_TIME').raw})"
            + (" with East- and West-looking passes" if combined else "")
            + ", so each pixel's viewing geometry is not known"
        )
        frequency_text = (
            UNKNOWN
            if frequency is None
            else f"CENTER_FREQUENCY = {frequency.raw}, as the label states it"
            + ("" if s_band is None else f"; the instrument catalog states '{s_band.group(0)}'")
        )
        map_block = label.child("IMAGE_MAP_PROJECTION")
        common: dict[str, Any] = {
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
            "frequency_or_wavelength": frequency_text,
            "transmit_receive_convention": (
                "; ".join(f"{item.key} = {item.raw}" for item in handedness_items)
                if handedness_items
                else UNKNOWN
            ),
            "units": units,
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
                + ("" if pa is None else f"; the data set catalog states '{pa.group(0).strip()}'")
            ),
            "incidence_source": incidence,
            "qa_definition": (
                "ISIS special pixels as the label's IMAGE object declares them ("
                + ", ".join(f"{key} = {raw}" for key, raw in layout.special_raw.items())
                + "), masked by quality.validity_mask; the label defines no other invalid value"
            ),
            "license_or_attribution": attribution,
            "verification_status": "PARTIAL",
        }
        label_local = f"data/raw/{self.name}/{self.cpr_label_name}"
        records = [
            ProductRecord.model_validate(
                {
                    **common,
                    "product_id": f"{self.record_prefix}{self.cpr_label_name}",
                    "source_url": label_entry["url"],
                    "local_path": label_local,
                    "checksum": sha256_file(root / label_local),
                }
            )
        ]
        verification = {
            records[0].product_id: {
                "status": "PARTIAL",
                "basis": (
                    "downloaded and read as a PDS3 label; every PRD 5 layout lead is checked "
                    "against it (layout_leads)"
                ),
            }
        }
        range_read: dict[str, Any]
        if rows_entry is not None:
            rows_local = f"data/raw/{self.name}/{rows_entry['file_name']}"
            image_record = ProductRecord.model_validate(
                {
                    **common,
                    "product_id": f"{self.record_prefix}{image['file_name']}",
                    "source_url": image["url"],
                    "local_path": rows_local,
                    "checksum": sha256_file(root / rows_local),
                }
            )
            records.append(image_record)
            verification[image_record.product_id] = {
                "status": "PARTIAL",
                "basis": (
                    "the Site01 window rows read by one HTTP range request (206), decoded with "
                    "the label's layout and masked with quality.validity_mask; not plotted, so "
                    "the PRD 5 entry rule is not complete"
                ),
            }
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
                "local_sha256": image_record.checksum,
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
        open_items = [
            {
                "item": (
                    "Read one LRO-L-MRFLRO-5-CDR-MAP-V1.0 map-projected single-swath product over "
                    "Site01 and observe its S1 and S4 layers (PRD 5 row 1)"
                ),
                "reason": (
                    "the polar mosaic has no S4 layer (stokes_observation); reading a single "
                    "swath is outside T1-04"
                ),
            }
        ]
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
                "sha256": records[0].checksum,
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
            "projection": self._projection(projection, map_block, convention),
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
                "single_look_mosaics_listed": [
                    layer["file_name"] for layer in layers if layer["look_direction"] is not None
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
            "mosaic_layers": layers,
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

    def _special_values(self, layout: Pds3ImageLayout) -> list[dict[str, Any]]:
        """CORE_NULL first, then the saturation codes, each with the mask reason it gets."""
        if "CORE_NULL" not in layout.special_values:
            raise PilotError(f"{self.cpr_label_name} declares no CORE_NULL")
        result = []
        for key, value in layout.special_values.items():
            if key == "CORE_NULL":
                reason = InvalidReason.NODATA.label
            elif value in ISIS_SATURATION_REAL4:
                reason = InvalidReason.ISIS_SATURATION.label
            else:
                raise PilotError(
                    f"{self.cpr_label_name} declares {key} = {layout.special_raw[key]}, which is "
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

    def _layer(
        self, raw_dir: Path, entry: Mapping[str, Any], site: _SiteWindow | str
    ) -> dict[str, Any]:
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
        if not isinstance(site, _SiteWindow):
            covers = None
        else:
            try:
                extent = site.site_extent["extent_m"]
                projection.pixel_window(
                    *_converted_bounds(extent, site.site_extent["crs_wkt"], projection.crs)
                )
                covers = True
            except (ValueError, pyproj.exceptions.ProjError):
                covers = False
        look = label.get("LOOK_DIRECTION")
        return {
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

    def _stokes_observation(
        self, layers: Sequence[Mapping[str, Any]], mosaics_url: str, site: _SiteWindow | str
    ) -> dict[str, Any]:
        s4 = [layer["file_name"] for layer in layers if layer["quantity"] == "S4"]
        if s4:
            raise PilotError(
                f"the mosaic labels list S4 layers ({', '.join(s4)}); this source records no "
                "Stokes observation for them, so the record would be incomplete. Extend "
                "MinirfSource to range-read S1 and S4 (T1-04 AC5) instead of ignoring them"
            )
        quantities = sorted({str(layer["quantity"]) for layer in layers})
        stokes = [layer for layer in layers if layer["quantity"] in _STOKES_QUANTITIES]
        if isinstance(site, str):
            coverage = "their coverage of Site01 is not established, because the extent is missing"
        else:
            covering = [str(layer["file_name"]) for layer in stokes if layer["covers_site01"]]
            coverage = f"{len(covering)} S1 layers cover Site01 ({', '.join(covering)})"
        return {
            "status": "not_performed",
            "reason": (
                f"the {len(layers)} {self.data_set_id} labels listed at {mosaics_url} describe "
                f"{' and '.join(quantities)} layers only: {coverage}, but no label describes an S4 "
                "(fourth Stokes parameter) layer, so the S4 sign cannot be observed from this "
                "mosaic. CPR is never used to infer S4. The S4-sign verdict is computed by "
                "polarimetry.lunar_s4_sign_check (T2-05) at integration, from a product with S4"
            ),
        }

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

    def _cpr_statistics(
        self,
        raw_dir: Path,
        rows_entry: Mapping[str, Any],
        layout: Pds3ImageLayout,
        site: _SiteWindow,
    ) -> dict[str, Any]:
        col_off, row_off, width, height = site.window
        rows = rows_entry["rows"]
        expected = pds3_row_byte_range(layout, row_off, row_off + height - 1)
        if (rows["first"], rows["last"]) != (row_off, row_off + height - 1) or (
            rows_entry["first_byte"],
            rows_entry["last_byte"],
        ) != expected:
            raise PilotError(
                f"the rows in data/raw/{self.name}/{rows_entry['file_name']} are not the window's "
                f"rows; run: {COMMAND} fetch {self.name}"
            )
        data = (raw_dir / str(rows_entry["file_name"])).read_bytes()
        window = decode_pds3_rows(data, layout, height)[:, col_off : col_off + width]
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


SOURCES: Mapping[str, Source] = {"pgda78": Pgda78Source(), "minirf": MinirfSource()}
"""The registered pilot sources, by the name used on the command line."""


if __name__ == "__main__":
    raise SystemExit(main())
