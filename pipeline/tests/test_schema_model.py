"""Contract tests for the model records I (CONTRACTS a.4; PRD 8.4, 8.12, 9, 11.6, 11.7, 11.9).

ModelRecord (model/model_card.json), MetricsFile (report/metrics.json, contract name Metrics),
EmbeddingRecord and EmbeddingsIndex (model/embeddings.index.json) carry exactly the CONTRACTS
a.4 field names, listed here. Values in these tests are schema test inputs, not observations,
model results or gate passes.
"""

from __future__ import annotations

import copy
import json
import re
import struct
import typing
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from terraluna_pipeline.schema import (
    CONTRACTS,
    SCHEMA_VERSION,
    Ablation,
    EmbeddingRecord,
    EmbeddingRow,
    EmbeddingsIndex,
    G5Component,
    G5ConditionRecord,
    G5Summary,
    MetricBaseline,
    MetricRecord,
    MetricsFile,
    ModelRecord,
    ObjectivesAndWeights,
    RunRecord,
    TrainingData,
    VicregWeights,
)

HEX64 = "0123456789abcdef" * 4
OTHER_HEX64 = "fedcba9876543210" * 4
RUN_ID = "sha256:" + HEX64
WINDOW = "moon-aeqd-0003-0007"
EIGHT_TESTS = [
    "physics_probe",
    "morphology_probe",
    "body_invariance",
    "physics_agreement",
    "target_retrieval",
    "seed_stability",
    "held_out_body",
    "out_of_distribution",
]


def literal_values(annotation: object) -> list[object]:
    if typing.get_origin(annotation) is typing.Literal:
        return list(typing.get_args(annotation))
    values: list[object] = []
    for arg in typing.get_args(annotation):
        values += literal_values(arg)
    return values


# --- Valid examples (schema test inputs) --------------------------------------------------------


def vicreg(**overrides: Any) -> dict[str, Any]:
    return {"invariance": 25.0, "variance": 25.0, "covariance": 1.0, **overrides}


def objectives(**overrides: Any) -> dict[str, Any]:
    return {
        "vicreg": vicreg(),
        "alpha_phys": 0.5,
        "lambda_max": 0.1,
        "schedule": "linear warm-up of lambda over the first 10 epochs",
        **overrides,
    }


def training_data(**overrides: Any) -> dict[str, Any]:
    return {
        "bodies": ["moon", "mars", "earth"],
        "product_ids": ["lola-ldem-site01", "ctx-dtm-jezero", "cop-dem-glo30"],
        "window_counts": {
            "moon": {"train": 1200, "val": 150, "test": 150},
            "mars": {"train": 800, "val": None, "test": 100},
            "earth": {"train": 5000, "val": 600, "test": 600},
        },
        "split_groups": {"moon": "spatial-blocks-20km", "mars": "dtm-id", "earth": "mgrs-tile"},
        **overrides,
    }


def baseline(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "dino_vits8",
        "value": 0.61,
        "ci_low": 0.55,
        "ci_high": 0.66,
        "paired_diff_ci_low": 0.12,
        "paired_diff_ci_high": 0.31,
        "unavailable_reason": None,
        **overrides,
    }


def metric(**overrides: Any) -> dict[str, Any]:
    return {
        "test_id": "physics_probe",
        "measure": "r2",
        "target": "log_nu_relief_lag_270",
        "body": "pooled",
        "value": 0.83,
        "ci_low": 0.8,
        "ci_high": 0.86,
        "n_blocks": 48,
        "n_bootstrap": 1000,
        "unavailable_reason": None,
        "evidence_label": "Predicted",
        "baselines": [baseline()],
        **overrides,
    }


def unavailable_metric(**overrides: Any) -> dict[str, Any]:
    unavailable: dict[str, Any] = {
        "test_id": "held_out_body",
        "body": "mars",
        "value": None,
        "ci_low": None,
        "ci_high": None,
        "n_blocks": None,
        "n_bootstrap": None,
        "unavailable_reason": "the Moon-and-Earth-only run was not trained",
        "baselines": [],
    }
    return metric(**{**unavailable, **overrides})


def model_record(**overrides: Any) -> dict[str, Any]:
    return {
        "model_id": "terraluna-enc-v1",
        "version": "1.0.0",
        "architecture": "ResNet-18, 3 input channels, 256-d projection",
        "parameter_count": 11_310_000,
        "training_data": training_data(),
        "objectives_and_weights": objectives(),
        "seeds": [0, 1, 2],
        "metrics_with_ci": [metric(), unavailable_metric()],
        "g5_status": "NOT_RUN",
        "intended_use": "comparing terrain windows across bodies as a Predicted family",
        "limitations": "no claim about radar, trafficability, regolith mechanics or ice",
        "licence": "Apache-2.0",
        "onnx_sha256": HEX64,
        **overrides,
    }


