"""T6-07: Appendix A analyses I (block bootstrap, breakover test, percentiles, records).

Synthetic surfaces and fields only.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from terraluna_pipeline import checks


def _correlated_field(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    white = rng.standard_normal((n, n))
    k = np.fft.fftfreq(n)
    kk = np.hypot(*np.meshgrid(k, k))
    field = np.real(np.fft.ifft2(np.fft.fft2(white) * np.exp(-((kk / 0.04) ** 2))))
    return np.asarray(field / field.std(), dtype=np.float64)


def test_block_bootstrap_wider_than_independent_on_correlated_field() -> None:
    field = _correlated_field(40, 3)
    block = checks.block_bootstrap_median(field, block=8, replicates=400, seed=1)
    iid = checks.block_bootstrap_median(field, block=1, replicates=400, seed=1)
    assert block["block"] == 8 and block["replicates"] == 400 and block["seed"] == 1
    assert (block["hi"] - block["lo"]) > 1.5 * (iid["hi"] - iid["lo"])


def test_block_bootstrap_ignores_missing_cells() -> None:
    field = np.full((6, 6), np.nan)
    field[:3, :3] = 2.0
    result = checks.block_bootstrap_median(field, block=2, replicates=50, seed=0)
    assert result["lo"] == 2.0 and result["hi"] == 2.0


def _fingerprint_rows() -> list[dict[str, float | None]]:
    rows: list[dict[str, float | None]] = []
    for i in range(9):
        rows.append(
            {
                "relief.nu_m@270": 10.0 + i,
                "relief.nu_m@1080": None if i == 0 else 30.0 + i,
                "relief.H": None if i < 2 else 0.8 + 0.01 * i,
                "valid_fraction": 1.0,
            }
        )
    return rows


def test_body_distribution_counts_h_unavailable_and_skips_unsupported_lags() -> None:
    positions = [(i // 3, i % 3) for i in range(9)]
    result = checks.body_distribution(
        _fingerprint_rows(), positions, block=2, replicates=50, seed=0
    )
    h = result["relief.H"]
    assert h["n"] == 7 and h["n_unavailable"] == 2
    lag = result["log_relief.nu_m@1080"]
    assert lag["n"] == 8 and lag["n_unavailable"] == 1
    assert lag["median"] == pytest.approx(float(np.median(np.log([30.0 + i for i in range(1, 9)]))))
    assert result["valid_fraction"]["median"] == 1.0


def _two_slope(lags: np.ndarray, break_m: float, h1: float, h2: float) -> np.ndarray:
    nu_break = break_m**h1
    return np.where(lags <= break_m, lags**h1, nu_break * (lags / break_m) ** h2)


LAGS = np.array([45, 90, 135, 180, 270, 360, 540, 720, 1080, 1440, 2160, 2880, 4320], dtype=float)


def test_breakover_detected_near_one_km() -> None:
    nu = _two_slope(LAGS, 1000.0, 0.95, 0.5)
    result = checks.breakover_test(LAGS.tolist(), nu.tolist())
    assert result["status"] == "available"
    assert result["breakover_detected"] is True
    assert 720.0 <= result["break_m"] <= 1440.0
    assert result["criterion"].startswith("BIC")


def test_single_slope_has_no_breakover() -> None:
    nu = 3.0 * LAGS**0.8
    result = checks.breakover_test(LAGS.tolist(), nu.tolist())
    assert result["breakover_detected"] is False


def test_five_km_window_lags_are_insufficient_beyond_break() -> None:
    lags = [270.0, 360.0, 540.0, 720.0, 1080.0]
    nu = [lag**0.9 for lag in lags]
    result = checks.breakover_test(lags, nu)
    assert result["status"] == "unavailable"
    assert result["reason"] == "insufficient_lags_beyond_break"


def test_rosenburg_side_by_side_has_no_verdict() -> None:
    result = checks.rosenburg_side_by_side(0.9)
    keys = json.dumps(result).lower()
    assert "0.95" in keys and "0.76" in keys
    assert "agree" not in keys and "pass" not in keys and "fail" not in keys
    assert "baseline" in result["note"]


def test_target_percentile_and_clone_spread() -> None:
    distribution = [1.0, 2.0, 3.0, 4.0]
    result = checks.target_percentile(2.5, distribution, clone_values=[2.0, 3.5])
    assert result["percentile"] == 50.0
    assert result["clone_spread"] == {"min": 50.0, "median": 62.5, "max": 75.0, "n": 2}
    missing = checks.target_percentile(None, distribution, clone_values=[])
    assert missing["status"] == "unavailable"


def test_verify_records_detects_mismatch_and_mock(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    source = tmp_path / "input.json"
    source.write_text("{}\n")
    import hashlib

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    record = {"input_hashes": [{"path": "input.json", "sha256": digest}]}
    (analysis / "a.json").write_text(json.dumps(record))
    assert checks.main(["verify-records", "--root", str(tmp_path)]) == 0
    source.write_text('{"x": 1}\n')
    assert checks.main(["verify-records", "--root", str(tmp_path)]) == 1
    source.write_text("{}\n")
    (analysis / "b.json").write_text(json.dumps({"mock": True, "input_hashes": []}))
    assert checks.main(["verify-records", "--root", str(tmp_path)]) == 1


def test_log_values_are_natural_log() -> None:
    assert checks.LOG_BASE == "natural"
    assert math.log(math.e) == 1.0


def test_clone_percentile_spread_reranks_within_the_same_clone() -> None:
    target = {"0001": 2.0, "0002": 3.0, "0003": None}
    dists = {"0001": [1.0, 2.0, 3.0, 4.0], "0002": [1.0, 2.0, 3.0, 4.0], "0003": [1.0]}
    result = checks.clone_percentile_spread(target, dists)
    assert result is not None
    assert (result["min"], result["max"], result["n"], result["n_skipped"]) == (50.0, 75.0, 2, 1)
    assert checks.clone_percentile_spread({"0001": None}, {"0001": [1.0]}) is None
