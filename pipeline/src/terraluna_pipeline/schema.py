"""Pydantic records for the release bundle (PRD 8.4, 8.12).

The bundle is the only interface between Python and the web (D23), so these models are the
contract that the JSON Schemas and the zod schemas are generated from.

Rules that hold for every record (PRD 8.12):

- Unknown fields are rejected.
- Every field is required and has no default. A value that may be absent is typed ``X | None``
  and must still be present, as ``null``.
- Missing values are ``null``, never ``0`` or an empty string. Strings are therefore non-empty.
- Validation is strict: ``"true"`` is not a bool and ``1.0`` is not a byte count.

The JSON Schemas (2020-12) in packages/schema/json/ are generated from these models and
committed; the web's zod schemas are generated from them (PRD 8.3, 8.9). CI fails on drift.

Command line::

    python -m terraluna_pipeline.schema write <out_dir>
    python -m terraluna_pipeline.schema check <schema_dir>
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, Strict, TypeAdapter
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode, JsonSchemaValue
from pydantic_core import CoreSchema

SCHEMA_VERSION = "1.0.0"
"""Version of the bundle records, as in the PRD 8.4 manifest example these models implement.

A test keeps the two equal, so a new version of the records also needs a PRD amendment.
"""

GateId = Literal["G0", "G1", "G2", "G3", "G4", "G5", "G6"]
"""The release gates of PRD section 9."""

GateStatus = Literal["NOT_RUN", "PASS", "FAIL", "PARTIAL", "NOT_APPLICABLE"]
"""Gate states (PRD 8.12). Every gate starts as NOT_RUN; an untested gate is never a PASS."""

ShippingMode = Literal["A", "B", "C"]
"""Shipping modes (PRD 4). ``None`` in a manifest means the mode has not been chosen yet."""

ModelStatus = Literal["EXPERIMENTAL"]
"""Encoder status. EXPERIMENTAL is the only status the PRD defines (11.7)."""

JS_MAX_SAFE_INTEGER = 2**53 - 1
"""JavaScript's Number.MAX_SAFE_INTEGER. A larger byte count would reach the web rounded."""

NonEmptyStr = Annotated[str, Field(min_length=1)]

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
"""A SHA-256 digest as exactly 64 lowercase hexadecimal characters."""

_SEGMENT = r"[A-Za-z0-9_-][A-Za-z0-9._-]*"

BundlePath = Annotated[str, Field(pattern=rf"^{_SEGMENT}(/{_SEGMENT})*$")]
"""A relative POSIX path inside the bundle.

It is never absolute, never contains ``.`` or ``..`` segments, empty segments or backslashes,
and no segment starts with a dot.
"""

BundleVersion = Annotated[str, Field(pattern=rf"^{_SEGMENT}$")]
"""A bundle version, used as the directory name ``release/<bundle_version>/``."""

SemVer = Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]

UtcTimestamp = Annotated[
    str,
    Field(
        pattern=(
            r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
            r"T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
        )
    ),
]
"""A UTC timestamp in the form ``YYYY-MM-DDTHH:MM:SSZ``."""


class Record(BaseModel):
    """Base for bundle records: unknown fields are rejected and nothing is coerced."""

    # rust-regex (pydantic's default, pinned here) anchors `$` at the end of the text; Python's
    # re would also let `$` match before a trailing newline, so "<64 hex>\n" would pass.
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, regex_engine="rust-regex")


class BundleFile(Record):
    """One file in the bundle, other than manifest.json itself (PRD 8.4)."""

    path: BundlePath
    sha256: Sha256
    bytes: Annotated[int, Field(ge=0, le=JS_MAX_SAFE_INTEGER)]


class ModelSummary(Record):
    """The encoder shipped in the bundle (PRD 8.4). It stays out of rankings until G5 passes."""

    id: NonEmptyStr
    status: ModelStatus
    g5: GateStatus


class BundleManifest(Record):
    """manifest.json: every field of the PRD 8.4 example."""

    bundle_version: BundleVersion
    schema_version: SemVer
    mock: bool
    run_id: NonEmptyStr | None
    git_sha: NonEmptyStr | None
    created_utc: UtcTimestamp
    shipping_mode: ShippingMode | None
    targets: list[NonEmptyStr]
    model: ModelSummary | None
    files: list[BundleFile]


class GateRecord(Record):
    """One release gate's state in validation.json (PRD 8.12, section 9)."""

    gate_id: GateId
    status: GateStatus
    evidence_path: NonEmptyStr | None
    run_id: NonEmptyStr | None
    scope: NonEmptyStr | None
    result: NonEmptyStr | None
    consequence: NonEmptyStr | None


ValidationFile = Annotated[
    list[GateRecord],
    Strict(),
    Field(title="Validation", description="validation.json: the GateRecords (PRD 8.4, 8.12)."),
]
"""validation.json: an array of GateRecords."""


# --- JSON Schema generation (PRD 8.3, 8.9) ------------------------------------------------------

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


