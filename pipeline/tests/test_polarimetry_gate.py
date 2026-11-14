"""G1 gate record from real pytest results (T2-06; PRD 9 G1, 8.12 GateRecord, 7 Fixtures).

``python -m terraluna_pipeline.polarimetry record-g1 --junit <path> --out <path>`` reads
pytest's own junit report and writes one :class:`GateRecord` for G1. The required tests are
listed in ``fixtures/polarimetry/g1_required_tests.json``: every F1-F10 fixture test, every
test of T2-01 to T2-03, grouped per story as convention tests, invalid-data tests and the
other tests (PRD 9: "conventions labelled; invalid-data behaviour tested"), and the T2-07
real-scene oracle check.

Status rules fixed by these tests (also in the polarimetry module docstring of the recorder):

- ``FAIL``: a required test failed or errored in any case, or any other test in a module that
  holds a required test failed (collection errors included).
- ``NOT_RUN``: no required test passed (all absent or skipped).
- ``PARTIAL``: some required tests passed and none failed, but at least one is absent from the
  report, skipped (xfail included) or incomplete (a listed parametrized case is absent). The
  real-scene check is required, so G1 stays ``PARTIAL`` until it has passed on a real scene.
- ``PASS``: every required test passed in every listed case.

The junit reports built in this file are synthetic inputs, labelled as such, written only to
temporary directories and never used as evidence. The real-run tests launch pytest on the
required tests themselves.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from terraluna_pipeline import polarimetry
from terraluna_pipeline.polarimetry import (
    G1_FAILURE_ACTION,
    G1_REQUIRED_TESTS_PATH,
    G1RequiredTests,
    load_g1_required_tests,
    main,
    record_g1,
)
from terraluna_pipeline.schema import GateRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "pipeline" / "tests"
REQUIRED_PATH = REPO_ROOT / "fixtures" / "polarimetry" / "g1_required_tests.json"
COMMITTED_RECORD = REPO_ROOT / "fixtures" / "polarimetry" / "g1-record.json"
ORACLE_FILE = "pipeline/tests/test_polarimetry_oracle.py"

REQUIRED = load_g1_required_tests()
FIXTURES = tuple(f"F{n}" for n in range(1, 11))
STORIES = ("T2-01", "T2-02", "T2-03")
STORY_FILES = {
    "T2-01": "pipeline/tests/test_polarimetry_decode.py",
    "T2-02": "pipeline/tests/test_polarimetry_stokes.py",
    "T2-03": "pipeline/tests/test_polarimetry_covariance.py",
}


def unique(ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ids))


STORY_GROUPS = ("conventions", "invalid_data", "other")
NO_SCENE_IDS = unique(
    [nodeid for ids in REQUIRED.fixtures.values() for nodeid in ids]
    + [
        nodeid
        for group in STORY_GROUPS
        for ids in getattr(REQUIRED, group).values()
        for nodeid in ids
    ]
)
"""Every required test except the real-scene check: they run on synthetic inputs."""
REAL_SCENE_IDS = tuple(REQUIRED.real_scene_check)
ALL_REQUIRED_IDS = unique(NO_SCENE_IDS + REAL_SCENE_IDS)
STORY_1_F1_ID = (
    "pipeline/tests/test_polarimetry_stokes.py::test_F1_minirf_zero_cpr_is_valid_not_missing"
)
F7_ORACLES_ID = "pipeline/tests/test_polarimetry_covariance.py::test_F7_matches_both_oracles"
UNLISTED_CASE = "synthetic-unlisted-case"


def listed_cases(nodeid: str) -> tuple[str, ...]:
    """The node ids pytest reports for a listed function: one per listed parametrized case."""
    cases = REQUIRED.parametrized_cases.get(nodeid)
    return (nodeid,) if cases is None else tuple(f"{nodeid}[{case}]" for case in cases)


FIXTURE_NAME = re.compile(r"^test_F(?P<first>[0-9]+)(?:_to_F(?P<last>[0-9]+))?_")


def covered_fixtures(function: str) -> set[str]:
    match = FIXTURE_NAME.match(function)
    if match is None:
        return set()
    first = int(match["first"])
    last = int(match["last"] or first)
    return {f"F{n}" for n in range(first, last + 1)}


def module_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }


# --- Synthetic junit reports (test inputs only) ------------------------------------------------

# Outcome name -> child element of <testcase>, as pytest 9.2 writes it (checked on real runs).
CHILDREN: dict[str, tuple[str, dict[str, str]] | None] = {
    "passed": None,
    "failed": ("failure", {"message": "assert 1 == 2"}),
    "error": ("error", {"message": 'failed on setup with "RuntimeError: boom"'}),
    "skipped": ("skipped", {"type": "pytest.skip", "message": "skipped"}),
    "xfail": ("skipped", {"type": "pytest.xfail", "message": "expected to fail"}),
    "xpass_strict": ("failure", {"message": "[XPASS(strict)] "}),
    "xpass_reported_skipped": (
        "skipped",
        {"message": "xfail-marked test passes unexpectedly"},
    ),
}
FAILING = {"failed", "error", "xpass_strict"}
NOT_PASSED = {"skipped", "xfail", "xpass_reported_skipped"}


def address(nodeid: str) -> tuple[str, str]:
    """pytest's junit classname and name for a node id (its mangle_test_address)."""
    path, _, rest = nodeid.partition("::")
    return path.removesuffix(".py").replace("/", "."), rest


