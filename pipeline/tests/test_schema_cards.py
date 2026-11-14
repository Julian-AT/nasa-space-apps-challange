"""Contract tests for the science records II (CONTRACTS a.4; PRD 3, 4, 8.4, 8.10, 10).

TileLayer, TargetRecord (targets.json) and CardRecord (cards/{card_id}.json) carry exactly the
CONTRACTS a.4 field names, listed here. PRD 8.12 does not list a card or target record, so the
card is an engineering record built on PRD 10 (seven answers, table D). Values in these tests
are schema test inputs, not observations or results.
"""

from __future__ import annotations

import copy
import json
import re
import typing
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from terraluna_pipeline.schema import (
    CONTRACTS,
    SCHEMA_VERSION,
    CampaignEntry,
    CardCampaign,
    CardDifferences,
    CardEarth,
    CardInterpretation,
    CardRecord,
    CardStability,
    CardTarget,
    ClassesLegend,
    ComparisonRef,
    FieldExperiment,
    FigureRef,
    Hillshade,
    LegendClass,
    NumericLegend,
    Projection,
    RadarContextEntry,
    Shortlist,
    TableDRow,
    TargetRecord,
    TargetsFile,
    TiedByFamily,
    TieGroupByFamily,
    TileEncoding,
    TileLayer,
    WindowGrid,
)

HEX64 = "0123456789abcdef" * 4
RUN_ID = "sha256:" + HEX64
FAMILIES = ["terrain_relief", "terrain_texture", "radar_cpr", "model_embedding"]
TARGET_WINDOW = "moon-aeqd-0003-0007"
EARTH_WINDOW = "earth-27n-0012-0040"
TARGET_ID = "moon-cr-site01-w000"


def literal_values(annotation: object) -> list[object]:
    if typing.get_origin(annotation) is typing.Literal:
        return list(typing.get_args(annotation))
    values: list[object] = []
    for arg in typing.get_args(annotation):
        values += literal_values(arg)
    return values


# --- Valid examples (schema test inputs) --------------------------------------------------------


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


def numeric_legend(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "kind": "numeric",
        "min": 0.125,
        "max": 1.875,
        "scale": "viridis",
        "unit": None,
    }
    value.update(overrides)
    return value


def legend_class(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "code": 60,
        "label": "Bare / sparse vegetation",
        "rgba": [180, 180, 180, 255],
    }
    value.update(overrides)
    return value


def classes_legend(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "kind": "classes",
        "classes": [
            legend_class(),
            legend_class(code=80, label="Permanent water bodies", rgba=[0, 100, 200, 255]),
        ],
    }
    value.update(overrides)
    return value


def hillshade(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"azimuth_deg": 315.0, "altitude_deg": 45.0}
    value.update(overrides)
    return value


def tile_encoding(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"format": "webp", "lossless": False, "quality": 90}
    value.update(overrides)
    return value


def tile_layer(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "layer": "site01-mrf-cpr",
        "body": "moon",
        "crs": "+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R=1737400 +units=m",
        "units": None,
        "evidence_label": "Derived",
        "source_product_ids": ["mrf-polar-cpr-mosaic-example"],
        "origin_x_m": -2560.0,
        "origin_y_m": 2560.0,
        "y_direction": "down",
        "tile_px": 512,
        "metres_per_pixel_by_zoom": [20.0, 10.0, 5.0],
        "zoom_min": 0,
        "zoom_max": 2,
        "extent_m": [-2560.0, -2560.0, 2560.0, 2560.0],
        "omitted_tiles": [[2, 0, 3]],
        "legend": numeric_legend(),
        "hillshade": None,
        "encoding": tile_encoding(),
        "bytes_total": 1048576,
        "mock": False,
    }
    value.update(overrides)
    return value


def projection(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "kind": "polar_stereographic",
        "params": {"lat_0": -90.0, "lon_0": 0.0, "k": 1, "datum": "Moon 2015 sphere"},
    }
    value.update(overrides)
    return value


def window_grid(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "window_m": 5000.0,
        "stride_m": 2500.0,
        "n_generated": 49,
        "n_eligible": 31,
    }
    value.update(overrides)
    return value


def shortlist(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "earth_window_ids": [EARTH_WINDOW],
        "rule": "pareto layers over terrain_relief, terrain_texture; then window_id",
        "n_compared": 120,
    }
    value.update(overrides)
    return value


def target(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "target_id": TARGET_ID,
        "mock": False,
        "run_id": RUN_ID,
        "body": "moon",
        "name": "Connecting Ridge Site01",
        "description": "Candidate Artemis region at Connecting Ridge",
        "product_ids": ["pgda78-site01-dem-5m"],
        "crs": "+proj=stere +lat_0=-90 +lon_0=0 +k=1 +R=1737400 +units=m",
        "projection": projection(),
        "footprint": moon_polygon(),
        "primary_window_id": TARGET_WINDOW,
        "window_grid": window_grid(),
        "reference_window_ids": ["moon-aeqd-0003-0008"],
        "shortlist": shortlist(),
        "shortlist_reason": None,
        "shipping_mode": "B",
        "tile_layers": [tile_layer()],
    }
    value.update(overrides)
    return value


def unfrozen_target() -> dict[str, Any]:
    """A target before G0: no footprint, primary window, shortlist, mode or run yet."""
    return target(
        run_id=None,
        footprint=None,
        primary_window_id=None,
        window_grid=window_grid(n_generated=None, n_eligible=None),
        reference_window_ids=[],
        shortlist=None,
        shortlist_reason="search_not_run",
        shipping_mode=None,
        tile_layers=[],
    )


def distance(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "value": 0.375,
        "unit": "RMS of log nu",
        "evidence_label": "Derived",
        "status": "available",
        "reason": None,
        "reference_spread": {"min": 0.125, "median": 0.25, "max": 0.5, "n": 12},
        "reference_spread_reason": None,
        "background_percentile": 12.5,
        "background_percentile_reason": None,
        "tied": False,
        "tie_group": None,
        "tie_reason": None,
    }
    value.update(overrides)
    return value


