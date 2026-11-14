"""derive_circular and the named block statistics (PRD 6.2 Aggregation, 7, 8.11).

``derive_circular`` puts Mini-RF Stokes input (story T2-02) and UAVSAR covariance input (story
T2-03) behind one interface. ``aggregate_blocks`` aggregates linear SC and OC per block first and
derives the ratio afterwards (PRD 6.2: ratio of mean powers, the primary radar statistic), and
reports the mean of pixelwise ratios as a separately named statistic.

Comparisons use the PRD 7 arithmetic tolerances on float64, rtol = 1e-10 and atol = 1e-12.
Hand-computed values come from the acceptance criteria of T2-04; the exact oracles use
``fractions.Fraction`` and the PRD 6.2 expanded formulas in ``pipeline/tests/polarimetry``,
neither of which imports the code under test.

Decision fixed here (T2-04): the ratio of mean powers uses every pixel whose SC and OC are both
VALID powers. A pixel with OC = 0 (F5) is such a pixel, so its SC and its zero OC enter both block
means; it has no pixelwise CPR, so it does not enter the mean of pixelwise ratios.
"""

from __future__ import annotations

import dataclasses
import inspect
import math
from fractions import Fraction
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from terraluna_pipeline.polarimetry import (
    ARITHMETIC_TOLERANCE,
    COVARIANCE_HH_HV_VV,
    COVARIANCE_HH_SQRT2HV_VV,
    COVARIANCE_INPUT,
    MINIRF_SIS_CONVENTION,
    NEISH_SC_LL_CONVENTION,
    OPPOSITE_SC_CONVENTION,
    PRIMARY_RADAR_STATISTIC,
    REASON_DTYPE,
    STATE_DTYPE,
    STOKES_INPUT,
    BlockAggregates,
    BlockSupport,
    CircularPowers,
    CovarianceCircularPowers,
    CovarianceInput,
    DecibelArray,
    InvalidReason,
    ObsState,
    StatedArray,
    StokesInput,
    Tolerance,
    aggregate_blocks,
    circular_from_covariance,
    circular_from_stokes,
    decode_linear,
    derive_circular,
    to_db_for_display,
)

if TYPE_CHECKING:  # mypy's name for the oracle package; see pipeline/tests/polarimetry
    from pipeline.tests.polarimetry import expanded_oracle, jones_oracle
else:  # pytest's name for the same files
    from polarimetry import expanded_oracle, jones_oracle

RTOL = 1e-10
ATOL = 1e-12

MISSING = ObsState.MISSING
INVALID = ObsState.INVALID
VALID = ObsState.VALID

SMALLEST_NORMAL = float(np.finfo(np.float64).smallest_normal)
FLOAT64_MAX = float(np.finfo(np.float64).max)

SIS = MINIRF_SIS_CONVENTION
LL = NEISH_SC_LL_CONVENTION
OPP = OPPOSITE_SC_CONVENTION
RAW = COVARIANCE_HH_HV_VV
SQRT2 = COVARIANCE_HH_SQRT2HV_VV

CIRCULAR_FIELDS = ("total", "sc", "oc", "cpr")
COVARIANCE_FIELDS = (*CIRCULAR_FIELDS, "base_sc", "handedness_term", "handedness_ratio")
STATISTICS = ("mean_sc", "mean_oc", "ratio_of_mean_powers", "mean_of_pixelwise_ratios")
RATIO_STATISTICS = ("ratio_of_mean_powers", "mean_of_pixelwise_ratios")


# --- Helpers ----------------------------------------------------------------------------------


def from_stokes(
    s1: npt.ArrayLike,
    s4: npt.ArrayLike,
    valid: npt.ArrayLike = True,
    *,
    s2: npt.ArrayLike | None = None,
    s3: npt.ArrayLike | None = None,
    tolerance: Tolerance = ARITHMETIC_TOLERANCE,
) -> CircularPowers:
    """Derive from Stokes input under the declared SIS convention; a test helper only."""
    return derive_circular(
        STOKES_INPUT,
        StokesInput(s1=s1, s4=s4, s2=s2, s3=s3),
        SIS,
        valid,
        tolerance=tolerance,
    )


def from_covariance(
    covariance: npt.ArrayLike,
    valid: npt.ArrayLike = True,
    *,
    convention: Any = LL,
    ordering: Any = RAW,
) -> CovarianceCircularPowers:
    """Derive from covariance input with declared handedness and ordering; a test helper only."""
    return derive_circular(
        COVARIANCE_INPUT,
        CovarianceInput(covariance=covariance, ordering=ordering),
        convention,
        valid,
    )


def assert_value(result: StatedArray, expected: float) -> None:
    assert np.all(result.state == VALID)
    assert np.all(result.reason == InvalidReason.NONE)
    np.testing.assert_allclose(result.value, expected, rtol=RTOL, atol=ATOL)


def assert_unavailable(result: StatedArray, state: ObsState, reason: InvalidReason) -> None:
    """Only a state and a reason: no finite number, no huge number, no 0.0."""
    assert np.all(result.state == state)
    assert np.all(result.reason == reason)
    assert np.all(np.isnan(result.value))


def assert_same(actual: StatedArray, expected: StatedArray) -> None:
    """Same states and reasons; values equal to the PRD 7 tolerance where VALID."""
    np.testing.assert_array_equal(actual.state, expected.state)
    np.testing.assert_array_equal(actual.reason, expected.reason)
    np.testing.assert_allclose(actual.value, expected.value, rtol=RTOL, atol=ATOL, equal_nan=True)


def single(values: npt.NDArray[Any]) -> Any:
    """The one element of a one-block array."""
    assert values.shape == (1, 1)
    return values[0, 0].item()


def invalid_counts(support: BlockSupport) -> dict[str, int]:
    """The per-reason invalid counts of a one-block support record."""
    return {
        str(reason): int(single(count)) for reason, count in support.n_invalid_by_reason.items()
    }


def stated(values: npt.ArrayLike) -> StatedArray:
    """A VALID stated array of finite values; a test fixture, not a product."""
    value = np.array(values, dtype=np.float64)
    assert np.all(np.isfinite(value))
    return StatedArray(
        value=value,
        state=np.full(value.shape, VALID, dtype=STATE_DTYPE),
        reason=np.full(value.shape, InvalidReason.NONE, dtype=REASON_DTYPE),
    )


