"""Release bundle writer and validator, and the labelled mock bundle (PRD 8.4, 8.12, 12).

The bundle is the only interface between the offline pipeline and the web (D23). ``write_bundle``
writes one, with a manifest that lists every other file with its SHA-256 and byte count.
``validate_bundle`` reports every way a bundle breaks the PRD 8.4 contract: schema errors,
unlisted, missing or altered files, blown budgets and invalid gate records, and on production
also a mock bundle or an unchosen shipping mode.

Command line::

    python -m terraluna_pipeline.export validate <bundle_dir> [--production]
    python -m terraluna_pipeline.export mock-bundle <out_dir>
    python -m terraluna_pipeline.export mock-bundle-check <bundle_dir>
    python -m terraluna_pipeline.export cards [--check] [--target-window W --earth-window E]
"""

from __future__ import annotations

import argparse
import difflib
import functools
import hashlib
import json
import math
import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import TypeAdapter, ValidationError
from pydantic_core import ErrorDetails

from terraluna_pipeline import provenance
from terraluna_pipeline.schema import (
    SCHEMA_VERSION,
    Body,
    BundleFile,
    BundleManifest,
    CampaignEntry,
    CardCampaign,
    CardDifferences,
    CardEarth,
    CardInterpretation,
    CardRecord,
    CardStability,
    CardTarget,
    ComparisonRecord,
    ComparisonRef,
    Distribution,
    FieldExperiment,
    FigureRef,
    GateId,
    GateRecord,
    GateStatus,
    ProductRecord,
    RadarContextEntry,
    RadarFingerprint,
    ShippingMode,
    TableDRow,
    TargetRecord,
    TiedByFamily,
    TieGroupByFamily,
    WindowEntry,
    WindowPolygon,
)

MANIFEST = "manifest.json"
VALIDATION = "validation.json"
BUNDLE_ROOT = "."
"""The error path for a problem with the bundle directory as a whole."""

KB = 1000
MB = 1000 * KB
"""PRD 8.4 budgets are SI units: 1 kB = 1000 bytes and 1 MB = 1,000,000 bytes."""

ErrorCode = Literal[
    "bundle_not_directory",
    "not_regular_file",
    "manifest_missing",
    "manifest_invalid_json",
    "manifest_schema",
    "manifest_lists_itself",
    "file_listed_twice",
    "missing_file",
    "unlisted_file",
    "bytes_mismatch",
    "sha256_mismatch",
    "budget_exceeded",
    "validation_missing",
    "validation_invalid_json",
    "validation_not_array",
    "gate_record_invalid",
    "gate_listed_twice",
    "mock_on_production",
    "shipping_mode_unset_on_production",
]


@dataclass(frozen=True)
class BundleError:
    """One way a bundle breaks the contract.

    ``path`` is relative to the bundle directory: a file, a PRD 8.4 path template for a budget
    over several files, or ``"."`` for the bundle directory itself.
    """

    code: ErrorCode
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.code}: {self.message}"


# --- Budgets (PRD 8.4) --------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


@functools.cache
def _template_pattern(template: str) -> re.Pattern[str]:
    """A PRD path template as a regex; each ``{placeholder}`` stands for one path segment."""
    return re.compile("[^/]+".join(re.escape(part) for part in _PLACEHOLDER.split(template)))


@dataclass(frozen=True)
class Budget:
    """A PRD 8.4 size budget for the files matching ``templates``.

    ``scope`` is ``"each"`` when every matching file has the budget on its own, and ``"total"``
    when the matching files share it. The PRD says "each", "per body" or "total" for some rows;
    a row that lists several files without saying so is read as a total, the stricter reading.
    """

    templates: tuple[str, ...]
    comparator: Literal["<", "≤"]
    limit_bytes: int
    scope: Literal["each", "total"]

    def matches(self, path: str) -> bool:
        return any(_template_pattern(template).fullmatch(path) for template in self.templates)

    def allows(self, size: int) -> bool:
        return size < self.limit_bytes if self.comparator == "<" else size <= self.limit_bytes

    def describe(self) -> str:
        return f"{self.comparator} {self.limit_bytes} bytes"


BUDGETS: tuple[Budget, ...] = (
    Budget(("manifest.json",), "<", 50 * KB, "each"),
    Budget(("sources.json",), "<", 200 * KB, "each"),
    Budget(("targets.json",), "<", 50 * KB, "each"),
    Budget(("windows/{body}.json",), "≤", 5 * MB, "each"),
    Budget(("comparisons/{target_id}.json",), "≤", 2 * MB, "each"),
    Budget(("tiles/{layer}/{z}/{x}/{y}.webp",), "≤", 300 * MB, "total"),
    Budget(("model/encoder.fp16.onnx",), "≤", 10 * MB, "each"),
    Budget(("model/embeddings.f16.bin", "model/embeddings.index.json"), "≤", 20 * MB, "total"),
    Budget(
        ("report/metrics.json", "report/curves.json", "report/umap.bin", "report/examples.json"),
        "≤",
        15 * MB,
        "total",
    ),
    Budget(("reference/background_{target_id}.json",), "<", 1 * MB, "each"),
)
"""Every PRD 8.4 path with a numeric budget, in table order. The other paths have none."""


def budget_errors(sizes: Mapping[str, int]) -> list[BundleError]:
    """Check file sizes, keyed by bundle path, against every PRD 8.4 budget."""
    errors: list[BundleError] = []
    for rule in BUDGETS:
        matched = sorted(path for path in sizes if rule.matches(path))
        if rule.scope == "each":
            errors.extend(
                BundleError(
                    "budget_exceeded",
                    path,
                    f"{sizes[path]} bytes; PRD 8.4 allows {rule.describe()}",
                )
                for path in matched
                if not rule.allows(sizes[path])
            )
            continue
        total = sum(sizes[path] for path in matched)
        if not rule.allows(total):
            errors.append(
                BundleError(
                    "budget_exceeded",
                    ", ".join(rule.templates),
                    f"{total} bytes in {len(matched)} files; "
                    f"PRD 8.4 allows {rule.describe()} in total",
                )
            )
    return errors


# --- JSON ---------------------------------------------------------------------------------------


def canonical_json(value: object) -> bytes:
    """Deterministic JSON: sorted keys, 2-space indent, LF line endings and a trailing newline.

    NaN and infinities are refused: they are not JSON, and a missing value is ``null``.
    """
    text = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def _refuse_constant(name: str) -> object:
    raise ValueError(f"{name} is not valid JSON")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _load_json(data: bytes) -> object:
    """Parse strict JSON: UTF-8, no NaN or infinities, and no duplicate keys.

    Raises ValueError otherwise, also for nesting too deep to parse.
    """
    try:
        return json.loads(
            data.decode("utf-8"), parse_constant=_refuse_constant, object_pairs_hook=_unique_keys
        )
    except RecursionError:
        raise ValueError("JSON is nested too deeply to parse") from None


def _describe(error: ErrorDetails) -> str:
    location = ".".join(str(part) for part in error["loc"])
    return f"{location}: {error['msg']}" if location else error["msg"]


# --- Writer -------------------------------------------------------------------------------------


def _check_paths(paths: set[str]) -> None:
    """Refuse a path that is also another path's directory, or that differs only in case."""
    taken = paths | {MANIFEST}
    spelling: dict[str, str] = {}
    for path in sorted(taken):
        parts = path.split("/")
        names = ["/".join(parts[:end]) for end in range(1, len(parts) + 1)]
        for directory in names[:-1]:
            if directory in taken:
                raise ValueError(f"{directory!r} would be both a file and a directory")
        for name in names:
            first = spelling.setdefault(name.casefold(), name)
            if first != name:
                raise ValueError(f"{first!r} and {name!r} differ only in case")


