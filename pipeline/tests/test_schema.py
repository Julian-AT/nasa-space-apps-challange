"""Contract tests for the release bundle records (PRD 8.4, 8.12).

The PRD is the source of truth, so the manifest example (8.4) and the GateRecord field and status
lists (8.12) are read from PRD.md itself rather than copied here.
"""

from __future__ import annotations

import json
import re
import typing
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from terraluna_pipeline.schema import (
    SCHEMA_VERSION,
    BundleFile,
    BundleManifest,
    GateRecord,
    ModelSummary,
)

PRD = Path(__file__).resolve().parents[2] / "PRD.md"
HEX64 = "0123456789abcdef" * 4
GATE_IDS = [f"G{n}" for n in range(7)]
RECORDS: list[type[BaseModel]] = [BundleFile, ModelSummary, BundleManifest, GateRecord]


# --- PRD extraction ---------------------------------------------------------------------------


def prd_section(heading: str) -> str:
    text = PRD.read_text(encoding="utf-8")
    start = text.index(f"\n{heading}\n")
    end = text.find("\n#", start + len(heading) + 2)
    return text[start : end if end != -1 else len(text)]


def prd_manifest_example() -> dict[str, Any]:
    """The PRD 8.4 manifest example, with each placeholder '…' replaced by 64 hex characters."""
    block = re.search(r"```json\n(.*?)\n```", prd_section("### 8.4 Release bundle contract"), re.S)
    assert block is not None, "PRD 8.4 has no JSON manifest example"
    replaced = 0

    def concrete(value: Any) -> Any:
        nonlocal replaced
        if isinstance(value, dict):
            return {key: concrete(item) for key, item in value.items()}
        if isinstance(value, list):
            return [concrete(item) for item in value]
        if isinstance(value, str) and "…" in value:
            replaced += 1
            return value.replace("…", HEX64)
        return value

    example = concrete(json.loads(block.group(1)))
    assert replaced >= 2, "expected the run_id and file sha256 placeholders in the PRD example"
    assert isinstance(example, dict)
    return example


def prd_gate_record() -> tuple[list[str], list[str]]:
    """The GateRecord field names and status values listed in PRD 8.12."""
    line = next(
        line
        for line in prd_section("### 8.12 Records").splitlines()
        if line.startswith("- **GateRecord:**")
    )
    statuses = re.search(r"\(([^)]*)\)", line)
    assert statuses is not None
    fields = re.findall(r"`([^`]+)`", line.replace(statuses.group(0), ""))
    return fields, re.findall(r"`([^`]+)`", statuses.group(1))


def literal_values(annotation: object) -> set[object]:
    """The values of a Literal annotation, including Literals inside a union."""
    if typing.get_origin(annotation) is typing.Literal:
        return set(typing.get_args(annotation))
    values: set[object] = set()
    for arg in typing.get_args(annotation):
        values |= literal_values(arg)
    return values


# --- Helpers ------------------------------------------------------------------------------------


def gate(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "gate_id": "G0",
        "status": "NOT_RUN",
        "evidence_path": None,
        "run_id": None,
        "scope": None,
        "result": None,
        "consequence": None,
    }
    record.update(overrides)
    return record


def manifest(**overrides: Any) -> dict[str, Any]:
    record = prd_manifest_example()
    record.update(overrides)
    return record


def assert_accepted[M: BaseModel](model: type[M], value: Any) -> M:
    parsed = model.model_validate(value)
    assert model.model_validate_json(json.dumps(value)) == parsed
    return parsed


def assert_rejected(
    model: type[BaseModel], value: Any, loc: tuple[str | int, ...], error_type: str | None = None
) -> None:
    """Reject in both Python and JSON mode, with exactly one error at `loc`."""
    for mode in ("python", "json"):
        with pytest.raises(ValidationError) as excinfo:
            if mode == "python":
                model.model_validate(value)
            else:
                model.model_validate_json(json.dumps(value))
        errors = excinfo.value.errors()
        assert [error["loc"] for error in errors] == [loc], errors
        if error_type is not None:
            assert errors[0]["type"] == error_type, errors


