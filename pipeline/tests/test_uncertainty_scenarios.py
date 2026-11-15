"""T6-02: scenario runs, separate components and rank frequency. Synthetic distance tables."""

from __future__ import annotations

import pytest

from terraluna_pipeline import uncertainty
from terraluna_pipeline.schema import UncertaintyComponents

SCENARIOS = [
    {
        "name": "mask_strict",
        "change": "WorldCover allowed fraction 0.95 instead of 0.9",
        "values_by_family": {"terrain_relief": 0.41, "terrain_texture": 0.38, "radar_cpr": 0.2},
        "rank_by_family": {"terrain_relief": 1, "terrain_texture": 2, "radar_cpr": 1},
    },
    {
        "name": "band_assumption",
        "change": "treat L-band and S-band CPR as comparable",
        "values_by_family": {"radar_cpr": 0.1},
        "rank_by_family": {"radar_cpr": 1},
    },
]


def _components(**overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "body": "moon",
        "heterogeneity": {"terrain_relief": [1.0, 2.0, 3.0]},
        "clone_distances": {"terrain_relief": [0.4, 0.5, 0.45], "terrain_texture": [0.3, 0.31]},
        "clone_n_requested": 100,
        "scenarios": SCENARIOS,
        "rank_frequency_by_family": {"terrain_relief": {"frequency": 0.83, "count": 83, "n": 100}},
        "shipping_mode": "C",
    }
    arguments.update(overrides)
    return uncertainty.summarize_uncertainty(**arguments)  # type: ignore[arg-type]


def test_validates_as_uncertainty_components_with_four_keys() -> None:
    result = _components()
    assert set(result) == {
        "heterogeneity",
        "measurement_error",
        "statistical_prediction_error",
        "scenario_sensitivity",
    }
    UncertaintyComponents.model_validate(result)


def _walk_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys |= _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            keys |= _walk_keys(item)
    return keys


def test_no_combined_scalar_and_no_probability() -> None:
    keys = _walk_keys(_components())
    for forbidden in ("score", "combined", "total", "probability", "weight"):
        assert not any(forbidden in key for key in keys), forbidden


def test_prediction_error_unavailable_without_predictor() -> None:
    component = _components()["statistical_prediction_error"]
    assert component["status"] == "unavailable"  # type: ignore[index]
    assert component["note"] == "no predictor evaluated"  # type: ignore[index]


@pytest.mark.parametrize("body", ["earth", "mars"])
def test_measurement_error_unavailable_off_the_moon(body: str) -> None:
    component = _components(body=body)["measurement_error"]
    assert component["status"] == "unavailable"  # type: ignore[index]
    assert component["reason"] == "no_published_clone_set"  # type: ignore[index]


def test_missing_clone_results_are_unavailable_not_zero() -> None:
    component = _components(clone_distances=None)["measurement_error"]
    assert component["status"] == "unavailable"  # type: ignore[index]
    assert component["by_family"] is None  # type: ignore[index]


def test_measurement_error_is_terrain_only() -> None:
    component = _components(
        clone_distances={"terrain_relief": [0.1, 0.2], "radar_cpr": [0.3, 0.4]}
    )["measurement_error"]
    assert set(component["by_family"]) == {"terrain_relief"}  # type: ignore[index]
    assert component["n_requested"] == 100  # type: ignore[index]


def test_rank_frequency_is_a_count_over_runs_and_absent_is_not_last() -> None:
    first: dict[str, int | None] = {"a": 1, "b": 2}
    second: dict[str, int | None] = {"b": 1}
    runs = [first] * 83 + [second] * 17
    result = uncertainty.rank_frequency(runs, candidate="a")
    assert result == {"frequency": 0.83, "count": 83, "n": 100}
    assert uncertainty.rank_frequency(runs, candidate="b", rank=1)["count"] == 17


def test_mode_c_drops_radar_and_skips_band_scenario() -> None:
    scen = _components()["scenario_sensitivity"]
    names = [s["name"] for s in scen["scenarios"]]  # type: ignore[index]
    assert "band_assumption" not in names
    for entry in scen["scenarios"]:  # type: ignore[index]
        assert "radar_cpr" not in entry["values_by_family"]
        assert "radar_cpr" not in entry["rank_by_family"]
    assert "no cross-band radar match" in scen["note"]  # type: ignore[index]


def test_mode_a_keeps_radar_family_separate() -> None:
    scen = _components(shipping_mode="A")["scenario_sensitivity"]
    names = [s["name"] for s in scen["scenarios"]]  # type: ignore[index]
    assert names == ["mask_strict", "band_assumption"]
    assert scen["scenarios"][0]["values_by_family"]["radar_cpr"] == 0.2  # type: ignore[index]


def test_encoder_excluded_from_rank_frequency_until_g5_pass() -> None:
    freq = {
        "terrain_relief": {"frequency": 0.5, "count": 1, "n": 2},
        "model_embedding": {"frequency": 1.0, "count": 2, "n": 2},
    }
    scen = _components(rank_frequency_by_family=freq)["scenario_sensitivity"]
    assert set(scen["rank_frequency"]) == {"terrain_relief"}  # type: ignore[index]
    scen = _components(rank_frequency_by_family=freq, g5_status="PASS")["scenario_sensitivity"]
    assert set(scen["rank_frequency"]) == {"terrain_relief", "model_embedding"}  # type: ignore[index]


def test_heterogeneity_is_a_distribution_not_a_confidence_interval() -> None:
    het = _components()["heterogeneity"]
    assert "not a confidence interval" in het["note"]  # type: ignore[index]
    assert het["by_family"]["terrain_relief"]["spread_median"] == 2.0  # type: ignore[index]
