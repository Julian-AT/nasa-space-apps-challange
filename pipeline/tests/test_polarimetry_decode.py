"""PALSAR-2 decoding, angle conversion and the validity vocabulary (PRD 7, F8-F10).

Fixture values come from PRD 7 (Fixtures). Comparisons use the PRD 7 arithmetic tolerances on
float64: rtol = 1e-10, atol = 1e-12.
"""

from __future__ import annotations

import inspect
import math
import re
from collections.abc import Callable

import numpy as np
import numpy.typing as npt
import pytest
from hypothesis import given
from hypothesis import strategies as st

from terraluna_pipeline.polarimetry import (
    INTERNAL_ANGLE_UNIT,
    PALSAR2_CALIBRATION_OFFSET_DB,
    REASON_DTYPE,
    STATE_DTYPE,
    InvalidReason,
    ObsState,
    StatedArray,
    angle_to_degrees,
    decode_db,
    decode_linear,
    x_power,
)

RTOL = 1e-10
ATOL = 1e-12

MISSING = ObsState.MISSING
INVALID = ObsState.INVALID
VALID = ObsState.VALID

# An arbitrary declared nodata DN, used only to show that a declared value is honoured.
DECLARED_NODATA = 65535

Decode = Callable[..., StatedArray]


def assert_unavailable(result: StatedArray, state: ObsState, reason: InvalidReason) -> None:
    """The element exists only as a state and a reason: no finite number, no -inf, no 0.0."""
    assert np.all(result.state == state)
    assert np.all(result.reason == reason)
    assert np.all(np.isnan(result.value))


# --- Vocabulary -------------------------------------------------------------------------------


def test_obs_state_has_exactly_three_distinct_states() -> None:
    assert [s.name for s in ObsState] == ["MISSING", "INVALID", "VALID"]
    assert len({int(s) for s in ObsState}) == 3


def test_no_state_is_encoded_as_zero() -> None:
    # A zero-filled state array must never read as a legal state.
    assert all(int(s) != 0 for s in ObsState)
    codes = [int(s) for s in ObsState]
    assert np.array(codes, dtype=STATE_DTYPE).tolist() == codes


def test_invalid_reasons_are_machine_readable_string_codes() -> None:
    codes = [r.value for r in InvalidReason]
    assert len(set(codes)) == len(codes)
    for reason in InvalidReason:
        assert isinstance(reason.value, str)
        assert reason.value == reason.name
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", reason.value)
        assert len(reason.value) <= REASON_DTYPE.itemsize // np.dtype("<U1").itemsize
    assert InvalidReason.NONE in InvalidReason


def test_stated_array_rejects_nan_for_a_valid_element() -> None:
    with pytest.raises(ValueError):
        StatedArray(
            value=np.array([np.nan]),
            state=np.array([VALID], dtype=STATE_DTYPE),
            reason=np.array([InvalidReason.NONE], dtype=REASON_DTYPE),
        )


def test_stated_array_rejects_zero_for_a_missing_element() -> None:
    with pytest.raises(ValueError):
        StatedArray(
            value=np.array([0.0]),
            state=np.array([MISSING], dtype=STATE_DTYPE),
            reason=np.array([InvalidReason.NODATA], dtype=REASON_DTYPE),
        )


def test_stated_array_rejects_a_finite_number_for_an_invalid_element() -> None:
    with pytest.raises(ValueError):
        StatedArray(
            value=np.array([1.0]),
            state=np.array([INVALID], dtype=STATE_DTYPE),
            reason=np.array([InvalidReason.DN_ZERO], dtype=REASON_DTYPE),
        )


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (VALID, InvalidReason.DN_ZERO),  # a valid element carries no reason
        (INVALID, InvalidReason.NONE),  # an invalid element must say why
        (MISSING, InvalidReason.NONE),  # a missing element must say why
    ],
)
def test_stated_array_ties_reason_to_state(state: ObsState, reason: InvalidReason) -> None:
    value = 1.0 if state == VALID else np.nan
    with pytest.raises(ValueError):
        StatedArray(
            value=np.array([value]),
            state=np.array([state], dtype=STATE_DTYPE),
            reason=np.array([reason], dtype=REASON_DTYPE),
        )


