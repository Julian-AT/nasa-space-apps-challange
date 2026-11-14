"""Pydantic records for the release bundle (PRD 8.4, 8.12).

The bundle is the only interface between Python and the web (D23), so these models are the
contract that the JSON Schemas and the zod schemas are generated from.

Rules that hold for every record (PRD 8.12):

- Unknown fields are rejected.
- Every field is required and has no default. A value that may be absent is typed ``X | None``
  and must still be present, as ``null``.
- Missing values are ``null``, never ``0`` or an empty string. Strings are therefore non-empty.
  A ProductRecord also writes ``"unknown"`` for a field that applies to the product but was not
  established from it, and keeps ``null`` for a field that does not apply.
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
import re
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    Strict,
    TypeAdapter,
    model_validator,
)
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


# --- ProductRecord (PRD 5, 8.11, 8.12) ----------------------------------------------------------

UNKNOWN = "unknown"
"""The value of a field that applies to a product but was not established from it."""

Body = Literal["moon", "mars", "earth"]
"""The bodies the pipeline reads products for, as named in config/run.yaml."""

VerificationStatus = Literal["NOT_CHECKED", "VERIFIED", "PARTIAL", "BLOCKED", "FAILED"]
"""How far a product has been checked (PRD 5). Every input starts as NOT_CHECKED.

VERIFIED means the PRD 5 entry rule is met: a real sample and its metadata were downloaded,
decoded, masked and plotted. PARTIAL means some of that was done or some metadata is unknown.
BLOCKED means access needs an account, a registration or a human step. FAILED means a read was
attempted and did not succeed.
"""

LocalPath = Annotated[
    str, Field(pattern=rf"^data/(raw|interim|processed)/{_SEGMENT}(/{_SEGMENT})*$")
]
"""A file under the git-ignored data/raw, data/interim or data/processed (PRD 8.3).

It is relative to the repository root, so a record never names a home directory, and like a
bundle path it has no ``.`` or ``..`` segments, empty segments, backslashes or hidden segments.
"""

_URL_CHARACTER = r"!#$%&'()*+,./0-9:;=?@A-Z\[\]_a-z~-"
"""The characters RFC 3986 allows in a URL: unreserved, reserved and ``%``. Anything else, such
as a space, a newline or a non-ASCII letter, must be percent-encoded."""

_AUTHORITY_CHARACTER = r"!$%&'()*+,.0-9:;=@A-Z\[\]_a-z~-"
"""The URL characters other than ``/``, ``?`` and ``#``, which end the authority."""

_SCHEME = r"[A-Za-z][A-Za-z0-9+.-]*"

URL_PATTERN = rf"^{_SCHEME}://[{_URL_CHARACTER}]+$"
"""A URL with a scheme, written in RFC 3986 characters only."""

SIGNED_URL_TOKENS: tuple[str, ...] = (
    "signature=",
    "sig=",
    "token=",
    "credential=",
    "access_key",
    "accesskey",
    "api_key",
    "apikey",
    "expires=",
    "key-pair-id=",
    "password",
    "passwd",
    "secret",
)
"""Text that, in any case, marks a query string or fragment as signed or carrying a credential.

Matching is by substring, so ``signature=`` also covers ``X-Amz-Signature=`` and
``X-Goog-Signature=``, ``token=`` covers ``X-Amz-Security-Token=`` and ``access_token=``,
``credential=`` covers ``X-Amz-Credential=``, ``accesskey`` covers ``AWSAccessKeyId`` and
``expires=`` covers ``X-Amz-Expires=``. ``sig=`` is the Azure SAS signature. The ``=`` also
matches its percent-encoded form ``%3D``.
"""

_ENCODED_UNRESERVED = "%(2[DEde]|3[0-9]|[46][1-9A-Fa-f]|[57][0-9Aa]|5[Ff]|7[Ee])"
"""A percent-encoded RFC 3986 unreserved character (2.3): ``-``, ``.``, a digit, a letter, ``_``
or ``~``, in either hex case. Encoding one never changes what a URL means, and normalizers decode
it, so ``?%74oken=x`` is ``?token=x`` to a server. In a query or fragment it could only hide a
parameter name from the token check, which matches the literal text."""


