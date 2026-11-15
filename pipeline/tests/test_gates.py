"""gates.py: merge, verify, snapshot and attest-g0 on tmp_path trees (CONTRACTS d, c.3)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from terraluna_pipeline import gates, provenance
from terraluna_pipeline.schema import GateRecord

REPO = Path(__file__).resolve().parents[2]

CONFIG_C = "run:\n  shipping_mode: C\n"


def _tree(tmp_path: Path, config: str = CONFIG_C) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "run.yaml").write_text(config, encoding="utf-8")
    (tmp_path / "outputs" / "g0").mkdir(parents=True)
    (tmp_path / "outputs" / "g0" / "windows-evidence.json").write_text("{}\n", encoding="utf-8")
    return tmp_path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(root: Path, gate_id: str, status: str = "PARTIAL", **overrides: object) -> GateRecord:
    evidence = root / "outputs" / "gates" / f"{gate_id.lower()}_evidence.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    if not evidence.exists():
        evidence.write_text('{"mock": false}\n', encoding="utf-8")
    rel = evidence.relative_to(root).as_posix()
    document: dict[str, object] = {
        "gate_id": gate_id,
        "status": status,
        "evidence_path": rel,
        "run_id": provenance.run_id(root / "config/run.yaml", [("p", None)]),
        "scope": f"synthetic scope for {gate_id}",
        "result": "synthetic result",
        "consequence": None,
        "command": f"python -m synthetic {gate_id}",
        "config_version": provenance.config_version(root / "config/run.yaml"),
        "input_hashes": [{"path": rel, "sha256": _sha(evidence)}],
    }
    document.update(overrides)
    return GateRecord.model_validate_json(json.dumps(document))


def test_no_sources_gives_seven_not_run(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    records = gates.merged_records(root)
    assert [r.gate_id for r in records] == list(gates.GATE_IDS)
    assert all(r.status == "NOT_RUN" and r.input_hashes == [] for r in records)
    assert all(r.command is None and r.config_version is None for r in records)


def test_recording_g4_leaves_other_entries_byte_identical(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    gates.write_fragment(_record(root, "G2"), root=root)
    before = json.loads(gates.merge_bytes(root))
    gates.write_fragment(_record(root, "G4"), root=root)
    after = json.loads(gates.merge_bytes(root))
    for index in (0, 1, 2, 3, 5, 6):
        assert before[index] == after[index]
    assert after[4]["status"] == "PARTIAL"


def test_fragment_gate_id_must_match_file_name(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    path = gates.write_fragment(_record(root, "G3"), root=root)
    path.rename(path.with_name("G2.json"))
    with pytest.raises(gates.GateError, match=r"G2\.json"):
        gates.merged_records(root)
    assert gates.main(["--root", str(root), "merge"]) == 1


def test_pass_without_run_id_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    good = _record(root, "G2", "PASS").model_dump(mode="json")
    good["run_id"] = None
    target = root / "outputs" / "gates" / "G2.json"
    target.write_bytes(provenance.canonical_json(good))
    with pytest.raises(gates.GateError, match="G2"):
        gates.merged_records(root)


def test_mock_evidence_is_refused(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    evidence = root / "outputs" / "gates" / "g3_evidence.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"mock": true}\n', encoding="utf-8")
    gates.write_fragment(_record(root, "G3"), root=root)
    with pytest.raises(gates.GateError, match="mock true"):
        gates.merged_records(root)


def test_evidence_under_mock_bundle_is_refused(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    evidence = root / "fixtures" / "mock-bundle" / "x.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"mock": false}\n', encoding="utf-8")
    rel = "fixtures/mock-bundle/x.json"
    record = _record(
        root, "G3", evidence_path=rel, input_hashes=[{"path": rel, "sha256": _sha(evidence)}]
    )
    gates.write_fragment(record, root=root)
    with pytest.raises(gates.GateError, match="mock-bundle"):
        gates.merged_records(root)


def test_g0_pass_without_attestation_fails(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    gates.write_fragment(_record(root, "G0", "PASS"), root=root)
    with pytest.raises(gates.GateError, match="attestation"):
        gates.merged_records(root)


def test_attestation_mode_must_equal_config(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    with pytest.raises(gates.GateError, match="differs"):
        gates.attest_g0("B", "synthetic operator", root=root)
    assert not (root / gates.G0_ATTESTATION).exists()
    null_root = tmp_path / "null"
    null_root.mkdir()
    _tree(null_root, "run:\n  shipping_mode: null\n")
    with pytest.raises(gates.GateError, match="null"):
        gates.attest_g0("C", "synthetic operator", root=null_root)


def test_attest_requires_the_confirm_string(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    argv = ["--root", str(root), "attest-g0", "--mode", "C", "--operator", "x", "--confirm", "no"]
    assert gates.main(argv) == 2
    assert not (root / "outputs" / "gates").exists()


def test_attested_g0_merges_and_verifies(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    record = gates.attest_g0("C", "synthetic operator", root=root)
    assert record.status == "PASS" and record.run_id is not None
    gates.merge(root)
    assert gates.verify(root) == []
    # A later config edit makes the attestation stale.
    (root / "config" / "run.yaml").write_text(CONFIG_C + "# edit\n", encoding="utf-8")
    problems = gates.verify(root)
    assert any("stale_run_id" in p for p in problems)


def test_editing_an_input_makes_verify_fail_and_names_the_gate(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    gates.write_fragment(_record(root, "G2"), root=root)
    gates.merge(root)
    assert gates.verify(root) == []
    (root / "outputs" / "gates" / "g2_evidence.json").write_text('{"mock": false, "x": 1}\n')
    problems = gates.verify(root)
    assert any(p.startswith("G2:") and "changed" in p for p in problems)


def test_snapshot_never_copies_another_bundles_g6(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    gates.write_fragment(_record(root, "G6", scope="bundle old-version"), root=root)
    gates.merge(root)
    snap = json.loads(gates.snapshot_bytes("2026-11-15T0900Z-3f9c2a1", root))
    assert len(snap) == 7
    assert snap[6]["status"] == "NOT_RUN"
    assert snap[6]["scope"] == gates.G6_SNAPSHOT_SCOPE.format(v="2026-11-15T0900Z-3f9c2a1")
    assert json.loads(gates.merge_bytes(root))[:6] == snap[:6]


def test_no_wall_clock_and_merge_is_deterministic(tmp_path: Path) -> None:
    source = Path(gates.__file__).read_text(encoding="utf-8")
    for needle in ("datetime.now", "time.time", "utcnow"):
        assert needle not in source
    root = _tree(tmp_path)
    gates.write_fragment(_record(root, "G2"), root=root)
    assert gates.merge_bytes(root) == gates.merge_bytes(root)


def test_no_automation_runs_attest_g0() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    recipes = [line for line in makefile.splitlines() if line.startswith("\t")]
    assert not any("attest-g0" in line for line in recipes)
    for workflow in (REPO / ".github").rglob("*.y*ml") if (REPO / ".github").exists() else []:
        assert "attest-g0" not in workflow.read_text(encoding="utf-8")
    for prd in (REPO / "scripts" / "ralph").rglob("prd.json"):
        document = json.loads(prd.read_text(encoding="utf-8"))
        for story in document.get("userStories", []):
            for check in story.get("checks", []) or []:
                assert not re.search(r"attest-g0", json.dumps(check))


def test_not_run_records_validate(tmp_path: Path) -> None:
    record = gates.not_run("G5")
    round_trip = GateRecord.model_validate_json(record.model_dump_json())
    assert round_trip == record and round_trip.input_hashes == []


def test_g1_pass_with_run_id_merges_unchanged(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    record = _record(root, "G1", "PASS")
    target = root / gates.G1_RECORD
    target.parent.mkdir(parents=True)
    data = provenance.canonical_json(record.model_dump(mode="json"))
    target.write_bytes(data)
    merged = json.loads(gates.merge_bytes(root))
    assert provenance.canonical_json(merged[1]) == data