def test_stated_array_rejects_unknown_states_reasons_and_shapes() -> None:
    with pytest.raises(ValueError):
        StatedArray(
            value=np.array([1.0]),
            state=np.array([0], dtype=STATE_DTYPE),
            reason=np.array([InvalidReason.NONE], dtype=REASON_DTYPE),
        )
    with pytest.raises(ValueError):
        StatedArray(
            value=np.array([np.nan]),
            state=np.array([INVALID], dtype=STATE_DTYPE),
            reason=np.array(["SOMETHING_ELSE"], dtype=REASON_DTYPE),
        )
    with pytest.raises(ValueError):
        StatedArray(
            value=np.array([1.0, 2.0]),
            state=np.array([VALID], dtype=STATE_DTYPE),
            reason=np.array([InvalidReason.NONE], dtype=REASON_DTYPE),
        )


def test_stated_array_rejects_wrong_dtypes() -> None:
    with pytest.raises(TypeError):
        StatedArray(
            value=np.array([1], dtype=np.int64),
            state=np.array([VALID], dtype=STATE_DTYPE),
            reason=np.array([InvalidReason.NONE], dtype=REASON_DTYPE),
        )
    with pytest.raises(TypeError):
        StatedArray(
            value=np.array([1.0]),
            state=np.array([VALID], dtype=np.int64),
            reason=np.array([InvalidReason.NONE], dtype=REASON_DTYPE),
        )


def test_results_are_read_only() -> None:
    result = decode_linear(np.array([100, 0]), nodata=None)
    for array in (result.value, result.state, result.reason):
        assert not array.flags.writeable
        with pytest.raises(ValueError):
            array[0] = array[1]


# --- F8: PALSAR amplitude --------------------------------------------------------------------


def test_F8_palsar_amplitude_to_db() -> None:
    result = decode_db(100, nodata=None)
    np.testing.assert_allclose(result.value, -43.0, rtol=RTOL, atol=ATOL)
    assert result.state == VALID
    assert result.reason == InvalidReason.NONE


def test_F8_palsar_amplitude_to_linear() -> None:
    result = decode_linear(100, nodata=None)
    # DN**2 * 10**-8.3, to 16 digits.
    np.testing.assert_allclose(result.value, 5.011872336272722e-5, rtol=RTOL, atol=ATOL)
    # Relative check as well, since atol dominates at this magnitude.
    np.testing.assert_allclose(result.value, 5.011872336272722e-5, rtol=RTOL, atol=0.0)
    # Consistent with the PRD's rounded "about 5.011872e-5".
    np.testing.assert_allclose(result.value, 5.011872e-5, rtol=1e-6, atol=0.0)
    assert result.state == VALID


def test_calibration_offset_is_the_prd_value_and_an_argument() -> None:
    assert PALSAR2_CALIBRATION_OFFSET_DB == 83.0
    np.testing.assert_allclose(
        decode_db(100, nodata=None, calibration_offset_db=80.0).value, -40.0, rtol=RTOL, atol=ATOL
    )
    np.testing.assert_allclose(
        decode_linear(100, nodata=None, calibration_offset_db=80.0).value,
        1e-4,
        rtol=RTOL,
        atol=0.0,
    )


def test_decode_takes_amplitude_dn_and_returns_power() -> None:
    # Linear power goes with the square of amplitude: doubling DN quadruples gamma0.
    single = decode_linear(100, nodata=None).value
    double = decode_linear(200, nodata=None).value
    np.testing.assert_allclose(double / single, 4.0, rtol=RTOL, atol=0.0)
    # dB is 20*log10 of amplitude: doubling DN adds 20*log10(2) dB.
    gain = decode_db(200, nodata=None).value - decode_db(100, nodata=None).value
    np.testing.assert_allclose(gain, 20.0 * math.log10(2.0), rtol=RTOL, atol=ATOL)


def test_decode_offers_no_averaging() -> None:
    # Decoding is elementwise: no axis or reduction argument, and the shape is preserved.
    for decode in (decode_db, decode_linear):
        assert list(inspect.signature(decode).parameters) == [
            "dn",
            "nodata",
            "calibration_offset_db",
        ]
        assert decode(np.full((2, 3), 100), nodata=None).value.shape == (2, 3)