def _any_case(text: str) -> str:
    """An ECMA-262 and Python regex matching ``text`` in any letter case, without flags."""
    return "".join(f"[{c.upper()}{c.lower()}]" if c.isalpha() else c for c in text)


def _token_pattern(token: str) -> str:
    """``token`` in any letter case, with ``=`` also matching ``%3D`` or ``%3d``."""
    return _any_case(token).replace("=", "(=|%3[Dd])")


CREDENTIAL_URL_PATTERN = (
    rf"^{_SCHEME}://[{_AUTHORITY_CHARACTER}]*@"
    rf"|[?#][{_URL_CHARACTER}]*({'|'.join(_token_pattern(token) for token in SIGNED_URL_TOKENS)})"
    rf"|[?#][{_URL_CHARACTER}]*{_ENCODED_UNRESERVED}"
)
"""Found in a URL that names userinfo credentials, carries a signed or token parameter, or
percent-encodes an unreserved character in its query or fragment.

A URL whose query and fragment encode no unreserved character spells every parameter name with
literal letters, digits and ``-._~``, so the substring check sees the name a server decodes. Other
percent-encodings, such as ``%2F`` or ``%20``, stay allowed, as does any encoding in the path.

The pattern is searched for, not matched in full, and is written in the subset where ECMA-262,
Python ``re`` and Rust ``regex`` agree: explicit ASCII classes, no ``.``, no flags, no lookaround.
The JSON Schema states it as ``not: {pattern}``; pydantic checks it with the same text.
"""

_CREDENTIAL_URL = re.compile(CREDENTIAL_URL_PATTERN)


def has_url_credentials(url: str) -> bool:
    """Whether ``url`` has userinfo credentials or a signed or token query parameter (PRD 8.12).

    A query or fragment that percent-encodes an unreserved character counts as one, because the
    encoding could hide such a parameter (see ``CREDENTIAL_URL_PATTERN``).
    """
    return _CREDENTIAL_URL.search(url) is not None


def _refuse_url_credentials(url: str) -> str:
    if has_url_credentials(url):
        raise ValueError(
            "source_url has credentials or a signature, or percent-encodes a letter, digit or "
            "-._~ in its query or fragment; record the public URL of the product"
        )
    return url


SourceUrl = Annotated[
    str,
    Field(pattern=URL_PATTERN),
    AfterValidator(_refuse_url_credentials),
    Field(json_schema_extra={"not": {"pattern": CREDENTIAL_URL_PATTERN}}),
]
"""A public URL: never userinfo credentials, a signed query or a token (PRD 8.12)."""

_UNKNOWN_ANY_CASE = _any_case(UNKNOWN)
"""``unknown`` in any letter case. The JSON Schema anchors it with ``^`` and ``$``; Python uses
``re.fullmatch``, because Python's ``$`` also matches before a trailing newline."""

_VERIFIED_NEEDS = (
    "a VERIFIED ProductRecord needs a local_path, a checksum and known units: the PRD 5 entry "
    "rule requires a downloaded, decoded file, and units are never guessed"
)