def component(**overrides: Any) -> dict[str, Any]:
    return {
        "component": "relief_lag_270",
        "observed": 0.81,
        "ci_low": 0.81,
        "ci_high": 0.88,
        "passed": True,
        "reason": None,
        **overrides,
    }


def condition(**overrides: Any) -> dict[str, Any]:
    return {
        "condition": 1,
        "test_id": "physics_probe",
        "statistic": "lower_95",
        "threshold": 0.8,
        "observed": 0.81,
        "passed": True,
        "reason": None,
        "components": [component()],
        **overrides,
    }


def g5_summary(**overrides: Any) -> dict[str, Any]:
    return {
        "status": "NOT_RUN",
        "conditions": [condition()],
        "failed_tests": [],
        **overrides,
    }


def ablation(**overrides: Any) -> dict[str, Any]:
    return {
        "run_name": "lambda_max_0",
        "status": "available",
        "reason": None,
        "metrics": [metric(test_id="body_invariance", measure="balanced_accuracy")],
        **overrides,
    }


def run_record(**overrides: Any) -> dict[str, Any]:
    return {
        "config_hash": HEX64,
        "data_manifest_sha256": OTHER_HEX64,
        "split_map_sha256": None,
        "checkpoint_sha256s": [HEX64, OTHER_HEX64],
        "git_sha": "3f9c2a1",
        "uv_lock_sha256": OTHER_HEX64,
        "hardware": "CPU server, 32 cores",
        "gpu_hours_total": None,
        "gpu_hours_partial": False,
        **overrides,
    }


def metrics_file(**overrides: Any) -> dict[str, Any]:
    return {
        "mock": False,
        "run_id": RUN_ID,
        "model_id": "terraluna-enc-v1",
        "evaluated_split": "test",
        "g5": g5_summary(),
        "tests": [metric(), unavailable_metric()],
        "ablations": [ablation()],
        "run_record": run_record(),
        **overrides,
    }


# Every value is a float16 (an integer multiple of a power of two within its range).
F16_VALUES = [0.5, -0.25, 0.125, 1.5, -2.75, 0.0009765625, 0.0, 65504.0]


def embedding_values(n: int = 256) -> list[float]:
    return [F16_VALUES[i % len(F16_VALUES)] for i in range(n)]


def embedding(**overrides: Any) -> dict[str, Any]:
    return {
        "window_id": WINDOW,
        "body": "moon",
        "model_id": "terraluna-enc-v1",
        "embedding_f16": embedding_values(),
        "input_hash": HEX64,
        "valid_fraction": 0.97,
        "ood_score": 3.2,
        **overrides,
    }


def embedding_row(**overrides: Any) -> dict[str, Any]:
    return {
        "row": 0,
        "window_id": WINDOW,
        "body": "moon",
        "split": "test",
        "input_hash": HEX64,
        "valid_fraction": 0.97,
        "ood_score": 3.2,
        "ood_flag": False,
        **overrides,
    }


def embeddings_index(**overrides: Any) -> dict[str, Any]:
    return {
        "model_id": "terraluna-enc-v1",
        "onnx_sha256": HEX64,
        "mock": False,
        "dim": 256,
        "dtype": "float16",
        "byte_order": "little",
        "n_rows": 2,
        "rows": [
            embedding_row(),
            embedding_row(
                row=1, window_id="earth-27n-0012-0040", body="earth", ood_score=None, ood_flag=None
            ),
        ],
        **overrides,
    }


EXAMPLES: list[tuple[type[BaseModel], Callable[[], dict[str, Any]]]] = [
    (VicregWeights, vicreg),
    (ObjectivesAndWeights, objectives),
    (TrainingData, training_data),
    (MetricBaseline, baseline),
    (MetricRecord, metric),
    (ModelRecord, model_record),
    (G5Component, component),
    (G5ConditionRecord, condition),
    (G5Summary, g5_summary),
    (Ablation, ablation),
    (RunRecord, run_record),
    (MetricsFile, metrics_file),
    (EmbeddingRecord, embedding),
    (EmbeddingRow, embedding_row),
    (EmbeddingsIndex, embeddings_index),
]
EXAMPLE_IDS = [model.__name__ for model, _ in EXAMPLES]