def missing_distance(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "value": None,
        "unit": "CPR Wasserstein-1",
        "evidence_label": "Unavailable",
        "status": "not_comparable",
        "reason": "cross_band_calibration_unresolved",
        "reference_spread": None,
        "reference_spread_reason": "spread_unavailable",
        "background_percentile": None,
        "background_percentile_reason": "background_sample_size_unset",
        "tied": None,
        "tie_group": None,
        "tie_reason": "spread_unavailable",
    }
    value.update(overrides)
    return value


def distances(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "terrain_relief": distance(tied=True, tie_group="relief-1"),
        "terrain_texture": distance(value=0),
        "radar_cpr": missing_distance(),
        "model_embedding": missing_distance(unit="cosine distance", reason="g5_not_pass"),
    }
    value.update(overrides)
    return value


def component(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "status": "unavailable",
        "evidence_label": "Unavailable",
        "reason": "search_not_run",
        "note": None,
        "by_family": None,
        "n_requested": None,
        "n_used": None,
        "scenarios": None,
        "rank_frequency": None,
    }
    value.update(overrides)
    return value


def uncertainty() -> dict[str, Any]:
    return {
        "heterogeneity": component(),
        "measurement_error": component(
            status="available",
            evidence_label="Derived",
            reason=None,
            note="The clones cover the published topographic error model only (PRD 6.6).",
            by_family={
                "terrain_relief": {
                    "spread_min": 0.0625,
                    "spread_median": 0.125,
                    "spread_max": 0.25,
                    "n": 100,
                }
            },
            n_requested=100,
            n_used=100,
        ),
        "statistical_prediction_error": component(reason="encoder_not_evaluated"),
        "scenario_sensitivity": component(reason="search_not_run"),
    }