class ProductRecord(Record):
    """A product read, or attempted, by the pipeline (PRD 8.12); sources.json lists them.

    Every field must be present. A field that applies to the product but was not established
    from it is the string ``"unknown"``. ``null`` marks a field that does not apply, such as the
    radar frequency of a DEM, and is how the formatted fields (``source_url``, ``local_path`` and
    ``checksum``) say that there is no URL or no downloaded file. A value is never ``""`` or 0.

    Free-text fields hold what the product or its documentation states, keeping its wording and
    units. ``checksum`` is the SHA-256 of the file at ``local_path``.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "if": {
                "properties": {"verification_status": {"const": "VERIFIED"}},
                "required": ["verification_status"],
            },
            "then": {
                "properties": {
                    "local_path": {"type": "string"},
                    "checksum": {"type": "string"},
                    "units": {"not": {"pattern": f"^{_UNKNOWN_ANY_CASE}$"}},
                },
            },
        }
    )

    product_id: NonEmptyStr
    provider: NonEmptyStr
    version: NonEmptyStr
    source_url: SourceUrl | None
    local_path: LocalPath | None
    checksum: Sha256 | None
    body: Body
    acquisition_time: NonEmptyStr | None
    frequency_or_wavelength: NonEmptyStr | None
    transmit_receive_convention: NonEmptyStr | None
    units: NonEmptyStr
    calibration_type: NonEmptyStr | None
    native_grid: NonEmptyStr | None
    effective_resolution: NonEmptyStr | None
    crs_and_frame: NonEmptyStr
    incidence_source: NonEmptyStr | None
    qa_definition: NonEmptyStr | None
    license_or_attribution: NonEmptyStr
    verification_status: VerificationStatus

    @model_validator(mode="after")
    def _verified_has_a_decoded_file_with_units(self) -> Self:
        if self.verification_status == "VERIFIED" and (
            self.local_path is None
            or self.checksum is None
            or re.fullmatch(_UNKNOWN_ANY_CASE, self.units) is not None
        ):
            raise ValueError(_VERIFIED_NEEDS)
        return self


SourcesFile = Annotated[
    list[ProductRecord],
    Strict(),
    Field(title="Sources", description="sources.json: the ProductRecords (PRD 8.4, 8.12)."),
]
"""sources.json: an array of ProductRecords."""


# --- Science records I: common types (CONTRACTS a.2; PRD 3, 8.4) --------------------------------
#
# Cross-field rules are written twice: as a pydantic validator and as JSON Schema conditionals in
# ``allOf`` (``_when``), which the zod generator translates. Three rules compare values inside a
# document, which JSON Schema cannot express, so only pydantic checks them: a Distribution's
# values are sorted and counted by ``n``, a WindowPolygon's rings are closed, and a WindowEntry's
# ``terrain`` and ``radar`` slots hold fingerprints of that kind. Every bundle file is written
# through these models, so the web never receives a record that breaks them.

EvidenceLabel = Literal["Observed", "Derived", "Predicted", "Assumed", "Unavailable"]
"""The evidence label every displayed or exported quantity carries (PRD 3)."""

Family = Literal["terrain_relief", "terrain_texture", "radar_cpr", "model_embedding"]
"""The distance families, which are never combined into one score (D07)."""

REASON_CODE_PATTERN = r"^[a-z][a-z0-9_]*$"

ReasonCode = Annotated[str, Field(pattern=REASON_CODE_PATTERN)]
"""A snake_case reason. Each owning module defines its codes as a StrEnum (CONTRACTS a.5)."""

RunId = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
"""``sha256:`` and 64 lowercase hex characters, as provenance.run_id returns (PRD 8.4, 8.13)."""

RepoPath = Annotated[str, Field(pattern=rf"^{_SEGMENT}(/{_SEGMENT})*$")]
"""A relative POSIX path in the repository, with the BundlePath segment rules."""

WindowId = Annotated[str, Field(pattern=r"^(moon|mars|earth)-[a-z0-9][a-z0-9-]*$")]
"""A window id: the body, then lower-case letters, digits and hyphens (CONTRACTS a.2)."""

IsoDate = Annotated[str, Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")]
"""A calendar date in the form ``YYYY-MM-DD``."""

Float = Annotated[float, Field(allow_inf_nan=False)]
"""A finite number. pydantic would otherwise accept NaN and infinities, which JSON cannot hold."""

NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
"""A distance, a nu value or another quantity that cannot be negative. 0 is a real zero."""

Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
"""A size or spacing in metres, or a factor; 0 would stand for a missing value."""

Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
"""A fraction from 0 to 1."""

Percentile = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
"""A percentile from 0 to 100."""

Count = Annotated[int, Field(ge=0, le=JS_MAX_SAFE_INTEGER)]
"""A count, within JavaScript's safe integers."""

PositiveCount = Annotated[int, Field(ge=1, le=JS_MAX_SAFE_INTEGER)]
"""A count of at least one."""

ObservableName = NonEmptyStr
"""The key of a per-observable or per-field map."""

_NULL: dict[str, Any] = {"type": "null"}
_NUMBER: dict[str, Any] = {"type": "number"}
_STRING: dict[str, Any] = {"type": "string"}


def _const(value: str) -> dict[str, Any]:
    return {"type": "string", "const": value}