def expand(cases: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Function ids become one testcase per listed case; ids with parameters stay as given.

    ``incomplete`` reports a function without one of its listed cases: a parametrized one
    without its first case, an unparametrized one only as an unlisted parametrized case.
    """
    expanded: list[tuple[str, str]] = []
    for nodeid, outcome in cases:
        if "[" in nodeid:
            expanded.append((nodeid, outcome))
            continue
        listed = listed_cases(nodeid)
        if outcome != "incomplete":
            expanded += [(case, outcome) for case in listed]
        elif listed == (nodeid,):
            expanded.append((f"{nodeid}[{UNLISTED_CASE}]", "passed"))
        else:
            expanded += [(case, "passed") for case in listed[1:]]
    return expanded


def junit_xml(
    cases: Iterable[tuple[str, str]],
    *,
    failures: int | None = None,
    errors: int | None = None,
    collection_errors: Iterable[str] = (),
) -> bytes:
    """A synthetic junit report in pytest's layout; never evidence."""
    suite = ET.Element("testsuite", name="pytest", hostname="synthetic")
    suite.set("timestamp", "2026-11-14T00:00:00.000000+00:00")
    n_failures = n_errors = n_skipped = n_tests = 0
    for nodeid, outcome in expand(cases):
        classname, name = address(nodeid)
        case = ET.SubElement(suite, "testcase", classname=classname, name=name, time="0.001")
        child = CHILDREN[outcome]
        if child is not None:
            ET.SubElement(case, child[0], child[1])
            n_failures += child[0] == "failure"
            n_errors += child[0] == "error"
            n_skipped += child[0] == "skipped"
        n_tests += 1
    for module_path in collection_errors:
        module, _ = address(module_path)
        case = ET.SubElement(suite, "testcase", classname="", name=module, time="0.001")
        ET.SubElement(case, "error", message="collection failure")
        n_errors += 1
        n_tests += 1
    suite.set("failures", str(n_failures if failures is None else failures))
    suite.set("errors", str(n_errors if errors is None else errors))
    suite.set("skipped", str(n_skipped))
    suite.set("tests", str(n_tests))
    suite.set("time", "0.100")
    root = ET.Element("testsuites", name="pytest tests")
    root.append(suite)
    return b'<?xml version="1.0" encoding="utf-8"?>' + ET.tostring(root)


def all_passed(*, real_scene: str | None = "passed") -> dict[str, str]:
    cases = dict.fromkeys(NO_SCENE_IDS, "passed")
    if real_scene is not None:
        cases.update(dict.fromkeys(REAL_SCENE_IDS, real_scene))
    return cases


def record_from(
    directory: Path,
    cases: Mapping[str, str] | Iterable[tuple[str, str]],
    *extra: str,
    **report: Any,
) -> tuple[int, GateRecord | None]:
    """Write a synthetic report, run the command in-process and read what it wrote."""
    pairs = list(cases.items()) if isinstance(cases, Mapping) else list(cases)
    junit = directory / "junit.xml"
    out = directory / "g1-record.json"
    junit.write_bytes(junit_xml(pairs, **report))
    code = main(["record-g1", "--junit", str(junit), "--out", str(out), *extra])
    if not out.exists():
        return code, None
    return code, GateRecord.model_validate_json(out.read_text(encoding="utf-8"))


def result_of(record: GateRecord) -> dict[str, Any]:
    assert record.result is not None
    result = json.loads(record.result)
    assert isinstance(result, dict)
    return result


def written(code: int, record: GateRecord | None) -> GateRecord:
    assert record is not None, f"no record written (exit {code})"
    return record


# --- The list of required tests ---------------------------------------------------------------


def test_command_reads_the_list_in_fixtures() -> None:
    assert G1_REQUIRED_TESTS_PATH == REQUIRED_PATH
    assert isinstance(REQUIRED, G1RequiredTests)


def test_required_list_names_every_fixture_and_the_convention_and_invalid_data_stories() -> None:
    assert set(REQUIRED.fixtures) == set(FIXTURES)
    for group in STORY_GROUPS:
        assert set(getattr(REQUIRED, group)) == set(STORIES)
    groups = [
        *REQUIRED.fixtures.values(),
        *(ids for group in STORY_GROUPS for ids in getattr(REQUIRED, group).values()),
    ]
    for ids in groups:
        assert ids
        assert len(set(ids)) == len(ids)
    assert REAL_SCENE_IDS


def test_listed_ids_are_test_functions_not_parametrized_cases() -> None:
    for nodeid in ALL_REQUIRED_IDS:
        path, separator, function = nodeid.partition("::")
        assert separator
        assert path.startswith("pipeline/tests/test_polarimetry")
        assert path.endswith(".py")
        assert re.fullmatch(r"test_[A-Za-z0-9_]+", function), nodeid


def test_every_listed_test_but_the_real_scene_check_exists() -> None:
    for nodeid in NO_SCENE_IDS:
        path, _, function = nodeid.partition("::")
        assert (REPO_ROOT / path).is_file(), nodeid
        assert function in module_functions(REPO_ROOT / path), nodeid


def test_every_fixture_named_test_is_listed_under_each_fixture_it_covers() -> None:
    files = sorted(TESTS_DIR.glob("test_polarimetry_*.py"))
    assert len(files) >= 5
    seen = 0
    for path in files:
        relative = path.relative_to(REPO_ROOT).as_posix()
        for function in module_functions(path):
            for fixture in covered_fixtures(function):
                seen += 1
                assert f"{relative}::{function}" in REQUIRED.fixtures[fixture], function
    assert seen >= len(FIXTURES)


def test_listed_fixture_tests_follow_the_naming_rule() -> None:
    for fixture, ids in REQUIRED.fixtures.items():
        for nodeid in ids:
            assert fixture in covered_fixtures(nodeid.partition("::")[2]), nodeid


@pytest.mark.parametrize("group", STORY_GROUPS)
def test_story_group_tests_come_from_their_story_files(group: str) -> None:
    for story, ids in getattr(REQUIRED, group).items():
        for nodeid in ids:
            assert nodeid.partition("::")[0] == STORY_FILES[story], nodeid
            assert not covered_fixtures(nodeid.partition("::")[2]), nodeid


def test_story_groups_do_not_overlap() -> None:
    seen: set[str] = set()
    for group in STORY_GROUPS:
        ids = {nodeid for ids in getattr(REQUIRED, group).values() for nodeid in ids}
        assert not seen & ids, group
        seen |= ids


@pytest.mark.parametrize("story", STORIES)
def test_every_test_of_stories_1_to_3_is_required(story: str) -> None:
    """Each test function of a story file is in its story's groups or a fixture group."""
    path = STORY_FILES[story]
    fixture_ids = {nodeid for ids in REQUIRED.fixtures.values() for nodeid in ids}
    listed = {nodeid for group in STORY_GROUPS for nodeid in getattr(REQUIRED, group)[story]}
    functions = module_functions(REPO_ROOT / path)
    assert len(functions) >= 40
    for function in functions:
        nodeid = f"{path}::{function}"
        assert nodeid in (fixture_ids if covered_fixtures(function) else listed), nodeid
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    assert not [name for name in classes if name.startswith("Test")]


def test_real_scene_check_is_the_story_7_oracle_test() -> None:
    oracle = REPO_ROOT / ORACLE_FILE
    for nodeid in REAL_SCENE_IDS:
        path, _, function = nodeid.partition("::")
        assert path == ORACLE_FILE
        if oracle.exists():
            assert function in module_functions(oracle), nodeid


def required_list_variant(tmp_path: Path, change: str) -> Path:
    data = json.loads(REQUIRED_PATH.read_text(encoding="utf-8"))
    if change == "missing_fixture":
        del data["fixtures"]["F10"]
    elif change == "extra_fixture":
        data["fixtures"]["F11"] = ["pipeline/tests/test_polarimetry_decode.py::test_F11_x"]
    elif change == "empty_list":
        data["fixtures"]["F9"] = []
    elif change == "parametrized_id":
        data["fixtures"]["F9"] = ["pipeline/tests/test_polarimetry_decode.py::test_F9_x[a]"]
    elif change == "foreign_file":
        data["fixtures"]["F9"] = ["pipeline/tests/test_schema.py::test_F9_x"]
    elif change == "duplicate_id":
        data["fixtures"]["F9"] = 2 * data["fixtures"]["F9"]
    elif change == "missing_story":
        del data["invalid_data"]["T2-03"]
    elif change == "missing_convention_story":
        del data["conventions"]["T2-01"]
    elif change == "missing_other_story":
        del data["other"]["T2-02"]
    elif change == "no_other":
        del data["other"]
    elif change == "no_conventions":
        del data["conventions"]
    elif change == "no_real_scene_check":
        data["real_scene_check"] = []
    elif change == "unknown_key":
        data["extra"] = []
    elif change == "string_instead_of_list":
        data["real_scene_check"] = REAL_SCENE_IDS[0]
    elif change == "no_parametrized_cases":
        del data["parametrized_cases"]
    elif change == "cases_of_an_unlisted_test":
        data["parametrized_cases"]["pipeline/tests/test_polarimetry_decode.py::test_x"] = ["a"]
    elif change == "cases_of_a_parametrized_id":
        data["parametrized_cases"][f"{F7_ORACLES_ID}[0.1]"] = ["a"]
    elif change == "no_cases":
        data["parametrized_cases"][F7_ORACLES_ID] = []
    elif change == "duplicate_case":
        data["parametrized_cases"][F7_ORACLES_ID] = ["0.1", "0.1"]
    elif change == "case_holding_a_node_separator":
        data["parametrized_cases"][F7_ORACLES_ID] = ["0.1", "a::b"]
    elif change == "case_holding_a_line_break":
        data["parametrized_cases"][F7_ORACLES_ID] = ["0.1", "a\nb"]
    else:
        raise AssertionError(change)
    path = tmp_path / "g1_required_tests.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "change",
    [
        "missing_fixture",
        "extra_fixture",
        "empty_list",
        "parametrized_id",
        "foreign_file",
        "duplicate_id",
        "missing_story",
        "missing_convention_story",
        "missing_other_story",
        "no_other",
        "no_conventions",
        "no_real_scene_check",
        "unknown_key",
        "string_instead_of_list",
        "no_parametrized_cases",
        "cases_of_an_unlisted_test",
        "cases_of_a_parametrized_id",
        "no_cases",
        "duplicate_case",
        "case_holding_a_node_separator",
        "case_holding_a_line_break",
    ],
)
def test_malformed_required_lists_are_refused(tmp_path: Path, change: str) -> None:
    with pytest.raises(ValueError):
        load_g1_required_tests(required_list_variant(tmp_path, change))


