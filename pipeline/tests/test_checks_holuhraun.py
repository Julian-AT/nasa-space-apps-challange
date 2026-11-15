"""G3 Holuhraun reproduction (T6-05): numerical and behaviour tests on synthetic data only.

Nothing here reads the real scene; the real run is ``python -m terraluna_pipeline.holuhraun run``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from rasterio.transform import from_origin  # type: ignore[import-untyped]
from scipy.stats import norm  # type: ignore[import-untyped]

from terraluna_pipeline import holuhraun as hol
from terraluna_pipeline.polarimetry import (
    COVARIANCE_HH_HV_VV,
    NEISH_SC_LL_CONVENTION,
    ObsState,
    circular_from_covariance,
)

FloatArray = npt.NDArray[np.float64]


def _cov(k: tuple[complex, complex, complex]) -> npt.NDArray[np.complex128]:
    vec = np.array(k, dtype=complex)
    return np.outer(vec, vec.conj())


# --- overlap coefficient ---------------------------------------------------------------------


def test_overlap_of_equal_variance_gaussians_is_analytic() -> None:
    rng = np.random.default_rng(20260515)
    a = rng.normal(0.0, 1.0, 1_000_000)
    b = rng.normal(2.0, 1.0, 1_000_000)
    edges = np.linspace(-8.0, 10.0, 721)
    analytic = 2.0 * norm.cdf(-1.0)
    assert hol.overlap_coefficient(a, b, edges=edges) == pytest.approx(analytic, abs=0.01)


def test_overlap_is_one_for_identical_and_zero_for_disjoint() -> None:
    x = np.linspace(0.1, 0.9, 1000)
    edges = np.linspace(0.0, 2.0, 101)
    assert hol.overlap_coefficient(x, x, edges=edges) == pytest.approx(1.0, abs=1e-12)
    assert hol.overlap_coefficient(x, x + 1.0, edges=edges) == 0.0


def test_overlap_keeps_values_beyond_the_range_in_overflow_bins() -> None:
    """Nothing is clipped: values above the last edge share one overflow bin."""
    edges = np.linspace(0.0, 2.0, 101)
    a = np.array([5.0, 7.0, 9.0])
    b = np.array([3.0, 4.0, 6.0])
    assert hol.overlap_coefficient(a, b, edges=edges) == pytest.approx(1.0)
    c = np.array([0.5, 0.5, 0.5])
    assert hol.overlap_coefficient(a, c, edges=edges) == 0.0


def test_overlap_refuses_empty_or_nonfinite_input() -> None:
    edges = np.linspace(0.0, 2.0, 101)
    with pytest.raises(ValueError, match="empty"):
        hol.overlap_coefficient(np.array([]), np.array([1.0]), edges=edges)
    with pytest.raises(ValueError, match="finite"):
        hol.overlap_coefficient(np.array([np.nan]), np.array([1.0]), edges=edges)


# --- aggregation -----------------------------------------------------------------------------


def test_ratio_of_means_and_mean_of_ratios_differ_and_both_are_reported() -> None:
    # Two pixel classes: a bright, smooth one (low CPR) and a faint, rough one (high CPR).
    sc = np.array([1.0] * 90 + [10.0] * 10)
    oc = np.array([10.0] * 90 + [4.0] * 10)
    result = hol.aggregate_powers(sc, oc)
    assert result["n"] == 100
    assert result["ratio_of_mean_powers"] == pytest.approx(sc.mean() / oc.mean())
    assert result["mean_pixelwise_cpr"] == pytest.approx((sc / oc).mean())
    assert abs(result["ratio_of_mean_powers"] - result["mean_pixelwise_cpr"]) > 0.05
    assert set(result) >= {"ratio_of_mean_powers", "mean_pixelwise_cpr", "n", "label"}


def test_aggregate_refuses_nonpositive_oc() -> None:
    with pytest.raises(ValueError, match="OC"):
        hol.aggregate_powers(np.array([1.0, 1.0]), np.array([1.0, 0.0]))


def test_distribution_summary_fields() -> None:
    values = np.arange(1.0, 101.0)
    s = hol.summarize_distribution(values)
    assert s["n"] == 100
    assert s["median"] == pytest.approx(50.5)
    assert s["iqr"] == pytest.approx(49.5)
    assert s["mean"] == pytest.approx(50.5)
    assert s["percentiles"]["50"] == pytest.approx(50.5)
    assert s["fraction_above_1"] == pytest.approx(0.99)


# --- CPR through the T2 conversion, with counted exclusions ---------------------------------


def test_nonpositive_oc_is_excluded_with_a_counted_reason_and_high_cpr_stays_valid() -> None:
    covs = np.stack(
        [
            _cov((1, 0, 1)),  # CPR 0
            _cov((1, 1, 1)),  # CPR 1
            _cov((1, 0, -1)),  # OC = 0 exactly
            _cov((1, 0, -0.9)),  # CPR 361, valid
        ]
    )[None]  # shape (1, 4, 3, 3)
    labels = np.ones((1, 4), dtype=np.int8)
    incidence = np.full((1, 4), 50.0)
    nodata = np.zeros((1, 4), dtype=bool)
    out = hol.analyse_variant(
        covs, nodata, incidence, labels, ("a",), boxcar=False, overlap_mask=np.zeros((1, 4), bool)
    )["a"]
    assert out["n_in_polygon"] == 4
    assert out["n_valid"] == 3
    assert out["excluded"] == {"OC_NONPOSITIVE": 1}
    assert sorted(out["cpr"].tolist()) == pytest.approx([0.0, 1.0, 361.0], rel=1e-9)
    # The same numbers as the T2 conversion: no epsilon, no clipping.
    direct = circular_from_covariance(
        covs,
        ordering=COVARIANCE_HH_HV_VV,
        convention=NEISH_SC_LL_CONVENTION,
        product_masked=nodata,
        tolerance=hol.CONVERSION_TOLERANCE,
    )
    assert direct.cpr.state[0, 2] == ObsState.INVALID


def test_exclusion_reasons_are_distinct_and_counted() -> None:
    covs = np.stack([_cov((1, 1, 1))] * 6)[None]  # (1, 6, 3, 3)
    labels = np.ones((1, 6), dtype=np.int8)
    nodata = np.array([[False, True, False, False, False, False]])
    incidence = np.array([[50.0, 50.0, np.nan, 70.0, 64.9, 65.0]])
    overlap = np.array([[False, False, False, False, False, True]])
    out = hol.analyse_variant(covs, nodata, incidence, labels, ("a",), False, overlap)["a"]
    assert out["n_in_polygon"] == 6
    assert out["excluded"] == {
        "FACIES_OVERLAP": 1,
        "PRODUCT_NODATA": 1,
        "INCIDENCE_NODATA": 1,
        "INCIDENCE_ABOVE_MAX": 1,
    }
    assert out["n_valid"] == 2  # 50.0 and 64.9; the limit itself (65.0) is inside the band


def test_boxcar_variant_requires_a_complete_window_and_filters_the_covariance() -> None:
    rows, cols = 5, 5
    cov = np.zeros((rows, cols, 3, 3), dtype=complex)
    cov[:] = _cov((1, 1, 1))
    cov[2, 2] = _cov((3, 3, 3))  # centre: a 3x3 mean of covariances is not the centre one
    nodata = np.zeros((rows, cols), dtype=bool)
    nodata[0, 4] = True
    incidence = np.full((rows, cols), 50.0)
    labels = np.ones((rows, cols), dtype=np.int8)
    out = hol.analyse_variant(
        cov, nodata, incidence, labels, ("a",), True, np.zeros((rows, cols), bool)
    )["a"]
    # Window incomplete: the image border (16 px) and the neighbours of the nodata pixel.
    assert out["excluded"]["FILTER_WINDOW_INCOMPLETE"] >= 16
    assert out["excluded"]["PRODUCT_NODATA"] == 1
    assert out["n_valid"] == 25 - sum(out["excluded"].values())
    # All covariances here are rank 1 with HH=HV=VV, so every surviving CPR is exactly 1.
    assert out["cpr"].size == out["n_valid"]
    assert np.allclose(out["cpr"], 1.0)


def test_boxcar_mean_of_covariances_is_not_a_mean_of_cprs() -> None:
    cov = np.zeros((3, 3, 3, 3), dtype=complex)
    cov[:] = _cov((1, 0, 1))  # CPR 0
    cov[1, 1] = _cov((1, 1, 1)) * 9.0  # CPR 1, nine times the power
    nodata = np.zeros((3, 3), dtype=bool)
    out = hol.analyse_variant(
        cov,
        nodata,
        np.full((3, 3), 50.0),
        np.ones((3, 3), dtype=np.int8),
        ("a",),
        True,
        np.zeros((3, 3), bool),
    )["a"]
    assert out["n_valid"] == 1
    mean_cov = cov.mean(axis=(0, 1))
    expected = circular_from_covariance(
        mean_cov[None],
        ordering=COVARIANCE_HH_HV_VV,
        convention=NEISH_SC_LL_CONVENTION,
        product_masked=np.zeros(1, bool),
        tolerance=hol.CONVERSION_TOLERANCE,
    ).cpr.value[0]
    assert out["cpr"][0] == pytest.approx(expected)
    assert out["cpr"][0] != pytest.approx(1.0 / 9.0)


# --- facies membership -----------------------------------------------------------------------


def _square(x0: float, y0: float, x1: float, y1: float) -> dict[str, object]:
    ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    return {"type": "Polygon", "coordinates": [ring]}


def test_rasterize_marks_pixel_centres_and_flags_overlap() -> None:
    transform = from_origin(0.0, 10.0, 1.0, 1.0)  # 10x10 pixels of size 1, upper-left (0, 10)
    polygons = {
        "a": [_square(0.0, 5.0, 5.0, 10.0)],  # rows 0-4, cols 0-4
        "b": [_square(4.0, 5.0, 8.0, 10.0)],  # rows 0-4, cols 4-7
    }
    labels, overlap = hol.rasterize_facies(polygons, ("a", "b"), transform, (10, 10))
    assert labels.dtype == np.int8
    assert overlap[0, 4] and overlap.sum() == 5  # column 4, rows 0-4
    assert labels[0, 0] == 1 and labels[0, 7] == 2 and labels[9, 9] == 0
    assert (labels[overlap] == 1).all()  # overlap pixels carry the first facies' label


# --- Unavailable and applicability -----------------------------------------------------------


def test_no_polygons_gives_unavailable_and_no_overlap_is_computed() -> None:
    for samples in (None, {}, {"rubbly": np.array([0.4]), "spiny": np.array([])}):
        out = hol.compare_facies(samples)
        assert out["status"] == "Unavailable"
        assert out["reason"] == "facies polygons not available"
        assert "overlaps" not in out


def test_compare_facies_reports_pairwise_overlaps() -> None:
    rng = np.random.default_rng(1)
    samples = {
        "rubbly": rng.normal(0.5, 0.1, 5000),
        "spiny": rng.normal(0.45, 0.1, 5000),
        "undifferentiated": rng.normal(0.5, 0.1, 5000),
    }
    out = hol.compare_facies(samples)
    assert out["status"] == "computed"
    assert set(out["overlaps"]) == {
        "rubbly|spiny",
        "rubbly|undifferentiated",
        "spiny|undifferentiated",
    }
    assert all(0.0 <= v <= 1.0 for v in out["overlaps"].values())


PROTOCOL_TARGET = {
    "region": "Holuhraun lava field polygon of Neish et al. 2017",
    "convention": "NEISH_SC_LL_TX_1_MINUS_I",
    "aggregation": ["ratio_of_mean_powers", "mean_pixelwise_cpr"],
    "incidence_band_deg": [50.0, 53.0],
}


def test_target_is_unavailable_with_mismatching_items_named() -> None:
    facts = {
        "region": "Voigt and Hamilton 2021 facies polygons",
        "convention": "NEISH_SC_LL_TX_1_MINUS_I",
        "aggregation": ["ratio_of_mean_powers", "mean_pixelwise_cpr"],
        "incidence_range_deg": [20.0, 65.0],
    }
    out = hol.check_applicability(facts, PROTOCOL_TARGET)
    assert out["status"] == "Unavailable"
    assert out["mismatches"] == ["region", "incidence_band"]
    assert "convention" not in out["mismatches"]


def test_target_is_applicable_only_when_every_item_matches() -> None:
    facts = {
        "region": PROTOCOL_TARGET["region"],
        "convention": PROTOCOL_TARGET["convention"],
        "aggregation": PROTOCOL_TARGET["aggregation"],
        "incidence_range_deg": [50.5, 52.5],
    }
    assert hol.check_applicability(facts, PROTOCOL_TARGET) == {
        "status": "applicable",
        "mismatches": [],
    }
    unknown = dict(facts, convention=None)
    out = hol.check_applicability(unknown, PROTOCOL_TARGET)
    assert out["status"] == "Unavailable" and "convention" in out["mismatches"]


# --- plausibility and the G3 status ---------------------------------------------------------

RULE = {
    "overlap_consistent_min": 0.78,
    "overlap_suspect_below": 0.60,
    "level_tolerance_cpr": 0.10,
    "published_overlap_range": [0.88, 0.89],
    "published_mean_cpr": {"rubbly": 0.47, "spiny": 0.45, "undifferentiated": 0.47},
}


def _overlaps(v: float) -> dict[str, float]:
    return {"rubbly|spiny": v, "rubbly|undifferentiated": v, "spiny|undifferentiated": v}


LEVELS_OK = {"rubbly": 0.48, "spiny": 0.44, "undifferentiated": 0.5}


@pytest.mark.parametrize(
    ("overlap", "levels", "expected"),
    [
        (0.88, LEVELS_OK, "consistent"),
        (0.70, LEVELS_OK, "discrepant"),
        (0.40, LEVELS_OK, "suspected_processing_error"),
        (0.88, {"rubbly": 0.9, "spiny": 0.44, "undifferentiated": 0.5}, "discrepant"),
    ],
)
def test_plausibility_classification(
    overlap: float, levels: dict[str, float], expected: str
) -> None:
    assert hol.plausibility(_overlaps(overlap), levels, RULE)["outcome"] == expected


def test_g3_status_logic() -> None:
    assert hol.g3_status(run=False, outcome=None, other_parts_recorded=False) == "NOT_RUN"
    assert hol.g3_status(run=False, outcome=None, other_parts_recorded=True) == "NOT_RUN"
    # Inside tolerance: PASS only when the other G3 parts are also recorded.
    assert hol.g3_status(run=True, outcome="consistent", other_parts_recorded=False) == "PARTIAL"
    assert hol.g3_status(run=True, outcome="consistent", other_parts_recorded=True) == "PASS"
    # Outside tolerance never passes.
    assert hol.g3_status(run=True, outcome="discrepant", other_parts_recorded=True) == "PARTIAL"
    assert hol.g3_status(run=True, outcome="discrepant", other_parts_recorded=False) == "PARTIAL"
    assert (
        hol.g3_status(run=True, outcome="suspected_processing_error", other_parts_recorded=True)
        == "FAIL"
    )
    with pytest.raises(ValueError, match="outcome"):
        hol.g3_status(run=True, outcome="bogus", other_parts_recorded=True)
    with pytest.raises(ValueError, match="outcome"):
        hol.g3_status(run=True, outcome=None, other_parts_recorded=True)


# --- CLI -------------------------------------------------------------------------------------


def test_run_is_blocked_without_the_scene_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(hol.SCENE_ENV, raising=False)
    assert hol.main(["run"]) == 3
    assert capsys.readouterr().out.startswith("blocked: ")


def test_run_is_blocked_when_the_scene_directory_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(hol.SCENE_ENV, str(tmp_path / "nowhere"))
    assert hol.main(["run"]) == 3
    assert "blocked: " in capsys.readouterr().out


def test_run_is_blocked_without_facies_polygons(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(hol.SCENE_ENV, str(tmp_path))
    monkeypatch.delenv(hol.FACIES_ENV, raising=False)
    assert hol.main(["run"]) == 3
    assert "facies polygons not available" in capsys.readouterr().out


def test_subcommand_is_required() -> None:
    with pytest.raises(SystemExit) as info:
        hol.main([])
    assert info.value.code == 2


def test_check_passes_on_the_committed_records_or_fails_with_a_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = hol.main(["check"])
    assert code in (0, 1)
    if code == 1:
        assert capsys.readouterr().out.strip()


def test_canonical_output_is_stable_and_refuses_nan() -> None:
    text = hol.dumps({"b": 1, "a": [1.5, 2]})
    assert text == '{\n  "a": [\n    1.5,\n    2\n  ],\n  "b": 1\n}\n'
    with pytest.raises(ValueError):
        hol.dumps({"x": float("nan")})
    assert json.loads(text) == {"a": [1.5, 2], "b": 1}
