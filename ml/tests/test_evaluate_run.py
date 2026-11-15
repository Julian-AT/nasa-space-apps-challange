from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from helpers_report import make_run, make_shards, standin_loaders
from terraluna_ml import evaluate, probes, shards, stats
from terraluna_ml.evaluate import EvaluationError
from terraluna_ml.probes import EmbeddingSet, Unavailable
from terraluna_pipeline.schema import MetricRecord

CONFIG = Path(__file__).resolve().parents[2] / "config" / "run.yaml"
OPERATOR = "orchestrator, delegated by the team"


def _inputs(tmp_path: Path) -> tuple[Path, list[str]]:
    manifest = make_shards(tmp_path / "shards")
    sha = shards.open_shards(manifest).data_manifest_sha256
    runs = [
        make_run(tmp_path / "runs", f"main_s{s}", seed=s, lambda_max=1.0, manifest_sha=sha)
        for s in (0, 1, 2)
    ]
    runs.append(make_run(tmp_path / "runs", "lambda0_s0", seed=0, lambda_max=0.0, manifest_sha=sha))
    runs.append(
        make_run(
            tmp_path / "runs",
            "nohead_s0",
            seed=0,
            lambda_max=1.0,
            use_phys_head=False,
            manifest_sha=sha,
        )
    )
    return manifest, [*(str(r) for r in runs), "random_init"]


def _run(tmp_path: Path, split: str, **kw: object) -> dict[str, object]:
    manifest, runs = (
        _inputs(tmp_path)
        if not (tmp_path / "shards").exists()
        else (
            tmp_path / "shards" / "manifest.json",
            [*sorted(str(p) for p in (tmp_path / "runs").iterdir()), "random_init"],
        )
    )
    out = tmp_path / "out" / split
    return evaluate.run_evaluation(
        manifest=manifest,
        run_tokens=runs,
        split=split,
        out_dir=out,
        config_path=CONFIG,
        loaders=standin_loaders(),
        n_pairs=500,
        n_resamples=30,
        max_train=100,
        device="cpu",
        read_log=tmp_path / "log" / "test_read_log.json",
        freeze_path=tmp_path / "out" / "val" / "freeze.json",
        **kw,  # type: ignore[arg-type]
    )


def test_val_run_writes_valid_records_and_freeze(tmp_path: Path) -> None:
    doc = _run(tmp_path, "val")
    out = tmp_path / "out" / "val"
    assert (out / "freeze.json").is_file() and (out / "evaluation_curves.json").is_file()
    assert (out / "evaluation_metrics.json").read_text().endswith("}\n")
    tests = [MetricRecord.model_validate(t) for t in doc["tests"]]  # type: ignore[attr-defined]
    ids = {t.test_id for t in tests}
    assert ids == {
        "physics_probe",
        "morphology_probe",
        "body_invariance",
        "physics_agreement",
        "target_retrieval",
        "seed_stability",
        "held_out_body",
        "out_of_distribution",
    }
    assert all(t.evidence_label == "Predicted" for t in tests)
    for t in tests:
        if t.test_id in ("morphology_probe", "target_retrieval", "held_out_body"):
            assert t.value is None and t.unavailable_reason
    morph = next(t for t in tests if t.test_id == "morphology_probe")
    assert "Robbins" in (morph.unavailable_reason or "")
    assert {a for a in doc["arms"]} == {"lambda_1", "lambda_0", "no_phys_head", "random_init"}  # type: ignore[attr-defined]
    assert doc["main_arm"] == "lambda_1" and doc["mock"] is True
    rr = doc["run_record"]  # type: ignore[index]
    assert rr["gpu_hours_partial"] is False and len(rr["checkpoint_sha256s"]) == 6  # type: ignore[index]
    seed = next(t for t in tests if t.test_id == "seed_stability")
    assert seed.value is not None and seed.baselines[0].name == "random_ranking"
    body = next(t for t in tests if t.test_id == "body_invariance")
    assert {b.name for b in body.baselines} >= {"lambda_0", "random_init", "chance"}


def test_test_read_needs_confirmation_freeze_and_is_logged_once(tmp_path: Path) -> None:
    _run(tmp_path, "val")
    with pytest.raises(EvaluationError, match="confirm"):
        _run(tmp_path, "test")
    _run(tmp_path, "test", confirm=evaluate.CONFIRM_TEXT)
    log = json.loads((tmp_path / "log" / "test_read_log.json").read_text())
    assert len(log) == 1 and log[0]["operator"] == OPERATOR and log[0]["reread"] is False
    freeze = json.loads((tmp_path / "out" / "val" / "freeze.json").read_text())
    assert log[0]["freeze_sha256"] == evaluate.freeze_hash(freeze)
    with pytest.raises(EvaluationError, match="already read"):
        _run(tmp_path, "test", confirm=evaluate.CONFIRM_TEXT)
    _run(tmp_path, "test", confirm=evaluate.CONFIRM_TEXT, allow_reread=True)
    log = json.loads((tmp_path / "log" / "test_read_log.json").read_text())
    assert [e["reread"] for e in log] == [False, True]