def test_missing_required_list_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="required"):
        load_g1_required_tests(tmp_path / "absent.json")


# --- Status rules on synthetic reports --------------------------------------------------------


def test_all_required_tests_passed_gives_pass(tmp_path: Path) -> None:
    code, record = record_from(tmp_path, all_passed())
    record = written(code, record)
    assert record.status == "PASS"
    assert code == 0
    result = result_of(record)
    assert result["failed"] == result["skipped"] == result["absent"] == []
    assert result["other_failed"] == []


@pytest.mark.parametrize("outcome", ["failed", "error", "xpass_strict"])
def test_a_failing_required_test_gives_fail_with_its_id(tmp_path: Path, outcome: str) -> None:
    cases = all_passed()
    cases[STORY_1_F1_ID] = outcome
    code, record = record_from(tmp_path, cases)
    record = written(code, record)
    assert record.status == "FAIL"
    assert code == 1
    assert result_of(record)["failed"] == [STORY_1_F1_ID]
    assert result_of(record)["fixtures"]["F1"]["failed"] == 1


def test_one_failing_parametrized_case_fails_its_test(tmp_path: Path) -> None:
    nodeid = "pipeline/tests/test_polarimetry_mchi.py::test_F6_matches_the_jones_oracle"
    listed = listed_cases(nodeid)
    assert f"{nodeid}[HH_HV_VV-45.0]" in listed
    cases = [(key, value) for key, value in all_passed().items() if key != nodeid]
    cases += [(case, "failed" if case.endswith("[HH_HV_VV-45.0]") else "passed") for case in listed]
    code, record = record_from(tmp_path, cases)
    record = written(code, record)
    assert record.status == "FAIL"
    assert result_of(record)["failed"] == [nodeid]
    f6_cases = sum(len(listed_cases(test)) for test in REQUIRED.fixtures["F6"])
    assert result_of(record)["fixtures"]["F6"]["cases"] == f6_cases


def test_a_parametrized_test_missing_one_listed_case_is_incomplete_not_pass(
    tmp_path: Path,
) -> None:
    """The review example: test_F7_matches_both_oracles[0.1] is gone, [0.9] passed."""
    assert listed_cases(F7_ORACLES_ID) == (f"{F7_ORACLES_ID}[0.9]", f"{F7_ORACLES_ID}[0.1]")
    cases = [(key, value) for key, value in all_passed().items() if key != F7_ORACLES_ID]
    cases.append((f"{F7_ORACLES_ID}[0.9]", "passed"))
    code, record = record_from(tmp_path, cases)
    record = written(code, record)
    assert record.status == "PARTIAL"
    assert code == 1
    result = result_of(record)
    assert result["incomplete"] == [F7_ORACLES_ID]
    assert result["absent_cases"] == [f"{F7_ORACLES_ID}[0.1]"]
    assert result["absent"] == result["failed"] == result["skipped"] == []
    assert result["fixtures"]["F7"]["incomplete"] == 1
    assert result["fixtures"]["F7"]["passed"] == len(REQUIRED.fixtures["F7"]) - 1


def test_an_unparametrized_test_reported_only_with_parameters_is_incomplete(
    tmp_path: Path,
) -> None:
    assert listed_cases(STORY_1_F1_ID) == (STORY_1_F1_ID,)
    cases = all_passed()
    cases[STORY_1_F1_ID] = "incomplete"
    code, record = record_from(tmp_path, cases)
    record = written(code, record)
    assert record.status == "PARTIAL"
    assert result_of(record)["incomplete"] == [STORY_1_F1_ID]
    assert result_of(record)["absent_cases"] == [STORY_1_F1_ID]


