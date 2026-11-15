"""Pareto shortlist, sort rule and trim (T4L-04). All records are synthetic."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import pytest

from terraluna_pipeline.matching import (
    MatchReason,
    pareto_shortlist,
    trim_comparisons,
)
from terraluna_pipeline.schema import ComparisonRecord, GateStatus

if TYPE_CHECKING:  # mypy's name for the builders; pytest puts pipeline/tests on sys.path
    from pipeline.tests.test_matching_builders import comparison
else:
    from test_matching_builders import comparison


def hand_records(model: bool = False) -> list[ComparisonRecord]:
    points = {"earth-a": (1.0, 3.0), "earth-b": (2.0, 2.0), "earth-c": (3.0, 1.0)}
    points["earth-d"] = (3.0, 3.0)
    # Model values that would reverse the order if they ranked.
    models = {"earth-a": 0.9, "earth-b": 0.8, "earth-c": 0.7, "earth-d": 0.0}
    return [
        comparison(w, radar=r, relief=t, model=models[w] if model else None)
        for w, (r, t) in points.items()
    ]


def test_hand_case_layers() -> None:
    result = pareto_shortlist(hand_records(), "NOT_RUN")
    assert result.families == ("terrain_relief", "radar_cpr")
    assert result.layers == (("earth-a", "earth-b", "earth-c"), ("earth-d",))
    assert result.shortlist == ("earth-a", "earth-b", "earth-c")
    assert result.sort_rule == "pareto layers over terrain_relief, radar_cpr; then window_id"
    assert result.reason is None


def test_null_radar_is_not_ranked_on_radar() -> None:
    records = [*hand_records(), comparison("earth-e", relief=0.5)]
    result = pareto_shortlist(records, None)
    assert result.families == ("terrain_relief",)
    assert result.not_comparable["earth-e"] == ("terrain_texture", "radar_cpr")
    assert result.layers[0] == ("earth-e",)


def test_no_family_for_every_candidate_gives_an_explicit_reason() -> None:
    records = [comparison("earth-a", relief=1.0), comparison("earth-b", radar=1.0)]
    result = pareto_shortlist(records, None)
    assert result.layers == () and result.order == () and result.shortlist == ()
    assert result.reason == MatchReason.NO_RANKING_FAMILY


@pytest.mark.parametrize("status", ["NOT_RUN", "FAIL", "PARTIAL", "NOT_APPLICABLE", None])
def test_encoder_never_ranks_without_g5_pass(status: GateStatus | None) -> None:
    plain = pareto_shortlist(hand_records(), status)
    with_model = pareto_shortlist(hand_records(model=True), status)
    assert with_model.layers == plain.layers
    assert with_model.families == plain.families
    assert "model_embedding" not in with_model.families
    with pytest.raises(ValueError, match="cannot rank"):
        pareto_shortlist(hand_records(model=True), status, first_family="model_embedding")


def test_g5_pass_includes_the_encoder_family() -> None:
    result = pareto_shortlist(hand_records(model=True), "PASS")
    assert result.families == ("terrain_relief", "radar_cpr", "model_embedding")
    assert "earth-d" in result.layers[0]


def test_named_first_family_sorts_then_window_id() -> None:
    records = [*hand_records(), comparison("earth-0", radar=2.0, relief=9.0)]
    result = pareto_shortlist(records, None, first_family="radar_cpr")
    assert result.order == ("earth-a", "earth-0", "earth-b", "earth-c", "earth-d")
    assert result.sort_rule == "radar_cpr ascending; then window_id"
    with pytest.raises(ValueError, match="not available"):
        pareto_shortlist(records, None, first_family="terrain_texture")


def test_shuffling_does_not_change_the_result() -> None:
    records = hand_records()
    expected = pareto_shortlist(records, None)
    rng = random.Random(7)
    for _ in range(5):
        rng.shuffle(records)
        assert pareto_shortlist(records, None) == expected


def many(count: int) -> list[ComparisonRecord]:
    return [
        comparison(f"earth-w{i:04d}", relief=float((i * 37) % 101), texture=float((i * 53) % 89))
        for i in range(count)
    ]


def test_trim_keeps_the_shortlist_and_the_declared_order() -> None:
    records = many(600)
    shortlist = ["earth-w0599", "earth-w0300", "earth-w0007"]
    kept, report = trim_comparisons(records, shortlist, "NOT_RUN")
    assert report.n_input == 600 and report.n_kept == 500 and report.n_dropped == 100
    ids = [r.earth_window_id for r in kept]
    assert ids[:3] == sorted(shortlist)
    ranking = pareto_shortlist(records, "NOT_RUN")
    rest = [w for w in ranking.order if w not in shortlist]
    assert ids[3:] == rest[:497]
    assert report.dropped_window_ids == tuple(rest[497:])
    assert report.sort_rule == (
        "shortlist; then pareto layers over terrain_relief, terrain_texture; then window_id"
    )
    line = kept[0].claim_limits[-1]
    assert "100 dropped" in line and all(r.claim_limits[-1] == line for r in kept)
    assert all(r.sort_rule == report.sort_rule for r in kept)
    shuffled = list(records)
    random.Random(3).shuffle(shuffled)
    assert trim_comparisons(shuffled, shortlist, "NOT_RUN") == (kept, report)


def test_trim_adds_no_line_when_nothing_is_dropped_and_refuses_a_long_shortlist() -> None:
    records = many(10)
    kept, report = trim_comparisons(records, [], None)
    assert report.n_dropped == 0
    assert all(r.claim_limits == ["synthetic test record"] for r in kept)
    with pytest.raises(ValueError, match="exceeds"):
        trim_comparisons(records, [r.earth_window_id for r in records[:4]], None, limit=3)
