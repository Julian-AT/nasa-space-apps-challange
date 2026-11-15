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

import copy
import itertools
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
from hypothesis import given
from hypothesis import strategies as st
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
    "ProductRecord": "product-record.schema.json",
    "Sources": "sources.schema.json",
    "WindowRecord": "window-record.schema.json",
    "FingerprintRecord": "fingerprint-record.schema.json",
    "Windows": "windows.schema.json",
    "ComparisonRecord": "comparison-record.schema.json",
    "Comparisons": "comparisons.schema.json",
}
GENERATOR_CASES = ROOT / "fixtures" / "schema-cases" / "generator-cases.json"
"""Synthetic schemas with ``if``/``then`` and ``not``, and documents with authored verdicts. The
zod generator's tests (packages/schema) translate the same schemas; here the verdicts must also
be what Draft 2020-12 gives, so the two languages check one set of verdicts."""


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


def json_schema_accepts(name: str, value: Any) -> bool:
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


def test_contracts_are_the_bundle_schemas() -> None:
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


def test_sources_schema_is_an_array_of_product_records() -> None:
    sources = committed_schema("Sources")
    product_record = committed_schema("ProductRecord")
    assert sources["type"] == "array"
    assert sources["items"] == {"$ref": "#/$defs/ProductRecord"}
    assert sources["$defs"] == {
        "ProductRecord": {k: v for k, v in product_record.items() if k != "$schema"}
    }


@pytest.mark.parametrize(
    ("array", "record"), [("Windows", "WindowEntry"), ("Comparisons", "ComparisonRecord")]
)
def test_bundle_arrays_are_arrays_of_their_records(array: str, record: str) -> None:
    schema = committed_schema(array)
    assert schema["type"] == "array"
    assert schema["items"] == {"$ref": f"#/$defs/{record}"}
    assert record in schema["$defs"]


@pytest.mark.parametrize("name", ["WindowRecord", "FingerprintRecord", "ComparisonRecord"])
def test_record_schemas_are_the_definitions_used_in_the_bundle_arrays(name: str) -> None:
    array = "Comparisons" if name == "ComparisonRecord" else "Windows"
    record = committed_schema(name)
    definitions = committed_schema(array)["$defs"]
    assert definitions[name] == {k: v for k, v in record.items() if k not in ("$schema", "$defs")}
    for key, definition in record.get("$defs", {}).items():
        assert definitions[key] == definition


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


def test_product_record_cases_test_what_they_name() -> None:
    """Each ProductRecord or Sources case differs from product-verified only as its name says."""
    cases = cases_by_name()
    verified = cases["product-verified"]
    assert (verified["schema"], verified["valid"]) == ("ProductRecord", True)
    base = verified["value"]
    assert base["verification_status"] == "VERIFIED"
    assert (base["checksum"], base["units"]) == (HEX64, "m")
    assert base["local_path"] is not None

    def changes(name: str) -> dict[str, Any]:
        value = cases[name]["value"]
        assert cases[name]["schema"] == "ProductRecord"
        assert set(value) == set(base), name
        return {key: item for key, item in value.items() if item != base[key]}

    nothing_read = {"local_path": None, "checksum": None}
    expected = {
        "product-not-checked-nothing-read": {**nothing_read, "verification_status": "NOT_CHECKED"},
        "product-partial-units-unknown": {"units": "unknown", "verification_status": "PARTIAL"},
        "product-failed-units-unknown-uppercase": {
            "units": "UNKNOWN",
            "verification_status": "FAILED",
        },
        "product-verified-local-path-null": {"local_path": None},
        "product-verified-checksum-null": {"checksum": None},
        "product-verified-units-unknown": {"units": "unknown"},
        "product-verified-units-unknown-capitalised": {"units": "Unknown"},
        "product-verified-units-unknown-uppercase": {"units": "UNKNOWN"},
        "product-verified-units-unknown-trailing-newline": {"units": "unknown\n"},
        "product-checksum-63-chars": {"checksum": HEX64[:63]},
        "product-checksum-uppercase": {"checksum": HEX64.upper()},
        "product-provider-empty-string": {"provider": ""},
        "product-units-zero": {"units": 0},
    }
    for name, change in expected.items():
        assert changes(name) == change, name

    assert cases["product-missing-units"]["value"] == {
        key: item for key, item in base.items() if key != "units"
    }
    unknown = cases["product-unknown-field"]["value"]
    assert len(set(unknown) - set(base)) == 1
    assert {key: item for key, item in unknown.items() if key in base} == base

    assert cases["sources-empty"]["value"] == []
    assert cases["sources-one-record"]["value"] == [base]
    assert cases["sources-one-invalid-entry"]["value"] == [
        base,
        cases["product-verified-checksum-null"]["value"],
    ]
    assert cases["sources-object"]["value"] == base
    for name in ("sources-empty", "sources-one-record", "sources-two-records"):
        assert (cases[name]["schema"], cases[name]["valid"]) == ("Sources", True)