def test_a_duplicated_case_cannot_stand_in_for_a_missing_one(tmp_path: Path) -> None:
    cases = [(key, value) for key, value in all_passed().items() if key != F7_ORACLES_ID]
    cases += 2 * [(f"{F7_ORACLES_ID}[0.9]", "passed")]
    code, record = record_from(tmp_path, cases)
    record = written(code, record)
    assert record.status == "PARTIAL"
    assert result_of(record)["absent_cases"] == [f"{F7_ORACLES_ID}[0.1]"]


def test_a_failing_duplicate_of_a_passing_test_gives_fail(tmp_path: Path) -> None:
    # pytest writes two testcases for one test when its call fails and its teardown errors.
    cases = [*all_passed().items(), (STORY_1_F1_ID, "error")]
    code, record = record_from(tmp_path, cases)
    assert written(code, record).status == "FAIL"


def test_a_missing_required_test_gives_partial(tmp_path: Path) -> None:
    cases = all_passed()
    del cases[STORY_1_F1_ID]
    code, record = record_from(tmp_path, cases)
    record = written(code, record)
    assert record.status == "PARTIAL"
    assert code == 1
    assert result_of(record)["absent"] == [STORY_1_F1_ID]
    assert result_of(record)["fixtures"]["F1"]["absent"] == 1


@pytest.mark.parametrize("outcome", ["skipped", "xfail", "xpass_reported_skipped"])
def test_a_skipped_or_xfailed_required_test_gives_partial(tmp_path: Path, outcome: str) -> None:
    cases = all_passed()
    cases[STORY_1_F1_ID] = outcome
    code, record = record_from(tmp_path, cases)
    record = written(code, record)
    assert record.status == "PARTIAL"
    assert code == 1
    assert result_of(record)["skipped"] == [STORY_1_F1_ID]


def test_no_required_test_run_gives_not_run(tmp_path: Path) -> None:
    code, record = record_from(tmp_path, {"pipeline/tests/test_schema.py::test_x": "passed"})
    record = written(code, record)
    assert record.status == "NOT_RUN"
    assert code == 1
    assert sorted(result_of(record)["absent"]) == sorted(ALL_REQUIRED_IDS)


def test_an_empty_report_gives_not_run(tmp_path: Path) -> None:
    code, record = record_from(tmp_path, {})
    assert written(code, record).status == "NOT_RUN"


def test_only_skipped_required_tests_give_not_run(tmp_path: Path) -> None:
    code, record = record_from(tmp_path, dict.fromkeys(ALL_REQUIRED_IDS, "skipped"))
    assert written(code, record).status == "NOT_RUN"


def test_a_failure_wins_over_missing_and_skipped_tests(tmp_path: Path) -> None:
    cases = dict.fromkeys(ALL_REQUIRED_IDS[:3], "skipped")
    cases[ALL_REQUIRED_IDS[3]] = "failed"
    code, record = record_from(tmp_path, cases)
    assert written(code, record).status == "FAIL"


def test_absent_real_scene_check_gives_partial_and_scope_says_not_included(
    tmp_path: Path,
) -> None:
    code, record = record_from(tmp_path, all_passed(real_scene=None))
    record = written(code, record)
    assert record.status == "PARTIAL"
    assert code == 1
    assert record.scope is not None
    assert "real-scene" in record.scope
    assert "not included" in record.scope
    assert "absent" in record.scope
    assert result_of(record)["absent"] == list(REAL_SCENE_IDS)
    assert result_of(record)["real_scene_check"]["absent"] == len(REAL_SCENE_IDS)


def test_skipped_real_scene_check_gives_partial_and_scope_says_not_included(
    tmp_path: Path,
) -> None:
    code, record = record_from(tmp_path, all_passed(real_scene="skipped"))
    record = written(code, record)
    assert record.status == "PARTIAL"
    assert record.scope is not None
    assert "not included" in record.scope
    assert "skipped" in record.scope


def test_passed_real_scene_check_is_stated_as_included(tmp_path: Path) -> None:
    code, record = record_from(tmp_path, all_passed())
    record = written(code, record)
    assert record.scope is not None
    assert "included and passed" in record.scope
    assert "not included" not in record.scope


def test_scope_names_the_prd_9_g1_evidence(tmp_path: Path) -> None:
    code, record = record_from(tmp_path, all_passed())
    scope = written(code, record).scope
    assert scope is not None
    for phrase in [
        "F1-F10",
        "independent basis transformation",
        "every test of T2-01 to T2-03",
        "convention",
        "invalid-data",
        "fixtures/polarimetry/g1_required_tests.json",
    ]:
        assert phrase in scope


def test_failed_real_scene_check_gives_fail(tmp_path: Path) -> None:
    code, record = record_from(tmp_path, all_passed(real_scene="failed"))
    record = written(code, record)
    assert record.status == "FAIL"
    assert record.scope is not None
    assert "run and failed" in record.scope
    assert "included and passed" not in record.scope
    assert result_of(record)["failed"] == list(REAL_SCENE_IDS)


def test_a_failing_unlisted_test_in_a_g1_module_gives_fail(tmp_path: Path) -> None:
    unlisted = (
        "pipeline/tests/test_polarimetry_derive.py::test_input_kinds_are_named_string_constants"
    )
    assert unlisted not in ALL_REQUIRED_IDS
    cases = [*all_passed().items(), (unlisted + "[case]", "failed")]
    code, record = record_from(tmp_path, cases)
    record = written(code, record)
    assert record.status == "FAIL"
    assert result_of(record)["failed"] == []
    assert result_of(record)["other_failed"] == [
        "pipeline.tests.test_polarimetry_derive::test_input_kinds_are_named_string_constants[case]"
    ]


def test_a_collection_error_in_a_g1_module_gives_fail(tmp_path: Path) -> None:
    code, record = record_from(
        tmp_path, all_passed(), collection_errors=["pipeline/tests/test_polarimetry_derive.py"]
    )
    record = written(code, record)
    assert record.status == "FAIL"
    assert result_of(record)["other_failed"] == ["pipeline.tests.test_polarimetry_derive"]


def test_failures_outside_the_g1_modules_do_not_decide_g1(tmp_path: Path) -> None:
    cases = [*all_passed().items(), ("pipeline/tests/test_schema.py::test_x", "failed")]
    code, record = record_from(tmp_path, cases, collection_errors=["pipeline/tests/test_schema.py"])
    record = written(code, record)
    assert record.status == "PASS"
    assert result_of(record)["other_failed"] == []


OUTCOMES = [*sorted(CHILDREN), "absent", "incomplete"]


