"""Contract tests for the science records I (CONTRACTS a.2 and a.4; PRD 3, 6.5, 6.6, 8.12).

WindowRecord and ComparisonRecord carry exactly the PRD 8.12 field names, which are read from
PRD.md. The nested records carry exactly the CONTRACTS a.4 field names, listed here. Values in
these tests are schema test inputs, not observations or results.
"""

from __future__ import annotations

import copy
import json
import re
import typing
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from terraluna_pipeline.schema import (
    CONTRACTS,
    SCHEMA_VERSION,
    ComparisonRecord,
    ComparisonsFile,
    DistanceEntry,
    DistancesByFamily,
    Distribution,
    EnvironmentFlags,
    EvidenceLabel,
    ExcludedObservable,
    Family,
    FamilySpread,
    FingerprintRecord,
    IncidenceRange,
    ModelEmbeddingDistanceEntry,
    ObservedInterpolatedQuality,
    RadarFingerprint,
    RankFrequency,
    ReasonCode,
    ReferenceSpread,
    RepoPath,
    RunId,
    Scenario,
    TerrainFingerprint,
    TerrainVariant,
    UncertaintyComponent,
    UncertaintyComponents,
    UnsupportedLag,
    WindowEntry,
    WindowId,
    WindowPolygon,
    WindowRecord,
    WindowsFile,
)

PRD = Path(__file__).resolve().parents[2] / "PRD.md"
HEX64 = "0123456789abcdef" * 4
RUN_ID = "sha256:" + HEX64
FAMILIES = ["terrain_relief", "terrain_texture", "radar_cpr", "model_embedding"]
COMPONENTS = [
    "heterogeneity",
    "measurement_error",
    "statistical_prediction_error",
    "scenario_sensitivity",
]


# --- PRD extraction -----------------------------------------------------------------------------


def prd_section(heading: str) -> str:
    text = PRD.read_text(encoding="utf-8")
    start = text.index(f"\n{heading}\n")
    end = text.find("\n#", start + len(heading) + 2)
    return text[start : end if end != -1 else len(text)]


def prd_record_fields(record: str) -> list[str]:
    """The field names PRD 8.12 lists for ``record``, in order."""
    line = next(
        line
        for line in prd_section("### 8.12 Records").splitlines()
        if line.startswith(f"- **{record}:**")
    )
    return re.findall(r"`([^`]+)`", line)


def prd_evidence_labels() -> list[str]:
    """The labels of the PRD 3 evidence-label table, in order."""
    lines = prd_section("### Evidence labels").splitlines()
    separator = next(i for i, line in enumerate(lines) if line.startswith("| ---"))
    rows = [line for line in lines[separator + 1 :] if line.startswith("|")]
    return [row.split("|")[1].strip() for row in rows]


def literal_values(annotation: object) -> list[object]:
    if typing.get_origin(annotation) is typing.Literal:
        return list(typing.get_args(annotation))
    values: list[object] = []
    for arg in typing.get_args(annotation):
        values += literal_values(arg)
    return values


# --- Valid examples -----------------------------------------------------------------------------


def distribution(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "n": 3,
        "values": [0.25, 0.5, 0.75],
        "min": 0.25,
        "median": 0.5,
        "p05": 0.275,
        "p95": 0.725,
        "max": 0.75,
    }
    value.update(overrides)
    return value


def empty_distribution() -> dict[str, Any]:
    return {
        "n": 0,
        "values": [],
        "min": None,
        "median": None,
        "p05": None,
        "p95": None,
        "max": None,
    }


RING_M = [[-2500.0, -2500.0], [2500.0, -2500.0], [2500.0, 2500.0], [-2500.0, 2500.0]]
RING_LONLAT = [[10.25, -89.5], [12.5, -89.5], [12.5, -89.25], [10.25, -89.25]]


def closed(ring: list[list[float]]) -> list[list[float]]:
    return [*ring, ring[0]]


def polygon(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "frame": "aeqd_sphere",
        "crs": "+proj=aeqd +lat_0=-89.375 +lon_0=11.375 +R=1737400 +units=m",
        "epsg": None,
        "utm_zone": None,
        "ring_m": closed(RING_M),
        "ring_lonlat": closed(RING_LONLAT),
    }
    value.update(overrides)
    return value


def environment_flags(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "search_region_id": None,
        "scene_ids": [],
        "acquisition_dates": [],
        "snow_or_freeze": None,
        "moisture_state": None,
        "surface_change_since_acquisition": None,
        "permanent_shadow_fraction": 0.125,
        "surface_model_note": None,
    }
    value.update(overrides)
    return value


def observed_interpolated_quality(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "observed_fraction": 0.125,
        "interpolated_fraction": 0.875,
        "source": "PGDA78 count map",
        "reason": None,
    }
    value.update(overrides)
    return value


def window_record(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "window_id": "moon-aeqd-0003-0007",
        "body": "moon",
        "polygon": polygon(),
        "area_m2": 25000000.0,
        "analysis_grid_m": 90.0,
        "product_ids": ["pgda78-site01-dem-5m"],
        "valid_fraction_by_observable": {"terrain": 0.875, "radar_cpr": None},
        "observed_interpolated_quality": observed_interpolated_quality(),
        "environment_flags": environment_flags(),
        "evidence_status_by_field": {"permanent_shadow_fraction": "Derived"},
    }
    value.update(overrides)
    return value


def unsupported_lag(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"lag_m": 1080.0, "reason": "below_min_valid_fraction"}
    value.update(overrides)
    return value


def terrain_variant(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "detrend": "none",
        "supported_lags_m": [270.0, 360.0, 540.0, 720.0],
        "unsupported_lags": [unsupported_lag()],
        "nu_m": {"270": 1.5, "360": 1.75, "540": 2.25, "720": 2.5, "1080": None},
        "nu_by_direction_m": {"row": {"270": 1.25}, "column": {"270": 1.75, "1080": None}},
        "hurst": 0.625,
        "hurst_reason": None,
        "tilt": None,
    }
    value.update(overrides)
    return value


