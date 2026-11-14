"""Pydantic records for the release bundle (PRD 8.4, 8.12).

The bundle is the only interface between Python and the web (D23), so these models are the
contract that the JSON Schemas and the zod schemas are generated from.

Rules that hold for every record (PRD 8.12):

- Unknown fields are rejected.
- Every field is required and has no default. A value that may be absent is typed ``X | None``
  and must still be present, as ``null``.
- Missing values are ``null``, never ``0`` or an empty string. Strings are therefore non-empty.
- Validation is strict: ``"true"`` is not a bool and ``1.0`` is not a byte count.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0.0"
"""Version of the bundle records. Change it whenever a model in this module changes."""

GateId = Literal["G0", "G1", "G2", "G3", "G4", "G5", "G6"]
"""The release gates of PRD section 9."""

GateStatus = Literal["NOT_RUN", "PASS", "FAIL", "PARTIAL", "NOT_APPLICABLE"]
"""Gate states (PRD 8.12). Every gate starts as NOT_RUN; an untested gate is never a PASS."""

ShippingMode = Literal["A", "B", "C"]
"""Shipping modes (PRD 4). ``None`` in a manifest means the mode has not been chosen yet."""

ModelStatus = Literal["EXPERIMENTAL"]
"""Encoder status. EXPERIMENTAL is the only status the PRD defines (11.7)."""

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
    bytes: Annotated[int, Field(ge=0)]


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