def expected_status(outcomes: Mapping[str, str]) -> str:
    """The status rules, restated independently of the implementation."""
    if any(outcome in FAILING for outcome in outcomes.values()):
        return "FAIL"
    if not any(outcome == "passed" for outcome in outcomes.values()):
        return "NOT_RUN"
    if any(outcome != "passed" for outcome in outcomes.values()):
        return "PARTIAL"
    return "PASS"


@settings(deadline=None, max_examples=60)
@example(default="passed", changes={})
@example(default="absent", changes={})
@given(
    default=st.sampled_from(OUTCOMES),
    changes=st.dictionaries(
        st.sampled_from(ALL_REQUIRED_IDS), st.sampled_from(OUTCOMES), max_size=4
    ),
)
def test_pass_only_when_every_required_test_passed(
    tmp_path_factory: pytest.TempPathFactory, default: str, changes: dict[str, str]
) -> None:
    """Every required test gets ``default`` or its drawn outcome; absent ones are left out."""
    outcomes = {nodeid: changes.get(nodeid, default) for nodeid in ALL_REQUIRED_IDS}
    cases = {nodeid: outcome for nodeid, outcome in outcomes.items() if outcome != "absent"}
    code, record = record_from(tmp_path_factory.mktemp("property"), cases)
    record = written(code, record)
    assert record.status == expected_status(outcomes)
    assert (code == 0) == (record.status == "PASS")
    assert (record.status == "PASS") == all(o == "passed" for o in outcomes.values())


# --- What the record holds --------------------------------------------------------------------


def test_record_validates_against_the_gate_record_model_and_nulls_stay_null(
    tmp_path: Path,
) -> None:
    code, record = record_from(tmp_path, all_passed())
    record = written(code, record)
    text = (tmp_path / "g1-record.json").read_text(encoding="utf-8")
    assert GateRecord.model_validate_json(text) == record
    data = json.loads(text)
    assert set(data) == set(GateRecord.model_fields)
    assert data["gate_id"] == "G1"
    assert data["run_id"] is None
    assert data["consequence"] is None


@pytest.mark.parametrize(
    ("cases", "status"),
    [
        (all_passed(real_scene=None), "PARTIAL"),
        (all_passed(real_scene="failed"), "FAIL"),
        ({}, "NOT_RUN"),
    ],
)
def test_consequence_is_the_prd_failure_action_unless_pass(
    tmp_path: Path, cases: dict[str, str], status: str
) -> None:
    code, record = record_from(tmp_path, cases)
    record = written(code, record)
    assert record.status == status
    assert record.consequence == G1_FAILURE_ACTION
    assert "No quantitative CPR output until fixed" in G1_FAILURE_ACTION


def test_run_id_is_recorded_only_when_supplied(tmp_path: Path) -> None:
    code, record = record_from(tmp_path, all_passed(), "--run-id", "run-2026-11-14-a")
    assert written(code, record).run_id == "run-2026-11-14-a"


def test_an_empty_run_id_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    code, record = record_from(tmp_path, all_passed(), "--run-id", "")
    assert code == 2
    assert record is None


def test_result_holds_counts_per_fixture_and_per_story(tmp_path: Path) -> None:
    cases = all_passed(real_scene=None)
    del cases[STORY_1_F1_ID]
    code, record = record_from(tmp_path, cases)
    result = result_of(written(code, record))
    assert list(result["fixtures"]) == list(FIXTURES)
    for group in STORY_GROUPS:
        assert list(result[group]) == list(STORIES)
    for fixture in FIXTURES:
        counts = result["fixtures"][fixture]
        assert counts["tests"] == len(REQUIRED.fixtures[fixture])
        outcomes = ("passed", "failed", "skipped", "incomplete", "absent")
        assert sum(counts[outcome] for outcome in outcomes) == counts["tests"]
    assert result["fixtures"]["F1"] == {
        "tests": 3,
        "passed": 2,
        "failed": 0,
        "skipped": 0,
        "incomplete": 0,
        "absent": 1,
        "cases": sum(len(listed_cases(test)) for test in REQUIRED.fixtures["F1"][1:]),
    }
    assert result["fixtures"]["F2"]["passed"] == 3
    for group in STORY_GROUPS:
        for story in STORIES:
            assert result[group][story]["passed"] == len(getattr(REQUIRED, group)[story])
    assert result["real_scene_check"]["absent"] == len(REAL_SCENE_IDS)


def test_result_records_the_command_and_the_hashes_of_its_inputs(tmp_path: Path) -> None:
    code, record = record_from(tmp_path, all_passed())
    record = written(code, record)
    junit = tmp_path / "junit.xml"
    result = result_of(record)
    assert result["notes"]["command"] == (
        "python -m terraluna_pipeline.polarimetry record-g1"
        f" --junit {junit} --out {tmp_path / 'g1-record.json'}"
    )
    assert result["notes"]["pytest_command"] is None
    assert result["junit_sha256"] == hashlib.sha256(junit.read_bytes()).hexdigest()
    assert result["junit_timestamps"] == ["2026-11-14T00:00:00.000000+00:00"]
    assert result["required_tests"] == "fixtures/polarimetry/g1_required_tests.json"
    assert result["required_tests_sha256"] == hashlib.sha256(REQUIRED_PATH.read_bytes()).hexdigest()
    assert record.evidence_path == str(junit)


def test_record_is_a_function_of_its_inputs(tmp_path: Path) -> None:
    out = tmp_path / "g1-record.json"
    record_from(tmp_path, all_passed(real_scene=None))
    first = out.read_bytes()
    record_from(tmp_path, all_passed(real_scene=None))
    assert out.read_bytes() == first


def test_only_the_given_output_path_is_written(tmp_path: Path) -> None:
    record_from(tmp_path, all_passed())
    assert sorted(path.name for path in tmp_path.iterdir()) == ["g1-record.json", "junit.xml"]


