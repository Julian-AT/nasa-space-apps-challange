"""Model report builder (PRD 11.9): metrics.json, curves.json, examples.json, model_card.json.

``python -m terraluna_ml.report build --evaluation DIR --runs RUNDIR... --manifest M --out DIR``
reads the evaluation outputs (``evaluation_metrics.json``, ``evaluation_curves.json`` and, when
G5 has been computed, ``g5_conditions.json``) and the training run directories, and writes

- ``<out>/report/metrics.json``: a ``MetricsFile`` (every PRD 11.6 test with its interval and
  baselines side by side, the G5 summary copied unchanged, ablations, run record);
- ``<out>/report/curves.json``, ``<out>/report/examples.json`` (layouts below);
- ``<out>/model/model_card.json``: a ``ModelRecord``.

The G5 summary is copied from ``g5_conditions.json``; this module never evaluates a threshold
and never writes ``validation.json``. Everything is Predicted and the model is EXPERIMENTAL.

curves.json layout (all arrays are parallel; missing is ``null``, never 0)::

    {"format": "terraluna-ml-report-curves/1", "mock": bool, "evaluated_split": "val"|"test",
     "model_status": "EXPERIMENTAL",
     "loss_curves": [                       # one entry per run group (arm)
        {"group": "lambda_1", "config_hash": "<sha256 of seed-free config>", "seeds": [0, 1, 2],
         "terms": {"loss_total": {"epochs": [0, 1], "mean": [..], "min": [..], "max": [..],
                                  "n_seeds": [3, 3]}}}],
     "gpu_hours": {"total": float|null, "partial": bool,
                   "per_run": [{"run": str, "gpu_hours": float|null, "reason": str|null}]},
     "tradeoff": {"chance_body_accuracy": 1/n_bodies, "n_bodies": 3,
                  "points": [{"lambda_max": float, "arm": str,
                              "status": "available"|"unavailable", "reason": str|null,
                              "body_balanced_accuracy": {"value","ci_low","ci_high"}|null,
                              "physics_r2_relief": {"mean_of_lag_point_estimates": float,
                                                    "per_lag": [{"lag_m", "value", "ci_low",
                                                                 "ci_high"}]}|null}]}}

examples.json layout::

    {"format": "terraluna-ml-report-examples/1", "mock": bool, "evaluated_split": str,
     "model_status": "EXPERIMENTAL", "evidence_label": "Predicted",
     "gallery": [{"target_window_id": str,
                  "encoder_top5": {"evidence_label": "Predicted", "distance_family": "encoder",
                                   "shortfall": str|null,
                                   "windows": [{"rank", "window_id", "cosine_distance",
                                                "lat_deg", "lon_deg"}]},
                  "deterministic_top5": {"status": "unavailable", "reason": str}}],
     "failure_cases": {"sort_rule": str, "basis": str, "shortfall": str|null,
                       "cases": [{"window_id", "target_window_id", "rank_disagreement",
                                  "encoder_rank", "rms_log_nu_rank", "encoder_cosine_distance",
                                  "rms_log_nu_distance", "evidence_label", "distance_family"}]},
     "ood_examples": [{"window_id", "body", "mahalanobis", "threshold", "label"}]}

The two gallery lists are never merged into one score (PRD 3).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from terraluna_ml.evaluate import REASON_RETRIEVAL
from terraluna_ml.shards import BODIES, canonical_json, open_shards, sha256_path
from terraluna_pipeline.config import load_run_config
from terraluna_pipeline.schema import (
    Ablation,
    G5Summary,
    MetricRecord,
    MetricsFile,
    ModelRecord,
    ObjectivesAndWeights,
    RunRecord,
    TrainingData,
    VicregWeights,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
LAGS_M: Final = (270, 360, 540, 720, 1080)
FORBIDDEN_PHRASES: Final = ("AI-verified analog", "best match", "best place on Earth")
SEED_FREE_STRIP: Final = frozenset(
    {"seed", "name", "run_name", "out_dir", "output_dir", "run_dir", "path", "checkpoint"}
)
LAMBDA_SWEEP: Final = (0.0, 0.1, 1.0)
VICREG_DEFAULT: Final = (25.0, 25.0, 1.0)

INTENDED_USE: Final = (
    "A second opinion only. The encoder embeds 5 km windows at 78 m sampling of Moon, Mars and "
    "Earth terrain into 256 numbers and offers a third, always Predicted, distance family beside "
    "the deterministic terrain fingerprint. It is EXPERIMENTAL, stays outside every ranking until "
    "gate G5 passes, and never replaces the fingerprint on any card."
)
LIMITATIONS: Final = (
    "Every encoder statistic is Predicted, a model output and not an observation. The model "
    "makes no claim about radar, trafficability, regolith mechanics or ice, and a close "
    "embedding is not evidence of an analog. It was evaluated only on 5 km windows at 78 m "
    "sampling from spatially held-out blocks; results do not transfer to other scales. The "
    "morphology probe is Unavailable because no crater labels exist in this dataset. Frozen "
    "ImageNet ResNet-18 and DINO ViT-S/8 baselines are Unavailable until their weights are "
    "fetched. Windows overlapping a target footprint are excluded by the dataset build."
)
ONNX_ABSENT_NOTE: Final = " No ONNX file was exported with this card, so onnx_sha256 is null."


class ReportError(ValueError):
    """Evaluation outputs that cannot honestly be turned into a report."""


def _read(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ReportError(f"{path} is not a JSON object")
    return loaded


def _write(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(document), encoding="utf-8")


def _check_phrases(document: object, where: str) -> None:
    text = json.dumps(document)
    for phrase in FORBIDDEN_PHRASES:
        if phrase.lower() in text.lower():
            raise ReportError(f"forbidden phrase {phrase!r} in {where}")


# --- hygiene -------------------------------------------------------------------------------------


def check_hygiene(evaluation: Mapping[str, Any]) -> None:
    freeze = evaluation.get("freeze") or {}
    if not freeze.get("sha256"):
        raise ReportError("the evaluation output carries no freeze hash")
    if freeze.get("written_before_test_read") is not True:
        raise ReportError("test blocks were read before the config was frozen")


# --- metrics.json --------------------------------------------------------------------------------


def _summary(evaluation_dir: Path) -> G5Summary:
    path = evaluation_dir / "g5_conditions.json"
    if not path.is_file():
        return G5Summary(status="NOT_RUN", conditions=[], failed_tests=[])
    return G5Summary.model_validate(_read(path))


def _records(raw: Sequence[Mapping[str, Any]]) -> list[MetricRecord]:
    return [MetricRecord.model_validate(r) for r in raw]


def build_ablations(evaluation: Mapping[str, Any]) -> list[Ablation]:
    """The PRD 11.5 ablation rows; an absent run is an unavailable row with its reason."""
    arms: Mapping[str, Any] = evaluation["arms"]
    wanted: list[tuple[str, str]] = [
        ("no_physics_head", "no_phys_head"),
        ("lambda_max_0", "lambda_0"),
        ("vicreg_only", "vicreg_only"),
        ("physics_head_only", "physics_head_only"),
        ("lambda_max_1.0", "lambda_1"),
        ("lambda_max_0.1", "lambda_0.1"),
    ]
    rows: list[Ablation] = []
    used: set[str] = set()
    for name, arm in wanted:
        if arm in arms:
            used.add(arm)
            first = sorted(arms[arm]["runs"])[0]
            rows.append(
                Ablation(
                    run_name=name,
                    status="available",
                    reason=None,
                    metrics=_records(arms[arm]["runs"][first]["records"]),
                )
            )
        else:
            rows.append(
                Ablation(
                    run_name=name,
                    status="unavailable",
                    reason="no run of this configuration was supplied to the evaluation",
                    metrics=[],
                )
            )
    held = [
        MetricRecord.model_validate(t)
        for t in evaluation["tests"]
        if t["test_id"] == "held_out_body" and t["value"] is not None
    ]
    if held:
        rows.append(
            Ablation(
                run_name="moon_earth_only_tested_on_mars",
                status="available",
                reason=None,
                metrics=held,
            )
        )
    else:
        rows.append(
            Ablation(
                run_name="moon_earth_only_tested_on_mars",
                status="unavailable",
                reason=(evaluation.get("unavailable") or {}).get("held_out_body")
                or "no Moon+Earth-only run was supplied",
                metrics=[],
            )
        )
    for arm in sorted(set(arms) - used):
        if arm == evaluation["main_arm"]:
            continue
        first = sorted(arms[arm]["runs"])[0]
        rows.append(
            Ablation(
                run_name=arm,
                status="available",
                reason=None,
                metrics=_records(arms[arm]["runs"][first]["records"]),
            )
        )
    return rows


def build_metrics(evaluation_dir: Path) -> MetricsFile:
    evaluation = _read(evaluation_dir / "evaluation_metrics.json")
    check_hygiene(evaluation)
    return MetricsFile(
        mock=bool(evaluation["mock"]),
        run_id=None,
        model_id=evaluation["model_id"],
        evaluated_split=evaluation["evaluated_split"],
        g5=_summary(evaluation_dir),
        tests=_records(evaluation["tests"]),
        ablations=build_ablations(evaluation),
        run_record=RunRecord.model_validate(evaluation["run_record"]),
    )


# --- curves.json ---------------------------------------------------------------------------------


def seed_free_hash(config: Mapping[str, Any]) -> str:
    kept = {k: v for k, v in config.items() if k not in SEED_FREE_STRIP}
    return hashlib.sha256(canonical_json(kept).encode("utf-8")).hexdigest()


def _arm_of(metrics: Mapping[str, Any]) -> str:
    lam = float(metrics["lambda_max"])
    if not metrics["use_phys_head"]:
        return "no_phys_head"
    return "lambda_0" if lam == 0.0 else f"lambda_{lam:g}"


def loss_curves(run_dirs: Sequence[Path]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per-term, per-epoch seed mean/min/max (a seed missing an epoch lowers ``n_seeds``)."""
    groups: dict[str, list[tuple[Path, dict[str, Any], dict[str, Any]]]] = {}
    for d in run_dirs:
        metrics, config = _read(d / "metrics.json"), _read(d / "config.json")
        groups.setdefault(_arm_of(metrics), []).append((d, metrics, config))
    curves: list[dict[str, Any]] = []
    per_run: list[dict[str, Any]] = []
    for group, runs in sorted(groups.items()):
        hashes = {seed_free_hash(c) for _, _, c in runs}
        if len(hashes) != 1:
            raise ReportError(f"runs of group {group} differ in their seed-free config")
        terms: dict[str, dict[int, list[float]]] = {}
        for d, metrics, _ in runs:
            for entry in metrics.get("epochs", []):
                for key, value in entry.items():
                    if (
                        key == "epoch"
                        or isinstance(value, bool)
                        or not isinstance(value, int | float)
                    ):
                        continue
                    terms.setdefault(key, {}).setdefault(int(entry["epoch"]), []).append(
                        float(value)
                    )
            gpu = metrics.get("gpu_hours")
            per_run.append(
                {
                    "run": d.name,
                    "gpu_hours": float(gpu) if isinstance(gpu, int | float) else None,
                    "reason": None if isinstance(gpu, int | float) else "gpu_hours not logged",
                }
            )
        out_terms: dict[str, Any] = {}
        for term, by_epoch in sorted(terms.items()):
            epochs = sorted(by_epoch)
            out_terms[term] = {
                "epochs": epochs,
                "mean": [sum(by_epoch[e]) / len(by_epoch[e]) for e in epochs],
                "min": [min(by_epoch[e]) for e in epochs],
                "max": [max(by_epoch[e]) for e in epochs],
                "n_seeds": [len(by_epoch[e]) for e in epochs],
            }
        curves.append(
            {
                "group": group,
                "config_hash": hashes.pop(),
                "seeds": sorted(int(m["seed"]) for _, m, _ in runs),
                "terms": out_terms,
            }
        )
    logged = [r["gpu_hours"] for r in per_run if r["gpu_hours"] is not None]
    gpu_hours = {
        "total": float(sum(logged)) if logged else None,
        "partial": len(logged) < len(per_run) or not per_run,
        "per_run": sorted(per_run, key=lambda r: str(r["run"])),
    }
    return curves, gpu_hours


