"""G3 lunar context checks (T6-06): shadow behaviour and impact-melt context, synthetic only."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest

from terraluna_pipeline import lunar_context as ctx


def _win(
    wid: str,
    cpr: list[float],
    s1: list[float],
    shadow: float | None,
    n_pixels: int | None = None,
) -> dict[str, Any]:
    record = ctx.window_stats(
        wid,
        np.array(cpr, dtype=np.float64),
        np.array(s1, dtype=np.float64),
        n_pixels=n_pixels if n_pixels is not None else len(cpr),
    )
    record["shadow_fraction"] = shadow
    return record


def test_window_stats_counts_valid_fraction_and_medians() -> None:
    w = ctx.window_stats("w", np.array([0.2, 0.4, 0.6, 0.8]), np.array([1.0, 2.0, 3.0]), n_pixels=8)
    assert w["n_pixels"] == 8
    assert w["n_valid_cpr"] == 4
    assert w["valid_fraction"] == pytest.approx(0.5)
    assert w["cpr_median"] == pytest.approx(0.5)
    assert w["s1_median"] == pytest.approx(2.0)


def test_window_with_no_valid_pixel_has_null_not_zero() -> None:
    w = ctx.window_stats("w", np.array([]), np.array([]), n_pixels=10)
    assert w["cpr_median"] is None
    assert w["s1_median"] is None
    assert w["valid_fraction"] == 0.0


def test_split_uses_recorded_fraction_and_keeps_unknown_apart() -> None:
    windows = [
        _win("a", [0.5], [1.0], 0.9),
        _win("b", [0.5], [1.0], 0.1),
        _win("c", [0.5], [1.0], None),
        _win("d", [0.5], [1.0], 0.5),
    ]
    groups = ctx.split_by_shadow(windows, split_fraction=0.5)
    assert [w["window_id"] for w in groups["shadowed"]] == ["a", "d"]
    assert [w["window_id"] for w in groups["sunlit"]] == ["b"]
    assert [w["window_id"] for w in groups["unknown"]] == ["c"]


def test_split_threshold_must_be_given_and_in_unit_interval() -> None:
    with pytest.raises(ValueError, match="split_fraction"):
        ctx.split_by_shadow([], split_fraction=1.5)


def test_shadow_comparison_reports_group_statistics_qualitatively() -> None:
    windows = [
        _win("s1", [0.4, 0.5, 0.6], [4.0, 5.0, 6.0], 0.9),
        _win("s2", [0.45, 0.5, 0.55], [5.0, 6.0, 7.0], 0.8),
        _win("u1", [0.4, 0.5, 0.6], [1.0, 2.0, 3.0], 0.0),
        _win("u2", [0.45, 0.5, 0.55], [2.0, 3.0, 4.0], 0.1),
        _win("x", [0.5], [1.0], None),
    ]
    out = ctx.shadow_comparison(
        windows,
        split_fraction=0.5,
        justification="engineering choice for the test",
        shadow_source="synthetic",
    )
    assert out["status"] == "computed"
    assert out["groups"]["shadowed"]["n_windows"] == 2
    assert out["groups"]["sunlit"]["n_windows"] == 2
    assert out["groups"]["unknown"]["n_windows"] == 1
    for name in ("cpr_median", "s1_median", "valid_fraction"):
        stats = out["groups"]["shadowed"][name]
        assert set(stats) >= {"median", "iqr", "q25", "q75", "n"}
    assert (
        out["groups"]["shadowed"]["s1_median"]["median"]
        > out["groups"]["sunlit"]["s1_median"]["median"]
    )
    assert "higher" in out["statement"] and "S1" in out["statement"]
    assert "topographic" in out["correction_difference"]
    flat = json.dumps(out).lower()
    for banned in ("pass", "fail", "p_value", "p-value", "equivalen"):
        assert banned not in flat.replace("passes", "")
    assert out["split_fraction"] == 0.5
    assert out["split_justification"] == "engineering choice for the test"


def test_shadow_comparison_without_fractions_is_unavailable_naming_the_decision() -> None:
    windows = [_win("a", [0.5], [1.0], None), _win("b", [0.6], [2.0], None)]
    out = ctx.shadow_comparison(windows, split_fraction=0.5, justification="x", shadow_source=None)
    assert out["status"] == "Unavailable"
    assert "G0-10" in out["reason"]
    assert "groups" not in out


def test_shadow_comparison_needs_both_groups() -> None:
    windows = [_win("a", [0.5], [1.0], 0.9), _win("b", [0.6], [2.0], None)]
    out = ctx.shadow_comparison(windows, split_fraction=0.5, justification="x", shadow_source="s")
    assert out["status"] == "Unavailable"
    assert "sunlit" in out["reason"]


def test_shadow_comparison_without_windows_names_the_artifact() -> None:
    out = ctx.shadow_comparison([], split_fraction=0.5, justification="x", shadow_source=None)
    assert out["status"] == "Unavailable"
    assert "windows" in out["reason"]


def test_impact_melt_context_counts_fractions_and_keeps_cpr_above_one() -> None:
    windows = [
        ctx.window_stats(
            "a", np.array([0.5, 0.7, 0.9, 1.0, 1.2, 1.5]), np.array([1.0]), n_pixels=8
        ),
        ctx.window_stats("b", np.array([0.1, 0.2]), np.array([1.0]), n_pixels=2),
    ]
    assert windows[0]["n_cpr_in_context_range"] == 3  # 0.7, 0.9, 1.0 (closed range)
    assert windows[0]["n_cpr_above_1"] == 2  # kept valid, not truncated
    out = ctx.impact_melt_context(windows)
    assert out["status"] == "computed"
    assert out["label"] == "Context, Derived"
    assert out["range"] == [0.7, 1.0]
    assert out["source"].startswith("Carter et al. 2012")
    row = {r["window_id"]: r for r in out["windows"]}
    assert row["a"]["fraction_of_valid_in_range"] == pytest.approx(0.5)
    assert row["a"]["n_valid"] == 6
    assert row["a"]["n_above_1"] == 2
    assert row["b"]["fraction_of_valid_in_range"] == 0.0
    assert out["pooled"]["n_valid"] == 8
    assert out["pooled"]["fraction_of_valid_in_range"] == pytest.approx(3 / 8)
    flat = json.dumps(out).lower()
    for banned in ('"pass', '"fail', "threshold", '"verdict'):
        assert banned not in flat


def test_impact_melt_context_unavailable_without_windows() -> None:
    out = ctx.impact_melt_context([])
    assert out["status"] == "Unavailable"
    assert "windows" in out["reason"]


def test_impact_melt_window_without_valid_cpr_has_null_fraction() -> None:
    w = ctx.window_stats("z", np.array([]), np.array([]), n_pixels=5)
    out = ctx.impact_melt_context([w])
    assert out["windows"][0]["fraction_of_valid_in_range"] is None


def test_g3_status_does_not_depend_on_the_impact_melt_count() -> None:
    low = ctx.impact_melt_context([ctx.window_stats("a", np.array([0.1]), np.array([1.0]), 1)])
    high = ctx.impact_melt_context([ctx.window_stats("a", np.array([0.8]), np.array([1.0]), 1)])
    assert low["pooled"] != high["pooled"]
    a = ctx.combined_g3_status("consistent", shadow_recorded=False, impact_melt_recorded=True)
    assert a == "PARTIAL"
    assert ctx.combined_g3_status("consistent", True, True) == "PASS"
    assert ctx.combined_g3_status("consistent", True, False) == "PARTIAL"
    assert ctx.combined_g3_status("discrepant", True, True) == "PARTIAL"
    assert ctx.combined_g3_status("suspected_processing_error", True, True) == "FAIL"
    assert ctx.combined_g3_status(None, True, True) == "NOT_RUN"


def test_fassett_layers_are_refused_for_distances_and_labelled_context_otherwise() -> None:
    for layer in ("delta_cpr", "delta_s1"):
        refused = ctx.fassett_layer_use(layer, purpose="distance")
        assert refused["status"] == "Unavailable"
        assert "D13" in refused["reason"]
        allowed = ctx.fassett_layer_use(layer, purpose="context")
        assert allowed["status"] == "context_only"
        assert allowed["label"] == "lunar context"
    with pytest.raises(ValueError, match="layer"):
        ctx.fassett_layer_use("cpr", purpose="context")
    with pytest.raises(ValueError, match="purpose"):
        ctx.fassett_layer_use("delta_cpr", purpose="ranking")


def test_window_pixels_use_pixel_centres_of_the_declared_mosaic_grid() -> None:
    grid = ctx.MosaicGrid(scale_m=100.0, offset_sample=51.5, offset_line=51.5)
    # x = (sample - 51.5) * 100 for 1-based sample; centre of 0-based col j is sample j+1.
    rows, cols = ctx.window_pixels(grid, x0=-300.0, y0=-300.0, size_m=500.0)
    # x_j = (j + 1 - 51.5) * 100 = (j - 50.5) * 100: centres in [-300, 200) are j = 48..52
    assert (cols.start, cols.stop) == (48, 53)
    # y_i = (51.5 - (i + 1)) * 100 = (50.5 - i) * 100: centres in [-300, 200) are i = 49..53
    assert (rows.start, rows.stop) == (49, 54)


def test_run_is_blocked_without_network_flag_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ctx, "_read_mosaic_windows", _raise_unreadable)
    assert ctx.main(["run"]) == 3
    assert "blocked: " in capsys.readouterr().out


def _raise_unreadable(*_: object, **__: object) -> Any:
    raise ctx.MosaicUnreadable("mosaic not readable in the test")


def test_subcommand_is_required() -> None:
    with pytest.raises(SystemExit) as info:
        ctx.main([])
    assert info.value.code == 2