def leaf_changes(before: Any, after: Any, path: tuple[Any, ...] = ()) -> Iterator[tuple[Any, ...]]:
    """The paths at which ``after`` differs from ``before``: a changed, added or removed leaf."""
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after), key=str):
            if key not in before or key not in after:
                yield (*path, key)
            else:
                yield from leaf_changes(before[key], after[key], (*path, key))
    elif isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        for index, (old, new) in enumerate(zip(before, after, strict=True)):
            yield from leaf_changes(old, new, (*path, index))
    elif before != after or type(before) is not type(after):
        yield path


DISTANCES = ("distances_by_family",)
UNCERTAINTY = ("uncertainty_components",)

# Each science case and the base case it differs from, with the paths at which it differs.
SCIENCE_CASE_CHANGES: dict[str, tuple[str, set[tuple[Any, ...]]]] = {
    "window-record-missing-environment-flags": ("window-record-moon", {("environment_flags",)}),
    "window-record-unknown-field": ("window-record-moon", {("score",)}),
    "window-record-area-zero": ("window-record-moon", {("area_m2",)}),
    "window-record-valid-fraction-above-one": (
        "window-record-moon",
        {("valid_fraction_by_observable", "terrain")},
    ),
    "window-record-empty-observable-key": (
        "window-record-moon",
        {("valid_fraction_by_observable", "")},
    ),
    "window-record-evidence-label-unknown": (
        "window-record-moon",
        {("evidence_status_by_field", "radar_cpr")},
    ),
    "window-record-ring-three-positions": ("window-record-moon", {("polygon", "ring_m")}),
    "window-record-position-three-numbers": (
        "window-record-moon",
        {("polygon", "ring_lonlat", 1)},
    ),
    "window-record-frame-unknown": ("window-record-moon", {("polygon", "frame")}),
    "window-record-acquisition-date-not-iso": (
        "window-record-earth-utm",
        {("environment_flags", "acquisition_dates", 0)},
    ),
    "window-record-shadow-fraction-above-one": (
        "window-record-moon",
        {("environment_flags", "permanent_shadow_fraction")},
    ),
    "window-record-window-id-uppercase": ("window-record-moon", {("window_id",)}),
    "fingerprint-terrain-with-radar-set": ("fingerprint-terrain", {("radar",)}),
    "fingerprint-radar-with-terrain-set": ("fingerprint-radar", {("terrain",)}),
    "fingerprint-terrain-without-terrain": ("fingerprint-terrain", {("terrain",)}),
    "fingerprint-radar-holding-terrain": ("fingerprint-radar", {("terrain",), ("radar",)}),
    "fingerprint-blocks-independent-true": (
        "fingerprint-radar",
        {("radar", "blocks_independent")},
    ),
    "fingerprint-blocks-independent-zero": (
        "fingerprint-radar",
        {("radar", "blocks_independent")},
    ),
    "fingerprint-hurst-unavailable-with-reason": (
        "fingerprint-terrain",
        {("terrain", "relief", "hurst"), ("terrain", "relief", "hurst_reason")},
    ),
    "fingerprint-hurst-and-reason-null": ("fingerprint-terrain", {("terrain", "relief", "hurst")}),
    "fingerprint-hurst-and-reason-set": (
        "fingerprint-terrain",
        {("terrain", "texture", "hurst_reason")},
    ),
    "fingerprint-nu-zero": ("fingerprint-terrain", {("terrain", "relief", "nu_m", "270")}),
    "fingerprint-nu-negative": ("fingerprint-terrain", {("terrain", "relief", "nu_m", "270")}),
    "fingerprint-distribution-empty": ("fingerprint-radar", {("radar", "sc")}),
    "fingerprint-distribution-empty-with-statistic": ("fingerprint-radar", {("radar", "sc")}),
    "fingerprint-distribution-with-null-statistic": (
        "fingerprint-radar",
        {("radar", "cpr_ratio_of_mean_powers", "p95")},
    ),
    "fingerprint-distribution-n-zero-with-values": ("fingerprint-radar", {("radar", "sc")}),
    "fingerprint-run-id-without-prefix": ("fingerprint-terrain", {("run_id",)}),
    "fingerprint-aggregation-pixelwise": ("fingerprint-radar", {("radar", "aggregation")}),
    "comparison-mode-b": ("comparison-terrain-only", {("shipping_mode",)}),
    "comparison-real-zero-distance": (
        "comparison-terrain-only",
        {(*DISTANCES, "terrain_relief", "value")},
    ),
    "comparison-missing-model-embedding": (
        "comparison-terrain-only",
        {(*DISTANCES, "model_embedding")},
    ),
    "comparison-five-families": ("comparison-terrain-only", {(*DISTANCES, "radar_sc")}),
    "comparison-score-field": ("comparison-terrain-only", {("score",)}),
    "comparison-distance-score-field": (
        "comparison-terrain-only",
        {(*DISTANCES, "terrain_relief", "score")},
    ),
    "comparison-distance-null-without-reason": (
        "comparison-terrain-only",
        {(*DISTANCES, "radar_cpr", "reason")},
    ),
    "comparison-distance-value-with-reason": (
        "comparison-terrain-only",
        {(*DISTANCES, "terrain_relief", "reason")},
    ),
    "comparison-distance-value-not-available": (
        "comparison-terrain-only",
        {(*DISTANCES, "terrain_relief", "status"), (*DISTANCES, "terrain_relief", "reason")},
    ),
    "comparison-distance-available-without-value": (
        "comparison-terrain-only",
        {(*DISTANCES, "radar_cpr", "status"), (*DISTANCES, "radar_cpr", "reason")},
    ),
    "comparison-distance-negative": (
        "comparison-terrain-only",
        {(*DISTANCES, "terrain_relief", "value")},
    ),
    "comparison-spread-and-reason-null": (
        "comparison-terrain-only",
        {
            (*DISTANCES, "terrain_relief", "reference_spread"),
            (*DISTANCES, "terrain_relief", "tied"),
        },
    ),
    "comparison-spread-and-reason-set": (
        "comparison-terrain-only",
        {(*DISTANCES, "terrain_relief", "reference_spread_reason")},
    ),
    "comparison-percentile-and-reason-null": (
        "comparison-terrain-only",
        {(*DISTANCES, "terrain_relief", "background_percentile")},
    ),
    "comparison-percentile-and-reason-set": (
        "comparison-terrain-only",
        {(*DISTANCES, "radar_cpr", "background_percentile")},
    ),
    "comparison-tied-without-spread": (
        "comparison-terrain-only",
        {
            (*DISTANCES, "terrain_relief", "reference_spread"),
            (*DISTANCES, "terrain_relief", "reference_spread_reason"),
        },
    ),
    "comparison-uncertainty-missing-component": (
        "comparison-terrain-only",
        {(*UNCERTAINTY, "scenario_sensitivity")},
    ),
    "comparison-uncertainty-fifth-component": (
        "comparison-terrain-only",
        {(*UNCERTAINTY, "combined")},
    ),
    "comparison-uncertainty-unknown-family": (
        "comparison-terrain-only",
        {(*UNCERTAINTY, "measurement_error", "by_family", "terrain")},
    ),
    "comparison-scenario-probability": (
        "comparison-terrain-only",
        {(*UNCERTAINTY, "scenario_sensitivity", "scenarios", 0, "probability")},
    ),
    "comparison-rank-frequency-above-one": (
        "comparison-terrain-only",
        {(*UNCERTAINTY, "measurement_error", "rank_frequency", "terrain_relief", "frequency")},
    ),
    "comparison-excluded-without-reasons": (
        "comparison-terrain-only",
        {("excluded_observables_and_reasons", 0, "reasons")},
    ),
    "comparison-run-id-uppercase": ("comparison-terrain-only", {("run_id",)}),
}