def unavailable(shape: tuple[int, ...], reason: InvalidReason) -> StatedArray:
    return StatedArray(
        value=np.full(shape, np.nan),
        state=np.full(shape, INVALID, dtype=STATE_DTYPE),
        reason=np.full(shape, reason, dtype=REASON_DTYPE),
    )


def power_record(sc: npt.ArrayLike, oc: npt.ArrayLike) -> CircularPowers:
    """A circular record with the given VALID SC and OC, built directly for range tests.

    Total and CPR are computed here in float64. Where they are not finite, or where CPR is not
    zero but below the smallest normal float64, they are INVALID with FLOAT64_OUT_OF_RANGE; a
    nonpositive OC gives CPR OC_NONPOSITIVE. A test fixture labelled as such, not a product.
    """
    sc_values = np.array(sc, dtype=np.float64)
    oc_values = np.array(oc, dtype=np.float64)
    shape = sc_values.shape
    with np.errstate(over="ignore", under="ignore", divide="ignore", invalid="ignore"):
        total = sc_values + oc_values
        cpr = np.where(oc_values > 0.0, sc_values / oc_values, np.nan)
    cpr_state = np.full(shape, VALID, dtype=STATE_DTYPE)
    cpr_reason = np.full(shape, InvalidReason.NONE, dtype=REASON_DTYPE)
    out_of_range = ~np.isfinite(cpr) | ((cpr != 0.0) & (np.abs(cpr) < SMALLEST_NORMAL))
    cpr_state[out_of_range] = INVALID
    cpr_reason[out_of_range] = InvalidReason.FLOAT64_OUT_OF_RANGE
    cpr_state[oc_values <= 0.0] = INVALID
    cpr_reason[oc_values <= 0.0] = InvalidReason.OC_NONPOSITIVE
    total_ok = np.isfinite(total)
    return CircularPowers(
        total=StatedArray(
            value=np.where(total_ok, total, np.nan),
            state=np.where(total_ok, VALID, INVALID).astype(STATE_DTYPE),
            reason=np.where(
                total_ok, InvalidReason.NONE, InvalidReason.FLOAT64_OUT_OF_RANGE
            ).astype(REASON_DTYPE),
        ),
        sc=stated(sc_values),
        oc=stated(oc_values),
        cpr=StatedArray(
            value=np.where(cpr_state == VALID, cpr, np.nan), state=cpr_state, reason=cpr_reason
        ),
        convention=SIS,
        tolerance=ARITHMETIC_TOLERANCE,
    )


def stokes_block(
    pixels: dict[tuple[int, int], tuple[float, float]],
    shape: tuple[int, int] = (4, 4),
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.bool_]]:
    """S1, S4 and validity arrays: the listed pixels as (S1, S4), every other pixel masked."""
    s1 = np.zeros(shape)
    s4 = np.zeros(shape)
    valid = np.zeros(shape, dtype=bool)
    for (row, col), (first, fourth) in pixels.items():
        s1[row, col] = first
        s4[row, col] = fourth
        valid[row, col] = True
    return s1, s4, valid


def sc_oc(sc: float, oc: float) -> tuple[float, float]:
    """The Stokes pair (S1, S4) with these SC and OC under the SIS convention, S4 = OC - SC."""
    return sc + oc, oc - sc


# --- Covariance fixtures (PRD 7), asserted Hermitian PSD before use ---------------------------


def matrix(
    a: float, b: float, v: float, c: complex, d: complex, e: complex
) -> npt.NDArray[np.complex128]:
    """The (HH, HV, VV) covariance with the PRD 6.2 elements, ``C_ij = <k_i k_j*>``."""
    return np.array(
        [[a, d, c], [np.conj(d), v, e], [np.conj(c), np.conj(e), b]], dtype=np.complex128
    )


def assert_hermitian_psd(covariance: npt.NDArray[np.complex128]) -> npt.NDArray[np.float64]:
    np.testing.assert_array_equal(covariance, covariance.conj().T)
    eigenvalues: npt.NDArray[np.float64] = np.linalg.eigvalsh(covariance)
    assert eigenvalues.min() >= -ATOL
    return eigenvalues


def f6_covariance() -> npt.NDArray[np.complex128]:
    """PRD 7, F6: HH = 1, HV = 0.2i, VV = 0 (rank one), asserted Hermitian PSD."""
    covariance = jones_oracle.covariance_of([(1.0, 0.2j, 0.0)])
    np.testing.assert_allclose(
        covariance, matrix(1.0, 0.0, 0.04, 0.0, -0.2j, 0.0), rtol=RTOL, atol=ATOL
    )
    eigenvalues = assert_hermitian_psd(covariance)
    np.testing.assert_allclose(eigenvalues, [0.0, 0.0, 1.04], rtol=RTOL, atol=ATOL)
    return covariance


def f7_covariance(c: float) -> npt.NDArray[np.complex128]:
    """PRD 7, F7: A = B = 1, V = 0.1, D = E = 0 and real C, asserted Hermitian PSD with the PRD
    eigenvalues."""
    covariance = matrix(1.0, 1.0, 0.1, c, 0.0, 0.0)
    eigenvalues = assert_hermitian_psd(covariance)
    expected = {0.9: [0.1, 0.1, 1.9], 0.1: [0.1, 0.9, 1.1]}[c]
    np.testing.assert_allclose(eigenvalues, expected, rtol=RTOL, atol=ATOL)
    return covariance


def to_sqrt2_ordering(covariance: npt.NDArray[np.complex128]) -> npt.NDArray[np.complex128]:
    n = np.array([1.0, math.sqrt(2.0), 1.0])
    result: npt.NDArray[np.complex128] = covariance * np.outer(n, n)
    return result


# --- derive_circular: the interface (PRD 8.11) -------------------------------------------------


