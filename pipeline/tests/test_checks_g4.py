"""T6-04: G4 candidate table, rank sensitivity over clones, ties and status logic (synthetic)."""

from __future__ import annotations

import json

import numpy as np

from terraluna_pipeline import checks, matching
from terraluna_pipeline.schema import TerrainVariant

LAGS = ["270", "360", "540", "720", "1080"]


def _variant(nus: list[float], detrend: str = "none") -> TerrainVariant:
    return TerrainVariant.model_validate(
        {
            "detrend": detrend,
            "supported_lags_m": [float(lag) for lag in LAGS],
            "unsupported_lags": [],
            "nu_m": dict(zip(LAGS, nus, strict=True)),
            "nu_by_direction_m": {},
            "hurst": 0.9,
            "hurst_reason": None,
            "tilt": None,
        }
    )


def _pair(scale: float) -> dict[str, TerrainVariant]:
    base = [10.0, 13.0, 19.0, 24.0, 33.0]
    return {
        "terrain_relief": _variant([scale * v for v in base]),
        "terrain_texture": _variant([0.9 * scale * v for v in base], "plane"),
    }


def _setup() -> tuple[
    dict[str, TerrainVariant],
    dict[str, dict[str, TerrainVariant]],
    dict[str, dict[str, TerrainVariant]],
]:
    target = _pair(1.0)
    rng = np.random.default_rng(5)
    clones = {f"{i:04d}": _pair(1.0 + 0.002 * float(rng.standard_normal())) for i in range(1, 21)}
    scales = {"c1": 0.5, "c2": 0.501, "c3": 0.7, "c4": 0.3, "c5": 0.69, "c6": 2.0}
    candidates = {cid: _pair(s) for cid, s in scales.items()}
    return target, clones, candidates


def test_rank_sensitivity_counts_runs_and_copies_terrain_distance() -> None:
    target, clones, candidates = _setup()
    result = checks.g4_rank_sensitivity(target, clones, candidates)
    assert result["n_runs"] == 20
    row = result["candidates"]["c3"]
    expected = matching.terrain_distance(
        target["terrain_relief"], candidates["c3"]["terrain_relief"]
    )
    assert row["distances"]["terrain_relief"]["base"] == expected.value
    freq = row["rank_frequency"]["first_pareto_layer"]
    assert freq["n"] == 20 and 0 <= freq["count"] <= 20
    assert freq["frequency"] == freq["count"] / 20


def test_ties_come_from_matching_tie_groups() -> None:
    target, clones, candidates = _setup()
    result = checks.g4_rank_sensitivity(target, clones, candidates)
    for family in ("terrain_relief", "terrain_texture"):
        entries = {c: r["distances"][family]["base"] for c, r in result["candidates"].items()}
        spreads = {
            c: r["distances"][family]["spread_half_width_95"]
            for c, r in result["candidates"].items()
        }
        expected = matching.tie_groups(family, entries, spreads)
        got = result["ties"][family]
        assert got["groups"] == [
            {
                "group_id": g.group_id,
                "members": list(g.members),
                "not_pairwise_tied": [list(p) for p in g.not_pairwise_tied],
            }
            for g in expected.groups
        ]


def test_families_stay_separate_and_no_composite() -> None:
    target, clones, candidates = _setup()
    text = json.dumps(checks.g4_rank_sensitivity(target, clones, candidates)).lower()
    for word in ("score", "combined", "composite", "probability", "valid analog", "confirmed"):
        assert word not in text


def test_g4_status_logic() -> None:
    full = {
        "table": True,
        "masks": True,
        "distances_and_rank_sensitivity": True,
        "counterexample": True,
    }
    assert checks.g4_status(full, t5_outputs=True)[0] == "PASS"
    partial = dict(full, counterexample=False)
    status, missing = checks.g4_status(partial, t5_outputs=True)
    assert status == "PARTIAL" and missing == ["counterexample"]
    assert checks.g4_status(full, t5_outputs=False)[0] == "NOT_RUN"


def test_invalid_windows_listed_separately_never_ranked() -> None:
    entries = [
        {"window": {"window_id": "a"}, "status": "eligible", "reasons": []},
        {
            "window": {"window_id": "b"},
            "status": "rejected",
            "reasons": ["land_cover_below_min_fraction"],
        },
        {"window": {"window_id": "c"}, "status": "unknown", "reasons": ["worldcover_missing"]},
    ]
    eligible, excluded = checks.split_windows(entries)
    assert eligible == ["a"]
    assert [x["window_id"] for x in excluded] == ["b", "c"]
    assert excluded[0]["reasons"] == ["land_cover_below_min_fraction"]


def test_counterexample_check_requires_existing_paths(tmp_path: object) -> None:
    from pathlib import Path

    root = Path(str(tmp_path))
    assert checks.counterexample_ok(root) == (False, "analysis/g4_counterexample.json is missing")
    (root / "analysis").mkdir()
    (root / "analysis" / "g4_counterexample.json").write_text(
        json.dumps({"candidate_id": "x", "inspected": "y", "artifact_paths": ["nope.txt"]})
    )
    ok, reason = checks.counterexample_ok(root)
    assert not ok and reason is not None and "nope.txt" in reason
    (root / "nope.txt").write_text("seen")
    assert checks.counterexample_ok(root) == (True, None)


def test_mask_scenarios_filter_and_recompute_first_layer() -> None:
    def row(
        wid: str, relief: float, texture: float, allowed: float, region: str
    ) -> dict[str, object]:
        return {
            "window_id": wid,
            "terrain_relief": {"base": relief},
            "terrain_texture": {"base": texture},
            "valid_fraction_by_observable": {"worldcover_allowed": allowed},
            "search_region_id": region,
        }

    table = [row("a", 1.0, 1.0, 0.92, "r1"), row("b", 2.0, 2.0, 1.0, "r2")]
    scenarios = {s["name"]: s for s in checks.mask_scenarios(table, base_threshold=0.9)}
    assert scenarios["pipeline"]["first_pareto_layer"] == ["a"]
    assert scenarios["worldcover_allowed_ge_0.95"]["first_pareto_layer"] == ["b"]
    assert scenarios["region_r1_only"]["n_eligible"] == 1
    assert "probability" not in json.dumps(scenarios)