def test_science_cases_test_what_they_name() -> None:
    """Each WindowRecord, FingerprintRecord or ComparisonRecord case differs from its base only
    at the paths its entry names; the array cases are built from those records."""
    cases = cases_by_name()
    for name, (base_name, paths) in SCIENCE_CASE_CHANGES.items():
        case, base = cases[name], cases[base_name]
        assert case["schema"] == base["schema"], name
        assert base["valid"] is True, base_name
        assert set(leaf_changes(base["value"], case["value"])) == paths, name
    # The model_embedding entry of these two is the available terrain_relief entry with another
    # unit and value, labelled Predicted in one and left Derived in the other.
    for name, differs in (
        ("comparison-model-embedding-predicted-value", {"unit", "value", "evidence_label"}),
        ("comparison-model-embedding-derived-value", {"unit", "value"}),
    ):
        embedding = cases[name]["value"]["distances_by_family"]["model_embedding"]
        relief = cases["comparison-terrain-only"]["value"]["distances_by_family"]["terrain_relief"]
        assert {key for key in embedding if embedding[key] != relief[key]} == differs, name
        assert embedding["evidence_label"] == (
            "Predicted" if name.endswith("predicted-value") else "Derived"
        )
        changes = set(leaf_changes(cases["comparison-terrain-only"]["value"], cases[name]["value"]))
        assert changes, name
        assert all(path[:2] == (*DISTANCES, "model_embedding") for path in changes), changes
    assert cases["comparison-model-embedding-predicted-value"]["valid"] is True
    assert cases["comparison-model-embedding-derived-value"]["valid"] is False

    entry = cases["windows-one-entry"]["value"][0]
    assert entry["window"] == cases["window-record-moon"]["value"]
    assert entry["terrain"] == cases["fingerprint-terrain"]["value"]
    assert entry["radar"] == [cases["fingerprint-radar"]["value"]]
    assert cases["windows-empty"]["value"] == []
    assert cases["windows-object"]["value"] == entry
    assert cases["comparisons-empty"]["value"] == []
    assert cases["comparisons-one-record"]["value"] == [cases["comparison-terrain-only"]["value"]]
    assert cases["comparisons-one-invalid-entry"]["value"] == [
        cases["comparison-terrain-only"]["value"],
        cases["comparison-distance-null-without-reason"]["value"],
    ]
    assert cases["comparisons-object"]["value"] == cases["comparison-terrain-only"]["value"]
    # Every single-record case is a base, has an entry above, or is one of the two embedding cases.
    singles = {
        name
        for name, case in cases.items()
        if case["schema"] in ("WindowRecord", "FingerprintRecord", "ComparisonRecord")
    }
    bases = {base for base, _ in SCIENCE_CASE_CHANGES.values()}
    assert singles - set(SCIENCE_CASE_CHANGES) - bases == {
        "comparison-model-embedding-predicted-value",
        "comparison-model-embedding-derived-value",
    }


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

    def anchored_patterns(value: object) -> Iterator[str]:
        """Patterns a value must match in full; those under ``not`` are searched for instead."""
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "pattern":
                    assert isinstance(item, str)
                    yield item
                elif key != "not":
                    yield from anchored_patterns(item)
        elif isinstance(value, list):
            for item in value:
                yield from anchored_patterns(item)

    found = [p for name in SCHEMA_FILES for p in patterns(committed_schema(name))]
    assert len(found) >= 5
    for pattern in found:
        ecma_to_python(pattern)
    anchored = [p for name in SCHEMA_FILES for p in anchored_patterns(committed_schema(name))]
    assert len(anchored) >= 5
    for pattern in anchored:
        assert pattern.startswith("^")
        assert ecma_to_python(pattern).endswith(r"\Z")