def test_derive_circular_takes_kind_data_convention_and_validity_mask() -> None:
    parameters = inspect.signature(derive_circular).parameters
    assert list(parameters) == ["input_kind", "data", "convention", "validity_mask", "tolerance"]
    for name in ("input_kind", "data", "convention", "validity_mask"):
        assert parameters[name].default is inspect.Parameter.empty, name
    assert parameters["tolerance"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["tolerance"].default == ARITHMETIC_TOLERANCE


def test_input_kinds_are_named_string_constants() -> None:
    assert STOKES_INPUT == "STOKES"
    assert COVARIANCE_INPUT == "COVARIANCE"


@pytest.mark.parametrize("kind", ["JONES", "stokes", "covariance", "", None, 1, b"STOKES"])
def test_unknown_input_kind_raises(kind: Any) -> None:
    with pytest.raises(ValueError, match="input kind"):
        derive_circular(kind, StokesInput(s1=2.0, s4=0.0, s2=None, s3=None), SIS, True)


@pytest.mark.parametrize(
    "convention", [None, "", "SIS", "MINIRF_SIS_S4_EQ_SC_MINUS_OC", LL, OPP, "sis"]
)
def test_undeclared_stokes_convention_raises(convention: Any) -> None:
    with pytest.raises(ValueError, match="convention"):
        derive_circular(
            STOKES_INPUT, StokesInput(s1=2.0, s4=0.0, s2=None, s3=None), convention, True
        )


@pytest.mark.parametrize("convention", [None, "", "LL", "SC_LL", SIS, "neish_sc_ll_tx_1_minus_i"])
def test_undeclared_handedness_convention_raises(convention: Any) -> None:
    with pytest.raises(ValueError, match="convention"):
        from_covariance(f6_covariance(), convention=convention)


@pytest.mark.parametrize("ordering", [None, "", "HH_VV_HV", "hh_hv_vv", 0])
def test_undeclared_covariance_ordering_raises(ordering: Any) -> None:
    with pytest.raises(ValueError, match="ordering"):
        CovarianceInput(covariance=f6_covariance(), ordering=ordering)


def test_input_kind_must_match_the_data() -> None:
    stokes = StokesInput(s1=2.0, s4=0.0, s2=None, s3=None)
    covariance = CovarianceInput(covariance=f6_covariance(), ordering=RAW)
    bad_stokes: Any = covariance
    bad_covariance: Any = stokes
    with pytest.raises(TypeError, match="StokesInput"):
        derive_circular(STOKES_INPUT, bad_stokes, SIS, True)
    with pytest.raises(TypeError, match="CovarianceInput"):
        derive_circular(COVARIANCE_INPUT, bad_covariance, LL, True)


@pytest.mark.parametrize(
    "data", [(2.0, 0.0), {"s1": 2.0, "s4": 0.0}, np.array([2.0, 0.0]), None, 2.0]
)
def test_bare_data_is_refused(data: Any) -> None:
    with pytest.raises(TypeError):
        derive_circular(STOKES_INPUT, data, SIS, True)


def test_stokes_input_needs_s2_and_s3_stated_explicitly() -> None:
    with pytest.raises(TypeError):
        StokesInput(s1=2.0, s4=0.0)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="together"):
        from_stokes(2.0, 0.0, s2=0.0, s3=None)


def test_validity_mask_is_true_where_the_product_marks_a_pixel_valid() -> None:
    result = from_stokes([2.0, 2.0], [0.0, 0.0], [True, False])
    for name in CIRCULAR_FIELDS:
        field = getattr(result, name)
        assert field.state[0] == VALID, name
        assert field.state[1] == MISSING, name
        assert field.reason[1] == InvalidReason.PRODUCT_MASKED, name
        assert np.isnan(field.value[1]), name
    covariance = np.stack([f6_covariance(), f6_covariance()])
    result_cov = from_covariance(covariance, [False, True])
    for name in COVARIANCE_FIELDS:
        field = getattr(result_cov, name)
        assert field.state[0] == MISSING, name
        assert field.reason[0] == InvalidReason.PRODUCT_MASKED, name
        assert field.state[1] == VALID, name


@pytest.mark.parametrize(
    "mask",
    [
        1,
        [1, 0],
        np.array([1.0, 0.0]),
        "True",
        None,
        np.ma.array([True, True], mask=[False, True]),
        [np.ma.array([True, True], mask=[False, True])],
    ],
)
def test_validity_mask_must_be_a_plain_boolean_array(mask: Any) -> None:
    with pytest.raises(TypeError):
        from_stokes([2.0, 2.0], [0.0, 0.0], mask)


def test_validity_mask_must_broadcast_to_the_pixel_shape() -> None:
    with pytest.raises(ValueError):
        from_stokes([2.0, 2.0], [0.0, 0.0], [True, True, True])


def test_derive_circular_returns_linear_powers_with_states_and_reasons() -> None:
    doc = inspect.getdoc(derive_circular)
    assert doc is not None
    assert "linear" in doc
    result = from_stokes([2.0, 1.0], [0.0, 2.0])
    assert type(result) is CircularPowers
    for name in ("sc", "oc", "cpr"):
        field = getattr(result, name)
        assert type(field) is StatedArray
        assert field.state.dtype == STATE_DTYPE
        assert field.reason.dtype.kind == "U"
    assert type(from_covariance(f6_covariance())) is CovarianceCircularPowers


def test_covariance_results_stay_unverified_for_handedness() -> None:
    assert from_covariance(f6_covariance()).handedness_verified is False


def test_tolerance_is_passed_through() -> None:
    # |S4| - S1 = 5e-10: beyond the arithmetic tolerance, within rtol = 1e-9.
    s1, s4 = 1.0, 1.0 + 5e-10
    strict = from_stokes(s1, s4)
    assert_unavailable(strict.cpr, INVALID, InvalidReason.STOKES_S1_LT_ABS_S4)
    loose_tolerance = Tolerance(rtol=1e-9, atol=0.0)
    loose = from_stokes(s1, s4, tolerance=loose_tolerance)
    assert loose.tolerance == loose_tolerance
    assert loose.sc.state == VALID
    assert loose.sc.value < 0.0
    assert_unavailable(loose.cpr, INVALID, InvalidReason.SC_NEGATIVE)


# --- F1-F7 through derive_circular equal stories T2-02 and T2-03 (PRD 7) -----------------------


def stokes_through_derive(s1: npt.ArrayLike, s4: npt.ArrayLike) -> CircularPowers:
    derived = from_stokes(s1, s4)
    direct = circular_from_stokes(s1, s4, s2=None, s3=None, convention=SIS, product_masked=False)
    for name in CIRCULAR_FIELDS:
        assert_same(getattr(derived, name), getattr(direct, name))
    assert derived.convention == direct.convention == SIS
    assert derived.tolerance == direct.tolerance
    return derived


def covariance_through_derive(
    covariance: npt.NDArray[np.complex128], convention: Any, ordering: Any
) -> CovarianceCircularPowers:
    derived = from_covariance(covariance, convention=convention, ordering=ordering)
    direct = circular_from_covariance(
        covariance, ordering=ordering, convention=convention, product_masked=False
    )
    for name in COVARIANCE_FIELDS:
        assert_same(getattr(derived, name), getattr(direct, name))
    assert derived.convention == convention
    assert derived.ordering == ordering
    return derived