def ties_from(by_family: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    tied = {family: by_family[family]["tied"] for family in FAMILIES}
    groups = {family: by_family[family]["tie_group"] for family in FAMILIES}
    return tied, groups


def stability(**overrides: Any) -> dict[str, Any]:
    tied, groups = ties_from(distances())
    value: dict[str, Any] = {
        "uncertainty_components": uncertainty(),
        "tied_by_family": tied,
        "tie_group_by_family": groups,
    }
    value.update(overrides)
    return value


def table_d_row(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "observable": "nu_relief_270m",
        "value": 1.5,
        "unit": "m",
        "evidence_label": "Derived",
        "support": "90 m analysis grid, 270 m lag",
        "product_id": "pgda78-site01-dem-5m",
        "band": None,
        "geometry": None,
        "reason": None,
    }
    value.update(overrides)
    return value


def campaign_entry(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"value": "unknown", "source": None}
    value.update(overrides)
    return value


def campaign() -> dict[str, Any]:
    return {
        "access": campaign_entry(),
        "permits": campaign_entry(),
        "season": campaign_entry(
            value="June to September", source="https://example.org/season-page"
        ),
        "land_status": campaign_entry(),
        "logistics": campaign_entry(),
    }


def radar_context_entry(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "product_id": "mrf-polar-cpr-mosaic-example",
        "band": "S",
        "observable": "cpr",
        "statistic": "median of block ratio of mean powers",
        "value": 0.625,
        "unit": None,
        "evidence_label": "Observed",
        "support_m": 250.0,
        "reason": None,
    }
    value.update(overrides)
    return value


def figure_ref(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "figure": "A",
        "path": "figures/moon-aeqd-0003-0007__earth-27n-0012-0040-a.svg",
        "alt": "Earth window on a basemap and the lunar window on hillshade, with scale bars",
        "reason": None,
    }
    value.update(overrides)
    return value


EARTH_LIMITS = [
    "Gravity",
    "Atmosphere",
    "Water and life",
    "Radiation and space weathering",
    "Temperature",
    "Illumination",
    "Regolith origin",
]
MARS_LIMITS = [
    "Gravity",
    "Thin CO2 atmosphere",
    "No liquid surface water or biology today",
    "Lower temperatures and higher surface radiation",
    "CTX DTMs resolve only features of roughly 100 m and larger",
]


def card(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "card_id": f"{TARGET_WINDOW}__{EARTH_WINDOW}",
        "mock": False,
        "run_id": RUN_ID,
        "shipping_mode": "B",
        "title": "Terrain analog candidates for Connecting Ridge, with radar context",
        "wording": "candidate window identified by this search",
        "target": {
            "target_id": TARGET_ID,
            "target_window_id": TARGET_WINDOW,
            "body": "moon",
            "polygon": moon_polygon(),
            "product_ids": ["pgda78-site01-dem-5m"],
        },
        "earth": {
            "earth_window_id": EARTH_WINDOW,
            "polygon": earth_polygon(),
            "product_ids": ["copernicus-dem-glo-30-2021-1"],
            "search_region_id": "holuhraun",
        },
        "comparison_ref": {
            "path": f"comparisons/{TARGET_ID}.json",
            "target_window_id": TARGET_WINDOW,
            "earth_window_id": EARTH_WINDOW,
        },
        "q1_compared": "Earth window earth-27n-0012-0040 and lunar window moon-aeqd-0003-0007",
        "q2_interpretation": {
            "text": "A terrain context for interpreting the lunar window",
            "evidence_label": "Assumed",
        },
        "q3_measurements": [table_d_row()],
        "q4_differences": {
            "limits": list(EARTH_LIMITS),
            "differing": ["terrain_texture at 540 m"],
            "ambiguous": ["radar context: cross-band calibration unresolved"],
            "not_observed": ["clast-size distribution"],
        },
        "q5_stability": stability(),
        "q6_campaign": campaign(),
        "q7_field_experiment": {
            "text": "A surveyed surface patch with clast-size and coverage counts",
            "evidence_label": "Assumed",
            "status": "proposed",
        },
        "distances_by_family": distances(),
        "radar_context": [radar_context_entry()],
        "figures": [
            figure_ref(),
            figure_ref(figure="B", path="figures/b.svg", alt="nu(L) curves"),
            figure_ref(figure="C", path=None, alt="Radar panel", reason="figure_not_rendered"),
        ],
        "g5_status": "NOT_RUN",
        "claim_limits": ["Terrain descriptors only; radar shown as context (PRD 4 Mode B)"],
    }
    value.update(overrides)
    return value


EXAMPLES: list[tuple[type[BaseModel], Callable[[], dict[str, Any]]]] = [
    (LegendClass, legend_class),
    (ClassesLegend, classes_legend),
    (NumericLegend, numeric_legend),
    (Hillshade, hillshade),
    (TileEncoding, tile_encoding),
    (TileLayer, tile_layer),
    (Projection, projection),
    (WindowGrid, window_grid),
    (Shortlist, shortlist),
    (TargetRecord, target),
    (CardTarget, lambda: card()["target"]),
    (CardEarth, lambda: card()["earth"]),
    (ComparisonRef, lambda: card()["comparison_ref"]),
    (CardInterpretation, lambda: card()["q2_interpretation"]),
    (TableDRow, table_d_row),
    (CardDifferences, lambda: card()["q4_differences"]),
    (TiedByFamily, lambda: stability()["tied_by_family"]),
    (TieGroupByFamily, lambda: stability()["tie_group_by_family"]),
    (CardStability, stability),
    (CampaignEntry, campaign_entry),
    (CardCampaign, campaign),
    (FieldExperiment, lambda: card()["q7_field_experiment"]),
    (RadarContextEntry, radar_context_entry),
    (FigureRef, figure_ref),
    (CardRecord, card),
]
EXAMPLE_IDS = [model.__name__ for model, _ in EXAMPLES]

# CONTRACTS a.4: the field names of every record of this story, in order.
CONTRACT_FIELDS: dict[type[BaseModel], list[str]] = {
    LegendClass: ["code", "label", "rgba"],
    ClassesLegend: ["kind", "classes"],
    NumericLegend: ["kind", "min", "max", "scale", "unit"],
    Hillshade: ["azimuth_deg", "altitude_deg"],
    TileEncoding: ["format", "lossless", "quality"],
    TileLayer: [
        "layer",
        "body",
        "crs",
        "units",
        "evidence_label",
        "source_product_ids",
        "origin_x_m",
        "origin_y_m",
        "y_direction",
        "tile_px",
        "metres_per_pixel_by_zoom",
        "zoom_min",
        "zoom_max",
        "extent_m",
        "omitted_tiles",
        "legend",
        "hillshade",
        "encoding",
        "bytes_total",
        "mock",
    ],
    Projection: ["kind", "params"],
    WindowGrid: ["window_m", "stride_m", "n_generated", "n_eligible"],
    Shortlist: ["earth_window_ids", "rule", "n_compared"],
    TargetRecord: [
        "target_id",
        "mock",
        "run_id",
        "body",
        "name",
        "description",
        "product_ids",
        "crs",
        "projection",
        "footprint",
        "primary_window_id",
        "window_grid",
        "reference_window_ids",
        "shortlist",
        "shortlist_reason",
        "shipping_mode",
        "tile_layers",
    ],
    CardTarget: ["target_id", "target_window_id", "body", "polygon", "product_ids"],
    CardEarth: ["earth_window_id", "polygon", "product_ids", "search_region_id"],
    ComparisonRef: ["path", "target_window_id", "earth_window_id"],
    CardInterpretation: ["text", "evidence_label"],
    TableDRow: [
        "observable",
        "value",
        "unit",
        "evidence_label",
        "support",
        "product_id",
        "band",
        "geometry",
        "reason",
    ],
    CardDifferences: ["limits", "differing", "ambiguous", "not_observed"],
    TiedByFamily: FAMILIES,
    TieGroupByFamily: FAMILIES,
    CardStability: ["uncertainty_components", "tied_by_family", "tie_group_by_family"],
    CampaignEntry: ["value", "source"],
    CardCampaign: ["access", "permits", "season", "land_status", "logistics"],
    FieldExperiment: ["text", "evidence_label", "status"],
    RadarContextEntry: [
        "product_id",
        "band",
        "observable",
        "statistic",
        "value",
        "unit",
        "evidence_label",
        "support_m",
        "reason",
    ],
    FigureRef: ["figure", "path", "alt", "reason"],
    CardRecord: [
        "card_id",
        "mock",
        "run_id",
        "shipping_mode",
        "title",
        "wording",
        "target",
        "earth",
        "comparison_ref",
        "q1_compared",
        "q2_interpretation",
        "q3_measurements",
        "q4_differences",
        "q5_stability",
        "q6_campaign",
        "q7_field_experiment",
        "distances_by_family",
        "radar_context",
        "figures",
        "g5_status",
        "claim_limits",
    ],
}


# --- Helpers ------------------------------------------------------------------------------------


def assert_accepted[M: BaseModel](model: type[M], value: Any) -> M:
    parsed = model.model_validate(value)
    assert model.model_validate_json(json.dumps(value)) == parsed
    return parsed


def assert_rejected(model: type[BaseModel], value: Any) -> None:
    """Reject in both Python and JSON mode."""
    with pytest.raises(ValidationError):
        model.model_validate(value)
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(value))


def own_models(model: type[BaseModel], seen: set[type[BaseModel]]) -> Iterator[type[BaseModel]]:
    """``model`` and every record of this story reachable from its fields."""
    if model in seen or model not in CONTRACT_FIELDS:
        return
    seen.add(model)
    yield model

    def walk(annotation: object) -> Iterator[type[BaseModel]]:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield from own_models(annotation, seen)
        for arg in typing.get_args(annotation):
            yield from walk(arg)

    for field in model.model_fields.values():
        yield from walk(field.annotation)


# --- Field names, required fields, unknown fields -----------------------------------------------


@pytest.mark.parametrize("model", list(CONTRACT_FIELDS), ids=lambda m: m.__name__)
def test_record_fields_are_exactly_contracts_a4(model: type[BaseModel]) -> None:
    assert list(model.model_fields) == CONTRACT_FIELDS[model]


def test_every_record_is_reachable_and_covered() -> None:
    seen: set[type[BaseModel]] = set()
    reachable = {*own_models(TargetRecord, seen), *own_models(CardRecord, seen)}
    assert reachable == set(CONTRACT_FIELDS) == {model for model, _ in EXAMPLES}