def terrain_fingerprint(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "analysis_grid_m": 90.0,
        "coarsening_factor": None,
        "resampler": "aeqd_sphere",
        "valid_fraction": 0.875,
        "relief": terrain_variant(),
        "texture": terrain_variant(detrend="plane", tilt=[0.0125, -0.025, 3.5]),
    }
    value.update(overrides)
    return value


def incidence_range(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"min": 35.5, "max": 48.25}
    value.update(overrides)
    return value


def radar_fingerprint(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "product_id": "mrf-swath-example",
        "band": "S",
        "frequency_or_wavelength": "12.6 cm",
        "convention": None,
        "support_m": 250.0,
        "aggregation": "ratio_of_mean_powers",
        "blocks_independent": False,
        "n_blocks": 400,
        "n_valid": 3,
        "valid_fraction": 0.0075,
        "cpr_ratio_of_mean_powers": distribution(),
        "cpr_mean_of_pixelwise_ratios": distribution(),
        "sc": None,
        "oc": None,
        "total_power": None,
        "incidence_deg": incidence_range(),
        "handedness_ratio": None,
        "reason": None,
    }
    value.update(overrides)
    return value


def fingerprint(kind: str = "terrain", **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "window_id": "moon-aeqd-0003-0007",
        "body": "moon",
        "kind": kind,
        "product_ids": ["pgda78-site01-dem-5m"],
        "evidence_label": "Derived",
        "input_hash": HEX64,
        "run_id": RUN_ID,
        "terrain": terrain_fingerprint() if kind == "terrain" else None,
        "radar": radar_fingerprint() if kind == "radar" else None,
    }
    value.update(overrides)
    return value


def window_entry(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "window": window_record(),
        "terrain": fingerprint("terrain"),
        "radar": [fingerprint("radar")],
        "status": "eligible",
        "reasons": [],
        "grid_index": [3, 7],
        "card_id": None,
    }
    value.update(overrides)
    return value


def reference_spread(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"min": 0.125, "median": 0.25, "max": 0.5, "n": 12}
    value.update(overrides)
    return value


def distance(**overrides: Any) -> dict[str, Any]:
    """An available distance with a reference spread and a background percentile."""
    value: dict[str, Any] = {
        "value": 0.375,
        "unit": "RMS of log nu",
        "evidence_label": "Derived",
        "status": "available",
        "reason": None,
        "reference_spread": reference_spread(),
        "reference_spread_reason": None,
        "background_percentile": 12.5,
        "background_percentile_reason": None,
        "tied": False,
        "tie_group": None,
        "tie_reason": None,
    }
    value.update(overrides)
    return value


def missing_distance(status: str = "unavailable", **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "value": None,
        "unit": "CPR Wasserstein-1",
        "evidence_label": "Unavailable",
        "status": status,
        "reason": "shipping_mode_unset",
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
        "terrain_relief": distance(),
        "terrain_texture": distance(value=0.0),
        "radar_cpr": missing_distance(),
        "model_embedding": missing_distance(
            "not_comparable", unit="cosine distance", reason="g5_not_pass"
        ),
    }
    value.update(overrides)
    return value


def family_spread(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "spread_min": 0.0625,
        "spread_median": 0.125,
        "spread_max": 0.25,
        "n": 100,
    }
    value.update(overrides)
    return value


def rank_frequency(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"frequency": 0.75, "count": 75, "n": 100}
    value.update(overrides)
    return value