def test_F1_derive_circular_gives_a_valid_zero_cpr() -> None:
    result = stokes_through_derive(2.0, 2.0)
    assert_value(result.sc, 0.0)
    assert_value(result.oc, 2.0)
    assert_value(result.cpr, 0.0)


def test_F2_derive_circular_gives_unit_cpr() -> None:
    result = stokes_through_derive(2.0, 0.0)
    assert_value(result.sc, 1.0)
    assert_value(result.oc, 1.0)
    assert_value(result.cpr, 1.0)


def test_F3_derive_circular_keeps_cpr_above_the_old_bound() -> None:
    result = stokes_through_derive(4.0, -2.0)
    assert_value(result.sc, 3.0)
    assert_value(result.oc, 1.0)
    assert_value(result.cpr, 3.0)


def test_F4_derive_circular_publishes_nothing_for_an_invalid_stokes_pair() -> None:
    result = stokes_through_derive(1.0, 2.0)
    for name in CIRCULAR_FIELDS:
        assert_unavailable(getattr(result, name), INVALID, InvalidReason.STOKES_S1_LT_ABS_S4)


def test_F5_derive_circular_gives_no_ratio_for_zero_oc() -> None:
    result = stokes_through_derive(2.0, -2.0)
    assert_value(result.sc, 2.0)
    assert_value(result.oc, 0.0)
    assert_unavailable(result.cpr, INVALID, InvalidReason.OC_NONPOSITIVE)


def test_F1_to_F5_derive_circular_on_an_array_equals_circular_from_stokes() -> None:
    s1 = np.array([[2.0, 2.0, 4.0], [1.0, 2.0, np.nan]])
    s4 = np.array([[2.0, 0.0, -2.0], [2.0, -2.0, 0.0]])
    result = stokes_through_derive(s1, s4)
    np.testing.assert_allclose(
        result.cpr.value, [[0.0, 1.0, 3.0], [np.nan, np.nan, np.nan]], equal_nan=True
    )


@pytest.mark.parametrize("ordering", [RAW, SQRT2])
def test_F6_derive_circular_full_covariance_handedness(ordering: str) -> None:
    covariance = f6_covariance()
    if ordering == SQRT2:
        covariance = to_sqrt2_ordering(covariance)
    ll = covariance_through_derive(covariance, LL, ordering)
    opp = covariance_through_derive(covariance, OPP, ordering)
    assert_value(ll.oc, 0.25)
    assert_value(ll.sc, 0.49)
    assert_value(opp.oc, 0.25)
    assert_value(opp.sc, 0.09)
    assert_value(ll.cpr, 0.49 / 0.25)
    assert_value(opp.cpr, 0.09 / 0.25)


@pytest.mark.parametrize("ordering", [RAW, SQRT2])
def test_F7_derive_circular_two_channel_data_cannot_determine_cpr(ordering: str) -> None:
    high, low = f7_covariance(0.9), f7_covariance(0.1)
    if ordering == SQRT2:
        high, low = to_sqrt2_ordering(high), to_sqrt2_ordering(low)
    for convention in (LL, OPP):
        cpr_high = covariance_through_derive(high, convention, ordering).cpr
        cpr_low = covariance_through_derive(low, convention, ordering).cpr
        assert_value(cpr_high, 3.0 / 19.0)
        assert_value(cpr_low, 1.0)
        assert not np.isclose(cpr_high.value, cpr_low.value, rtol=RTOL, atol=ATOL)


# --- Block statistics: the two named statistics (PRD 6.2 Aggregation) --------------------------


def test_primary_statistic_is_the_ratio_of_mean_powers() -> None:
    assert PRIMARY_RADAR_STATISTIC == "ratio_of_mean_powers"
    s1, s4, valid = stokes_block({(0, 0): sc_oc(1.0, 1.0)})
    result = aggregate_blocks(from_stokes(s1, s4, valid), block_size=4)
    assert result.primary_statistic == PRIMARY_RADAR_STATISTIC
    assert type(getattr(result, result.primary_statistic)) is StatedArray


def test_two_pixels_give_different_named_statistics() -> None:
    """The acceptance example: OC 1 and 3 with SC 1 and 1 in one 4x4 block, the other 14
    pixels missing or invalid. Ratio of mean powers (1 + 1) / (1 + 3) = 0.5; mean of pixelwise
    ratios (1/1 + 1/3) / 2 = 2/3."""
    pixels = {(0, 0): sc_oc(1.0, 1.0), (2, 3): sc_oc(1.0, 3.0)}
    for index in [(1, 0), (1, 1), (1, 2), (3, 3)]:
        pixels[index] = (np.nan, 0.0)  # an observation that is not finite
    for index in [(0, 2), (2, 0), (3, 1)]:
        pixels[index] = (1.0, 2.0)  # F4: S1 < |S4|
    s1, s4, valid = stokes_block(pixels)
    result = aggregate_blocks(from_stokes(s1, s4, valid), block_size=4)

    assert_value(result.mean_sc, 1.0)
    assert_value(result.mean_oc, 2.0)
    assert_value(result.ratio_of_mean_powers, 0.5)
    assert_value(result.mean_of_pixelwise_ratios, 2.0 / 3.0)
    assert not np.isclose(
        result.ratio_of_mean_powers.value, result.mean_of_pixelwise_ratios.value, rtol=RTOL
    )

    for support in (result.power_support, result.ratio_support):
        assert support.n_pixels == 16
        assert single(support.n_valid) == 2
        assert single(support.n_missing) == 7
        assert invalid_counts(support) == {"NONFINITE": 4, "STOKES_S1_LT_ABS_S4": 3}
        assert single(support.valid_fraction) == 2 / 16


def test_full_block_hand_computed_statistics() -> None:
    pixels = {
        (row, col): sc_oc(1.0, 1.0 if (row + col) % 2 == 0 else 3.0)
        for row in range(4)
        for col in range(4)
    }
    s1, s4, valid = stokes_block(pixels)
    result = aggregate_blocks(from_stokes(s1, s4, valid), block_size=4)
    assert_value(result.ratio_of_mean_powers, 0.5)
    assert_value(result.mean_of_pixelwise_ratios, 2.0 / 3.0)
    assert single(result.power_support.n_valid) == 16
    assert single(result.power_support.n_missing) == 0
    assert invalid_counts(result.power_support) == {}
    assert single(result.power_support.valid_fraction) == 1.0