CONTRACT_FIELDS: dict[type[BaseModel], list[str]] = {
    VicregWeights: ["invariance", "variance", "covariance"],
    ObjectivesAndWeights: ["vicreg", "alpha_phys", "lambda_max", "schedule"],
    TrainingData: ["bodies", "product_ids", "window_counts", "split_groups"],
    MetricBaseline: [
        "name",
        "value",
        "ci_low",
        "ci_high",
        "paired_diff_ci_low",
        "paired_diff_ci_high",
        "unavailable_reason",
    ],
    MetricRecord: [
        "test_id",
        "measure",
        "target",
        "body",
        "value",
        "ci_low",
        "ci_high",
        "n_blocks",
        "n_bootstrap",
        "unavailable_reason",
        "evidence_label",
        "baselines",
    ],
    # Exactly the PRD 8.12 ModelRecord fields, in that order, and no mock field.
    ModelRecord: [
        "model_id",
        "version",
        "architecture",
        "parameter_count",
        "training_data",
        "objectives_and_weights",
        "seeds",
        "metrics_with_ci",
        "g5_status",
        "intended_use",
        "limitations",
        "licence",
        "onnx_sha256",
    ],
    G5Component: ["component", "observed", "ci_low", "ci_high", "passed", "reason"],
    G5ConditionRecord: [
        "condition",
        "test_id",
        "statistic",
        "threshold",
        "observed",
        "passed",
        "reason",
        "components",
    ],
    G5Summary: ["status", "conditions", "failed_tests"],
    Ablation: ["run_name", "status", "reason", "metrics"],
    RunRecord: [
        "config_hash",
        "data_manifest_sha256",
        "split_map_sha256",
        "checkpoint_sha256s",
        "git_sha",
        "uv_lock_sha256",
        "hardware",
        "gpu_hours_total",
        "gpu_hours_partial",
    ],
    MetricsFile: [
        "mock",
        "run_id",
        "model_id",
        "evaluated_split",
        "g5",
        "tests",
        "ablations",
        "run_record",
    ],
    # Exactly the PRD 8.12 EmbeddingRecord fields, in that order.
    EmbeddingRecord: [
        "window_id",
        "body",
        "model_id",
        "embedding_f16",
        "input_hash",
        "valid_fraction",
        "ood_score",
    ],
    EmbeddingRow: [
        "row",
        "window_id",
        "body",
        "split",
        "input_hash",
        "valid_fraction",
        "ood_score",
        "ood_flag",
    ],
    EmbeddingsIndex: [
        "model_id",
        "onnx_sha256",
        "mock",
        "dim",
        "dtype",
        "byte_order",
        "n_rows",
        "rows",
    ],
}

TOP_LEVEL = (ModelRecord, MetricsFile, EmbeddingRecord, EmbeddingsIndex)


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


# --- Field names, required fields, unknown fields, frozen ----------------------------------------


@pytest.mark.parametrize("model", list(CONTRACT_FIELDS), ids=lambda m: m.__name__)
def test_record_fields_are_exactly_contracts_a4(model: type[BaseModel]) -> None:
    assert list(model.model_fields) == CONTRACT_FIELDS[model]


def test_every_record_is_reachable_and_covered() -> None:
    seen: set[type[BaseModel]] = set()
    reachable = {model for top in TOP_LEVEL for model in own_models(top, seen)}
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
@pytest.mark.parametrize("extra", ["mock_reason", "score", "notes"])
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


def test_examples_do_not_share_state() -> None:
    first = metrics_file()
    second = copy.deepcopy(first)
    first["tests"][0]["baselines"][0]["value"] = 0.1
    first["g5"]["conditions"][0]["components"][0]["observed"] = 0.1
    assert metrics_file() == second


def test_schema_version_stays_1_0_0() -> None:
    assert SCHEMA_VERSION == "1.0.0"


def test_model_contracts_follow_the_card_record() -> None:
    files = [(c.name, c.file_name) for c in CONTRACTS]
    start = files.index(("CardRecord", "card-record.schema.json")) + 1
    assert files[start : start + 4] == [
        ("ModelRecord", "model-record.schema.json"),
        ("Metrics", "metrics.schema.json"),
        ("EmbeddingRecord", "embedding-record.schema.json"),
        ("EmbeddingsIndex", "embeddings-index.schema.json"),
    ]


def test_contract_adapters_validate_the_records() -> None:
    adapters = {c.name: c.adapter for c in CONTRACTS}
    examples = {
        "ModelRecord": model_record,
        "Metrics": metrics_file,
        "EmbeddingRecord": embedding,
        "EmbeddingsIndex": embeddings_index,
    }
    for name, example in examples.items():
        adapters[name].validate_python(example())