@pytest.mark.parametrize(("model", "example"), EXAMPLES, ids=EXAMPLE_IDS)
def test_example_is_accepted(model: type[BaseModel], example: Callable[[], dict[str, Any]]) -> None:
    assert_accepted(model, example())


@pytest.mark.parametrize(("model", "example"), EXAMPLES, ids=EXAMPLE_IDS)
def test_every_field_is_required_without_default(
    model: type[BaseModel], example: Callable[[], dict[str, Any]]
) -> None:
    for name, field in model.model_fields.items():
        assert field.is_required(), f"{model.__name__}.{name} has a default"
        value = example()
        del value[name]
        assert_rejected(model, value)


@pytest.mark.parametrize(("model", "example"), EXAMPLES, ids=EXAMPLE_IDS)
@pytest.mark.parametrize("extra", ["score", "combined_score", "notes"])
def test_unknown_fields_are_rejected(
    model: type[BaseModel], example: Callable[[], dict[str, Any]], extra: str
) -> None:
    assert_rejected(model, {**example(), extra: 0.5})


@pytest.mark.parametrize(("model", "example"), EXAMPLES, ids=EXAMPLE_IDS)
def test_records_are_frozen(model: type[BaseModel], example: Callable[[], dict[str, Any]]) -> None:
    record = model.model_validate(example())
    name = next(iter(model.model_fields))
    with pytest.raises(ValidationError):
        setattr(record, name, getattr(record, name))


def test_no_record_has_a_score_combined_weight_or_probability_field() -> None:
    # D07: the families stay separate; there is no universal analog score.
    forbidden = re.compile(r"score|combined|weight|probability")
    for model in CONTRACT_FIELDS:
        for name in model.model_fields:
            assert not forbidden.search(name), f"{model.__name__}.{name}"


def test_schema_version_stays_1_0_0() -> None:
    assert SCHEMA_VERSION == "1.0.0"


def test_targets_and_card_record_follow_the_t0b_01_contracts() -> None:
    files = [(c.name, c.file_name) for c in CONTRACTS]
    start = files.index(("Comparisons", "comparisons.schema.json")) + 1
    assert files[start : start + 2] == [
        ("Targets", "targets.schema.json"),
        ("CardRecord", "card-record.schema.json"),
    ]


def test_examples_do_not_share_state() -> None:
    first = card()
    second = copy.deepcopy(first)
    first["distances_by_family"]["terrain_relief"]["value"] = 1.5
    first["q6_campaign"]["access"]["value"] = "open"
    assert card() == second


# --- (1) shipping_mode, (2) no bundle_version ---------------------------------------------------


def test_a_card_without_shipping_mode_fails() -> None:
    value = card()
    del value["shipping_mode"]
    assert_rejected(CardRecord, value)


@pytest.mark.parametrize("mode", [None, "D", "a", "", "B\n", 1])
def test_a_card_shipping_mode_is_a_b_or_c_and_never_null(mode: object) -> None:
    assert_rejected(CardRecord, card(shipping_mode=mode))


@pytest.mark.parametrize("mode", ["A", "B", "C"])
def test_a_card_takes_each_shipping_mode(mode: str) -> None:
    assert assert_accepted(CardRecord, card(shipping_mode=mode)).shipping_mode == mode


def test_card_record_has_no_bundle_version_field() -> None:
    assert "bundle_version" not in CardRecord.model_fields
    assert_rejected(CardRecord, card(bundle_version="2026-11-15T0900Z-3f9c2a1"))


def test_card_record_has_no_gate_states_other_than_its_g5_status() -> None:
    assert [name for name in CardRecord.model_fields if re.search(r"gate|g[0-9]", name)] == [
        "g5_status"
    ]


@pytest.mark.parametrize("status", ["NOT_RUN", "PASS", "FAIL", "PARTIAL", "NOT_APPLICABLE"])
def test_g5_status_takes_each_gate_status(status: str) -> None:
    assert assert_accepted(CardRecord, card(g5_status=status)).g5_status == status


@pytest.mark.parametrize("status", [None, "PASSED", "pass", ""])
def test_g5_status_rejects_anything_else(status: object) -> None:
    assert_rejected(CardRecord, card(g5_status=status))


# --- (3) TableDRow ------------------------------------------------------------------------------


def test_a_table_d_row_without_evidence_label_fails() -> None:
    value = table_d_row()
    del value["evidence_label"]
    assert_rejected(TableDRow, value)
    assert_rejected(CardRecord, card(q3_measurements=[value]))


@pytest.mark.parametrize("label", [None, "observed", "Measured", ""])
def test_a_table_d_row_evidence_label_is_a_prd_3_label(label: object) -> None:
    assert_rejected(TableDRow, table_d_row(evidence_label=label))


def test_a_missing_table_d_value_is_null_and_labelled() -> None:
    row = table_d_row(value=None, unit=None, evidence_label="Unavailable", reason="qa_missing")
    parsed = assert_accepted(TableDRow, row)
    assert parsed.value is None
    assert json.loads(parsed.model_dump_json())["value"] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("observable", ""),
        ("value", float("nan")),
        ("value", "1.5"),
        ("support", ""),
        ("product_id", ""),
        ("reason", "Not Run"),
    ],
)
def test_table_d_row_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TableDRow.model_validate(table_d_row(**{field: value}))


# --- (4) CampaignEntry --------------------------------------------------------------------------


def test_an_unknown_campaign_entry_has_no_source() -> None:
    assert assert_accepted(CampaignEntry, campaign_entry()).source is None


def test_an_unknown_campaign_entry_with_a_source_fails() -> None:
    assert_rejected(CampaignEntry, campaign_entry(source="https://example.org/permits"))


@pytest.mark.parametrize("value", ["open to the public", "Unknown", "UNKNOWN", "unknown "])
def test_a_checked_campaign_entry_needs_a_source(value: str) -> None:
    assert_rejected(CampaignEntry, campaign_entry(value=value))
    assert_accepted(CampaignEntry, campaign_entry(value=value, source="field office letter"))


@pytest.mark.parametrize(("value", "source"), [("", None), ("open", ""), (None, None)])
def test_campaign_entry_values_are_non_empty_strings(value: object, source: object) -> None:
    assert_rejected(CampaignEntry, campaign_entry(value=value, source=source))