def test_mean_of_decoded_power_differs_from_power_of_mean_dn() -> None:
    dn = np.array([100, 300])
    mean_of_power = float(np.mean(decode_linear(dn, nodata=None).value))
    power_of_mean_dn = float(decode_linear(np.mean(dn), nodata=None).value)
    k = 10.0**-8.3
    np.testing.assert_allclose(mean_of_power, 50_000 * k, rtol=RTOL, atol=0.0)
    np.testing.assert_allclose(power_of_mean_dn, 40_000 * k, rtol=RTOL, atol=0.0)
    assert not np.isclose(mean_of_power, power_of_mean_dn, rtol=RTOL, atol=ATOL)


# --- Zero, nodata and non-finite DN ----------------------------------------------------------


@pytest.mark.parametrize("decode", [decode_db, decode_linear])
def test_zero_dn_is_invalid_and_never_minus_infinity(decode: Decode) -> None:
    result = decode(0, nodata=None)
    assert_unavailable(result, INVALID, InvalidReason.DN_ZERO)
    assert not np.isneginf(result.value)


@pytest.mark.parametrize("decode", [decode_db, decode_linear])
def test_declared_nodata_is_missing(decode: Decode) -> None:
    assert_unavailable(
        decode(DECLARED_NODATA, nodata=DECLARED_NODATA), MISSING, InvalidReason.NODATA
    )
    # A declared nodata of 0 makes DN 0 missing rather than invalid.
    assert_unavailable(decode(0, nodata=0), MISSING, InvalidReason.NODATA)


@pytest.mark.parametrize("decode", [decode_db, decode_linear])
@pytest.mark.parametrize("dn", [math.nan, math.inf, -math.inf])
def test_nonfinite_dn_is_invalid(decode: Decode, dn: float) -> None:
    assert_unavailable(decode(dn, nodata=None), INVALID, InvalidReason.NONFINITE)


@pytest.mark.parametrize("decode", [decode_db, decode_linear])
def test_negative_dn_is_invalid(decode: Decode) -> None:
    assert_unavailable(decode(-5, nodata=None), INVALID, InvalidReason.DN_NEGATIVE)


def test_decode_array_keeps_states_apart() -> None:
    dn = np.array([[100, 0], [DECLARED_NODATA, 300]], dtype=np.uint16)
    for result in (
        decode_db(dn, nodata=DECLARED_NODATA),
        decode_linear(dn, nodata=DECLARED_NODATA),
    ):
        assert result.value.shape == result.state.shape == result.reason.shape == (2, 2)
        assert result.value.dtype == np.float64
        assert result.state.tolist() == [[VALID, INVALID], [MISSING, VALID]]
        assert result.reason.tolist() == [
            [InvalidReason.NONE, InvalidReason.DN_ZERO],
            [InvalidReason.NODATA, InvalidReason.NONE],
        ]
        unavailable = result.state != VALID
        assert np.all(np.isnan(result.value[unavailable]))
        assert np.all(np.isfinite(result.value[~unavailable]))
    np.testing.assert_allclose(
        decode_db(dn, nodata=DECLARED_NODATA).value[[0, 1], [0, 1]],
        [-43.0, 20.0 * math.log10(300.0) - 83.0],
        rtol=RTOL,
        atol=ATOL,
    )


@pytest.mark.parametrize(
    "dn", [np.array([True]), np.array([1 + 2j]), np.array(["100"]), np.array([None])]
)
def test_decode_rejects_non_real_input(dn: npt.NDArray[np.generic]) -> None:
    with pytest.raises(TypeError):
        decode_linear(dn, nodata=None)


@pytest.mark.parametrize("nodata", [0.0, True, "0"])
def test_decode_rejects_a_non_integer_nodata(nodata: object) -> None:
    with pytest.raises(TypeError):
        decode_linear(100, nodata=nodata)  # type: ignore[arg-type]


# --- F9: PALSAR power ratio ------------------------------------------------------------------


