from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from terraluna_ml import g5
from terraluna_pipeline.schema import MetricRecord

TH = g5.G5Thresholds(0.8, 0.10, 0.60, 0.5, 0.5)
LAGS = g5.LAGS_M


def rec(
    test_id: str,
    value: float | None,
    lo: float | None = None,
    hi: float | None = None,
    *,
    target: str | None = None,
    body: str | None = "pooled",
    baselines: list[dict[str, Any]] | None = None,
) -> MetricRecord:
    return MetricRecord.model_validate(
        {
            "test_id": test_id,
            "measure": "m",
            "target": target,
            "body": body,
            "value": value,
            "ci_low": lo if value is not None else None,
            "ci_high": hi if value is not None else None,
            "n_blocks": 5 if value is not None else None,
            "n_bootstrap": 1000 if value is not None else None,
            "unavailable_reason": None if value is not None else "not computed here",
            "evidence_label": "Predicted",
            "baselines": baselines or [],
        }
    )


def make_tests(
    *,
    r2_low: float = 0.9,
    acc: float = 0.5,
    rho: float = 0.7,
    jac: float = 0.8,
    morph: tuple[float, float] | None = None,
) -> list[MetricRecord]:
    out = [
        rec("physics_probe", r2_low + 0.05, r2_low, 0.99, target=f"log_nu_relief_{lag}")
        for lag in LAGS
    ]
    out.append(rec("body_invariance", acc, acc - 0.05, acc + 0.05))
    out.append(rec("physics_agreement", rho, rho - 0.05, rho + 0.05, target="rms_log_nu_10"))
    out.append(rec("seed_stability", jac, jac - 0.05, jac + 0.05))
    if morph is None:
        out.append(rec("morphology_probe", None, body="moon"))
    else:
        out.append(rec("morphology_probe", morph[0], morph[1], morph[1] + 0.1, body="moon"))
    return out


def by_n(conds: list[Any]) -> dict[int, bool | None]:
    return {c.condition: c.passed for c in conds}


def test_boundaries() -> None:
    assert by_n(g5.compute_g5(make_tests(r2_low=0.8), TH))[1] is True
    assert by_n(g5.compute_g5(make_tests(r2_low=0.7999999), TH))[1] is False
    assert by_n(g5.compute_g5(make_tests(acc=0.60), TH))[3] is True
    assert by_n(g5.compute_g5(make_tests(acc=0.6000001), TH))[3] is False
    assert by_n(g5.compute_g5(make_tests(rho=0.5), TH))[4] is True
    assert by_n(g5.compute_g5(make_tests(rho=0.4999999), TH))[4] is False
    assert by_n(g5.compute_g5(make_tests(jac=0.5), TH))[5] is True
    assert by_n(g5.compute_g5(make_tests(jac=0.4999999), TH))[5] is False


def test_morph_gain_boundaries_and_unavailable() -> None:
    assert by_n(g5.compute_g5(make_tests(), TH))[2] is None
    assert by_n(g5.compute_g5(make_tests(morph=(0.10, 0.0)), TH))[2] is False
    assert by_n(g5.compute_g5(make_tests(morph=(0.10, 0.001)), TH))[2] is True
    assert by_n(g5.compute_g5(make_tests(morph=(0.0999999, 0.5)), TH))[2] is False


def test_condition6_is_paired_interval_strictly_above_zero() -> None:
    def with_pair(low: float) -> list[MetricRecord]:
        out = []
        for t in make_tests():
            if t.test_id == "physics_probe":
                bl = [
                    {
                        "name": b,
                        "value": 0.1,
                        "ci_low": 0.0,
                        "ci_high": 0.2,
                        "paired_diff_ci_low": low,
                        "paired_diff_ci_high": low + 1,
                        "unavailable_reason": None,
                    }
                    for b in g5.BASELINES_6
                ]
                t = MetricRecord.model_validate({**t.model_dump(), "baselines": bl})
            out.append(t)
        return out

    # morphology is unavailable, so condition 6 cannot be true; but a zero bound is false.
    assert by_n(g5.compute_g5(with_pair(0.0), TH))[6] is False
    assert by_n(g5.compute_g5(with_pair(0.01), TH))[6] is None
    assert by_n(g5.compute_g5(make_tests(), TH))[6] is None