@pytest.mark.parametrize("question", ["access", "permits", "season", "land_status", "logistics"])
def test_each_campaign_question_takes_a_campaign_entry(question: str) -> None:
    value = card()
    value["q6_campaign"][question] = {"value": "unknown", "source": "should be null"}
    assert_rejected(CardRecord, value)


# --- (5) q2 and q7 are Assumed; q7 is proposed --------------------------------------------------


@pytest.mark.parametrize("label", ["Observed", "Derived", "Predicted", "Unavailable", "assumed"])
def test_q2_interpretation_accepts_only_assumed(label: str) -> None:
    value = card()
    value["q2_interpretation"]["evidence_label"] = label
    assert_rejected(CardRecord, value)


@pytest.mark.parametrize("label", ["Observed", "Derived", "Predicted", "Unavailable", "assumed"])
def test_q7_field_experiment_accepts_only_assumed(label: str) -> None:
    value = card()
    value["q7_field_experiment"]["evidence_label"] = label
    assert_rejected(CardRecord, value)


@pytest.mark.parametrize("status", ["completed", "done", "Proposed", "", None])
def test_q7_field_experiment_status_is_only_proposed(status: object) -> None:
    value = card()
    value["q7_field_experiment"]["status"] = status
    assert_rejected(CardRecord, value)


def test_q2_and_q7_literals_are_the_contract_values() -> None:
    assert literal_values(CardInterpretation.model_fields["evidence_label"].annotation) == [
        "Assumed"
    ]
    assert literal_values(FieldExperiment.model_fields["evidence_label"].annotation) == ["Assumed"]
    assert literal_values(FieldExperiment.model_fields["status"].annotation) == ["proposed"]


@pytest.mark.parametrize(
    "wording", ["Candidate window identified by this search", "discovered analog", ""]
)
def test_card_wording_is_the_prd_3_wording(wording: str) -> None:
    assert_rejected(CardRecord, card(wording=wording))


# --- (6) TargetRecord nulls and the shortlist pair ----------------------------------------------


def test_an_unfrozen_target_keeps_its_nulls_through_a_json_round_trip() -> None:
    value = unfrozen_target()
    parsed = assert_accepted(TargetRecord, value)
    assert (parsed.footprint, parsed.primary_window_id, parsed.shortlist) == (None, None, None)
    dumped = json.loads(parsed.model_dump_json())
    assert dumped == value
    for key in ("footprint", "primary_window_id", "shortlist", "run_id", "shipping_mode"):
        assert key in dumped
        assert dumped[key] is None
    assert TargetRecord.model_validate_json(parsed.model_dump_json()) == parsed


def test_a_frozen_target_round_trips_unchanged() -> None:
    value = target()
    assert (
        json.loads(TargetRecord.model_validate_json(json.dumps(value)).model_dump_json()) == value
    )


def test_shortlist_and_shortlist_reason_both_null_fails() -> None:
    assert_rejected(TargetRecord, target(shortlist=None, shortlist_reason=None))


def test_shortlist_and_shortlist_reason_both_set_fails() -> None:
    assert_rejected(TargetRecord, target(shortlist=shortlist(), shortlist_reason="search_not_run"))


@pytest.mark.parametrize("reason", ["Search not run", "search-not-run", ""])
def test_shortlist_reason_is_a_reason_code(reason: str) -> None:
    assert_rejected(TargetRecord, target(shortlist=None, shortlist_reason=reason))


@pytest.mark.parametrize("mode", ["A", "B", "C", None])
def test_target_shipping_mode_is_a_b_c_or_null(mode: str | None) -> None:
    assert assert_accepted(TargetRecord, target(shipping_mode=mode)).shipping_mode == mode


@pytest.mark.parametrize(
    "target_id", [TARGET_ID, "mars-jezero-w000", "mars-arcadia-w000", "moon-a"]
)
def test_target_id_takes_the_prd_8_4_example_ids(target_id: str) -> None:
    assert assert_accepted(TargetRecord, target(target_id=target_id)).target_id == target_id


@pytest.mark.parametrize(
    "target_id", ["", "Moon-cr", "moon", "moon-", "moon-cr/site01", "../moon-cr", "moon-cr\n"]
)
def test_target_id_is_a_lower_case_body_prefixed_path_segment(target_id: str) -> None:
    assert_rejected(TargetRecord, target(target_id=target_id))


@pytest.mark.parametrize("kind", ["polar_stereographic", "equirectangular", "geographic"])
def test_projection_takes_each_kind(kind: str) -> None:
    assert assert_accepted(Projection, projection(kind=kind)).kind == kind


@pytest.mark.parametrize(
    "params",
    [{"lat_0": None}, {"lat_0": True}, {"lat_0": [1.5]}, {"": 1.5}, {"datum": ""}],
)
def test_projection_params_are_numbers_or_strings(params: dict[str, object]) -> None:
    assert_rejected(Projection, projection(params=params))


def test_projection_params_keep_numbers_and_strings() -> None:
    parsed = assert_accepted(Projection, projection())
    assert parsed.params == {"lat_0": -90.0, "lon_0": 0.0, "k": 1, "datum": "Moon 2015 sphere"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("description", ""),
        ("crs", ""),
        ("run_id", HEX64),
        ("body", "venus"),
        ("primary_window_id", "Moon-a"),
        ("reference_window_ids", ["moon-a", "bad id"]),
        ("projection", projection(kind="utm")),
        ("window_grid", window_grid(window_m=0)),
        ("window_grid", window_grid(stride_m=-2500.0)),
        ("window_grid", window_grid(n_generated=-1)),
        ("shortlist", shortlist(earth_window_ids=["Earth-1"])),
        ("shortlist", shortlist(rule="")),
        ("mock", 0),
    ],
)
def test_target_record_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(TargetRecord, target(**{field: value}))


def test_eligible_windows_cannot_exceed_generated_windows() -> None:
    assert_rejected(WindowGrid, window_grid(n_generated=30, n_eligible=31))
    assert_accepted(WindowGrid, window_grid(n_generated=31, n_eligible=31))
    assert_accepted(WindowGrid, window_grid(n_generated=None, n_eligible=31))


