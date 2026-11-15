"""Tests for export_card, the record writers and ``export cards`` (T4L-06; PRD 3, 4, 10, 12).

Every record here is a synthetic test input built in memory or under pytest's tmp_path. None of
it is an observation or a result, and nothing is written outside tmp_path.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from terraluna_pipeline import export
from terraluna_pipeline.export import (
    EARTH_LIMITS,
    MARS_LIMITS,
    CardInputs,
    CardLimits,
    EncoderGuardError,
    FigureIndexEntry,
    MockRecordError,
    ShippingModeUnsetError,
    canonical_json,
    default_card_limits,
    export_card,
    main,
    recompute_card_run_id,
    write_cards,
    write_comparisons,
    write_targets,
    write_windows,
)
from terraluna_pipeline.schema import (
    CardRecord,
    ComparisonRecord,
    GateRecord,
    ProductRecord,
    TargetRecord,
    WindowEntry,
)

ROOT = Path(__file__).resolve().parents[2]
PRD = (ROOT / "PRD.md").read_text(encoding="utf-8")
HEX = "0123456789abcdef" * 4
HEX2 = "fedcba9876543210" * 4
RUN_ID = "sha256:" + HEX
TARGET_ID = "moon-cr-site01-w000"
MOON_WINDOW = "moon-sps-x2-y2"
EARTH_WINDOW = "earth-27n-holuhraun-0003-0004"
MOON_DEM = "PGDA78/Site01/Site01_final_adj_5mpp_surf.tif"
EARTH_DEM = "COP30/N64W017"
WORLDCOVER = "WorldCover/N63W018"
MINIRF = "MINIRF/lsz_xxxxx_3cp_piu_90s000_v1.img"
UAVSAR = "UAVSAR/Icelnd_00208_15083_004_150530_L090_CX_01"
FORBIDDEN = (
    "radar match: passed",
    "analog found",
    "best match",
    "AI-verified analog",
    "best place on Earth",
)


# --- Synthetic inputs ---------------------------------------------------------------------------


def closed(ring: list[list[float]]) -> list[list[float]]:
    return [*ring, ring[0]]


def moon_polygon() -> dict[str, Any]:
    return {
        "frame": "aeqd_sphere",
        "crs": "+proj=aeqd +lat_0=-89.375 +lon_0=11.375 +R=1737400 +units=m",
        "epsg": None,
        "utm_zone": None,
        "ring_m": closed([[-2500.0, -2500.0], [2500.0, -2500.0], [2500.0, 2500.0]]),
        "ring_lonlat": closed([[10.25, -89.5], [12.5, -89.5], [12.5, -89.25]]),
    }


def earth_polygon() -> dict[str, Any]:
    return {
        "frame": "utm",
        "crs": "EPSG:32627",
        "epsg": 32627,
        "utm_zone": "27N",
        "ring_m": closed([[410000.5, 7200000.5], [415000.5, 7200000.5], [415000.5, 7205000.5]]),
        "ring_lonlat": closed([[-16.75, 64.875], [-16.625, 64.875], [-16.625, 64.9375]]),
    }


def distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "values": ordered,
        "min": ordered[0],
        "median": ordered[len(ordered) // 2],
        "p05": ordered[0] + 0.0123456789,
        "p95": ordered[-1] - 0.0987654321,
        "max": ordered[-1],
    }


def radar_print(window_id: str, body: str, product_id: str, band: str) -> dict[str, Any]:
    return {
        "window_id": window_id,
        "body": body,
        "kind": "radar",
        "product_ids": [product_id],
        "evidence_label": "Derived",
        "input_hash": HEX,
        "run_id": RUN_ID,
        "terrain": None,
        "radar": {
            "product_id": product_id,
            "band": band,
            "frequency_or_wavelength": "12.6 cm" if band == "S" else "23.84 cm",
            "convention": None,
            "support_m": 250.0,
            "aggregation": "ratio_of_mean_powers",
            "blocks_independent": False,
            "n_blocks": 400,
            "n_valid": 3,
            "valid_fraction": 0.0075,
            "cpr_ratio_of_mean_powers": distribution([0.31234567, 0.4471, 0.9012345678]),
            "cpr_mean_of_pixelwise_ratios": None,
            "sc": distribution([1.25e-3, 2.5e-3, 7.75e-3]),
            "oc": None,
            "total_power": None,
            "incidence_deg": {"min": 35.5, "max": 48.25},
            "handedness_ratio": None,
            "reason": None,
        },
    }


def window(
    window_id: str, body: str, polygon: dict[str, Any], products: list[str], radar: list[Any]
) -> dict[str, Any]:
    return {
        "window": {
            "window_id": window_id,
            "body": body,
            "polygon": polygon,
            "area_m2": 25000000.0,
            "analysis_grid_m": 90.0,
            "product_ids": products,
            "valid_fraction_by_observable": {"terrain": 0.875},
            "observed_interpolated_quality": {
                "observed_fraction": None,
                "interpolated_fraction": None,
                "source": None,
                "reason": "not_applicable_body",
            },
            "environment_flags": {
                "search_region_id": "holuhraun" if body == "earth" else None,
                "scene_ids": [],
                "acquisition_dates": [],
                "snow_or_freeze": "unknown" if body == "earth" else None,
                "moisture_state": "unknown" if body == "earth" else None,
                "surface_change_since_acquisition": None,
                "permanent_shadow_fraction": None,
                "surface_model_note": None,
            },
            "evidence_status_by_field": {"terrain": "Derived"},
        },
        "terrain": None,
        "radar": radar,
        "status": "eligible",
        "reasons": [],
        "grid_index": [2, 2],
        "card_id": None,
    }


def moon_entry(**overrides: Any) -> dict[str, Any]:
    value = window(
        MOON_WINDOW,
        "moon",
        moon_polygon(),
        [MOON_DEM],
        [radar_print(MOON_WINDOW, "moon", MINIRF, "S")],
    )
    value.update(overrides)
    return value


def earth_entry(**overrides: Any) -> dict[str, Any]:
    value = window(
        EARTH_WINDOW,
        "earth",
        earth_polygon(),
        [EARTH_DEM, WORLDCOVER],
        [radar_print(EARTH_WINDOW, "earth", UAVSAR, "L")],
    )
    value.update(overrides)
    return value


def distance(value: float, unit: str = "RMS of log nu", **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "value": value,
        "unit": unit,
        "evidence_label": "Derived",
        "status": "available",
        "reason": None,
        "reference_spread": {"min": 0.0123, "median": 0.0456789, "max": 0.0987654321, "n": 12},
        "reference_spread_reason": None,
        "background_percentile": 12.3456789,
        "background_percentile_reason": None,
        "tied": False,
        "tie_group": None,
        "tie_reason": None,
    }
    entry.update(overrides)
    return entry


def missing(reason: str, unit: str, status: str = "unavailable") -> dict[str, Any]:
    return {
        "value": None,
        "unit": unit,
        "evidence_label": "Unavailable",
        "status": status,
        "reason": reason,
        "reference_spread": None,
        "reference_spread_reason": "spread_unavailable",
        "background_percentile": None,
        "background_percentile_reason": "background_sample_size_unset",
        "tied": None,
        "tie_group": None,
        "tie_reason": "spread_unavailable",
    }


def component(status: str = "unavailable", **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "status": status,
        "evidence_label": "Unavailable" if status == "unavailable" else "Derived",
        "reason": "search_not_run" if status == "unavailable" else None,
        "note": None,
        "by_family": None,
        "n_requested": None,
        "n_used": None,
        "scenarios": None,
        "rank_frequency": None,
    }
    value.update(overrides)
    return value


def comparison(mode: str | None = "C", relief: float = 0.123456789, **overrides: Any) -> Any:
    radar = (
        distance(0.0321987654, unit="CPR Wasserstein-1")
        if mode == "A"
        else missing("radar_not_completed" if mode == "C" else "band_mismatch", "CPR W1")
    )
    value: dict[str, Any] = {
        "target_window_id": MOON_WINDOW,
        "earth_window_id": EARTH_WINDOW,
        "shipping_mode": mode,
        "comparable_observables": ["terrain_relief", "terrain_texture"],
        "excluded_observables_and_reasons": [
            {"observable": "radar_cpr", "reasons": ["radar_not_completed"]}
        ],
        "distances_by_family": {
            "terrain_relief": distance(relief),
            "terrain_texture": distance(0.0, tied=True, tie_group="g1"),
            "radar_cpr": radar,
            "model_embedding": missing("g5_not_pass", "cosine distance", "not_comparable"),
        },
        "uncertainty_components": {
            "heterogeneity": component(),
            "measurement_error": component(
                "available",
                note="clone limits",
                by_family={
                    "terrain_relief": {
                        "spread_min": 0.0101,
                        "spread_median": 0.0202,
                        "spread_max": 0.0303,
                        "n": 100,
                    }
                },
                n_requested=100,
                n_used=97,
            ),
            "statistical_prediction_error": component(reason="encoder_not_evaluated"),
            "scenario_sensitivity": component(),
        },
        "sort_rule": "pareto layers over terrain_relief, terrain_texture; then window_id",
        "claim_limits": ["Earth ground is not lunar-dry (PRD 5)"],
        "source_ids": [MOON_DEM, EARTH_DEM],
        "run_id": RUN_ID,
    }
    value.update(overrides)
    return value


def product(product_id: str, body: str, checksum: str = HEX) -> dict[str, Any]:
    return {
        "product_id": product_id,
        "provider": "synthetic test provider",
        "version": "1",
        "source_url": None,
        "local_path": None,
        "checksum": checksum,
        "body": body,
        "acquisition_time": None,
        "frequency_or_wavelength": None,
        "transmit_receive_convention": None,
        "units": "m",
        "calibration_type": None,
        "native_grid": None,
        "effective_resolution": None,
        "crs_and_frame": "synthetic",
        "incidence_source": None,
        "qa_definition": None,
        "license_or_attribution": "test input",
        "verification_status": "PARTIAL",
    }


def sources(**checksums: str) -> list[dict[str, Any]]:
    bodies = {MOON_DEM: "moon", MINIRF: "moon", EARTH_DEM: "earth", WORLDCOVER: "earth"}
    bodies[UAVSAR] = "earth"
    return [product(pid, body, checksums.get(pid, HEX)) for pid, body in sorted(bodies.items())]


def target(mode: str | None = "C", body: str = "moon", **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "target_id": TARGET_ID if body == "moon" else "mars-jezero-w000",
        "mock": False,
        "run_id": None,
        "body": body,
        "name": "Connecting Ridge Site01" if body == "moon" else "Jezero",
        "description": "Candidate Artemis region at Connecting Ridge",
        "product_ids": [MOON_DEM],
        "crs": "synthetic",
        "projection": {"kind": "polar_stereographic", "params": {"lat_0": -90.0}},
        "footprint": None,
        "primary_window_id": MOON_WINDOW,
        "window_grid": {"window_m": 5000.0, "stride_m": 2500.0, "n_generated": 9, "n_eligible": 4},
        "reference_window_ids": [],
        "shortlist": {"earth_window_ids": [EARTH_WINDOW], "rule": "pareto", "n_compared": 1},
        "shortlist_reason": None,
        "shipping_mode": mode,
        "tile_layers": [],
    }
    value.update(overrides)
    return value


def gate(gate_id: str, status: str = "NOT_RUN") -> dict[str, Any]:
    record: dict[str, Any] = {
        "gate_id": gate_id,
        "status": "NOT_RUN",
        "evidence_path": None,
        "run_id": None,
        "scope": None,
        "result": None,
        "consequence": None,
    }
    fields = GateRecord.model_fields
    if "command" in fields:
        record.update(command=None, config_version=None, input_hashes=[])
    if status != "NOT_RUN":
        record.update(
            status=status,
            evidence_path="outputs/gates/x.json",
            run_id=RUN_ID,
            scope="synthetic test scope",
            result="synthetic test result",
        )
        if "command" in fields:
            record.update(
                command="python -m test",
                config_version=HEX,
                input_hashes=[{"path": "config/run.yaml", "sha256": HEX}],
            )
    return record


def gates(g5: str = "NOT_RUN") -> list[GateRecord]:
    return [GateRecord.model_validate(gate(f"G{n}", g5 if n == 5 else "NOT_RUN")) for n in range(7)]


def config(tmp_path: Path, text: str = "run:\n  shipping_mode: C\n") -> Path:
    path = tmp_path / "config" / "run.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def windows_map(moon: dict[str, Any] | None = None) -> dict[str, WindowEntry]:
    entries = [WindowEntry.model_validate(moon or moon_entry())]
    entries.append(WindowEntry.model_validate(earth_entry()))
    return {entry.window.window_id: entry for entry in entries}


def build(
    tmp_path: Path,
    mode: str | None = "C",
    *,
    comparison_value: dict[str, Any] | None = None,
    target_value: dict[str, Any] | None = None,
    moon: dict[str, Any] | None = None,
    product_list: list[dict[str, Any]] | None = None,
    g5: str = "NOT_RUN",
    figures: tuple[FigureIndexEntry, ...] = (),
    config_path: Path | None = None,
) -> CardRecord:
    record = ComparisonRecord.model_validate(comparison_value or comparison(mode))
    return export_card(
        windows_map(moon),
        record,
        [ProductRecord.model_validate(p) for p in (product_list or sources())],
        default_card_limits(record, "moon"),
        gates(g5),
        target=TargetRecord.model_validate(target_value or target(mode)),
        config_path=config_path or config(tmp_path),
        figures_index=figures,
    )


def serialized(card: CardRecord) -> str:
    return canonical_json(card.model_dump(mode="json")).decode("utf-8")


# --- Numbers are the comparison's ---------------------------------------------------------------


@pytest.mark.parametrize("mode", ["A", "B", "C"])
def test_card_numbers_equal_the_serialized_comparison(tmp_path: Path, mode: str) -> None:
    record = ComparisonRecord.model_validate(comparison(mode))
    stored = json.loads(canonical_json(record.model_dump(mode="json")))
    card = build(tmp_path, mode)
    shown = json.loads(serialized(card))
    assert shown["distances_by_family"] == stored["distances_by_family"]
    rows = {row["observable"]: row for row in shown["q3_measurements"]}
    for family, entry in stored["distances_by_family"].items():
        row = rows[f"{family} distance"]
        assert row["value"] == entry["value"]
        assert type(row["value"]) is type(entry["value"])
        assert row["unit"] == entry["unit"]
        assert row["evidence_label"] == entry["evidence_label"]
        assert row["reason"] == entry["reason"]
    assert shown["q5_stability"]["uncertainty_components"] == stored["uncertainty_components"]
    assert shown["q5_stability"]["tied_by_family"] == {
        family: entry["tied"] for family, entry in stored["distances_by_family"].items()
    }


def test_radar_context_numbers_equal_the_serialized_fingerprints(tmp_path: Path) -> None:
    card = json.loads(serialized(build(tmp_path, "B")))
    prints = {
        p["radar"]["product_id"]: p["radar"]
        for entry in (moon_entry(), earth_entry())
        for p in json.loads(json.dumps(entry))["radar"]
    }
    values = [entry for entry in card["radar_context"] if entry["value"] is not None]
    assert values
    for entry in values:
        radar = prints[entry["product_id"]]
        assert entry["value"] == radar[entry["observable"]][entry["statistic"]]
        assert entry["band"] == radar["band"]
        assert entry["support_m"] == radar["support_m"]
    missing_rows = [entry for entry in card["radar_context"] if entry["value"] is None]
    assert all(e["reason"] and e["evidence_label"] == "Unavailable" for e in missing_rows)


def test_card_stores_g5_status_and_no_bundle_version(tmp_path: Path) -> None:
    card = build(tmp_path, g5="FAIL")
    assert card.g5_status == "FAIL"
    assert "bundle_version" not in serialized(card)


# --- The seven questions ------------------------------------------------------------------------


def test_the_seven_questions_are_separate_fields(tmp_path: Path) -> None:
    card = build(tmp_path)
    assert card.q1_compared and MOON_WINDOW in card.q1_compared and EARTH_WINDOW in card.q1_compared
    assert card.q2_interpretation.evidence_label == "Assumed"
    assert card.q3_measurements
    for row in card.q3_measurements:
        assert row.support and row.evidence_label
    assert card.q4_differences.limits
    assert set(type(card.q5_stability.uncertainty_components).model_fields) == {
        "heterogeneity",
        "measurement_error",
        "statistical_prediction_error",
        "scenario_sensitivity",
    }
    assert card.q7_field_experiment.status == "proposed"
    assert card.q7_field_experiment.evidence_label == "Assumed"
    text = card.q7_field_experiment.text
    assert text.startswith("Proposed") and "none of these measurements has been made" in text
    assert "clast-size and coverage distribution" in text
    assert card.wording == "candidate window identified by this search"


def prd_bullets(heading: str, stop: str) -> list[str]:
    section = PRD.split(heading, 1)[1].split(stop, 1)[0]
    return [
        line[2:].replace("**", "").strip() for line in section.splitlines() if line.startswith("- ")
    ]


def test_earth_limits_are_the_prd_10_wording() -> None:
    assert list(EARTH_LIMITS) == prd_bullets("### Earth's limits on every card", "**For Mars")


def test_mars_limits_cover_the_prd_10_mars_sentence() -> None:
    sentence = PRD.split("**For Mars targets** question 4 lists instead: ", 1)[1].split("\n")[0]
    text = " ".join(MARS_LIMITS)
    for phrase in (
        "0.38 of Earth's",
        "thin CO₂ atmosphere in which wind and dust act but rain does not",
        "no liquid surface water or biology today",
        "lower temperatures and higher surface radiation",
        "resolve only features of roughly 100 m and larger, so finer roughness is not compared",
    ):
        assert phrase in sentence
        assert phrase in text


@pytest.mark.parametrize("relief", [0.0, 7.5], ids=["close", "poor"])
def test_q4_always_lists_every_earth_limit(tmp_path: Path, relief: float) -> None:
    card = build(tmp_path, comparison_value=comparison("C", relief=relief))
    assert card.q4_differences.limits == list(EARTH_LIMITS)
    assert len(card.q4_differences.limits) == 7


def mars_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    mars_window = "mars-jezero-0001-0001"
    moon = window(mars_window, "mars", moon_polygon(), [MOON_DEM], [])
    record = comparison("C", target_window_id=mars_window)
    return moon, record


@pytest.mark.parametrize("relief", [0.0, 7.5], ids=["close", "poor"])
def test_q4_lists_the_mars_list_for_a_mars_target(tmp_path: Path, relief: float) -> None:
    moon, record = mars_inputs()
    record["distances_by_family"]["terrain_relief"] = distance(relief)
    products = [p | {"body": "mars"} if p["product_id"] == MOON_DEM else p for p in sources()]
    card = build(
        tmp_path,
        comparison_value=record,
        moon=moon,
        target_value=target("C", body="mars"),
        product_list=products,
    )
    assert card.q4_differences.limits == list(MARS_LIMITS)
    assert any("roughly 100 m and larger" in limit for limit in card.q4_differences.limits)
    assert card.title == "Terrain analog pilot for Jezero; radar comparison not completed"


# --- Mode, labels and wording -------------------------------------------------------------------


def prd_titles() -> dict[str, str]:
    titles: dict[str, str] = {}
    for line in PRD.splitlines():
        match = re.match(r"\| \*\*([ABC]): ", line)
        if match:
            titles[match.group(1)] = line.rstrip(" |").rsplit("| ", 1)[1]
    return titles


@pytest.mark.parametrize("mode", ["A", "B", "C"])
def test_title_is_the_prd_4_title_for_the_mode(tmp_path: Path, mode: str) -> None:
    assert build(tmp_path, mode).title == prd_titles()[mode]


def test_a_null_mode_refuses_to_export_a_card(tmp_path: Path) -> None:
    with pytest.raises(ShippingModeUnsetError):
        build(tmp_path, None, target_value=target(None))


@pytest.mark.parametrize("mode", ["A", "B", "C"])
def test_no_forbidden_wording_on_any_card(tmp_path: Path, mode: str) -> None:
    text = serialized(build(tmp_path, mode)).lower()
    for phrase in FORBIDDEN:
        assert phrase.lower() not in text


def test_mode_b_shows_band_labelled_radar_signatures_without_a_distance(tmp_path: Path) -> None:
    card = build(tmp_path, "B")
    assert card.shipping_mode == "B"
    assert card.distances_by_family.radar_cpr.value is None
    assert {entry.band for entry in card.radar_context} == {"S", "L"}
    assert all(entry.evidence_label for entry in card.radar_context)
    assert re.search(r"\bmatch\b", serialized(card).lower()) is None


def test_mode_a_keeps_radar_and_terrain_distances_separate(tmp_path: Path) -> None:
    card = build(tmp_path, "A")
    shown = json.loads(serialized(card))["distances_by_family"]
    assert set(shown) == {"terrain_relief", "terrain_texture", "radar_cpr", "model_embedding"}
    assert shown["radar_cpr"]["value"] == 0.0321987654
    assert shown["terrain_relief"]["value"] == 0.123456789
    text = serialized(card)
    assert "combined" not in json.dumps(shown) and '"score"' not in text


def test_mode_c_states_radar_comparison_not_completed(tmp_path: Path) -> None:
    card = build(tmp_path, "C")
    assert "radar comparison not completed" in card.title
    assert any("radar comparison not completed" in claim for claim in card.claim_limits)
    assert card.radar_context == []
    assert card.distances_by_family.radar_cpr.value is None


def test_unchecked_campaign_entries_stay_unknown_with_a_null_source(tmp_path: Path) -> None:
    card = json.loads(serialized(build(tmp_path)))
    assert card["q6_campaign"] == {
        name: {"value": "unknown", "source": None}
        for name in ("access", "permits", "season", "land_status", "logistics")
    }


def test_an_unavailable_distance_stays_null_with_its_reason(tmp_path: Path) -> None:
    card = build(tmp_path, "C")
    radar = card.distances_by_family.radar_cpr
    assert radar.value is None and radar.reason == "radar_not_completed"
    row = next(r for r in card.q3_measurements if r.observable == "radar_cpr distance")
    assert row.value is None and row.reason == "radar_not_completed"


def test_mock_label_survives(tmp_path: Path) -> None:
    assert build(tmp_path, target_value=target(mock=True)).mock is True
    assert build(tmp_path).mock is False


# --- Encoder guard ------------------------------------------------------------------------------


def encoder_comparison(sort_rule: str) -> dict[str, Any]:
    record: dict[str, Any] = comparison("C", sort_rule=sort_rule)
    record["distances_by_family"]["model_embedding"] = distance(
        0.4321, unit="cosine distance", evidence_label="Predicted"
    )
    return record


def test_encoder_distance_is_shown_predicted_and_outside_ranking(tmp_path: Path) -> None:
    card = build(tmp_path, comparison_value=encoder_comparison("pareto over terrain_relief"))
    assert card.distances_by_family.model_embedding.evidence_label == "Predicted"
    row = next(r for r in card.q3_measurements if r.observable == "model_embedding distance")
    assert row.evidence_label == "Predicted" and row.value == 0.4321
    assert any("EXPERIMENTAL" in claim for claim in card.claim_limits)


def test_encoder_in_a_sort_rule_is_refused_while_g5_is_not_pass(tmp_path: Path) -> None:
    rule = "pareto layers over terrain_relief, model_embedding; then window_id"
    with pytest.raises(EncoderGuardError):
        build(tmp_path, comparison_value=encoder_comparison(rule))
    assert build(tmp_path, comparison_value=encoder_comparison(rule), g5="PASS").g5_status == "PASS"


# --- Figures and run_id -------------------------------------------------------------------------


def test_figures_point_to_svgs_only_when_the_index_lists_them(tmp_path: Path) -> None:
    card_id = f"{MOON_WINDOW}__{EARTH_WINDOW}"
    listed = FigureIndexEntry(card_id, "A", f"data/processed/figures/{card_id}_A.svg", HEX, 10)
    card = build(tmp_path, figures=(listed,))
    figures = {figure.figure: figure for figure in card.figures}
    assert figures["A"].path == f"figures/{card_id}_A.svg" and figures["A"].reason is None
    for letter in ("B", "C"):
        assert figures[letter].path is None
        assert figures[letter].reason == "figure_not_rendered"


def test_run_id_changes_with_polygon_checksum_and_config(tmp_path: Path) -> None:
    base = build(tmp_path).run_id
    assert build(tmp_path).run_id == base
    moved = moon_entry()
    moved["window"]["polygon"]["ring_lonlat"][1] = [12.625, -89.5]
    assert build(tmp_path, moon=moved).run_id != base
    assert build(tmp_path, product_list=sources(**{MOON_DEM: HEX2})).run_id != base
    other = config(tmp_path / "other", "run:\n  shipping_mode: C \n")
    assert build(tmp_path, config_path=other).run_id != base


def test_recomputed_run_id_matches_the_card(tmp_path: Path) -> None:
    card = build(tmp_path, "B")
    by_id = {p.product_id: p for p in (ProductRecord.model_validate(s) for s in sources())}
    assert recompute_card_run_id(card, by_id, config(tmp_path)) == card.run_id


# --- Writers ------------------------------------------------------------------------------------


def test_writers_validate_and_write_identical_bytes(tmp_path: Path) -> None:
    path = tmp_path / "outputs" / "comparisons" / f"{TARGET_ID}.json"
    first = write_comparisons(path, [comparison("C")])
    assert path.read_bytes() == first
    assert write_comparisons(path, [ComparisonRecord.model_validate(comparison("C"))]) == first
    assert path.read_bytes() == canonical_json(json.loads(first))
    windows_path = tmp_path / "data" / "windows" / "earth.json"
    write_windows(windows_path, [earth_entry()])
    assert json.loads(windows_path.read_bytes())[0]["window"]["window_id"] == EARTH_WINDOW


def test_an_invalid_record_raises_and_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "outputs" / "comparisons" / f"{TARGET_ID}.json"
    bad = comparison("C")
    bad["distances_by_family"]["terrain_relief"]["value"] = None
    with pytest.raises(ValidationError):
        write_comparisons(path, [bad])
    assert not path.exists()


def test_a_mock_record_is_refused_under_outputs(tmp_path: Path) -> None:
    path = tmp_path / "outputs" / "search" / "targets.json"
    with pytest.raises(MockRecordError):
        write_targets(path, [target(mock=True)])
    with pytest.raises(MockRecordError):
        write_targets(path, [target(mock=True)], allow_mock=True)
    assert not path.exists()
    fixture = tmp_path / "fixtures" / "mock-bundle" / "targets.json"
    with pytest.raises(MockRecordError):
        write_targets(fixture, [target(mock=True)])
    write_targets(fixture, [target(mock=True)], allow_mock=True)
    assert fixture.is_file()


def test_write_cards_names_each_file_by_card_id(tmp_path: Path) -> None:
    card = build(tmp_path)
    paths = write_cards(tmp_path / "outputs" / "cards", [card])
    assert [p.name for p in paths] == [f"{card.card_id}.json"]
    assert paths[0].read_bytes() == canonical_json(card.model_dump(mode="json"))


# --- export cards -------------------------------------------------------------------------------


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def card_tree(tmp_path: Path, mode: str = "C") -> Path:
    root = tmp_path / "repo"
    config(root)
    write_json(root / "outputs" / "search" / "targets.json", [target(mode)])
    write_json(root / "outputs" / "comparisons" / f"{TARGET_ID}.json", [comparison(mode)])
    write_json(root / "data" / "processed" / "windows" / "moon.json", [moon_entry()])
    write_json(root / "data" / "processed" / "windows" / "earth.json", [earth_entry()])
    write_json(root / "outputs" / "validation.json", [gate(f"G{n}") for n in range(7)])
    write_json(root / "outputs" / "sources.json", sources())
    return root


def cards_main(root: Path, *extra: str) -> int:
    return main(["cards", "--root", str(root), *extra])


def test_export_cards_writes_the_shortlisted_cards(tmp_path: Path) -> None:
    root = card_tree(tmp_path)
    assert cards_main(root) == 0
    path = root / "outputs" / "cards" / f"{MOON_WINDOW}__{EARTH_WINDOW}.json"
    card = CardRecord.model_validate_json(path.read_bytes())
    assert card.title == "Terrain analog pilot for Connecting Ridge; radar comparison not completed"
    assert cards_main(root, "--check") == 0
    first = path.read_bytes()
    assert cards_main(root) == 0
    assert path.read_bytes() == first


def test_export_cards_builds_one_declared_slice_pair(tmp_path: Path) -> None:
    root = card_tree(tmp_path)
    targets = root / "outputs" / "search" / "targets.json"
    write_json(targets, [target(shortlist=None, shortlist_reason="search_not_run")])
    assert cards_main(root) == 3
    pair = ["--target-window", MOON_WINDOW, "--earth-window", EARTH_WINDOW]
    assert cards_main(root, *pair) == 0
    assert cards_main(root, "--check", *pair) == 0


@pytest.mark.parametrize(
    "missing",
    [
        "outputs/search/targets.json",
        "outputs/validation.json",
        "outputs/sources.json",
        "data/processed/windows/earth.json",
        "outputs/comparisons/moon-cr-site01-w000.json",
        "config/run.yaml",
    ],
)
def test_a_missing_input_exits_3_and_writes_nothing(
    tmp_path: Path, missing: str, capsys: pytest.CaptureFixture[str]
) -> None:
    root = card_tree(tmp_path)
    (root / missing).unlink()
    assert cards_main(root) == 3
    assert "blocked:" in capsys.readouterr().err
    assert not (root / "outputs" / "cards").exists()


def test_check_fails_on_changed_bytes_and_on_an_absent_pair(tmp_path: Path) -> None:
    root = card_tree(tmp_path)
    assert cards_main(root) == 0
    path = root / "outputs" / "cards" / f"{MOON_WINDOW}__{EARTH_WINDOW}.json"
    original = path.read_bytes()
    path.write_bytes(original.replace(b"0.123456789", b"0.12345679"))
    assert cards_main(root, "--check") == 1
    path.write_bytes(original)
    assert cards_main(root, "--check") == 0
    stray = copy.deepcopy(json.loads(original))
    stray["card_id"] = f"{MOON_WINDOW}__earth-27n-holuhraun-0009-0009"
    (path.parent / f"{stray['card_id']}.json").write_bytes(canonical_json(stray))
    assert cards_main(root, "--check") == 1


def test_card_inputs_defaults_are_the_contract_paths() -> None:
    inputs = CardInputs(root=Path("."))
    assert inputs.targets == Path("outputs/search/targets.json")
    assert inputs.windows_dir == Path("data/processed/windows")
    assert inputs.out_dir == Path("outputs/cards")
    assert export.ExportReason.FIGURE_NOT_RENDERED.value == "figure_not_rendered"


def test_card_limits_add_to_the_card(tmp_path: Path) -> None:
    record = ComparisonRecord.model_validate(comparison("C"))
    limits = CardLimits(interpretation="Assumed: test interpretation.", differing=("x",))
    card = export_card(
        windows_map(),
        record,
        [ProductRecord.model_validate(p) for p in sources()],
        limits,
        gates(),
        target=TargetRecord.model_validate(target("C")),
        config_path=config(tmp_path),
    )
    assert card.q4_differences.differing == ["x"]
    assert card.q2_interpretation.text == "Assumed: test interpretation."