def test_record_g1_returns_the_record_it_wrote(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    out = tmp_path / "out.json"
    junit.write_bytes(junit_xml(all_passed().items()))
    record = record_g1(junit, out, command="record-g1 (in-process test)", run_id=None)
    assert GateRecord.model_validate_json(out.read_text(encoding="utf-8")) == record
    assert result_of(record)["notes"]["command"] == "record-g1 (in-process test)"
    assert result_of(record)["notes"]["pytest_command"] is None


def test_evidence_inside_the_repository_is_recorded_relative_to_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(polarimetry, "_REPO_ROOT", tmp_path)
    junit = tmp_path / "fixtures" / "polarimetry" / "junit.xml"
    junit.parent.mkdir(parents=True)
    junit.write_bytes(junit_xml(all_passed().items()))
    out = tmp_path / "g1-record.json"
    assert main(["record-g1", "--junit", str(junit), "--out", str(out)]) == 0
    record = GateRecord.model_validate_json(out.read_text(encoding="utf-8"))
    assert record.evidence_path == "fixtures/polarimetry/junit.xml"


def test_evidence_inside_the_repository_outside_fixtures_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(polarimetry, "_REPO_ROOT", tmp_path)
    junit = tmp_path / "outputs" / "junit.xml"
    junit.parent.mkdir(parents=True)
    junit.write_bytes(junit_xml(all_passed().items()))
    out = tmp_path / "g1-record.json"
    assert main(["record-g1", "--junit", str(junit), "--out", str(out)]) == 2
    assert not out.exists()
    assert "fixtures/polarimetry" in capsys.readouterr().err


def test_evidence_in_the_real_repository_outside_fixtures_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "g1-record.json"
    junit = REPO_ROOT / "outputs" / "g1-junit.xml"
    assert not junit.exists()
    assert main(["record-g1", "--junit", str(junit), "--out", str(out)]) == 2
    assert not out.exists()


# --- Notes: the commands and the commit ----------------------------------------------------------

SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
SCRATCH_IDENTITY = {
    "GIT_AUTHOR_NAME": "Terraluna test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Terraluna test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def git(directory: Path, *args: str) -> str:
    """Run git in ``directory`` (a scratch repository) and return its output."""
    completed = subprocess.run(
        ["git", "-C", str(directory), *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **SCRATCH_IDENTITY},
        timeout=60,
    )
    return completed.stdout.strip()


def git_or_none(directory: Path, *args: str) -> str | None:
    """git's output, or None where git cannot be run or fails (no checkout)."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(directory), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except OSError:
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


@pytest.fixture
def scratch_repo(tmp_path: Path) -> tuple[Path, str]:
    """A scratch git repository with one commit, written with plumbing (no hook runs)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    files = {".gitignore": "ignored.txt\n", "kept.txt": "kept\n"}
    files |= {"changed.txt": "before\n", "deleted.txt": "deleted\n"}
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", *files)
    commit = git(repo, "commit-tree", git(repo, "write-tree"), "-m", "scratch")
    git(repo, "update-ref", "HEAD", commit)
    return repo, commit


def notes_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root: Path, *extra: str
) -> dict[str, Any]:
    """Record a synthetic all-passed report as if ``root`` were the repository root."""
    monkeypatch.setattr(polarimetry, "_REPO_ROOT", root)
    reports = tmp_path / "reports"
    reports.mkdir()
    notes = result_of(written(*record_from(reports, all_passed(), *extra)))["notes"]
    assert isinstance(notes, dict)
    return notes


def test_notes_state_the_commit_of_a_clean_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch_repo: tuple[Path, str]
) -> None:
    repo, commit = scratch_repo
    notes = notes_from(tmp_path, monkeypatch, repo)
    assert notes["commit"] == commit
    assert notes["uncommitted_changes"] == []


def test_notes_list_every_change_the_commit_does_not_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch_repo: tuple[Path, str]
) -> None:
    repo, commit = scratch_repo
    (repo / "changed.txt").write_text("after\n", encoding="utf-8")
    (repo / "deleted.txt").unlink()
    (repo / "new file.txt").write_text("untracked\n", encoding="utf-8")
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(repo, "add", "staged.txt")
    (repo / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    notes = notes_from(tmp_path, monkeypatch, repo)
    assert notes["commit"] == commit
    assert notes["uncommitted_changes"] == [
        "changed.txt",
        "deleted.txt",
        "new file.txt",
        "staged.txt",
    ]


def test_notes_state_no_commit_outside_a_git_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    notes = notes_from(tmp_path, monkeypatch, plain)
    assert notes["commit"] is None
    assert notes["uncommitted_changes"] is None


def test_a_directory_inside_a_checkout_is_not_taken_for_its_top(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scratch_repo: tuple[Path, str]
) -> None:
    repo, _ = scratch_repo
    (repo / "sub").mkdir()
    notes = notes_from(tmp_path, monkeypatch, repo / "sub")
    assert notes["commit"] is None
    assert notes["uncommitted_changes"] is None


def test_notes_state_the_commit_of_this_repository(tmp_path: Path) -> None:
    notes = result_of(written(*record_from(tmp_path, all_passed())))["notes"]
    top = git_or_none(REPO_ROOT, "rev-parse", "--show-toplevel")
    if top is not None and Path(top).resolve() == REPO_ROOT.resolve():
        assert notes["commit"] == git_or_none(REPO_ROOT, "rev-parse", "HEAD")
        assert isinstance(notes["uncommitted_changes"], list)
    else:
        assert notes["commit"] is None


@pytest.mark.parametrize("form", ["equals", "separate", "junit-xml"])
def test_the_pytest_command_that_wrote_the_report_is_recorded(tmp_path: Path, form: str) -> None:
    junit = tmp_path / "junit.xml"
    option = {
        "equals": f"--junitxml={junit}",
        "separate": f"--junitxml {junit}",
        "junit-xml": f"--junit-xml={junit}",
    }[form]
    command = f"uv run pytest {option} -q -p no:cacheprovider"
    code, record = record_from(tmp_path, all_passed(), "--pytest-command", command)
    notes = result_of(written(code, record))["notes"]
    assert notes["pytest_command"] == command
    assert notes["command"].endswith(f"--pytest-command '{command}'")


@pytest.mark.parametrize(
    "command",
    [
        "",
        "uv run pytest -q",
        "uv run pytest --junitxml=OTHER",
        "uv run pytest --junitxml",
        "uv run pytest --junitxml='unbalanced",
    ],
)
def test_a_pytest_command_that_does_not_write_the_report_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    command = command.replace("OTHER", str(tmp_path / "other.xml"))
    code, record = record_from(tmp_path, all_passed(), "--pytest-command", command)
    assert code == 2
    assert record is None
    assert "pytest command" in capsys.readouterr().err


# --- Inputs that write nothing ----------------------------------------------------------------


def assert_refused(tmp_path: Path, data: bytes | None, capsys: pytest.CaptureFixture[str]) -> str:
    junit = tmp_path / "junit.xml"
    if data is not None:
        junit.write_bytes(data)
    out = tmp_path / "g1-record.json"
    out.write_text("previous record\n", encoding="utf-8")
    assert main(["record-g1", "--junit", str(junit), "--out", str(out)]) == 2
    assert out.read_text(encoding="utf-8") == "previous record\n"
    return capsys.readouterr().err


def test_missing_junit_report_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert "record-g1" in assert_refused(tmp_path, None, capsys)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not xml",
        b"<html><body/></html>",
        b"<testsuites><testcase classname='a' name='b'/></testsuites>",
        b"<testsuites><testsuite><testcase classname='a'/></testsuite></testsuites>",
        b"<testsuites><testsuite><testcase name='b'/></testsuite></testsuites>",
        b"<testsuites><testsuite><other/></testsuite></testsuites>",
        b"<testsuites><testsuite failures='x'/></testsuites>",
        b"<testsuites><testsuite failures='-1'/></testsuites>",
    ],
)
def test_unreadable_or_foreign_reports_write_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], data: bytes
) -> None:
    assert assert_refused(tmp_path, data, capsys)


