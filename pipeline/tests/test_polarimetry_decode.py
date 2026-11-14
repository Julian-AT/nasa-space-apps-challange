"""PALSAR-2 decoding, angle conversion and the validity vocabulary (PRD 7, F8-F10).

Fixture values come from PRD 7 (Fixtures). Comparisons use the PRD 7 arithmetic tolerances on
float64: rtol = 1e-10, atol = 1e-12.
"""

from __future__ import annotations

import inspect
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

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


def test_stated_array_copies_its_inputs() -> None:
    value = np.array([100.0, np.nan])
    state = np.array([VALID, INVALID], dtype=STATE_DTYPE)
    reason = np.array([InvalidReason.NONE, InvalidReason.DN_ZERO], dtype=REASON_DTYPE)
    record = StatedArray(value=value, state=state, reason=reason)
    value[1] = 0.0
    state[1] = VALID
    assert record.state.tolist() == [VALID, INVALID]
    assert np.isnan(record.value[1])
    assert value.flags.writeable


@pytest.mark.parametrize("field", ["value", "state", "reason"])
def test_stated_array_refuses_masked_fields(field: str) -> None:
    # np.array drops a mask silently, so a masked VALID element would survive as a number.
    fields: dict[str, npt.NDArray[Any]] = {
        "value": np.array([1.0]),
        "state": np.array([VALID], dtype=STATE_DTYPE),
        "reason": np.array([InvalidReason.NONE], dtype=REASON_DTYPE),
    }
    fields[field] = np.ma.array(fields[field], mask=[True])
    with pytest.raises(TypeError, match="masked"):
        StatedArray(**fields)


@pytest.mark.parametrize("field", ["value", "state", "reason"])
def test_stated_array_refuses_masked_fields_nested_in_lists(field: str) -> None:
    # Review codex2-r2-1: two list levels hid the mask, and the masked VALID element survived.
    fields: dict[str, Any] = {
        "value": np.array([1.0]),
        "state": np.array([VALID], dtype=STATE_DTYPE),
        "reason": np.array([InvalidReason.NONE], dtype=REASON_DTYPE),
    }
    fields[field] = np.ma.array(fields[field], mask=[True])
    nested: dict[str, Any] = {name: [[array]] for name, array in fields.items()}
    with pytest.raises(TypeError, match="masked"):
        StatedArray(**nested)


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


# --- Masked inputs ---------------------------------------------------------------------------
# A numpy masked array carries validity in its mask, and np.asarray drops that mask without a
# word, so a masked pixel would come out VALID. Product flags therefore enter only through the
# explicit arguments (nodata, noise flags), and a masked array is refused wherever it appears
# (PRD 7: keep the product's validity and noise flags; three states stay distinct).

ARRAY_ARGUMENTS = [
    "decode_db:dn",
    "decode_linear:dn",
    "x_power:dn_hh",
    "x_power:dn_hv",
    "x_power:hh_noise_dominated",
    "x_power:hv_noise_dominated",
    "angle_to_degrees:angle",
]


def call_with(argument: str, dn: npt.ArrayLike, flag: npt.ArrayLike) -> StatedArray:
    """Call one entry point with ``dn`` (DN or angle) or ``flag`` in the named argument."""
    plain_dn = np.array([1000, 1000])
    plain_flag = np.array([False, False])
    match argument:
        case "decode_db:dn":
            return decode_db(dn, nodata=None)
        case "decode_linear:dn":
            return decode_linear(dn, nodata=None)
        case "x_power:dn_hh":
            return x_power(
                dn,
                plain_dn,
                nodata=None,
                hh_noise_dominated=plain_flag,
                hv_noise_dominated=plain_flag,
            )
        case "x_power:dn_hv":
            return x_power(
                plain_dn,
                dn,
                nodata=None,
                hh_noise_dominated=plain_flag,
                hv_noise_dominated=plain_flag,
            )
        case "x_power:hh_noise_dominated":
            return x_power(
                plain_dn,
                plain_dn,
                nodata=None,
                hh_noise_dominated=flag,
                hv_noise_dominated=plain_flag,
            )
        case "x_power:hv_noise_dominated":
            return x_power(
                plain_dn,
                plain_dn,
                nodata=None,
                hh_noise_dominated=plain_flag,
                hv_noise_dominated=flag,
            )
        case "angle_to_degrees:angle":
            return angle_to_degrees(dn, unit="degrees")
    raise AssertionError(f"unknown argument {argument}")