def scenario(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "name": "footprint-roi-union",
        "change": "footprint: Site01 ROI union instead of the frozen window",
        "values_by_family": {"terrain_relief": 0.375, "radar_cpr": None},
        "rank_by_family": {"terrain_relief": 2, "radar_cpr": None},
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


def uncertainty(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "heterogeneity": component(),
        "measurement_error": component(
            status="available",
            evidence_label="Derived",
            reason=None,
            note="The clones cover the published topographic error model only (PRD 6.6).",
            by_family={"terrain_relief": family_spread()},
            n_requested=100,
            n_used=100,
            rank_frequency={"terrain_relief": rank_frequency()},
        ),
        "statistical_prediction_error": component(reason="encoder_not_evaluated"),
        "scenario_sensitivity": component(
            status="available", evidence_label="Assumed", reason=None, scenarios=[scenario()]
        ),
    }
    value.update(overrides)
    return value


def excluded_observable(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"observable": "radar_cpr", "reasons": ["shipping_mode_unset"]}
    value.update(overrides)
    return value


def comparison(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "target_window_id": "moon-aeqd-0003-0007",
        "earth_window_id": "earth-27n-0012-0040",
        "shipping_mode": None,
        "comparable_observables": ["terrain_relief", "terrain_texture"],
        "excluded_observables_and_reasons": [excluded_observable()],
        "distances_by_family": distances(),
        "uncertainty_components": uncertainty(),
        "sort_rule": "pareto layers over terrain_relief, terrain_texture; then window_id",
        "claim_limits": ["Earth ground is not lunar-dry (PRD 5)"],
        "source_ids": ["pgda78-site01-dem-5m", "copernicus-dem-glo-30-2021-1"],
        "run_id": RUN_ID,
    }
    value.update(overrides)
    return value


EXAMPLES: list[tuple[type[BaseModel], Callable[[], dict[str, Any]]]] = [
    (Distribution, distribution),
    (WindowPolygon, polygon),
    (EnvironmentFlags, environment_flags),
    (ObservedInterpolatedQuality, observed_interpolated_quality),
    (WindowRecord, window_record),
    (UnsupportedLag, unsupported_lag),
    (TerrainVariant, terrain_variant),
    (TerrainFingerprint, terrain_fingerprint),
    (IncidenceRange, incidence_range),
    (RadarFingerprint, radar_fingerprint),
    (FingerprintRecord, fingerprint),
    (WindowEntry, window_entry),
    (ReferenceSpread, reference_spread),
    (DistanceEntry, distance),
    (ModelEmbeddingDistanceEntry, missing_distance),
    (DistancesByFamily, distances),
    (FamilySpread, family_spread),
    (RankFrequency, rank_frequency),
    (Scenario, scenario),
    (UncertaintyComponent, component),
    (UncertaintyComponents, uncertainty),
    (ExcludedObservable, excluded_observable),
    (ComparisonRecord, comparison),
]
EXAMPLE_IDS = [model.__name__ for model, _ in EXAMPLES]

# CONTRACTS a.4: the field names of every nested record, in order.
CONTRACT_FIELDS: dict[type[BaseModel], list[str]] = {
    Distribution: ["n", "values", "min", "median", "p05", "p95", "max"],
    WindowPolygon: ["frame", "crs", "epsg", "utm_zone", "ring_m", "ring_lonlat"],
    EnvironmentFlags: [
        "search_region_id",
        "scene_ids",
        "acquisition_dates",
        "snow_or_freeze",
        "moisture_state",
        "surface_change_since_acquisition",
        "permanent_shadow_fraction",
        "surface_model_note",
    ],
    ObservedInterpolatedQuality: [
        "observed_fraction",
        "interpolated_fraction",
        "source",
        "reason",
    ],
    UnsupportedLag: ["lag_m", "reason"],
    TerrainVariant: [
        "detrend",
        "supported_lags_m",
        "unsupported_lags",
        "nu_m",
        "nu_by_direction_m",
        "hurst",
        "hurst_reason",
        "tilt",
    ],
    TerrainFingerprint: [
        "analysis_grid_m",
        "coarsening_factor",
        "resampler",
        "valid_fraction",
        "relief",
        "texture",
    ],
    IncidenceRange: ["min", "max"],
    RadarFingerprint: [
        "product_id",
        "band",
        "frequency_or_wavelength",
        "convention",
        "support_m",
        "aggregation",
        "blocks_independent",
        "n_blocks",
        "n_valid",
        "valid_fraction",
        "cpr_ratio_of_mean_powers",
        "cpr_mean_of_pixelwise_ratios",
        "sc",
        "oc",
        "total_power",
        "incidence_deg",
        "handedness_ratio",
        "reason",
    ],
    FingerprintRecord: [
        "window_id",
        "body",
        "kind",
        "product_ids",
        "evidence_label",
        "input_hash",
        "run_id",
        "terrain",
        "radar",
    ],
    WindowEntry: ["window", "terrain", "radar", "status", "reasons", "grid_index", "card_id"],
    ReferenceSpread: ["min", "median", "max", "n"],
    DistanceEntry: [
        "value",
        "unit",
        "evidence_label",
        "status",
        "reason",
        "reference_spread",
        "reference_spread_reason",
        "background_percentile",
        "background_percentile_reason",
        "tied",
        "tie_group",
        "tie_reason",
    ],
    DistancesByFamily: FAMILIES,
    FamilySpread: ["spread_min", "spread_median", "spread_max", "n"],
    RankFrequency: ["frequency", "count", "n"],
    Scenario: ["name", "change", "values_by_family", "rank_by_family"],
    UncertaintyComponent: [
        "status",
        "evidence_label",
        "reason",
        "note",
        "by_family",
        "n_requested",
        "n_used",
        "scenarios",
        "rank_frequency",
    ],
    UncertaintyComponents: COMPONENTS,
    ExcludedObservable: ["observable", "reasons"],
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


def model_types(model: type[BaseModel], seen: set[type[BaseModel]]) -> Iterator[type[BaseModel]]:
    """``model`` and every record reachable from its fields."""
    if model in seen:
        return
    seen.add(model)
    yield model

    def walk(annotation: object) -> Iterator[type[BaseModel]]:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield from model_types(annotation, seen)
        for arg in typing.get_args(annotation):
            yield from walk(arg)

    for field in model.model_fields.values():
        yield from walk(field.annotation)


# --- (1) PRD 8.12 and CONTRACTS a.4 field names -------------------------------------------------


def test_window_record_fields_are_exactly_prd_8_12() -> None:
    fields = prd_record_fields("WindowRecord")
    assert len(fields) == 10
    assert list(WindowRecord.model_fields) == fields


def test_comparison_record_fields_are_exactly_prd_8_12() -> None:
    fields = prd_record_fields("ComparisonRecord")
    assert len(fields) == 11
    assert list(ComparisonRecord.model_fields) == fields


@pytest.mark.parametrize("model", list(CONTRACT_FIELDS), ids=lambda m: m.__name__)
def test_nested_record_fields_are_exactly_contracts_a4(model: type[BaseModel]) -> None:
    assert list(model.model_fields) == CONTRACT_FIELDS[model]


def test_model_embedding_distance_entry_is_a_distance_entry_with_the_same_fields() -> None:
    assert issubclass(ModelEmbeddingDistanceEntry, DistanceEntry)
    assert list(ModelEmbeddingDistanceEntry.model_fields) == CONTRACT_FIELDS[DistanceEntry]


def test_every_record_is_reachable_and_covered() -> None:
    reachable = {
        model for root in (WindowEntry, ComparisonRecord) for model in model_types(root, set())
    }
    assert reachable == {model for model, _ in EXAMPLES}
    assert reachable - {WindowRecord, ComparisonRecord} == set(CONTRACT_FIELDS) | {
        ModelEmbeddingDistanceEntry,
        FingerprintRecord,
        WindowEntry,
    } - {WindowRecord, ComparisonRecord}


# --- Common types (CONTRACTS a.2) ---------------------------------------------------------------


def test_evidence_labels_are_the_prd_3_labels() -> None:
    labels = prd_evidence_labels()
    assert labels == ["Observed", "Derived", "Predicted", "Assumed", "Unavailable"]
    assert literal_values(EvidenceLabel) == labels


def test_families_are_the_four_contract_families() -> None:
    assert literal_values(Family) == FAMILIES


@pytest.mark.parametrize("code", ["nu_zero", "below_min_valid_fraction", "a", "g5_not_pass", "x1"])
def test_reason_code_accepts_snake_case(code: str) -> None:
    assert TypeAdapter(ReasonCode).validate_python(code) == code


@pytest.mark.parametrize(
    "code", ["", "Nu_zero", "1st", "_a", "nu-zero", "nu zero", "nu_zero\n", "ü", None, 0]
)
def test_reason_code_rejects_anything_else(code: object) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ReasonCode).validate_python(code)


def test_run_id_accepts_the_prd_example_form() -> None:
    assert TypeAdapter(RunId).validate_python(RUN_ID) == RUN_ID


@pytest.mark.parametrize(
    "run_id",
    [HEX64, "sha256:" + HEX64.upper(), "sha256:" + HEX64[:63], RUN_ID + "\n", "SHA256:" + HEX64],
)
def test_run_id_rejects_other_forms(run_id: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(RunId).validate_python(run_id)


@pytest.mark.parametrize(
    "window_id", ["moon-a", "earth-27n-0012-0040", "mars-jezero-w000", "moon-cr-site01-w000"]
)
def test_window_id_accepts_body_prefixed_ids(window_id: str) -> None:
    assert TypeAdapter(WindowId).validate_python(window_id) == window_id


@pytest.mark.parametrize(
    "window_id",
    [
        "",
        "moon",
        "moon-",
        "moon--a",
        "Moon-a",
        "venus-a",
        "moon-A",
        "moon-a_b",
        "moon-a\n",
        " moon-a",
    ],
)
def test_window_id_rejects_anything_else(window_id: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(WindowId).validate_python(window_id)


@pytest.mark.parametrize("path", ["outputs/gates/G0.json", "config/run.yaml", "a", "a-b/c_d.e"])
def test_repo_path_accepts_relative_posix_paths(path: str) -> None:
    assert TypeAdapter(RepoPath).validate_python(path) == path


@pytest.mark.parametrize(
    "path", ["", "/abs/path", "../up", "a/../b", "./a", "a//b", "a/", ".hidden", "a\\b", "a\n"]
)
def test_repo_path_rejects_absolute_escaping_or_malformed_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(RepoPath).validate_python(path)


# --- (2) every field required, (3) unknown fields rejected --------------------------------------


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


def test_no_record_has_a_score_combined_weight_or_probability_field() -> None:
    # D07: radar and terrain distances stay separate; there is no universal analog score.
    forbidden = re.compile(r"score|combined|weight|probability")
    for model, _ in EXAMPLES:
        for name in model.model_fields:
            assert not forbidden.search(name), f"{model.__name__}.{name}"


@pytest.mark.parametrize(("model", "example"), EXAMPLES, ids=EXAMPLE_IDS)
def test_records_are_frozen(model: type[BaseModel], example: Callable[[], dict[str, Any]]) -> None:
    record = model.model_validate(example())
    name = next(iter(model.model_fields))
    with pytest.raises(ValidationError):
        setattr(record, name, getattr(record, name))


def test_schema_version_stays_1_0_0() -> None:
    assert SCHEMA_VERSION == "1.0.0"


# --- Distribution -------------------------------------------------------------------------------


def test_an_empty_distribution_has_null_order_statistics() -> None:
    parsed = assert_accepted(Distribution, empty_distribution())
    assert (parsed.min, parsed.median, parsed.p05, parsed.p95, parsed.max) == (None,) * 5


@pytest.mark.parametrize("statistic", ["min", "median", "p05", "p95", "max"])
def test_n_zero_with_an_order_statistic_set_fails(statistic: str) -> None:
    assert_rejected(Distribution, {**empty_distribution(), statistic: 0.5})


@pytest.mark.parametrize("statistic", ["min", "median", "p05", "p95", "max"])
def test_n_positive_with_a_null_order_statistic_fails(statistic: str) -> None:
    assert_rejected(Distribution, distribution(**{statistic: None}))


def test_distribution_values_must_be_sorted_ascending() -> None:
    assert_rejected(Distribution, distribution(values=[0.5, 0.25, 0.75]))
    assert_accepted(Distribution, distribution(values=[0.25, 0.25, 0.75]))


@pytest.mark.parametrize(
    ("n", "values"), [(2, [0.25, 0.5, 0.75]), (4, [0.25, 0.5, 0.75]), (0, [0.5]), (1, [])]
)
def test_n_is_the_number_of_values(n: int, values: list[float]) -> None:
    statistics = (
        {"min": 0.5, "median": 0.5, "p05": 0.5, "p95": 0.5, "max": 0.5}
        if n
        else {key: None for key in ("min", "median", "p05", "p95", "max")}
    )
    assert_rejected(Distribution, {"n": n, "values": values, **statistics})


@pytest.mark.parametrize("n", [-1, 1.5, True, "3"])
def test_n_is_a_non_negative_integer(n: object) -> None:
    assert_rejected(Distribution, distribution(n=n))


# --- WindowPolygon, EnvironmentFlags, WindowRecord ----------------------------------------------


@pytest.mark.parametrize("frame", ["utm", "polar_stereographic", "equirectangular", "aeqd_sphere"])
def test_polygon_accepts_each_frame(frame: str) -> None:
    assert_accepted(WindowPolygon, polygon(frame=frame))


@pytest.mark.parametrize("frame", ["UTM", "geographic", "", None])
def test_polygon_rejects_other_frames(frame: object) -> None:
    assert_rejected(WindowPolygon, polygon(frame=frame))


def test_an_earth_utm_polygon_names_its_zone_and_epsg() -> None:
    parsed = assert_accepted(
        WindowPolygon, polygon(frame="utm", crs="EPSG:32627", epsg=32627, utm_zone="27N")
    )
    assert (parsed.epsg, parsed.utm_zone) == (32627, "27N")


@pytest.mark.parametrize("ring", ["ring_m", "ring_lonlat"])
def test_a_ring_that_is_not_closed_fails(ring: str) -> None:
    open_ring = RING_M if ring == "ring_m" else RING_LONLAT
    assert_rejected(WindowPolygon, polygon(**{ring: [*open_ring, open_ring[1]]}))
    assert_rejected(WindowPolygon, polygon(**{ring: open_ring}))


@pytest.mark.parametrize("ring", ["ring_m", "ring_lonlat"])
def test_a_closed_ring_needs_four_positions(ring: str) -> None:
    assert_rejected(WindowPolygon, polygon(**{ring: [[0.5, 0.5], [1.5, 0.5], [0.5, 0.5]]}))


@pytest.mark.parametrize(
    "position", [[0.5], [0.5, 0.5, 0.5], [], ["0.5", 0.5], [None, 0.5], [True, 0.5]]
)
def test_a_ring_position_is_two_numbers(position: list[object]) -> None:
    ring = [position, [1.5, 0.5], [1.5, 1.5], position]
    assert_rejected(WindowPolygon, polygon(ring_m=ring))


@pytest.mark.parametrize("crs", ["", None])
def test_polygon_crs_is_never_assumed(crs: object) -> None:
    assert_rejected(WindowPolygon, polygon(crs=crs))


@pytest.mark.parametrize("fraction", [0, 0.5, 1, None])
def test_permanent_shadow_fraction_accepts_0_to_1_or_null(fraction: object) -> None:
    assert_accepted(EnvironmentFlags, environment_flags(permanent_shadow_fraction=fraction))


@pytest.mark.parametrize("fraction", [-0.25, 1.5, "0.5", True])
def test_permanent_shadow_fraction_rejects_values_outside_0_to_1(fraction: object) -> None:
    assert_rejected(EnvironmentFlags, environment_flags(permanent_shadow_fraction=fraction))


def test_an_earth_window_lists_its_region_scenes_and_dates() -> None:
    assert_accepted(
        EnvironmentFlags,
        environment_flags(
            search_region_id="holuhraun",
            scene_ids=["uavsar-holuhraun-example"],
            acquisition_dates=["2015-05-30"],
            snow_or_freeze="unknown",
            moisture_state="unknown",
            surface_change_since_acquisition="unknown",
            permanent_shadow_fraction=None,
            surface_model_note="Copernicus DEM is a surface model including vegetation",
        ),
    )


@pytest.mark.parametrize("date", ["30 May 2015", "2015-5-30", "2015-13-01", "2015-05-30\n", ""])
def test_acquisition_dates_are_iso_dates(date: str) -> None:
    assert_rejected(EnvironmentFlags, environment_flags(acquisition_dates=[date]))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("area_m2", 0),
        ("area_m2", -25000000.0),
        ("analysis_grid_m", 0),
        ("window_id", "moon_a"),
        ("body", "venus"),
        ("product_ids", [""]),
        ("valid_fraction_by_observable", {"terrain": 1.5}),
        ("valid_fraction_by_observable", {"": 0.5}),
        ("evidence_status_by_field", {"radar_cpr": "Missing"}),
        ("observed_interpolated_quality", observed_interpolated_quality(observed_fraction=2.5)),
        ("observed_interpolated_quality", observed_interpolated_quality(reason="Not Code")),
    ],
)
def test_window_record_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(WindowRecord, window_record(**{field: value}))


def test_window_record_keeps_null_fractions_as_null() -> None:
    parsed = assert_accepted(WindowRecord, window_record())
    assert parsed.valid_fraction_by_observable["radar_cpr"] is None
    dumped = json.loads(parsed.model_dump_json())
    assert dumped["valid_fraction_by_observable"] == {"terrain": 0.875, "radar_cpr": None}


# --- TerrainVariant, RadarFingerprint, FingerprintRecord ----------------------------------------


def test_hurst_unavailable_carries_a_reason() -> None:
    assert_accepted(TerrainVariant, terrain_variant(hurst=None, hurst_reason="insufficient_lags"))


@pytest.mark.parametrize(
    ("hurst", "reason"), [(None, None), (0.625, "insufficient_lags")], ids=["both-null", "both-set"]
)
def test_exactly_one_of_hurst_and_hurst_reason_is_null(hurst: object, reason: object) -> None:
    assert_rejected(TerrainVariant, terrain_variant(hurst=hurst, hurst_reason=reason))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("detrend", "linear"),
        ("supported_lags_m", [0]),
        ("supported_lags_m", [-270.0]),
        ("unsupported_lags", [unsupported_lag(reason="")]),
        ("unsupported_lags", [unsupported_lag(lag_m=0)]),
        ("nu_m", {"270": -1.5}),
        ("nu_m", {"": 1.5}),
        ("nu_by_direction_m", {"row": {"270": "1.5"}}),
        ("tilt", ["0.5"]),
    ],
)
def test_terrain_variant_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(TerrainVariant, terrain_variant(**{field: value}))