def _when(
    condition: dict[str, dict[str, Any]],
    then: dict[str, dict[str, Any]],
    otherwise: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A JSON Schema conditional: when every ``condition`` property is present and matches its
    test (``{"const": ...}`` or ``{"type": "null"}``), each ``then`` property must match its
    schema, and otherwise each ``otherwise`` property."""
    rule: dict[str, Any] = {
        "if": {"properties": condition, "required": list(condition)},
        "then": {"properties": then},
    }
    if otherwise is not None:
        rule["else"] = {"properties": otherwise}
    return rule


def _exactly_one_null(value: object, reason: object) -> bool:
    return (value is None) != (reason is None)


_STATISTICS = ("min", "median", "p05", "p95", "max")


class Distribution(Record):
    """Block values of one observable in one window, sorted ascending, and their order
    statistics. ``n`` counts the values; the five statistics are null exactly when n is 0."""

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                _when(
                    {"n": {"const": 0}},
                    {
                        "values": {"type": "array", "items": _NUMBER, "maxItems": 0},
                        **dict.fromkeys(_STATISTICS, _NULL),
                    },
                    {
                        "values": {"type": "array", "items": _NUMBER, "minItems": 1},
                        **dict.fromkeys(_STATISTICS, _NUMBER),
                    },
                )
            ]
        }
    )

    n: Count
    values: list[Float]
    min: Float | None
    median: Float | None
    p05: Float | None
    p95: Float | None
    max: Float | None

    @model_validator(mode="after")
    def _statistics_follow_the_values(self) -> Self:
        if self.n != len(self.values):
            raise ValueError(f"n is {self.n} but there are {len(self.values)} values")
        if any(a > b for a, b in pairwise(self.values)):
            raise ValueError("values must be sorted ascending")
        missing = [name for name in _STATISTICS if getattr(self, name) is None]
        if self.n == 0 and len(missing) != len(_STATISTICS):
            raise ValueError("an empty distribution has null order statistics")
        if self.n > 0 and missing:
            raise ValueError(f"a distribution of {self.n} values needs {', '.join(missing)}")
        return self


# --- WindowRecord (CONTRACTS a.4; PRD 5, 6.1, 7 D29, 8.12) --------------------------------------

Frame = Literal["utm", "polar_stereographic", "equirectangular", "aeqd_sphere"]

Position = Annotated[list[Float], Field(min_length=2, max_length=2)]
"""Two coordinates. A list, not a tuple: strict pydantic would refuse a Python list for a tuple."""

Ring = Annotated[list[Position], Field(min_length=4)]
"""A closed ring: four or more positions, the last equal to the first (RFC 7946 3.1.6)."""


class WindowPolygon(Record):
    """A window outline in its frame's metres and in degrees (WGS84 for Earth, planetocentric
    for the Moon and Mars). ``crs`` is read from the product, never assumed."""

    frame: Frame
    crs: NonEmptyStr
    epsg: Annotated[int, Field(ge=1, le=JS_MAX_SAFE_INTEGER)] | None
    utm_zone: NonEmptyStr | None
    ring_m: Ring
    ring_lonlat: Ring

    @model_validator(mode="after")
    def _rings_are_closed(self) -> Self:
        for name in ("ring_m", "ring_lonlat"):
            ring = getattr(self, name)
            if ring[0] != ring[-1]:
                raise ValueError(f"{name} is not closed: its last position must equal its first")
        return self


class EnvironmentFlags(Record):
    """Environment records of a window (PRD 5) and its permanent shadow (PRD 6.1).

    A flag that applies but was not checked is ``"unknown"``; on the Moon and Mars the Earth
    flags are null. A null permanent_shadow_fraction carries a reason in the WindowRecord's
    evidence_status_by_field.
    """

    search_region_id: NonEmptyStr | None
    scene_ids: list[NonEmptyStr]
    acquisition_dates: list[IsoDate]
    snow_or_freeze: NonEmptyStr | None
    moisture_state: NonEmptyStr | None
    surface_change_since_acquisition: NonEmptyStr | None
    permanent_shadow_fraction: Fraction | None
    surface_model_note: NonEmptyStr | None


class ObservedInterpolatedQuality(Record):
    """How much of the window's DEM was observed rather than interpolated (PGDA78 count map)."""

    observed_fraction: Fraction | None
    interpolated_fraction: Fraction | None
    source: NonEmptyStr | None
    reason: ReasonCode | None