def test_a_shortlist_cannot_hold_more_windows_than_were_compared() -> None:
    two = [EARTH_WINDOW, "earth-27n-0012-0041"]
    assert_rejected(Shortlist, shortlist(earth_window_ids=two, n_compared=1))
    assert_accepted(Shortlist, shortlist(earth_window_ids=two, n_compared=2))
    assert_accepted(Shortlist, shortlist(earth_window_ids=two, n_compared=None))


def test_targets_is_a_list_of_target_records() -> None:
    adapter = TypeAdapter(TargetsFile)
    assert adapter.validate_json("[]") == []
    assert len(adapter.validate_json(json.dumps([target(), unfrozen_target()]))) == 2
    for invalid in ([target(shortlist_reason="search_not_run")], target(), [1]):
        with pytest.raises(ValidationError):
            adapter.validate_json(json.dumps(invalid))


# --- (7) TileLayer: 512 px tiles and the viridis or cividis scales ------------------------------


@pytest.mark.parametrize("tile_px", [256, 1024, 511, 0, "512", 512.5, True, None])
def test_tile_px_other_than_512_fails(tile_px: object) -> None:
    assert_rejected(TileLayer, tile_layer(tile_px=tile_px))


def test_tile_px_is_an_integer_512() -> None:
    # 512.0 is a float: pydantic keeps tile sizes integers, as it does every count.
    with pytest.raises(ValidationError):
        TileLayer.model_validate(tile_layer(tile_px=512.0))
    assert TileLayer.model_validate(tile_layer()).tile_px == 512


@pytest.mark.parametrize("scale", ["jet", "magma", "Viridis", "grey", "", None])
def test_a_numeric_legend_scale_other_than_viridis_or_cividis_fails(scale: object) -> None:
    assert_rejected(TileLayer, tile_layer(legend=numeric_legend(scale=scale)))


@pytest.mark.parametrize("scale", ["viridis", "cividis"])
def test_a_numeric_legend_takes_viridis_and_cividis(scale: str) -> None:
    layer = assert_accepted(TileLayer, tile_layer(legend=numeric_legend(scale=scale, unit="m")))
    assert isinstance(layer.legend, NumericLegend)
    assert layer.legend.scale == scale


def test_a_classes_legend_is_read_as_classes() -> None:
    layer = assert_accepted(TileLayer, tile_layer(legend=classes_legend()))
    assert isinstance(layer.legend, ClassesLegend)
    assert [c.code for c in layer.legend.classes] == [60, 80]


@pytest.mark.parametrize(
    "legend",
    [
        {**numeric_legend(), "kind": "classes"},
        {**classes_legend(), "kind": "numeric"},
        {**numeric_legend(), "kind": "continuous"},
        {key: value for key, value in numeric_legend().items() if key != "kind"},
        {**numeric_legend(), "classes": classes_legend()["classes"]},
        None,
    ],
    ids=["numeric-as-classes", "classes-as-numeric", "unknown-kind", "no-kind", "both", "null"],
)
def test_the_legend_is_a_union_on_kind(legend: object) -> None:
    assert_rejected(TileLayer, tile_layer(legend=legend))


def test_the_legend_field_is_discriminated_by_kind() -> None:
    schema = TypeAdapter(TileLayer).json_schema()
    assert schema["properties"]["legend"]["discriminator"]["propertyName"] == "kind"


@pytest.mark.parametrize(
    "legend",
    [
        classes_legend(classes=[]),
        classes_legend(classes=[legend_class(rgba=[180, 180, 180])]),
        classes_legend(classes=[legend_class(rgba=[180, 180, 180, 256])]),
        classes_legend(classes=[legend_class(rgba=[-1, 180, 180, 255])]),
        classes_legend(classes=[legend_class(rgba=[180.5, 180, 180, 255])]),
        classes_legend(classes=[legend_class(label="")]),
        classes_legend(classes=[legend_class(code=1.5)]),
        classes_legend(classes=[legend_class(), legend_class(label="Duplicate code")]),
        numeric_legend(min=1.875, max=0.125),
        numeric_legend(min=0.5, max=0.5),
        numeric_legend(min=float("inf")),
        numeric_legend(unit=""),
    ],
    ids=[
        "no-classes",
        "rgba-three",
        "rgba-above-255",
        "rgba-negative",
        "rgba-float",
        "empty-label",
        "code-float",
        "duplicate-code",
        "min-above-max",
        "min-equals-max",
        "min-infinite",
        "empty-unit",
    ],
)
def test_legend_rejects_invalid_values(legend: dict[str, Any]) -> None:
    assert_rejected(TileLayer, tile_layer(legend=legend))


def test_zoom_levels_run_from_min_to_max_with_one_pixel_size_each() -> None:
    # An empty pixel-size list matches the length rule for zoom 3 to 2, so only the order fails.
    reversed_zooms = tile_layer(
        zoom_min=3, zoom_max=2, metres_per_pixel_by_zoom=[], omitted_tiles=[]
    )
    assert_rejected(TileLayer, reversed_zooms)
    assert_rejected(TileLayer, tile_layer(metres_per_pixel_by_zoom=[20.0, 10.0]))
    assert_rejected(TileLayer, tile_layer(metres_per_pixel_by_zoom=[20.0, 10.0, 5.0, 2.5]))
    one = tile_layer(zoom_min=2, zoom_max=2, metres_per_pixel_by_zoom=[5.0], omitted_tiles=[])
    assert assert_accepted(TileLayer, one).zoom_min == 2


def zooms_one_to_two(tile: list[object]) -> dict[str, Any]:
    return tile_layer(zoom_min=1, metres_per_pixel_by_zoom=[10.0, 5.0], omitted_tiles=[tile])


def test_an_omitted_tile_within_the_zoom_range_is_accepted() -> None:
    for tile in ([1, 0, 0], [2, 3, 1]):
        assert assert_accepted(TileLayer, zooms_one_to_two(tile)).omitted_tiles == [tile]


