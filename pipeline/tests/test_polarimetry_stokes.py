"""Mini-RF Stokes parameters to SC, OC and CPR with validity states (PRD 6.2, 7, F1-F5).

Fixture values come from PRD 7 (Fixtures). Comparisons use the PRD 7 arithmetic tolerances on
float64: rtol = 1e-10, atol = 1e-12. The same values are the Stokes validity tolerance in these
tests; PRD 7 gives no numeric Stokes tolerance for real data, which stays an open decision.
"""

from __future__ import annotations

import inspect
import math
from fractions import Fraction
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from terraluna_pipeline.polarimetry import (
    ARITHMETIC_TOLERANCE,
    MINIRF_SIS_CONVENTION,
    REASON_DTYPE,
    STATE_DTYPE,
    CircularPowers,
    InvalidReason,
    ObsState,
    StatedArray,
    Tolerance,
    circular_from_stokes,
)

RTOL = 1e-10
ATOL = 1e-12

MISSING = ObsState.MISSING
INVALID = ObsState.INVALID
VALID = ObsState.VALID

SMALLEST_NORMAL = float(np.finfo(np.float64).smallest_normal)
FLOAT64_MAX = float(np.finfo(np.float64).max)


def convert(
    s1: npt.ArrayLike,
    s4: npt.ArrayLike,
    *,
    s2: npt.ArrayLike | None = None,
    s3: npt.ArrayLike | None = None,
    masked: npt.ArrayLike = False,
    tolerance: Tolerance = ARITHMETIC_TOLERANCE,
) -> CircularPowers:
    """Convert under the declared SIS convention; a test helper, not a production default."""
    return circular_from_stokes(
        s1,
        s4,
        s2=s2,
        s3=s3,
        convention=MINIRF_SIS_CONVENTION,
        product_masked=masked,
        tolerance=tolerance,
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


# --- Vocabulary -------------------------------------------------------------------------------


def test_arithmetic_tolerance_is_the_prd_value() -> None:
    assert Tolerance(rtol=1e-10, atol=1e-12) == ARITHMETIC_TOLERANCE


def test_tolerance_defaults_to_the_arithmetic_tolerance_and_is_named() -> None:
    parameter = inspect.signature(circular_from_stokes).parameters["tolerance"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default == ARITHMETIC_TOLERANCE


def test_stokes_reason_codes_exist() -> None:
    codes = {r.value for r in InvalidReason}
    assert {
        "STOKES_S1_LT_ABS_S4",
        "STOKES_NONPHYSICAL",
        "OC_NONPOSITIVE",
        "SC_NEGATIVE",
        "PRODUCT_MASKED",
        "NONFINITE",
    } <= codes


def test_sis_convention_is_a_named_string_constant() -> None:
    assert isinstance(MINIRF_SIS_CONVENTION, str)
    assert MINIRF_SIS_CONVENTION
    assert convert(2.0, 2.0).convention == MINIRF_SIS_CONVENTION


def test_docstring_states_the_power_definition() -> None:
    doc = inspect.getdoc(circular_from_stokes)
    assert doc is not None
    assert "factor 2" in doc
    assert "label" in doc


# --- Fixtures F1-F5 (PRD 7) -------------------------------------------------------------------


def test_F1_minirf_zero_cpr_is_valid_not_missing() -> None:
    result = convert(2.0, 2.0)
    assert_value(result.sc, 0.0)
    assert_value(result.oc, 2.0)
    assert_value(result.cpr, 0.0)
    # A valid low CPR is a number with state VALID, distinct from a missing observation.
    assert result.cpr.state == VALID
    assert result.cpr.value == 0.0


def test_F2_minirf_unit_cpr() -> None:
    result = convert(2.0, 0.0)
    assert_value(result.sc, 1.0)
    assert_value(result.oc, 1.0)
    assert_value(result.cpr, 1.0)


def test_F3_valid_cpr_above_the_old_bound_is_not_truncated() -> None:
    result = convert(4.0, -2.0)
    assert_value(result.sc, 3.0)
    assert_value(result.oc, 1.0)
    assert_value(result.cpr, 3.0)
    assert result.cpr.value != 1.5
    assert result.cpr.value > 1.5


def test_F4_invalid_stokes_pair_publishes_no_ratio() -> None:
    result = convert(1.0, 2.0)
    for field in (result.total, result.sc, result.oc, result.cpr):
        assert_unavailable(field, INVALID, InvalidReason.STOKES_S1_LT_ABS_S4)


def test_F5_zero_opposite_sense_power_leaves_cpr_unavailable() -> None:
    result = convert(2.0, -2.0)
    assert_value(result.sc, 2.0)
    assert_value(result.oc, 0.0)
    # Not clipped, not regularized: no finite or huge CPR, only the state and the reason.
    assert_unavailable(result.cpr, INVALID, InvalidReason.OC_NONPOSITIVE)


# --- Total power beside CPR (PRD 6.2: SC, OC and total power are kept) ------------------------


@pytest.mark.parametrize(
    ("s1", "s4"), [(2.0, 2.0), (2.0, 0.0), (4.0, -2.0), (2.0, -2.0)], ids=["F1", "F2", "F3", "F5"]
)
def test_total_power_is_s1_and_kept_beside_cpr(s1: float, s4: float) -> None:
    result = convert(s1, s4)
    assert_value(result.total, s1)
    np.testing.assert_allclose(result.sc.value + result.oc.value, s1, rtol=RTOL, atol=ATOL)


def test_total_power_is_kept_where_cpr_is_unavailable() -> None:
    result = convert(2.0, -2.0)  # F5: OC = 0
    assert result.cpr.state == INVALID
    assert_value(result.total, 2.0)


def test_total_power_below_the_smallest_normal_float64_is_out_of_range() -> None:
    result = convert(1e-310, 0.0)
    assert_unavailable(result.total, INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)


def test_zero_total_power_is_a_valid_zero() -> None:
    result = convert(0.0, 0.0)
    assert_value(result.total, 0.0)
    assert_unavailable(result.cpr, INVALID, InvalidReason.OC_NONPOSITIVE)


# --- Formulas ---------------------------------------------------------------------------------


def test_high_cpr_is_never_truncated() -> None:
    # PRD 7: lunar CPR reaches 2 to 3; nothing caps it at 1.5, 2 or 3.
    result = convert(10.0, -8.0)
    assert_value(result.sc, 9.0)
    assert_value(result.oc, 1.0)
    assert_value(result.cpr, 9.0)


def test_near_zero_oc_gets_no_epsilon() -> None:
    # OC = 2**-51 exactly; CPR = SC / OC = 2**52 - 1 exactly, with nothing added to OC.
    result = convert(2.0, -(2.0 - 2.0**-50))
    assert result.oc.value == 2.0**-51
    assert result.sc.value == 2.0 - 2.0**-51
    assert result.cpr.state == VALID
    assert result.cpr.value == 2.0**52 - 1.0


def test_cpr_is_sc_over_oc_not_the_share_of_total_echo() -> None:
    result = convert(4.0, -2.0)
    assert result.cpr.value == 3.0
    share = result.cpr.value / (1.0 + result.cpr.value)
    np.testing.assert_allclose(share, result.sc.value / 4.0, rtol=RTOL, atol=ATOL)
    assert share != result.cpr.value


def test_integer_stokes_inputs_are_converted_as_float64() -> None:
    result = convert(np.array([4], dtype=np.int64), np.array([-2], dtype=np.int64))
    assert result.cpr.value.dtype == np.float64
    assert_value(result.cpr, 3.0)


def test_sums_that_would_overflow_float64_are_not_formed() -> None:
    # (S1 + S4) / 2 overflows for these inputs; S1/2 + S4/2 does not.
    result = convert(1.5e308, 1.0e308)
    assert_value(result.oc, 1.25e308)
    assert_value(result.sc, 0.25e308)
    assert_value(result.cpr, 0.2)


def test_extreme_opposite_stokes_give_valid_powers_and_no_ratio() -> None:
    result = convert(FLOAT64_MAX, -FLOAT64_MAX)
    assert result.sc.state == VALID
    assert result.sc.value == FLOAT64_MAX
    assert_value(result.oc, 0.0)
    assert_unavailable(result.cpr, INVALID, InvalidReason.OC_NONPOSITIVE)


def test_powers_below_the_smallest_normal_float64_are_out_of_range() -> None:
    # SC = 2**-1053 is positive but subnormal; OC is a normal float64.
    s1 = 2.0**-1000
    s4 = s1 - 2.0**-1052
    result = convert(s1, s4)
    assert_unavailable(result.sc, INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)
    assert result.oc.state == VALID
    assert_unavailable(result.cpr, INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)


def test_positive_oc_that_underflows_is_out_of_range_not_nonpositive() -> None:
    # OC = 2**-1075 rounds to zero in float64, but it is positive, not a zero echo.
    result = convert(2.0**-1074, 0.0)
    assert_unavailable(result.oc, INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)
    assert_unavailable(result.cpr, INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)


def test_exact_zero_sc_stays_a_valid_zero_at_any_magnitude() -> None:
    result = convert(2.0**-1000, 2.0**-1000)
    assert_value(result.sc, 0.0)
    assert_value(result.cpr, 0.0)


# --- Convention -------------------------------------------------------------------------------


def test_convention_is_a_required_argument() -> None:
    parameter = inspect.signature(circular_from_stokes).parameters["convention"]
    assert parameter.default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        circular_from_stokes(2.0, 2.0, s2=None, s3=None, product_masked=False)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "convention",
    [
        None,
        "",
        "S4 = SC - OC",  # a second, undefined sign convention
        "MINIRF_SIS_S4_SC_MINUS_OC",
        "minirf_sis",
        "SIS",
    ],
)
def test_undeclared_or_unknown_convention_raises(convention: object) -> None:
    with pytest.raises(ValueError, match="convention"):
        circular_from_stokes(
            2.0,
            2.0,
            s2=None,
            s3=None,
            convention=convention,  # type: ignore[arg-type]
            product_masked=False,
        )


def test_constant_with_extra_text_is_not_the_declared_convention() -> None:
    for variant in (MINIRF_SIS_CONVENTION + " ", MINIRF_SIS_CONVENTION.lower()):
        with pytest.raises(ValueError, match="convention"):
            circular_from_stokes(
                2.0,
                2.0,
                s2=None,
                s3=None,
                convention=variant,  # type: ignore[arg-type]
                product_masked=False,
            )


def test_opposite_sign_convention_is_refused_not_reinterpreted() -> None:
    # Under S4 = SC - OC, F3's inputs would mean CPR = 1/3. The module knows only the SIS
    # convention, so it refuses rather than guessing which sign the product uses.
    with pytest.raises(ValueError, match="convention"):
        circular_from_stokes(
            4.0,
            -2.0,
            s2=None,
            s3=None,
            convention="S4 = SC - OC",  # type: ignore[arg-type]
            product_masked=False,
        )


# --- Validity: S1 >= |S4| within the tolerance ------------------------------------------------


def test_s4_above_s1_within_tolerance_is_accepted_and_not_clipped() -> None:
    result = convert(1.0, 1.0 + 5e-11)
    assert result.sc.state == VALID
    assert result.sc.value < 0.0  # reported as computed, not clipped to zero
    np.testing.assert_allclose(result.sc.value, -2.5e-11, rtol=1e-5, atol=0.0)
    assert result.oc.state == VALID
    # No ratio is formed from a negative power, and none is clipped to a valid zero.
    assert_unavailable(result.cpr, INVALID, InvalidReason.SC_NEGATIVE)


def test_negative_sc_within_tolerance_never_gives_a_negative_cpr() -> None:
    # The absolute tolerance accepts S1 = 0, S4 = 5e-13; SC/OC would be -1.
    result = convert(0.0, 5e-13)
    assert result.sc.state == VALID
    assert result.oc.state == VALID
    assert_unavailable(result.cpr, INVALID, InvalidReason.SC_NEGATIVE)


def test_s4_above_s1_beyond_tolerance_is_invalid() -> None:
    result = convert(1.0, 1.0 + 1e-9)
    for field in (result.total, result.sc, result.oc, result.cpr):
        assert_unavailable(field, INVALID, InvalidReason.STOKES_S1_LT_ABS_S4)


def test_s4_below_minus_s1_within_tolerance_gives_nonpositive_oc() -> None:
    result = convert(1.0, -(1.0 + 5e-11))
    assert result.sc.state == VALID
    assert result.oc.state == VALID
    assert result.oc.value < 0.0
    assert_unavailable(result.cpr, INVALID, InvalidReason.OC_NONPOSITIVE)


def test_s4_below_minus_s1_beyond_tolerance_is_invalid() -> None:
    result = convert(1.0, -(1.0 + 1e-9))
    for field in (result.total, result.sc, result.oc, result.cpr):
        assert_unavailable(field, INVALID, InvalidReason.STOKES_S1_LT_ABS_S4)


def test_absolute_tolerance_applies_near_zero() -> None:
    assert convert(0.0, 5e-13).sc.state == VALID
    assert convert(0.0, 2e-12).sc.reason == InvalidReason.STOKES_S1_LT_ABS_S4


def test_negative_s1_beyond_tolerance_is_invalid() -> None:
    result = convert(-1.0, 0.0)
    assert_unavailable(result.cpr, INVALID, InvalidReason.STOKES_S1_LT_ABS_S4)


def test_tolerance_argument_decides_the_outcome() -> None:
    loose = Tolerance(rtol=1e-8, atol=0.0)
    exact = Tolerance(rtol=0.0, atol=0.0)
    assert convert(1.0, 1.0 + 1e-9, tolerance=loose).sc.state == VALID
    assert convert(1.0, 1.0 + 5e-11, tolerance=exact).sc.reason == (
        InvalidReason.STOKES_S1_LT_ABS_S4
    )
    assert convert(1.0, 1.0, tolerance=exact).cpr.state == VALID


def test_opposite_extremes_are_invalid_without_overflow_passing_them() -> None:
    # |S4| - S1 overflows to +inf here, which must count as beyond the tolerance.
    result = convert(-FLOAT64_MAX, FLOAT64_MAX)
    assert_unavailable(result.cpr, INVALID, InvalidReason.STOKES_S1_LT_ABS_S4)


@pytest.mark.parametrize(
    ("rtol", "atol"),
    [(-1e-10, 1e-12), (1e-10, -1e-12), (math.nan, 1e-12), (1e-10, math.inf)],
)
def test_tolerance_must_be_finite_and_nonnegative(rtol: float, atol: float) -> None:
    with pytest.raises(ValueError):
        Tolerance(rtol=rtol, atol=atol)


@pytest.mark.parametrize(("rtol", "atol"), [(True, 1e-12), (1e-10, "0"), (None, 1e-12)])
def test_tolerance_must_be_real_numbers(rtol: object, atol: object) -> None:
    with pytest.raises(TypeError):
        Tolerance(rtol=rtol, atol=atol)  # type: ignore[arg-type]


def test_tolerance_argument_must_be_a_tolerance() -> None:
    with pytest.raises(TypeError):
        convert(2.0, 2.0, tolerance=1e-10)  # type: ignore[arg-type]


# --- Validity: all four Stokes parameters -----------------------------------------------------


def test_full_stokes_beyond_the_polarization_bound_is_nonphysical() -> None:
    # S1^2 = 1 < S2^2 + S3^2 + S4^2 = 1.28.
    result = convert(1.0, 0.0, s2=0.8, s3=0.8)
    for field in (result.total, result.sc, result.oc, result.cpr):
        assert_unavailable(field, INVALID, InvalidReason.STOKES_NONPHYSICAL)


def test_fully_polarized_full_stokes_is_valid() -> None:
    result = convert(1.0, 0.0, s2=0.6, s3=0.8)
    assert_value(result.sc, 0.5)
    assert_value(result.oc, 0.5)
    assert_value(result.cpr, 1.0)


def test_full_stokes_within_tolerance_is_valid_and_beyond_is_nonphysical() -> None:
    assert convert(1.0, 0.0, s2=1.0 + 5e-11, s3=0.0).cpr.state == VALID
    beyond = convert(1.0, 0.0, s2=1.0 + 1e-9, s3=0.0)
    assert_unavailable(beyond.cpr, INVALID, InvalidReason.STOKES_NONPHYSICAL)


def test_s1_below_abs_s4_is_reported_before_the_full_bound() -> None:
    result = convert(1.0, 2.0, s2=0.0, s3=0.0)
    assert_unavailable(result.cpr, INVALID, InvalidReason.STOKES_S1_LT_ABS_S4)


def test_full_stokes_bound_does_not_overflow() -> None:
    # Squaring these overflows float64; the bound is still decided correctly.
    inside = convert(1.7e308, 0.5e308, s2=1.0e308, s3=1.0e308)
    assert inside.sc.state == VALID
    outside = convert(1.0e308, 1.0e308, s2=1.0e308, s3=1.0e308)
    assert_unavailable(outside.cpr, INVALID, InvalidReason.STOKES_NONPHYSICAL)
    extreme = convert(FLOAT64_MAX, FLOAT64_MAX, s2=FLOAT64_MAX, s3=FLOAT64_MAX)
    assert_unavailable(extreme.cpr, INVALID, InvalidReason.STOKES_NONPHYSICAL)


# The tolerance applies to the squared inequality itself (S2^2 + S3^2 + S4^2 may exceed S1^2 by
# at most atol + rtol * S1^2), not to the polarization norm. On the norm the relative part
# would be about twice as loose, since a relative norm excess d is a squared excess of 2d.


@pytest.mark.parametrize("excess", [7.5e-11, 8e-11])
def test_squared_excess_beyond_the_tolerance_is_nonphysical(excess: float) -> None:
    # S1 = 1, S2 = 1 + d: the squared excess 2d + d^2 exceeds 1.01e-10 though the norm excess d
    # does not (codex-r1-1, codex2-r1-1).
    result = convert(1.0, 0.0, s2=1.0 + excess, s3=0.0)
    for field in (result.total, result.sc, result.oc, result.cpr):
        assert_unavailable(field, INVALID, InvalidReason.STOKES_NONPHYSICAL)


def test_squared_excess_within_the_tolerance_is_valid() -> None:
    result = convert(1.0, 0.0, s2=1.0 + 4e-11, s3=0.0)
    assert_value(result.total, 1.0)
    assert_value(result.cpr, 1.0)


@pytest.mark.parametrize("position", ["s2", "s3", "s4"])
def test_squared_tolerance_is_the_same_for_each_polarized_component(position: str) -> None:
    beyond = {"s2": 0.0, "s3": 0.0, "s4": 0.0, position: 1.0 + 7.5e-11}
    result = convert(1.0, beyond["s4"], s2=beyond["s2"], s3=beyond["s3"])
    assert_unavailable(result.total, INVALID, InvalidReason.STOKES_NONPHYSICAL)
    within = {"s2": 0.0, "s3": 0.0, "s4": 0.0, position: 1.0 + 4e-11}
    assert convert(1.0, within["s4"], s2=within["s2"], s3=within["s3"]).total.state == VALID


def test_squared_tolerance_holds_where_squares_overflow() -> None:
    # (1e200)^2 overflows float64; atol is negligible beside rtol * S1^2 here.
    beyond = convert(1e200, 0.0, s2=1e200 * (1.0 + 7.5e-11), s3=0.0)
    assert_unavailable(beyond.total, INVALID, InvalidReason.STOKES_NONPHYSICAL)
    assert convert(1e200, 0.0, s2=1e200 * (1.0 + 4e-11), s3=0.0).total.state == VALID


def test_squared_tolerance_holds_where_squares_underflow() -> None:
    # (1e-200)^2 underflows to 0.0, so squaring directly would accept any S2 here. Without an
    # absolute part the tolerance is scale-free and the relative rule decides.
    relative = Tolerance(rtol=1e-10, atol=0.0)
    beyond = convert(1e-200, 0.0, s2=1e-200 * (1.0 + 7.5e-11), s3=0.0, tolerance=relative)
    assert_unavailable(beyond.total, INVALID, InvalidReason.STOKES_NONPHYSICAL)
    within = convert(1e-200, 0.0, s2=1e-200 * (1.0 + 4e-11), s3=0.0, tolerance=relative)
    assert within.total.state == VALID


def test_absolute_tolerance_of_the_squared_bound_is_in_squared_power_units() -> None:
    # With S1 = 0 the bound is S2^2 <= atol, so atol = 1e-12 admits |S2| up to 1e-6. This is
    # the stated tolerance on the squared inequality; a real-data value is an open decision.
    absolute = Tolerance(rtol=0.0, atol=1e-12)
    assert convert(0.0, 0.0, s2=0.9e-6, s3=0.0, tolerance=absolute).total.state == VALID
    beyond = convert(0.0, 0.0, s2=1.1e-6, s3=0.0, tolerance=absolute)
    assert_unavailable(beyond.total, INVALID, InvalidReason.STOKES_NONPHYSICAL)


def test_exact_squared_bound_with_zero_tolerance() -> None:
    exact = Tolerance(rtol=0.0, atol=0.0)
    assert convert(0.0, 0.0, s2=0.0, s3=0.0, tolerance=exact).total.state == VALID
    assert convert(1.0, 0.0, s2=1.0, s3=0.0, tolerance=exact).total.state == VALID
    beyond = convert(1.0, 0.0, s2=1.0 + 2.0**-52, s3=0.0, tolerance=exact)
    assert_unavailable(beyond.total, INVALID, InvalidReason.STOKES_NONPHYSICAL)


@pytest.mark.parametrize("given_only", ["s2", "s3"])
def test_s2_and_s3_are_given_together_or_not_at_all(given_only: str) -> None:
    kwargs: dict[str, Any] = {"s2": None, "s3": None, given_only: 0.0}
    with pytest.raises(ValueError, match="s2 and s3"):
        circular_from_stokes(
            1.0, 0.0, convention=MINIRF_SIS_CONVENTION, product_masked=False, **kwargs
        )


def test_s2_and_s3_must_be_declared_explicitly() -> None:
    for name in ("s2", "s3"):
        parameter = inspect.signature(circular_from_stokes).parameters[name]
        assert parameter.default is inspect.Parameter.empty


# --- Validity: product flags and non-finite input ---------------------------------------------


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("position", ["s1", "s4", "s2", "s3"])
def test_nonfinite_input_is_invalid(value: float, position: str) -> None:
    stokes: dict[str, float] = {"s1": 2.0, "s4": 0.0, "s2": 0.0, "s3": 0.0, position: value}
    result = convert(stokes["s1"], stokes["s4"], s2=stokes["s2"], s3=stokes["s3"])
    for field in (result.total, result.sc, result.oc, result.cpr):
        assert_unavailable(field, INVALID, InvalidReason.NONFINITE)


def test_product_masked_pixel_is_missing() -> None:
    result = convert(2.0, 0.0, masked=True)
    for field in (result.total, result.sc, result.oc, result.cpr):
        assert_unavailable(field, MISSING, InvalidReason.PRODUCT_MASKED)


@pytest.mark.parametrize(("s1", "s4"), [(math.nan, 0.0), (1.0, 2.0), (2.0, -2.0)])
def test_product_mask_wins_over_every_other_rule(s1: float, s4: float) -> None:
    result = convert(s1, s4, masked=True)
    for field in (result.total, result.sc, result.oc, result.cpr):
        assert_unavailable(field, MISSING, InvalidReason.PRODUCT_MASKED)


def test_product_masked_is_a_required_argument() -> None:
    parameter = inspect.signature(circular_from_stokes).parameters["product_masked"]
    assert parameter.default is inspect.Parameter.empty


def test_product_mask_must_be_boolean() -> None:
    with pytest.raises(TypeError):
        convert(np.array([2.0, 2.0]), np.array([0.0, 0.0]), masked=np.array([0, 1]))


def test_no_snr_rule_is_hard_coded() -> None:
    # Very low but finite powers stay valid: only the product's flags or a configured rule
    # (PRD 7) may exclude them, and no such argument exists here.
    result = convert(1e-30, 0.5e-30)
    assert_value(result.cpr, 1.0 / 3.0)
    parameters = inspect.signature(circular_from_stokes).parameters
    assert not any("snr" in name.lower() for name in parameters)


@pytest.mark.parametrize(
    "s1", [np.array([2.0 + 0j]), np.array([True]), np.array(["2.0"]), np.array([2.0], dtype=object)]
)
def test_non_real_stokes_are_refused(s1: npt.NDArray[np.generic]) -> None:
    with pytest.raises(TypeError):
        convert(s1, np.array([0.0]))


def test_mismatched_shapes_raise() -> None:
    with pytest.raises(ValueError, match="shape"):
        convert(np.array([2.0, 2.0]), np.array([0.0]))
    with pytest.raises(ValueError, match="shape"):
        convert(np.array([2.0]), np.array([0.0]), s2=np.array([0.0, 0.0]), s3=np.array([0.0]))
    with pytest.raises(ValueError):
        convert(np.array([2.0, 2.0]), np.array([0.0, 0.0]), masked=np.array([True, False, True]))


# --- Arrays -----------------------------------------------------------------------------------

# Pixel layout: F1, F3, F4 on the first row; F5, a NaN and a masked pixel holding F2's inputs on
# the second. The mask, not the values, makes the last pixel MISSING.
ARRAY_S1 = np.array([[2.0, 4.0, 1.0], [2.0, math.nan, 2.0]])
ARRAY_S4 = np.array([[2.0, -2.0, 2.0], [-2.0, 0.0, 0.0]])
ARRAY_MASK = np.array([[False, False, False], [False, False, True]])


def test_array_mixing_fixtures_nan_and_mask() -> None:
    result = convert(ARRAY_S1, ARRAY_S4, masked=ARRAY_MASK)
    for field in (result.total, result.sc, result.oc, result.cpr):
        assert field.value.shape == (2, 3)
        assert field.state.shape == (2, 3)
        assert field.reason.shape == (2, 3)
        assert field.state.dtype == STATE_DTYPE
        assert field.reason.dtype == REASON_DTYPE

    nan = math.nan
    np.testing.assert_allclose(
        result.sc.value, [[0.0, 3.0, nan], [2.0, nan, nan]], rtol=RTOL, atol=ATOL
    )
    np.testing.assert_allclose(
        result.oc.value, [[2.0, 1.0, nan], [0.0, nan, nan]], rtol=RTOL, atol=ATOL
    )
    np.testing.assert_allclose(
        result.cpr.value, [[0.0, 3.0, nan], [nan, nan, nan]], rtol=RTOL, atol=ATOL
    )
    assert result.cpr.state.tolist() == [
        [VALID, VALID, INVALID],
        [INVALID, INVALID, MISSING],
    ]
    assert result.cpr.reason.tolist() == [
        ["NONE", "NONE", "STOKES_S1_LT_ABS_S4"],
        ["OC_NONPOSITIVE", "NONFINITE", "PRODUCT_MASKED"],
    ]
    assert result.sc.state.tolist() == [[VALID, VALID, INVALID], [VALID, INVALID, MISSING]]
    assert np.array_equal(result.sc.state, result.oc.state)
    assert np.array_equal(result.sc.reason, result.oc.reason)
    assert np.array_equal(result.total.state, result.sc.state)
    np.testing.assert_allclose(
        result.total.value, [[2.0, 4.0, nan], [2.0, nan, nan]], rtol=RTOL, atol=ATOL
    )
    # Invalid and missing pixels never hold 0.0 in CPR.
    not_valid = result.cpr.state != VALID
    assert not np.any(result.cpr.value[not_valid] == 0.0)
    assert np.all(np.isnan(result.cpr.value[not_valid]))


def test_unmasked_array_gives_f2_at_the_masked_pixel() -> None:
    result = convert(ARRAY_S1, ARRAY_S4, masked=np.zeros((2, 3), dtype=bool))
    assert result.cpr.state[1, 2] == VALID
    assert result.cpr.value[1, 2] == 1.0


def test_product_mask_broadcasts_to_the_stokes_shape() -> None:
    result = convert(ARRAY_S1, ARRAY_S4, masked=np.array([False, False, True]))
    assert result.cpr.state[0, 2] == MISSING
    assert result.cpr.state[1, 2] == MISSING


def test_results_are_read_only() -> None:
    result = convert(np.array([2.0]), np.array([0.0]))
    with pytest.raises(ValueError):
        result.cpr.value[0] = 0.0


# --- Result record ----------------------------------------------------------------------------


def stated(value: float, state: ObsState, reason: InvalidReason) -> StatedArray:
    return StatedArray(
        value=np.array([value]),
        state=np.array([state], dtype=STATE_DTYPE),
        reason=np.array([reason], dtype=REASON_DTYPE),
    )


def test_record_carries_its_tolerance() -> None:
    loose = Tolerance(rtol=1e-8, atol=0.0)
    assert convert(2.0, 0.0, tolerance=loose).tolerance == loose


def test_record_refuses_a_valid_cpr_without_valid_powers() -> None:
    ok = stated(1.0, VALID, InvalidReason.NONE)
    bad = stated(math.nan, INVALID, InvalidReason.NONFINITE)
    with pytest.raises(ValueError):
        CircularPowers(
            total=ok,
            sc=bad,
            oc=ok,
            cpr=ok,
            convention=MINIRF_SIS_CONVENTION,
            tolerance=ARITHMETIC_TOLERANCE,
        )


def test_record_refuses_mismatched_shapes_and_unknown_conventions() -> None:
    one = stated(1.0, VALID, InvalidReason.NONE)
    two = StatedArray(
        value=np.array([1.0, 1.0]),
        state=np.array([VALID, VALID], dtype=STATE_DTYPE),
        reason=np.array(["NONE", "NONE"], dtype=REASON_DTYPE),
    )
    with pytest.raises(ValueError):
        CircularPowers(
            total=two,
            sc=one,
            oc=one,
            cpr=one,
            convention=MINIRF_SIS_CONVENTION,
            tolerance=ARITHMETIC_TOLERANCE,
        )
    with pytest.raises(ValueError):
        CircularPowers(
            total=one,
            sc=one,
            oc=two,
            cpr=one,
            convention=MINIRF_SIS_CONVENTION,
            tolerance=ARITHMETIC_TOLERANCE,
        )
    with pytest.raises(ValueError):
        CircularPowers(
            total=one,
            sc=one,
            oc=one,
            cpr=one,
            convention="S4 = SC - OC",  # type: ignore[arg-type]
            tolerance=ARITHMETIC_TOLERANCE,
        )


# --- Masked inputs ----------------------------------------------------------------------------
# PRD 7 keeps the product's flags; a numpy mask would be dropped on conversion, so a masked
# array is refused in every array argument and the flag goes in product_masked.

STOKES_ARGUMENTS = ["s1", "s4", "s2", "s3", "product_masked"]


def call_with(argument: str, values: npt.ArrayLike, flag: npt.ArrayLike) -> CircularPowers:
    arguments: dict[str, Any] = {
        "s1": np.array([2.0, 2.0]),
        "s4": np.array([0.0, 0.0]),
        "s2": np.array([0.0, 0.0]),
        "s3": np.array([0.0, 0.0]),
        "product_masked": np.array([False, False]),
    }
    arguments[argument] = flag if argument == "product_masked" else values
    return circular_from_stokes(
        arguments["s1"],
        arguments["s4"],
        s2=arguments["s2"],
        s3=arguments["s3"],
        convention=MINIRF_SIS_CONVENTION,
        product_masked=arguments["product_masked"],
    )


@pytest.mark.parametrize("argument", STOKES_ARGUMENTS)
def test_every_argument_accepts_plain_inputs(argument: str) -> None:
    result = call_with(argument, np.array([0.0, 0.0]), np.array([False, False]))
    assert np.all(result.sc.state == VALID)


@pytest.mark.parametrize("argument", STOKES_ARGUMENTS)
def test_masked_input_is_refused(argument: str) -> None:
    values = np.ma.array([0.0, 0.0], mask=[False, True])
    flag = np.ma.array([False, False], mask=[False, True])
    with pytest.raises(TypeError, match="masked"):
        call_with(argument, values, flag)


@pytest.mark.parametrize("argument", STOKES_ARGUMENTS)
def test_masked_array_nested_in_a_list_is_refused(argument: str) -> None:
    values = [[np.ma.array([0.0, 0.0], mask=[False, True])]]
    flag = [[np.ma.array([False, False], mask=[False, True])]]
    with pytest.raises(TypeError, match="masked"):
        call_with(argument, values, flag)


# --- Properties -------------------------------------------------------------------------------

S1_VALUES = st.floats(min_value=1e-100, max_value=1e100)
S4_FRACTIONS = st.floats(min_value=-1.0, max_value=1.0)


@given(st.lists(st.tuples(S1_VALUES, S4_FRACTIONS), min_size=1, max_size=50))
def test_sc_and_oc_split_the_total_power(pairs: list[tuple[float, float]]) -> None:
    s1 = np.array([p[0] for p in pairs])
    s4 = s1 * np.array([p[1] for p in pairs])  # |S4| <= S1 exactly, by monotone rounding
    result = convert(s1, s4)
    assert np.all(result.sc.state == VALID)
    assert np.all(result.oc.state == VALID)
    assert np.all(result.sc.value >= 0.0)
    assert np.all(result.oc.value >= 0.0)
    np.testing.assert_allclose(result.sc.value + result.oc.value, s1, rtol=RTOL, atol=ATOL)
    assert np.all(result.total.state == VALID)
    assert np.array_equal(result.total.value, s1)

    oc_positive = result.oc.value > 0.0
    assert np.array_equal(result.cpr.state == VALID, oc_positive)
    assert np.all(result.cpr.reason[~oc_positive] == InvalidReason.OC_NONPOSITIVE)
    cpr = result.cpr.value[oc_positive]
    np.testing.assert_allclose(
        cpr / (1.0 + cpr), result.sc.value[oc_positive] / s1[oc_positive], rtol=RTOL, atol=ATOL
    )


ANY_FINITE = st.floats(allow_nan=False, allow_infinity=False)


@given(st.lists(st.tuples(ANY_FINITE, ANY_FINITE), min_size=1, max_size=50))
def test_any_finite_stokes_pair_gives_a_published_state(pairs: list[tuple[float, float]]) -> None:
    s1 = np.array([p[0] for p in pairs])
    s4 = np.array([p[1] for p in pairs])
    result = convert(s1, s4)
    for field in (result.total, result.sc, result.oc, result.cpr):
        valid = field.state == VALID
        assert np.all(np.isfinite(field.value[valid]))
        assert np.all(np.isnan(field.value[~valid]))
    cpr_valid = result.cpr.state == VALID
    assert np.all(result.cpr.value[cpr_valid] >= 0.0)
    assert np.all(result.sc.state[cpr_valid] == VALID)
    assert np.all(result.oc.value[cpr_valid] > 0.0)
    # The Stokes rule matches a direct oracle wherever it decides.
    with np.errstate(over="ignore"):  # |S4| - S1 overflows to +inf only far beyond the bound
        beyond = np.abs(s4) - s1 > ATOL + RTOL * np.abs(s1)
    assert np.all(result.sc.reason[beyond] == InvalidReason.STOKES_S1_LT_ABS_S4)
    assert not np.any(result.sc.reason[~beyond] == InvalidReason.STOKES_S1_LT_ABS_S4)


def _exact_squared_excess(s1: float, s2: float, s3: float, s4: float) -> tuple[Fraction, Fraction]:
    """Return the exact squared excess S2^2 + S3^2 + S4^2 - S1^2 beyond the arithmetic tolerance,
    and the scale of the comparison (the sum of all four squares), in rational arithmetic."""
    p = Fraction(s1) ** 2
    q = Fraction(s2) ** 2 + Fraction(s3) ** 2 + Fraction(s4) ** 2
    allowed = Fraction(ATOL) + Fraction(RTOL) * p
    return q - p - allowed, p + q


@settings(deadline=None)
@given(st.lists(st.tuples(ANY_FINITE, ANY_FINITE, ANY_FINITE, ANY_FINITE), min_size=1, max_size=20))
def test_full_stokes_rule_matches_exact_arithmetic_over_the_float64_range(
    quads: list[tuple[float, float, float, float]],
) -> None:
    s1, s2, s3, s4 = (np.array([q[i] for q in quads]) for i in range(4))
    result = convert(s1, s4, s2=s2, s3=s3)
    for index, quad in enumerate(quads):
        reason = result.total.reason[index]
        if reason == InvalidReason.STOKES_S1_LT_ABS_S4:
            continue  # rule 3 comes first and has its own oracle
        excess, scale = _exact_squared_excess(*quad)
        # Outside a rounding band of 1e-13 of the squares, float64 decides as exact arithmetic.
        if excess > Fraction(1, 10**13) * scale:
            assert reason == InvalidReason.STOKES_NONPHYSICAL, quad
        elif excess < -Fraction(1, 10**13) * scale:
            assert reason != InvalidReason.STOKES_NONPHYSICAL, quad


SCALES = st.tuples(st.floats(min_value=1.0, max_value=9.99), st.integers(-300, 300))
DIRECTIONS = st.tuples(*(st.floats(min_value=-1.0, max_value=1.0) for _ in range(3)))


@given(SCALES, DIRECTIONS)
def test_squared_bound_is_decided_by_rtol_at_every_scale(
    scale: tuple[float, int], direction: tuple[float, float, float]
) -> None:
    # Without atol the rule is scale-free: a squared excess of 0.5 * rtol * S1^2 is accepted
    # and one of 1.5 * rtol * S1^2 is not, at any magnitude, though squares overflow or
    # underflow. A norm-based rule would accept both.
    norm = math.sqrt(sum(u * u for u in direction))
    assume(norm >= 0.1)
    s1 = scale[0] * 10.0 ** scale[1]
    relative = Tolerance(rtol=1e-10, atol=0.0)
    for factor, accepted in ((0.5, True), (1.5, False)):
        length = s1 * math.sqrt(1.0 + factor * relative.rtol) / norm
        s2, s3, s4 = (length * u for u in direction)
        result = convert(s1, s4, s2=s2, s3=s3, tolerance=relative)
        if accepted:
            assert result.total.state == VALID
        else:
            assert_unavailable(result.total, INVALID, InvalidReason.STOKES_NONPHYSICAL)