def test_a_nu_of_zero_is_kept_as_a_real_zero() -> None:
    parsed = assert_accepted(TerrainVariant, terrain_variant(nu_m={"270": 0.0}))
    assert parsed.nu_m["270"] == 0.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("resampler", "bilinear"),
        ("valid_fraction", 1.25),
        ("analysis_grid_m", 0),
        ("coarsening_factor", 0),
    ],
)
def test_terrain_fingerprint_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(TerrainFingerprint, terrain_fingerprint(**{field: value}))


def test_blocks_independent_accepts_false() -> None:
    parsed = assert_accepted(RadarFingerprint, radar_fingerprint())
    assert parsed.blocks_independent is False


@pytest.mark.parametrize("value", [True, 0, "false", None])
def test_blocks_independent_accepts_only_false(value: object) -> None:
    assert_rejected(RadarFingerprint, radar_fingerprint(blocks_independent=value))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("aggregation", "mean_of_pixelwise_ratios"),
        ("support_m", 0),
        ("n_blocks", -1),
        ("n_valid", 2.5),
        ("valid_fraction", 1.5),
        ("band", ""),
        ("cpr_ratio_of_mean_powers", distribution(n=2)),
        ("incidence_deg", {"min": 35.5}),
        ("reason", "No-Valid-Blocks"),
    ],
)
def test_radar_fingerprint_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(RadarFingerprint, radar_fingerprint(**{field: value}))


