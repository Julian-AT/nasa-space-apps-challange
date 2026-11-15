"""G5 (PRD 11.7): the six promotion conditions, computed from an evaluation metrics file.

This module reads only the metrics records and thresholds. It imports nothing that can read
embeddings or test blocks (no evaluate, probes or dataset), so the verdict step cannot itself
read the test blocks. Thresholds come only from ``config/run.yaml`` ``ml.g5`` (G5-02 defaults for
the open readings):

1. pooled lower-95 R2 of log nu relief at each of the five lags >= ``phys_r2_min``;
2. morphology gain >= ``morph_gain_min`` with lower bound above 0, Moon or Mars (Unavailable here
   because no crater labels exist, so ``passed`` is null);
3. body-probe balanced accuracy (point) <= ``body_acc_max``; the lambda_0 value is reported beside;
4. physics-agreement Spearman rho (point) >= ``agreement_rho_min``;
5. seed-stability Jaccard@20 mean (point) >= ``seed_jaccard20_min``;
6. the paired interval of encoder minus ImageNet ResNet-18 and DINO excludes 0 in the encoder's
   favour at each relief lag and on the morphology gain (Unavailable here, ``passed`` null).

Status: FAIL if any condition is false; PASS only if all six are true and an ONNX parity record
for the evaluated model passes; otherwise PARTIAL naming what is missing; NOT_RUN for mock metrics
or without test-block results. A missing input is never a pass.

ONNX parity record format consumed here (to be matched by the exporter): a JSON object with
``mock`` false, ``model_source`` not ``random-init``, ``source_checkpoint_sha256`` equal to one of
the frozen checkpoints and, for each of ``python`` and ``wasm``: ``model_sha256``,
``n_windows >= 256``, ``fp32_max_abs_diff <= 1e-4`` and ``fp16_cosine_min >= 0.999``.
"""

from __future__ import annotations