def test_zero_oc_pixel_enters_the_ratio_of_means_but_not_the_pixelwise_mean() -> None:
    """Decision: SC and OC are aggregated over the pixels where both are VALID powers. An F5
    pixel (SC = 2, OC = 0) adds its SC and its zero OC to the block means, (1 + 1 + 2) /
    (1 + 1 + 0) = 2, and has no pixelwise CPR, so the mean of pixelwise ratios is 1."""
    pixels = {(0, 0): sc_oc(1.0, 1.0), (0, 1): sc_oc(1.0, 1.0), (3, 3): (2.0, -2.0)}
    s1, s4, valid = stokes_block(pixels)
    derived = from_stokes(s1, s4, valid)
    assert derived.cpr.reason[3, 3] == InvalidReason.OC_NONPOSITIVE
    result = aggregate_blocks(derived, block_size=4)
    assert_value(result.mean_sc, 4.0 / 3.0)
    assert_value(result.mean_oc, 2.0 / 3.0)
    assert_value(result.ratio_of_mean_powers, 2.0)
    assert_value(result.mean_of_pixelwise_ratios, 1.0)
    assert single(result.power_support.n_valid) == 3
    assert single(result.ratio_support.n_valid) == 2
    assert invalid_counts(result.ratio_support) == {"OC_NONPOSITIVE": 1}
    assert single(result.ratio_support.n_missing) == 13


def test_block_whose_mean_oc_is_zero_has_no_ratio_and_no_epsilon() -> None:
    pixels = {(row, 0): (2.0, -2.0) for row in range(4)}  # F5 four times
    s1, s4, valid = stokes_block(pixels)
    result = aggregate_blocks(from_stokes(s1, s4, valid), block_size=4)
    assert_value(result.mean_sc, 2.0)
    assert_value(result.mean_oc, 0.0)
    assert_unavailable(result.ratio_of_mean_powers, INVALID, InvalidReason.OC_NONPOSITIVE)
    assert_unavailable(
        result.mean_of_pixelwise_ratios, INVALID, InvalidReason.BLOCK_NO_VALID_PIXELS
    )


def test_negative_sc_within_tolerance_is_averaged_as_computed() -> None:
    """S1 = 1, S4 = 1 + 5e-11 is within the arithmetic tolerance: SC is -2.5e-11, VALID as a
    power, never clipped, and without a pixelwise CPR (SC_NEGATIVE)."""
    pixels = {(0, 0): (1.0, 1.0 + 5e-11), (0, 1): sc_oc(1.0, 1.0)}
    s1, s4, valid = stokes_block(pixels)
    derived = from_stokes(s1, s4, valid)
    negative_sc = float(derived.sc.value[0, 0])
    assert negative_sc < 0.0
    result = aggregate_blocks(derived, block_size=4)
    np.testing.assert_allclose(single(result.mean_sc.value), (negative_sc + 1.0) / 2.0, rtol=RTOL)
    assert_value(result.mean_of_pixelwise_ratios, 1.0)
    assert invalid_counts(result.ratio_support) == {"SC_NEGATIVE": 1}

    only_negative = {(0, 0): (1.0, 1.0 + 5e-11)}
    s1, s4, valid = stokes_block(only_negative)
    result = aggregate_blocks(from_stokes(s1, s4, valid), block_size=4)
    assert result.mean_sc.state[0, 0] == VALID
    assert result.mean_sc.value[0, 0] < 0.0
    assert_unavailable(result.ratio_of_mean_powers, INVALID, InvalidReason.SC_NEGATIVE)


# --- Blocks with no valid pixel ----------------------------------------------------------------


def test_block_of_missing_pixels_is_missing_and_never_zero() -> None:
    s1, s4, valid = stokes_block({})
    result = aggregate_blocks(from_stokes(s1, s4, valid), block_size=4)
    for name in STATISTICS:
        assert_unavailable(getattr(result, name), MISSING, InvalidReason.BLOCK_NO_VALID_PIXELS)
    for support in (result.power_support, result.ratio_support):
        assert single(support.n_valid) == 0
        assert single(support.n_missing) == 16
        assert single(support.valid_fraction) == 0.0


def test_block_of_invalid_pixels_is_invalid_and_never_zero() -> None:
    s1 = np.full((4, 4), 1.0)
    s4 = np.full((4, 4), 2.0)  # F4 everywhere
    result = aggregate_blocks(from_stokes(s1, s4), block_size=4)
    for name in STATISTICS:
        assert_unavailable(getattr(result, name), INVALID, InvalidReason.BLOCK_NO_VALID_PIXELS)
    assert invalid_counts(result.power_support) == {"STOKES_S1_LT_ABS_S4": 16}
    assert single(result.power_support.n_missing) == 0


def test_block_of_missing_and_invalid_pixels_is_invalid() -> None:
    s1, s4, valid = stokes_block({(1, 1): (np.nan, 0.0)})
    result = aggregate_blocks(from_stokes(s1, s4, valid), block_size=4)
    for name in STATISTICS:
        assert_unavailable(getattr(result, name), INVALID, InvalidReason.BLOCK_NO_VALID_PIXELS)
    assert single(result.power_support.n_missing) == 15
    assert invalid_counts(result.power_support) == {"NONFINITE": 1}


# --- Block support and origins -----------------------------------------------------------------


def loop_oracle(
    sc: npt.NDArray[np.float64],
    oc: npt.NDArray[np.float64],
    usable: npt.NDArray[np.bool_],
    rows: int,
    cols: int,
) -> list[tuple[tuple[int, int], Fraction, Fraction, int]]:
    """Per block, by explicit slicing: origin, exact sum of SC, exact sum of OC, pixel count."""
    out = []
    for r0 in range(0, sc.shape[0], rows):
        for c0 in range(0, sc.shape[1], cols):
            window = (slice(r0, r0 + rows), slice(c0, c0 + cols))
            use = usable[window]
            sum_sc = sum((Fraction(float(x)) for x in sc[window][use]), Fraction(0))
            sum_oc = sum((Fraction(float(x)) for x in oc[window][use]), Fraction(0))
            out.append(((r0, c0), sum_sc, sum_oc, int(use.sum())))
    return out