# --- (1) ModelRecord: every PRD 8.12 field, unknown field, missing field -------------------------


def test_1_a_model_record_with_every_prd_8_12_field_parses() -> None:
    record = assert_accepted(ModelRecord, model_record())
    assert record.onnx_sha256 == HEX64
    assert record.metrics_with_ci[1].value is None


def test_1_a_model_record_with_an_unknown_field_fails() -> None:
    assert_rejected(ModelRecord, {**model_record(), "ece": 0.1})


def test_1_a_model_record_with_mock_fails() -> None:
    # mock lives in the manifest and the export record, not in model_card.json.
    assert_rejected(ModelRecord, {**model_record(), "mock": True})


@pytest.mark.parametrize("field", CONTRACT_FIELDS[ModelRecord])
def test_1_a_model_record_with_one_missing_field_fails(field: str) -> None:
    value = model_record()
    del value[field]
    assert_rejected(ModelRecord, value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parameter_count", 0),
        ("parameter_count", 1.5),
        ("parameter_count", "11310000"),
        ("seeds", [0.5]),
        ("seeds", [-1]),
        ("seeds", [True]),
        ("model_id", ""),
        ("licence", ""),
        ("intended_use", ""),
        ("limitations", ""),
        ("version", ""),
        ("architecture", ""),
    ],
)
def test_model_record_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(ModelRecord, model_record(**{field: value}))


@pytest.mark.parametrize(
    ("override", "valid"),
    [
        ({"bodies": ["moon", "venus"]}, False),
        ({"window_counts": {"venus": {"train": 1}}}, False),
        ({"window_counts": {"moon": {"holdout": 1}}}, False),
        ({"window_counts": {"moon": {"train": -1}}}, False),
        ({"window_counts": {"moon": {"train": None, "val": None, "test": None}}}, True),
        ({"window_counts": {}}, True),
        ({"split_groups": {"moon": ""}}, False),
        ({"split_groups": {"mars": "dtm-id"}}, True),
        ({"product_ids": [""]}, False),
    ],
)
def test_training_data_keys_are_bodies_and_splits(override: dict[str, Any], valid: bool) -> None:
    value = training_data(**override)
    if valid:
        assert_accepted(TrainingData, value)
    else:
        assert_rejected(TrainingData, value)


def test_vicreg_may_be_null() -> None:
    assert assert_accepted(ObjectivesAndWeights, objectives(vicreg=None)).vicreg is None


@pytest.mark.parametrize("field", ["alpha_phys", "lambda_max"])
@pytest.mark.parametrize("value", [-0.1, None, "0.5"])
def test_objective_weights_are_non_negative_numbers(field: str, value: object) -> None:
    assert_rejected(ObjectivesAndWeights, objectives(**{field: value}))


@pytest.mark.parametrize("field", ["invariance", "variance", "covariance"])
def test_vicreg_weights_are_non_negative(field: str) -> None:
    assert_rejected(VicregWeights, vicreg(**{field: -1.0}))


# --- (2) onnx_sha256 ----------------------------------------------------------------------------


@pytest.mark.parametrize("model", [ModelRecord, EmbeddingsIndex], ids=lambda m: m.__name__)
def test_2_onnx_sha256_null_parses_and_stays_null_after_a_json_round_trip(
    model: type[BaseModel],
) -> None:
    example = model_record if model is ModelRecord else embeddings_index
    record = assert_accepted(model, example(onnx_sha256=None))
    dumped = json.loads(record.model_dump_json())
    assert "onnx_sha256" in dumped
    assert dumped["onnx_sha256"] is None
    assert getattr(model.model_validate_json(record.model_dump_json()), "onnx_sha256") is None


@pytest.mark.parametrize("model", [ModelRecord, EmbeddingsIndex], ids=lambda m: m.__name__)
@pytest.mark.parametrize(
    "digest", [HEX64[:63], HEX64.upper(), HEX64 + "0", "sha256:" + HEX64, HEX64 + "\n", ""]
)
def test_2_onnx_sha256_63_characters_or_uppercase_fails(
    model: type[BaseModel], digest: str
) -> None:
    example = model_record if model is ModelRecord else embeddings_index
    assert_rejected(model, example(onnx_sha256=digest))


# --- (3) g5_status ------------------------------------------------------------------------------