def test_F9_palsar_power_ratio() -> None:
    result = x_power(1000, 100, nodata=None, hh_noise_dominated=False, hv_noise_dominated=False)
    np.testing.assert_allclose(result.value, 0.01, rtol=RTOL, atol=ATOL)
    np.testing.assert_allclose(result.value, 0.01, rtol=RTOL, atol=0.0)
    assert not np.isclose(result.value, 0.1, rtol=RTOL, atol=ATOL)
    assert result.state == VALID
    assert result.reason == InvalidReason.NONE


def test_x_power_is_the_ratio_of_decoded_linear_powers() -> None:
    dn_hh = np.array([1000, 1234, 77])
    dn_hv = np.array([100, 4321, 5])
    ratio = x_power(dn_hh, dn_hv, nodata=None, hh_noise_dominated=False, hv_noise_dominated=False)
    expected = decode_linear(dn_hv, nodata=None).value / decode_linear(dn_hh, nodata=None).value
    np.testing.assert_allclose(ratio.value, expected, rtol=RTOL, atol=ATOL)


def test_x_power_zero_hh_is_an_invalid_denominator() -> None:
    result = x_power(0, 100, nodata=None, hh_noise_dominated=False, hv_noise_dominated=False)
    assert_unavailable(result, INVALID, InvalidReason.DENOMINATOR_INVALID)


def test_x_power_nodata_hh_is_an_invalid_denominator_and_missing() -> None:
    result = x_power(
        DECLARED_NODATA,
        100,
        nodata=DECLARED_NODATA,
        hh_noise_dominated=False,
        hv_noise_dominated=False,
    )
    assert_unavailable(result, MISSING, InvalidReason.DENOMINATOR_INVALID)


def test_x_power_noise_dominated_hh_is_an_invalid_denominator() -> None:
    result = x_power(1000, 100, nodata=None, hh_noise_dominated=True, hv_noise_dominated=False)
    assert_unavailable(result, INVALID, InvalidReason.DENOMINATOR_INVALID)


def test_x_power_adds_no_epsilon() -> None:
    # The smallest valid denominator gives the exact ratio, not a regularized one.
    tiny = x_power(1, 1000, nodata=None, hh_noise_dominated=False, hv_noise_dominated=False)
    np.testing.assert_allclose(tiny.value, 1e6, rtol=RTOL, atol=0.0)
    # A zero denominator gives no number at all: neither finite nor huge.
    zero = x_power(0, 1000, nodata=None, hh_noise_dominated=False, hv_noise_dominated=False)
    assert np.isnan(zero.value)
    assert not np.isinf(zero.value)


@pytest.mark.parametrize(
    ("dn_hv", "hv_noise", "state"),
    [
        (0, False, INVALID),
        (DECLARED_NODATA, False, MISSING),
        (100, True, INVALID),
    ],
)
def test_x_power_invalid_numerator(dn_hv: int, hv_noise: bool, state: ObsState) -> None:
    result = x_power(
        1000,
        dn_hv,
        nodata=DECLARED_NODATA,
        hh_noise_dominated=False,
        hv_noise_dominated=hv_noise,
    )
    assert_unavailable(result, state, InvalidReason.NUMERATOR_INVALID)


def test_x_power_reports_the_denominator_first() -> None:
    result = x_power(0, 0, nodata=None, hh_noise_dominated=False, hv_noise_dominated=False)
    assert_unavailable(result, INVALID, InvalidReason.DENOMINATOR_INVALID)


def test_x_power_array_with_noise_masks() -> None:
    dn_hh = np.array([[1000, 0, 1000], [DECLARED_NODATA, 1000, 1000]])
    dn_hv = np.array([[100, 100, 100], [100, 100, 0]])
    hh_noise = np.array([[False, False, True], [False, False, False]])
    result = x_power(
        dn_hh,
        dn_hv,
        nodata=DECLARED_NODATA,
        hh_noise_dominated=hh_noise,
        hv_noise_dominated=False,
    )
    assert result.state.tolist() == [[VALID, INVALID, INVALID], [MISSING, VALID, INVALID]]
    assert result.reason.tolist() == [
        [InvalidReason.NONE, InvalidReason.DENOMINATOR_INVALID, InvalidReason.DENOMINATOR_INVALID],
        [InvalidReason.DENOMINATOR_INVALID, InvalidReason.NONE, InvalidReason.NUMERATOR_INVALID],
    ]
    np.testing.assert_allclose(result.value[result.state == VALID], [0.01, 0.01], rtol=RTOL)
    assert np.all(np.isnan(result.value[result.state != VALID]))