@pytest.mark.parametrize("block_size", [(2, 4), (4, 2), (1, 8), 2])
def test_blocks_follow_the_pixel_grid_with_their_origins(block_size: int | tuple[int, int]) -> None:
    rng = np.random.default_rng(20261114)
    s1 = rng.uniform(1.0, 10.0, size=(4, 8))
    s4 = s1 * rng.uniform(-0.9, 0.9, size=(4, 8))
    valid = rng.uniform(size=(4, 8)) > 0.2
    derived = from_stokes(s1, s4, valid)
    result = aggregate_blocks(derived, block_size=block_size)
    rows, cols = (block_size, block_size) if isinstance(block_size, int) else block_size
    assert result.block_size == (rows, cols)
    grid = (4 // rows, 8 // cols)
    assert result.block_origin.shape == (*grid, 2)
    assert result.block_origin.dtype == np.int64
    for name in STATISTICS:
        assert getattr(result, name).value.shape == grid
    usable = (derived.sc.state == VALID) & (derived.oc.state == VALID)
    blocks = loop_oracle(derived.sc.value, derived.oc.value, usable, rows, cols)
    for index, (origin, sum_sc, sum_oc, count) in enumerate(blocks):
        position = np.unravel_index(index, grid)
        assert tuple(result.block_origin[position].tolist()) == origin
        assert int(result.power_support.n_valid[position]) == count
        assert count > 0  # the seed leaves a valid pixel in every block
        np.testing.assert_allclose(result.mean_sc.value[position], float(sum_sc / count), rtol=RTOL)
        np.testing.assert_allclose(
            result.ratio_of_mean_powers.value[position], float(sum_sc / sum_oc), rtol=RTOL
        )


def test_support_counts_add_up_in_every_block() -> None:
    rng = np.random.default_rng(7)
    s1 = rng.uniform(0.5, 2.0, size=(6, 6))
    s4 = rng.uniform(-3.0, 3.0, size=(6, 6))  # some S1 < |S4|
    s1[rng.uniform(size=(6, 6)) < 0.1] = np.nan
    valid = rng.uniform(size=(6, 6)) > 0.3
    result = aggregate_blocks(from_stokes(s1, s4, valid), block_size=3)
    for support in (result.power_support, result.ratio_support):
        invalid = sum(support.n_invalid_by_reason.values(), np.zeros((2, 2), dtype=np.int64))
        np.testing.assert_array_equal(
            support.n_valid + support.n_missing + invalid, np.full((2, 2), 9)
        )
        np.testing.assert_array_equal(support.valid_fraction, support.n_valid / 9)
        assert all(count.min() >= 0 for count in support.n_invalid_by_reason.values())


def test_block_size_is_a_required_argument_in_pixels() -> None:
    parameter = inspect.signature(aggregate_blocks).parameters["block_size"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    doc = inspect.getdoc(aggregate_blocks)
    assert doc is not None
    assert "pixels" in doc
    s1 = np.full((4, 4), 2.0)
    s4 = np.zeros((4, 4))
    assert aggregate_blocks(from_stokes(s1, s4), block_size=2).mean_sc.value.shape == (2, 2)
    assert aggregate_blocks(from_stokes(s1, s4), block_size=4).mean_sc.value.shape == (1, 1)


@pytest.mark.parametrize(
    "block_size", [0, -1, (0, 2), (2, -2), True, (True, 2), 2.0, (2,), (2, 2, 2), "4", None]
)
def test_undeclared_block_size_raises(block_size: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        aggregate_blocks(from_stokes(np.full((4, 4), 2.0), np.zeros((4, 4))), block_size=block_size)


def test_pixel_grid_must_divide_into_whole_blocks() -> None:
    derived = from_stokes(np.full((4, 6), 2.0), np.zeros((4, 6)))
    with pytest.raises(ValueError, match="whole blocks"):
        aggregate_blocks(derived, block_size=4)


@pytest.mark.parametrize("shape", [(16,), (2, 2, 4), ()])
def test_pixel_grid_must_be_two_dimensional(shape: tuple[int, ...]) -> None:
    derived = from_stokes(np.full(shape, 2.0), np.zeros(shape))
    with pytest.raises(ValueError, match="two-dimensional"):
        aggregate_blocks(derived, block_size=2)


def test_blocks_are_not_presented_as_independent_samples() -> None:
    result = aggregate_blocks(from_stokes(np.full((4, 4), 2.0), np.zeros((4, 4))), block_size=2)
    assert result.blocks_independent is False
    with pytest.raises(ValueError, match="independent"):
        dataclasses.replace(result, blocks_independent=True)


def test_block_records_are_read_only() -> None:
    result = aggregate_blocks(from_stokes(np.full((4, 4), 2.0), np.zeros((4, 4))), block_size=2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.blocks_independent = True  # type: ignore[misc]
    with pytest.raises(ValueError, match="read-only"):
        result.block_origin[0, 0, 0] = 5
    with pytest.raises(ValueError, match="read-only"):
        result.power_support.n_valid[0, 0] = 5
    reasons: Any = result.power_support.n_invalid_by_reason
    with pytest.raises(TypeError):
        reasons[InvalidReason.NONFINITE] = np.zeros((2, 2))


def test_aggregates_record_the_input_and_its_declarations() -> None:
    stokes = aggregate_blocks(from_stokes(np.full((2, 2), 2.0), np.zeros((2, 2))), block_size=2)
    assert stokes.input_kind == STOKES_INPUT
    assert stokes.convention == SIS
    assert stokes.handedness_verified is None
    covariance = aggregate_blocks(
        from_covariance(np.stack([f6_covariance()] * 4).reshape(2, 2, 3, 3)), block_size=2
    )
    assert covariance.input_kind == COVARIANCE_INPUT
    assert covariance.convention == LL
    assert covariance.handedness_verified is False


# --- Covariance input: powers of the mean covariance (PRD 6.2: aggregate first) ----------------


@pytest.mark.parametrize("convention", [LL, OPP])
def test_covariance_block_statistics_are_the_powers_of_the_mean_covariance(convention: str) -> None:
    second = jones_oracle.covariance_of([(0.6 - 0.2j, 0.1 + 0.3j, -0.4j), (0.2, -0.1j, 0.5)])
    assert_hermitian_psd(second)
    matrices = [f6_covariance(), f7_covariance(0.9), f7_covariance(0.1), second]
    covariance = np.stack(matrices).reshape(2, 2, 3, 3)
    result = aggregate_blocks(from_covariance(covariance, convention=convention), block_size=2)
    mean = expanded_oracle.expanded_from_matrix(np.mean(np.stack(matrices), axis=0))
    sc = mean.sc_ll if convention == LL else mean.sc_opp
    assert_value(result.mean_sc, sc)
    assert_value(result.mean_oc, mean.oc)
    assert_value(result.ratio_of_mean_powers, sc / mean.oc)
    pixelwise = [
        expanded_oracle.expanded_from_matrix(m).cpr_ll
        if convention == LL
        else expanded_oracle.expanded_from_matrix(m).cpr_opp
        for m in matrices
    ]
    assert_value(result.mean_of_pixelwise_ratios, math.fsum(pixelwise) / 4)


# --- Linear power only; dB for display (PRD 6.2, 7) -------------------------------------------


def test_db_helper_is_for_display_and_tags_its_values() -> None:
    doc = inspect.getdoc(to_db_for_display)
    assert doc is not None
    assert "display" in doc
    shown = to_db_for_display(stated([100.0, 0.5, 1.0]))
    assert type(shown) is DecibelArray
    np.testing.assert_allclose(shown.db.value, [20.0, 10 * math.log10(0.5), 0.0], rtol=RTOL)


def test_db_of_a_zero_or_negative_value_is_unavailable_not_minus_infinity() -> None:
    shown = to_db_for_display(stated([0.0, -1e-13]))
    assert_unavailable(shown.db, INVALID, InvalidReason.DB_OF_NONPOSITIVE)


def test_db_keeps_missing_and_invalid_states() -> None:
    result = from_stokes([2.0, 1.0, 2.0], [0.0, 2.0, 0.0], [True, True, False])
    shown = to_db_for_display(result.cpr)
    assert shown.db.state.tolist() == [VALID, INVALID, MISSING]
    assert shown.db.reason.tolist() == ["NONE", "STOKES_S1_LT_ABS_S4", "PRODUCT_MASKED"]
    assert shown.db.value[0] == 0.0


def test_db_summary_of_a_block_statistic() -> None:
    pixels = {(0, 0): sc_oc(1.0, 1.0), (2, 3): sc_oc(1.0, 3.0)}
    s1, s4, valid = stokes_block(pixels)
    result = aggregate_blocks(from_stokes(s1, s4, valid), block_size=4)
    shown = to_db_for_display(result.ratio_of_mean_powers)
    np.testing.assert_allclose(single(shown.db.value), 10 * math.log10(0.5), rtol=RTOL)


@pytest.mark.parametrize("values", [np.array([1.0]), [1.0], 1.0])
def test_db_helper_takes_only_stated_values(values: Any) -> None:
    with pytest.raises(TypeError):
        to_db_for_display(values)


def test_db_tagged_values_cannot_be_aggregated_or_used_as_powers() -> None:
    derived = from_stokes(np.full((2, 2), 2.0), np.zeros((2, 2)))
    shown: Any = to_db_for_display(derived.sc)
    with pytest.raises(TypeError, match="dB"):
        aggregate_blocks(shown, block_size=2)
    with pytest.raises(TypeError):
        dataclasses.replace(derived, sc=shown)
    with pytest.raises(TypeError):
        CircularPowers(
            total=derived.total,
            sc=shown,
            oc=derived.oc,
            cpr=derived.cpr,
            convention=SIS,
            tolerance=ARITHMETIC_TOLERANCE,
        )
    with pytest.raises(TypeError):
        from_stokes(shown, np.zeros((2, 2)))


def test_amplitude_dn_and_bare_arrays_cannot_be_aggregated() -> None:
    dn: Any = np.array([[100.0, 300.0], [100.0, 300.0]])
    with pytest.raises(TypeError):
        aggregate_blocks(dn, block_size=2)
    power: Any = decode_linear(dn, nodata=None)
    with pytest.raises(TypeError):
        aggregate_blocks(power, block_size=2)
    bare: Any = dn.tolist()
    with pytest.raises(TypeError):
        aggregate_blocks(bare, block_size=2)


# --- float64 range ----------------------------------------------------------------------------


def test_block_means_of_huge_powers_do_not_overflow() -> None:
    s1 = np.full((4, 4), 1.7e308)
    s4 = np.full((4, 4), 0.5e308)
    result = aggregate_blocks(from_stokes(s1, s4), block_size=4)
    assert_value(result.mean_sc, 0.6e308)
    assert_value(result.mean_oc, 1.1e308)
    assert_value(result.ratio_of_mean_powers, 0.6 / 1.1)
    assert_value(result.mean_of_pixelwise_ratios, 0.6 / 1.1)


def test_block_mean_below_the_smallest_normal_is_out_of_range_not_zero() -> None:
    sc = np.zeros((2, 2))
    oc = np.zeros((2, 2))
    sc[0, 0] = oc[0, 0] = SMALLEST_NORMAL
    result = aggregate_blocks(power_record(sc, oc), block_size=2)
    for name in ("mean_sc", "mean_oc", "ratio_of_mean_powers"):
        assert_unavailable(getattr(result, name), INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)
    assert_value(result.mean_of_pixelwise_ratios, 1.0)


def test_block_ratio_that_overflows_is_out_of_range() -> None:
    result = aggregate_blocks(power_record([[1e300, 0.0]], [[1e-300, 0.0]]), block_size=(1, 2))
    assert_value(result.mean_sc, 0.5e300)
    assert_value(result.mean_oc, 0.5e-300)
    assert_unavailable(result.ratio_of_mean_powers, INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)


def test_powers_far_apart_in_magnitude_keep_their_ratio() -> None:
    """SC near 1e-150 and OC near 1e150 give a representable ratio near 1e-300, so each power
    keeps its own scale and neither is lost at the other's."""
    result = aggregate_blocks(power_record([[1e-150, 3e-150]], [[1e150, 3e150]]), block_size=(1, 2))
    assert_value(result.mean_sc, 2e-150)
    assert_value(result.mean_oc, 2e150)
    np.testing.assert_allclose(single(result.ratio_of_mean_powers.value), 1e-300, rtol=RTOL)
    np.testing.assert_allclose(single(result.mean_of_pixelwise_ratios.value), 1e-300, rtol=RTOL)


# --- Properties --------------------------------------------------------------------------------

stokes_pixel = st.tuples(
    st.floats(min_value=1e-3, max_value=1e3),
    st.one_of(st.just(-1.0), st.just(1.0), st.just(0.0), st.floats(-0.9, 0.9)),
    st.booleans(),
)
"""S1, S4 / S1 and the validity flag of one pixel; |S4| <= S1, so every unmasked pixel has valid
powers. S4 = +-S1 gives SC = 0 or OC = 0 exactly."""


def stokes_arrays(
    pixels: list[tuple[float, float, bool]],
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.bool_]]:
    s1 = np.array([p[0] for p in pixels])
    s4 = np.array([p[0] * p[1] for p in pixels])
    valid = np.array([p[2] for p in pixels])
    return s1, s4, valid


@settings(max_examples=200, deadline=None)
@given(st.lists(stokes_pixel, min_size=16, max_size=16), st.permutations(list(range(16))))
def test_permuting_pixels_within_a_block_leaves_the_statistics_unchanged(
    pixels: list[tuple[float, float, bool]], order: list[int]
) -> None:
    s1, s4, valid = stokes_arrays(pixels)
    index = np.array(order)
    before = aggregate_blocks(
        from_stokes(s1.reshape(4, 4), s4.reshape(4, 4), valid.reshape(4, 4)), block_size=4
    )
    after = aggregate_blocks(
        from_stokes(s1[index].reshape(4, 4), s4[index].reshape(4, 4), valid[index].reshape(4, 4)),
        block_size=4,
    )
    for name in STATISTICS:
        assert_same(getattr(after, name), getattr(before, name))
    for support in ("power_support", "ratio_support"):
        a, b = getattr(after, support), getattr(before, support)
        np.testing.assert_array_equal(a.n_valid, b.n_valid)
        np.testing.assert_array_equal(a.n_missing, b.n_missing)
        assert invalid_counts(a) == invalid_counts(b)


positive_power = st.floats(min_value=1e-100, max_value=1e100)


@settings(max_examples=200, deadline=None)
@given(
    st.lists(st.tuples(positive_power, positive_power), min_size=4, max_size=4),
    st.floats(min_value=1e-100, max_value=1e100),
)
def test_scaling_sc_and_oc_by_one_factor_leaves_the_cpr_statistics_unchanged(
    pairs: list[tuple[float, float]], factor: float
) -> None:
    sc = np.array([p[0] for p in pairs]).reshape(2, 2)
    oc = np.array([p[1] for p in pairs]).reshape(2, 2)
    before = aggregate_blocks(power_record(sc, oc), block_size=2)
    after = aggregate_blocks(power_record(sc * factor, oc * factor), block_size=2)
    for name in RATIO_STATISTICS:
        assert_same(getattr(after, name), getattr(before, name))
    np.testing.assert_allclose(after.mean_sc.value, before.mean_sc.value * factor, rtol=RTOL)


@settings(max_examples=100, deadline=None)
@given(
    st.lists(stokes_pixel, min_size=16, max_size=16),
    st.integers(min_value=-900, max_value=900),
)
def test_scaling_stokes_by_a_power_of_two_leaves_the_cpr_statistics_unchanged(
    pixels: list[tuple[float, float, bool]], exponent: int
) -> None:
    s1, s4, valid = stokes_arrays(pixels)
    shape = (4, 4)
    before = aggregate_blocks(
        from_stokes(s1.reshape(shape), s4.reshape(shape), valid.reshape(shape)), block_size=2
    )
    after = aggregate_blocks(
        from_stokes(
            np.ldexp(s1, exponent).reshape(shape),
            np.ldexp(s4, exponent).reshape(shape),
            valid.reshape(shape),
        ),
        block_size=2,
    )
    for name in RATIO_STATISTICS:
        assert_same(getattr(after, name), getattr(before, name))


def check_exact(result: StatedArray, exact: Fraction) -> None:
    """A one-block statistic against its exact value, outside the declared rounding bands."""
    value = Fraction(SMALLEST_NORMAL)
    assume(not (value * Fraction(1 - 1e-9) <= exact <= value * Fraction(1 + 1e-9)))
    assume(exact <= Fraction(FLOAT64_MAX) * Fraction(1 - 1e-9))
    if exact != 0 and exact < value:
        assert_unavailable(result, INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)
        return
    assert result.state[0, 0] == VALID
    error = abs(Fraction(float(result.value[0, 0])) - exact)
    assert error <= Fraction(RTOL) * exact


any_power = st.floats(min_value=0.0, max_value=FLOAT64_MAX)


@settings(max_examples=300, deadline=None)
@given(st.lists(st.tuples(any_power, any_power), min_size=4, max_size=4))
def test_block_statistics_match_an_exact_oracle_over_the_float64_range(
    pairs: list[tuple[float, float]],
) -> None:
    sc = np.array([p[0] for p in pairs]).reshape(2, 2)
    oc = np.array([p[1] for p in pairs]).reshape(2, 2)
    record = power_record(sc, oc)
    result = aggregate_blocks(record, block_size=2)
    sum_sc = sum((Fraction(x) for x in sc.flat), Fraction(0))
    sum_oc = sum((Fraction(x) for x in oc.flat), Fraction(0))
    check_exact(result.mean_sc, sum_sc / 4)
    check_exact(result.mean_oc, sum_oc / 4)

    smallest = Fraction(SMALLEST_NORMAL)
    means_out_of_range = any(0 < s / 4 < smallest for s in (sum_sc, sum_oc))
    if sum_oc == 0:
        assert_unavailable(result.ratio_of_mean_powers, INVALID, InvalidReason.OC_NONPOSITIVE)
    elif means_out_of_range:
        assert_unavailable(result.ratio_of_mean_powers, INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)
    else:
        exact_ratio = sum_sc / sum_oc
        if exact_ratio > Fraction(FLOAT64_MAX):
            assume(exact_ratio > Fraction(FLOAT64_MAX) * Fraction(1 + 1e-9))
            assert_unavailable(
                result.ratio_of_mean_powers, INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE
            )
        else:
            check_exact(result.ratio_of_mean_powers, exact_ratio)

    cpr_valid = record.cpr.state == VALID
    cprs = [Fraction(float(x)) for x in record.cpr.value[cpr_valid]]
    if not cprs:
        assert_unavailable(
            result.mean_of_pixelwise_ratios, INVALID, InvalidReason.BLOCK_NO_VALID_PIXELS
        )
    else:
        check_exact(result.mean_of_pixelwise_ratios, sum(cprs, Fraction(0)) / len(cprs))
