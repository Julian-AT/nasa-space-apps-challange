"""Report records II, the background file and the bundle path map (T0B-04; CONTRACTS a.3, a.4;
PRD 6.5, 8.4, 8.12, 11.8 to 11.10).

CurvesFile (report/curves.json), UmapHeader (the report/umap.bin header), ExamplesFile
(report/examples.json), Background (reference/background_{target_id}.json) and BUNDLE_PATHS,
the one map from PRD 8.4 path template to contract name, emitted as
packages/schema/json/bundle-paths.json. Values here are synthetic schema inputs, not results.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from terraluna_pipeline import provenance
from terraluna_pipeline.schema import (
    BUNDLE_PATHS,
    BUNDLE_PATHS_FILE,
    CONTRACTS,
    NO_JSON_SCHEMA,
    SVG_ALLOWLIST,
    Background,
    BackgroundFamily,
    CurvesFile,
    ExampleHillshade,
    ExamplesFile,
    GalleryEntry,
    ModelBackground,
    UmapHeader,
    bundle_contract_for,
    bundle_paths_json,
    main,
)

ROOT = Path(__file__).resolve().parents[2]
PRD = ROOT / "PRD.md"
SCHEMA_DIR = ROOT / "packages" / "schema" / "json"
HEX64 = "0123456789abcdef" * 4
OTHER_HEX64 = "fedcba9876543210" * 4
RUN_ID = "sha256:" + HEX64
FAMILIES = ("terrain_relief", "terrain_texture", "radar_cpr", "model_embedding")
EMBEDDING = [0.5, -0.25, 0.125, 1.5] * 64


def accepted(model: type[BaseModel], value: Any) -> bool:
    try:
        model.model_validate(value)
        model.model_validate_json(json.dumps(value))
    except ValidationError:
        return False
    return True


# --- Builders -----------------------------------------------------------------------------------


def histogram(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "unit": "dimensionless",
        "evidence_label": "Derived",
        "bin_edges": [0.25, 0.5, 1.125, 1.5],
        "counts": [2, 3, 5],
        "n": 10,
        "percentile_definition": "linear_within_bin_v1",
        "direction": "smaller_is_closer",
    }
    value.update(overrides)
    return value


SAMPLE_IDS = ["moon-aeqd-0001-0001", "moon-aeqd-0001-0002"]


def ids_sha256(ids: list[str]) -> str:
    return hashlib.sha256(provenance.canonical_json(ids)).hexdigest()


def model_histogram(**overrides: Any) -> dict[str, Any]:
    value = histogram(
        unit="embedding cosine distance",
        evidence_label="Predicted",
        model_id="terraluna-enc-v1",
        onnx_sha256=HEX64,
        g5_status="NOT_RUN",
        target_embedding={
            "window_id": "moon-aeqd-0003-0007",
            "body": "moon",
            "model_id": "terraluna-enc-v1",
            "embedding_f16": EMBEDDING,
            "input_hash": HEX64,
            "valid_fraction": 0.75,
            "ood_score": None,
        },
        sample_window_ids_sha256=ids_sha256(SAMPLE_IDS),
    )
    value.update(overrides)
    return value


def background(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "target_id": "moon-cr-site01-w000",
        "mock": False,
        "run_id": RUN_ID,
        "statement": "empirical background comparison, not a physical truth test",
        "seed": 7,
        "requested_n": 2,
        "actual_n": 2,
        "sample_window_ids": list(SAMPLE_IDS),
        "source_ids": ["pgda78_site01"],
        "families": {
            "terrain_relief": histogram(),
            "terrain_texture": histogram(),
            "radar_cpr": None,
            "model_embedding": model_histogram(),
        },
        "family_reasons": {
            "terrain_relief": None,
            "terrain_texture": None,
            "radar_cpr": "radar_unavailable_for_body",
            "model_embedding": None,
        },
    }
    value.update(overrides)
    return value


def interval(value: float | None = 0.6) -> dict[str, Any]:
    if value is None:
        return {"value": None, "ci_low": None, "ci_high": None}
    return {"value": value, "ci_low": value - 0.125, "ci_high": value + 0.125}


def curves(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "mock": False,
        "run_id": RUN_ID,
        "loss_terms": [
            {
                "term": "vicreg_invariance",
                "epochs": [
                    {"epoch": 0, "mean": 1.5, "min": 1.25, "max": 1.75, "n_seeds": 3},
                    {"epoch": 1, "mean": 1.125, "min": 0.75, "max": 1.25, "n_seeds": 3},
                ],
            }
        ],
        "gpu_hours": {
            "runs": [
                {"run_name": "seed-0", "hours": 1.5, "reason": None, "source": "scheduler log"},
                {"run_name": "seed-1", "hours": 2.25, "reason": None, "source": "scheduler log"},
            ],
            "total_hours": 3.75,
            "partial": False,
        },
        "tradeoff": [
            {
                "lambda_max": 0.5,
                "status": "available",
                "reason": None,
                "body_probe_balanced_accuracy": interval(0.625),
                "physics_r2": interval(0.75),
            },
            {
                "lambda_max": 1.5,
                "status": "unavailable",
                "reason": "run_not_completed",
                "body_probe_balanced_accuracy": interval(None),
                "physics_r2": interval(None),
            },
        ],
        "chance_level_body_probe": 0.5,
        "validation_probe_metrics": {
            "status": "unavailable",
            "reason": "encoder_not_evaluated",
            "metrics": [],
        },
    }
    value.update(overrides)
    return value


def entry(family: str = "model_embedding", rank: int = 1, **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "window_id": f"earth-utm27n-0001-{rank:04d}",
        "body": "earth",
        "rank": rank,
        "distance": 0.25 * rank + 0.125,
        "distance_family": family,
        "evidence_label": "Predicted" if family == "model_embedding" else "Derived",
    }
    value.update(overrides)
    return value


def top5(family: str) -> list[dict[str, Any]]:
    return [entry(family, rank) for rank in range(1, 6)]


def hillshade(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "window_id": "earth-utm27n-0001-0001",
        "size": 64,
        "encoding": "png",
        "data_b64": "iVBORw0KGgo=",
        "unavailable_reason": None,
    }
    value.update(overrides)
    return value


def examples(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "mock": False,
        "run_id": RUN_ID,
        "retrieval_gallery": [
            {
                "target_id": "moon-cr-site01-w000",
                "encoder_top5": top5("model_embedding"),
                "deterministic_top5": top5("terrain_relief"),
                "shortfall_reason": None,
            }
        ],
        "failure_cases": {
            "sort_rule": "largest physics residual first",
            "entries": [entry("model_embedding", 1)],
            "shortfall_reason": None,
        },
        "ood_examples": [entry("model_embedding", 1)],
        "hillshades": [hillshade()],
    }
    value.update(overrides)
    return value


def umap(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "mock": False,
        "run_id": RUN_ID,
        "n_points": 3,
        "columns": [
            {"name": "x", "dtype": "float32", "unit": None},
            {"name": "y", "dtype": "float32", "unit": None},
            {"name": "body", "dtype": "uint8", "unit": None},
        ],
        "body_codes": {"0": "moon", "1": "mars", "2": "earth"},
        "nan_means_unavailable": True,
        "seed": 0,
        "n_neighbors": 15,
        "min_dist": 0.125,
        "source_embedding_sha256": HEX64,
    }
    value.update(overrides)
    return value


def test_the_builders_are_valid() -> None:
    assert accepted(Background, background())
    assert accepted(CurvesFile, curves())
    assert accepted(ExamplesFile, examples())
    assert accepted(UmapHeader, umap())
    assert accepted(BackgroundFamily, histogram())
    assert accepted(ModelBackground, model_histogram())


# --- (1) Background families and reasons ----------------------------------------------------------


def test_1_families_and_family_reasons_cover_all_four_family_keys() -> None:
    assert list(Background.model_fields["families"].annotation.model_fields) == list(FAMILIES)  # type: ignore[union-attr]
    for key in FAMILIES:
        value = background()
        del value["family_reasons"][key]
        assert not accepted(Background, value), key
        value = background()
        del value["families"][key]
        assert not accepted(Background, value), key


@pytest.mark.parametrize("key", FAMILIES)
def test_1_a_family_with_both_or_neither_set_fails(key: str) -> None:
    value = background()
    family = value["families"][key]
    if family is None:
        both = copy.deepcopy(value)
        both["families"][key] = histogram()
        neither = copy.deepcopy(value)
        neither["family_reasons"][key] = None
    else:
        both = copy.deepcopy(value)
        both["family_reasons"][key] = "some_reason"
        neither = copy.deepcopy(value)
        neither["families"][key] = None
    assert not accepted(Background, both)
    assert not accepted(Background, neither)


# --- (2) and (3) ModelBackground ------------------------------------------------------------------


@pytest.mark.parametrize("label", ["Derived", "Observed", "Assumed", "Unavailable"])
def test_2_a_model_background_labelled_other_than_predicted_fails(label: str) -> None:
    assert not accepted(ModelBackground, model_histogram(evidence_label=label))
    value = background()
    value["families"]["model_embedding"]["evidence_label"] = label
    assert not accepted(Background, value)


def test_3_sample_window_ids_sha256_must_hash_the_parents_ids() -> None:
    assert accepted(Background, background())
    value = background()
    value["families"]["model_embedding"]["sample_window_ids_sha256"] = OTHER_HEX64
    assert not accepted(Background, value)
    reordered = background(sample_window_ids=list(reversed(SAMPLE_IDS)))
    assert not accepted(Background, reordered)


def test_canonical_json_rule_is_provenance_canonical_json() -> None:
    expected = hashlib.sha256(provenance.canonical_json(SAMPLE_IDS)).hexdigest()
    assert model_histogram()["sample_window_ids_sha256"] == expected


# --- BackgroundFamily -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"counts": [2, 3]},
        {"bin_edges": [0.25, 1.0, 0.5, 1.5]},
        {"bin_edges": [0.25, 0.5, 0.5, 1.5]},
        {"n": 9},
        {"percentile_definition": "nearest_rank"},
        {"direction": "larger_is_closer"},
        {"counts": [2, -3, 11]},
    ],
)
def test_malformed_histograms_fail(overrides: dict[str, Any]) -> None:
    assert not accepted(BackgroundFamily, histogram(**overrides))


def test_linear_within_bin_v1_is_documented_on_the_field() -> None:
    description = BackgroundFamily.model_fields["percentile_definition"].description
    assert description is not None
    assert "counts[k] * (d - lo_k) / (hi_k - lo_k)" in description


def test_background_actual_n_counts_the_sample() -> None:
    assert not accepted(Background, background(actual_n=3))


def test_background_statement_is_the_literal() -> None:
    assert not accepted(Background, background(statement="a physical truth test"))


# --- (6) and (7) CurvesFile -----------------------------------------------------------------------


def test_6_a_curves_file_with_ablations_fails() -> None:
    assert not accepted(CurvesFile, curves(ablations=[]))
    assert "ablations" not in CurvesFile.model_fields


def test_7_null_gpu_hours_without_reason_fails() -> None:
    value = curves()
    value["gpu_hours"]["runs"][1].update(hours=None, reason=None)
    value["gpu_hours"].update(total_hours=None, partial=True)
    assert not accepted(CurvesFile, value)
    value["gpu_hours"]["runs"][1]["reason"] = "scheduler log missing"
    assert accepted(CurvesFile, value)


def test_7_a_numeric_total_with_a_null_run_and_partial_false_fails() -> None:
    value = curves()
    value["gpu_hours"]["runs"][1].update(hours=None, reason="scheduler log missing")
    value["gpu_hours"].update(total_hours=1.5, partial=False)
    assert not accepted(CurvesFile, value)
    value["gpu_hours"]["partial"] = True
    assert accepted(CurvesFile, value)
    value["gpu_hours"].update(total_hours=None, partial=False)
    assert accepted(CurvesFile, value)


def test_an_unavailable_tradeoff_point_needs_a_reason_and_no_values() -> None:
    value = curves()
    value["tradeoff"][1]["reason"] = None
    assert not accepted(CurvesFile, value)
    value = curves()
    value["tradeoff"][1]["physics_r2"] = interval(0.75)
    assert not accepted(CurvesFile, value)


def test_loss_epoch_mean_lies_within_min_and_max() -> None:
    value = curves()
    value["loss_terms"][0]["epochs"][0]["mean"] = 2.0
    assert not accepted(CurvesFile, value)


def test_unavailable_validation_probe_metrics_need_a_reason() -> None:
    value = curves()
    value["validation_probe_metrics"]["reason"] = None
    assert not accepted(CurvesFile, value)


# --- (8) and (9) ExamplesFile ---------------------------------------------------------------------


def test_8_an_encoder_gallery_entry_labelled_derived_fails() -> None:
    assert not accepted(GalleryEntry, entry("model_embedding", evidence_label="Derived"))
    assert not accepted(GalleryEntry, entry("terrain_relief", evidence_label="Predicted"))
    value = examples()
    value["retrieval_gallery"][0]["encoder_top5"][0]["evidence_label"] = "Derived"
    assert not accepted(ExamplesFile, value)


def test_encoder_and_deterministic_lists_hold_their_own_families() -> None:
    value = examples()
    value["retrieval_gallery"][0]["encoder_top5"] = top5("terrain_relief")
    assert not accepted(ExamplesFile, value)
    value = examples()
    value["retrieval_gallery"][0]["deterministic_top5"] = top5("model_embedding")
    assert not accepted(ExamplesFile, value)


def test_a_short_top5_needs_a_shortfall_reason() -> None:
    value = examples()
    value["retrieval_gallery"][0]["encoder_top5"] = top5("model_embedding")[:3]
    assert not accepted(ExamplesFile, value)
    value["retrieval_gallery"][0]["shortfall_reason"] = "reference_set_too_small"
    assert accepted(ExamplesFile, value)


def test_more_than_20_failure_cases_fail() -> None:
    value = examples()
    value["failure_cases"]["entries"] = [entry("model_embedding", rank) for rank in range(1, 22)]
    assert not accepted(ExamplesFile, value)
    value["failure_cases"]["entries"] = value["failure_cases"]["entries"][:20]
    assert accepted(ExamplesFile, value)


def test_9_a_hillshade_with_both_data_and_reason_null_fails() -> None:
    assert not accepted(ExampleHillshade, hillshade(data_b64=None, unavailable_reason=None))
    assert not accepted(ExampleHillshade, hillshade(unavailable_reason="hillshade_not_stored"))
    assert accepted(
        ExampleHillshade, hillshade(data_b64=None, unavailable_reason="hillshade_not_stored")
    )


# --- UmapHeader -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"nan_means_unavailable": False},
        {"body_codes": {"0": "venus"}},
        {"source_embedding_sha256": "abc"},
        {"columns": []},
    ],
)
def test_malformed_umap_headers_fail(overrides: dict[str, Any]) -> None:
    assert not accepted(UmapHeader, umap(**overrides))


def test_umap_column_names_are_unique() -> None:
    value = umap()
    value["columns"][1]["name"] = "x"
    assert not accepted(UmapHeader, value)


# --- (4) and (5) BUNDLE_PATHS ---------------------------------------------------------------------


def prd_8_4_templates() -> list[str]:
    """Every path template in the PRD 8.4 table's Path column, expanded where a cell abbreviates
    ``report/metrics.json, curves.json`` to the directory of the first path."""
    text = PRD.read_text(encoding="utf-8")
    start = text.index("\n### 8.4 Release bundle contract\n")
    section = text[start : text.index("\n### ", start + 1)]
    templates: list[str] = []
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cell = line.split("|")[1]
        directory = ""
        for path in re.findall(r"`([^`]+)`", cell):
            if "/" in path:
                directory = path.rsplit("/", 1)[0] + "/"
            else:
                path = directory + path
            templates.append(path)
    return templates


def normalised(template: str) -> str:
    """The PRD names model/encoder.onnx and encoder.fp16.onnx; the map covers both as
    model/*.onnx, and figures/*.svg as figures/{name}.svg."""
    if template.startswith("model/") and template.endswith(".onnx"):
        return "model/*.onnx"
    if template == "figures/*.svg":
        return "figures/{name}.svg"
    return template


def test_4_bundle_paths_list_every_prd_8_4_template_exactly_once() -> None:
    templates = {normalised(template) for template in prd_8_4_templates()}
    assert len(templates) >= 18
    assert set(BUNDLE_PATHS) == templates
    data = json.loads((SCHEMA_DIR / BUNDLE_PATHS_FILE).read_text(encoding="utf-8"))
    assert data == BUNDLE_PATHS
    names = [contract.name for contract in CONTRACTS]
    for name in data.values():
        assert name in names or name in {SVG_ALLOWLIST, NO_JSON_SCHEMA}, name


def test_4_bundle_paths_are_exactly_contracts_a_3() -> None:
    assert BUNDLE_PATHS == {
        "manifest.json": "BundleManifest",
        "validation.json": "Validation",
        "sources.json": "Sources",
        "targets.json": "Targets",
        "windows/{body}.json": "Windows",
        "comparisons/{target_id}.json": "Comparisons",
        "cards/{card_id}.json": "CardRecord",
        "reference/background_{target_id}.json": "Background",
        "model/model_card.json": "ModelRecord",
        "model/embeddings.index.json": "EmbeddingsIndex",
        "report/metrics.json": "Metrics",
        "report/curves.json": "Curves",
        "report/examples.json": "Examples",
        "report/umap.bin": "UmapHeader",
        "figures/{name}.svg": "svg_allowlist",
        "tiles/{layer}/{z}/{x}/{y}.webp": "none",
        "windows/{body}.parquet": "none",
        "model/*.onnx": "none",
        "model/embeddings.f16.bin": "none",
    }


def test_the_committed_bundle_paths_file_is_canonical() -> None:
    committed = (SCHEMA_DIR / BUNDLE_PATHS_FILE).read_bytes()
    assert committed == bundle_paths_json()
    assert committed == provenance.canonical_json(BUNDLE_PATHS)


@pytest.mark.parametrize(
    ("path", "contract"),
    [
        ("manifest.json", "BundleManifest"),
        ("windows/moon.json", "Windows"),
        ("windows/moon.parquet", "none"),
        ("comparisons/moon-cr-site01-w000.json", "Comparisons"),
        ("reference/background_moon-cr-site01-w000.json", "Background"),
        ("tiles/relief/3/1/2.webp", "none"),
        ("model/encoder.fp16.onnx", "none"),
        ("figures/a.svg", "svg_allowlist"),
        ("report/umap.bin", "UmapHeader"),
        ("unknown.json", None),
        ("windows/moon/extra.json", None),
    ],
)
def test_bundle_contract_for_matches_one_template(path: str, contract: str | None) -> None:
    assert bundle_contract_for(path) == contract


def test_5_schema_check_fails_when_bundle_paths_json_drifts(tmp_path: Path) -> None:
    copy_dir = tmp_path / "json"
    shutil.copytree(SCHEMA_DIR, copy_dir)
    assert main(["check", str(copy_dir)]) == 0
    drifted = json.loads((copy_dir / BUNDLE_PATHS_FILE).read_text(encoding="utf-8"))
    drifted["report/curves.json"] = "Metrics"
    (copy_dir / BUNDLE_PATHS_FILE).write_bytes(provenance.canonical_json(drifted))
    assert main(["check", str(copy_dir)]) == 1
    (copy_dir / BUNDLE_PATHS_FILE).unlink()
    assert main(["check", str(copy_dir)]) == 1


def test_schema_write_emits_bundle_paths_json(tmp_path: Path) -> None:
    out = tmp_path / "json"
    assert main(["write", str(out)]) == 0
    assert (out / BUNDLE_PATHS_FILE).read_bytes() == bundle_paths_json()