def test_3_g5_status_experimental_fails() -> None:
    # EXPERIMENTAL belongs to ModelSummary.status; g5_status is a gate status (PRD 8.12, 9).
    assert_rejected(ModelRecord, model_record(g5_status="EXPERIMENTAL"))
    assert_rejected(MetricsFile, metrics_file(g5=g5_summary(status="EXPERIMENTAL")))


@pytest.mark.parametrize("status", ["NOT_RUN", "PASS", "FAIL", "PARTIAL", "NOT_APPLICABLE"])
def test_g5_status_takes_each_gate_status(status: str) -> None:
    assert_accepted(ModelRecord, model_record(g5_status=status))
    assert_accepted(G5Summary, g5_summary(status=status))


@pytest.mark.parametrize("status", [None, "PASSED", "pass", ""])
def test_g5_status_rejects_anything_else(status: object) -> None:
    assert_rejected(ModelRecord, model_record(g5_status=status))


# --- (4), (5) intervals -------------------------------------------------------------------------


def test_4_a_percentile_interval_that_excludes_the_point_estimate_parses() -> None:
    above = assert_accepted(MetricRecord, metric(value=0.9, ci_low=0.8, ci_high=0.86))
    below = assert_accepted(MetricRecord, metric(value=0.7, ci_low=0.8, ci_high=0.86))
    assert (above.value, below.value) == (0.9, 0.7)


def test_an_interval_of_one_point_parses() -> None:
    assert_accepted(MetricRecord, metric(value=0.8, ci_low=0.8, ci_high=0.8))


def test_5_ci_low_above_ci_high_fails() -> None:
    assert_rejected(MetricRecord, metric(ci_low=0.86, ci_high=0.8))


@pytest.mark.parametrize(
    ("low", "high"), [("ci_low", "ci_high"), ("paired_diff_ci_low", "paired_diff_ci_high")]
)
def test_5_a_baseline_interval_with_low_above_high_fails(low: str, high: str) -> None:
    assert_rejected(MetricBaseline, baseline(**{low: 0.4, high: 0.3}))


def test_5_a_component_interval_with_low_above_high_fails() -> None:
    assert_rejected(G5Component, component(ci_low=0.9, ci_high=0.8))


@pytest.mark.parametrize(
    ("low", "high"), [(None, 0.86), (0.8, None), (None, None)], ids=["low", "high", "both"]
)
def test_an_interval_with_a_null_end_parses(low: float | None, high: float | None) -> None:
    assert_accepted(MetricRecord, metric(ci_low=low, ci_high=high))
    assert_accepted(MetricBaseline, baseline(ci_low=low, ci_high=high))
    assert_accepted(MetricBaseline, baseline(paired_diff_ci_low=low, paired_diff_ci_high=high))
    assert_accepted(G5Component, component(ci_low=low, ci_high=high))


# --- (6), (7) unavailable metrics ---------------------------------------------------------------


def test_6_an_unavailable_test_with_value_null_and_a_reason_parses() -> None:
    record = assert_accepted(MetricRecord, unavailable_metric())
    assert (record.value, record.n_bootstrap, record.n_blocks) == (None, None, None)
    assert record.unavailable_reason


@pytest.mark.parametrize("reason", [None, ""])
def test_6_value_null_without_a_reason_fails(reason: str | None) -> None:
    assert_rejected(MetricRecord, unavailable_metric(unavailable_reason=reason))


def test_6_value_set_with_a_reason_fails() -> None:
    assert_rejected(MetricRecord, metric(unavailable_reason="it was run"))


def test_7_value_null_with_n_bootstrap_5_fails() -> None:
    assert_rejected(MetricRecord, unavailable_metric(n_bootstrap=5))


def test_7_value_null_with_n_blocks_set_fails() -> None:
    assert_rejected(MetricRecord, unavailable_metric(n_blocks=12))


@pytest.mark.parametrize("n_bootstrap", [0, None])
def test_7_value_set_with_n_bootstrap_0_or_null_fails(n_bootstrap: int | None) -> None:
    assert_rejected(MetricRecord, metric(n_bootstrap=n_bootstrap))


@pytest.mark.parametrize("n_blocks", [0, None])
def test_7_value_set_with_n_blocks_0_or_null_fails(n_blocks: int | None) -> None:
    assert_rejected(MetricRecord, metric(n_blocks=n_blocks))


def test_a_value_of_zero_is_a_real_zero() -> None:
    # Missing is never zero: 0.0 is an observed R² of zero, so it needs its blocks.
    assert assert_accepted(MetricRecord, metric(value=0.0, ci_low=-0.1, ci_high=0.1)).value == 0.0


def test_7_one_bootstrap_resample_parses() -> None:
    assert_accepted(MetricRecord, metric(n_bootstrap=1, n_blocks=1))