def tradeoff(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """Body-probe accuracy against physics R2 per lambda_max; chance is 1 / number of bodies."""
    n_bodies = len(evaluation.get("training_data", {}).get("bodies", BODIES))
    arms: Mapping[str, Any] = evaluation["arms"]
    points: list[dict[str, Any]] = []
    seen: set[float] = set()
    for arm, info in sorted(arms.items()):
        if not info.get("use_phys_head") or info.get("lambda_max") is None:
            continue
        recs = _records(info["runs"][sorted(info["runs"])[0]]["records"])
        body = next((r for r in recs if r.test_id == "body_invariance"), None)
        lags = [
            next(
                (
                    r
                    for r in recs
                    if r.test_id == "physics_probe" and r.target == f"log_nu_relief_{lag}"
                ),
                None,
            )
            for lag in LAGS_M
        ]
        ok = (
            body is not None
            and body.value is not None
            and all(r is not None and r.value is not None for r in lags)
        )
        lam = float(info["lambda_max"])
        seen.add(lam)
        if not ok:
            points.append(
                {
                    "lambda_max": lam,
                    "arm": arm,
                    "status": "unavailable",
                    "reason": "body probe or a relief-lag R2 is Unavailable for this run",
                    "body_balanced_accuracy": None,
                    "physics_r2_relief": None,
                }
            )
            continue
        assert body is not None
        points.append(
            {
                "lambda_max": lam,
                "arm": arm,
                "status": "available",
                "reason": None,
                "body_balanced_accuracy": {
                    "value": body.value,
                    "ci_low": body.ci_low,
                    "ci_high": body.ci_high,
                },
                "physics_r2_relief": {
                    "mean_of_lag_point_estimates": sum(
                        r.value for r in lags if r and r.value is not None
                    )
                    / len(lags),
                    "per_lag": [
                        {"lag_m": lag, "value": r.value, "ci_low": r.ci_low, "ci_high": r.ci_high}
                        for lag, r in zip(LAGS_M, lags, strict=True)
                        if r is not None
                    ],
                },
            }
        )
    for lam in LAMBDA_SWEEP:
        if lam not in seen:
            points.append(
                {
                    "lambda_max": lam,
                    "arm": f"lambda_{lam:g}",
                    "status": "unavailable",
                    "reason": "no completed run with this lambda_max was supplied",
                    "body_balanced_accuracy": None,
                    "physics_r2_relief": None,
                }
            )
    points.sort(key=lambda p: p["lambda_max"])
    return {"chance_body_accuracy": 1.0 / n_bodies, "n_bodies": n_bodies, "points": points}


def build_curves(evaluation_dir: Path, run_dirs: Sequence[Path]) -> dict[str, Any]:
    evaluation = _read(evaluation_dir / "evaluation_metrics.json")
    curves, gpu = loss_curves(run_dirs)
    return {
        "format": "terraluna-ml-report-curves/1",
        "mock": bool(evaluation["mock"]),
        "evaluated_split": evaluation["evaluated_split"],
        "model_status": "EXPERIMENTAL",
        "loss_curves": curves,
        "gpu_hours": gpu,
        "tradeoff": tradeoff(evaluation),
    }


# --- examples.json -------------------------------------------------------------------------------


def build_examples(evaluation_dir: Path) -> dict[str, Any]:
    ev = _read(evaluation_dir / "evaluation_curves.json")
    ex = ev["examples"]
    gallery = [
        {
            "target_window_id": t["target_window_id"],
            "encoder_top5": {
                "evidence_label": "Predicted",
                "distance_family": "encoder",
                "shortfall": t["shortfall"],
                "windows": t["windows"],
            },
            "deterministic_top5": {"status": "unavailable", "reason": REASON_RETRIEVAL},
        }
        for t in ex["top5_by_target"]
    ]
    return {
        "format": "terraluna-ml-report-examples/1",
        "mock": bool(ev["mock"]),
        "evaluated_split": ev["evaluated_split"],
        "model_status": "EXPERIMENTAL",
        "evidence_label": "Predicted",
        "gallery": gallery,
        "failure_cases": {
            "sort_rule": ex["failure_sort_rule"],
            "basis": ex["failure_basis"],
            "shortfall": ex.get("failure_shortfall"),
            "cases": ex["failure_cases"],
        },
        "ood_examples": ex["ood_examples"],
    }


# --- model_card.json -----------------------------------------------------------------------------


def build_model_card(
    evaluation_dir: Path, out_dir: Path, metrics: MetricsFile, config_path: Path
) -> ModelRecord:
    ev = _read(evaluation_dir / "evaluation_metrics.json")
    model = ev["model"]
    if not model.get("parameter_count"):
        raise ReportError("the evaluation output has no parameter count")
    cfg: Mapping[str, Any] = model.get("config", {})
    counts = ev["training_data"]["counts_by_body_split"]
    window_counts: Any = {
        b: {s: counts.get(b, {}).get(s) for s in ("train", "val", "test")} for b in BODIES
    }
    vicreg_cfg = cfg.get("vicreg")
    inv, var, cov = (
        (vicreg_cfg["invariance"], vicreg_cfg["variance"], vicreg_cfg["covariance"])
        if isinstance(vicreg_cfg, Mapping)
        else VICREG_DEFAULT
    )
    alpha = cfg.get("alpha_phys")
    if alpha is None:
        alpha = load_run_config(config_path).value("ml.alpha_phys")
    onnx = out_dir / "model" / "encoder.onnx"
    limitations = LIMITATIONS if onnx.is_file() else LIMITATIONS + ONNX_ABSENT_NOTE
    main_run = ev["arms"][ev["main_arm"]]
    lam = main_run["lambda_max"]
    card = ModelRecord(
        model_id=ev["model_id"],
        version="0.1.0-experimental",
        architecture=f"{model.get('architecture') or 'encoder'}; 256-d L2-normalised embedding",
        parameter_count=int(model["parameter_count"]),
        training_data=TrainingData(
            bodies=list(BODIES),
            product_ids=ev["training_data"]["product_ids"],
            window_counts=window_counts,
            split_groups={
                b: "spatial blocks (eval_block); train/val/test by block" for b in BODIES
            },
        ),
        objectives_and_weights=ObjectivesAndWeights(
            vicreg=VicregWeights(invariance=inv, variance=var, covariance=cov),
            alpha_phys=float(str(alpha)),
            lambda_max=float(lam),
            schedule=str(cfg.get("lambda_schedule") or "see the run's config.json"),
        ),
        seeds=list(model["seeds"]),
        metrics_with_ci=metrics.tests,
        g5_status=metrics.g5.status,
        intended_use=INTENDED_USE,
        limitations=limitations,
        licence="Apache-2.0",
        onnx_sha256=sha256_path(onnx) if onnx.is_file() else None,
    )
    return card


def build_report(
    evaluation_dir: Path,
    run_dirs: Sequence[Path],
    out_dir: Path,
    *,
    manifest: Path | None = None,
    config_path: Path = REPO_ROOT / "config" / "run.yaml",
) -> None:
    ev = _read(evaluation_dir / "evaluation_metrics.json")
    if manifest is not None:
        recorded = ev["run_record"]["data_manifest_sha256"]
        if open_shards(manifest).data_manifest_sha256 != recorded:
            raise ReportError("the shard manifest is not the one the evaluation used")
    metrics = build_metrics(evaluation_dir)
    documents: dict[Path, object] = {
        out_dir / "report" / "metrics.json": metrics.model_dump(mode="json"),
        out_dir / "report" / "curves.json": build_curves(evaluation_dir, run_dirs),
        out_dir / "report" / "examples.json": build_examples(evaluation_dir),
        out_dir / "model" / "model_card.json": build_model_card(
            evaluation_dir, out_dir, metrics, config_path
        ).model_dump(mode="json"),
    }
    for path, doc in documents.items():
        _check_phrases(doc, str(path))
    for path, doc in documents.items():
        _write(path, doc)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m terraluna_ml.report")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="write report/*.json and model/model_card.json")
    b.add_argument("--evaluation", type=Path, required=True)
    b.add_argument("--runs", type=Path, nargs="*", default=[])
    b.add_argument("--manifest", type=Path, default=None)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "run.yaml")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        build_report(
            args.evaluation, args.runs, args.out, manifest=args.manifest, config_path=args.config
        )
    except (ReportError, FileNotFoundError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 3
    print(f"wrote {args.out}/report and {args.out}/model")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