def test_reports_with_a_doctype_or_entities_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = (
        b'<?xml version="1.0"?><!DOCTYPE t [<!ENTITY a "aaaa">]>'
        b"<testsuites><testsuite><testcase classname='a' name='&a;'/></testsuite></testsuites>"
    )
    assert "DOCTYPE" in assert_refused(tmp_path, data, capsys)


def test_a_suite_counting_failures_without_a_failing_testcase_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = junit_xml(all_passed().items(), failures=1)
    assert "inconsistent" in assert_refused(tmp_path, data, capsys)
    data = junit_xml(all_passed().items(), errors=2)
    assert "inconsistent" in assert_refused(tmp_path, data, capsys)


def test_missing_output_directory_writes_nothing(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_bytes(junit_xml(all_passed().items()))
    out = tmp_path / "absent" / "g1-record.json"
    assert main(["record-g1", "--junit", str(junit), "--out", str(out)]) == 2
    assert not out.parent.exists()


def test_output_cannot_overwrite_the_evidence(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    data = junit_xml(all_passed().items())
    junit.write_bytes(data)
    assert main(["record-g1", "--junit", str(junit), "--out", str(junit)]) == 2
    assert junit.read_bytes() == data


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["record-g1"],
        ["record-g1", "--junit", "j.xml"],
        ["record-g1", "--out", "o.json"],
        ["record-g2", "--junit", "j.xml", "--out", "o.json"],
    ],
)
def test_incomplete_or_unknown_commands_are_usage_errors(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(argv)
    assert error.value.code == 2


# --- Real pytest runs ----------------------------------------------------------------------------

BREAK_PLUGIN_NAME = "terraluna_g1_break_conversion"
BREAK_PLUGIN = '''\
"""Test-only pytest plugin (T2-06): negates a float S4 passed to circular_from_stokes.

test_polarimetry_gate.py writes it to a temporary directory so that a real pytest run of the
required tests fails for a real reason. It is never installed or committed.
"""

import terraluna_pipeline.polarimetry as polarimetry

_ORIGINAL = polarimetry.circular_from_stokes


def _broken(s1, s4, **kwargs):
    if type(s4) is float:
        s4 = -s4
    return _ORIGINAL(s1, s4, **kwargs)


def pytest_configure(config):
    polarimetry.circular_from_stokes = _broken
'''


RUN_TARGETS = (
    *STORY_FILES.values(),
    *(nodeid for nodeid in NO_SCENE_IDS if nodeid.partition("::")[0] not in STORY_FILES.values()),
)
"""The story files whole (so pytest's collection shows any unlisted test) and the other
listed tests by id."""


def run_pytest(junit: Path, *, plugin_dir: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    args = [sys.executable, "-m", "pytest", *RUN_TARGETS, f"--junitxml={junit}"]
    args += ["-q", "-p", "no:cacheprovider"]
    if plugin_dir is not None:
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(plugin_dir), env.get("PYTHONPATH", "")) if part
        )
        args += ["-p", BREAK_PLUGIN_NAME]
    return subprocess.run(
        args, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False, timeout=600
    )


def run_record_g1(junit: Path, out: Path) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, "-m", "terraluna_pipeline.polarimetry", "record-g1"]
    args += ["--junit", str(junit), "--out", str(out)]
    return subprocess.run(
        args, cwd=REPO_ROOT, capture_output=True, text=True, check=False, timeout=120
    )


@pytest.fixture(scope="module")
def real_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[int, Path]:
    junit = tmp_path_factory.mktemp("real-run") / "junit.xml"
    completed = run_pytest(junit)
    return completed.returncode, junit


@pytest.fixture(scope="module")
def broken_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[int, Path]:
    directory = tmp_path_factory.mktemp("broken-run")
    plugin_dir = directory / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / f"{BREAK_PLUGIN_NAME}.py").write_text(BREAK_PLUGIN, encoding="utf-8")
    junit = directory / "junit.xml"
    completed = run_pytest(junit, plugin_dir=plugin_dir)
    return completed.returncode, junit


def test_real_run_of_the_required_tests_is_partial_without_the_real_scene_check(
    real_run: tuple[int, Path], tmp_path: Path
) -> None:
    returncode, junit = real_run
    assert returncode == 0
    out = tmp_path / "g1-record.json"
    completed = run_record_g1(junit, out)
    assert completed.returncode == 1, completed.stderr
    record = GateRecord.model_validate_json(out.read_text(encoding="utf-8"))
    assert record.status == "PARTIAL"
    assert record.scope is not None
    assert "not included" in record.scope
    result = result_of(record)
    assert result["failed"] == result["skipped"] == result["other_failed"] == []
    assert result["absent"] == list(REAL_SCENE_IDS)
    for fixture in FIXTURES:
        counts = result["fixtures"][fixture]
        assert counts["passed"] == counts["tests"] == len(REQUIRED.fixtures[fixture])
        assert counts["cases"] >= counts["tests"]
    for group in STORY_GROUPS:
        for story in STORIES:
            counts = result[group][story]
            assert counts["passed"] == counts["tests"] == len(getattr(REQUIRED, group)[story])
    assert "PARTIAL" in completed.stdout


def test_listed_cases_are_the_cases_pytest_reports(real_run: tuple[int, Path]) -> None:
    """Every listed function's junit testcases in a real run are exactly its listed cases."""
    _, junit = real_run
    reported: dict[str, list[str]] = {}
    for case in ET.fromstring(junit.read_bytes()).iter("testcase"):
        classname, name = case.get("classname"), case.get("name")
        assert classname is not None and name is not None
        reported.setdefault(classname, []).append(name)
    for nodeid in NO_SCENE_IDS:
        module, function = address(nodeid)
        names = [
            name
            for name in reported.get(module, [])
            if name == function or name.startswith(function + "[")
        ]
        expected = [address(case)[1] for case in listed_cases(nodeid)]
        assert sorted(names) == sorted(expected), nodeid