import json
import os
import shlex
import numbers
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from terraluna_pipeline.config import load_run_config
from terraluna_pipeline.provenance import config_version
from terraluna_pipeline.schema import (
    G5Component,
    G5ConditionRecord,
    G5Summary,
    GateRecord,
    GateStatus,
    InputHash,
    MetricRecord,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
LAGS_M: Final = (270, 360, 540, 720, 1080)
THRESHOLD_KEYS: Final = (
    "phys_r2_min",
    "morph_gain_min",
    "body_acc_max",
    "agreement_rho_min",
    "seed_jaccard20_min",
)
BASELINES_6: Final = ("imagenet_resnet18", "dino_vits8")
PARITY_MAX_ABS: Final = 1e-4
PARITY_MIN_COSINE: Final = 0.999
PARITY_MIN_WINDOWS: Final = 256

CONSEQUENCE: Final = (
    "The encoder stays EXPERIMENTAL with the failed tests named; its report and live probe "
    "still ship and rankings stay on M0 (PRD 11.7)."
)


class G5ThresholdError(ValueError):
    """A missing, null or non-numeric ml.g5 threshold."""


@dataclass(frozen=True)
class G5Thresholds:
    phys_r2_min: float
    morph_gain_min: float
    body_acc_max: float
    agreement_rho_min: float
    seed_jaccard20_min: float

    def as_dict(self) -> dict[str, float]:
        return {k: getattr(self, k) for k in THRESHOLD_KEYS}


def thresholds_from_mapping(values: Mapping[str, object]) -> G5Thresholds:
    out: dict[str, float] = {}
    for key in THRESHOLD_KEYS:
        v = values.get(key)
        if isinstance(v, bool) or not isinstance(v, numbers.Real):
            raise G5ThresholdError(f"ml.g5.{key} is missing, null or not a number: {v!r}")
        out[key] = float(v)
    return G5Thresholds(**out)


def load_thresholds(config_path: Path) -> G5Thresholds:
    """Read ml.g5 from the run config; a missing key raises naming it."""
    config = load_run_config(config_path)
    values: dict[str, object] = {}
    for key in THRESHOLD_KEYS:
        try:
            values[key] = config.value(f"ml.g5.{key}")
        except Exception as error:
            raise G5ThresholdError(f"ml.g5.{key} cannot be read from {config_path}") from error
    return thresholds_from_mapping(values)


# --- helpers -------------------------------------------------------------------------------------


def _find(
    tests: Sequence[MetricRecord],
    test_id: str,
    *,
    target: str | None = None,
    body: str | None = None,
) -> MetricRecord | None:
    for t in tests:
        if (
            t.test_id == test_id
            and (target is None or t.target == target)
            and (body is None or t.body == body)
        ):
            return t
    return None


def _all(flags: Sequence[bool | None]) -> bool | None:
    if any(f is False for f in flags):
        return False
    if flags and all(f is True for f in flags):
        return True
    return None


def _component(name: str, rec: MetricRecord | None, why: str, test: Any) -> G5Component:
    if rec is None or rec.value is None:
        return G5Component(
            component=name,
            observed=None,
            ci_low=None,
            ci_high=None,
            passed=None,
            reason=(rec.unavailable_reason if rec is not None else None) or why,
        )
    return G5Component(
        component=name,
        observed=rec.value,
        ci_low=rec.ci_low,
        ci_high=rec.ci_high,
        passed=bool(test(rec)),
        reason=None,
    )


def _point_condition(
    n: int,
    test_id: str,
    rec: MetricRecord | None,
    threshold: float,
    ok: Any,
) -> G5ConditionRecord:
    if rec is None or rec.value is None:
        return G5ConditionRecord(
            condition=n,
            test_id=test_id,
            statistic="point",
            threshold=threshold,
            observed=None,
            passed=None,
            reason=(rec.unavailable_reason if rec else None) or f"no {test_id} result",
            components=[],
        )
    return G5ConditionRecord(
        condition=n,
        test_id=test_id,
        statistic="point",
        threshold=threshold,
        observed=rec.value,
        passed=bool(ok(rec.value)),
        reason=None,
        components=[],
    )


def compute_g5(tests: Sequence[MetricRecord], thresholds: G5Thresholds) -> list[G5ConditionRecord]:
    """The six conditions as evaluated; thresholds are applied here and nowhere else."""
    # 1. pooled lower-95 R2 of log nu relief at every lag
    comps1 = []
    for lag in LAGS_M:
        rec = _find(tests, "physics_probe", target=f"log_nu_relief_{lag}", body="pooled")
        comps1.append(
            _component(
                f"relief_lag_{lag}",
                rec,
                "no pooled physics probe result",
                lambda r: r.ci_low is not None and r.ci_low >= thresholds.phys_r2_min,
            )
        )
    lows = [c.ci_low for c in comps1]
    cond1 = G5ConditionRecord(
        condition=1,
        test_id="physics_probe",
        statistic="lower_95",
        threshold=thresholds.phys_r2_min,
        observed=min(x for x in lows if x is not None)
        if all(x is not None for x in lows)
        else None,
        passed=_all([c.passed for c in comps1]),
        reason=None
        if _all([c.passed for c in comps1]) is not None
        else "a relief lag has no result",
        components=comps1,
    )

    # 2. morphology gain, Moon or Mars
    morph_test = lambda r: (  # noqa: E731
        r.value is not None
        and r.value >= thresholds.morph_gain_min
        and r.ci_low is not None
        and r.ci_low > 0
    )
    present = [b for b in ("moon", "mars") if _find(tests, "morphology_probe", body=b) is not None]
    comps2 = [
        _component(f"{b}:morph_gain", _find(tests, "morphology_probe", body=b), "", morph_test)
        for b in (present or ["moon", "mars"])
    ]
    any2: bool | None
    if any(c.passed is True for c in comps2):
        any2 = True
    elif all(c.passed is False for c in comps2):
        any2 = False
    else:
        any2 = None
    cond2 = G5ConditionRecord(
        condition=2,
        test_id="morphology_probe",
        statistic="lower_95",
        threshold=thresholds.morph_gain_min,
        observed=None,
        passed=any2,
        reason=None if any2 is not None else "; ".join(c.reason or "" for c in comps2).strip("; "),
        components=comps2,
    )

    # 3. body probe balanced accuracy; lambda_0 reported beside, not tested
    body = _find(tests, "body_invariance", body="pooled")
    cond3 = _point_condition(
        3, "body_invariance", body, thresholds.body_acc_max, lambda v: v <= thresholds.body_acc_max
    )
    if body is not None:
        lam0 = next((b for b in body.baselines if b.name == "lambda_0"), None)
        if lam0 is not None and lam0.value is not None:
            cond3 = cond3.model_copy(
                update={
                    "components": [
                        G5Component(
                            component="lambda_0:reported_not_tested",
                            observed=lam0.value,
                            ci_low=lam0.ci_low,
                            ci_high=lam0.ci_high,
                            passed=None,
                            reason="reported beside the main run, not a pass condition",
                        )
                    ]
                }
            )

    cond4 = _point_condition(
        4,
        "physics_agreement",
        _find(tests, "physics_agreement", target="rms_log_nu_10"),
        thresholds.agreement_rho_min,
        lambda v: v >= thresholds.agreement_rho_min,
    )
    cond5 = _point_condition(
        5,
        "seed_stability",
        _find(tests, "seed_stability"),
        thresholds.seed_jaccard20_min,
        lambda v: v >= thresholds.seed_jaccard20_min,
    )

    # 6. paired interval vs each frozen baseline on each relief lag and on morphology gain
    comps6: list[G5Component] = []
    sources: list[tuple[str, MetricRecord | None]] = [
        (
            f"relief_lag_{lag}",
            _find(tests, "physics_probe", target=f"log_nu_relief_{lag}", body="pooled"),
        )
        for lag in LAGS_M
    ]
    sources += [
        (f"{b}:morph_gain", _find(tests, "morphology_probe", body=b)) for b in ("moon", "mars")
    ]
    for base in BASELINES_6:
        for name, rec in sources:
            entry = next((b for b in rec.baselines if b.name == base), None) if rec else None
            label = f"{base}:{name}"
            if entry is None or entry.paired_diff_ci_low is None:
                comps6.append(
                    G5Component(
                        component=label,
                        observed=None,
                        ci_low=None,
                        ci_high=None,
                        passed=None,
                        reason=(entry.unavailable_reason if entry else None)
                        or "no paired interval against this baseline",
                    )
                )
            else:
                comps6.append(
                    G5Component(
                        component=label,
                        observed=None,
                        ci_low=entry.paired_diff_ci_low,
                        ci_high=entry.paired_diff_ci_high,
                        passed=entry.paired_diff_ci_low > 0,
                        reason=None,
                    )
                )
    # Morphology components of the two bodies are alternatives (Moon or Mars): a baseline is
    # beaten on morphology if either body is, so judge conjunction over lags + morphology-either.
    flags6: list[bool | None] = []
    for base in BASELINES_6:
        mine = [c for c in comps6 if c.component.startswith(f"{base}:")]
        lag_flags = [c.passed for c in mine if "relief_lag" in c.component]
        morph = [c.passed for c in mine if "morph_gain" in c.component]
        morph_flag: bool | None = (
            True
            if any(m is True for m in morph)
            else False
            if all(m is False for m in morph)
            else None
        )
        flags6.extend([*lag_flags, morph_flag])
    passed6 = _all(flags6)
    cond6 = G5ConditionRecord(
        condition=6,
        test_id="physics_probe",
        statistic="paired_interval",
        threshold=None,
        observed=None,
        passed=passed6,
        reason=None
        if passed6 is not None
        else "paired intervals against ImageNet ResNet-18 and DINO baselines are Unavailable",
        components=comps6,
    )
    return [cond1, cond2, cond3, cond4, cond5, cond6]


# --- parity and the gate -------------------------------------------------------------------------


def check_parity(
    parity: Mapping[str, Any] | None, checkpoint_sha256s: Sequence[str]
) -> tuple[bool, str]:
    """Whether an ONNX parity record counts for the evaluated model, and why not."""
    if parity is None:
        return False, "ONNX parity for the evaluated model missing"
    if parity.get("mock") is not False:
        return False, "ONNX parity record is mock or lacks mock=false"
    if parity.get("model_source") in (None, "random-init"):
        return False, "ONNX parity record is for a random-init or unknown model"
    if parity.get("source_checkpoint_sha256") not in set(checkpoint_sha256s):
        return False, "ONNX parity record is not for a frozen checkpoint"
    for side in ("python", "wasm"):
        rec = parity.get(side)
        if not isinstance(rec, Mapping):
            return False, f"ONNX parity record has no {side} result"
        diff, cos, n = (
            rec.get("fp32_max_abs_diff"),
            rec.get("fp16_cosine_min"),
            rec.get("n_windows"),
        )
        if not (isinstance(diff, numbers.Real) and isinstance(cos, numbers.Real)):
            return False, f"ONNX parity {side} result lacks its numbers"
        if not isinstance(n, int) or n < PARITY_MIN_WINDOWS or not rec.get("model_sha256"):
            return False, f"ONNX parity {side} result lacks 256 windows or the model hash"
        if float(diff) > PARITY_MAX_ABS or float(cos) < PARITY_MIN_COSINE:
            return False, f"ONNX parity {side} result is outside tolerance"
    return True, "ONNX parity passes"


def failed_tests(conditions: Sequence[G5ConditionRecord]) -> list[str]:
    out = []
    for c in conditions:
        if c.passed is False:
            out.append(f"condition_{c.condition}:{c.test_id}")
        elif c.passed is None:
            out.append(f"condition_{c.condition}:{c.test_id} (unavailable)")
    return out


def summarise(
    metrics: Mapping[str, Any],
    thresholds: G5Thresholds,
    parity: Mapping[str, Any] | None = None,
    checkpoint_sha256s: Sequence[str] = (),
) -> tuple[G5Summary, str]:
    """The G5 summary (status, conditions, failed tests) and a one-line result."""
    frozen = metrics.get("ml_g5_thresholds_at_freeze")
    if frozen is not None and thresholds_from_mapping(frozen) != thresholds:
        raise G5ThresholdError("ml.g5 thresholds differ from the thresholds recorded at the freeze")
    if metrics.get("mock") is True:
        return G5Summary(status="NOT_RUN", conditions=[], failed_tests=[]), (
            "mock metrics; G5 not evaluated"
        )
    tests = [MetricRecord.model_validate(t) for t in metrics.get("tests", [])]
    conditions = compute_g5(tests, thresholds)
    failed = failed_tests(conditions)
    if metrics.get("evaluated_split") != "test" or not tests:
        return G5Summary(status="NOT_RUN", conditions=conditions, failed_tests=failed), (
            "validation-split dry run; G5 is evaluated on test blocks only"
        )
    ok, note = check_parity(parity, checkpoint_sha256s)
    status: GateStatus
    flags = [c.passed for c in conditions]
    if any(f is False for f in flags):
        status = "FAIL"
        result = "failed: " + ", ".join(f for f in failed if "(unavailable)" not in f)
    elif all(f is True for f in flags) and ok:
        status, result = "PASS", "all six conditions hold and ONNX parity passes"
    else:
        status = "PARTIAL"
        missing = [f for f in failed if "(unavailable)" in f]
        if all(f is True for f in flags):
            missing = []
        if not ok:
            missing.append(note)
        result = "no condition failed; missing: " + "; ".join(missing)
    return G5Summary(status=status, conditions=conditions, failed_tests=failed), result


def to_gate_record(
    summary: G5Summary,
    result: str,
    *,
    evidence_path: str | None,
    run_id: str | None = None,
    command: str | None = None,
    config_version: str | None = None,
    input_hashes: Sequence[InputHash] = (),
) -> GateRecord:
    return GateRecord(
        gate_id="G5",
        status=summary.status,
        evidence_path=evidence_path,
        run_id=run_id,
        scope="encoder on spatial test blocks",
        result=result,
        consequence=None if summary.status == "PASS" else CONSEQUENCE,
        command=command,
        config_version=config_version,
        input_hashes=list(input_hashes),
    )


def _repo_relative(path: Path) -> str | None:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return None


def canonical_json(document: object) -> str:
    return (
        json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )


def write_g5(
    metrics: Mapping[str, Any],
    thresholds: G5Thresholds,
    out_dir: Path,
    gate_path: Path,
    *,
    parity: Mapping[str, Any] | None = None,
    checkpoint_sha256s: Sequence[str] = (),
    run_id: str | None = None,
    command: str | None = None,
    config_path: Path = REPO_ROOT / "config" / "run.yaml",
    input_paths: Sequence[Path] = (),
) -> GateRecord:
    """Write ``g5_conditions.json`` (a G5Summary) and the GateRecord JSON.

    The record names its ``command``, the SHA-256 of ``config_path`` (the thresholds' source) and
    the hashes of that config plus every ``input_paths`` file inside the repository (PRD 8.12;
    paths are repo-relative, so a file outside the repository cannot be listed). ``evidence_path``
    is repo-relative, or, for a scratch output outside the repository, relative to the gate file.
    """
    summary, result = summarise(metrics, thresholds, parity, checkpoint_sha256s)
    out_dir.mkdir(parents=True, exist_ok=True)
    conditions_path = out_dir / "g5_conditions.json"
    conditions_path.write_text(canonical_json(summary.model_dump(mode="json")), encoding="utf-8")
    inputs: dict[str, Path] = {}
    for path in (config_path, *input_paths):
        relative = _repo_relative(path)
        if relative is not None:
            inputs[relative] = path
    gate = to_gate_record(
        summary,
        result,
        evidence_path=_repo_relative(conditions_path)
        or os.path.relpath(conditions_path, gate_path.parent).replace(os.sep, "/"),
        run_id=run_id,
        command=command or shlex.join(["python", "-m", "terraluna_ml.evaluate", "g5"]),
        config_version=config_version(config_path),
        input_hashes=[
            InputHash(path=name, sha256=config_version(inputs[name])) for name in sorted(inputs)
        ],
    )
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text(canonical_json(gate.model_dump(mode="json")), encoding="utf-8")
    return gate