def test_a_radar_fingerprint_without_valid_blocks_keeps_empty_distributions() -> None:
    assert_accepted(
        RadarFingerprint,
        radar_fingerprint(
            n_valid=0,
            valid_fraction=0,
            cpr_ratio_of_mean_powers=empty_distribution(),
            cpr_mean_of_pixelwise_ratios=None,
            incidence_deg=None,
            reason="no_valid_blocks",
        ),
    )


@pytest.mark.parametrize("kind", ["terrain", "radar"])
def test_a_fingerprint_holds_the_part_its_kind_names(kind: str) -> None:
    assert_accepted(FingerprintRecord, fingerprint(kind))


def test_kind_terrain_with_radar_set_fails() -> None:
    assert_rejected(FingerprintRecord, fingerprint("terrain", radar=radar_fingerprint()))
    assert_rejected(
        FingerprintRecord, fingerprint("terrain", terrain=None, radar=radar_fingerprint())
    )


def test_kind_radar_with_terrain_set_fails() -> None:
    assert_rejected(FingerprintRecord, fingerprint("radar", terrain=terrain_fingerprint()))
    assert_rejected(
        FingerprintRecord, fingerprint("radar", terrain=terrain_fingerprint(), radar=None)
    )


@pytest.mark.parametrize("kind", ["terrain", "radar"])
def test_a_fingerprint_without_its_part_fails(kind: str) -> None:
    assert_rejected(FingerprintRecord, fingerprint(kind, terrain=None, radar=None))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "model"),
        ("input_hash", HEX64.upper()),
        ("run_id", HEX64),
        ("evidence_label", "derived"),
        ("window_id", "moon"),
    ],
)
def test_fingerprint_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(FingerprintRecord, {**fingerprint(), field: value})


