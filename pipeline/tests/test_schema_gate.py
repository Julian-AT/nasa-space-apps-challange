"""GateRecord provenance fields (T0B-05 option A; PRD 8.12 as amended by PRD-01, PRD 9).

A gate that was run (PASS, FAIL, PARTIAL or NOT_APPLICABLE) names the command that produced it,
the sha256 of config/run.yaml as read and the sha256 of every file it read, so `gates verify`
can re-hash the inputs offline. PASS also needs the PRD 8.13 run_id. NOT_RUN may leave every
nullable field null and list no inputs. The same rules hold in pydantic, in the generated JSON
Schema and in the generated zod (fixtures/schema-cases/cases.json runs in all three).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from terraluna_pipeline.schema import GateRecord, InputHash

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "packages" / "schema" / "json"
ZOD_GATE = ROOT / "packages" / "schema" / "src" / "generated" / "gate-record.ts"
HEX64 = "0123456789abcdef" * 4
OTHER_HEX64 = "fedcba9876543210" * 4
RUN_ID = "sha256:" + HEX64
EXECUTED = ("PASS", "FAIL", "PARTIAL", "NOT_APPLICABLE")
NULLABLE = (
    "evidence_path",
    "run_id",
    "scope",
    "result",
    "consequence",
    "command",
    "config_version",
)


def not_run(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "gate_id": "G0",
        "status": "NOT_RUN",
        "evidence_path": None,
        "run_id": None,
        "scope": None,
        "result": None,
        "consequence": None,
        "command": None,
        "config_version": None,
        "input_hashes": [],
    }
    record.update(overrides)
    return record


def executed(status: str = "PASS", **overrides: Any) -> dict[str, Any]:
    record = not_run(
        gate_id="G1",
        status=status,
        evidence_path="outputs/gates/G1-evidence.json",
        run_id=RUN_ID,
        scope="synthetic test scope",
        result="synthetic test result",
        consequence=None if status == "PASS" else "synthetic test consequence",
        command="python -m terraluna_pipeline.synthetic record --out outputs/gates/G1.json",
        config_version=OTHER_HEX64,
        input_hashes=[
            {"path": "config/run.yaml", "sha256": OTHER_HEX64},
            {"path": "fixtures/synthetic/input.json", "sha256": HEX64},
        ],
    )
    record.update(overrides)
    return record


def json_schema() -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / "gate-record.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def accepted(value: dict[str, Any]) -> bool:
    try:
        GateRecord.model_validate(value)
        GateRecord.model_validate_json(json.dumps(value))
    except ValidationError:
        return False
    return True


def assert_verdict(value: dict[str, Any], valid: bool) -> None:
    assert accepted(value) is valid
    assert json_schema().is_valid(value) is valid


# --- Fields -------------------------------------------------------------------------------------


def test_gate_record_has_the_amended_prd_8_12_fields_in_order() -> None:
    assert list(GateRecord.model_fields) == [
        "gate_id",
        "status",
        "evidence_path",
        "run_id",
        "scope",
        "result",
        "consequence",
        "command",
        "config_version",
        "input_hashes",
    ]
    assert list(InputHash.model_fields) == ["path", "sha256"]


def test_there_are_no_wall_clock_fields() -> None:
    names = [*GateRecord.model_fields, *InputHash.model_fields]
    for word in ("time", "utc", "date", "created", "timestamp", "at"):
        assert not [name for name in names if word in name.split("_")], word


def test_a_t0_shaped_seven_field_record_fails() -> None:
    value = not_run()
    for key in ("command", "config_version", "input_hashes"):
        del value[key]
    assert_verdict(value, False)


@pytest.mark.parametrize("field", ["command", "config_version", "input_hashes"])
def test_each_new_field_is_required(field: str) -> None:
    value = not_run()
    del value[field]
    assert_verdict(value, False)


# --- NOT_RUN ------------------------------------------------------------------------------------


def test_not_run_with_every_nullable_field_null_passes_and_round_trips() -> None:
    value = not_run()
    assert_verdict(value, True)
    record = GateRecord.model_validate(value)
    dumped = json.loads(record.model_dump_json())
    assert dumped == value
    for field in NULLABLE:
        assert dumped[field] is None
    assert dumped["input_hashes"] == []
    assert GateRecord.model_validate_json(record.model_dump_json()) == record


# --- Executed statuses --------------------------------------------------------------------------


@pytest.mark.parametrize("status", EXECUTED)
def test_an_executed_record_with_its_provenance_passes(status: str) -> None:
    assert_verdict(executed(status), True)


def test_a_pass_without_run_id_fails() -> None:
    assert_verdict(executed("PASS", run_id=None), False)


@pytest.mark.parametrize("status", ["FAIL", "PARTIAL", "NOT_APPLICABLE"])
def test_only_pass_requires_a_run_id(status: str) -> None:
    assert_verdict(executed(status, run_id=None), True)


def test_a_fail_without_command_fails() -> None:
    assert_verdict(executed("FAIL", command=None), False)


def test_a_partial_with_empty_input_hashes_fails() -> None:
    assert_verdict(executed("PARTIAL", input_hashes=[]), False)


@pytest.mark.parametrize("status", EXECUTED)
@pytest.mark.parametrize("field", ["command", "config_version", "evidence_path", "scope"])
def test_an_executed_record_needs_each_provenance_field(status: str, field: str) -> None:
    assert_verdict(executed(status, **{field: None}), False)


@pytest.mark.parametrize("status", EXECUTED)
def test_an_executed_record_needs_input_hashes(status: str) -> None:
    assert_verdict(executed(status, input_hashes=[]), False)


# --- Field formats ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", ""),
        ("config_version", HEX64.upper()),
        ("config_version", "sha256:" + HEX64),
        ("evidence_path", "/tmp/evidence.json"),
        ("evidence_path", "../outputs/evidence.json"),
        ("run_id", "run-2026-11-14-a"),
        ("run_id", HEX64),
    ],
)
def test_malformed_provenance_values_fail(field: str, value: str) -> None:
    assert_verdict(executed("PASS", **{field: value}), False)


@pytest.mark.parametrize(
    "entry",
    [
        {"path": "/abs/path.json", "sha256": HEX64},
        {"path": "outputs/x.json", "sha256": HEX64[:-1]},
        {"path": "outputs/x.json"},
        {"path": "outputs/x.json", "sha256": HEX64, "bytes": 3},
    ],
)
def test_malformed_input_hash_entries_fail(entry: dict[str, Any]) -> None:
    assert_verdict(executed("PASS", input_hashes=[entry]), False)


def test_unsorted_input_hashes_fail() -> None:
    hashes = executed()["input_hashes"]
    assert not accepted(executed("PASS", input_hashes=list(reversed(hashes))))


def test_a_path_listed_twice_in_input_hashes_fails() -> None:
    entry = {"path": "fixtures/synthetic/input.json", "sha256": HEX64}
    assert not accepted(executed("PASS", input_hashes=[entry, entry]))
    assert not accepted(executed("PASS", input_hashes=[entry, {**entry, "sha256": OTHER_HEX64}]))


def test_not_run_input_hashes_must_also_be_sorted() -> None:
    hashes = executed()["input_hashes"]
    assert not accepted(not_run(input_hashes=list(reversed(hashes))))


# --- Generated JSON Schema and zod --------------------------------------------------------------


def test_json_schema_mirrors_the_conditional_rules() -> None:
    schema = json.loads((SCHEMA_DIR / "gate-record.schema.json").read_text(encoding="utf-8"))
    rules = {rule["if"]["properties"]["status"]["const"]: rule["then"] for rule in schema["allOf"]}
    assert set(rules) == set(EXECUTED)
    for status in EXECUTED:
        constrained = rules[status]["properties"]
        assert set(constrained) >= {
            "command",
            "config_version",
            "evidence_path",
            "scope",
            "input_hashes",
        }
        assert constrained["input_hashes"]["minItems"] == 1
        assert ("run_id" in constrained) is (status == "PASS")


def test_generated_zod_carries_the_new_fields_and_rules() -> None:
    source = ZOD_GATE.read_text(encoding="utf-8")
    for field in ("command", "config_version", "input_hashes"):
        assert f"{field}:" in source or f'"{field}"' in source, field
    for status in EXECUTED:
        assert f"'{status}'" in source or f'"{status}"' in source, status