# --- PRD conformance ----------------------------------------------------------------------------


def test_manifest_fields_are_exactly_the_prd_example_fields() -> None:
    example = prd_manifest_example()
    assert list(BundleManifest.model_fields) == list(example)
    assert list(ModelSummary.model_fields) == list(example["model"])
    assert list(BundleFile.model_fields) == list(example["files"][0])


def test_gate_record_fields_and_statuses_are_exactly_prd_8_12() -> None:
    fields, statuses = prd_gate_record()
    assert list(GateRecord.model_fields) == fields
    assert statuses == ["NOT_RUN", "PASS", "FAIL", "PARTIAL", "NOT_APPLICABLE"]
    assert literal_values(GateRecord.model_fields["status"].annotation) == set(statuses)
    assert literal_values(ModelSummary.model_fields["g5"].annotation) == set(statuses)


@pytest.mark.parametrize("model", RECORDS, ids=lambda model: model.__name__)
def test_every_field_is_required_without_default(model: type[BaseModel]) -> None:
    for name, field in model.model_fields.items():
        assert field.is_required(), f"{model.__name__}.{name} has a default"


def test_schema_version_is_semver() -> None:
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", SCHEMA_VERSION)


def test_schema_version_is_the_prd_example_version() -> None:
    # The models implement exactly the PRD 8.4 example's fields, so they carry its version too.
    assert prd_manifest_example()["schema_version"] == SCHEMA_VERSION


# --- BundleManifest -----------------------------------------------------------------------------


def test_prd_example_manifest_parses() -> None:
    example = prd_manifest_example()
    parsed = assert_accepted(BundleManifest, example)
    assert parsed.mock is False
    assert parsed.shipping_mode == "B"
    assert parsed.model == ModelSummary(id="terraluna-enc-v1", status="EXPERIMENTAL", g5="NOT_RUN")
    assert parsed.files == [
        BundleFile(path="comparisons/moon-cr-site01-w000.json", sha256=HEX64, bytes=412331)
    ]
    assert json.loads(parsed.model_dump_json()) == example


def test_manifest_without_mock_fails() -> None:
    value = manifest()
    del value["mock"]
    assert_rejected(BundleManifest, value, ("mock",), "missing")


@pytest.mark.parametrize("field", list(prd_manifest_example()))
def test_manifest_without_any_field_fails(field: str) -> None:
    value = manifest()
    del value[field]
    assert_rejected(BundleManifest, value, (field,), "missing")


def test_manifest_nullable_fields_stay_null_after_json_round_trip() -> None:
    value = manifest(
        run_id=None, git_sha=None, shipping_mode=None, model=None, targets=[], files=[]
    )
    parsed = assert_accepted(BundleManifest, value)
    dumped = json.loads(parsed.model_dump_json())
    for field in ("run_id", "git_sha", "shipping_mode", "model"):
        assert field in dumped
        assert dumped[field] is None
    assert BundleManifest.model_validate_json(parsed.model_dump_json()) == parsed


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("unexpected",), True),
        (("model", "unexpected"), True),
        (("files", 0, "unexpected"), True),
    ],
)
def test_manifest_unknown_field_fails(path: tuple[str | int, ...], value: object) -> None:
    record = manifest()
    target: Any = record
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert_rejected(BundleManifest, record, path, "extra_forbidden")


@pytest.mark.parametrize("mock", [True, False])
def test_mock_accepts_bools(mock: bool) -> None:
    assert_accepted(BundleManifest, manifest(mock=mock))


@pytest.mark.parametrize("mock", ["true", "false", 1, 0, None])
def test_mock_rejects_non_bools(mock: object) -> None:
    assert_rejected(BundleManifest, manifest(mock=mock), ("mock",))


@pytest.mark.parametrize("mode", ["A", "B", "C", None])
def test_shipping_mode_accepts_a_b_c_or_null(mode: str | None) -> None:
    assert_accepted(BundleManifest, manifest(shipping_mode=mode))


