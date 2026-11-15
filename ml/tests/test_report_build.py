from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from helpers_report import make_run, make_shards, standin_loaders
from terraluna_ml import evaluate, g5, report, shards
from terraluna_pipeline.schema import MetricsFile, ModelRecord

CONFIG = Path(__file__).resolve().parents[2] / "config" / "run.yaml"


def _build(tmp_path: Path) -> tuple[Path, list[Path], Path]:
    manifest = make_shards(tmp_path / "shards")
    sha = shards.open_shards(manifest).data_manifest_sha256
    runs = [
        make_run(tmp_path / "runs", f"main_s{s}", seed=s, lambda_max=1.0, manifest_sha=sha)
        for s in (0, 1, 2)
    ]
    runs.append(make_run(tmp_path / "runs", "l0", seed=0, lambda_max=0.0, manifest_sha=sha))
    ev = tmp_path / "ev"
    evaluate.run_evaluation(
        manifest=manifest,
        run_tokens=[*map(str, runs), "random_init"],
        split="val",
        out_dir=ev,
        config_path=CONFIG,
        loaders=standin_loaders(),
        n_pairs=300,
        n_resamples=20,
        max_train=100,
        device="cpu",
    )
    return ev, runs, manifest


def test_report_files_validate_and_are_deterministic(tmp_path: Path) -> None:
    ev, runs, manifest = _build(tmp_path)  # noqa: RUF059
    for name in ("a", "b"):
        report.build_report(ev, runs, tmp_path / name, manifest=manifest, config_path=CONFIG)
    for rel in (
        "report/metrics.json",
        "report/curves.json",
        "report/examples.json",
        "model/model_card.json",
    ):
        a, b = (tmp_path / n / rel for n in ("a", "b"))
        assert a.read_bytes() == b.read_bytes() and a.read_text().endswith("}\n")
    out = tmp_path / "a"
    metrics = MetricsFile.model_validate_json((out / "report/metrics.json").read_text())
    card = ModelRecord.model_validate_json((out / "model/model_card.json").read_text())
    assert metrics.mock is True and metrics.g5.status == "NOT_RUN"
    assert card.g5_status == metrics.g5.status and card.onnx_sha256 is None
    assert "onnx_sha256 is null" in card.limitations
    names = {a.run_name: a.status for a in metrics.ablations}
    assert names["lambda_max_0"] == "available" and names["vicreg_only"] == "unavailable"
    assert names["moon_earth_only_tested_on_mars"] == "unavailable"
    text = "".join(
        (out / r).read_text()
        for r in (
            "report/metrics.json",
            "report/curves.json",
            "report/examples.json",
            "model/model_card.json",
        )
    ).lower()
    for phrase in report.FORBIDDEN_PHRASES:
        assert phrase.lower() not in text


def test_curves_aggregate_seeds_and_tradeoff(tmp_path: Path) -> None:
    ev, runs, _ = _build(tmp_path)
    curves = report.build_curves(ev, runs)
    grp = next(g for g in curves["loss_curves"] if g["group"] == "lambda_1")
    term = grp["terms"]["loss_total"]
    expected = [np.mean([3.0 / (e + 1) + 0.01 * s for s in (0, 1, 2)]) for e in range(3)]
    assert term["mean"] == pytest.approx(expected) and term["n_seeds"] == [3, 3, 3]
    assert term["min"][0] == pytest.approx(3.0) and term["max"][0] == pytest.approx(3.02)
    assert curves["gpu_hours"]["total"] == pytest.approx(1.0) and not curves["gpu_hours"]["partial"]
    assert curves["tradeoff"]["chance_body_accuracy"] == pytest.approx(1 / 3)
    by_lam = {p["lambda_max"]: p["status"] for p in curves["tradeoff"]["points"]}
    assert by_lam[0.1] == "unavailable" and by_lam[1.0] == "available"


def test_missing_epoch_and_gpu_hours_are_not_filled(tmp_path: Path) -> None:
    ev, runs, _ = _build(tmp_path)
    m = runs[2] / "metrics.json"
    doc: dict[str, Any] = json.loads(m.read_text())
    doc["epochs"] = doc["epochs"][:2]
    del doc["gpu_hours"]
    m.write_text(json.dumps(doc))
    curves = report.build_curves(ev, runs)
    grp = next(g for g in curves["loss_curves"] if g["group"] == "lambda_1")
    assert grp["terms"]["loss_total"]["n_seeds"] == [3, 3, 2]
    assert curves["gpu_hours"]["partial"] is True
    assert curves["gpu_hours"]["total"] == pytest.approx(0.75)
    assert any(r["gpu_hours"] is None and r["reason"] for r in curves["gpu_hours"]["per_run"])


def test_different_seed_free_configs_in_a_group_raise(tmp_path: Path) -> None:
    ev, runs, _ = _build(tmp_path)
    c = runs[1] / "config.json"
    doc = json.loads(c.read_text())
    doc["lr"] = 0.5
    c.write_text(json.dumps(doc))
    with pytest.raises(report.ReportError, match="seed-free"):
        report.build_curves(ev, runs)


def test_hygiene_and_g5_copy(tmp_path: Path) -> None:
    ev, runs, manifest = _build(tmp_path)  # noqa: RUF059
    doc = json.loads((ev / "evaluation_metrics.json").read_text())
    doc["freeze"]["written_before_test_read"] = False
    (ev / "evaluation_metrics.json").write_text(json.dumps(doc))
    with pytest.raises(report.ReportError, match="before the config was frozen"):
        report.build_metrics(ev)
    doc["freeze"]["written_before_test_read"] = True
    (ev / "evaluation_metrics.json").write_text(json.dumps(doc))
    th = g5.load_thresholds(CONFIG)
    g5.write_g5(doc, th, ev, tmp_path / "G5.json")
    copied = json.loads((ev / "g5_conditions.json").read_text())
    built = report.build_metrics(ev).g5.model_dump(mode="json")
    assert built == copied


def test_examples_layout_and_cli(tmp_path: Path) -> None:
    ev, runs, manifest = _build(tmp_path)  # noqa: RUF059
    ex = report.build_examples(ev)
    assert len(ex["gallery"]) == 4
    g = ex["gallery"][0]
    assert g["encoder_top5"]["evidence_label"] == "Predicted"
    assert g["encoder_top5"]["distance_family"] == "encoder"
    assert len(g["encoder_top5"]["windows"]) == 5
    assert g["deterministic_top5"]["status"] == "unavailable"
    assert len(ex["failure_cases"]["cases"]) == 20
    d = [c["rank_disagreement"] for c in ex["failure_cases"]["cases"]]
    assert d == sorted(d, reverse=True)
    out = tmp_path / "cli"
    code = report.main(
        [
            "build",
            "--evaluation",
            str(ev),
            "--runs",
            *map(str, runs),
            "--manifest",
            str(manifest),
            "--out",
            str(out),
            "--config",
            str(CONFIG),
        ]
    )
    assert code == 0 and (out / "report" / "examples.json").is_file()
    assert report.main(["build", "--evaluation", str(tmp_path / "none"), "--out", str(out)]) == 3