# --- WindowEntry and Windows --------------------------------------------------------------------


def test_an_eligible_window_has_no_reasons() -> None:
    assert_rejected(WindowEntry, window_entry(reasons=["land_cover_below_min_fraction"]))


@pytest.mark.parametrize("status", ["rejected", "unknown"])
def test_a_rejected_or_unknown_window_has_reasons(status: str) -> None:
    assert_rejected(WindowEntry, window_entry(status=status, reasons=[]))
    parsed = assert_accepted(
        WindowEntry,
        window_entry(status=status, reasons=["worldcover_missing", "dem_valid_fraction_below_min"]),
    )
    assert parsed.reasons == ["worldcover_missing", "dem_valid_fraction_below_min"]


def test_a_window_without_fingerprints_is_accepted() -> None:
    assert_accepted(
        WindowEntry,
        window_entry(terrain=None, radar=[], status="unknown", reasons=["cache_missing"]),
    )


def test_the_terrain_slot_holds_a_terrain_fingerprint() -> None:
    assert_rejected(WindowEntry, window_entry(terrain=fingerprint("radar")))


def test_the_radar_slot_holds_radar_fingerprints() -> None:
    assert_rejected(WindowEntry, window_entry(radar=[fingerprint("radar"), fingerprint("terrain")]))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "eligible_with_warnings"),
        ("reasons", ["Bad Reason"]),
        ("grid_index", [3]),
        ("grid_index", [3, 7, 1]),
        ("grid_index", [3, 7.5]),
        ("grid_index", [-1, 7]),
        ("card_id", ""),
    ],
)
def test_window_entry_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(WindowEntry, window_entry(**{field: value}))


def test_windows_is_a_list_of_window_entries() -> None:
    adapter = TypeAdapter(WindowsFile)
    assert adapter.validate_python([]) == []
    assert len(adapter.validate_json(json.dumps([window_entry(), window_entry()]))) == 2
    with pytest.raises(ValidationError):
        adapter.validate_python(window_entry())
    with pytest.raises(ValidationError):
        adapter.validate_python([window_entry(), {**window_entry(), "score": 1.5}])


# --- DistanceEntry ------------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["unavailable", "not_comparable"])
def test_a_missing_distance_is_null_with_a_reason(status: str) -> None:
    parsed = assert_accepted(DistanceEntry, missing_distance(status))
    assert parsed.value is None
    assert parsed.reason == "shipping_mode_unset"


def test_value_null_with_reason_null_fails() -> None:
    assert_rejected(DistanceEntry, missing_distance(reason=None))


def test_value_and_reason_both_set_fails() -> None:
    assert_rejected(DistanceEntry, distance(reason="spread_unavailable"))


@pytest.mark.parametrize("status", ["unavailable", "not_comparable"])
def test_a_value_needs_status_available(status: str) -> None:
    assert_rejected(DistanceEntry, distance(status=status))
    assert_rejected(DistanceEntry, distance(status=status, reason="band_mismatch"))


def test_status_available_needs_a_value() -> None:
    assert_rejected(DistanceEntry, distance(value=None))
    assert_rejected(DistanceEntry, missing_distance(status="available"))


@pytest.mark.parametrize("value", [-0.5, "0.5", True])
def test_a_distance_is_a_non_negative_number(value: object) -> None:
    assert_rejected(DistanceEntry, distance(value=value))


@pytest.mark.parametrize(
    ("spread", "reason"),
    [(None, None), (reference_spread(), "spread_unavailable")],
    ids=["both-null", "both-set"],
)
def test_exactly_one_of_reference_spread_and_its_reason_is_null(
    spread: object, reason: object
) -> None:
    assert_rejected(
        DistanceEntry, distance(reference_spread=spread, reference_spread_reason=reason, tied=None)
    )