@pytest.mark.parametrize("argument", ARRAY_ARGUMENTS)
def test_every_array_argument_accepts_the_plain_inputs(argument: str) -> None:
    # Guards the refusal tests below: they fail only because of the mask.
    result = call_with(argument, np.array([100, 300]), np.array([False, False]))
    assert np.all(result.state == VALID)


@pytest.mark.parametrize("argument", ARRAY_ARGUMENTS)
def test_masked_input_is_refused(argument: str) -> None:
    # Review codex-r1-1 / codex2-r1-1: the masked 300 must never be published as VALID.
    dn = np.ma.array([100.0, 300.0], mask=[False, True])
    flag = np.ma.array([False, False], mask=[False, True])
    with pytest.raises(TypeError, match="masked"):
        call_with(argument, dn, flag)


@pytest.mark.parametrize("argument", ARRAY_ARGUMENTS)
def test_masked_input_is_refused_even_with_nothing_masked(argument: str) -> None:
    # The refusal does not depend on the mask content, so a mask is never half-honoured.
    dn = np.ma.array([100.0, 300.0], mask=[False, False])
    flag = np.ma.array([False, False], mask=[False, False])
    with pytest.raises(TypeError, match="masked"):
        call_with(argument, dn, flag)


@pytest.mark.parametrize("argument", ARRAY_ARGUMENTS)
def test_masked_array_inside_a_sequence_is_refused(argument: str) -> None:
    # np.asarray([masked_array]) builds a plain array and keeps the masked value as a number.
    dn = [np.ma.array([100.0, 300.0], mask=[False, True])]
    flag = [np.ma.array([False, False], mask=[False, True])]
    with pytest.raises(TypeError, match="masked"):
        call_with(argument, dn, flag)


@pytest.mark.parametrize("argument", ARRAY_ARGUMENTS)
def test_masked_constant_is_refused(argument: str) -> None:
    # np.asarray(np.ma.masked) is 0.0, which would read as a valid 0 degree angle.
    with pytest.raises(TypeError, match="masked"):
        call_with(argument, np.ma.masked, np.ma.masked)


# Review codex2-r2-1: np.ma.asarray reads masks only from the immediate members of a sequence,
# so [[masked_array]] still lost its mask and the masked 300 came out VALID. The inputs are now
# walked to every depth, and only plain numpy arrays, scalars, lists and tuples are accepted.

Container = Callable[[list[object]], object]
CONTAINERS: list[Container] = [list, tuple]


def nest(values: object, depth: int, container: Container = list) -> object:
    """Wrap ``values`` in ``depth`` single-member containers."""
    for _ in range(depth):
        values = container([values])
    return values


def test_review_example_masked_array_two_levels_down_is_refused() -> None:
    masked = np.ma.array([100.0, 300.0], mask=[False, True])
    with pytest.raises(TypeError, match="masked"):
        decode_linear([[masked]], nodata=None)


@pytest.mark.parametrize("argument", ARRAY_ARGUMENTS)
@pytest.mark.parametrize("depth", [2, 3])
@pytest.mark.parametrize("container", CONTAINERS)
def test_masked_array_nested_at_any_depth_is_refused(
    argument: str, depth: int, container: Container
) -> None:
    dn = nest(np.ma.array([100.0, 300.0], mask=[False, True]), depth, container)
    flag = nest(np.ma.array([False, False], mask=[False, True]), depth, container)
    with pytest.raises(TypeError, match="masked"):
        call_with(argument, dn, flag)  # type: ignore[arg-type]


@pytest.mark.parametrize("argument", ARRAY_ARGUMENTS)
@pytest.mark.parametrize("depth", [1, 2, 3])
def test_masked_constant_nested_at_any_depth_is_refused(argument: str, depth: int) -> None:
    # np.asarray([[100.0, np.ma.masked]]) turns the masked element into a number or NaN.
    dn = nest([100.0, np.ma.masked], depth - 1)
    flag = nest([False, np.ma.masked], depth - 1)
    with pytest.raises(TypeError, match="masked"):
        call_with(argument, dn, flag)  # type: ignore[arg-type]