class WindowRecord(Record):
    """One analysis window (PRD 8.12). UTM zone and EPSG live in ``polygon``; search region,
    scenes and environment live in ``environment_flags``."""

    window_id: WindowId
    body: Body
    polygon: WindowPolygon
    area_m2: Positive
    analysis_grid_m: Positive
    product_ids: list[NonEmptyStr]
    valid_fraction_by_observable: dict[ObservableName, Fraction | None]
    observed_interpolated_quality: ObservedInterpolatedQuality
    environment_flags: EnvironmentFlags
    evidence_status_by_field: dict[ObservableName, EvidenceLabel]


# --- FingerprintRecord (CONTRACTS a.4; PRD 6.2, 6.3) --------------------------------------------
# Values are copied from T3 terrain fingerprints and T2 block statistics, never derived again.


class UnsupportedLag(Record):
    """A lag that is not supported in this window, with the reason."""

    lag_m: Positive
    reason: ReasonCode


class TerrainVariant(Record):
    """nu over the window's lags for one variant: relief (raw) or texture (plane removed).

    ``nu_m`` keys are lags in metres as T3 writes them. Exactly one of ``hurst`` and
    ``hurst_reason`` is null. ``tilt`` holds the fitted plane coefficients (texture only).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                _when({"hurst": _NULL}, {"hurst_reason": _STRING}, {"hurst_reason": _NULL}),
            ]
        }
    )

    detrend: Literal["none", "plane"]
    supported_lags_m: list[Positive]
    unsupported_lags: list[UnsupportedLag]
    nu_m: dict[ObservableName, NonNegative | None]
    nu_by_direction_m: dict[ObservableName, dict[ObservableName, NonNegative | None]]
    hurst: Float | None
    hurst_reason: ReasonCode | None
    tilt: list[Float] | None

    @model_validator(mode="after")
    def _hurst_or_its_reason(self) -> Self:
        if not _exactly_one_null(self.hurst, self.hurst_reason):
            raise ValueError("exactly one of hurst and hurst_reason must be null")
        return self


class TerrainFingerprint(Record):
    """Terrain observables of a window on the analysis grid (PRD 6.3, D29 resampler)."""

    analysis_grid_m: Positive
    coarsening_factor: Positive | None
    resampler: Literal["aeqd_sphere"]
    valid_fraction: Fraction
    relief: TerrainVariant
    texture: TerrainVariant


class IncidenceRange(Record):
    """The incidence angles of the radar product over the window, in degrees."""

    min: Float
    max: Float


def _refuse_non_bool(value: object) -> object:
    # pydantic matches Literal[False] by equality, so 0 and 0.0 would pass; JSON Schema's const
    # and zod's literal accept only the boolean.
    if not isinstance(value, bool):
        raise ValueError("must be the boolean false")
    return value


NotIndependent = Annotated[Literal[False], BeforeValidator(_refuse_non_bool)]
"""Always false: radar blocks within a window are not independent samples (PRD 6.2)."""


class RadarFingerprint(Record):
    """Radar block statistics of one product over a window (PRD 6.2). Blocks are never
    independent samples, and CPR is aggregated as a ratio of mean powers."""

    product_id: NonEmptyStr
    band: NonEmptyStr
    frequency_or_wavelength: NonEmptyStr
    convention: NonEmptyStr | None
    support_m: Positive
    aggregation: Literal["ratio_of_mean_powers"]
    blocks_independent: NotIndependent
    n_blocks: Count
    n_valid: Count
    valid_fraction: Fraction
    cpr_ratio_of_mean_powers: Distribution | None
    cpr_mean_of_pixelwise_ratios: Distribution | None
    sc: Distribution | None
    oc: Distribution | None
    total_power: Distribution | None
    incidence_deg: IncidenceRange | None
    handedness_ratio: Distribution | None
    reason: ReasonCode | None


FingerprintKind = Literal["terrain", "radar"]


class FingerprintRecord(Record):
    """The terrain or radar fingerprint of a window: ``terrain`` is set exactly when kind is
    terrain and ``radar`` exactly when kind is radar."""

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                _when({"kind": {"const": "terrain"}}, {"radar": _NULL}),
                _when({"kind": {"const": "radar"}}, {"terrain": _NULL}),
                _when({"terrain": _NULL}, {"kind": _const("radar")}),
                _when({"radar": _NULL}, {"kind": _const("terrain")}),
            ]
        }
    )

    window_id: WindowId
    body: Body
    kind: FingerprintKind
    product_ids: list[NonEmptyStr]
    evidence_label: EvidenceLabel
    input_hash: Sha256
    run_id: RunId
    terrain: TerrainFingerprint | None
    radar: RadarFingerprint | None

    @model_validator(mode="after")
    def _kind_names_the_part(self) -> Self:
        if (self.terrain is not None) != (self.kind == "terrain") or (self.radar is not None) != (
            self.kind == "radar"
        ):
            raise ValueError(f"a {self.kind} fingerprint sets {self.kind} and nothing else")
        return self


WindowStatus = Literal["eligible", "rejected", "unknown"]

GridIndex = Annotated[list[Count], Field(min_length=2, max_length=2)]


class WindowEntry(Record):
    """One row of windows/{body}.json: a window, its fingerprints and its eligibility. A
    rejected or unknown window lists its reasons in order; an eligible one lists none."""

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                _when(
                    {"status": {"const": "eligible"}},
                    {"reasons": {"type": "array", "items": _STRING, "maxItems": 0}},
                    {"reasons": {"type": "array", "items": _STRING, "minItems": 1}},
                )
            ]
        }
    )

    window: WindowRecord
    terrain: FingerprintRecord | None
    radar: list[FingerprintRecord]
    status: WindowStatus
    reasons: list[ReasonCode]
    grid_index: GridIndex | None
    card_id: NonEmptyStr | None

    @model_validator(mode="after")
    def _reasons_and_slots(self) -> Self:
        if (self.status == "eligible") != (self.reasons == []):
            raise ValueError("an eligible window has no reasons; any other status has some")
        if self.terrain is not None and self.terrain.kind != "terrain":
            raise ValueError("the terrain slot holds a terrain fingerprint")
        if any(item.kind != "radar" for item in self.radar):
            raise ValueError("the radar slot holds radar fingerprints only")
        return self


WindowsFile = Annotated[
    list[WindowEntry],
    Strict(),
    Field(title="Windows", description="windows/{body}.json: the WindowEntries (PRD 8.4)."),
]
"""windows/{body}.json: an array of WindowEntries."""


# --- ComparisonRecord (CONTRACTS a.4; PRD 6.5, 6.6, 8.12; D07) -----------------------------------


class ReferenceSpread(Record):
    """The spread of distances within the target reference set (PRD 6.5 step 5)."""

    min: NonNegative
    median: NonNegative
    max: NonNegative
    n: PositiveCount


_DISTANCE_RULES: list[Any] = [
    _when(
        {"status": {"const": "available"}},
        {"value": _NUMBER, "reason": _NULL},
        {"value": _NULL, "reason": _STRING},
    ),
    _when(
        {"reference_spread": _NULL},
        {"reference_spread_reason": _STRING, "tied": _NULL},
        {"reference_spread_reason": _NULL},
    ),
    _when(
        {"background_percentile": _NULL},
        {"background_percentile_reason": _STRING},
        {"background_percentile_reason": _NULL},
    ),
]


class DistanceEntry(Record):
    """One family's distance between a target and an Earth window (PRD 6.5).

    ``value`` is set exactly when status is available, and ``reason`` exactly when it is not.
    A value of 0 is a real zero (identical fingerprints) and never stands for missing. Exactly
    one of each spread and percentile pair is null, and ``tied`` is null without a spread.
    """

    model_config = ConfigDict(json_schema_extra={"allOf": _DISTANCE_RULES})

    value: NonNegative | None
    unit: NonEmptyStr
    evidence_label: EvidenceLabel
    status: Literal["available", "unavailable", "not_comparable"]
    reason: ReasonCode | None
    reference_spread: ReferenceSpread | None
    reference_spread_reason: ReasonCode | None
    background_percentile: Percentile | None
    background_percentile_reason: ReasonCode | None
    tied: bool | None
    tie_group: NonEmptyStr | None
    tie_reason: ReasonCode | None

    @model_validator(mode="after")
    def _missing_is_null_with_a_reason(self) -> Self:
        if (self.value is not None) != (self.status == "available"):
            raise ValueError("value is set exactly when status is available")
        if (self.reason is None) != (self.status == "available"):
            raise ValueError("reason is null exactly when status is available")
        if not _exactly_one_null(self.reference_spread, self.reference_spread_reason):
            raise ValueError("exactly one of reference_spread and its reason must be null")
        if not _exactly_one_null(self.background_percentile, self.background_percentile_reason):
            raise ValueError("exactly one of background_percentile and its reason must be null")
        if self.reference_spread is None and self.tied is not None:
            raise ValueError("tied is null when no reference spread was supplied")
        return self


class ModelEmbeddingDistanceEntry(DistanceEntry):
    """The model_embedding distance: a value is a model prediction, so it is labelled
    Predicted."""

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                *_DISTANCE_RULES,
                _when({"status": {"const": "available"}}, {"evidence_label": _const("Predicted")}),
            ]
        }
    )

    @model_validator(mode="after")
    def _a_value_is_predicted(self) -> Self:
        if self.value is not None and self.evidence_label != "Predicted":
            raise ValueError("a model_embedding distance with a value is labelled Predicted")
        return self


class DistancesByFamily(Record):
    """The four family distances, every key always present (D07: never combined). Indexing by
    family name reads it like the ``dict[Family, DistanceEntry]`` of CONTRACTS a.4."""

    terrain_relief: DistanceEntry
    terrain_texture: DistanceEntry
    radar_cpr: DistanceEntry
    model_embedding: ModelEmbeddingDistanceEntry

    def __getitem__(self, family: Family) -> DistanceEntry:
        if family not in type(self).model_fields:
            raise KeyError(family)
        entry: DistanceEntry = getattr(self, family)
        return entry


class FamilySpread(Record):
    """The spread of one family's values across clones or within the window."""

    spread_min: Float | None
    spread_median: Float | None
    spread_max: Float | None
    n: Count | None


