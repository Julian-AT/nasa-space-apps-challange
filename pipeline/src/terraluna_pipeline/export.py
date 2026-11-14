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
"""

from __future__ import annotations

import argparse
import difflib
import functools
import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from terraluna_pipeline.schema import (
    SCHEMA_VERSION,
    BundleFile,
    BundleManifest,
    GateId,
    GateRecord,
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

    Raises ValueError otherwise.
    """
    return json.loads(
        data.decode("utf-8"), parse_constant=_refuse_constant, object_pairs_hook=_unique_keys
    )


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
    if MANIFEST not in sizes:
        return
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


# --- Command line -------------------------------------------------------------------------------


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
    args = parser.parse_args(argv)

    problems: list[str] = []
    if args.command == "validate":
        bundle_dir: Path = args.bundle_dir
        label = "production bundle" if args.production else "bundle"
        problems += map(str, validate_bundle(bundle_dir, production=args.production))
    elif args.command == "mock-bundle":
        bundle_dir = args.out_dir
        label = f"mock bundle {MOCK_BUNDLE_VERSION}"
        try:
            write_mock_bundle(bundle_dir, replace=True)
        except FileExistsError as error:
            print(f"mock-bundle: {error}", file=sys.stderr)
            return 1
        problems += map(str, validate_bundle(bundle_dir, production=False))
    else:
        bundle_dir = args.bundle_dir
        label = "mock bundle, identical to the generated one,"
        problems += mock_bundle_differences(bundle_dir)
        if not problems:
            problems += map(str, validate_bundle(bundle_dir, production=False))
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} problem(s) in {bundle_dir}", file=sys.stderr)
        return 1
    print(f"valid {label}: {bundle_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