@pytest.mark.parametrize(
    ("percentile", "reason"),
    [(None, None), (12.5, "background_sample_size_unset")],
    ids=["both-null", "both-set"],
)
def test_exactly_one_of_background_percentile_and_its_reason_is_null(
    percentile: object, reason: object
) -> None:
    assert_rejected(
        DistanceEntry,
        distance(background_percentile=percentile, background_percentile_reason=reason),
    )


@pytest.mark.parametrize("percentile", [-0.5, 100.5])
def test_background_percentile_is_between_0_and_100(percentile: float) -> None:
    assert_rejected(DistanceEntry, distance(background_percentile=percentile))


@pytest.mark.parametrize("tied", [True, False])
def test_tied_is_null_when_no_spread_was_supplied(tied: bool) -> None:
    assert_rejected(
        DistanceEntry,
        distance(reference_spread=None, reference_spread_reason="spread_unavailable", tied=tied),
    )


def test_a_tie_without_a_spread_is_null_with_a_reason() -> None:
    parsed = assert_accepted(
        DistanceEntry,
        distance(
            reference_spread=None,
            reference_spread_reason="reference_set_too_small",
            tied=None,
            tie_reason="spread_unavailable",
        ),
    )
    assert parsed.tied is None


def test_a_spread_may_leave_the_tie_open() -> None:
    assert_accepted(DistanceEntry, distance(tied=None, tie_reason="spread_unavailable"))
    assert_accepted(DistanceEntry, distance(tied=True, tie_group="tie-1"))


@pytest.mark.parametrize("n", [0, -1])
def test_a_reference_spread_is_over_at_least_one_distance(n: int) -> None:
    assert_rejected(ReferenceSpread, reference_spread(n=n))


@pytest.mark.parametrize("label", ["Observed", "Derived", "Assumed", "Unavailable"])
def test_a_model_embedding_value_must_be_predicted(label: str) -> None:
    embedding = distance(unit="cosine distance", value=0.25, evidence_label=label)
    assert_rejected(ModelEmbeddingDistanceEntry, embedding)
    assert_rejected(DistancesByFamily, distances(model_embedding=embedding))
    assert_rejected(
        ComparisonRecord, comparison(distances_by_family=distances(model_embedding=embedding))
    )


def test_a_predicted_model_embedding_value_is_accepted() -> None:
    embedding = distance(unit="cosine distance", value=0.25, evidence_label="Predicted")
    assert_accepted(ModelEmbeddingDistanceEntry, embedding)
    parsed = assert_accepted(DistancesByFamily, distances(model_embedding=embedding))
    assert parsed.model_embedding.evidence_label == "Predicted"


def test_a_missing_model_embedding_distance_need_not_be_predicted() -> None:
    assert_accepted(ModelEmbeddingDistanceEntry, missing_distance(evidence_label="Unavailable"))


def test_other_families_take_any_label() -> None:
    assert_accepted(DistanceEntry, distance(evidence_label="Predicted"))


def test_a_null_distance_survives_a_json_round_trip_as_null() -> None:
    parsed = DistanceEntry.model_validate(missing_distance())
    dumped = json.loads(parsed.model_dump_json())
    assert dumped["value"] is None
    assert dumped == missing_distance()
    assert DistanceEntry.model_validate_json(parsed.model_dump_json()) == parsed


def test_a_real_zero_distance_survives_a_json_round_trip_as_zero() -> None:
    parsed = DistanceEntry.model_validate(distance(value=0.0))
    assert parsed.value == 0.0
    assert parsed.status == "available"
    dumped = json.loads(parsed.model_dump_json())
    assert dumped["value"] == 0.0
    assert isinstance(dumped["value"], float)
    assert dumped["status"] == "available"
    record = ComparisonRecord.model_validate_json(json.dumps(comparison()))
    text = json.loads(record.model_dump_json())
    assert text["distances_by_family"]["terrain_texture"]["value"] == 0.0
    assert text["distances_by_family"]["radar_cpr"]["value"] is None


# --- (4) distances_by_family keys ---------------------------------------------------------------


@pytest.mark.parametrize("family", FAMILIES)
def test_distances_by_family_with_three_keys_fails(family: str) -> None:
    three = {key: value for key, value in distances().items() if key != family}
    assert_rejected(DistancesByFamily, three)
    assert_rejected(ComparisonRecord, comparison(distances_by_family=three))


@pytest.mark.parametrize("fifth", ["radar_sc", "score", "combined"])
def test_distances_by_family_with_five_keys_fails(fifth: str) -> None:
    five = {**distances(), fifth: distance()}
    assert_rejected(DistancesByFamily, five)
    assert_rejected(ComparisonRecord, comparison(distances_by_family=five))


def test_distances_by_family_reads_as_the_contract_mapping_of_families() -> None:
    # CONTRACTS a.4 names distances_by_family a dict[Family, DistanceEntry].
    parsed = DistancesByFamily.model_validate(distances())
    entries = [getattr(parsed, family) for family in FAMILIES]
    assert list(parsed) == FAMILIES
    assert len(parsed) == 4
    assert list(parsed.keys()) == FAMILIES
    assert list(parsed.values()) == entries
    assert list(parsed.items()) == list(zip(FAMILIES, entries, strict=True))
    assert dict(parsed) == dict(zip(FAMILIES, entries, strict=True))
    assert parsed["radar_cpr"] is parsed.radar_cpr
    assert parsed.get("radar_cpr") is parsed.radar_cpr
    assert parsed["terrain_texture"].value == 0.0


@pytest.mark.parametrize("family", FAMILIES)
def test_every_family_is_in_distances_by_family(family: str) -> None:
    assert family in DistancesByFamily.model_validate(distances())