@pytest.mark.parametrize(
    ("value", "reason", "valid"),
    [(0.5, None, True), (None, "baseline not evaluated", True), (None, None, False)],
)
def test_a_missing_baseline_value_needs_a_reason(
    value: float | None, reason: str | None, valid: bool
) -> None:
    candidate = baseline(
        value=value,
        ci_low=None,
        ci_high=None,
        paired_diff_ci_low=None,
        paired_diff_ci_high=None,
        unavailable_reason=reason,
    )
    if valid:
        assert_accepted(MetricBaseline, candidate)
    else:
        assert_rejected(MetricBaseline, candidate)


def test_a_baseline_with_a_value_may_explain_a_missing_paired_interval() -> None:
    assert_accepted(
        MetricBaseline,
        baseline(
            paired_diff_ci_low=None,
            paired_diff_ci_high=None,
            unavailable_reason="paired bootstrap not run",
        ),
    )


# --- (8) evidence label, test ids and bodies ----------------------------------------------------


@pytest.mark.parametrize("label", ["Observed", "Derived", "Assumed", "Unavailable", "predicted"])
def test_8_a_metric_record_with_a_label_other_than_predicted_fails(label: str) -> None:
    assert_rejected(MetricRecord, metric(evidence_label=label))


def test_metric_test_ids_are_the_eight_prd_11_6_tests() -> None:
    assert literal_values(MetricRecord.model_fields["test_id"].annotation) == EIGHT_TESTS
    for test_id in EIGHT_TESTS:
        assert_accepted(MetricRecord, metric(test_id=test_id))
    assert_rejected(MetricRecord, metric(test_id="ood"))


@pytest.mark.parametrize(
    ("body", "valid"),
    [("moon", True), ("pooled", True), (None, True), ("venus", False), ("Pooled", False)],
)
def test_metric_body_is_a_body_pooled_or_null(body: object, valid: bool) -> None:
    if valid:
        assert_accepted(MetricRecord, metric(body=body))
    else:
        assert_rejected(MetricRecord, metric(body=body))


# --- (9), (10), (11) G5 conditions --------------------------------------------------------------


def test_9_a_condition_1_record_with_one_failing_relief_lag_component_parses() -> None:
    record = assert_accepted(
        G5ConditionRecord,
        condition(
            observed=0.71,
            passed=False,
            components=[
                component(component="relief_lag_90", observed=0.84, passed=True),
                component(
                    component="relief_lag_270",
                    observed=0.71,
                    ci_low=0.71,
                    ci_high=0.8,
                    passed=False,
                ),
            ],
        ),
    )
    assert record.passed is False
    assert [c.passed for c in record.components] == [True, False]


def test_a_condition_stores_passed_without_recomputing_it_from_its_components() -> None:
    # Condition 2 holds on the Moon or Mars (PRD 11.7), so passed is not always the AND.
    record = assert_accepted(
        G5ConditionRecord,
        condition(
            condition=2,
            test_id="morphology_probe",
            threshold=0.1,
            observed=None,
            passed=True,
            components=[
                component(component="moon:morph_gain", observed=0.14, passed=True),
                component(component="mars:morph_gain", observed=0.04, passed=False),
            ],
        ),
    )
    assert record.passed is True


def test_10_a_condition_6_record_with_a_null_threshold_parses() -> None:
    record = assert_accepted(
        G5ConditionRecord,
        condition(
            condition=6,
            test_id="physics_probe",
            statistic="paired_interval",
            threshold=None,
            observed=None,
            components=[
                component(component="dino:morph_gain", observed=0.08, ci_low=0.02, ci_high=0.13),
                component(component="resnet18:relief_r2", observed=0.2, ci_low=0.1, ci_high=0.3),
            ],
        ),
    )
    assert record.threshold is None


@pytest.mark.parametrize("number", [1, 2, 3, 4, 5])
def test_10_a_condition_1_to_5_record_with_a_null_threshold_fails(number: int) -> None:
    assert_rejected(G5ConditionRecord, condition(condition=number, threshold=None))


@pytest.mark.parametrize("number", [0, 7, -1, 1.5, "1", True])
def test_condition_is_an_integer_from_1_to_6(number: object) -> None:
    assert_rejected(G5ConditionRecord, condition(condition=number))


@pytest.mark.parametrize("statistic", ["lower_95", "point", "paired_interval"])
def test_each_statistic_parses(statistic: str) -> None:
    assert_accepted(G5ConditionRecord, condition(statistic=statistic))