def test_real_run_of_the_story_files_holds_only_listed_cases(real_run: tuple[int, Path]) -> None:
    """pytest's own collection of the whole story files finds no test the list leaves out."""
    _, junit = real_run
    listed = {
        (address(nodeid)[0], name)
        for nodeid in NO_SCENE_IDS
        for name in REQUIRED.junit_names(nodeid)
    }
    story_modules = {address(path)[0] for path in STORY_FILES.values()}
    seen = 0
    for case in ET.fromstring(junit.read_bytes()).iter("testcase"):
        if case.get("classname") in story_modules:
            seen += 1
            assert (case.get("classname"), case.get("name")) in listed, case.get("name")
    assert seen >= 3 * 40


def test_deliberately_failing_run_records_fail_and_its_exit_code_is_not_hidden(
    broken_run: tuple[int, Path], tmp_path: Path
) -> None:
    returncode, junit = broken_run
    assert returncode == 1
    out = tmp_path / "g1-record.json"
    completed = run_record_g1(junit, out)
    assert completed.returncode == 1
    record = GateRecord.model_validate_json(out.read_text(encoding="utf-8"))
    assert record.status == "FAIL"
    assert STORY_1_F1_ID in result_of(record)["failed"]
    assert result_of(record)["fixtures"]["F1"]["failed"] >= 1
    assert record.consequence == G1_FAILURE_ACTION


def add_synthetic_real_scene_pass(root: ET.Element) -> None:
    suite = root.find("testsuite")
    assert suite is not None
    for nodeid in REAL_SCENE_IDS:
        classname, name = address(nodeid)
        ET.SubElement(suite, "testcase", classname=classname, name=name, time="0.001")


@pytest.mark.parametrize(
    "remove",
    [
        None,
        STORY_1_F1_ID,
        REQUIRED.conventions["T2-02"][0],
        REQUIRED.invalid_data["T2-03"][0],
        f"{F7_ORACLES_ID}[0.1]",
    ],
)
def test_tampered_real_report_missing_a_required_test_is_not_pass(
    real_run: tuple[int, Path], tmp_path: Path, remove: str | None
) -> None:
    """The real report plus a synthetic real-scene pass; then one required test is removed."""
    _, junit = real_run
    root = ET.fromstring(junit.read_bytes())
    add_synthetic_real_scene_pass(root)
    if remove is not None:
        classname, name = address(remove)
        suite = root.find("testsuite")
        assert suite is not None
        removed = [
            case
            for case in suite.findall("testcase")
            if case.get("classname") == classname
            and (case.get("name") == name or str(case.get("name")).startswith(name + "["))
        ]
        assert removed
        for case in removed:
            suite.remove(case)
    tampered = tmp_path / "tampered.xml"
    tampered.write_bytes(ET.tostring(root))
    out = tmp_path / "g1-record.json"
    completed = run_record_g1(tampered, out)
    record = GateRecord.model_validate_json(out.read_text(encoding="utf-8"))
    if remove is None:
        assert record.status == "PASS"
        assert completed.returncode == 0
    elif "[" in remove:
        assert record.status == "PARTIAL"
        assert completed.returncode == 1
        assert result_of(record)["incomplete"] == [remove.partition("[")[0]]
        assert result_of(record)["absent_cases"] == [remove]
    else:
        assert record.status == "PARTIAL"
        assert completed.returncode == 1
        assert result_of(record)["absent"] == [remove]


def test_tampered_report_with_its_failures_deleted_is_refused(
    broken_run: tuple[int, Path], tmp_path: Path
) -> None:
    _, junit = broken_run
    root = ET.fromstring(junit.read_bytes())
    deleted = 0
    for case in root.iter("testcase"):
        for child in list(case):
            if child.tag in {"failure", "error"}:
                case.remove(child)
                deleted += 1
    assert deleted
    tampered = tmp_path / "tampered.xml"
    tampered.write_bytes(ET.tostring(root))
    out = tmp_path / "g1-record.json"
    completed = run_record_g1(tampered, out)
    assert completed.returncode == 2
    assert not out.exists()
    assert "inconsistent" in completed.stderr


# --- The committed record ---------------------------------------------------------------------


def test_committed_record_follows_the_rules_from_its_own_result() -> None:
    """fixtures/polarimetry/g1-record.json is written by the command, never by hand."""
    record = GateRecord.model_validate_json(COMMITTED_RECORD.read_text(encoding="utf-8"))
    assert record.gate_id == "G1"
    result = result_of(record)
    notes = result["notes"]
    assert notes["command"].startswith("python -m terraluna_pipeline.polarimetry record-g1 ")
    assert "--out fixtures/polarimetry/g1-record.json" in notes["command"]
    assert "--junitxml" in notes["pytest_command"]
    assert str(record.evidence_path) in notes["pytest_command"]
    assert f"--pytest-command {shlex.quote(notes['pytest_command'])}" in notes["command"]
    assert SHA.fullmatch(notes["commit"])
    assert set(notes["uncommitted_changes"]) <= {"fixtures/polarimetry/g1-record.json"}
    if git_or_none(REPO_ROOT, "rev-parse", "HEAD") is not None:
        if git_or_none(REPO_ROOT, "cat-file", "-e", notes["commit"] + "^{commit}") is not None:
            ancestry = ["merge-base", "--is-ancestor", notes["commit"], "HEAD"]
            assert git_or_none(REPO_ROOT, *ancestry) is not None
        else:
            assert git_or_none(REPO_ROOT, "rev-parse", "--is-shallow-repository") == "true"
    assert result["required_tests_sha256"] == hashlib.sha256(REQUIRED_PATH.read_bytes()).hexdigest()
    groups = [
        *(result["fixtures"][fixture] for fixture in FIXTURES),
        *(result[group][story] for group in STORY_GROUPS for story in STORIES),
        result["real_scene_check"],
    ]
    expected_tests = [
        *(len(REQUIRED.fixtures[fixture]) for fixture in FIXTURES),
        *(len(getattr(REQUIRED, group)[story]) for group in STORY_GROUPS for story in STORIES),
        len(REAL_SCENE_IDS),
    ]
    assert [group["tests"] for group in groups] == expected_tests
    if result["failed"] or result["other_failed"]:
        expected = "FAIL"
    elif not any(group["passed"] for group in groups):
        expected = "NOT_RUN"
    elif result["absent"] or result["skipped"] or result["incomplete"]:
        expected = "PARTIAL"
    else:
        expected = "PASS"
    assert record.status == expected
    assert record.consequence == (None if expected == "PASS" else G1_FAILURE_ACTION)
    assert record.scope is not None
    real_scene = result["real_scene_check"]
    if real_scene["passed"] == real_scene["tests"]:
        assert "included and passed" in record.scope
    else:
        assert "included and passed" not in record.scope
        assert record.status != "PASS"