def test_x_power_rejects_mismatched_channels_and_non_boolean_flags() -> None:
    with pytest.raises(ValueError):
        x_power(
            np.array([1000, 1000]),
            np.array([100]),
            nodata=None,
            hh_noise_dominated=False,
            hv_noise_dominated=False,
        )
    with pytest.raises(TypeError):
        x_power(1000, 100, nodata=None, hh_noise_dominated=0.5, hv_noise_dominated=False)


# --- F10: angle conversion -------------------------------------------------------------------


def test_F10_radians_to_degrees() -> None:
    result = angle_to_degrees(math.pi / 3, unit="radians")
    np.testing.assert_allclose(result.value, 60.0, rtol=RTOL, atol=ATOL)
    assert result.state == VALID


def test_internal_angle_unit_is_degrees() -> None:
    assert INTERNAL_ANGLE_UNIT == "degrees"
    np.testing.assert_allclose(angle_to_degrees(60.0, unit="degrees").value, 60.0, rtol=RTOL)


def test_declared_unit_decides_the_result() -> None:
    as_radians = angle_to_degrees(1.0, unit="radians").value
    as_degrees = angle_to_degrees(1.0, unit="degrees").value
    np.testing.assert_allclose(as_radians, 180.0 / math.pi, rtol=RTOL, atol=ATOL)
    np.testing.assert_allclose(as_degrees, 1.0, rtol=RTOL, atol=ATOL)
    assert not np.isclose(as_radians, as_degrees, rtol=RTOL, atol=ATOL)


def test_units_are_not_inferred_from_magnitude() -> None:
    # 45 looks like degrees but is declared radians, so it is converted as radians.
    np.testing.assert_allclose(
        angle_to_degrees(45.0, unit="radians").value, 45.0 * 180.0 / math.pi, rtol=RTOL
    )
    # 0.5 looks like radians but is declared degrees, so it is kept.
    np.testing.assert_allclose(angle_to_degrees(0.5, unit="degrees").value, 0.5, rtol=RTOL)


@pytest.mark.parametrize("unit", [None, "", "rad", "deg", "Radians", "DEGREES", "gradians"])
def test_undeclared_or_unknown_angle_unit_raises(unit: object) -> None:
    with pytest.raises(ValueError):
        angle_to_degrees(1.0, unit=unit)  # type: ignore[arg-type]


def test_angle_array_marks_nonfinite_values() -> None:
    result = angle_to_degrees(np.array([0.0, math.nan, math.pi]), unit="radians")
    assert result.state.tolist() == [VALID, INVALID, VALID]
    assert result.reason.tolist() == [
        InvalidReason.NONE,
        InvalidReason.NONFINITE,
        InvalidReason.NONE,
    ]
    np.testing.assert_allclose(result.value[[0, 2]], [0.0, 180.0], rtol=RTOL, atol=ATOL)
    assert np.isnan(result.value[1])


# --- Properties ------------------------------------------------------------------------------

DN_POSITIVE = st.integers(min_value=1, max_value=2**32)


@given(st.lists(DN_POSITIVE, min_size=2, max_size=50, unique=True))
def test_decode_linear_is_monotone_in_dn(dns: list[int]) -> None:
    dn = np.array(sorted(dns), dtype=np.int64)
    result = decode_linear(dn, nodata=None)
    assert np.all(result.state == VALID)
    assert np.all(np.diff(result.value) > 0)


@given(st.lists(DN_POSITIVE, min_size=1, max_size=50))
def test_db_and_linear_agree(dns: list[int]) -> None:
    dn = np.array(dns, dtype=np.int64)
    db = decode_db(dn, nodata=None)
    linear = decode_linear(dn, nodata=None)
    assert np.all(db.state == VALID)
    assert np.all(linear.state == VALID)
    np.testing.assert_allclose(10.0 * np.log10(linear.value), db.value, rtol=RTOL, atol=ATOL)
    np.testing.assert_allclose(10.0 ** (db.value / 10.0), linear.value, rtol=RTOL, atol=ATOL)