WRAPPINGS = st.lists(
    st.tuples(st.sampled_from(CONTAINERS), st.integers(0, 2), st.integers(0, 2)),
    min_size=1,
    max_size=6,
)


def wrap(
    masked: object, plain: object, wrapping: list[tuple[Container, int, int]]
) -> tuple[object, object]:
    """Nest ``masked`` among plain siblings of the same shape, so numpy would convert it whole.

    Each level is ``(container, before, after)``: the masked member sits after ``before`` plain
    copies and before ``after`` more. Returns the masked structure and its plain counterpart.
    """
    for container, before, after in wrapping:
        masked = container([plain] * before + [masked] + [plain] * after)
        plain = container([plain] * (before + 1 + after))
    return masked, plain


@given(argument=st.sampled_from(ARRAY_ARGUMENTS), wrapping=WRAPPINGS)
def test_masked_array_anywhere_in_nested_sequences_is_refused(
    argument: str, wrapping: list[tuple[Container, int, int]]
) -> None:
    dn, _ = wrap(
        np.ma.array([100.0, 300.0], mask=[False, True]), np.array([100.0, 300.0]), wrapping
    )
    flag, _ = wrap(
        np.ma.array([False, False], mask=[False, True]), np.array([False, False]), wrapping
    )
    with pytest.raises(TypeError, match="masked"):
        call_with(argument, dn, flag)  # type: ignore[arg-type]


class OneMemberSequence:
    """A sequence that is neither a list nor a tuple; numpy descends into it as into a list."""

    def __init__(self, member: object) -> None:
        self.member = member

    def __len__(self) -> int:
        return 1

    def __getitem__(self, index: int) -> object:
        if index != 0:
            raise IndexError(index)
        return self.member


class ArrayLike:
    """An array-like whose ``__array__`` returns ``array``; np.asarray keeps only its data."""

    def __init__(self, array: npt.NDArray[Any]) -> None:
        self.array = array

    def __array__(self, dtype: object = None, copy: object = None) -> npt.NDArray[Any]:
        return self.array


class PlainSubclass(np.ndarray[Any, Any]):
    """An ndarray subclass other than numpy's own; third-party subclasses may carry masks."""


# Review codex-r3-1 / codex2-r3-1: list and tuple subclasses passed the walk by isinstance, and
# numpy then converted them by their own protocol, so the masked 300 came out VALID. Containers
# are now matched by exact type.


class ListWithArray(list[object]):
    """A list subclass holding the plain data, while ``__array__`` returns ``array``.

    The walk sees only the plain list members; numpy converts through ``__array__``.
    """

    def __init__(self, array: npt.NDArray[Any]) -> None:
        super().__init__(np.ma.getdata(array).tolist())
        self.array = array

    def __array__(self, dtype: object = None, copy: object = None) -> npt.NDArray[Any]:
        return self.array


class TupleWithArray(tuple[object, ...]):
    """The tuple counterpart of :class:`ListWithArray`."""

    array: npt.NDArray[Any]

    def __new__(cls, array: npt.NDArray[Any]) -> TupleWithArray:
        self = super().__new__(cls, np.ma.getdata(array).tolist())
        self.array = array
        return self

    def __array__(self, dtype: object = None, copy: object = None) -> npt.NDArray[Any]:
        return self.array


class ListSubclass(list[object]):
    """A list subclass that overrides nothing. It is refused all the same: a subclass can change
    how numpy converts it, so only exact lists and tuples are walked."""


HIDING_CONTAINERS: dict[str, Callable[[npt.NDArray[Any]], object]] = {
    "custom sequence": OneMemberSequence,
    "array-like": ArrayLike,
    "array-like in a list": lambda array: [ArrayLike(array)],
    "ndarray subclass": lambda array: np.asarray(array).view(PlainSubclass),
    "list subclass with __array__": ListWithArray,
    "tuple subclass with __array__": TupleWithArray,
    "list subclass": lambda array: ListSubclass(list(array)),
}