@pytest.mark.parametrize("statistic", ["upper_95", "mean", ""])
def test_other_statistics_fail(statistic: str) -> None:
    assert_rejected(G5ConditionRecord, condition(statistic=statistic))


@pytest.mark.parametrize("reason", [None, ""])
def test_11_passed_null_without_a_reason_fails(reason: str | None) -> None:
    assert_rejected(G5ConditionRecord, condition(passed=None, reason=reason))
    assert_rejected(G5Component, component(passed=None, reason=reason))


def test_11_passed_null_with_a_reason_parses() -> None:
    assert_accepted(
        G5ConditionRecord, condition(passed=None, reason="test blocks not yet read", observed=None)
    )
    assert_accepted(G5Component, component(passed=None, reason="lag unsupported"))


def test_a_failed_condition_may_give_a_reason() -> None:
    assert_accepted(G5ConditionRecord, condition(passed=False, reason="lower bound below 0.8"))


def test_passed_true_with_observed_null_and_no_components_fails() -> None:
    assert_rejected(G5ConditionRecord, condition(observed=None, components=[]))


@pytest.mark.parametrize(
    "override",
    [
        {"observed": None},
        {"components": []},
        {"observed": None, "components": [], "passed": False},
        {"observed": None, "components": [], "passed": None, "reason": "not evaluated"},
    ],
    ids=["no-observed", "no-components", "failed-without-evidence", "unknown-without-evidence"],
)
def test_a_pass_needs_an_observed_value_or_a_component(override: dict[str, Any]) -> None:
    assert_accepted(G5ConditionRecord, condition(**override))


# --- (12) embeddings ----------------------------------------------------------------------------


@pytest.mark.parametrize("n", [255, 257, 0, 128])
def test_12_an_embedding_not_of_256_values_fails(n: int) -> None:
    assert_rejected(EmbeddingRecord, embedding(embedding_f16=embedding_values(n)))


@pytest.mark.parametrize("value", [0.1, 1 / 3, 65520.0, 1e-8, 2049.0 + 0.5])
def test_12_a_value_not_representable_in_float16_fails(value: float) -> None:
    values = embedding_values()
    values[17] = value
    assert_rejected(EmbeddingRecord, embedding(embedding_f16=values))


@pytest.mark.parametrize(
    "value", [0.0999755859375, -65504.0, 6.103515625e-05, 5.960464477539063e-08]
)
def test_float16_values_parse(value: float) -> None:
    # 0.0999755859375 is float16(0.1); the others are the extremes of the float16 range.
    assert struct.unpack("<e", struct.pack("<e", value))[0] == value
    values = embedding_values()
    values[17] = value
    record = assert_accepted(EmbeddingRecord, embedding(embedding_f16=values))
    assert record.embedding_f16[17] == value


def test_an_integer_embedding_value_parses() -> None:
    values: list[Any] = embedding_values()
    values[3] = 2
    assert_accepted(EmbeddingRecord, embedding(embedding_f16=values))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "0.5", True, None])
def test_an_embedding_value_is_a_finite_number(value: object) -> None:
    values: list[Any] = embedding_values()
    values[0] = value
    with pytest.raises(ValidationError):
        EmbeddingRecord.model_validate(embedding(embedding_f16=values))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("valid_fraction", 1.5),
        ("valid_fraction", -0.1),
        ("ood_score", -1.0),
        ("input_hash", HEX64.upper()),
        ("window_id", "venus-0001"),
        ("body", "venus"),
        ("model_id", ""),
    ],
)
def test_embedding_record_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(EmbeddingRecord, embedding(**{field: value}))


def test_ood_score_may_be_null() -> None:
    assert assert_accepted(EmbeddingRecord, embedding(ood_score=None)).ood_score is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dim", 128),
        ("dim", 256.0),
        ("dim", "256"),
        ("dtype", "float32"),
        ("byte_order", "big"),
        ("n_rows", 3),
        ("n_rows", 1),
        ("n_rows", -2),
    ],
)
def test_embeddings_index_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(EmbeddingsIndex, embeddings_index(**{field: value}))


def test_n_rows_counts_the_rows() -> None:
    assert assert_accepted(EmbeddingsIndex, embeddings_index(n_rows=0, rows=[])).n_rows == 0


