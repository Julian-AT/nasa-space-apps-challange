"""Tests for the release bundle writer, validator and mock bundle (PRD 8.4, 8.12, 12).

Every bundle written here is a temporary, synthetic test bundle under pytest's tmp_path. Its
values are test inputs, not observations or results, and it never leaves the test.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from terraluna_pipeline.export import (
    BUDGETS,
    MOCK_CREATED_UTC,
    Budget,
    BundleError,
    ErrorCode,
    budget_errors,
    main,
    mock_bundle_differences,
    validate_bundle,
    write_bundle,
    write_mock_bundle,
)
from terraluna_pipeline.schema import SCHEMA_VERSION, BundleManifest

ROOT = Path(__file__).resolve().parents[2]
PRD = ROOT / "PRD.md"
MOCK_BUNDLE = ROOT / "fixtures" / "mock-bundle"
GATE_IDS = [f"G{n}" for n in range(7)]
HEX64 = "0123456789abcdef" * 4


# --- Helpers ------------------------------------------------------------------------------------


def gate(gate_id: str = "G0", **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "gate_id": gate_id,
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


def fields(**overrides: Any) -> dict[str, Any]:
    """Manifest fields (all but `files`) of a synthetic test bundle.

    mock=false and a chosen shipping mode let each production check be tested on its own.
    """
    value: dict[str, Any] = {
        "bundle_version": "test-0",
        "schema_version": SCHEMA_VERSION,
        "mock": False,
        "run_id": None,
        "git_sha": None,
        "created_utc": "2026-11-14T00:00:00Z",
        "shipping_mode": "B",
        "targets": [],
        "model": None,
    }
    value.update(overrides)
    return value


def gates() -> list[dict[str, Any]]:
    return [gate(gate_id) for gate_id in GATE_IDS]


def files(extra: dict[str, object] | None = None) -> dict[str, object]:
    """validation.json and a small targets.json, updated with `extra`."""
    value: dict[str, object] = {"validation.json": gates(), "targets.json": ["test-target"]}
    value.update(extra or {})
    return value


def make_bundle(
    tmp_path: Path, bundle_files: dict[str, object] | None = None, **field_overrides: Any
) -> Path:
    out = tmp_path / "bundle"
    write_bundle(out, files() if bundle_files is None else bundle_files, fields(**field_overrides))
    return out


def codes(errors: list[BundleError]) -> list[tuple[str, str]]:
    return sorted((error.code, error.path) for error in errors)


def edit_manifest(bundle: Path, change: Callable[[dict[str, Any]], object]) -> None:
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    change(manifest)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def listed(manifest: dict[str, Any], path: str) -> dict[str, Any]:
    entry = next(entry for entry in manifest["files"] if entry["path"] == path)
    assert isinstance(entry, dict)
    return entry


def disk_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def prd_section(heading: str) -> str:
    text = PRD.read_text(encoding="utf-8")
    start = text.index(f"\n{heading}\n")
    end = text.find("\n#", start + len(heading) + 2)
    return text[start : end if end != -1 else len(text)]


def prd_budget_table() -> list[tuple[str, str]]:
    """(path cell, budget cell) for every row of the PRD 8.4 bundle table."""
    rows = []
    for line in prd_section("### 8.4 Release bundle contract").splitlines():
        if line.startswith("| `"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            rows.append((cells[0], cells[-1]))
    assert rows, "PRD 8.4 has no bundle table"
    return rows


def sample_path(template: str) -> str:
    return re.sub(r"\{[a-z_]+\}", "x", template)


# --- write_bundle -------------------------------------------------------------------------------


def test_written_bundle_validates(tmp_path: Path) -> None:
    bundle = make_bundle(
        tmp_path,
        files(
            {
                "comparisons/moon-test-w000.json": {"test": "synthetic"},
                "figures/test.svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>\n",
            }
        ),
    )
    assert validate_bundle(bundle, production=False) == []
    assert validate_bundle(bundle, production=True) == []


def test_manifest_lists_every_written_file_but_itself(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    written = write_bundle(
        out, files({"cards/test.json": {"b": 2}, "model/test.bin": b"\x00\x01"}), fields()
    )
    on_disk = disk_files(out)
    manifest = json.loads(on_disk.pop("manifest.json"))
    expected = [
        {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        for path, data in sorted(on_disk.items())
    ]
    assert manifest["files"] == expected
    assert [entry["path"] for entry in manifest["files"]] == [
        "cards/test.json",
        "model/test.bin",
        "targets.json",
        "validation.json",
    ]
    assert written == BundleManifest.model_validate(manifest)


def test_manifest_carries_the_given_fields(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    write_bundle(out, files(), fields(run_id="sha256:" + HEX64, targets=["test-target"]))
    manifest = json.loads((out / "manifest.json").read_bytes())
    files_listed = manifest.pop("files")
    assert manifest == fields(run_id="sha256:" + HEX64, targets=["test-target"])
    assert len(files_listed) == 2


def test_json_is_sorted_two_space_indented_lf_and_newline_terminated(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    write_bundle(out, files({"cards/x.json": {"b": 1, "a": {"d": [1, 2.5], "c": "é"}}}), fields())
    assert (out / "cards/x.json").read_bytes() == (
        b'{\n  "a": {\n    "c": "\xc3\xa9",\n    "d": [\n      1,\n      2.5\n    ]\n  },\n'
        b'  "b": 1\n}\n'
    )
    for path, data in disk_files(out).items():
        if path.endswith(".json"):
            assert b"\r" not in data, path
            assert data.endswith(b"}\n") or data.endswith(b"]\n"), path
            parsed = json.loads(data)
            canonical = json.dumps(parsed, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
            assert data == canonical.encode("utf-8"), path


def test_writing_twice_gives_identical_bytes(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    write_bundle(first, files({"cards/x.json": {"z": None, "a": True}}), fields())
    write_bundle(second, files({"cards/x.json": {"a": True, "z": None}}), fields())
    assert disk_files(first) == disk_files(second)


def test_bytes_values_are_written_verbatim(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    write_bundle(out, files({"figures/x.svg": b"<svg/>\r\n\x00"}), fields())
    assert (out / "figures/x.svg").read_bytes() == b"<svg/>\r\n\x00"


@pytest.mark.parametrize(
    ("bundle_files", "manifest_fields"),
    [
        (files({"manifest.json": {}}), fields()),
        (files(), {**fields(), "files": []}),
        (files({"../escape.json": {}}), fields()),
        (files({"/abs.json": {}}), fields()),
        (files({".hidden": {}}), fields()),
        (files({"a": {}, "a/b.json": {}}), fields()),
        (files({"cards/x.json": {"value": float("nan")}}), fields()),
        (files({"cards/x.json": {"value": float("inf")}}), fields()),
        (files({"cards/x.json": {1, 2}}), fields()),
        (files(), fields(mock="true")),
        (files(), fields(shipping_mode="D")),
        (files(), {key: value for key, value in fields().items() if key != "mock"}),
    ],
    ids=[
        "manifest-json-as-a-file",
        "files-in-manifest-fields",
        "escaping-path",
        "absolute-path",
        "hidden-path",
        "file-and-directory-clash",
        "nan",
        "infinity",
        "not-json-serializable",
        "non-bool-mock",
        "unknown-shipping-mode",
        "missing-mock",
    ],
)
def test_write_bundle_rejects_bad_input_and_writes_nothing(
    tmp_path: Path, bundle_files: dict[str, object], manifest_fields: dict[str, Any]
) -> None:
    out = tmp_path / "bundle"
    with pytest.raises((ValueError, TypeError)):
        write_bundle(out, bundle_files, manifest_fields)
    assert not out.exists()


def test_invalid_manifest_field_raises_validation_error(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        write_bundle(tmp_path / "bundle", files(), fields(mock="true"))


def test_write_bundle_refuses_a_non_empty_directory(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "keep.txt").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        write_bundle(out, files(), fields())
    assert disk_files(out) == {"keep.txt": b"keep"}


def test_write_bundle_accepts_an_existing_empty_directory(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    out.mkdir()
    write_bundle(out, files(), fields())
    assert validate_bundle(out, production=False) == []


# --- validate_bundle: integrity -----------------------------------------------------------------


@pytest.mark.parametrize("path", ["extra.json", "figures/extra.svg", ".DS_Store"])
def test_unlisted_file_is_reported(tmp_path: Path, path: str) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / path).parent.mkdir(parents=True, exist_ok=True)
    (bundle / path).write_bytes(b"{}\n")
    assert codes(validate_bundle(bundle, production=False)) == [("unlisted_file", path)]


def test_missing_file_is_reported(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "targets.json").unlink()
    assert codes(validate_bundle(bundle, production=False)) == [("missing_file", "targets.json")]


def test_listed_directory_is_a_missing_file(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "targets.json").unlink()
    (bundle / "targets.json").mkdir()
    assert codes(validate_bundle(bundle, production=False)) == [("missing_file", "targets.json")]


def test_changed_content_of_the_same_length_is_a_checksum_mismatch(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    original = (bundle / "targets.json").read_bytes()
    tampered = original.replace(b"test-target", b"test-tarxet")
    assert len(tampered) == len(original)
    assert tampered != original
    (bundle / "targets.json").write_bytes(tampered)
    assert codes(validate_bundle(bundle, production=False)) == [("sha256_mismatch", "targets.json")]


def test_wrong_listed_checksum_is_a_checksum_mismatch(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    edit_manifest(bundle, lambda m: listed(m, "targets.json").update(sha256=HEX64))
    errors = validate_bundle(bundle, production=False)
    assert codes(errors) == [("sha256_mismatch", "targets.json")]
    assert HEX64 in errors[0].message


def test_wrong_listed_byte_count_is_a_byte_count_mismatch(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    size = (bundle / "targets.json").stat().st_size
    edit_manifest(bundle, lambda m: listed(m, "targets.json").update(bytes=size + 1))
    errors = validate_bundle(bundle, production=False)
    assert codes(errors) == [("bytes_mismatch", "targets.json")]
    assert str(size) in errors[0].message
    assert str(size + 1) in errors[0].message


def test_changed_length_is_both_mismatches(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "targets.json").write_bytes(b"[]\n")
    assert codes(validate_bundle(bundle, production=False)) == [
        ("bytes_mismatch", "targets.json"),
        ("sha256_mismatch", "targets.json"),
    ]


def test_file_listed_twice_is_reported(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    edit_manifest(bundle, lambda m: m["files"].append(dict(listed(m, "targets.json"))))
    assert codes(validate_bundle(bundle, production=False)) == [
        ("file_listed_twice", "targets.json")
    ]


def test_manifest_listing_itself_is_reported(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    entry = {"path": "manifest.json", "sha256": HEX64, "bytes": 1}
    edit_manifest(bundle, lambda m: m["files"].append(entry))
    assert codes(validate_bundle(bundle, production=False)) == [
        ("manifest_lists_itself", "manifest.json")
    ]


def test_symlink_is_not_a_regular_file(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}\n")
    (bundle / "link.json").symlink_to(outside)
    (bundle / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    assert codes(validate_bundle(bundle, production=False)) == [
        ("not_regular_file", "link.json"),
        ("not_regular_file", "linked-dir"),
    ]


def test_listed_file_replaced_by_a_symlink_is_reported(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    copy = tmp_path / "copy.json"
    copy.write_bytes((bundle / "targets.json").read_bytes())
    (bundle / "targets.json").unlink()
    (bundle / "targets.json").symlink_to(copy)
    assert codes(validate_bundle(bundle, production=False)) == [
        ("missing_file", "targets.json"),
        ("not_regular_file", "targets.json"),
    ]


# --- validate_bundle: manifest ------------------------------------------------------------------


def test_missing_bundle_directory_is_reported(tmp_path: Path) -> None:
    assert codes(validate_bundle(tmp_path / "absent", production=False)) == [
        ("bundle_not_directory", ".")
    ]


def test_missing_manifest_is_reported(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "manifest.json").unlink()
    assert codes(validate_bundle(bundle, production=False)) == [
        ("manifest_missing", "manifest.json")
    ]


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"",
        b'{"mock": NaN}',
        b'{"mock": true, "mock": false}',
        b"\xff\xfe{}",
        b"[" * 20_000 + b"]" * 20_000,
    ],
    ids=["truncated", "empty", "nan", "duplicate-key", "not-utf-8", "too-deeply-nested"],
)
def test_unparseable_manifest_is_reported(tmp_path: Path, raw: bytes) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "manifest.json").write_bytes(raw)
    assert codes(validate_bundle(bundle, production=False)) == [
        ("manifest_invalid_json", "manifest.json")
    ]


@pytest.mark.parametrize(
    ("change", "location"),
    [
        (lambda m: m.pop("mock"), "mock"),
        (lambda m: m.update(unexpected=True), "unexpected"),
        (lambda m: m.update(mock="true"), "mock"),
        (lambda m: m["files"][0].update(sha256=HEX64.upper()), "files.0.sha256"),
        (lambda m: m["files"][0].update(bytes=1.0), "files.0.bytes"),
        (lambda m: m["files"][0].update(path="../x.json"), "files.0.path"),
    ],
    ids=["missing-mock", "unknown-field", "string-mock", "uppercase-sha", "float-bytes", "escape"],
)
def test_manifest_schema_error_is_reported(
    tmp_path: Path, change: Callable[[dict[str, Any]], object], location: str
) -> None:
    bundle = make_bundle(tmp_path)
    edit_manifest(bundle, change)
    errors = validate_bundle(bundle, production=False)
    assert codes(errors) == [("manifest_schema", "manifest.json")]
    assert location in errors[0].message


def test_every_manifest_schema_error_is_reported(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    edit_manifest(bundle, lambda m: m.update(mock="true", shipping_mode="D"))
    errors = validate_bundle(bundle, production=False)
    assert codes(errors) == [("manifest_schema", "manifest.json")] * 2


def test_manifest_that_is_not_an_object_is_a_schema_error(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path)
    (bundle / "manifest.json").write_bytes(b"[]\n")
    assert codes(validate_bundle(bundle, production=False)) == [
        ("manifest_schema", "manifest.json")
    ]


# --- validate_bundle: budgets -------------------------------------------------------------------


def test_targets_json_of_51200_bytes_blows_its_budget(tmp_path: Path) -> None:
    targets = b"[" + b" " * 51_198 + b"]"
    assert len(targets) == 51_200
    bundle = make_bundle(tmp_path, files({"targets.json": targets}))
    errors = validate_bundle(bundle, production=False)
    assert codes(errors) == [("budget_exceeded", "targets.json")]
    assert "51200" in errors[0].message
    assert "50000" in errors[0].message


def test_oversized_manifest_blows_its_budget(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path, targets=[f"test-target-{n:05d}" for n in range(3_000)])
    assert (bundle / "manifest.json").stat().st_size >= 50_000
    assert codes(validate_bundle(bundle, production=False)) == [
        ("budget_exceeded", "manifest.json")
    ]


def test_budgets_are_the_prd_8_4_numeric_budgets_in_si_units() -> None:
    units = {"kB": 1000, "MB": 1000**2}
    expected = []
    for paths, budget in prd_budget_table():
        found = re.findall(r"([<≤]) ([0-9]+) (kB|MB)", budget)
        assert len(found) <= 1, budget
        if found:
            comparator, number, unit = found[0]
            expected.append((paths, budget, comparator, int(number) * units[unit]))
    assert len(expected) == 10
    assert len(BUDGETS) == len(expected)
    for (paths, budget, comparator, limit), rule in zip(expected, BUDGETS, strict=True):
        assert (rule.comparator, rule.limit_bytes) == (comparator, limit), paths
        basenames = {name.rsplit("/", 1)[-1] for name in re.findall(r"`([^`]+)`", paths)}
        for template in rule.templates:
            assert template.rsplit("/", 1)[-1] in basenames, (template, paths)
        if "total" in budget:
            assert rule.scope == "total", paths
        if "each" in budget or "per " in budget:
            assert rule.scope == "each", paths


def budget_paths(rule: Budget) -> list[str]:
    """One sample path per template; the single-template tile total gets two distinct tiles."""
    paths = [sample_path(template) for template in rule.templates]
    if rule.scope == "total" and len(paths) == 1:
        paths.append(re.sub(r"\{[a-z_]+\}", "y", rule.templates[0]))
    return paths


@pytest.mark.parametrize("rule", BUDGETS, ids=lambda rule: rule.templates[0])
def test_each_budget_allows_its_limit_and_rejects_one_byte_more(rule: Budget) -> None:
    allowed = rule.limit_bytes if rule.comparator == "≤" else rule.limit_bytes - 1
    paths = budget_paths(rule)
    if rule.scope == "each":
        for path in paths:
            assert budget_errors({path: allowed}) == []
            assert codes(budget_errors({path: allowed + 1})) == [("budget_exceeded", path)]
        return
    sizes = dict.fromkeys(paths, 1)
    sizes[paths[0]] = allowed - (len(paths) - 1)
    assert sum(sizes.values()) == allowed
    assert budget_errors(sizes) == []
    sizes[paths[-1]] += 1
    assert codes(budget_errors(sizes)) == [("budget_exceeded", ", ".join(rule.templates))]


def test_per_file_budgets_apply_to_each_file_separately() -> None:
    sizes = {"comparisons/a.json": 2_000_000, "comparisons/b.json": 2_000_000}
    assert budget_errors(sizes) == []
    sizes["comparisons/b.json"] += 1
    assert codes(budget_errors(sizes)) == [("budget_exceeded", "comparisons/b.json")]


def test_total_budgets_sum_every_matching_file() -> None:
    sizes = {f"tiles/hillshade/3/{x}/0.webp": 100_000_000 for x in range(3)}
    assert budget_errors(sizes) == []
    sizes["tiles/slope/0/0/0.webp"] = 1
    errors = budget_errors(sizes)
    assert codes(errors) == [("budget_exceeded", "tiles/{layer}/{z}/{x}/{y}.webp")]
    assert "300000001" in errors[0].message


def test_paths_without_a_numeric_budget_are_not_limited() -> None:
    sizes = {
        "validation.json": 10**12,
        "cards/x.json": 10**12,
        "figures/x.svg": 10**12,
        "windows/mars.parquet": 10**12,
        "model/encoder.onnx": 10**12,
        "model/model_card.json": 10**12,
    }
    assert budget_errors(sizes) == []


# --- validate_bundle: validation.json -----------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        gate(status="PASSED"),
        gate(gate_id="G7"),
        gate(result=""),
        gate(passed=True),
        {key: value for key, value in gate().items() if key != "status"},
        "G0",
    ],
    ids=["bad-status", "bad-gate-id", "empty-result", "unknown-field", "missing-status", "string"],
)
def test_invalid_gate_record_is_reported(tmp_path: Path, entry: object) -> None:
    records: list[object] = [entry, *gates()[1:]]
    bundle = make_bundle(tmp_path, files({"validation.json": records}))
    errors = validate_bundle(bundle, production=False)
    assert codes(errors) == [("gate_record_invalid", "validation.json")]
    assert "[0]" in errors[0].message


def test_every_invalid_gate_record_is_reported(tmp_path: Path) -> None:
    records = [gate("G0", status="PASSED"), gate("G1"), gate("G2", status="")]
    bundle = make_bundle(tmp_path, files({"validation.json": records}))
    errors = validate_bundle(bundle, production=False)
    assert codes(errors) == [("gate_record_invalid", "validation.json")] * 2
    assert sorted(error.message[:3] for error in errors) == ["[0]", "[2]"]


def test_validation_json_must_be_an_array(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path, files({"validation.json": {"G0": gate()}}))
    assert codes(validate_bundle(bundle, production=False)) == [
        ("validation_not_array", "validation.json")
    ]


@pytest.mark.parametrize(
    "raw", [b"[NaN]", b"[" * 20_000 + b"]" * 20_000], ids=["nan", "too-deeply-nested"]
)
def test_validation_json_must_be_json(tmp_path: Path, raw: bytes) -> None:
    bundle = make_bundle(tmp_path, files({"validation.json": raw}))
    assert codes(validate_bundle(bundle, production=False)) == [
        ("validation_invalid_json", "validation.json")
    ]


def test_missing_validation_json_is_reported(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path, {"targets.json": []})
    assert codes(validate_bundle(bundle, production=False)) == [
        ("validation_missing", "validation.json")
    ]


def test_gate_listed_twice_is_reported(tmp_path: Path) -> None:
    records = [*gates(), gate("G3", scope="a second G3 record")]
    bundle = make_bundle(tmp_path, files({"validation.json": records}))
    errors = validate_bundle(bundle, production=False)
    assert codes(errors) == [("gate_listed_twice", "validation.json")]
    assert "G3" in errors[0].message


# --- validate_bundle: production ----------------------------------------------------------------


def test_mock_bundle_is_refused_on_production(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path, mock=True)
    assert validate_bundle(bundle, production=False) == []
    assert codes(validate_bundle(bundle, production=True)) == [
        ("mock_on_production", "manifest.json")
    ]


def test_null_shipping_mode_is_refused_on_production(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path, shipping_mode=None)
    assert validate_bundle(bundle, production=False) == []
    assert codes(validate_bundle(bundle, production=True)) == [
        ("shipping_mode_unset_on_production", "manifest.json")
    ]


def test_every_error_carries_a_known_code_and_a_path(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path, files({"validation.json": [gate(status="PASSED")]}), mock=True)
    (bundle / "extra.json").write_bytes(b"{}\n")
    (bundle / "targets.json").unlink()
    errors = validate_bundle(bundle, production=True)
    assert codes(errors) == [
        ("gate_record_invalid", "validation.json"),
        ("missing_file", "targets.json"),
        ("mock_on_production", "manifest.json"),
        ("unlisted_file", "extra.json"),
    ]
    for error in errors:
        assert error.code in get_args(ErrorCode)
        assert error.path
        assert error.message
        assert str(error) == f"{error.path}: {error.code}: {error.message}"


# --- Mock bundle --------------------------------------------------------------------------------


def test_committed_mock_bundle_validates_without_production() -> None:
    assert validate_bundle(MOCK_BUNDLE, production=False) == []


def test_committed_mock_bundle_fails_on_production() -> None:
    assert codes(validate_bundle(MOCK_BUNDLE, production=True)) == [
        ("mock_on_production", "manifest.json"),
        ("shipping_mode_unset_on_production", "manifest.json"),
    ]


def test_mock_bundle_holds_only_its_manifest_and_validation_json() -> None:
    assert sorted(disk_files(MOCK_BUNDLE)) == ["manifest.json", "validation.json"]


def test_mock_manifest_is_labelled_mock_and_claims_nothing() -> None:
    manifest = json.loads((MOCK_BUNDLE / "manifest.json").read_bytes())
    validation = (MOCK_BUNDLE / "validation.json").read_bytes()
    assert manifest == {
        "bundle_version": "mock-0",
        "schema_version": SCHEMA_VERSION,
        "mock": True,
        "run_id": None,
        "git_sha": None,
        "created_utc": MOCK_CREATED_UTC,
        "shipping_mode": None,
        "targets": [],
        "model": None,
        "files": [
            {
                "path": "validation.json",
                "sha256": hashlib.sha256(validation).hexdigest(),
                "bytes": len(validation),
            }
        ],
    }


def test_mock_validation_json_has_every_gate_not_run() -> None:
    records = json.loads((MOCK_BUNDLE / "validation.json").read_bytes())
    assert records == [gate(gate_id) for gate_id in GATE_IDS]


def test_mock_bundle_regenerates_byte_for_byte(tmp_path: Path) -> None:
    write_mock_bundle(tmp_path / "mock-bundle")
    assert disk_files(tmp_path / "mock-bundle") == disk_files(MOCK_BUNDLE)


def test_mock_created_utc_is_a_fixed_constant() -> None:
    assert MOCK_CREATED_UTC == "1970-01-01T00:00:00Z"


def test_mock_bundle_without_replace_refuses_a_non_empty_directory(tmp_path: Path) -> None:
    out = tmp_path / "mock-bundle"
    write_mock_bundle(out)
    with pytest.raises(FileExistsError):
        write_mock_bundle(out)


def test_mock_bundle_replace_overwrites_an_existing_mock_bundle(tmp_path: Path) -> None:
    out = tmp_path / "mock-bundle"
    write_mock_bundle(out)
    (out / "validation.json").write_bytes(b"[]\n")
    write_mock_bundle(out, replace=True)
    assert disk_files(out) == disk_files(MOCK_BUNDLE)


def test_mock_bundle_replace_refuses_a_directory_with_other_files(tmp_path: Path) -> None:
    out = tmp_path / "mock-bundle"
    write_mock_bundle(out)
    (out / "validation.json").write_bytes(b"[]\n")
    (out / "cards").mkdir()
    (out / "cards/x.json").write_bytes(b"{}\n")
    before = disk_files(out)
    with pytest.raises(FileExistsError, match=r"cards/x\.json"):
        write_mock_bundle(out, replace=True)
    assert disk_files(out) == before


@pytest.mark.parametrize(
    "manifest",
    [b'{"mock": false}\n', b'{"mock": "true"}\n', b"{\n", b"[]\n"],
    ids=["mock-false", "string-mock", "invalid-json", "not-an-object"],
)
def test_mock_bundle_replace_refuses_a_bundle_not_marked_mock(
    tmp_path: Path, manifest: bytes
) -> None:
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "manifest.json").write_bytes(manifest)
    with pytest.raises(FileExistsError, match="mock"):
        write_mock_bundle(out, replace=True)
    assert disk_files(out) == {"manifest.json": manifest}


def test_mock_bundle_replace_refuses_a_directory_without_a_manifest(tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "validation.json").write_bytes(b"[]\n")
    with pytest.raises(FileExistsError, match="mock"):
        write_mock_bundle(out, replace=True)
    assert disk_files(out) == {"validation.json": b"[]\n"}


def test_mock_bundle_replace_refuses_a_file(tmp_path: Path) -> None:
    out = tmp_path / "mock-bundle"
    out.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        write_mock_bundle(out, replace=True)
    assert out.read_bytes() == b"keep"


def test_mock_bundle_replace_writes_a_new_directory(tmp_path: Path) -> None:
    write_mock_bundle(tmp_path / "mock-bundle", replace=True)
    assert disk_files(tmp_path / "mock-bundle") == disk_files(MOCK_BUNDLE)


def test_mock_bundle_differences_are_empty_for_the_committed_copy() -> None:
    assert mock_bundle_differences(MOCK_BUNDLE) == []


@pytest.mark.parametrize("drift", ["changed", "extra", "missing", "absent-directory"])
def test_mock_bundle_differences_report_drift(tmp_path: Path, drift: str) -> None:
    copy = tmp_path / "mock-bundle"
    write_mock_bundle(copy)
    if drift == "changed":
        (copy / "validation.json").write_bytes(b"[]\n")
        expected = "validation.json"
    elif drift == "extra":
        (copy / "extra.json").write_bytes(b"{}\n")
        expected = "extra.json"
    elif drift == "missing":
        (copy / "validation.json").unlink()
        expected = "validation.json"
    else:
        copy = tmp_path / "absent"
        expected = str(copy)
    differences = mock_bundle_differences(copy)
    assert differences
    assert any(expected in line for line in differences), differences


# --- CLI ----------------------------------------------------------------------------------------


def test_cli_validate_exits_0_for_a_valid_bundle(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(MOCK_BUNDLE)]) == 0
    out = capsys.readouterr().out
    assert "error" not in out.lower()


def test_cli_validate_prints_every_error_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = make_bundle(tmp_path, mock=True)
    (bundle / "extra.json").write_bytes(b"{}\n")
    (bundle / "targets.json").unlink()
    assert main(["validate", str(bundle)]) == 1
    out = capsys.readouterr().out
    assert "extra.json: unlisted_file: " in out
    assert "targets.json: missing_file: " in out
    assert "mock_on_production" not in out
    assert main(["validate", str(bundle), "--production"]) == 1
    out = capsys.readouterr().out
    for line in ("extra.json: unlisted_file: ", "targets.json: missing_file: "):
        assert line in out
    assert "manifest.json: mock_on_production: " in out


def test_cli_mock_bundle_writes_a_valid_mock_bundle(tmp_path: Path) -> None:
    assert main(["mock-bundle", str(tmp_path / "mock")]) == 0
    assert disk_files(tmp_path / "mock") == disk_files(MOCK_BUNDLE)


def test_cli_mock_bundle_regenerates_over_an_existing_mock_bundle(tmp_path: Path) -> None:
    out = tmp_path / "mock"
    write_mock_bundle(out)
    (out / "validation.json").write_bytes(b"[]\n")
    assert main(["mock-bundle", str(out)]) == 0
    assert disk_files(out) == disk_files(MOCK_BUNDLE)


def test_cli_mock_bundle_refuses_a_directory_with_other_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "mock"
    write_mock_bundle(out)
    (out / "extra.json").write_bytes(b"{}\n")
    assert main(["mock-bundle", str(out)]) == 1
    assert "extra.json" in capsys.readouterr().err
    assert (out / "extra.json").read_bytes() == b"{}\n"


def test_cli_mock_bundle_check_passes_on_the_committed_copy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["mock-bundle-check", str(MOCK_BUNDLE)]) == 0
    assert capsys.readouterr().out.strip()


def test_cli_mock_bundle_check_prints_drift_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copy = tmp_path / "mock-bundle"
    write_mock_bundle(copy)
    (copy / "extra.json").write_bytes(b"{}\n")
    (copy / "validation.json").write_bytes(b"[]\n")
    assert main(["mock-bundle-check", str(copy)]) == 1
    out = capsys.readouterr().out
    assert "extra.json" in out
    assert "validation.json" in out


def test_module_entry_point_validates_the_mock_bundle_for_production() -> None:
    command = [sys.executable, "-m", "terraluna_pipeline.export", "validate", str(MOCK_BUNDLE)]
    ok = subprocess.run(command, capture_output=True, text=True, check=False)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    refused = subprocess.run(
        [*command, "--production"], capture_output=True, text=True, check=False
    )
    assert refused.returncode == 1
    assert "manifest.json: mock_on_production: " in refused.stdout
    assert "manifest.json: shipping_mode_unset_on_production: " in refused.stdout