def metrics(split: str = "test", mock: bool = False, **kw: Any) -> dict[str, Any]:
    return {
        "mock": mock,
        "evaluated_split": split,
        "tests": [t.model_dump(mode="json") for t in make_tests(**kw)],
    }


def test_status_rules_and_never_pass_without_parity() -> None:
    s, result = g5.summarise(metrics(), TH)
    assert s.status == "PARTIAL" and "ONNX parity" in result and "condition_2" in result
    f, result = g5.summarise(metrics(acc=0.7), TH)
    assert f.status == "FAIL" and "condition_3:body_invariance" in f.failed_tests
    n, result = g5.summarise(metrics(split="val"), TH)
    assert n.status == "NOT_RUN" and "validation" in result
    m, result = g5.summarise(metrics(mock=True), TH)
    assert m.status == "NOT_RUN" and result == "mock metrics; G5 not evaluated"
    assert m.conditions == []


def test_pass_needs_all_true_and_parity() -> None:
    all_true = make_tests(morph=(0.2, 0.1))
    conds = g5.compute_g5(all_true, TH)
    # condition 6 stays unavailable without baselines: never a PASS
    assert by_n(conds)[6] is None
    side: dict[str, Any] = {
        "fp32_max_abs_diff": 1e-5,
        "fp16_cosine_min": 0.9999,
        "n_windows": 256,
        "model_sha256": "b",
    }
    parity: dict[str, Any] = {
        "mock": False,
        "model_source": "trained",
        "source_checkpoint_sha256": "a" * 64,
        "python": {
            "fp32_max_abs_diff": 1e-5,
            "fp16_cosine_min": 0.9999,
            "n_windows": 256,
            "model_sha256": "b",
        },
        "wasm": {
            "fp32_max_abs_diff": 1e-5,
            "fp16_cosine_min": 0.9999,
            "n_windows": 256,
            "model_sha256": "b",
        },
    }
    ok, _ = g5.check_parity(parity, ["a" * 64])
    assert ok
    assert not g5.check_parity(parity, ["c" * 64])[0]
    bad = {**parity, "python": {**side, "fp32_max_abs_diff": 1.1e-4}}
    assert not g5.check_parity(bad, ["a" * 64])[0]
    assert not g5.check_parity({**parity, "model_source": "random-init"}, ["a" * 64])[0]
    assert not g5.check_parity(None, [])[0]


def test_thresholds_only_from_config(tmp_path: Path) -> None:
    with pytest.raises(g5.G5ThresholdError, match="phys_r2_min"):
        g5.thresholds_from_mapping({})
    with pytest.raises(g5.G5ThresholdError, match="body_acc_max"):
        g5.thresholds_from_mapping(
            {
                "phys_r2_min": 0.8,
                "morph_gain_min": 0.1,
                "body_acc_max": None,
                "agreement_rho_min": 0.5,
                "seed_jaccard20_min": 0.5,
            }
        )
    real = g5.load_thresholds(Path(__file__).resolve().parents[2] / "config" / "run.yaml")
    assert real == TH
    m = metrics()
    m["ml_g5_thresholds_at_freeze"] = {**TH.as_dict(), "phys_r2_min": 0.5}
    with pytest.raises(g5.G5ThresholdError, match="freeze"):
        g5.summarise(m, TH)


def test_write_is_deterministic_and_validates(tmp_path: Path) -> None:
    for name in ("a", "b"):
        g5.write_g5(metrics(), TH, tmp_path / name, tmp_path / name / "G5.json")
    assert (tmp_path / "a" / "g5_conditions.json").read_bytes() == (
        tmp_path / "b" / "g5_conditions.json"
    ).read_bytes()
    gate = json.loads((tmp_path / "a" / "G5.json").read_text())
    assert gate["gate_id"] == "G5" and gate["status"] == "PARTIAL"


def test_g5_imports_nothing_that_reads_blocks() -> None:
    tree = ast.parse(Path(g5.__file__).read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
    for forbidden in ("evaluate", "probes", "dataset", "shards", "torch"):
        assert not any(forbidden in n for n in names), names
