"""Distances, background_percentile and compare_windows (T4L-03). Inputs are synthetic."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from terraluna_pipeline.config import DEFAULT_RUN_CONFIG
from terraluna_pipeline.matching import (
    FAMILIES,
    OBSERVABLES,
    MatchReason,
    QualifyResult,
    QualifySettings,
    WindowSummary,
    background_percentile,
    compare_windows,
    qualify_pair,
    radar_distance,
    terrain_distance,
    unavailable_uncertainty,
    wasserstein_1,
)
from terraluna_pipeline.schema import (
    ComparisonRecord,
    DistanceEntry,
    RadarFingerprint,
    UncertaintyComponents,
)

if TYPE_CHECKING:  # mypy's name for the builders; pytest puts pipeline/tests on sys.path
    from pipeline.tests.test_matching_builders import (
        E,
        entry,
        product,
        radar_fp,
        terrain_variant,
        variant,
    )
else:
    from test_matching_builders import E, entry, product, radar_fp, terrain_variant, variant

CONFIG = QualifySettings.from_config(DEFAULT_RUN_CONFIG)
TARGET_ID = "moon-sps-x2-y2"
EARTH_ID = "earth-utm27n-e280-n2880"
SOURCES = [("synthetic-dem", None), ("synthetic-earth-dem", None)]
LIMITS = ["synthetic test record"]


def radar(cpr: list[float], **kwargs: Any) -> RadarFingerprint:
    record = RadarFingerprint.model_validate(radar_fp(TARGET_ID, "r", cpr=cpr, **kwargs)["radar"])
    return record


# --- terrain_distance -------------------------------------------------------------------------


def test_terrain_hand_case() -> None:
    lags = [270.0, 360.0, 540.0]
    a = terrain_variant([1.0, E, E * E], lags=lags)
    b = terrain_variant([1.0, 1.0, 1.0], lags=lags)
    result = terrain_distance(a, b)
    assert result.value == pytest.approx(math.sqrt((0 + 1 + 4) / 3), rel=1e-15)
    assert result.lags_m == tuple(lags)


def test_identical_fingerprints_give_a_real_zero() -> None:
    a = terrain_variant([1.5, 2.0, 2.5, 3.0, 3.5])
    result = terrain_distance(a, a)
    assert result.value == 0.0 and result.reason is None


def test_shared_lags_are_the_intersection() -> None:
    a = terrain_variant([1.0, E, 5.0], lags=[270.0, 360.0, 540.0])
    b = terrain_variant([1.0, 1.0, 7.0], lags=[360.0, 540.0, 720.0])
    result = terrain_distance(a, b)
    assert result.lags_m == (360.0, 540.0)
    expected = math.sqrt((1.0 + math.log(5.0) ** 2) / 2)  # 360: ln e - ln 1; 540: ln 5 - ln 1
    assert result.value == pytest.approx(expected, rel=1e-15)


def test_no_shared_lags_and_nu_zero_are_reasons_not_numbers() -> None:
    a = terrain_variant([1.0], lags=[270.0])
    b = terrain_variant([1.0], lags=[360.0])
    assert terrain_distance(a, b).reason == MatchReason.NO_SHARED_LAGS
    zero = terrain_variant([0.0, 1.0], lags=[270.0, 360.0])
    one = terrain_variant([1.0, 1.0], lags=[270.0, 360.0])
    result = terrain_distance(zero, one)
    assert result.value is None and result.reason == MatchReason.NU_ZERO


def test_hurst_is_never_an_input() -> None:
    a = terrain_variant([1.0, 2.0, 3.0], lags=[270.0, 360.0, 540.0], hurst=0.5)
    b = terrain_variant([1.0, 2.0, 3.0], lags=[270.0, 360.0, 540.0], hurst=None)
    c = terrain_variant([2.0, 2.0, 2.0], lags=[270.0, 360.0, 540.0], hurst=0.9)
    assert terrain_distance(a, c) == terrain_distance(b, c)


# --- radar_distance ---------------------------------------------------------------------------


def quantile_integral(u: list[float], v: list[float], steps: int = 6000) -> float:
    """Independent check: midpoint rule over t of |F_u^-1(t) - F_v^-1(t)|, exact for steps
    divisible by both sample sizes."""
    a, b = sorted(u), sorted(v)
    total = 0.0
    for k in range(steps):
        t = (k + 0.5) / steps
        total += abs(a[math.floor(t * len(a))] - b[math.floor(t * len(b))])
    return total / steps


def test_wasserstein_hand_cases() -> None:
    assert wasserstein_1([0.0, 1.0], [1.0, 2.0]) == 1.0
    assert wasserstein_1([0.0, 1.0], [0.0, 1.0, 2.0]) == pytest.approx(0.5, abs=1e-15)
    assert quantile_integral([0.0, 1.0], [0.0, 1.0, 2.0]) == pytest.approx(0.5, abs=1e-12)


def test_radar_distance_needs_the_same_support_and_valid_blocks() -> None:
    assert radar_distance(radar([0.0, 1.0]), radar([1.0, 2.0])).value == 1.0
    other = radar([1.0, 2.0], support_m=500.0)
    assert radar_distance(radar([0.0, 1.0]), other).reason == MatchReason.SUPPORT_MISMATCH
    empty = radar([])
    assert radar_distance(empty, radar([1.0])).reason == MatchReason.NO_VALID_BLOCKS


finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False)
samples = st.lists(finite, min_size=1, max_size=12)
positive = st.floats(min_value=1e-6, max_value=1e6, allow_nan=False)


@given(samples, samples)
def test_wasserstein_is_symmetric_and_matches_the_integral(u: list[float], v: list[float]) -> None:
    d = wasserstein_1(u, v)
    assert d == pytest.approx(wasserstein_1(v, u), rel=1e-10, abs=1e-12)
    assert wasserstein_1(u, u) == 0.0
    steps = len(u) * len(v) * 2
    assert d == pytest.approx(quantile_integral(u, v, steps), rel=1e-9, abs=1e-6)


@given(samples, st.randoms())
def test_wasserstein_is_invariant_to_order(u: list[float], rng: Any) -> None:
    shuffled = list(u)
    rng.shuffle(shuffled)
    assert wasserstein_1(shuffled, [0.0, 1.0]) == wasserstein_1(u, [0.0, 1.0])


@given(st.lists(positive, min_size=5, max_size=5), st.lists(positive, min_size=5, max_size=5))
def test_terrain_distance_is_symmetric(a: list[float], b: list[float]) -> None:
    va, vb = terrain_variant(list(a)), terrain_variant(list(b))
    ab, ba = terrain_distance(va, vb).value, terrain_distance(vb, va).value
    assert ab is not None and ba is not None
    assert ab == pytest.approx(ba, rel=1e-10, abs=1e-12)
    assert terrain_distance(va, va).value == 0.0


# --- background_percentile --------------------------------------------------------------------


@pytest.mark.parametrize(("d", "expected"), [(1.5, 35.0), (1.0, 20.0), (-1.0, 0.0), (4.0, 100.0)])
def test_percentile_hand_cases(d: float, expected: float) -> None:
    result = background_percentile(d, [0.0, 1.0, 2.0, 3.0], [2, 3, 5])
    assert result.value == pytest.approx(expected, abs=1e-12)


def test_percentile_empty_and_invalid_inputs() -> None:
    empty = background_percentile(1.0, [0.0, 1.0], [0])
    assert empty.value is None and empty.reason == MatchReason.BACKGROUND_EMPTY
    with pytest.raises(ValueError, match="ascending"):
        background_percentile(1.0, [0.0, 0.0, 1.0], [1, 1])
    with pytest.raises(ValueError, match="counts"):
        background_percentile(1.0, [0.0, 1.0, 2.0], [1])
    with pytest.raises(ValueError, match="finite"):
        background_percentile(math.nan, [0.0, 1.0], [1])


# --- compare_windows --------------------------------------------------------------------------


def eligibility(target: Any, earth: Any, mode: Any = "C") -> dict[str, QualifyResult]:
    ts, es = WindowSummary.from_entry(target), WindowSummary.from_entry(earth)
    return {
        o: qualify_pair(product(), product(), o, mode, ts, es, settings=CONFIG) for o in OBSERVABLES
    }


def compare(target: Any, earth: Any, mode: Any = "C", **kwargs: Any) -> ComparisonRecord:
    return compare_windows(
        target,
        earth,
        eligibility(target, earth, mode),
        shipping_mode=mode,
        source_ids=SOURCES,
        claim_limits=LIMITS,
        config_path=DEFAULT_RUN_CONFIG,
        **kwargs,
    )


def test_mode_c_record_has_terrain_distances_and_no_radar_value() -> None:
    lags = [270.0, 360.0, 540.0]
    target = entry(TARGET_ID, relief=variant([1.0, E, E * E], lags=lags))
    earth = entry(EARTH_ID, relief=variant([1.0, 1.0, 1.0], lags=lags))
    record = compare(target, earth)
    distances = record.distances_by_family
    assert set(distances.keys()) == set(FAMILIES)
    relief = distances["terrain_relief"]
    assert relief.value == pytest.approx(math.sqrt(5 / 3), rel=1e-15)
    assert relief.unit == "RMS of log nu" and relief.evidence_label == "Derived"
    assert distances["terrain_texture"].value == 0.0
    assert distances["terrain_texture"].status == "available"
    radar_entry = distances["radar_cpr"]
    assert radar_entry.value is None and radar_entry.status == "not_comparable"
    assert radar_entry.reason == MatchReason.RADAR_NOT_COMPLETED
    model = distances["model_embedding"]
    assert model.value is None and model.reason == MatchReason.ENCODER_NOT_EVALUATED
    for item in distances.values():
        assert item.reference_spread is None and item.reference_spread_reason is not None
        assert item.background_percentile is None
        assert item.tied is None and item.tie_reason == MatchReason.SPREAD_UNAVAILABLE
    assert record.comparable_observables == ["terrain_relief", "terrain_texture"]
    assert [x.observable for x in record.excluded_observables_and_reasons] == ["radar_cpr"]
    assert record.run_id.startswith("sha256:")
    assert record.shipping_mode == "C"


def test_exact_key_sets_leave_no_room_for_a_combined_distance() -> None:
    record = compare(entry(TARGET_ID), entry(EARTH_ID))
    assert set(ComparisonRecord.model_fields) == {
        "target_window_id",
        "earth_window_id",
        "shipping_mode",
        "comparable_observables",
        "excluded_observables_and_reasons",
        "distances_by_family",
        "uncertainty_components",
        "sort_rule",
        "claim_limits",
        "source_ids",
        "run_id",
    }
    for item in record.distances_by_family.values():
        assert set(item.model_dump()) == set(DistanceEntry.model_fields)
    assert set(record.model_dump()["distances_by_family"]) == set(FAMILIES)


def test_mode_b_record_never_carries_a_radar_value() -> None:
    target = entry(TARGET_ID, radar=[radar_fp(TARGET_ID, "synthetic-dem")])
    earth = entry(EARTH_ID, radar=[radar_fp(EARTH_ID, "synthetic-dem", cpr=[5.0])])
    record = compare(target, earth, mode="B")
    radar_entry = record.distances_by_family["radar_cpr"]
    assert radar_entry.value is None and radar_entry.status == "not_comparable"
    assert "passed" not in record.model_dump_json().lower()


def test_ineligible_terrain_is_null_with_reason_never_zero() -> None:
    target = entry(TARGET_ID)
    earth = entry(EARTH_ID, valid_fraction=0.5)
    record = compare(target, earth)
    relief = record.distances_by_family["terrain_relief"]
    assert relief.value is None and relief.status == "not_comparable"
    assert relief.reason == "below_min_valid_fraction"


def test_model_distance_is_stored_as_predicted() -> None:
    record = compare(entry(TARGET_ID), entry(EARTH_ID), model_distance=0.25)
    model = record.distances_by_family["model_embedding"]
    assert model.value == 0.25 and model.evidence_label == "Predicted"
    assert model.unit == "cosine distance"


def test_radar_change_does_not_touch_terrain() -> None:
    target = entry(TARGET_ID, radar=[radar_fp(TARGET_ID, "synthetic-dem")])
    one = entry(EARTH_ID, radar=[radar_fp(EARTH_ID, "synthetic-dem", cpr=[0.1])])
    two = entry(EARTH_ID, radar=[radar_fp(EARTH_ID, "synthetic-dem", cpr=[0.9])])
    a, b = compare(target, one), compare(target, two)
    for family in ("terrain_relief", "terrain_texture", "model_embedding"):
        assert a.distances_by_family[family] == b.distances_by_family[family]


def test_unsupported_hurst_leaves_every_number_unchanged() -> None:
    lags = [270.0, 360.0, 540.0]
    with_h = entry(EARTH_ID, relief=variant([2.0, 3.0, 4.0], lags=lags, hurst=0.5))
    without = entry(EARTH_ID, relief=variant([2.0, 3.0, 4.0], lags=lags, hurst=None))
    target = entry(TARGET_ID, relief=variant([1.0, 1.0, 1.0], lags=lags))
    assert (
        compare(target, with_h).distances_by_family == compare(target, without).distances_by_family
    )


def test_missing_eligibility_raises() -> None:
    target, earth = entry(TARGET_ID), entry(EARTH_ID)
    partial = eligibility(target, earth)
    del partial["radar_cpr"]
    with pytest.raises(ValueError, match="radar_cpr"):
        compare_windows(
            target, earth, partial, shipping_mode="C", source_ids=SOURCES, claim_limits=LIMITS
        )


def test_uncertainty_default_is_four_unavailable_components() -> None:
    components = unavailable_uncertainty()
    assert set(UncertaintyComponents.model_fields) == {
        "heterogeneity",
        "measurement_error",
        "statistical_prediction_error",
        "scenario_sensitivity",
    }
    for name in UncertaintyComponents.model_fields:
        part = getattr(components, name)
        assert part.status == "unavailable" and part.reason == MatchReason.UNCERTAINTY_NOT_RUN