# --- Generator cases ----------------------------------------------------------------------------


def load_generator_cases() -> list[dict[str, Any]]:
    groups = load_json(GENERATOR_CASES)
    assert isinstance(groups, list)
    return groups


def generator_case_params() -> list[Any]:
    return [
        pytest.param(
            group["schema"], case["value"], case["valid"], id=f"{group['name']}/{case['name']}"
        )
        for group in load_generator_cases()
        for case in group["cases"]
    ]


def condition_case_params() -> list[Any]:
    return [
        pytest.param(
            group["schema"]["if"],
            case["value"],
            case["holds"],
            id=f"{group['name']}/{case['name']}",
        )
        for group in load_generator_cases()
        for case in group.get("conditionCases", [])
    ]


def test_generator_cases_are_well_formed() -> None:
    groups = load_generator_cases()
    group_names = [group["name"] for group in groups]
    assert len(group_names) == len(set(group_names))
    assert not set(group_names) & {name.lower() for name in SCHEMA_FILES}
    for group in groups:
        assert list(group) in (
            ["name", "schema", "cases"],
            ["name", "schema", "cases", "conditionCases"],
        )
        Draft202012Validator.check_schema(group["schema"])
        names = [case["name"] for case in group["cases"]]
        assert len(names) == len(set(names)), group["name"]
        assert {case["valid"] for case in group["cases"]} == {True, False}, group["name"]
        for case in group["cases"]:
            assert list(case) == ["name", "valid", "value"]
            assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", case["name"]), case["name"]
    conditional = next(group["schema"] for group in groups if group["name"] == "conditional")
    assert {"if", "then"} <= set(conditional)
    assert "else" not in conditional