@pytest.mark.parametrize("argument", ARRAY_ARGUMENTS)
@pytest.mark.parametrize("container", list(HIDING_CONTAINERS))
@pytest.mark.parametrize("masked", [True, False])
def test_containers_that_could_hide_a_mask_are_refused(
    argument: str, container: str, masked: bool
) -> None:
    # numpy converts these through their own protocols and drops a mask on the way, so they are
    # refused whether or not anything is masked; the caller converts them explicitly.
    wrapper = HIDING_CONTAINERS[container]
    dn: npt.NDArray[Any]
    flag: npt.NDArray[Any]
    if masked:
        dn = np.ma.array([100.0, 300.0], mask=[False, True])
        flag = np.ma.array([False, False], mask=[False, True])
    else:
        dn = np.array([100.0, 300.0])
        flag = np.array([False, False])
    with pytest.raises(TypeError, match="plain numpy array"):
        call_with(argument, wrapper(dn), wrapper(flag))  # type: ignore[arg-type]


def test_review_example_list_subclass_of_an_array_like_is_refused() -> None:
    # codex-r3-1: an empty list payload, and __array__ hands numpy the masked array.
    class ListArrayLike(ArrayLike, list[object]):
        pass

    masked = np.ma.array([100.0, 300.0], mask=[False, True])
    with pytest.raises(TypeError, match="plain numpy array"):
        decode_linear(ListArrayLike(masked), nodata=None)


@pytest.mark.parametrize("depth", [1, 2, 3])
@pytest.mark.parametrize("container", CONTAINERS)
def test_list_subclass_nested_in_plain_containers_is_refused(
    depth: int, container: Container
) -> None:
    # The walk descends through exact lists and tuples and still refuses the subclass inside.
    # Shape-free entry points only: nesting adds leading dimensions of size 1.
    masked = np.ma.array([100.0, 300.0], mask=[False, True])
    dn = nest(ListWithArray(masked), depth, container)
    with pytest.raises(TypeError, match="plain numpy array"):
        decode_db(dn, nodata=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="plain numpy array"):
        decode_linear(dn, nodata=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="plain numpy array"):
        angle_to_degrees(dn, unit="degrees")  # type: ignore[arg-type]


class FloatWithArray(float):
    """A float subclass whose ``__array__`` returns a masked element."""

    def __array__(self, dtype: object = None, copy: object = None) -> npt.NDArray[Any]:
        return np.ma.array(float(self), mask=True)


class IntSubclass(int):
    """An int subclass; scalars are matched by exact type, like containers."""


class Float64Subclass(np.float64):
    """A numpy scalar subclass other than numpy's own."""


SCALAR_SUBCLASSES: dict[str, Callable[[float], object]] = {
    "float subclass with __array__": FloatWithArray,
    "int subclass": lambda value: IntSubclass(int(value)),
    "numpy float64 subclass": Float64Subclass,
}

# Boolean flags have no scalar subclass to test: bool cannot be subclassed, and a subclass of
# np.bool_ constructs plain np.bool_ values.
DN_ARGUMENTS = [argument for argument in ARRAY_ARGUMENTS if "noise" not in argument]


@pytest.mark.parametrize("argument", DN_ARGUMENTS)
@pytest.mark.parametrize("scalar", list(SCALAR_SUBCLASSES))
def test_scalar_subclasses_are_refused(argument: str, scalar: str) -> None:
    # numpy may convert a subclass by its own protocol, so only exact scalar types are accepted.
    dn = [100.0, SCALAR_SUBCLASSES[scalar](300.0)]
    with pytest.raises(TypeError, match="plain numpy array"):
        call_with(argument, dn, np.array([False, False]))  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["value", "state", "reason"])
