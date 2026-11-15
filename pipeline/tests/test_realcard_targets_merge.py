"""realcard search must replace only its own target entry in targets.json."""

import json
from pathlib import Path

from terraluna_pipeline import realcard
from terraluna_pipeline.schema import TargetRecord

REAL = Path(__file__).resolve().parents[2] / "outputs" / "search" / "targets.json"


def _records() -> list[TargetRecord]:
    return [TargetRecord.model_validate(r) for r in json.loads(REAL.read_text())]


def test_merge_keeps_foreign_targets_and_replaces_own(tmp_path: Path) -> None:
    records = _records()
    path = tmp_path / "targets.json"
    moon = next(r for r in records if r.target_id == realcard.TARGET_ID)
    others = [r for r in records if r.target_id != realcard.TARGET_ID]
    assert others, "fixture needs non-moon targets"
    realcard.write_targets_merged(path, [*others, moon])
    changed = TargetRecord.model_validate({**moon.model_dump(), "description": "replaced"})
    realcard.write_targets_merged(path, [changed])
    out = json.loads(path.read_text())
    ids = [r["target_id"] for r in out]
    assert ids == sorted(r.target_id for r in records)
    by_id = {r["target_id"]: r for r in out}
    assert by_id[realcard.TARGET_ID]["description"] == "replaced"
    for o in others:
        assert by_id[o.target_id]["description"] == o.description


def test_merge_into_missing_file(tmp_path: Path) -> None:
    moon = next(r for r in _records() if r.target_id == realcard.TARGET_ID)
    path = tmp_path / "t.json"
    realcard.write_targets_merged(path, [moon])
    assert [r["target_id"] for r in json.loads(path.read_text())] == [realcard.TARGET_ID]