def test_every_top_level_condition_has_condition_cases_with_an_absent_property() -> None:
    # Every object here requires all of its properties, so a document without an `if` property
    # is rejected whatever the condition does. The condition cases test the `if` schema alone,
    # on objects as the zod refinement sees them, so an absent property has an observable verdict.
    groups = [group for group in load_generator_cases() if "if" in group["schema"]]
    assert [group["name"] for group in groups] == [
        "conditional",
        "conditional-two-keys",
        "conditional-else",
        "conditional-null",
        "conditional-integer",
    ]
    for group in groups:
        assert "conditionCases" in group, group["name"]
        condition = group["schema"]["if"]
        cases = group["conditionCases"]
        names = [case["name"] for case in cases]
        assert len(names) == len(set(names)), group["name"]
        assert {case["holds"] for case in cases} == {True, False}, group["name"]
        for case in cases:
            assert list(case) == ["name", "holds", "value"]
            assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", case["name"]), case["name"]
            assert isinstance(case["value"], dict), case["name"]
        for key in condition["required"]:
            absent = [case for case in cases if key not in case["value"]]
            assert absent, f"{group['name']}: no condition case without {key}"
            assert all(case["holds"] is False for case in absent)


@pytest.mark.parametrize(("schema", "value", "valid"), generator_case_params())
def test_generator_cases_get_their_verdict_from_json_schema(
    schema: dict[str, Any], value: Any, valid: bool
) -> None:
    assert EcmaValidator(schema).is_valid(value) is valid


@pytest.mark.parametrize(("condition", "value", "holds"), condition_case_params())
def test_condition_cases_get_their_verdict_from_the_if_schema(
    condition: dict[str, Any], value: dict[str, Any], holds: bool
) -> None:
    assert EcmaValidator(condition).is_valid(value) is holds


# --- ProductRecord parity (PRD 8.12) ------------------------------------------------------------
# The shared cases in fixtures/schema-cases/cases.json cover ProductRecord and Sources in every
# language, zod included. These local cases add to them: each must get its verdict from pydantic
# and from the generated JSON Schema, alone and inside a Sources array. Verdicts come from PRD
# 8.12 and the T1-01 contract, not from either validator.


def product_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "product_id": "example-product",
        "provider": "Example provider",
        "version": "v1",
        "source_url": "https://data.example.org/products/example/dem.tif",
        "local_path": "data/raw/example/dem.tif",
        "checksum": HEX64,
        "body": "moon",
        "acquisition_time": "unknown",
        "frequency_or_wavelength": None,
        "transmit_receive_convention": None,
        "units": "m",
        "calibration_type": None,
        "native_grid": "width=4 height=3",
        "effective_resolution": "unknown",
        "crs_and_frame": "unknown",
        "incidence_source": None,
        "qa_definition": "unknown",
        "license_or_attribution": "unknown",
        "verification_status": "VERIFIED",
    }
    record.update(overrides)
    return record


def _without(field_name: str) -> dict[str, Any]:
    return {k: v for k, v in product_record().items() if k != field_name}