@pytest.mark.parametrize("mode", ["D", "a", "", "AB", 1])
def test_shipping_mode_rejects_anything_else(mode: object) -> None:
    assert_rejected(BundleManifest, manifest(shipping_mode=mode), ("shipping_mode",))


@pytest.mark.parametrize(
    "created", ["2026-11-15T09:00:00+00:00", "2026-11-15 09:00:00Z", "2026-13-15T09:00:00Z", ""]
)
def test_created_utc_must_be_utc_iso_8601(created: str) -> None:
    assert_rejected(BundleManifest, manifest(created_utc=created), ("created_utc",))


@pytest.mark.parametrize("version", ["2026-11-15T0900Z-3f9c2a1", "mock-0"])
def test_bundle_version_accepts_path_safe_names(version: str) -> None:
    assert_accepted(BundleManifest, manifest(bundle_version=version))


@pytest.mark.parametrize("version", ["", "..", "a/b", ".hidden", "a b"])
def test_bundle_version_rejects_unsafe_names(version: str) -> None:
    assert_rejected(BundleManifest, manifest(bundle_version=version), ("bundle_version",))


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", ""])
def test_schema_version_field_rejects_non_semver(version: str) -> None:
    assert_rejected(BundleManifest, manifest(schema_version=version), ("schema_version",))


@pytest.mark.parametrize("field", ["run_id", "git_sha"])
def test_optional_manifest_strings_reject_empty(field: str) -> None:
    assert_rejected(BundleManifest, manifest(**{field: ""}), (field,))


def test_target_ids_reject_empty() -> None:
    assert_rejected(BundleManifest, manifest(targets=[""]), ("targets", 0))


# --- BundleFile ---------------------------------------------------------------------------------


def bundle_file(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"path": "targets.json", "sha256": HEX64, "bytes": 0}
    record.update(overrides)
    return record


@pytest.mark.parametrize(
    "sha256",
    [
        HEX64.upper(),
        HEX64[:-1].upper() + "F",
        HEX64[:63],
        HEX64 + "0",
        HEX64 + "\n",
        "g" + HEX64[1:],
        "",
    ],
    ids=[
        "uppercase",
        "one-uppercase",
        "63-chars",
        "65-chars",
        "trailing-newline",
        "non-hex",
        "empty",
    ],
)
def test_sha256_rejects_non_canonical_digests(sha256: str) -> None:
    assert_rejected(BundleFile, bundle_file(sha256=sha256), ("sha256",))


@given(st.text(alphabet="0123456789abcdef", min_size=64, max_size=64))
def test_sha256_accepts_any_64_lowercase_hex(sha256: str) -> None:
    assert_accepted(BundleFile, bundle_file(sha256=sha256))


@given(st.text().filter(lambda s: re.fullmatch("[0-9a-f]{64}", s) is None))
def test_sha256_rejects_any_other_string(sha256: str) -> None:
    assert_rejected(BundleFile, bundle_file(sha256=sha256), ("sha256",))


@given(st.integers(min_value=0, max_value=2**53 - 1))
def test_bytes_accepts_non_negative_integers(size: int) -> None:
    assert_accepted(BundleFile, bundle_file(bytes=size))


@pytest.mark.parametrize("size", [-1, 1.0, True, "1", None])
def test_bytes_rejects_negative_or_non_integer(size: object) -> None:
    assert_rejected(BundleFile, bundle_file(bytes=size), ("bytes",))


@pytest.mark.parametrize(
    "path", ["targets.json", "comparisons/moon-cr-site01-w000.json", "tiles/hillshade/3/1/2.webp"]
)
def test_file_path_accepts_relative_bundle_paths(path: str) -> None:
    assert_accepted(BundleFile, bundle_file(path=path))


@pytest.mark.parametrize(
    "path", ["", "/etc/passwd", "../secret.json", "a/../b.json", "a//b.json", "a/", "a\\b.json"]
)
def test_file_path_rejects_absolute_escaping_or_malformed_paths(path: str) -> None:
    assert_rejected(BundleFile, bundle_file(path=path), ("path",))


