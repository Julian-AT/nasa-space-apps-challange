"""The data pilot runner (PRD 5 pilot procedure, 6.1, 8.12, 9 G0): fetch, record and check.

``python -m terraluna_pipeline.pilot {fetch|record|check} <source>`` runs from the repository
root. Network and computation are separate:

- ``fetch`` is the only command that uses the network. It writes only to ``data/raw/<source>/``
  (git-ignored): the provider's files and pages, plus ``fetch.json`` with the retrieval time,
  URLs, HTTP status and byte counts. Files are staged in a hidden directory there and moved into
  place only when every download succeeded, so a failed fetch leaves an earlier one intact.
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
import struct
import sys
import tarfile
import tempfile
import urllib.parse
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from typing import Any, Final, Protocol

import numpy as np
import numpy.typing as npt
import rasterio  # type: ignore[import-untyped]
import shapefile  # type: ignore[import-untyped]
from affine import Affine
from PIL import Image, ImageDraw
from pydantic import TypeAdapter
from rasterio.crs import CRS  # type: ignore[import-untyped]

from terraluna_pipeline.export import canonical_json
from terraluna_pipeline.io import (
    WHOLE_FILE_LIMIT_BYTES,
    DownloadError,
    FetchedFile,
    ProductMetadata,
    RasterWindow,
    fetch_file,
    read_product,
    sha256_file,
)
from terraluna_pipeline.quality import InvalidReason, ValidityMask, valid_fraction, validity_mask
from terraluna_pipeline.schema import UNKNOWN, ProductRecord, SourcesFile, has_url_credentials

RAW_DIR: Final = Path("data/raw")
SOURCES_PATH: Final = "outputs/sources.json"
FETCH_METADATA: Final = "fetch.json"
COMMAND: Final = "python -m terraluna_pipeline.pilot"


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
    """One pilot source: a network fetch into its raw directory and an offline build."""

    @property
    def name(self) -> str: ...

    def fetch(self, raw_dir: Path, download: Downloader, now: Clock) -> None: ...

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
    now: Clock | None = None,
) -> None:
    """Download ``name``'s files into data/raw/<name>/; the only command using the network."""
    source = _source(name, sources)
    raw_dir = _raw_dir(Path.cwd() if root is None else root, name)
    source.fetch(raw_dir, fetch_file if download is None else download, now or utc_now)


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
            fetch(name, root=root, sources=registry, download=download, now=now)
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
    except (PilotError, DownloadError, OSError, ValueError) as error:
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


_POLYGON_TYPES: Final = frozenset({"POLYGON", "POLYGONZ", "POLYGONM"})


def _read_roi_archive(
    path: Path, height_crs: str | None, extent: Mapping[str, float]
) -> tuple[list[dict[str, Any]], list[_RoiLayer]]:
    """List the archive's members and read every shapefile in it, without extracting to disk."""
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
                        raise PilotError(
                            f"{path} unpacks to more than {_ROI_ARCHIVE_MAX_BYTES} bytes"
                        )
                    stream = archive.extractfile(member)
                    if stream is not None:
                        contents[member.name] = stream.read()
    except (tarfile.TarError, OSError, EOFError) as error:
        raise PilotError(f"{path} is not a readable gzip tar archive: {error}") from error
    members.sort(key=lambda item: str(item["name"]))
    layers: list[_RoiLayer] = []
    for shp_name in sorted(name for name in contents if name.lower().endswith(".shp")):
        stem = shp_name[: -len(".shp")]
        parts = {ext: contents.get(stem + ext) for ext in (".shp", ".shx", ".dbf", ".prj")}
        layers.append(_read_layer(stem, parts, height_crs, extent))
    return members, layers


