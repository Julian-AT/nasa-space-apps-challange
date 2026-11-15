"""T6-08: Appendix A analyses II on synthetic per-body samples."""

from __future__ import annotations

from typing import Any

import pytest

from terraluna_pipeline import checks


def _row(wid: str, group: str, h: float | None, nu: float, vf: float = 1.0) -> dict[str, Any]:
    return {
        "window_id": wid,
        "status": "eligible",
        "reasons": [],
        "group": group,
        "position": (int(wid[-1]), 0),
        "fractions": {"dem_90m": vf},
        "descriptors": {"relief.H": h, "relief.nu_m@270": nu},
    }


def test_identical_bodies_overlap_one_and_disjoint_zero() -> None:
    a = [_row(f"a{i}", "g", 0.5 + 0.01 * i, 10.0 + i) for i in range(5)]
    b = [_row(f"b{i}", "g", 0.5 + 0.01 * i, 10.0 + i) for i in range(5)]
    c = [_row(f"c{i}", "g", 5.0 + i, 1000.0 + i) for i in range(5)]
    result = checks.cross_body_overlap({"a": a, "b": b, "c": c})
    assert result["a|b"]["relief.H"]["overlap_coefficient"] == pytest.approx(1.0)
    assert result["a|c"]["log_relief.nu_m@270"]["overlap_coefficient"] == 0.0


def test_qa_threshold_removing_all_windows_is_unavailable() -> None:
    rows = [_row("w1", "dtm1", 0.5, 10.0), _row("w2", "dtm2", 0.6, 11.0)]
    scan = checks.qa_threshold_scan(rows, {"dtm1": 7.8, "dtm2": 9.2}, (5.0, 8.0, 10.0))
    assert (
        scan[0]["status"] == "unavailable" and scan[0]["reason"] == "threshold_removes_all_windows"
    )
    assert (scan[1]["n_windows"], scan[1]["n_lost"]) == (1, 1)
    assert scan[2]["n_windows"] == 2


def test_unknown_qa_drops_window_rather_than_keeping_it() -> None:
    scan = checks.qa_threshold_scan([_row("w1", "dtm1", 0.5, 10.0)], {"dtm1": None}, (10.0,))
    assert scan[0]["status"] == "unavailable"


def test_missing_h_is_not_imputed() -> None:
    rows = [_row("w1", "g", None, 10.0), _row("w2", "g", 0.7, 12.0)]
    dist = checks.rows_distribution(rows)
    assert dist["descriptors"]["relief.H"]["n"] == 1
    assert dist["descriptors"]["relief.H"]["n_unavailable"] == 1


def test_window_rows_parse_float_and_int_lag_keys() -> None:
    variant = {"nu_m": {"270.0": 5.0, "1080": None}, "hurst": None}
    entry = {
        "window": {"window_id": "w", "product_ids": ["CTX/c/dtm1/x.tif"], "environment_flags": {}},
        "status": "eligible",
        "reasons": [],
        "grid_index": [1, 2],
        "terrain": {"terrain": {"relief": variant, "texture": variant, "valid_fraction": 0.9}},
    }
    row = checks.window_rows([entry])[0]
    assert row["group"] == "dtm1"
    assert row["descriptors"]["relief.nu_m@270"] == 5.0
    assert row["descriptors"]["relief.nu_m@1080"] is None
