"""Tests for paper/scripts/tables.py."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.tables import HEADER, PAPER, TARGETS, distance_plot


def write(root: Path, records: list[dict[str, object]], shortlist: list[str]) -> None:
    (root / "outputs" / "search").mkdir(parents=True)
    (root / "outputs" / "comparisons").mkdir(parents=True)
    target = {"target_id": "moon-t", "shortlist": {"earth_window_ids": shortlist}}
    other = {"target_id": "mars-t", "shortlist": {"earth_window_ids": []}}
    (root / "outputs" / "search" / "targets.json").write_text(json.dumps([other, target]))
    (root / "outputs" / "comparisons" / "moon-t.json").write_text(json.dumps(records))


def record(window: str, relief: float | None, texture: float | None) -> dict[str, object]:
    return {
        "earth_window_id": window,
        "distances_by_family": {
            "terrain_relief": {"value": relief},
            "terrain_texture": {"value": texture},
        },
    }


def test_distance_plot_is_deterministic_and_skips_missing(tmp_path: Path) -> None:
    write(
        tmp_path,
        [
            record("earth-utm27n-a", 0.5, 0.6),
            record("earth-utm11n-b", 1.0, None),
            record("earth-utm11n-c", 2.0, 2.5),
        ],
        ["earth-utm27n-a"],
    )
    text = distance_plot(tmp_path, "moon-t")
    assert text == distance_plot(tmp_path, "moon-t")
    assert text.startswith(HEADER + "\n")
    assert "% Windows without both distances, left out: 1." in text
    assert "(0.5000,0.6000)" in text and "(2.0000,2.5000)" in text
    assert "(1.0000," not in text
    assert text.count("\\addplot") == 3


def test_committed_distance_plots_are_current() -> None:
    for target_id, stem in TARGETS:
        committed = (PAPER / "generated" / f"{stem}.tex").read_text("utf-8")
        assert committed == distance_plot(target_id=target_id)
