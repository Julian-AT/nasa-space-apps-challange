"""T6-09: uncertainty written into comparison records (synthetic G4 results)."""

from __future__ import annotations

import json
from typing import Any

from terraluna_pipeline import uncertainty
from terraluna_pipeline.schema import UncertaintyComponents


def _row(wid: str, relief: float, allowed: float, region: str) -> dict[str, Any]:
    dist = {"base": relief, "min": relief - 0.01, "median": relief, "max": relief + 0.01, "n": 20}
    return {
        "window_id": wid,
        "terrain_relief": dist,
        "terrain_texture": dict(dist),
        "valid_fraction_by_observable": {"worldcover_allowed": allowed},
        "search_region_id": region,
        "rank_frequency": {
            "first_pareto_layer": {"frequency": 1.0, "count": 20, "n": 20},
            "rank_1_terrain_relief": {"frequency": 0.85, "count": 17, "n": 20},
            "rank_1_terrain_texture": {"frequency": 0.85, "count": 17, "n": 20},
        },
    }


G4: dict[str, Any] = {
    "candidates": [_row("a", 1.0, 0.92, "r1"), _row("b", 2.0, 1.0, "r2")],
    "rank_sensitivity": {"n_runs": 20, "n_requested": 100},
    "scenario_sensitivity": [
        {"name": "pipeline", "change": "config masks"},
        {"name": "worldcover_allowed_ge_0.95", "change": "stricter"},
        {"name": "region_r2_only", "change": "r2 only"},
    ],
}


def test_components_validate_and_stay_separate() -> None:
    components = uncertainty.annotated_components(G4, "a")
    UncertaintyComponents.model_validate(components)
    measurement = components["measurement_error"]
    assert measurement["n_used"] == 20 and measurement["n_requested"] == 100
    assert set(measurement["by_family"]) == {"terrain_relief", "terrain_texture"}
    assert "radar calibration" in measurement["note"]
    assert components["statistical_prediction_error"]["status"] == "unavailable"
    text = json.dumps(components).lower()
    assert "score" not in text and "combined" not in text and "probability of" not in text


def test_scenario_ranks_and_absence() -> None:
    scen = {
        s["name"]: s
        for s in uncertainty.annotated_components(G4, "a")["scenario_sensitivity"]["scenarios"]
    }
    assert scen["pipeline"]["rank_by_family"]["terrain_relief"] == 1
    assert scen["worldcover_allowed_ge_0.95"]["rank_by_family"]["terrain_relief"] is None
    assert scen["worldcover_allowed_ge_0.95"]["values_by_family"]["terrain_relief"] is None
    rank_freq = uncertainty.annotated_components(G4, "a")["scenario_sensitivity"]["rank_frequency"]
    assert rank_freq["terrain_relief"] == {"frequency": 0.85, "count": 17, "n": 20}


def test_ineligible_window_is_unavailable_not_zero() -> None:
    components = uncertainty.annotated_components(G4, "zzz")
    assert components["measurement_error"]["status"] == "unavailable"
    assert components["measurement_error"]["by_family"] is None


def test_annotate_replaces_the_stale_claim_and_keeps_order() -> None:
    records = [
        {"earth_window_id": "b", "claim_limits": [uncertainty.STALE_CLAIM, "x"]},
        {"earth_window_id": "a", "claim_limits": ["x"]},
    ]
    out = uncertainty.annotate_records(records, G4)
    assert [r["earth_window_id"] for r in out] == ["b", "a"]
    assert out[0]["claim_limits"] == [uncertainty.CLONE_CLAIM, "x"]
    assert out[1]["claim_limits"] == ["x", uncertainty.CLONE_CLAIM]
    assert uncertainty.annotate_records(out, G4) == out