def _encode_bundle(
    files: Mapping[str, object], manifest_fields: Mapping[str, object]
) -> tuple[BundleManifest, dict[str, bytes]]:
    """Check a bundle and encode every file, manifest.json last, without writing anything."""
    if "files" in manifest_fields:
        raise ValueError("manifest_fields must not contain 'files'; write_bundle lists them")
    if MANIFEST in files:
        raise ValueError(f"{MANIFEST} is written by write_bundle and cannot be a bundle file")
    encoded = {
        path: value if isinstance(value, bytes) else canonical_json(value)
        for path, value in files.items()
    }
    listing = [
        BundleFile(path=path, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        for path, data in sorted(encoded.items())
    ]
    _check_paths(set(encoded))
    manifest = BundleManifest.model_validate({**manifest_fields, "files": listing})
    encoded[MANIFEST] = canonical_json(manifest.model_dump(mode="json"))
    return manifest, encoded


def _write_files(out: Path, encoded: Mapping[str, bytes]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for path, data in encoded.items():
        target = out.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _refuse_non_empty(out: Path) -> None:
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise FileExistsError(f"{out} exists and is not an empty directory")


def write_bundle(
    out_dir: Path, files: Mapping[str, object], manifest_fields: Mapping[str, object]
) -> BundleManifest:
    """Write a bundle and its manifest.json, and return the manifest.

    ``files`` maps bundle paths to contents: ``bytes`` are written as they are, and any other
    value is written as canonical JSON. ``manifest_fields`` holds every manifest field except
    ``files``, which lists each written file with its SHA-256 and byte count. Everything is
    checked before anything is written, and ``out_dir`` must be new or empty, since a bundle
    is immutable once written (PRD 8.4).
    """
    manifest, encoded = _encode_bundle(files, manifest_fields)
    out = Path(out_dir)
    _refuse_non_empty(out)
    _write_files(out, encoded)
    return manifest


# --- Validator ----------------------------------------------------------------------------------


def _raise(error: OSError) -> None:
    raise error


def _scan(root: Path) -> tuple[dict[str, int], list[str]]:
    """Sizes of the regular files under ``root``, and the paths of everything else.

    Symlinks are never followed, so a link cannot pull a file from outside into the bundle.
    """
    sizes: dict[str, int] = {}
    irregular: list[str] = []
    for directory, _, names in root.walk(on_error=_raise):
        for name in names:
            path = directory / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                irregular.append(relative)
            else:
                sizes[relative] = path.stat().st_size
    return dict(sorted(sizes.items())), sorted(irregular)


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _read_manifest(
    root: Path, sizes: Mapping[str, int]
) -> tuple[BundleManifest | None, list[BundleError]]:
    if MANIFEST not in sizes:
        return None, [BundleError("manifest_missing", MANIFEST, "no regular manifest.json")]
    try:
        raw = _load_json((root / MANIFEST).read_bytes())
    except ValueError as error:
        return None, [BundleError("manifest_invalid_json", MANIFEST, str(error))]
    try:
        return BundleManifest.model_validate(raw), []
    except ValidationError as error:
        return None, [
            BundleError("manifest_schema", MANIFEST, _describe(detail)) for detail in error.errors()
        ]


def _listing_errors(
    root: Path, manifest: BundleManifest, sizes: Mapping[str, int]
) -> list[BundleError]:
    errors: list[BundleError] = []
    listed: set[str] = set()
    for entry in manifest.files:
        path = entry.path
        if path == MANIFEST:
            errors.append(BundleError("manifest_lists_itself", MANIFEST, "it lists itself"))
            continue
        if path in listed:
            errors.append(BundleError("file_listed_twice", path, "listed more than once"))
            continue
        listed.add(path)
        if path not in sizes:
            errors.append(BundleError("missing_file", path, "listed but not a regular file"))
            continue
        if sizes[path] != entry.bytes:
            errors.append(
                BundleError(
                    "bytes_mismatch",
                    path,
                    f"manifest.json lists {entry.bytes} bytes; the file has {sizes[path]}",
                )
            )
        digest = _sha256(root / path)
        if digest != entry.sha256:
            errors.append(
                BundleError(
                    "sha256_mismatch",
                    path,
                    f"manifest.json lists sha256 {entry.sha256}; the file has {digest}",
                )
            )
    errors.extend(
        BundleError("unlisted_file", path, "present but not listed in manifest.json")
        for path in sizes
        if path != MANIFEST and path not in listed
    )
    return errors


def _production_errors(manifest: BundleManifest) -> list[BundleError]:
    errors: list[BundleError] = []
    if manifest.mock:
        errors.append(
            BundleError(
                "mock_on_production",
                MANIFEST,
                "mock is true; a mock bundle never deploys to production (PRD 8.4)",
            )
        )
    if manifest.shipping_mode is None:
        errors.append(
            BundleError(
                "shipping_mode_unset_on_production",
                MANIFEST,
                "shipping_mode is null; production needs a chosen shipping mode",
            )
        )
    return errors


def _validation_errors(root: Path, sizes: Mapping[str, int]) -> list[BundleError]:
    if VALIDATION not in sizes:
        return [BundleError("validation_missing", VALIDATION, "no regular validation.json")]
    try:
        raw = _load_json((root / VALIDATION).read_bytes())
    except ValueError as error:
        return [BundleError("validation_invalid_json", VALIDATION, str(error))]
    if not isinstance(raw, list):
        return [
            BundleError("validation_not_array", VALIDATION, "must be a JSON array of GateRecords")
        ]
    errors: list[BundleError] = []
    first_index: dict[str, int] = {}
    for index, entry in enumerate(raw):
        try:
            record = GateRecord.model_validate(entry)
        except ValidationError as error:
            errors.extend(
                BundleError("gate_record_invalid", VALIDATION, f"[{index}] {_describe(detail)}")
                for detail in error.errors()
            )
            continue
        first = first_index.setdefault(record.gate_id, index)
        if first != index:
            errors.append(
                BundleError(
                    "gate_listed_twice",
                    VALIDATION,
                    f"[{index}] {record.gate_id} is already recorded at [{first}]",
                )
            )
    return errors


def validate_bundle(bundle_dir: Path, *, production: bool) -> list[BundleError]:
    """Return every way ``bundle_dir`` breaks the bundle contract; an empty list means valid.

    With ``production=True`` a mock bundle and a null shipping_mode are errors too.
    """
    root = Path(bundle_dir)
    if not root.is_dir():
        return [BundleError("bundle_not_directory", BUNDLE_ROOT, f"{root} is not a directory")]
    sizes, irregular = _scan(root)
    errors = [
        BundleError("not_regular_file", path, "not a regular file; symlinks are not followed")
        for path in irregular
    ]
    manifest, manifest_errors = _read_manifest(root, sizes)
    errors.extend(manifest_errors)
    if manifest is not None:
        errors.extend(_listing_errors(root, manifest, sizes))
        if production:
            errors.extend(_production_errors(manifest))
    errors.extend(budget_errors(sizes))
    errors.extend(_validation_errors(root, sizes))
    return errors


# --- Mock bundle --------------------------------------------------------------------------------

MOCK_BUNDLE_VERSION = "mock-0"
MOCK_CREATED_UTC = "1970-01-01T00:00:00Z"
"""Fixed so the mock bundle regenerates byte for byte. It is not a creation time."""


def mock_manifest_fields() -> dict[str, object]:
    """The mock manifest: labelled mock, with every unknown value null and nothing claimed."""
    return {
        "bundle_version": MOCK_BUNDLE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "mock": True,
        "run_id": None,
        "git_sha": None,
        "created_utc": MOCK_CREATED_UTC,
        "shipping_mode": None,
        "targets": [],
        "model": None,
    }


def mock_gate_records() -> list[GateRecord]:
    """One record per gate, G0 to G6, all NOT_RUN: an untested gate is never a pass."""
    return [
        GateRecord(
            gate_id=gate_id,
            status="NOT_RUN",
            evidence_path=None,
            run_id=None,
            scope=None,
            result=None,
            consequence=None,
            command=None,
            config_version=None,
            input_hashes=[],
        )
        for gate_id in get_args(GateId)
    ]


def _encode_mock_bundle() -> tuple[BundleManifest, dict[str, bytes]]:
    records = [record.model_dump(mode="json") for record in mock_gate_records()]
    return _encode_bundle({VALIDATION: records}, mock_manifest_fields())


def _refuse_to_replace(out: Path, written: Mapping[str, bytes]) -> None:
    """Allow replacing ``out`` only if it is a mock bundle holding no file the mock does not write.

    Nothing is ever deleted: a file the mock bundle no longer writes has to be removed by hand.
    """
    if not out.exists():
        return
    if not out.is_dir():
        raise FileExistsError(f"{out} exists and is not a directory")
    sizes, irregular = _scan(out)
    others = sorted(set(irregular) | (set(sizes) - set(written)))
    if others:
        raise FileExistsError(
            f"{out} holds files the mock bundle does not write, so it is left as it is: "
            + ", ".join(others)
        )
    if not sizes:
        return
    if MANIFEST not in sizes:
        raise FileExistsError(f"{out} has no {MANIFEST} marked mock, so it is left as it is")
    try:
        manifest = _load_json((out / MANIFEST).read_bytes())
    except ValueError:
        manifest = None
    if not (isinstance(manifest, dict) and manifest.get("mock") is True):
        raise FileExistsError(f"{out}/{MANIFEST} is not marked mock, so it is left as it is")


def write_mock_bundle(out_dir: Path, *, replace: bool = False) -> BundleManifest:
    """Write the mock bundle: manifest.json and validation.json, and no other file.

    With ``replace=True`` an existing mock bundle in ``out_dir`` is overwritten in place.
    """
    manifest, encoded = _encode_mock_bundle()
    out = Path(out_dir)
    if replace:
        _refuse_to_replace(out, encoded)
    else:
        _refuse_non_empty(out)
    _write_files(out, encoded)
    return manifest


def mock_bundle_differences(committed: Path) -> list[str]:
    """Regenerate the mock bundle in a temporary directory and list how ``committed`` differs."""
    with tempfile.TemporaryDirectory() as temporary:
        fresh = Path(temporary) / "mock-bundle"
        write_mock_bundle(fresh)
        generated = {path: (fresh / path).read_bytes() for path in _scan(fresh)[0]}
    root = Path(committed)
    if not root.is_dir():
        return [f"{root}: not a directory"]
    sizes, irregular = _scan(root)
    differences: list[str] = []
    for path in sorted(set(generated) | set(sizes) | set(irregular)):
        if path in irregular:
            differences.append(f"{path}: not a regular file")
        elif path not in sizes:
            differences.append(f"{path}: generated but missing from {root}")
        elif path not in generated:
            differences.append(f"{path}: in {root} but not generated")
        else:
            current = (root / path).read_bytes()
            if current != generated[path]:
                diff = difflib.unified_diff(
                    current.decode("utf-8", "replace").splitlines(keepends=True),
                    generated[path].decode("utf-8", "replace").splitlines(keepends=True),
                    fromfile=f"{root}/{path}",
                    tofile=f"generated/{path}",
                )
                differences.append(f"{path}: differs from the generated file\n" + "".join(diff))
    return differences


# --- Bundle paths and contracts (CONTRACTS a.3) -------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
"""The repository root: pipeline/src/terraluna_pipeline/export.py is three levels below it."""

BUNDLE_PATHS_JSON = Path("packages/schema/json/bundle-paths.json")
"""The generated BUNDLE_PATHS map (T0B-04), relative to the repository root."""

SVG_ALLOWLIST = "svg_allowlist"
"""The contract of figures/{name}.svg: the SVG allowlist, not a JSON Schema."""

_FALLBACK_BUNDLE_PATHS: dict[str, str | None] = {
    "manifest.json": "BundleManifest",
    "validation.json": "Validation",
    "sources.json": "Sources",
    "targets.json": "Targets",
    "windows/{body}.json": "Windows",
    "comparisons/{target_id}.json": "Comparisons",
    "cards/{card_id}.json": "CardRecord",
    "reference/background_{target_id}.json": "Background",
    "model/model_card.json": "ModelRecord",
    "model/embeddings.index.json": "EmbeddingsIndex",
    "report/metrics.json": "Metrics",
    "report/curves.json": "Curves",
    "report/examples.json": "Examples",
    "report/umap.bin": "UmapHeader",
    "figures/{name}.svg": SVG_ALLOWLIST,
    "tiles/{layer}/{z}/{x}/{y}.webp": None,
    "windows/{body}.parquet": None,
    "model/encoder.onnx": None,
    "model/encoder.fp16.onnx": None,
    "model/embeddings.f16.bin": None,
}
"""A private copy of the CONTRACTS a.3 map, read only while bundle-paths.json does not exist.

``None`` marks a path with no JSON schema: budget and sha256 only.
"""


def _parse_bundle_paths(raw: object) -> dict[str, str | None]:
    if isinstance(raw, dict) and isinstance(raw.get("paths"), (dict, list)):
        raw = raw["paths"]
    mapping: dict[str, str | None] = {}
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = []
        for entry in raw:
            if not isinstance(entry, dict):
                raise ValueError("bundle-paths.json entries must be objects")
            template = entry.get("template", entry.get("path"))
            items.append((template, entry.get("contract")))
    else:
        raise ValueError("bundle-paths.json must be an object or an array")
    for template, contract in items:
        if not isinstance(template, str) or not (contract is None or isinstance(contract, str)):
            raise ValueError(f"bundle-paths.json: bad entry {template!r}: {contract!r}")
        mapping[template] = contract
    return mapping


def bundle_paths(root: Path = REPO_ROOT) -> dict[str, str | None]:
    """The BUNDLE_PATHS map: bundle path template to contract name (``None``: no JSON schema).

    Read from the generated packages/schema/json/bundle-paths.json once it exists; until then
    a private copy of the CONTRACTS a.3 map stands in for it.
    """
    path = Path(root) / BUNDLE_PATHS_JSON
    if not path.is_file():
        return dict(_FALLBACK_BUNDLE_PATHS)
    return _parse_bundle_paths(_load_json(path.read_bytes()))


def contract_for(bundle_path: str, paths: Mapping[str, str | None]) -> tuple[bool, str | None]:
    """``(known, contract)`` for a bundle path: whether a template matches, and its contract."""
    for template, contract in paths.items():
        if _template_pattern(template).fullmatch(bundle_path):
            return True, contract
    return False, None


def _contract_adapter(name: str) -> TypeAdapter[Any]:
    from terraluna_pipeline import schema

    for contract in schema.CONTRACTS:
        if contract.name == name:
            return contract.adapter
    raise ContractUnavailableError(f"contract {name} is not defined in schema.py yet")


def contract_errors(name: str | None, data: bytes) -> list[str]:
    """Every way ``data`` breaks contract ``name``; ``None`` (no JSON schema) always passes."""
    if name is None:
        return []
    if name == SVG_ALLOWLIST:
        from terraluna_pipeline.svg import svg_allowlist_errors

        return list(svg_allowlist_errors(data))
    if name == "UmapHeader":
        return ["report/umap.bin: the UmapHeader reader (report.read_umap) is not available"]
    try:
        adapter = _contract_adapter(name)
    except ContractUnavailableError as error:
        return [str(error)]
    try:
        raw = _load_json(data)
    except ValueError as error:
        return [f"invalid JSON: {error}"]
    try:
        adapter.validate_python(raw)
    except ValidationError as error:
        return [f"{name}: {_describe(detail)}" for detail in error.errors()]
    return []


def contains_mock(value: object) -> bool:
    """Whether any object inside ``value`` has ``"mock": true``."""
    if isinstance(value, dict):
        if value.get("mock") is True:
            return True
        return any(contains_mock(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_mock(item) for item in value)
    return False


# --- Record writers (T4L-06; CONTRACTS b) -------------------------------------------------------


class ContractUnavailableError(LookupError):
    """A BUNDLE_PATHS contract that schema.py does not define yet."""


class MockRecordError(ValueError):
    """A record with ``mock: true`` written where mock data may not go (PRD 8.4)."""


def _under_outputs(path: Path) -> bool:
    return "outputs" in Path(path).resolve().parts


def write_record(path: Path, contract: str, value: object, *, allow_mock: bool = False) -> bytes:
    """Validate ``value`` against ``contract`` and write it as canonical JSON; return the bytes.

    An invalid record raises (pydantic ValidationError) and writes nothing. A record holding
    ``mock: true`` is refused under any outputs/ directory, and elsewhere unless ``allow_mock``
    is passed (the mock generator writes only under fixtures/mock-bundle). The file is written
    through a temporary file and a rename, so a reader never sees half a file; the same value
    always gives the same bytes.
    """
    adapter = _contract_adapter(contract)
    validated = adapter.validate_python(value)
    data = canonical_json(adapter.dump_python(validated, mode="json"))
    adapter.validate_python(_load_json(data))
    if contains_mock(_load_json(data)):
        if _under_outputs(path):
            raise MockRecordError(f"{path}: a mock record is never written under outputs/")
        if not allow_mock:
            raise MockRecordError(f"{path}: a mock record needs allow_mock=True")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False, suffix=".tmp") as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(target)
    return data


def write_comparisons(path: Path, records: Sequence[object], *, allow_mock: bool = False) -> bytes:
    """comparisons/{target_id}.json (contract Comparisons); see write_record."""
    return write_record(path, "Comparisons", list(records), allow_mock=allow_mock)


def write_windows(path: Path, entries: Sequence[object], *, allow_mock: bool = False) -> bytes:
    """windows/{body}.json (contract Windows); see write_record."""
    return write_record(path, "Windows", list(entries), allow_mock=allow_mock)


def write_targets(path: Path, records: Sequence[object], *, allow_mock: bool = False) -> bytes:
    """targets.json (contract Targets); see write_record."""
    return write_record(path, "Targets", list(records), allow_mock=allow_mock)


def write_background(path: Path, background: object, *, allow_mock: bool = False) -> bytes:
    """reference/background_{target_id}.json (contract Background); see write_record."""
    return write_record(path, "Background", background, allow_mock=allow_mock)


def write_sources(path: Path, records: Sequence[object], *, allow_mock: bool = False) -> bytes:
    """A sources.json fragment (contract Sources); see write_record."""
    return write_record(path, "Sources", list(records), allow_mock=allow_mock)


def write_cards(
    out_dir: Path, cards: Sequence[CardRecord], *, allow_mock: bool = False
) -> list[Path]:
    """Write each card to ``out_dir/<card_id>.json`` (contract CardRecord) after validating all."""
    adapter = _contract_adapter("CardRecord")
    checked = [adapter.validate_python(card) for card in cards]
    written: list[Path] = []
    for card in checked:
        target = Path(out_dir) / f"{card.card_id}.json"
        write_record(target, "CardRecord", card, allow_mock=allow_mock)
        written.append(target)
    return written


# --- Evidence cards (T4L-06; PRD 3, 4, 10; CONTRACTS a.4) ----------------------------------------


class ExportReason(StrEnum):
    """The export.py reason codes (CONTRACTS a.5)."""

    FIGURE_NOT_RENDERED = "figure_not_rendered"
    STALE_RUN_ID = "stale_run_id"
    NOT_PRESENT = "not_present"


class CardExportError(ValueError):
    """A card that cannot be exported from its inputs."""


class ShippingModeUnsetError(CardExportError):
    """The comparison has no shipping mode: a card is never exported without one (PRD 4)."""


class EncoderGuardError(CardExportError):
    """The encoder distance would rank or sort a card while G5 is not PASS (PRD 11.7)."""


EARTH_LIMITS: tuple[str, ...] = (
    "Gravity: about one-sixth of Earth's on the Moon.",
    "Atmosphere: wind, rain, weathering and erosion shape Earth surfaces; none act on the Moon.",
    "Water and life: Earth ground carries moisture and biology; lunar regolith is dry apart "
    "from possible polar volatiles.",
    "Radiation and space weathering act directly on the lunar surface.",
    "Temperature: permanently shadowed lunar ground is far colder than any Earth surface.",
    "Illumination: the Sun stays near the horizon at the lunar pole.",
    "Regolith origin: lunar soil is made by impacts, with sharp, unweathered grains unlike most "
    "Earth sediments.",
)
"""Question 4's Earth limits for a lunar target, word for word from PRD 10."""

MARS_LIMITS: tuple[str, ...] = (
    "Gravity: about 0.38 of Earth's on Mars.",
    "Atmosphere: a thin CO₂ atmosphere in which wind and dust act but rain does not.",
    "Water and life: no liquid surface water or biology today.",
    "Temperature and radiation: lower temperatures and higher surface radiation than on Earth.",
    "Resolution: CTX DTMs resolve only features of roughly 100 m and larger, so finer "
    "roughness is not compared.",
)
"""Question 4's list for a Mars target, the PRD 10 Mars sentence split into its five items."""

TITLES: dict[str, str] = {
    "A": "Radar-scattering analog candidates for {place}",
    "B": "Terrain analog candidates for {place}, with radar context",
    "C": "Terrain analog pilot for {place}; radar comparison not completed",
}
"""PRD 4 title wording by shipping mode. ``{place}`` is Connecting Ridge for the lunar target
and the Mars target's name for a Mars target (the same pattern, an engineering choice)."""

MOON_PLACE = "Connecting Ridge"

CARD_WORDING: Literal["candidate window identified by this search"] = (
    "candidate window identified by this search"
)

FIELD_EXPERIMENT = (
    "Proposed, not yet carried out: survey a surface patch in this Earth window with a "
    "clast-size and coverage distribution, material and moisture notes, and calibrated radar "
    "observations under controlled geometry. Dimensions and sampling would be chosen for the "
    "sensor and site; none of these measurements has been made."
)
"""Question 7: the PRD 10 field-experiment template, worded as a proposal."""

CAMPAIGN_QUESTIONS = ("access", "permits", "season", "land_status", "logistics")

RADAR_OBSERVABLES = (
    "cpr_ratio_of_mean_powers",
    "cpr_mean_of_pixelwise_ratios",
    "sc",
    "oc",
    "total_power",
    "handedness_ratio",
)
RADAR_STATISTICS = ("median", "p05", "p95")

FIGURE_LETTERS: tuple[Literal["A", "B", "C"], ...] = ("A", "B", "C")

FIGURE_ALT = {
    "A": "Location maps: the Earth polygon and the {body} target window, each with a scale bar",
    "B": "nu(L) on log-log axes for the target and the Earth window, relief and texture",
    "C": "Radar block distributions with band labels, or the reason they are unavailable",
}

_BODY_NAME = {"moon": "lunar", "mars": "Mars", "earth": "Earth"}


@dataclass(frozen=True)
class CardLimits:
    """The written answers a card needs beyond the comparison's numbers.

    ``interpretation`` is question 2 (Assumed). ``differing``, ``ambiguous`` and
    ``not_observed`` are question 4 next to the fixed Earth or Mars limits. ``campaign`` holds
    the checked question 6 answers by question name; every other one stays ``unknown`` with a
    null source. ``claim_limits`` are added after the comparison's own.
    """

    interpretation: str
    differing: tuple[str, ...] = ()
    ambiguous: tuple[str, ...] = ()
    not_observed: tuple[str, ...] = ()
    campaign: Mapping[str, CampaignEntry] = field(default_factory=dict)
    claim_limits: tuple[str, ...] = ()


@dataclass(frozen=True)
class FigureIndexEntry:
    """One figure listed by outputs/figures/index.json (T4R-02)."""

    card_id: str
    figure: str
    path: str
    sha256: str
    bytes: int


def g5_status(gate_states: Sequence[GateRecord]) -> GateStatus:
    """The G5 status in ``gate_states``; NOT_RUN when G5 is absent (an untested gate never
    passes)."""
    statuses = [record.status for record in gate_states if record.gate_id == "G5"]
    if len(statuses) > 1:
        raise CardExportError("G5 is recorded more than once")
    return statuses[0] if statuses else "NOT_RUN"


def card_title(mode: ShippingMode, body: Body, target_name: str) -> str:
    """The PRD 4 title for ``mode``; Mars targets use the same pattern with their own name."""
    place = MOON_PLACE if body == "moon" else target_name
    return TITLES[mode].format(place=place)


def q4_limits(body: Body) -> list[str]:
    """The fixed question 4 list: the seven Earth limits, or the Mars list (PRD 10)."""
    if body == "moon":
        return list(EARTH_LIMITS)
    if body == "mars":
        return list(MARS_LIMITS)
    raise CardExportError("a card's target is a lunar or Mars window, not an Earth window")


def default_card_limits(comparison: ComparisonRecord, body: Body) -> CardLimits:
    """Question 2 and 4 answers stated only from the comparison record, for ``export cards``.

    ``not_observed`` names every excluded observable with its reasons and every family without
    a distance; ``ambiguous`` names every family tied with the target reference spread.
    ``differing`` stays empty: no rule in the PRD turns a distance into a verdict.
    """
    shipping_mode = comparison.shipping_mode
    if shipping_mode is None:
        raise ShippingModeUnsetError("shipping_mode is null; a card needs a chosen mode")
    observables = ", ".join(comparison.comparable_observables) or "no shared observable"
    interpretation = (
        f"Assumed: this Earth window could help investigate how surfaces with comparable "
        f"supported descriptors ({observables}) behave, as a {CARD_WORDING} for the "
        f"{_BODY_NAME[body]} target. It is an interpretation to test, not a measured result."
    )
    not_observed = [
        f"{item.observable}: {', '.join(item.reasons)}"
        for item in comparison.excluded_observables_and_reasons
    ]
    not_observed += [
        f"{family}: {entry.status} ({entry.reason})"
        for family, entry in comparison.distances_by_family.items()
        if entry.status != "available"
    ]
    ambiguous = [
        f"{family}: tied with the target reference spread"
        + (f" (group {entry.tie_group})" if entry.tie_group else "")
        for family, entry in comparison.distances_by_family.items()
        if entry.tied is True
    ]
    return CardLimits(
        interpretation=interpretation,
        ambiguous=tuple(ambiguous),
        not_observed=tuple(not_observed),
    )


def _polygon_pair(window_id: str, polygon: WindowPolygon) -> tuple[str, str]:
    digest = hashlib.sha256(canonical_json(polygon.model_dump(mode="json"))).hexdigest()
    return f"polygon:{window_id}", digest


def card_sources(card: CardRecord) -> list[str]:
    """The product ids a card's run_id is scoped over (CONTRACTS c.4)."""
    ids = set(card.target.product_ids) | set(card.earth.product_ids)
    ids |= {row.product_id for row in card.q3_measurements if row.product_id is not None}
    ids |= {entry.product_id for entry in card.radar_context}
    return sorted(ids)


def card_run_id(
    config_path: Path,
    target: CardTarget,
    earth: CardEarth,
    product_ids: Sequence[str],
    sources: Mapping[str, ProductRecord],
) -> str:
    """provenance.run_id over the card's own sources and its two polygons (CONTRACTS c.4).

    Each polygon enters as the pseudo-product ``polygon:<window_id>`` with the sha256 of its
    canonical JSON, so a changed polygon, a changed source checksum or a changed config byte
    each give a new run_id.
    """
    missing = sorted(set(product_ids) - set(sources))
    if missing:
        raise CardExportError(f"products not in sources.json: {', '.join(missing)}")
    products: list[ProductRecord | tuple[str, str | None]] = [
        sources[product_id] for product_id in sorted(set(product_ids))
    ]
    products.append(_polygon_pair(target.target_window_id, target.polygon))
    products.append(_polygon_pair(earth.earth_window_id, earth.polygon))
    return provenance.run_id(config_path, products)


def recompute_card_run_id(
    card: CardRecord, sources: Mapping[str, ProductRecord], config_path: Path
) -> str:
    """The run_id ``card`` should hold now, from its own fields, the sources and the config."""
    return card_run_id(config_path, card.target, card.earth, card_sources(card), sources)


def _radar_context(entries: Sequence[WindowEntry]) -> list[RadarContextEntry]:
    context: list[RadarContextEntry] = []
    for entry in entries:
        for fingerprint in entry.radar:
            radar = fingerprint.radar
            if radar is None:
                continue
            for observable in RADAR_OBSERVABLES:
                distribution: Distribution | None = getattr(radar, observable)
                if distribution is None or distribution.n == 0:
                    reason = radar.reason or (
                        "missing_channel" if distribution is None else "no_valid_blocks"
                    )
                    context.append(
                        RadarContextEntry(
                            product_id=radar.product_id,
                            band=radar.band,
                            observable=observable,
                            statistic="median",
                            value=None,
                            unit=None,
                            evidence_label="Unavailable",
                            support_m=radar.support_m,
                            reason=reason,
                        )
                    )
                    continue
                context.extend(
                    RadarContextEntry(
                        product_id=radar.product_id,
                        band=radar.band,
                        observable=observable,
                        statistic=statistic,
                        value=getattr(distribution, statistic),
                        unit=None,
                        evidence_label=fingerprint.evidence_label,
                        support_m=radar.support_m,
                        reason=None,
                    )
                    for statistic in RADAR_STATISTICS
                )
    return context


def _radar_prints(entries: Sequence[WindowEntry]) -> list[RadarFingerprint]:
    return [f.radar for entry in entries for f in entry.radar if f.radar is not None]


def _table_d(
    comparison: ComparisonRecord, target: WindowEntry, earth: WindowEntry
) -> list[TableDRow]:
    """Table D: one row per distance family, with the comparison's numbers unchanged."""
    grid = target.window.analysis_grid_m
    side = math.sqrt(target.window.area_m2)
    geometry = f"{target.window.polygon.frame} target window; {earth.window.polygon.frame} Earth"
    radar = _radar_prints([target, earth])
    bands = sorted({print_.band for print_ in radar})
    rows: list[TableDRow] = []
    for family, entry in comparison.distances_by_family.items():
        band: str | None = None
        row_geometry: str | None = geometry
        if family in ("terrain_relief", "terrain_texture"):
            support = f"{grid:g} m analysis grid in {side:g} m windows"
        elif family == "radar_cpr":
            supports = sorted({print_.support_m for print_ in radar})
            support = (
                ", ".join(f"{value:g} m blocks" for value in supports)
                if supports
                else "no qualified radar pair"
            )
            band = "/".join(bands) if bands else None
            incidences = [p.incidence_deg for p in radar if p.incidence_deg is not None]
            row_geometry = (
                "; ".join(f"incidence {i.min:g} to {i.max:g} deg" for i in incidences) or None
            )
        else:
            support = "encoder embedding of each window (experimental)"
            row_geometry = None
        rows.append(
            TableDRow(
                observable=f"{family} distance",
                value=entry.value,
                unit=entry.unit,
                evidence_label=entry.evidence_label,
                support=support,
                product_id=None,
                band=band,
                geometry=row_geometry,
                reason=entry.reason,
            )
        )
    return rows


def _figures(card_id: str, body: Body, index: Sequence[FigureIndexEntry]) -> list[FigureRef]:
    listed = {(entry.card_id, entry.figure) for entry in index}
    figures: list[FigureRef] = []
    for letter in FIGURE_LETTERS:
        alt = FIGURE_ALT[letter].format(body=_BODY_NAME[body])
        if (card_id, letter) in listed:
            path = f"figures/{card_id}_{letter}.svg"
            figures.append(FigureRef(figure=letter, path=path, alt=alt, reason=None))
        else:
            figures.append(
                FigureRef(
                    figure=letter,
                    path=None,
                    alt=alt,
                    reason=ExportReason.FIGURE_NOT_RENDERED.value,
                )
            )
    return figures


def _mode_claims(mode: ShippingMode, body: Body, g5: GateStatus) -> list[str]:
    claims: list[str] = []
    if body == "mars":
        claims.append("Mars targets are compared on terrain only; no radar product is used.")
    if mode == "C":
        claims.append(
            "Mode C: radar comparison not completed. The radar inputs were not qualified within "
            "the pilot, so no radar distance or radar signature is part of this card."
        )
    elif mode == "B":
        claims.append(
            "Mode B: candidates rest on supported terrain only. Radar signatures are shown "
            "separately with their band labels; cross-band calibration is unresolved, so no "
            "radar distance is given."
        )
    else:
        claims.append(
            "Mode A: radar and terrain distances are separate entries and are never combined."
        )
    if g5 != "PASS":
        claims.append(
            f"The encoder is EXPERIMENTAL (G5 {g5}); its distance, if shown, is Predicted and "
            "takes no part in any ranking."
        )
    claims.append(
        "Field confirmation is future work; similarity here does not establish block sizes, soil "
        "strength, buried structure or low-gravity mobility (PRD 3)."
    )
    return claims


def export_card(
    windows: Mapping[str, WindowEntry],
    comparison: ComparisonRecord,
    sources: Sequence[ProductRecord],
    limits: CardLimits,
    gate_states: Sequence[GateRecord],
    *,
    target: TargetRecord,
    config_path: Path,
    figures_index: Sequence[FigureIndexEntry] = (),
) -> CardRecord:
    """Build the evidence card of one comparison (PRD 10). Nothing here is a result.

    ``windows`` maps window ids to WindowEntries and must hold the comparison's target and Earth
    windows. ``sources`` are the ProductRecords the run_id is scoped over; every product the card
    names must be among them. ``gate_states`` are the validation.json records; the card stores
    only G5's status. Every number is copied from the comparison (distances, table D) or from the
    windows' radar fingerprints (radar context), never recomputed or rounded. A null shipping
    mode raises ShippingModeUnsetError, and an encoder sort rule while G5 is not PASS raises
    EncoderGuardError.
    """
    mode = comparison.shipping_mode
    if mode is None:
        raise ShippingModeUnsetError(
            f"{comparison.target_window_id}__{comparison.earth_window_id}: shipping_mode is "
            "null; a card is never exported without a chosen mode (PRD 4)"
        )
    if target.shipping_mode is not None and target.shipping_mode != mode:
        raise CardExportError(
            f"target {target.target_id} is Mode {target.shipping_mode}, comparison Mode {mode}"
        )
    g5 = g5_status(gate_states)
    if (
        g5 != "PASS"
        and comparison.sort_rule is not None
        and "model_embedding" in comparison.sort_rule
    ):
        raise EncoderGuardError(
            f"sort rule {comparison.sort_rule!r} uses the encoder while G5 is {g5}"
        )
    try:
        target_entry = windows[comparison.target_window_id]
        earth_entry = windows[comparison.earth_window_id]
    except KeyError as error:
        raise CardExportError(f"window {error.args[0]} is not in the windows file") from None
    body = target_entry.window.body
    if body != target.body:
        raise CardExportError(
            f"target window is on {body}, target {target.target_id} on {target.body}"
        )
    if earth_entry.window.body != "earth":
        raise CardExportError(f"{comparison.earth_window_id} is not an Earth window")
    by_id = {record.product_id: record for record in sources}
    unknown = sorted(set(comparison.source_ids) - set(by_id))
    if unknown:
        raise CardExportError(f"comparison sources not in sources.json: {', '.join(unknown)}")
    target_products = set(target_entry.window.product_ids) | {
        pid for pid in comparison.source_ids if by_id[pid].body == body
    }
    earth_products = set(earth_entry.window.product_ids) | {
        pid for pid in comparison.source_ids if by_id[pid].body == "earth"
    }
    card_target = CardTarget(
        target_id=target.target_id,
        target_window_id=comparison.target_window_id,
        body=body,
        polygon=target_entry.window.polygon,
        product_ids=sorted(target_products),
    )
    card_earth = CardEarth(
        earth_window_id=comparison.earth_window_id,
        polygon=earth_entry.window.polygon,
        product_ids=sorted(earth_products),
        search_region_id=earth_entry.window.environment_flags.search_region_id,
    )
    card_id = f"{comparison.target_window_id}__{comparison.earth_window_id}"
    distances = comparison.distances_by_family
    radar_context = _radar_context([target_entry, earth_entry]) if mode in ("A", "B") else []
    table_d = _table_d(comparison, target_entry, earth_entry)
    campaign = {
        question: limits.campaign.get(question, CampaignEntry(value="unknown", source=None))
        for question in CAMPAIGN_QUESTIONS
    }
    unknown_questions = sorted(set(limits.campaign) - set(CAMPAIGN_QUESTIONS))
    if unknown_questions:
        raise CardExportError(f"unknown campaign questions: {', '.join(unknown_questions)}")
    region = card_earth.search_region_id or "an undeclared region"
    q1 = (
        f"Earth window {card_earth.earth_window_id} ({region}; {card_earth.polygon.crs}) "
        f"compared with the {_BODY_NAME[body]} target window {card_target.target_window_id} "
        f"of {target.name} ({card_target.polygon.crs}), each a "
        f"{math.sqrt(target_entry.window.area_m2):g} m window on a "
        f"{target_entry.window.analysis_grid_m:g} m analysis grid. Mode {mode}."
    )
    product_ids = target_products | earth_products | {entry.product_id for entry in radar_context}
    run_id = card_run_id(config_path, card_target, card_earth, sorted(product_ids), by_id)
    claim_limits = list(
        dict.fromkeys(
            [*comparison.claim_limits, *_mode_claims(mode, body, g5), *limits.claim_limits]
        )
    )
    return CardRecord(
        card_id=card_id,
        mock=target.mock,
        run_id=run_id,
        shipping_mode=mode,
        title=card_title(mode, body, target.name),
        wording=CARD_WORDING,
        target=card_target,
        earth=card_earth,
        comparison_ref=ComparisonRef(
            path=f"comparisons/{target.target_id}.json",
            target_window_id=comparison.target_window_id,
            earth_window_id=comparison.earth_window_id,
        ),
        q1_compared=q1,
        q2_interpretation=CardInterpretation(text=limits.interpretation, evidence_label="Assumed"),
        q3_measurements=table_d,
        q4_differences=CardDifferences(
            limits=q4_limits(body),
            differing=list(limits.differing),
            ambiguous=list(limits.ambiguous),
            not_observed=list(limits.not_observed),
        ),
        q5_stability=CardStability(
            uncertainty_components=comparison.uncertainty_components,
            tied_by_family=TiedByFamily(
                terrain_relief=distances.terrain_relief.tied,
                terrain_texture=distances.terrain_texture.tied,
                radar_cpr=distances.radar_cpr.tied,
                model_embedding=distances.model_embedding.tied,
            ),
            tie_group_by_family=TieGroupByFamily(
                terrain_relief=distances.terrain_relief.tie_group,
                terrain_texture=distances.terrain_texture.tie_group,
                radar_cpr=distances.radar_cpr.tie_group,
                model_embedding=distances.model_embedding.tie_group,
            ),
        ),
        q6_campaign=CardCampaign(
            access=campaign["access"],
            permits=campaign["permits"],
            season=campaign["season"],
            land_status=campaign["land_status"],
            logistics=campaign["logistics"],
        ),
        q7_field_experiment=FieldExperiment(
            text=FIELD_EXPERIMENT, evidence_label="Assumed", status="proposed"
        ),
        distances_by_family=distances,
        radar_context=radar_context,
        figures=_figures(card_id, body, figures_index),
        g5_status=g5,
        claim_limits=claim_limits,
    )


# --- Inputs of export cards and export stage ----------------------------------------------------


class MissingInputError(LookupError):
    """A required input that does not exist yet; the command exits 3 naming it (CONTRACTS f)."""


class SourcesConflictError(ValueError):
    """Two sources.json fragments disagree about one product_id."""


@dataclass(frozen=True)
class Superseded:
    """A PARTIAL ProductRecord replaced at merge by a VERIFIED one of the same product_id."""

    product_id: str
    kept_from: str
    superseded_from: str
    superseded: dict[str, Any]


def source_fragments(root: Path) -> list[Path]:
    """Every outputs/**/sources.json, in sorted order (CONTRACTS b, sources.json row)."""
    outputs = Path(root) / "outputs"
    return sorted(path for path in outputs.rglob("sources.json") if path.is_file())


def merge_sources(
    fragments: Sequence[Path], root: Path
) -> tuple[list[ProductRecord], list[Superseded]]:
    """Merge sources fragments by product_id.

    Identical duplicates are kept once. When one record is VERIFIED and the other PARTIAL, the
    VERIFIED one is kept and the PARTIAL one is returned as superseded. Any other difference
    raises SourcesConflictError naming both files.
    """
    adapter = _contract_adapter("Sources")
    kept: dict[str, tuple[ProductRecord, str]] = {}
    superseded: list[Superseded] = []
    for fragment in fragments:
        name = Path(fragment).resolve().relative_to(Path(root).resolve()).as_posix()
        try:
            records: list[ProductRecord] = adapter.validate_python(
                _load_json(Path(fragment).read_bytes())
            )
        except (ValueError, ValidationError) as error:
            raise SourcesConflictError(f"{name}: not a valid sources file: {error}") from None
        for record in records:
            previous = kept.get(record.product_id)
            if previous is None or previous[0] == record:
                kept.setdefault(record.product_id, (record, name))
                continue
            old, old_name = previous
            statuses = (old.verification_status, record.verification_status)
            if statuses == ("PARTIAL", "VERIFIED"):
                kept[record.product_id] = (record, name)
                superseded.append(
                    Superseded(record.product_id, name, old_name, old.model_dump(mode="json"))
                )
            elif statuses == ("VERIFIED", "PARTIAL"):
                superseded.append(
                    Superseded(record.product_id, old_name, name, record.model_dump(mode="json"))
                )
            else:
                raise SourcesConflictError(
                    f"{record.product_id} differs between {old_name} and {name}"
                )
    merged = [kept[product_id][0] for product_id in sorted(kept)]
    return merged, superseded


def _read_contract(path: Path, contract: str) -> Any:
    if not path.is_file():
        raise MissingInputError(str(path))
    return _contract_adapter(contract).validate_python(_load_json(path.read_bytes()))


def read_figures_index(path: Path) -> list[FigureIndexEntry]:
    """outputs/figures/index.json (T4R-02), or ``[]`` when no figure has been rendered.

    Accepts an array of entries or ``{"figures": [...]}``; each entry names its card
    (``card_id`` or ``card``), figure letter, path, sha256 and bytes.
    """
    if not path.is_file():
        return []
    raw = _load_json(path.read_bytes())
    if isinstance(raw, dict):
        raw = raw.get("figures")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected an array of figure entries")
    entries: list[FigureIndexEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"{path}: a figure entry must be an object")
        card_id = item.get("card_id", item.get("card"))
        figure, file_path = item.get("figure"), item.get("path")
        sha, size = item.get("sha256"), item.get("bytes")
        if not (
            isinstance(card_id, str)
            and figure in FIGURE_LETTERS
            and isinstance(file_path, str)
            and isinstance(sha, str)
            and re.fullmatch(r"[0-9a-f]{64}", sha)
            and isinstance(size, int)
        ):
            raise ValueError(f"{path}: incomplete figure entry {item!r}")
        entries.append(FigureIndexEntry(card_id, str(figure), file_path, sha, size))
    return entries


@dataclass(frozen=True)
class CardInputs:
    """Where ``export cards`` reads and writes, relative to the repository root."""

    root: Path
    config: Path = Path("config/run.yaml")
    targets: Path = Path("outputs/search/targets.json")
    comparisons_dir: Path = Path("outputs/comparisons")
    windows_dir: Path = Path("data/processed/windows")
    validation: Path = Path("outputs/validation.json")
    figures_index: Path = Path("outputs/figures/index.json")
    out_dir: Path = Path("outputs/cards")

    def at(self, relative: Path) -> Path:
        return relative if relative.is_absolute() else self.root / relative


def _pairs_for(
    targets: Sequence[TargetRecord], pair: tuple[str, str] | None
) -> list[tuple[TargetRecord | None, str, str]]:
    if pair is not None:
        return [(None, pair[0], pair[1])]
    pairs: list[tuple[TargetRecord | None, str, str]] = []
    for target in targets:
        if target.shortlist is None or target.primary_window_id is None:
            continue
        pairs.extend(
            (target, target.primary_window_id, earth) for earth in target.shortlist.earth_window_ids
        )
    return pairs


def build_cards(
    inputs: CardInputs, pair: tuple[str, str] | None = None, *, extra_pairs: Sequence[str] = ()
) -> tuple[dict[str, bytes], list[str]]:
    """Build the cards ``export cards`` writes, in memory: ``({card_id: bytes}, absent)``.

    Without ``pair``, every shortlisted pair of every target; with it, that one slice pair.
    ``extra_pairs`` are card ids (from existing card files) to rebuild as well; those whose
    pair is not in the current comparisons are returned in ``absent``. A missing input raises
    MissingInputError.
    """
    config = inputs.at(inputs.config)
    if not config.is_file():
        raise MissingInputError(str(inputs.config))
    targets: list[TargetRecord] = _read_contract(inputs.at(inputs.targets), "Targets")
    gates: list[GateRecord] = _read_contract(inputs.at(inputs.validation), "Validation")
    fragments = source_fragments(inputs.root)
    if not fragments:
        raise MissingInputError("outputs/**/sources.json")
    sources, _ = merge_sources(fragments, inputs.root)
    figures = read_figures_index(inputs.at(inputs.figures_index))

    comparisons: dict[str, list[ComparisonRecord]] = {}
    for target in targets:
        path = inputs.at(inputs.comparisons_dir) / f"{target.target_id}.json"
        if path.is_file():
            comparisons[target.target_id] = _read_contract(path, "Comparisons")

    def locate(target_window: str, earth_window: str) -> tuple[TargetRecord, ComparisonRecord]:
        for target in targets:
            for record in comparisons.get(target.target_id, []):
                if (record.target_window_id, record.earth_window_id) == (
                    target_window,
                    earth_window,
                ):
                    return target, record
        raise LookupError(f"{target_window}__{earth_window}")

    windows: dict[str, dict[str, WindowEntry]] = {}

    def window_map(body: str) -> dict[str, WindowEntry]:
        if body not in windows:
            entries: list[WindowEntry] = _read_contract(
                inputs.at(inputs.windows_dir) / f"{body}.json", "Windows"
            )
            windows[body] = {entry.window.window_id: entry for entry in entries}
        return windows[body]

    wanted = _pairs_for(targets, pair)
    if not wanted and not extra_pairs:
        raise MissingInputError(f"a shortlisted pair in {inputs.targets}")
    requested = [(t, w) for _, t, w in wanted]
    for card_id in extra_pairs:
        target_window, _, earth_window = card_id.partition("__")
        requested.append((target_window, earth_window))
    built: dict[str, bytes] = {}
    absent: list[str] = []
    for target_window, earth_window in dict.fromkeys(requested):
        card_id = f"{target_window}__{earth_window}"
        try:
            target, record = locate(target_window, earth_window)
        except LookupError:
            if card_id in extra_pairs and (target_window, earth_window) not in [
                (t, w) for _, t, w in wanted
            ]:
                absent.append(card_id)
                continue
            raise MissingInputError(
                f"comparison {card_id} in {inputs.comparisons_dir}/<target_id>.json"
            ) from None
        entries = {**window_map(target.body), **window_map("earth")}
        card = export_card(
            entries,
            record,
            sources,
            default_card_limits(record, target.body),
            gates,
            target=target,
            config_path=config,
            figures_index=figures,
        )
        built[card_id] = canonical_json(card.model_dump(mode="json"))
    return built, absent


def run_cards(inputs: CardInputs, pair: tuple[str, str] | None, *, check: bool) -> int:
    """``export cards``: write outputs/cards/<card_id>.json, or with ``check`` compare only."""
    out_dir = inputs.at(inputs.out_dir)
    existing = sorted(path.stem for path in out_dir.glob("*.json")) if out_dir.is_dir() else []
    try:
        built, absent = build_cards(inputs, pair, extra_pairs=existing if check else ())
    except MissingInputError as error:
        print(f"blocked: missing_input {error}", file=sys.stderr)
        return 3
    except (CardExportError, SourcesConflictError, ValueError) as error:
        print(f"cards: {error}", file=sys.stderr)
        return 1
    if check:
        problems = [f"{card_id}: pair absent from the current comparisons" for card_id in absent]
        for card_id, data in sorted(built.items()):
            path = out_dir / f"{card_id}.json"
            if not path.is_file():
                problems.append(f"{card_id}: not written")
            elif path.read_bytes() != data:
                problems.append(f"{card_id}: differs from the recomputed card")
        for problem in problems:
            print(problem)
        if problems:
            return 1
        print(f"{len(built)} card(s) match their inputs")
        return 0
    cards = [CardRecord.model_validate_json(data) for data in built.values()]
    for path in write_cards(out_dir, cards):
        print(f"wrote {path}")
    return 0


# --- Command line -------------------------------------------------------------------------------


def _cards_command(args: argparse.Namespace) -> int:
    if (args.target_window is None) != (args.earth_window is None):
        print("cards: --target-window and --earth-window go together", file=sys.stderr)
        return 2
    pair = None if args.target_window is None else (args.target_window, args.earth_window)
    inputs = CardInputs(
        root=args.root,
        config=args.config,
        targets=args.targets,
        comparisons_dir=args.comparisons_dir,
        windows_dir=args.windows_dir,
        validation=args.validation,
        figures_index=args.figures_index,
        out_dir=args.out_dir,
    )
    return run_cards(inputs, pair, check=args.check)


def _add_card_commands(commands: Any) -> None:
    cards = commands.add_parser(
        "cards",
        help="build outputs/cards/<card_id>.json for the shortlisted (or one slice) pair(s)",
    )
    defaults = CardInputs(root=Path("."))
    cards.add_argument("--check", action="store_true", help="recompute in memory; write nothing")
    cards.add_argument("--target-window", default=None)
    cards.add_argument("--earth-window", default=None)
    cards.add_argument("--root", type=Path, default=Path("."))
    cards.add_argument("--config", type=Path, default=defaults.config)
    cards.add_argument("--targets", type=Path, default=defaults.targets)
    cards.add_argument("--comparisons-dir", type=Path, default=defaults.comparisons_dir)
    cards.add_argument("--windows-dir", type=Path, default=defaults.windows_dir)
    cards.add_argument("--validation", type=Path, default=defaults.validation)
    cards.add_argument("--figures-index", type=Path, default=defaults.figures_index)
    cards.add_argument("--out-dir", type=Path, default=defaults.out_dir)


_EXTRA_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {"cards": _cards_command}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m terraluna_pipeline.export",
        description="Validate a release bundle, or write the mock bundle (PRD 8.4).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="print every error in a bundle")
    validate.add_argument("bundle_dir", type=Path)
    validate.add_argument(
        "--production",
        action="store_true",
        help="also refuse a mock bundle and a null shipping_mode",
    )
    mock = commands.add_parser(
        "mock-bundle", help="write the mock bundle, replacing an existing mock bundle in place"
    )
    mock.add_argument("out_dir", type=Path)
    check = commands.add_parser(
        "mock-bundle-check",
        help="regenerate the mock bundle in a temporary directory and compare it with a copy",
    )
    check.add_argument("bundle_dir", type=Path)
    _add_card_commands(commands)
    args = parser.parse_args(argv)
    if args.command in _EXTRA_COMMANDS:
        return _EXTRA_COMMANDS[args.command](args)

    problems: list[str] = []
    if args.command == "validate":
        bundle_dir: Path = args.bundle_dir
        success = "valid production bundle" if args.production else "valid bundle"
        problems += map(str, validate_bundle(bundle_dir, production=args.production))
    elif args.command == "mock-bundle":
        bundle_dir = args.out_dir
        success = f"wrote a valid mock bundle {MOCK_BUNDLE_VERSION}"
        try:
            write_mock_bundle(bundle_dir, replace=True)
        except FileExistsError as error:
            print(f"mock-bundle: {error}", file=sys.stderr)
            return 1
        problems += map(str, validate_bundle(bundle_dir, production=False))
    else:
        bundle_dir = args.bundle_dir
        success = "mock bundle matches the generator and is valid"
        problems += mock_bundle_differences(bundle_dir)
        if not problems:
            problems += map(str, validate_bundle(bundle_dir, production=False))
    for problem in problems:
        print(problem)
    if problems:
        sys.stdout.flush()
        print(f"{len(problems)} problem(s) in {bundle_dir}", file=sys.stderr)
        return 1
    print(f"{success}: {bundle_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