PRODUCT_RECORD_CASES: list[tuple[str, dict[str, Any], bool]] = [
    ("verified", product_record(), True),
    (
        "blocked-nothing-read",
        product_record(
            source_url=None,
            local_path=None,
            checksum=None,
            units="unknown",
            verification_status="BLOCKED",
        ),
        True,
    ),
    ("partial-unknown-units", product_record(units="unknown", verification_status="PARTIAL"), True),
    (
        "query-string-without-credentials",
        product_record(source_url="https://ode.example.org/page.aspx?product_id=A&page=2"),
        True,
    ),
    ("s3-url", product_record(source_url="s3://copernicus-dem-30m/tileList.txt"), True),
    ("missing-units", _without("units"), False),
    ("missing-verification-status", _without("verification_status"), False),
    ("extra-field", {**product_record(), "notes": "x"}, False),
    ("empty-provider", product_record(provider=""), False),
    ("zero-units", product_record(units=0), False),
    ("null-provider", product_record(provider=None), False),
    ("bad-status", product_record(verification_status="PASS"), False),
    ("bad-body", product_record(body="Moon"), False),
    ("uppercase-checksum", product_record(checksum=HEX64.upper()), False),
    ("checksum-newline", product_record(checksum=HEX64 + "\n"), False),
    ("absolute-local-path", product_record(local_path="/Users/someone/dem.tif"), False),
    ("local-path-outside-data", product_record(local_path="outputs/dem.tif"), False),
    ("local-path-unknown", product_record(local_path="unknown"), False),
    ("url-unknown", product_record(source_url="unknown"), False),
    ("url-with-space", product_record(source_url="https://example.org/a b"), False),
    ("url-newline", product_record(source_url="https://example.org/a\n"), False),
    (
        "url-amz-signature",
        product_record(source_url="https://b.s3.amazonaws.com/a?X-Amz-Signature=abc"),
        False,
    ),
    ("url-signature", product_record(source_url="https://cdn.example.org/a?Signature=x"), False),
    ("url-token", product_record(source_url="https://example.org/a?token=x"), False),
    ("url-access-key", product_record(source_url="https://example.org/a?access_key=x"), False),
    ("url-expires", product_record(source_url="https://example.org/a?Expires=1"), False),
    ("url-fragment-token", product_record(source_url="https://example.org/a#id_token=x"), False),
    ("url-userinfo", product_record(source_url="https://user:pw@example.org/a"), False),
    ("url-encoded-token-name", product_record(source_url="https://example.org/a?%74oken=x"), False),
    (
        "url-encoded-token-equals",
        product_record(source_url="https://example.org/a?token%3Dx"),
        False,
    ),
    (
        "url-encoded-fragment-token",
        product_record(source_url="https://example.org/a#id_t%6Fken=x"),
        False,
    ),
    (
        "url-encoded-reserved-in-query",
        product_record(source_url="https://example.org/a?q=a%2Fb%20c%3D"),
        True,
    ),
    (
        "url-encoded-unreserved-in-path",
        product_record(source_url="https://example.org/%7Euser/a%2Db.tif"),
        True,
    ),
    ("verified-unknown-units", product_record(units="unknown"), False),
    ("verified-unknown-units-capitalised", product_record(units="Unknown"), False),
    ("verified-no-checksum", product_record(checksum=None), False),
    ("verified-no-local-path", product_record(local_path=None), False),
]


def test_product_record_cases_are_well_formed() -> None:
    names = [name for name, _, _ in PRODUCT_RECORD_CASES]
    assert len(names) == len(set(names))
    assert {valid for _, _, valid in PRODUCT_RECORD_CASES} == {True, False}


@pytest.mark.parametrize(
    ("value", "valid"),
    [pytest.param(value, valid, id=name) for name, value, valid in PRODUCT_RECORD_CASES],
)
def test_product_record_pydantic_and_json_schema_agree(value: dict[str, Any], valid: bool) -> None:
    assert pydantic_accepts("ProductRecord", value) is valid
    assert json_schema_accepts("ProductRecord", value) is valid
    assert pydantic_accepts("Sources", [value]) is valid
    assert json_schema_accepts("Sources", [value]) is valid


_URL_CHARACTERS = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~:/?#[]@!$&'()*+,;=%"
)


