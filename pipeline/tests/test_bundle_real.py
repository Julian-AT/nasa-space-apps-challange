"""The committed real card and comparison agree with config and with each other (T4R-04).

Reads only committed records, so it runs in CI without the raw-data cache.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from terraluna_pipeline import export, provenance
from terraluna_pipeline.schema import CardRecord, ComparisonRecord, ProductRecord

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "run.yaml"
CARDS = sorted((REPO / "outputs" / "cards").glob("*.json"))
COMPARISONS = sorted(
    p for p in (REPO / "outputs" / "comparisons").glob("*.json") if p.name != "sources.json"
)


def _config() -> dict[str, object]:
    document = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _mode() -> object:
    run = _config()["run"]
    assert isinstance(run, dict)
    return run.get("shipping_mode")


def _primary() -> object:
    targets = _config()["targets"]
    assert isinstance(targets, dict) and isinstance(targets["moon"], dict)
    return targets["moon"].get("primary_window_id")


def _target_windows() -> set[str]:
    """The Moon primary window and every Mars target's primary window (config targets)."""
    targets = _config()["targets"]
    assert isinstance(targets, dict)
    found = {str(_primary())}
    mars = targets.get("mars")
    assert isinstance(mars, list)
    found.update(
        str(entry["primary_window_id"])
        for entry in mars
        if isinstance(entry, dict) and entry.get("primary_window_id") is not None
    )
    return found


def _comparisons() -> list[ComparisonRecord]:
    records: list[ComparisonRecord] = []
    for path in COMPARISONS:
        records.extend(
            ComparisonRecord.model_validate(item) for item in json.loads(path.read_text())
        )
    return records


def _sources() -> dict[str, ProductRecord]:
    merged, _ = export.merge_sources(export.source_fragments(REPO), REPO)
    return {record.product_id: record for record in merged}


def test_no_real_card_while_decisions_are_open() -> None:
    if _mode() is not None and _primary() is not None:
        pytest.skip("G0-01 and G0-02 are decided")
    assert CARDS == [] and COMPARISONS == []


@pytest.mark.parametrize("path", CARDS, ids=[p.stem for p in CARDS])
def test_real_card_matches_config_and_comparison(path: Path) -> None:
    card = CardRecord.model_validate_json(path.read_bytes())
    assert card.mock is False
    assert card.shipping_mode == _mode()
    assert card.target.target_window_id in _target_windows()
    matches = [
        c
        for c in _comparisons()
        if (c.target_window_id, c.earth_window_id)
        == (card.target.target_window_id, card.earth.earth_window_id)
    ]
    assert len(matches) == 1
    comparison = matches[0]
    # Every number on the card is the serialized comparison value (PRD 12 end-to-end check).
    assert card.distances_by_family.model_dump(mode="json") == (
        comparison.distances_by_family.model_dump(mode="json")
    )
    if card.shipping_mode == "C":
        assert card.distances_by_family.radar_cpr.value is None
        assert "radar comparison not completed" in card.title
    assert card.distances_by_family.model_embedding.reason == "encoder_not_evaluated"
    for entry in (card.q6_campaign.access, card.q6_campaign.permits, card.q6_campaign.season):
        assert (entry.value == "unknown") == (entry.source is None)
    # Moon cards carry the full difference list; Mars cards have no permanent-shadow or
    # radar-geometry limits of their own, so they list gravity, atmosphere, resolution and the
    # two unavailable families.
    minimum = 7 if card.target.target_window_id == _primary() else 5
    assert len(card.q4_differences.limits) >= minimum


@pytest.mark.parametrize("path", CARDS, ids=[p.stem for p in CARDS])
def test_card_run_id_is_scoped_over_its_sources(path: Path) -> None:
    card = CardRecord.model_validate_json(path.read_bytes())
    assert card.run_id == export.recompute_card_run_id(card, _sources(), CONFIG)


def test_comparison_run_ids_are_fresh() -> None:
    sources = _sources()
    for comparison in _comparisons():
        products = [sources[pid] for pid in comparison.source_ids]
        assert comparison.run_id == provenance.run_id(CONFIG, products), comparison.earth_window_id