class Scenario(Record):
    """One named scenario run (PRD 6.6). It carries no probability or weight."""

    name: NonEmptyStr
    change: NonEmptyStr
    values_by_family: dict[Family, Float | None]
    rank_by_family: dict[Family, Count | None]


class RankFrequency(Record):
    """How often a rank held across runs: a frequency, never the probability that a site is a
    valid analog (PRD 6.6)."""

    frequency: Fraction
    count: Count
    n: PositiveCount


class UncertaintyComponent(Record):
    """One uncertainty category of PRD 6.6. Families stay separate; ``scenarios`` is used by
    scenario_sensitivity only."""

    status: Literal["available", "unavailable"]
    evidence_label: EvidenceLabel
    reason: ReasonCode | None
    note: NonEmptyStr | None
    by_family: dict[Family, FamilySpread] | None
    n_requested: Count | None
    n_used: Count | None
    scenarios: list[Scenario] | None
    rank_frequency: dict[Family, RankFrequency] | None


class UncertaintyComponents(Record):
    """The four uncertainty categories of PRD 6.6, kept separate and never combined."""

    heterogeneity: UncertaintyComponent
    measurement_error: UncertaintyComponent
    statistical_prediction_error: UncertaintyComponent
    scenario_sensitivity: UncertaintyComponent