@given(
    suffix=st.text(alphabet=_URL_CHARACTERS + " \n\u00e9", max_size=24),
    token=st.sampled_from(
        ["", "Signature=", "TOKEN=", "access_key", "Expires=", "sig=", "@", "%74oken=", "token%3D"]
    ),
    tail=st.text(alphabet=_URL_CHARACTERS, max_size=8),
)
def test_source_url_verdicts_agree(suffix: str, token: str, tail: str) -> None:
    for url in (
        f"https://example.org/{suffix}{token}{tail}",
        f"https://{suffix}{token}{tail}",
        f"https://example.org/a?{suffix}{token}{tail}",
    ):
        value = product_record(source_url=url)
        assert pydantic_accepts("ProductRecord", value) is json_schema_accepts(
            "ProductRecord", value
        ), url


def test_percent_encoded_byte_verdicts_agree() -> None:
    """pydantic and the JSON Schema agree on every percent-encoded byte in each part of a URL."""
    disagree = []
    for byte in range(256):
        for code in {f"{byte:02X}", f"{byte:02x}"}:
            for url in (
                f"https://example.org/a.tif?q=a%{code}b",
                f"https://example.org/a.tif#a%{code}b",
                f"https://example.org/a%{code}b.tif",
            ):
                value = product_record(source_url=url)
                if pydantic_accepts("ProductRecord", value) is not json_schema_accepts(
                    "ProductRecord", value
                ):
                    disagree.append(url)
    assert disagree == []


@given(units=st.text(alphabet="unkowUNKOW \n\u212a", min_size=1, max_size=9))
def test_verified_units_verdicts_agree(units: str) -> None:
    value = product_record(units=units)
    assert pydantic_accepts("ProductRecord", value) is json_schema_accepts("ProductRecord", value)


# --- Science record parity (CONTRACTS a.4) ------------------------------------------------------
# Every cross-field rule that JSON Schema can express is written as an allOf conditional next to
# its pydantic validator. These tests try every combination of the fields each rule reads and
# require the same verdict from pydantic and from the committed JSON Schema. The rules that
# compare values inside a document (sorted values, n == len(values), closed rings, slot kinds,
# incidence min <= max) are pydantic-only and stay out of these combinations.


def definition_accepts(name: str, definition: str, value: Any) -> bool:
    schema = committed_schema(name)
    wrapped = {
        "$schema": JSON_SCHEMA_DIALECT,
        "$defs": schema.get("$defs", {}),
        "$ref": f"#/$defs/{definition}",
    }
    return bool(EcmaValidator(wrapped).is_valid(value))


def science_case(name: str) -> Any:
    return copy.deepcopy(cases_by_name()[name]["value"])


def verdicts_agree(
    pydantic_name: str, schema_accepts: Callable[[Any], bool], values: list[Any]
) -> tuple[int, int]:
    accepted = 0
    for value in values:
        verdict = pydantic_accepts(pydantic_name, value)
        assert verdict is schema_accepts(value), value
        accepted += verdict
    return accepted, len(values) - accepted