@dataclass(frozen=True)
class Contract:
    """A record whose JSON Schema is generated and committed.

    ``name`` is the schema name used in fixtures/schema-cases/cases.json.
    """

    name: str
    file_name: str
    adapter: TypeAdapter[Any]


CONTRACTS: tuple[Contract, ...] = (
    Contract("BundleManifest", "bundle-manifest.schema.json", TypeAdapter(BundleManifest)),
    Contract("GateRecord", "gate-record.schema.json", TypeAdapter(GateRecord)),
    Contract("Validation", "validation.schema.json", TypeAdapter(ValidationFile)),
)


class _GenerateJsonSchema(GenerateJsonSchema):
    """pydantic's generator, pinned to 2020-12 and with ``$schema`` as the first key."""

    schema_dialect = JSON_SCHEMA_DIALECT

    def generate(self, schema: CoreSchema, mode: JsonSchemaMode = "validation") -> JsonSchemaValue:
        return {"$schema": self.schema_dialect, **super().generate(schema, mode=mode)}


def generate_json_schemas(contracts: Sequence[Contract] = CONTRACTS) -> dict[str, bytes]:
    """The JSON Schema file contents by file name.

    The output is deterministic: pydantic orders the keywords, properties keep the field order,
    and the text is UTF-8 with a 2-space indent and a trailing newline.
    """
    generated: dict[str, bytes] = {}
    for contract in contracts:
        if contract.file_name in generated:
            raise ValueError(f"two contracts write {contract.file_name}")
        schema = contract.adapter.json_schema(schema_generator=_GenerateJsonSchema)
        text = json.dumps(schema, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        generated[contract.file_name] = text.encode("utf-8")
    return generated


def write_json_schemas(out_dir: Path, contracts: Sequence[Contract] = CONTRACTS) -> list[Path]:
    """Write the JSON Schemas into ``out_dir``, creating it and overwriting the files in place.

    Nothing is deleted, and nothing is written if a target is a symlink or not a regular file.
    """
    out = Path(out_dir)
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise FileExistsError(f"{out} is not a directory, so nothing was written")
    generated = generate_json_schemas(contracts)
    targets = {out / name: data for name, data in generated.items()}
    for target in targets:
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise FileExistsError(f"{target} is not a regular file, so nothing was written")
    out.mkdir(parents=True, exist_ok=True)
    for target, data in targets.items():
        target.write_bytes(data)
    return list(targets)


def json_schema_differences(
    committed_dir: Path, contracts: Sequence[Contract] = CONTRACTS
) -> list[str]:
    """Regenerate the JSON Schemas in a temporary directory and list how ``committed_dir`` differs.

    Every file in ``committed_dir`` must be generated, apart from hidden files such as .DS_Store,
    which are git-ignored and read by no generator.
    """
    with tempfile.TemporaryDirectory() as temporary:
        fresh = Path(temporary) / "json"
        write_json_schemas(fresh, contracts)
        generated = {path.name: path.read_bytes() for path in fresh.iterdir()}
    root = Path(committed_dir)
    if root.is_symlink() or not root.is_dir():
        return [f"{root}: not a directory"]
    committed = {path.name: path for path in root.iterdir() if not path.name.startswith(".")}
    differences: list[str] = []
    for name in sorted(set(generated) | set(committed)):
        path = committed.get(name)
        if path is None:
            differences.append(f"{name}: generated but missing from {root}")
        elif path.is_symlink() or not path.is_file():
            differences.append(f"{name}: not a regular file")
        elif name not in generated:
            differences.append(f"{name}: in {root} but not generated")
        elif (current := path.read_bytes()) != generated[name]:
            diff = difflib.unified_diff(
                current.decode("utf-8", "replace").splitlines(keepends=True),
                generated[name].decode("utf-8").splitlines(keepends=True),
                fromfile=f"{root}/{name}",
                tofile=f"generated/{name}",
            )
            differences.append(f"{name}: differs from the generated file\n" + "".join(diff))
    return differences


# --- Command line -------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m terraluna_pipeline.schema",
        description="Write the JSON Schemas generated from the bundle records, or check a copy.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser(
        "write", help="write the JSON Schemas, overwriting existing ones in place"
    )
    write.add_argument("out_dir", type=Path)
    check = commands.add_parser(
        "check",
        help="regenerate the JSON Schemas in a temporary directory and compare them with a copy",
    )
    check.add_argument("schema_dir", type=Path)
    args = parser.parse_args(argv)

    if args.command == "write":
        try:
            written = write_json_schemas(args.out_dir)
        except FileExistsError as error:
            print(f"schema write: {error}", file=sys.stderr)
            return 1
        print(f"wrote {len(written)} JSON Schemas to {args.out_dir}")
        return 0
    problems = json_schema_differences(args.schema_dir)
    for problem in problems:
        print(problem)
    if problems:
        sys.stdout.flush()
        print(
            f"{len(problems)} problem(s) in {args.schema_dir}; run make schema to regenerate",
            file=sys.stderr,
        )
        return 1
    print(f"JSON Schemas match schema.py: {args.schema_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