def test_test_read_without_freeze_or_after_change_refused(tmp_path: Path) -> None:
    _inputs(tmp_path)
    with pytest.raises(EvaluationError, match="no freeze"):
        _run(tmp_path, "test", confirm=evaluate.CONFIRM_TEXT)
    assert not (tmp_path / "log" / "test_read_log.json").exists()
    _run(tmp_path, "val")
    (tmp_path / "runs" / "main_s0" / "backbone.pt").write_bytes(b"changed after freeze")
    with pytest.raises(EvaluationError, match="changed since the freeze"):
        _run(tmp_path, "test", confirm=evaluate.CONFIRM_TEXT)
    assert not (tmp_path / "log" / "test_read_log.json").exists()


def test_log_is_written_before_blocks_are_read(tmp_path: Path) -> None:
    _run(tmp_path, "val")
    loaders = standin_loaders()

    def boom(_: Path) -> object:
        raise RuntimeError("stop at first backbone load")

    with pytest.raises(RuntimeError, match="stop at first"):
        evaluate.run_evaluation(
            manifest=tmp_path / "shards" / "manifest.json",
            run_tokens=[*sorted(str(p) for p in (tmp_path / "runs").iterdir()), "random_init"],
            split="test",
            out_dir=tmp_path / "out" / "test",
            config_path=CONFIG,
            loaders=evaluate.Loaders(boom, loaders.random_init),
            confirm=evaluate.CONFIRM_TEXT,
            read_log=tmp_path / "log" / "test_read_log.json",
            freeze_path=tmp_path / "out" / "val" / "freeze.json",
            n_pairs=500,
            n_resamples=30,
            max_train=100,
            device="cpu",
        )
    assert (tmp_path / "log" / "test_read_log.json").is_file()


def test_manifest_mismatch_with_run_refused(tmp_path: Path) -> None:
    manifest = make_shards(tmp_path / "shards")
    run = make_run(tmp_path / "runs", "main", seed=0, lambda_max=1.0, manifest_sha="f" * 64)
    with pytest.raises(EvaluationError, match="data manifest"):
        evaluate.run_evaluation(
            manifest=manifest,
            run_tokens=[str(run)],
            split="val",
            out_dir=tmp_path / "o",
            config_path=CONFIG,
            loaders=standin_loaders(),
            n_pairs=100,
            n_resamples=10,
        )