@pytest.mark.parametrize(
    "key", ["score", "radar_sc", "model_fields", "", ("radar_cpr", None), ["radar_cpr"]]
)
def test_a_key_that_is_not_a_family_is_not_in_distances_by_family(key: Any) -> None:
    parsed = DistancesByFamily.model_validate(distances())
    assert key not in parsed
    assert parsed.get(key) is None
    with pytest.raises(KeyError):
        parsed[key]


def test_a_comparison_takes_the_four_families_as_distance_entries() -> None:
    # A producer may hold the dict[Family, DistanceEntry] that CONTRACTS a.4 names.
    entries = {family: DistanceEntry.model_validate(entry) for family, entry in distances().items()}
    record = ComparisonRecord.model_validate({**comparison(), "distances_by_family": entries})
    assert type(record.distances_by_family.model_embedding) is ModelEmbeddingDistanceEntry
    assert json.loads(record.model_dump_json()) == comparison()


@pytest.mark.parametrize("label", ["Observed", "Derived", "Assumed", "Unavailable"])
def test_a_model_embedding_distance_entry_object_must_still_be_predicted(label: str) -> None:
    embedding = DistanceEntry.model_validate(
        distance(unit="cosine distance", value=0.25, evidence_label=label)
    )
    with pytest.raises(ValidationError):
        DistancesByFamily.model_validate({**distances(), "model_embedding": embedding})
    predicted = embedding.model_copy(update={"evidence_label": "Predicted"})
    parsed = DistancesByFamily.model_validate({**distances(), "model_embedding": predicted})
    assert type(parsed.model_embedding) is ModelEmbeddingDistanceEntry
    assert parsed.model_embedding.evidence_label == "Predicted"


# --- (9) UncertaintyComponents ------------------------------------------------------------------


@pytest.mark.parametrize("key", COMPONENTS)
def test_uncertainty_components_missing_a_key_fails(key: str) -> None:
    value = {name: item for name, item in uncertainty().items() if name != key}
    assert_rejected(UncertaintyComponents, value)
    assert_rejected(ComparisonRecord, comparison(uncertainty_components=value))


@pytest.mark.parametrize("fifth", ["total", "combined", "radar_calibration"])
def test_uncertainty_components_with_a_fifth_key_fails(fifth: str) -> None:
    assert_rejected(UncertaintyComponents, {**uncertainty(), fifth: component()})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "partial"),
        ("by_family", {"terrain": family_spread()}),
        ("by_family", {"terrain_relief": {**family_spread(), "weight": 0.5}}),
        ("rank_frequency", {"radar_cpr": rank_frequency(frequency=1.5)}),
        ("rank_frequency", {"radar_cpr": rank_frequency(n=0)}),
        ("rank_frequency", {"overall": rank_frequency()}),
        ("scenarios", [{**scenario(), "probability": 0.5}]),
        ("scenarios", [{**scenario(), "weight": 0.5}]),
        ("scenarios", [scenario(values_by_family={"all": 0.5})]),
        ("scenarios", [scenario(rank_by_family={"radar_cpr": -1})]),
        ("n_used", -1),
    ],
)
def test_uncertainty_component_rejects_invalid_or_combining_values(
    field: str, value: object
) -> None:
    assert_rejected(UncertaintyComponent, component(**{field: value}))


def test_by_family_and_rank_frequency_take_any_subset_of_families() -> None:
    assert_accepted(UncertaintyComponent, component(by_family={}, rank_frequency={}))
    every = {family: family_spread() for family in FAMILIES}
    assert_accepted(UncertaintyComponent, component(by_family=every))


# --- ComparisonRecord and Comparisons -----------------------------------------------------------


@pytest.mark.parametrize("mode", ["A", "B", "C", None])
def test_shipping_mode_is_a_b_c_or_null(mode: object) -> None:
    assert_accepted(ComparisonRecord, comparison(shipping_mode=mode))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("shipping_mode", "D"),
        ("target_window_id", "site01"),
        ("earth_window_id", ""),
        ("run_id", HEX64),
        ("excluded_observables_and_reasons", [excluded_observable(reasons=[])]),
        ("excluded_observables_and_reasons", [excluded_observable(reasons=["Not Code"])]),
        ("claim_limits", [""]),
        ("source_ids", [""]),
        ("sort_rule", ""),
    ],
)
def test_comparison_record_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(ComparisonRecord, comparison(**{field: value}))


def test_comparison_record_has_no_shortlist_field() -> None:
    assert not {name for name in ComparisonRecord.model_fields if "shortlist" in name}
    assert_rejected(ComparisonRecord, {**comparison(), "shortlisted": True})


def test_comparisons_is_a_list_of_comparison_records() -> None:
    adapter = TypeAdapter(ComparisonsFile)
    assert adapter.validate_python([]) == []
    assert len(adapter.validate_json(json.dumps([comparison()]))) == 1
    with pytest.raises(ValidationError):
        adapter.validate_python(comparison())


def test_a_comparison_round_trips_through_json_unchanged() -> None:
    value = comparison()
    parsed = ComparisonRecord.model_validate_json(json.dumps(value))
    assert json.loads(parsed.model_dump_json()) == value


def test_a_window_entry_round_trips_through_json_unchanged() -> None:
    value = window_entry()
    parsed = WindowEntry.model_validate_json(json.dumps(value))
    assert json.loads(parsed.model_dump_json()) == value


# --- Contracts ----------------------------------------------------------------------------------


def test_new_records_are_appended_to_contracts_with_their_schema_files() -> None:
    assert [(c.name, c.file_name) for c in CONTRACTS][-5:] == [
        ("WindowRecord", "window-record.schema.json"),
        ("FingerprintRecord", "fingerprint-record.schema.json"),
        ("Windows", "windows.schema.json"),
        ("ComparisonRecord", "comparison-record.schema.json"),
        ("Comparisons", "comparisons.schema.json"),
    ]


def test_examples_do_not_share_state() -> None:
    first = comparison()
    second = copy.deepcopy(first)
    first["distances_by_family"]["radar_cpr"]["value"] = 1.5
    assert comparison() == second