def _read_layer(
    stem: str,
    parts: Mapping[str, bytes | None],
    height_crs: str | None,
    extent: Mapping[str, float],
) -> _RoiLayer:
    readers = {
        key.lstrip("."): stdio.BytesIO(data)
        for key, data in parts.items()
        if data is not None and key != ".prj"
    }
    try:
        with shapefile.Reader(**readers) as reader:
            fields = [field.name for field in reader.fields if field.name != "DeletionFlag"]
            shapes = reader.shapes()
            records = reader.records() if "dbf" in readers else [None] * len(shapes)
            layer_type = str(reader.shapeTypeName)
            layer_bbox = [float(value) for value in reader.bbox]
    except (shapefile.ShapefileException, OSError, ValueError, struct.error) as error:
        raise PilotError(f"the shapefile {stem}.shp cannot be read: {error}") from error
    rings: list[list[tuple[float, float]]] = []
    shape_summaries: list[dict[str, Any]] = []
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
        shape_summaries.append(
            {
                "index": index,
                "shape_type": str(shape.shapeTypeName),
                "points": len(points),
                "parts": len(shape.parts),
                "bbox_m": [float(value) for value in shape.bbox] if points else None,
                "record": None if row is None else _shape_record(row.as_dict()),
            }
        )
    prj = parts.get(".prj")
    prj_wkt = None if prj is None else prj.decode("utf-8", errors="strict").strip()
    crs_matches, comparison = _compare_crs(prj_wkt, height_crs)
    summary = {
        "name": stem,
        "components": sorted(ext for ext, data in parts.items() if data is not None),
        "shape_type": layer_type,
        "shape_count": len(shapes),
        "bbox_m": layer_bbox,
        "fields": fields,
        "prj_wkt": prj_wkt,
        "crs_matches_height": crs_matches,
        "crs_comparison": comparison,
        "inside_height_extent": inside,
        "shapes": shape_summaries,
    }
    drawable = bool(crs_matches) and inside and layer_type in _POLYGON_TYPES and bool(rings)
    return _RoiLayer(summary=summary, rings=rings, drawable=drawable)


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
    files. Only the height, count, slope and error GeoTIFFs and the ROI archive are fetched,
    whole and only below the 200 MB limit; never the clones, the point cloud or the 87S mosaic.
    """

    base_url: str = "https://pgda.gsfc.nasa.gov/"
    site: str = "Site01"

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

    def fetch(self, raw_dir: Path, download: Downloader, now: Clock) -> None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=raw_dir, prefix=".fetch-") as staging_name:
            staging = Path(staging_name)
            documents = []
            for role, url, file_name in self._documents():
                fetched = download(url, staging / file_name, max_bytes=WHOLE_FILE_LIMIT_BYTES)
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
            for role, name, magic in selected:
                fetched = download(
                    listed[name]["url"],
                    staging / name,
                    max_bytes=WHOLE_FILE_LIMIT_BYTES,
                    magic=magic,
                )
                files.append(
                    _fetch_entry(fetched, now(), role=role, listed_by=listed[name]["listed_by"])
                )
            chosen = {name for _, name, _ in selected}
            metadata = {
                "source": self.name,
                "product": self.product,
                "command": f"{COMMAND} fetch {self.name}",
                "whole_file_limit_bytes": WHOLE_FILE_LIMIT_BYTES,
                "documents": documents,
                "files": files,
                "roi_archive_listed": bool(rois),
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
        names = {role.key: files[role.key]["file_name"] for role in _GEOTIFF_ROLES}

        decoded: dict[str, _Decoded] = {}
        entries: list[dict[str, Any]] = []
        for role in _GEOTIFF_ROLES:
            fetch_entry = files[role.key]
            description = readme[role.suffix]
            units = role.units if role.units_phrase in description else UNKNOWN
            item = self._decode(raw_dir, root, role, fetch_entry, units, attribution, names, readme)
            decoded[role.key] = item
            entries.append(self._file_entry(role, fetch_entry, description, units, item))

        height = decoded["height"]
        transform, _, extent = _grid(height.band, raw_dir / names["height"])
        for entry, role in zip(entries, _GEOTIFF_ROLES, strict=True):
            entry["grid_matches_height"] = _same_grid(decoded[role.key].band, height.band)
        support = self._effective_support(height, decoded["lola_return_count"], names)

        roi, layers, roi_record = self._roi(
            raw_dir, root, files, height, extent, attribution, readme
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
            if role.key == "height" and item.record.units != UNKNOWN:
                record = ProductRecord.model_validate(
                    {**item.record.model_dump(), "verification_status": "VERIFIED"}
                )
                records[_GEOTIFF_ROLES.index(role)] = record
                basis = (
                    "downloaded, decoded, masked with quality.validity_mask and plotted in the "
                    "preview PNG, as the PRD 5 entry rule requires"
                )
            else:
                record = item.record
                basis = (
                    "downloaded, decoded and masked with quality.validity_mask; not plotted, so "
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
            verification[roi_record.product_id] = {
                "status": roi_record.verification_status,
                "basis": (
                    "downloaded and every shapefile read; outlines drawn on the preview"
                    if roi["outlines_drawn"]
                    else "downloaded and every shapefile read; outlines not drawn: "
                    + str(roi["outlines_not_drawn_reason"])
                ),
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
            "dem_valid_fraction": entries[0]["valid"]["valid_fraction"],
            "effective_support": support,
            "roi": roi,
            "preview": preview,
            "verification": verification,
            "field_notes": self._field_notes(),
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
    ) -> _Decoded:
        path = raw_dir / str(fetch_entry["file_name"])
        metadata = self._metadata(role, str(fetch_entry["url"]), units, attribution, names, readme)
        bands, record = read_product(
            f"{self.product}/{fetch_entry['file_name']}", [path], metadata, root=root
        )
        if len(bands) != 1:
            raise PilotError(f"{path} has {len(bands)} bands; the README describes one map")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
            with rasterio.open(path) as src:
                tags = {str(k): str(v) for k, v in src.tags().items()}
        band = bands[0]
        return _Decoded(
            band=band,
            record=record,
            mask=validity_mask(band.array, nodata=band.nodata),
            tags=tags,
        )

    def _file_entry(
        self,
        role: _Role,
        fetch_entry: Mapping[str, Any],
        description: str,
        units: str,
        item: _Decoded,
    ) -> dict[str, Any]:
        band = item.band
        path_name = str(fetch_entry["file_name"])
        _, pixel, extent = _grid(band, Path(path_name))
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
            "bytes": fetch_entry["bytes_received"],
            "sha256": item.record.checksum,
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
    ) -> tuple[dict[str, Any], list[_RoiLayer], ProductRecord | None]:
        entry = files.get("roi_shapefiles")
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
        members, layers = _read_roi_archive(path, height.band.crs, extent)
        drawn = any(layer.drawable for layer in layers)
        if drawn:
            not_drawn = None
        elif not layers:
            not_drawn = "the archive holds no shapefile"
        else:
            reasons = []
            for layer in layers:
                summary = layer.summary
                if summary["prj_wkt"] is None:
                    reasons.append(
                        f"{summary['name']}: no .prj states its CRS, and outlines are never "
                        "placed by assuming one"
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
            not_drawn = "; ".join(reasons)
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
                "verification_status": "PARTIAL",
            }
        )
        roi = {
            "status": "read",
            "reason": None,
            "archive": {
                "file_name": entry["file_name"],
                "url": entry["url"],
                "local_path": local_path,
                "bytes": entry["bytes_received"],
                "sha256": entry["sha256"],
                "retrieved_at": entry["retrieved_at"],
                "readme_description": readme.get(_ROI_SUFFIX),
            },
            "members": members,
            "member_note": "read in memory; nothing is extracted and links are not followed",
            "shapefiles": [layer.summary for layer in layers],
            "outlines_drawn": drawn,
            "outlines_not_drawn_reason": not_drawn,
        }
        return roi, layers, record

    def _field_notes(self) -> dict[str, str]:
        return {
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


SOURCES: Mapping[str, Source] = {"pgda78": Pgda78Source()}
"""The registered pilot sources, by the name used on the command line."""


if __name__ == "__main__":
    raise SystemExit(main())