@pytest.mark.parametrize(
    "tile", [[3, 0, 0], [0, 0, 0], [1, 0], [2, 0, 3, 1], [2, -1, 3], [2, 0.5, 3]]
)
def test_omitted_tiles_are_z_x_y_within_the_zoom_range(tile: list[object]) -> None:
    assert_rejected(TileLayer, zooms_one_to_two(tile))


@pytest.mark.parametrize(
    "extent",
    [
        [2560.0, -2560.0, -2560.0, 2560.0],
        [-2560.0, 2560.0, 2560.0, -2560.0],
        [0.5, -2560.0, 0.5, 2560.0],
        [-2560.0, -2560.0, 2560.0],
        [-2560.0, -2560.0, 2560.0, 2560.0, 0.5],
    ],
    ids=["x-reversed", "y-reversed", "zero-width", "three-numbers", "five-numbers"],
)
def test_extent_is_min_x_min_y_max_x_max_y(extent: list[float]) -> None:
    assert_rejected(TileLayer, tile_layer(extent_m=extent))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("layer", ""),
        ("layer", "site01/cpr"),
        ("layer", ".hidden"),
        ("layer", ".."),
        ("body", "venus"),
        ("crs", ""),
        ("units", ""),
        ("evidence_label", "observed"),
        ("source_product_ids", [""]),
        ("origin_x_m", float("nan")),
        ("y_direction", "north"),
        ("metres_per_pixel_by_zoom", [20.0, 0, 5.0]),
        ("zoom_min", -1),
        ("hillshade", hillshade(azimuth_deg=360.5)),
        ("hillshade", hillshade(azimuth_deg=-0.5)),
        ("hillshade", hillshade(altitude_deg=90.5)),
        ("hillshade", hillshade(altitude_deg=-0.5)),
        ("encoding", tile_encoding(format="png")),
        ("encoding", tile_encoding(quality=101)),
        ("encoding", tile_encoding(quality=-1)),
        ("encoding", tile_encoding(lossless="false")),
        ("bytes_total", -1),
        ("bytes_total", 2**53),
        ("mock", "false"),
    ],
)
def test_tile_layer_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(TileLayer, tile_layer(**{field: value}))


def test_a_hillshade_layer_records_its_light() -> None:
    layer = assert_accepted(
        TileLayer,
        tile_layer(
            layer="site01-dem-hillshade",
            units="m",
            legend=numeric_legend(min=-1250.5, max=375.25, scale="cividis", unit="m"),
            hillshade=hillshade(),
            encoding=tile_encoding(lossless=True, quality=None),
        ),
    )
    assert layer.hillshade is not None
    assert (layer.hillshade.azimuth_deg, layer.hillshade.altitude_deg) == (315.0, 45.0)


def test_a_target_holds_its_tile_layers() -> None:
    layers = [tile_layer(), tile_layer(layer="earth-worldcover", legend=classes_legend())]
    parsed = assert_accepted(TargetRecord, target(tile_layers=layers))
    assert [layer.layer for layer in parsed.tile_layers] == ["site01-mrf-cpr", "earth-worldcover"]
    assert_rejected(TargetRecord, target(tile_layers=[tile_layer(tile_px=256)]))


# --- (8) card_id and comparison_ref -------------------------------------------------------------


def test_card_id_is_the_target_and_earth_window_ids() -> None:
    parsed = assert_accepted(CardRecord, card())
    assert parsed.card_id == f"{parsed.target.target_window_id}__{parsed.earth.earth_window_id}"


@pytest.mark.parametrize(
    "card_id",
    [
        f"{EARTH_WINDOW}__{TARGET_WINDOW}",
        f"{TARGET_WINDOW}__earth-27n-0012-0041",
        f"moon-aeqd-0003-0008__{EARTH_WINDOW}",
        f"{TARGET_WINDOW}_{EARTH_WINDOW}",
        f"{TARGET_WINDOW}__{EARTH_WINDOW}\n",
        f"{TARGET_WINDOW}--{EARTH_WINDOW}",
        TARGET_WINDOW,
        "",
    ],
)
def test_a_card_id_other_than_target_window_id_and_earth_window_id_fails(card_id: str) -> None:
    assert_rejected(CardRecord, card(card_id=card_id))


@pytest.mark.parametrize(
    ("key", "window_id"),
    [
        ("target_window_id", "moon-aeqd-0003-0008"),
        ("earth_window_id", "earth-27n-0012-0041"),
    ],
)
def test_comparison_ref_window_ids_are_the_card_window_ids(key: str, window_id: str) -> None:
    value = card()
    value["comparison_ref"][key] = window_id
    assert_rejected(CardRecord, value)


def test_moving_both_window_ids_together_keeps_the_card_valid() -> None:
    other = "earth-27n-0012-0041"
    value = card(card_id=f"{TARGET_WINDOW}__{other}")
    value["earth"]["earth_window_id"] = other
    value["comparison_ref"]["earth_window_id"] = other
    assert assert_accepted(CardRecord, value).card_id == f"{TARGET_WINDOW}__{other}"


@pytest.mark.parametrize(
    "path",
    [
        "comparisons/mars-jezero-w000.json",
        f"comparisons/{TARGET_ID}.parquet",
        f"{TARGET_ID}.json",
        f"/comparisons/{TARGET_ID}.json",
        f"comparisons/../{TARGET_ID}.json",
    ],
)
def test_comparison_ref_path_is_the_target_comparisons_file(path: str) -> None:
    value = card()
    value["comparison_ref"]["path"] = path
    assert_rejected(CardRecord, value)


# --- (9) the four families ----------------------------------------------------------------------


@pytest.mark.parametrize("family", FAMILIES)
def test_a_card_whose_distances_by_family_lacks_a_family_fails(family: str) -> None:
    value = card()
    del value["distances_by_family"][family]
    assert_rejected(CardRecord, value)


def test_a_card_with_a_fifth_family_fails() -> None:
    value = card()
    value["distances_by_family"]["radar_sc"] = missing_distance()
    assert_rejected(CardRecord, value)


