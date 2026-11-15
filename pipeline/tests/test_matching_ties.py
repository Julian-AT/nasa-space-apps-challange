"""Ties, reference spread, background sample and percentile fill (T4L-05). Synthetic inputs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from terraluna_pipeline.config import DEFAULT_RUN_CONFIG
from terraluna_pipeline.matching import (
    BACKGROUND_STATEMENT,
    BackgroundSampleSizeUnsetError,
    Histogram,
    MatchReason,
    Outcome,
    background_sample,
    distance_entry,
    percentile_fields,
    reference_spread,
    sample_rank,
    tie_groups,
    uncertainty_components,
    with_background_percentile,
    with_reference_spread,
    with_tie,
)
from terraluna_pipeline.schema import (
    RankFrequency,
    Scenario,
    UncertaintyComponent,
    UncertaintyComponents,
)

if TYPE_CHECKING:  # mypy's name for the builders; pytest puts pipeline/tests on sys.path
    from pipeline.tests.test_matching_builders import comparison
else:
    from test_matching_builders import comparison


def test_ties_use_a_strict_sum_of_spreads() -> None:
    result = tie_groups("terrain_relief", {"a": 1.0, "b": 1.5}, {"a": 0.25, "b": 0.25})
    assert result.by_window["a"].tied is False and result.groups == ()
    result = tie_groups("terrain_relief", {"a": 1.0, "b": 1.25}, {"a": 0.25, "b": 0.25})
    assert result.by_window["a"].tied is True
    assert result.by_window["a"].tie_group == result.by_window["b"].tie_group


def test_chains_are_visible() -> None:
    result = tie_groups(
        "radar_cpr", {"a": 1.0, "b": 1.375, "c": 1.75}, {"a": 0.25, "b": 0.25, "c": 0.25}
    )
    (group,) = result.groups
    assert group.members == ("a", "b", "c")
    assert group.not_pairwise_tied == (("a", "c"),)
    assert group.group_id == "radar_cpr:a"


def test_no_spread_claims_no_tie() -> None:
    result = tie_groups("terrain_texture", {"a": 1.0, "b": 1.0, "c": None}, {"a": 0.5})
    assert result.by_window["b"].tied is None
    assert result.by_window["b"].tie_reason == MatchReason.SPREAD_UNAVAILABLE
    assert result.by_window["c"].tie_reason == MatchReason.DISTANCE_UNAVAILABLE
    assert result.by_window["a"].tied is False


def test_reference_spread_and_its_reason() -> None:
    spread, reason = reference_spread([0.5, 0.125, 0.25, 1.0])
    assert reason is None and spread is not None
    assert (spread.min, spread.median, spread.max, spread.n) == (0.125, 0.375, 1.0, 4)
    odd, _ = reference_spread([3.0, 1.0, 2.0])
    assert odd is not None and odd.median == 2.0
    assert reference_spread([1.0]) == (None, MatchReason.REFERENCE_SET_TOO_SMALL)
    entry = distance_entry("terrain_relief", Outcome(0.5, None))
    filled = with_reference_spread(entry, [0.25, 0.75])
    assert filled.reference_spread is not None and filled.reference_spread_reason is None
    tied = with_tie(filled, tie_groups("terrain_relief", {"x": 0.5}, {"x": 0.1}).by_window["x"])
    assert tied.tied is False


def test_uncertainty_components_stay_separate() -> None:
    scenario = Scenario(
        name="incidence bin",
        change="synthetic",
        values_by_family={"terrain_relief": 0.5},
        rank_by_family={"terrain_relief": 1},
    )
    sensitivity = UncertaintyComponent(
        status="available",
        evidence_label="Derived",
        reason=None,
        note=None,
        by_family=None,
        n_requested=None,
        n_used=None,
        scenarios=[scenario],
        rank_frequency={"terrain_relief": RankFrequency(frequency=0.75, count=3, n=4)},
    )
    components = uncertainty_components(scenario_sensitivity=sensitivity)
    assert set(UncertaintyComponents.model_fields) == {
        "heterogeneity",
        "measurement_error",
        "statistical_prediction_error",
        "scenario_sensitivity",
    }
    assert components.heterogeneity.reason == MatchReason.UNCERTAINTY_NOT_RUN
    assert set(Scenario.model_fields) == {"name", "change", "values_by_family", "rank_by_family"}
    text = components.model_dump_json().lower()
    assert "probability of validity" not in text and "probability" not in text
    assert '"frequency":0.75' in text


def test_background_sample_is_seeded_and_reproducible() -> None:
    universe = [(f"earth-w{i:03d}", "eligible") for i in range(20)]
    universe += [("earth-r1", "rejected"), ("earth-u1", "unknown"), ("earth-u2", "unknown")]
    sample = background_sample(universe, 5, seed=11)
    expected = sorted(
        sorted(
            (w for w, s in universe if s == "eligible"),
            key=lambda w: hashlib.sha256(f"11:{w}".encode()).hexdigest(),
        )[:5]
    )
    assert sample.sample_window_ids == tuple(expected)
    assert sample.requested_n == 5 and sample.actual_n == 5
    assert dict(sample.excluded_by_status) == {"rejected": 1, "unknown": 2}
    assert sample.statement == BACKGROUND_STATEMENT
    assert sample_rank(11, "earth-w000") == hashlib.sha256(b"11:earth-w000").hexdigest()
    small = background_sample(universe, 50, seed=11)
    assert small.requested_n == 50 and small.actual_n == 20


def test_background_sample_size_comes_from_config_or_blocks(tmp_path: Path) -> None:
    universe = [("earth-a", "eligible")]
    assert background_sample(universe, None, seed=1).requested_n == 1000
    unset = tmp_path / "run.yaml"
    text = DEFAULT_RUN_CONFIG.read_text(encoding="utf-8")
    unset.write_text(
        text.replace("background_sample_size: 1000", "background_sample_size: null"),
        encoding="utf-8",
    )
    with pytest.raises(BackgroundSampleSizeUnsetError, match="background_sample_size_unset"):
        background_sample(universe, None, seed=1, config_path=unset)


HISTOGRAM = Histogram("terrain_relief", "RMS of log nu", (0.0, 1.0, 2.0, 3.0), (2, 3, 5))


def test_percentile_fill_is_per_family() -> None:
    assert percentile_fields("terrain_relief", 1.5, HISTOGRAM) == (35.0, None)
    assert percentile_fields("model_embedding", 0.1, None) == (
        None,
        MatchReason.ENCODER_NOT_EVALUATED,
    )
    with pytest.raises(ValueError, match="never ranked against"):
        percentile_fields("terrain_texture", 1.5, HISTOGRAM)
    assert percentile_fields("terrain_relief", 1.5, HISTOGRAM, sample_size_unset=True) == (
        None,
        MatchReason.BACKGROUND_SAMPLE_SIZE_UNSET,
    )
    empty = Histogram("radar_cpr", "CPR Wasserstein-1", (0.0, 1.0), (0,))
    assert percentile_fields("radar_cpr", 0.5, empty) == (None, MatchReason.BACKGROUND_EMPTY)
    assert percentile_fields("radar_cpr", 0.5, None) == (None, MatchReason.BACKGROUND_MISSING)
    entry = with_background_percentile(
        distance_entry("terrain_relief", Outcome(1.0, None)), "terrain_relief", HISTOGRAM
    )
    assert entry.background_percentile == 20.0 and entry.background_percentile_reason is None


def test_equal_terrain_and_different_radar_change_only_radar() -> None:
    a = comparison("earth-a", relief=1.0, radar=0.5)
    b = comparison("earth-b", relief=1.0, radar=2.0)
    radar_hist = Histogram("radar_cpr", "CPR Wasserstein-1", (0.0, 1.0, 3.0), (4, 6))
    filled = {}
    for record in (a, b):
        relief = with_background_percentile(
            record.distances_by_family["terrain_relief"], "terrain_relief", HISTOGRAM
        )
        radar = with_background_percentile(
            record.distances_by_family["radar_cpr"], "radar_cpr", radar_hist
        )
        filled[record.earth_window_id] = (relief, radar)
    assert filled["earth-a"][0] == filled["earth-b"][0]
    assert filled["earth-a"][1] != filled["earth-b"][1]