@pytest.mark.parametrize("field", ["path", "sha256", "bytes"])
def test_bundle_file_without_any_field_fails(field: str) -> None:
    value = bundle_file()
    del value[field]
    assert_rejected(BundleFile, value, (field,), "missing")


# --- ModelSummary -------------------------------------------------------------------------------


def model_summary(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"id": "terraluna-enc-v1", "status": "EXPERIMENTAL", "g5": "NOT_RUN"}
    record.update(overrides)
    return record


@pytest.mark.parametrize("status", ["PROMOTED", "experimental", "", None])
def test_model_status_allows_only_experimental(status: object) -> None:
    assert_rejected(ModelSummary, model_summary(status=status), ("status",))


@pytest.mark.parametrize("g5", ["NOT_RUN", "PASS", "FAIL", "PARTIAL", "NOT_APPLICABLE"])
def test_model_g5_accepts_gate_statuses(g5: str) -> None:
    assert_accepted(ModelSummary, model_summary(g5=g5))


@pytest.mark.parametrize("g5", ["PASSED", "not_run", "", None])
def test_model_g5_rejects_anything_else(g5: object) -> None:
    assert_rejected(ModelSummary, model_summary(g5=g5), ("g5",))


def test_model_id_rejects_empty() -> None:
    assert_rejected(ModelSummary, model_summary(id=""), ("id",))


# --- GateRecord ---------------------------------------------------------------------------------


@pytest.mark.parametrize("gate_id", GATE_IDS)
def test_gate_id_accepts_g0_to_g6(gate_id: str) -> None:
    assert_accepted(GateRecord, gate(gate_id=gate_id))


@pytest.mark.parametrize("gate_id", ["G7", "g0", "G-1", "G", "", None])
def test_gate_id_rejects_anything_else(gate_id: object) -> None:
    assert_rejected(GateRecord, gate(gate_id=gate_id), ("gate_id",))


@pytest.mark.parametrize("status", ["NOT_RUN", "PASS", "FAIL", "PARTIAL", "NOT_APPLICABLE"])
def test_gate_status_accepts_prd_statuses(status: str) -> None:
    assert_accepted(GateRecord, gate(status=status))


@pytest.mark.parametrize("status", ["PASSED", "not_run", "", 0, None])
def test_gate_status_rejects_anything_else(status: object) -> None:
    assert_rejected(GateRecord, gate(status=status), ("status",))


def test_gate_record_without_status_fails() -> None:
    value = gate()
    del value["status"]
    assert_rejected(GateRecord, value, ("status",), "missing")


@pytest.mark.parametrize("field", list(gate()))
def test_gate_record_without_any_field_fails(field: str) -> None:
    value = gate()
    del value[field]
    assert_rejected(GateRecord, value, (field,), "missing")


def test_gate_record_unknown_field_fails() -> None:
    assert_rejected(GateRecord, gate(passed=True), ("passed",), "extra_forbidden")


def test_gate_result_null_stays_null_after_json_round_trip() -> None:
    record = GateRecord.model_validate(gate())
    dumped = record.model_dump_json()
    assert json.loads(dumped)["result"] is None
    restored = GateRecord.model_validate_json(dumped)
    assert restored.result is None
    assert restored == record
    for field in ("evidence_path", "run_id", "scope", "consequence"):
        assert json.loads(dumped)[field] is None


@pytest.mark.parametrize("field", ["evidence_path", "run_id", "scope", "result", "consequence"])
@pytest.mark.parametrize("missing", ["", 0])
def test_gate_optional_fields_reject_empty_or_zero_stand_ins(field: str, missing: object) -> None:
    assert_rejected(GateRecord, gate(**{field: missing}), (field,))


def test_executed_gate_record_round_trips() -> None:
    value = gate(
        gate_id="G1",
        status="PARTIAL",
        evidence_path="outputs/test/evidence.json",
        run_id="sha256:" + HEX64,
        scope="synthetic test scope",
        result="synthetic test result",
        consequence="synthetic test consequence",
    )
    parsed = assert_accepted(GateRecord, value)
    assert json.loads(parsed.model_dump_json()) == value
