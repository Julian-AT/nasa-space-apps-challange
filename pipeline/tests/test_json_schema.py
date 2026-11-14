"""Tests for the generated JSON Schemas and the shared schema cases (PRD 8.4, 8.9, D23).

The JSON Schemas in packages/schema/json/ are generated from schema.py and committed; the web's
zod schemas are generated from them in turn. fixtures/schema-cases/cases.json holds accept and
reject cases that Python and TypeScript both run, each with a verdict authored from PRD 8.4 and
8.12. Here every case must get that verdict from pydantic and from the generated JSON Schema.

JSON Schema 2020-12 defines ``pattern`` as an ECMA-262 regular expression, and the web evaluates
the patterns as JavaScript RegExps. The jsonschema package evaluates ``pattern`` with Python's
``re.search``, where ``$`` also matches just before a trailing newline, so it would accept
"<64 hex>\\n" as a SHA-256. The JSON Schema verdicts here therefore come from jsonschema's
Draft202012Validator with ``pattern`` evaluated with ECMA-262's ``$``. The translation covers the
regex subset the schemas use and raises for anything else, so a new construct cannot diverge
silently.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError, validators
from jsonschema.protocols import Validator
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from terraluna_pipeline.schema import (
    CONTRACTS,
    JS_MAX_SAFE_INTEGER,
    JSON_SCHEMA_DIALECT,
    BundleManifest,
    Contract,
    json_schema_differences,
    main,
)

ROOT = Path(__file__).resolve().parents[2]
PRD = ROOT / "PRD.md"
SCHEMA_DIR = ROOT / "packages" / "schema" / "json"
CASES = ROOT / "fixtures" / "schema-cases" / "cases.json"
MOCK_BUNDLE = ROOT / "fixtures" / "mock-bundle"
HEX64 = "0123456789abcdef" * 4
SCHEMA_FILES = {
    "BundleManifest": "bundle-manifest.schema.json",
    "GateRecord": "gate-record.schema.json",
    "Validation": "validation.schema.json",
}


# --- Strict JSON --------------------------------------------------------------------------------


def _refuse_constant(name: str) -> object:
    raise ValueError(f"{name} is not valid JSON")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    assert len(keys) == len(set(keys)), f"duplicate keys in {keys}"
    return dict(pairs)


def load_json(path: Path) -> Any:
    """Parse a fixture as strict JSON: UTF-8, no NaN or infinities, no duplicate keys."""
    return json.loads(
        path.read_bytes().decode("utf-8"),
        parse_constant=_refuse_constant,
        object_pairs_hook=_unique_keys,
    )


# --- ECMA-262 pattern oracle --------------------------------------------------------------------

_LITERAL_ESCAPES = set(".-/\\[](){}|*+?^$")


def ecma_to_python(pattern: str) -> str:
    """Python ``re`` source matching exactly what the ECMA-262 ``pattern`` matches (no flags).

    Only ``$`` differs within the supported subset: ECMA-262's matches at the end of the input,
    Python's also before a final newline, so it becomes ``\\Z``. Anything outside the subset
    raises ValueError: shorthand classes (``\\d``, ``\\w``, ``\\s``) and ``.`` differ in which
    characters they match, and ``(?``, negated or nested classes are not needed by the schemas.
    """
    out: list[str] = []
    in_class = False
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            escaped = pattern[i + 1 : i + 2]
            if escaped not in _LITERAL_ESCAPES:
                raise ValueError(f"unsupported escape \\{escaped} in {pattern!r}")
            out.append(char + escaped)
            i += 2
            continue
        if in_class:
            if char == "[":
                raise ValueError(f"nested class in {pattern!r}")
            in_class = char != "]"
        elif char == "[":
            if pattern[i + 1 : i + 2] in {"^", "]"}:
                raise ValueError(f"negated or empty class in {pattern!r}")
            in_class = True
        elif char == ".":
            raise ValueError(f"unescaped . in {pattern!r}")
        elif char == "(" and pattern[i + 1 : i + 2] == "?":
            raise ValueError(f"(? group in {pattern!r}")
        elif char == "$":
            if i != len(pattern) - 1:
                raise ValueError(f"$ before the end of {pattern!r}")
            out.append(r"\Z")
            i += 1
            continue
        out.append(char)
        i += 1
    if in_class:
        raise ValueError(f"unterminated class in {pattern!r}")
    return "".join(out)


def _ecma_pattern(
    validator: Validator, pattern: str, instance: Any, schema: object
) -> Iterator[ValidationError]:
    if validator.is_type(instance, "string") and not re.search(ecma_to_python(pattern), instance):
        yield ValidationError(f"{instance!r} does not match {pattern!r}")


# validators.extend is untyped in types-jsonschema.
EcmaValidator: type[Validator] = validators.extend(  # type: ignore[no-untyped-call]
    Draft202012Validator, {"pattern": _ecma_pattern}
)


def committed_schema(name: str) -> dict[str, Any]:
    schema = load_json(SCHEMA_DIR / SCHEMA_FILES[name])
    assert isinstance(schema, dict)
    return schema


def json_schema_accepts(name: str, value: object) -> bool:
    return bool(EcmaValidator(committed_schema(name)).is_valid(value))


# --- Shared cases -------------------------------------------------------------------------------


def load_cases() -> list[dict[str, Any]]:
    cases = load_json(CASES)
    assert isinstance(cases, list)
    return cases


def case_params() -> list[Any]:
    return [pytest.param(case, id=str(case.get("name"))) for case in load_cases()]


def cases_by_name() -> dict[str, dict[str, Any]]:
    return {case["name"]: case for case in load_cases()}


def adapter(name: str) -> TypeAdapter[Any]:
    return next(contract.adapter for contract in CONTRACTS if contract.name == name)


def _accepts(validate: Callable[[Any], object], value: object) -> bool:
    try:
        validate(value)
    except PydanticValidationError:
        return False
    return True


def pydantic_accepts(name: str, value: object) -> bool:
    """The pydantic verdict, which must be the same for the Python value and for its JSON text."""
    type_adapter = adapter(name)
    python_verdict = _accepts(type_adapter.validate_python, value)
    json_verdict = _accepts(type_adapter.validate_json, json.dumps(value))
    assert python_verdict == json_verdict, f"python {python_verdict}, JSON {json_verdict}"
    return python_verdict


def prd_manifest_example() -> dict[str, Any]:
    """The PRD 8.4 manifest example, with each placeholder '…' replaced by 64 hex characters."""
    text = PRD.read_text(encoding="utf-8")
    start = text.index("\n### 8.4 Release bundle contract\n")
    block = re.search(r"```json\n(.*?)\n```", text[start:], re.S)
    assert block is not None
    example = json.loads(block.group(1).replace("…", HEX64))
    assert isinstance(example, dict)
    return example


# --- Generation and drift -----------------------------------------------------------------------


def test_contracts_are_the_three_bundle_schemas() -> None:
    assert {contract.name: contract.file_name for contract in CONTRACTS} == SCHEMA_FILES


def test_committed_schemas_match_the_generator() -> None:
    assert json_schema_differences(SCHEMA_DIR) == []


@pytest.mark.parametrize("name", SCHEMA_FILES)
def test_committed_schema_is_json_schema_2020_12(name: str) -> None:
    schema = committed_schema(name)
    assert (
        schema["$schema"] == JSON_SCHEMA_DIALECT == "https://json-schema.org/draft/2020-12/schema"
    )
    assert next(iter(schema)) == "$schema"
    Draft202012Validator.check_schema(schema)
    assert validators.validator_for(schema) is Draft202012Validator


def test_generation_is_deterministic_across_processes(tmp_path: Path) -> None:
    for seed in ("0", "1", "4242"):
        out = tmp_path / f"seed-{seed}"
        subprocess.run(
            [sys.executable, "-m", "terraluna_pipeline.schema", "write", str(out)],
            check=True,
            capture_output=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert sorted(path.name for path in out.iterdir()) == sorted(SCHEMA_FILES.values())
        for file_name in SCHEMA_FILES.values():
            assert (out / file_name).read_bytes() == (SCHEMA_DIR / file_name).read_bytes()


def test_validation_schema_is_an_array_of_gate_records() -> None:
    validation = committed_schema("Validation")
    gate_record = committed_schema("GateRecord")
    assert validation["type"] == "array"
    assert validation["items"] == {"$ref": "#/$defs/GateRecord"}
    assert validation["$defs"] == {
        "GateRecord": {k: v for k, v in gate_record.items() if k != "$schema"}
    }


def test_byte_counts_stay_within_javascript_safe_integers() -> None:
    assert JS_MAX_SAFE_INTEGER == 2**53 - 1
    bundle_file = committed_schema("BundleManifest")["$defs"]["BundleFile"]
    assert bundle_file["properties"]["bytes"]["maximum"] == JS_MAX_SAFE_INTEGER


def test_a_model_with_one_extra_field_drifts_from_the_committed_schema() -> None:
    class ExtendedManifest(BundleManifest):
        extra_field: str

    extended = Contract(
        "BundleManifest", "bundle-manifest.schema.json", TypeAdapter(ExtendedManifest)
    )
    contracts = tuple(extended if c.name == "BundleManifest" else c for c in CONTRACTS)
    differences = json_schema_differences(SCHEMA_DIR, contracts)
    assert len(differences) == 1, differences
    assert differences[0].startswith("bundle-manifest.schema.json: differs")
    assert '+    "extra_field": {' in differences[0]
    assert '+    "extra_field"\n' in differences[0]


def copy_committed(tmp_path: Path) -> Path:
    copy = tmp_path / "json"
    shutil.copytree(SCHEMA_DIR, copy)
    return copy


def test_check_passes_on_an_unchanged_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check", str(copy_committed(tmp_path))]) == 0
    assert "match schema.py" in capsys.readouterr().out


def test_check_prints_a_diff_and_fails_when_a_committed_file_differs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copy = copy_committed(tmp_path)
    path = copy / "gate-record.schema.json"
    path.write_text(path.read_text("utf-8").replace('"G6"', '"G7"'), "utf-8")
    assert main(["check", str(copy)]) == 1
    captured = capsys.readouterr()
    assert f"--- {copy}/gate-record.schema.json" in captured.out
    assert "+++ generated/gate-record.schema.json" in captured.out
    assert re.search(r'^- +"G7"$', captured.out, re.M)
    assert re.search(r'^\+ +"G6"$', captured.out, re.M)
    assert "1 problem(s)" in captured.err


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("remove", "validation.schema.json: generated but missing from"),
        ("extra", "old.schema.json: in"),
        ("directory", "nested: not a regular file"),
        ("symlink", "validation.schema.json: not a regular file"),
    ],
)
def test_check_fails_on_missing_extra_or_irregular_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: str, expected: str
) -> None:
    copy = copy_committed(tmp_path)
    if change == "remove":
        (copy / "validation.schema.json").unlink()
    elif change == "extra":
        (copy / "old.schema.json").write_text("{}\n", "utf-8")
    elif change == "directory":
        (copy / "nested").mkdir()
    else:
        target = tmp_path / "elsewhere.json"
        shutil.copyfile(copy / "validation.schema.json", target)
        (copy / "validation.schema.json").unlink()
        (copy / "validation.schema.json").symlink_to(target)
    assert main(["check", str(copy)]) == 1
    assert expected in capsys.readouterr().out


def test_check_fails_when_the_directory_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check", str(tmp_path / "absent")]) == 1
    assert "not a directory" in capsys.readouterr().out


def test_write_creates_the_directory_and_overwrites_in_place(tmp_path: Path) -> None:
    out = tmp_path / "packages" / "schema" / "json"
    assert main(["write", str(out)]) == 0
    assert json_schema_differences(out) == []
    (out / "gate-record.schema.json").write_text("{}\n", "utf-8")
    (out / "keep.txt").write_text("not generated\n", "utf-8")
    assert main(["write", str(out)]) == 0
    assert (out / "keep.txt").read_text("utf-8") == "not generated\n"
    assert json_schema_differences(out) == ["keep.txt: in " + str(out) + " but not generated"]


def test_write_refuses_to_follow_a_symlink(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "json"
    out.mkdir()
    target = tmp_path / "outside.json"
    target.write_text("outside\n", "utf-8")
    (out / "gate-record.schema.json").symlink_to(target)
    assert main(["write", str(out)]) == 1
    assert "not a regular file" in capsys.readouterr().err
    assert target.read_text("utf-8") == "outside\n"


# --- Shared cases -------------------------------------------------------------------------------


def test_cases_are_well_formed() -> None:
    cases = load_cases()
    names = [case["name"] for case in cases]
    assert len(names) == len(set(names))
    for case in cases:
        assert isinstance(case, dict)
        assert list(case) == ["name", "schema", "valid", "value"], case
        assert isinstance(case["name"], str)
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", case["name"]), case["name"]
        assert case["schema"] in SCHEMA_FILES
        assert isinstance(case["valid"], bool)


@pytest.mark.parametrize("name", SCHEMA_FILES)
def test_every_schema_has_accepted_and_rejected_cases(name: str) -> None:
    verdicts = {case["valid"] for case in load_cases() if case["schema"] == name}
    assert verdicts == {True, False}


def _numbers(value: object) -> Iterator[int | float]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _numbers(item)
    elif isinstance(value, list):
        for item in value:
            yield from _numbers(item)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        yield value


def test_case_numbers_read_the_same_in_javascript() -> None:
    """JavaScript cannot tell 1.0 from 1, and rounds integers beyond 2**53, so neither appears."""
    for case in load_cases():
        for number in _numbers(case["value"]):
            if isinstance(number, float):
                assert not number.is_integer(), (case["name"], number)
            else:
                assert abs(number) <= 2**53, (case["name"], number)


def test_required_cases_test_what_they_name() -> None:
    cases = cases_by_name()
    real = cases["manifest-real-shaped"]
    assert (real["schema"], real["valid"]) == ("BundleManifest", True)
    assert real["value"] == prd_manifest_example()

    mock = cases["manifest-mock"]
    assert (mock["schema"], mock["valid"]) == ("BundleManifest", True)
    assert mock["value"] == load_json(MOCK_BUNDLE / "manifest.json")
    assert mock["value"]["mock"] is True

    missing_mock = cases["manifest-missing-mock"]
    assert missing_mock["valid"] is False
    assert missing_mock["value"] == {k: v for k, v in real["value"].items() if k != "mock"}

    unknown = cases["manifest-unknown-field"]
    assert unknown["valid"] is False
    extra = set(unknown["value"]) - set(real["value"])
    assert len(extra) == 1
    assert {k: v for k, v in unknown["value"].items() if k not in extra} == real["value"]

    bad_sha = cases["manifest-bad-sha256-uppercase"]
    assert bad_sha["valid"] is False
    files = bad_sha["value"]["files"]
    assert {k: v for k, v in bad_sha["value"].items() if k != "files"} == {
        k: v for k, v in real["value"].items() if k != "files"
    }
    assert files == [
        {**real["value"]["files"][0], "sha256": real["value"]["files"][0]["sha256"].upper()}
    ]

    gates = {name: case for name, case in cases.items() if case["schema"] == "GateRecord"}
    base = gates["gate-not-run"]["value"]
    assert gates["gate-not-run"]["valid"] is True
    assert gates["gate-bad-status"]["valid"] is False
    assert gates["gate-bad-status"]["value"] == {**base, "status": "PASSED"}
    assert gates["gate-missing-status"]["valid"] is False
    assert gates["gate-missing-status"]["value"] == {k: v for k, v in base.items() if k != "status"}
    assert gates["gate-result-null"]["valid"] is True
    assert set(gates["gate-result-null"]["value"]) == set(base)
    assert gates["gate-result-null"]["value"]["result"] is None

    validation = cases["validation-mock"]
    assert (validation["schema"], validation["valid"]) == ("Validation", True)
    assert validation["value"] == load_json(MOCK_BUNDLE / "validation.json")


@pytest.mark.parametrize("case", case_params())
def test_pydantic_gives_the_case_verdict(case: dict[str, Any]) -> None:
    assert pydantic_accepts(case["schema"], case["value"]) is case["valid"]


@pytest.mark.parametrize("case", case_params())
def test_generated_json_schema_gives_the_case_verdict(case: dict[str, Any]) -> None:
    assert json_schema_accepts(case["schema"], case["value"]) is case["valid"]


def test_mock_bundle_files_validate_against_the_generated_schemas() -> None:
    manifest = load_json(MOCK_BUNDLE / "manifest.json")
    validation = load_json(MOCK_BUNDLE / "validation.json")
    EcmaValidator(committed_schema("BundleManifest")).validate(manifest)
    EcmaValidator(committed_schema("Validation")).validate(validation)
    for record in validation:
        EcmaValidator(committed_schema("GateRecord")).validate(record)


# --- The oracle ---------------------------------------------------------------------------------


def test_stock_jsonschema_lets_dollar_match_before_a_trailing_newline() -> None:
    """Why the oracle exists: stock jsonschema would accept a digest with a trailing newline."""
    sha256 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    assert Draft202012Validator(sha256).is_valid(HEX64 + "\n")
    assert not EcmaValidator(sha256).is_valid(HEX64 + "\n")
    assert EcmaValidator(sha256).is_valid(HEX64)


@pytest.mark.parametrize(
    "pattern",
    [r"^\d+$", r"^a.b$", r"^a$|^b$", r"^(?=a)a$", r"^[^a]$", r"^[]a]$", r"^[a\w]$", r"^[a"],
)
def test_oracle_refuses_patterns_outside_its_subset(pattern: str) -> None:
    with pytest.raises(ValueError):
        ecma_to_python(pattern)


def test_oracle_translates_every_pattern_in_the_schemas() -> None:
    def patterns(value: object) -> Iterator[str]:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "pattern":
                    assert isinstance(item, str)
                    yield item
                else:
                    yield from patterns(item)
        elif isinstance(value, list):
            for item in value:
                yield from patterns(item)

    found = [p for name in SCHEMA_FILES for p in patterns(committed_schema(name))]
    assert len(found) >= 5
    for pattern in found:
        assert ecma_to_python(pattern).endswith(r"\Z")