def test_each_row_is_its_position_in_the_binary() -> None:
    rows = [embedding_row(row=1), embedding_row(row=0)]
    assert_rejected(EmbeddingsIndex, embeddings_index(rows=rows))
    assert_rejected(EmbeddingsIndex, embeddings_index(rows=[embedding_row(), embedding_row()]))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("split", "train"),
        ("split", "val"),
        ("row", -1),
        ("row", 0.5),
        ("valid_fraction", 1.01),
        ("ood_score", -0.5),
        ("ood_flag", "false"),
    ],
)
def test_embedding_row_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(EmbeddingRow, embedding_row(**{field: value}))


@pytest.mark.parametrize(("ood_flag", "valid"), [(None, True), (True, False), (False, False)])
def test_an_ood_flag_needs_an_ood_score(ood_flag: bool | None, valid: bool) -> None:
    value = embedding_row(ood_score=None, ood_flag=ood_flag)
    if valid:
        assert_accepted(EmbeddingRow, value)
    else:
        assert_rejected(EmbeddingRow, value)


def test_an_ood_score_without_a_flag_parses() -> None:
    # The flag waits for the 99th-percentile threshold (PRD 11.6), which may not be set yet.
    assert_accepted(EmbeddingRow, embedding_row(ood_flag=None))


# --- (13) MetricsFile ---------------------------------------------------------------------------


def test_13_a_metrics_file_with_mock_true_and_run_id_null_parses() -> None:
    record = assert_accepted(MetricsFile, metrics_file(mock=True, run_id=None))
    assert (record.mock, record.run_id) == (True, None)


@pytest.mark.parametrize("run_id", [HEX64, "sha256:" + HEX64.upper(), ""])
def test_metrics_file_run_id_is_a_run_id(run_id: str) -> None:
    assert_rejected(MetricsFile, metrics_file(run_id=run_id))


@pytest.mark.parametrize(("split", "valid"), [("val", True), ("test", True), ("train", False)])
def test_evaluated_split_is_val_or_test(split: str, valid: bool) -> None:
    if valid:
        assert_accepted(MetricsFile, metrics_file(evaluated_split=split))
    else:
        assert_rejected(MetricsFile, metrics_file(evaluated_split=split))


def test_an_unavailable_ablation_has_a_reason_and_no_metrics() -> None:
    assert_accepted(
        Ablation, ablation(status="unavailable", reason="no GPU hours left", metrics=[])
    )
    assert_rejected(Ablation, ablation(status="unavailable", reason=None, metrics=[]))
    assert_rejected(Ablation, ablation(status="unavailable", reason="", metrics=[]))
    assert_rejected(Ablation, ablation(status="unavailable", reason="no GPU hours left"))


def test_an_available_ablation_has_no_reason() -> None:
    assert_rejected(Ablation, ablation(reason="it ran"))


@pytest.mark.parametrize("status", ["missing", "Available", ""])
def test_ablation_status_is_available_or_unavailable(status: str) -> None:
    assert_rejected(Ablation, ablation(status=status))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("config_hash", None),
        ("config_hash", HEX64[:63]),
        ("uv_lock_sha256", None),
        ("data_manifest_sha256", HEX64.upper()),
        ("checkpoint_sha256s", [HEX64[:63]]),
        ("gpu_hours_total", -1.0),
        ("gpu_hours_partial", None),
        ("hardware", ""),
        ("git_sha", ""),
    ],
)
def test_run_record_rejects_invalid_values(field: str, value: object) -> None:
    assert_rejected(RunRecord, run_record(**{field: value}))


def test_run_record_nullable_fields_parse_as_null() -> None:
    record = assert_accepted(
        RunRecord,
        run_record(
            data_manifest_sha256=None, split_map_sha256=None, git_sha=None, gpu_hours_total=None
        ),
    )
    assert record.gpu_hours_total is None


def test_g5_failed_tests_are_non_empty_strings() -> None:
    assert_accepted(G5Summary, g5_summary(status="FAIL", failed_tests=["physics_probe"]))
    assert_rejected(G5Summary, g5_summary(failed_tests=[""]))


# --- The JSON Schemas ---------------------------------------------------------------------------


def test_metrics_schema_names_metric_and_g5_condition_records() -> None:
    schema = TypeAdapter(MetricsFile).json_schema()
    assert {"MetricRecord", "G5ConditionRecord"} <= set(schema["$defs"])


def test_no_new_record_has_a_combined_score_or_probability_field() -> None:
    # D07: no universal analog score. ood_score is a Mahalanobis distance (PRD 11.6), not a
    # combined score, and objectives_and_weights are loss weights (PRD 8.12).
    forbidden = re.compile(r"combined|probability|analog_score")
    for model in CONTRACT_FIELDS:
        for name in model.model_fields:
            assert not forbidden.search(name), f"{model.__name__}.{name}"