def comparison_with(family: str, entry: dict[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = science_case("comparison-terrain-only")
    value["distances_by_family"][family] = entry
    return value


@pytest.mark.parametrize("family", ["terrain_relief", "model_embedding"])
def test_distance_entry_rules_agree_on_every_combination(family: str) -> None:
    spread = {"min": 0.125, "median": 0.25, "max": 0.5, "n": 12}
    labels = ["Derived", "Predicted"] if family == "model_embedding" else ["Derived"]
    options: dict[str, list[Any]] = {
        "value": [None, 0, 0.375],
        "status": ["available", "unavailable", "not_comparable"],
        "reason": [None, "band_mismatch"],
        "reference_spread": [None, spread],
        "reference_spread_reason": [None, "spread_unavailable"],
        "background_percentile": [None, 12.5],
        "background_percentile_reason": [None, "background_sample_size_unset"],
        "tied": [None, True],
        "evidence_label": labels,
    }
    entries = [
        {
            "unit": "cosine distance",
            "tie_group": None,
            "tie_reason": None,
            **dict(zip(options, combination, strict=True)),
        }
        for combination in itertools.product(*options.values())
    ]
    values = [comparison_with(family, entry) for entry in entries]
    accepted, rejected = verdicts_agree(
        "ComparisonRecord", lambda value: json_schema_accepts("ComparisonRecord", value), values
    )
    assert accepted > 0
    assert rejected > 0


def test_fingerprint_rules_agree_on_every_combination() -> None:
    terrain = science_case("fingerprint-terrain")["terrain"]
    radar = science_case("fingerprint-radar")["radar"]
    values = []
    for kind, terrain_part, radar_part, hurst, reason, independent in itertools.product(
        ["terrain", "radar"],
        [None, terrain],
        [None, radar],
        [None, 0.625],
        [None, "insufficient_lags"],
        [False, True, 0],
    ):
        value = science_case("fingerprint-terrain")
        value["kind"] = kind
        value["terrain"] = copy.deepcopy(terrain_part)
        value["radar"] = copy.deepcopy(radar_part)
        if terrain_part is not None:
            value["terrain"]["relief"]["hurst"] = hurst
            value["terrain"]["relief"]["hurst_reason"] = reason
        if radar_part is not None:
            value["radar"]["blocks_independent"] = independent
        values.append(value)
    accepted, rejected = verdicts_agree(
        "FingerprintRecord", lambda value: json_schema_accepts("FingerprintRecord", value), values
    )
    assert accepted > 0
    assert rejected > 0


def test_distribution_rules_agree_on_every_combination() -> None:
    samples = {0: [], 1: [0.5], 3: [0.25, 0.5, 0.75]}
    statistics = ["min", "median", "p05", "p95", "max"]
    values = []
    for n, nulls in itertools.product(samples, [(), tuple(statistics), ("min",), ("max",)]):
        distribution = {"n": n, "values": samples[n]}
        distribution.update({key: None if key in nulls else 0.5 for key in statistics})
        value = science_case("fingerprint-radar")
        value["radar"]["sc"] = distribution
        values.append(value)
    accepted, rejected = verdicts_agree(
        "FingerprintRecord", lambda value: json_schema_accepts("FingerprintRecord", value), values
    )
    assert accepted == 3
    assert rejected == 9


def test_window_entry_reason_rule_agrees_on_every_combination() -> None:
    values = []
    for status, reasons in itertools.product(
        ["eligible", "rejected", "unknown"], [[], ["worldcover_missing"], ["a", "b"]]
    ):
        entry = science_case("windows-one-entry")[0]
        entry["status"] = status
        entry["reasons"] = reasons
        values.append([entry])
    accepted, rejected = verdicts_agree(
        "Windows", lambda value: json_schema_accepts("Windows", value), values
    )
    assert accepted == 5
    assert rejected == 4


def test_distance_entry_definition_alone_follows_the_same_rules() -> None:
    entry = science_case("comparison-terrain-only")["distances_by_family"]["radar_cpr"]
    assert definition_accepts("ComparisonRecord", "DistanceEntry", entry)
    entry["reason"] = None
    assert not definition_accepts("ComparisonRecord", "DistanceEntry", entry)


@pytest.mark.parametrize(
    ("name", "path", "value"),
    [
        (
            "fingerprint-radar",
            ("radar", "sc"),
            {
                "n": 2,
                "values": [0.5, 0.25],
                "min": 0.25,
                "median": 0.375,
                "p05": 0.25,
                "p95": 0.5,
                "max": 0.5,
            },
        ),
        (
            "fingerprint-radar",
            ("radar", "sc"),
            {
                "n": 1,
                "values": [0.25, 0.5],
                "min": 0.25,
                "median": 0.375,
                "p05": 0.25,
                "p95": 0.5,
                "max": 0.5,
            },
        ),
        ("window-record-moon", ("polygon", "ring_m", 4), [2500, 2500]),
        ("fingerprint-radar", ("radar", "incidence_deg"), {"min": 48.25, "max": 35.5}),
    ],
    ids=["values-unsorted", "n-not-the-count", "ring-not-closed", "incidence-min-above-max"],
)
def test_rules_inside_a_document_are_checked_by_pydantic_only(
    name: str, path: tuple[Any, ...], value: Any
) -> None:
    case = cases_by_name()[name]
    document = copy.deepcopy(case["value"])
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert not pydantic_accepts(case["schema"], document)
    assert json_schema_accepts(case["schema"], document)


def test_slot_kinds_are_checked_by_pydantic_only() -> None:
    entry = science_case("windows-one-entry")[0]
    entry["terrain"] = science_case("fingerprint-radar")
    assert not pydantic_accepts("Windows", [entry])
    assert json_schema_accepts("Windows", [entry])