def test_cli_exit_codes_and_g5(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        evaluate.main([])
    assert exc.value.code == 2
    _run(tmp_path, "val")
    metrics = tmp_path / "out" / "val" / "evaluation_metrics.json"
    out = tmp_path / "g5"
    code = evaluate.main(
        [
            "g5",
            "--metrics",
            str(metrics),
            "--out",
            str(out),
            "--config",
            str(CONFIG),
            "--gate-out",
            str(out / "G5.json"),
        ]
    )
    assert code == 0
    gate = json.loads((out / "G5.json").read_text())
    assert gate["status"] == "NOT_RUN" and gate["gate_id"] == "G5"
    assert (out / "g5_conditions.json").is_file()
    assert (
        evaluate.main(
            [
                "run",
                "--manifest",
                str(tmp_path / "nope.json"),
                "--runs",
                "x",
                "--split",
                "test",
                "--out",
                str(tmp_path / "t"),
            ]
        )
        == 3
    )


# --- known-answer tests of the individual statistics ---------------------------------------------


def _emb(x: np.ndarray, body: np.ndarray, blocks: np.ndarray) -> EmbeddingSet:
    n = len(x)
    return EmbeddingSet(
        x.astype(np.float32),
        np.arange(n, dtype=np.int64),
        body.astype(np.int64),
        blocks,
        np.array([f"w{i}" for i in range(n)]),
    )


def test_physics_agreement_known_answers() -> None:
    rng = np.random.default_rng(0)
    n = 600
    body = rng.integers(0, 3, size=n)
    blocks = np.array([f"b{i % 12}" for i in range(n)])
    raw = np.zeros((n, 11))
    raw[:, :10] = rng.normal(size=(n, 10))
    mask = np.ones((n, 11), bool)
    mask[0, 4] = False
    # embedding = the targets themselves: cosine distance ranks like RMS distance (monotone-ish)
    aligned = evaluate.physics_agreement(
        _emb(raw[:, :10] + 0.0, body, blocks), raw, mask, n_pairs=2000, n_resamples=20
    )
    rho = aligned["rms_log_nu_10"]
    assert isinstance(rho, stats.Interval) and rho.estimate > 0.5
    rnd = evaluate.physics_agreement(
        _emb(rng.normal(size=(n, 10)), body, blocks), raw, mask, n_pairs=2000, n_resamples=20
    )
    rho_r = rnd["rms_log_nu_10"]
    assert isinstance(rho_r, stats.Interval) and abs(rho_r.estimate) < 0.1
    assert isinstance(aligned["n_pairs_dropped"], int)


def test_cross_body_pairs_are_cross_body_and_too_few_raises() -> None:
    body = np.array([0] * 30 + [1] * 30 + [2] * 30)
    a, b = evaluate.sample_cross_body_pairs(body, 500, 1)
    assert len(a) == 500 and np.all(body[a] != body[b])
    a2, b2 = evaluate.sample_cross_body_pairs(body, 500, 1)
    assert np.array_equal(a, a2) and np.array_equal(b, b2)
    with pytest.raises(EvaluationError, match="cross-body pairs"):
        evaluate.sample_cross_body_pairs(body, 5000, 1)


def test_seed_stability_known_answers() -> None:
    rng = np.random.default_rng(3)
    cand = rng.normal(size=(400, 16)).astype(np.float32)
    tgt = rng.normal(size=(3, 16)).astype(np.float32)
    blocks = np.array([f"b{i % 20}" for i in range(400)])
    same = evaluate.seed_stability(
        {0: tgt, 1: tgt, 2: tgt}, {0: cand, 1: cand, 2: cand}, blocks, n_resamples=20
    )
    assert not isinstance(same, Unavailable) and same["mean"] == 1.0 and same["min"] == 1.0
    sims = []
    for rep in range(10):
        r = np.random.default_rng(100 + rep)
        ind = evaluate.seed_stability(
            {s: r.normal(size=(5, 16)).astype(np.float32) for s in (0, 1)},
            {s: r.normal(size=(400, 16)).astype(np.float32) for s in (0, 1)},
            blocks,
            n_resamples=2,
        )
        assert not isinstance(ind, Unavailable)
        sims.append(ind["mean"])
    expect = stats.random_jaccard_expectation(400, 20)
    assert abs(float(np.mean(sims)) - expect) < 0.03
    assert isinstance(evaluate.seed_stability({0: tgt}, {0: cand}, blocks), Unavailable)
    assert same["n_targets"] == 3 and same["n_blocks"] == 20


def test_ood_known_answers() -> None:
    rng = np.random.default_rng(5)
    d = 16
    train = rng.normal(size=(20000, d)).astype(np.float32)
    model = evaluate.fit_ood(train, reg=1e-6)
    fresh = rng.normal(size=(20000, d)).astype(np.float32)
    rate = float((evaluate.mahalanobis(model, fresh) > model.threshold).mean())
    assert 0.005 <= rate <= 0.02
    shifted = fresh + 5.0
    assert float((evaluate.mahalanobis(model, shifted) > model.threshold).mean()) >= 0.99


def test_physics_probe_on_standin_beats_random_labels(tmp_path: Path) -> None:
    # sanity: probes work on shard rows through the stand-in backbone
    manifest = make_shards(tmp_path / "s")
    sh = shards.open_shards(manifest)
    bb = standin_loaders().random_init(0)
    rows = {s: sh.rows(s) for s in ("train", "val", "test")}
    sets = {
        s: EmbeddingSet(
            evaluate.embed_rows(bb, sh, r),
            r,
            sh.column("body_index")[r].astype(np.int64),
            sh.column("eval_block")[r].astype(str),
            sh.column("window_id")[r].astype(str),
        )
        for s, r in rows.items()
    }
    raw, mask = sh.physics_raw()
    probe = probes.fit_physics_probe(
        sets["train"],
        raw[rows["train"]],
        mask[rows["train"]],
        sets["val"],
        raw[rows["val"]],
        mask[rows["val"]],
        sets["test"],
        raw[rows["test"]],
        mask[rows["test"]],
        ("a",) * 0 + tuple(f"t{i}" for i in range(11)),
    )
    r2 = probes.physics_r2(probe, "t0", n_resamples=20)
    assert not isinstance(r2, Unavailable) and r2.estimate > 0.3