class ExcludedObservable(Record):
    """An observable left out of the comparison, with every reason (PRD 6.5 step 1)."""

    observable: NonEmptyStr
    reasons: Annotated[list[ReasonCode], Field(min_length=1)]


class ComparisonRecord(Record):
    """A target window compared with one Earth window (PRD 8.12). Shortlist membership lives on
    the TargetRecord, and there is no score or combined distance (D07)."""

    target_window_id: WindowId
    earth_window_id: WindowId
    shipping_mode: ShippingMode | None
    comparable_observables: list[NonEmptyStr]
    excluded_observables_and_reasons: list[ExcludedObservable]
    distances_by_family: DistancesByFamily
    uncertainty_components: UncertaintyComponents
    sort_rule: NonEmptyStr | None
    claim_limits: list[NonEmptyStr]
    source_ids: list[NonEmptyStr]
    run_id: RunId


ComparisonsFile = Annotated[
    list[ComparisonRecord],
    Strict(),
    Field(
        title="Comparisons",
        description="comparisons/{target_id}.json: the ComparisonRecords (PRD 8.4).",
    ),
]
"""comparisons/{target_id}.json: an array of ComparisonRecords."""


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
    Contract("ProductRecord", "product-record.schema.json", TypeAdapter(ProductRecord)),
    Contract("Sources", "sources.schema.json", TypeAdapter(SourcesFile)),
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