def test_a_card_model_embedding_value_must_be_predicted() -> None:
    entry = distance(unit="cosine distance", value=0.25)
    value = card(distances_by_family=distances(model_embedding=entry))
    tied, groups = ties_from(value["distances_by_family"])
    value["q5_stability"]["tied_by_family"] = tied
    value["q5_stability"]["tie_group_by_family"] = groups
    assert_rejected(CardRecord, value)
    value["distances_by_family"]["model_embedding"]["evidence_label"] = "Predicted"
    assert_accepted(CardRecord, value)


@pytest.mark.parametrize("key", ["tied_by_family", "tie_group_by_family"])
@pytest.mark.parametrize("family", FAMILIES)
def test_q5_ties_name_every_family(key: str, family: str) -> None:
    value = card()
    del value["q5_stability"][key][family]
    assert_rejected(CardRecord, value)


@pytest.mark.parametrize("key", ["tied_by_family", "tie_group_by_family"])
def test_q5_ties_name_no_other_family(key: str) -> None:
    value = card()
    value["q5_stability"][key]["radar_sc"] = None
    assert_rejected(CardRecord, value)


@pytest.mark.parametrize(
    ("family", "tied"),
    [("terrain_relief", False), ("terrain_texture", True), ("radar_cpr", False)],
)
def test_q5_tied_by_family_is_the_tied_flag_of_each_distance(family: str, tied: bool) -> None:
    value = card()
    value["q5_stability"]["tied_by_family"][family] = tied
    assert_rejected(CardRecord, value)


@pytest.mark.parametrize(
    ("family", "group"),
    [("terrain_relief", "relief-2"), ("terrain_relief", None), ("terrain_texture", "texture-1")],
)
def test_q5_tie_group_by_family_is_the_tie_group_of_each_distance(
    family: str, group: str | None
) -> None:
    value = card()
    value["q5_stability"]["tie_group_by_family"][family] = group
    assert_rejected(CardRecord, value)


# --- Mode B and C carry no radar distance (PRD 4) -----------------------------------------------


@pytest.mark.parametrize("mode", ["B", "C"])
def test_a_mode_b_or_c_card_has_no_radar_distance(mode: str) -> None:
    radar = distance(unit="CPR Wasserstein-1", value=0.125, tied=None, tie_group=None)
    radar.update(reference_spread=None, reference_spread_reason="spread_unavailable")
    value = card(shipping_mode=mode, distances_by_family=distances(radar_cpr=radar))
    assert_rejected(CardRecord, value)
    assert_accepted(CardRecord, {**value, "shipping_mode": "A"})


# --- Figures, radar context, q4 limits ----------------------------------------------------------


@pytest.mark.parametrize("figure", ["A", "B", "C"])
def test_a_figure_has_a_path_or_a_reason(figure: str) -> None:
    assert_accepted(FigureRef, figure_ref(figure=figure))
    assert_accepted(FigureRef, figure_ref(figure=figure, path=None, reason="figure_not_rendered"))
    assert_rejected(FigureRef, figure_ref(figure=figure, path=None, reason=None))
    assert_rejected(FigureRef, figure_ref(figure=figure, reason="figure_not_rendered"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("figure", "D"),
        ("figure", "a"),
        ("figure", None),
        ("path", "/figures/a.svg"),
        ("path", "../figures/a.svg"),
        ("path", ""),
        ("alt", ""),
    ],
)
def test_figure_ref_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(FigureRef, figure_ref(**{field: value}))


def test_a_card_shows_each_figure_at_most_once() -> None:
    value = card()
    value["figures"][1]["figure"] = "A"
    assert_rejected(CardRecord, value)
    assert_accepted(CardRecord, card(figures=[]))


def test_radar_context_keeps_missing_values_null_with_a_reason() -> None:
    entry = radar_context_entry(
        value=None, evidence_label="Unavailable", support_m=None, reason="qa_missing"
    )
    parsed = assert_accepted(RadarContextEntry, entry)
    assert (parsed.value, parsed.support_m) == (None, None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("product_id", ""),
        ("band", ""),
        ("observable", ""),
        ("statistic", ""),
        ("value", float("inf")),
        ("unit", ""),
        ("evidence_label", None),
        ("support_m", 0),
        ("reason", "QA missing"),
    ],
)
def test_radar_context_entry_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(RadarContextEntry, radar_context_entry(**{field: value}))


@pytest.mark.parametrize("limits", [EARTH_LIMITS, MARS_LIMITS], ids=["earth", "mars"])
def test_q4_limits_are_the_strings_the_writer_supplies(limits: list[str]) -> None:
    value = card()
    value["q4_differences"]["limits"] = list(limits)
    assert assert_accepted(CardRecord, value).q4_differences.limits == limits


@pytest.mark.parametrize("key", ["limits", "differing", "ambiguous", "not_observed"])
def test_q4_entries_are_non_empty_strings(key: str) -> None:
    value = card()
    value["q4_differences"][key] = [""]
    assert_rejected(CardRecord, value)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("run_id",), None),
        (("run_id",), HEX64),
        (("title",), ""),
        (("q1_compared",), ""),
        (("q2_interpretation", "text"), ""),
        (("q7_field_experiment", "text"), ""),
        (("target", "target_id"), "Moon-cr"),
        (("target", "target_window_id"), "moon"),
        (("target", "body"), "venus"),
        (("earth", "earth_window_id"), "earth"),
        (("earth", "search_region_id"), ""),
        (("earth", "product_ids"), [""]),
        (("claim_limits",), [""]),
        (("mock",), "false"),
    ],
)
def test_card_record_rejects_invalid_values(path: tuple[str, ...], value: object) -> None:
    document = card()
    target_part = document
    for key in path[:-1]:
        target_part = target_part[key]
    target_part[path[-1]] = value
    assert_rejected(CardRecord, document)


def test_a_card_round_trips_through_json_unchanged() -> None:
    value = card()
    parsed = CardRecord.model_validate_json(json.dumps(value))
    assert json.loads(parsed.model_dump_json()) == value


def test_a_real_zero_distance_on_a_card_stays_zero() -> None:
    parsed = CardRecord.model_validate_json(json.dumps(card()))
    assert parsed.distances_by_family["terrain_texture"].value == 0
    assert (
        json.loads(parsed.model_dump_json())["distances_by_family"]["terrain_texture"]["value"] == 0
    )
