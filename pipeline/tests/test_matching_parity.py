"""The matching parity fixture (T4L-03): fixtures/parity/matching_synthetic.json.

The committed file must equal what the current code produces (every field but git_commit),
every float must read back bit-equal, and unavailable outputs are null with a reason.
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Any

import pytest

from terraluna_pipeline import matching, provenance
from terraluna_pipeline.config import DEFAULT_RUN_CONFIG

FIXTURE = matching.DEFAULT_PARITY_PATH


def committed() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return document


def test_committed_fixture_is_current() -> None:
    assert matching.main(["parity", "--check"]) == 0


def test_header_carries_provenance_and_the_prd_8_8_tolerance() -> None:
    document = committed()
    assert document["schema_version"] == matching.PARITY_SCHEMA_VERSION
    assert document["data_kind"] == "synthetic"
    assert document["tolerance"] == {"atol": 1e-9, "rtol": 0.0}
    assert document["config_sha256"] == provenance.config_version(DEFAULT_RUN_CONFIG)
    assert isinstance(document["git_commit"], str) and len(document["git_commit"]) == 40


def test_hand_cases_and_reasons() -> None:
    document = committed()
    terrain = {case["name"]: case["expected"] for case in document["terrain_distance"]}
    assert terrain["hand_case"]["value"] == pytest.approx(math.sqrt(5 / 3), rel=1e-15)
    assert terrain["identical"]["value"] == 0.0
    assert terrain["no_shared_lags"] == {
        "value": None,
        "reason": "no_shared_lags",
        "shared_lags_m": [],
        "unit": "RMS of log nu",
    }
    assert terrain["nu_zero"]["value"] is None and terrain["nu_zero"]["reason"] == "nu_zero"
    assert terrain["shared_lag_intersection"]["shared_lags_m"] == [360.0, 540.0]
    families = {case["family"] for case in document["terrain_distance"]}
    assert families == {"terrain_relief", "terrain_texture"}
    percentile = {case["name"]: case["expected"] for case in document["background_percentile"]}
    assert percentile["inside"]["value"] == 35.0
    assert percentile["interior_edge"]["value"] == 20.0
    assert percentile["below"]["value"] == 0.0
    assert percentile["above"]["value"] == 100.0
    assert percentile["empty_histogram"] == {"value": None, "reason": "background_empty"}


def floats(value: object) -> list[float]:
    if isinstance(value, float):
        return [value]
    if isinstance(value, dict):
        return [f for key in sorted(value) for f in floats(value[key])]
    if isinstance(value, list):
        return [f for item in value for f in floats(item)]
    return []


def test_floats_read_back_bit_equal() -> None:
    document = matching.parity_document()
    reloaded = json.loads(matching.parity_text(document))
    pairs = zip(floats(document), floats(reloaded), strict=True)
    assert all(struct.pack("<d", a) == struct.pack("<d", b) for a, b in pairs)


def test_check_ignores_git_commit_detects_drift_and_writes_nothing(tmp_path: Path) -> None:
    document = committed()
    document["git_commit"] = "f" * 40
    path = tmp_path / "matching_synthetic.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()
    assert matching.main(["parity", "--check", "--output", str(path)]) == 0
    document["background_percentile"][0]["expected"]["value"] = 35.000000001
    path.write_text(json.dumps(document), encoding="utf-8")
    assert matching.main(["parity", "--check", "--output", str(path)]) == 1
    assert path.read_bytes() != before  # only this test wrote it
    assert sorted(p.name for p in tmp_path.iterdir()) == ["matching_synthetic.json"]


def test_cli_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit) as error:
        matching.main([])
    assert error.value.code == 2