@pytest.mark.parametrize("container", [ListWithArray, TupleWithArray])
def test_stated_array_refuses_fields_in_list_or_tuple_subclasses(
    field: str, container: Callable[[npt.NDArray[Any]], object]
) -> None:
    fields: dict[str, Any] = {
        "value": np.array([1.0]),
        "state": np.array([VALID], dtype=STATE_DTYPE),
        "reason": np.array([InvalidReason.NONE], dtype=REASON_DTYPE),
    }
    fields[field] = container(np.ma.array(fields[field], mask=[True]))
    with pytest.raises(TypeError, match="plain numpy array"):
        StatedArray(**fields)


def test_stated_array_accepts_lists_of_its_own_codes() -> None:
    # Guards the exact-type rule: ObsState and InvalidReason members are int and str subclasses
    # defined here, and they convert to their plain codes.
    record = StatedArray(
        value=np.array([1.0, np.nan]),
        state=np.array([VALID, INVALID], dtype=STATE_DTYPE),
        reason=[InvalidReason.NONE, InvalidReason.DN_ZERO],  # type: ignore[arg-type]
    )
    assert record.reason.tolist() == ["NONE", "DN_ZERO"]


@pytest.mark.parametrize(
    "scalar", [np.float64(300.0), np.float32(300.0), np.int64(300), np.uint16(300)]
)
def test_numpy_scalars_are_accepted(scalar: np.generic) -> None:
    # Guards the exact-type rule: numpy's own scalar types are plain scalars.
    dn: list[Any] = [100, scalar]
    assert np.all(decode_linear(dn, nodata=None).state == VALID)


@pytest.mark.parametrize("argument", ARRAY_ARGUMENTS)
def test_object_array_holding_a_masked_array_is_refused(argument: str) -> None:
    # The walk accepts a plain ndarray without looking inside it; an object array could hold a
    # masked array, so every entry point must refuse the object dtype itself.
    dn = np.empty(1, dtype=object)
    dn[0] = np.ma.array([100.0, 300.0], mask=[False, True])
    flag = np.empty(1, dtype=object)
    flag[0] = np.ma.array([False, False], mask=[False, True])
    with pytest.raises(TypeError, match="object"):
        call_with(argument, dn, flag)


def test_stated_array_refuses_an_object_array_holding_a_masked_array() -> None:
    value = np.empty(1, dtype=object)
    value[0] = np.ma.array([1.0], mask=[True])
    with pytest.raises(TypeError, match="object"):
        StatedArray(
            value=value,
            state=np.array([VALID], dtype=STATE_DTYPE),
            reason=np.array([InvalidReason.NONE], dtype=REASON_DTYPE),
        )


@pytest.mark.parametrize("container", CONTAINERS)
def test_plain_values_nested_in_lists_and_tuples_are_accepted(container: Container) -> None:
    # Guards the refusals above: nesting alone is not refused.
    dn = nest(container([100, np.float64(300.0)]), 3, container)
    flag = nest(container([False, np.bool_(False)]), 3, container)
    for result in (
        decode_db(dn, nodata=None),  # type: ignore[arg-type]
        decode_linear(dn, nodata=None),  # type: ignore[arg-type]
        x_power(dn, dn, nodata=None, hh_noise_dominated=flag, hv_noise_dominated=flag),  # type: ignore[arg-type]
        angle_to_degrees(dn, unit="degrees"),  # type: ignore[arg-type]
    ):
        assert result.state.shape == (1, 1, 1, 2)
        assert np.all(result.state == VALID)


def test_memory_mapped_arrays_are_accepted(tmp_path: Path) -> None:
    # Raster stacks are often memory-mapped; np.memmap is numpy's own subclass and has no mask.
    dn = np.memmap(tmp_path / "dn.bin", dtype=np.float64, mode="w+", shape=(2,))
    dn[:] = [100.0, 300.0]
    assert np.all(decode_linear(dn, nodata=None).state == VALID)


def test_nesting_up_to_numpys_dimension_limit_is_accepted() -> None:
    result = decode_linear(nest(100, 64), nodata=None)  # type: ignore[arg-type]
    assert result.state.ndim == 64
    assert np.all(result.state == VALID)


def test_self_referential_sequence_raises_instead_of_recursing() -> None:
    loop: list[object] = []
    loop.append(loop)
    with pytest.raises(ValueError, match="64"):
        decode_linear(loop, nodata=None)  # type: ignore[arg-type]


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
