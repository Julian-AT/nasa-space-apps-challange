"""Tests for validity masks and the valid-fraction helper (PRD 5 Mini-RF rows, 7 validity, 8.11).

Every input is a small synthetic numpy array. The ISIS special-pixel constants are checked against
their bit patterns as published in ISIS ``SpecialPixel.h`` (USGS Astrogeology), and the property
tests compare each pixel against a per-pixel oracle built on :mod:`struct`, independent of the
numpy code under test.
"""

from __future__ import annotations

import dataclasses
import inspect
import math
import struct
import warnings
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.extra import numpy as hnp

from terraluna_pipeline import quality
from terraluna_pipeline.quality import (
    ISIS_HALF_SPACING_REAL4,
    ISIS_NULL_REAL4,
    ISIS_SATURATION_REAL4,
    InvalidReason,
    ValidFraction,
    ValidityMask,
    valid_fraction,
    validity_mask,
)


def _real4(bits: int) -> float:
    """The float32 value with the given IEEE-754 bit pattern, via struct (not numpy)."""
    value: float = struct.unpack("<f", struct.pack("<I", bits))[0]
    return value


# Bit patterns from ISIS SpecialPixel.h (isis/src/core/include/SpecialPixel.h, branch dev).
VALID_MIN4 = _real4(0xFF7FFFFA)
NULL4 = _real4(0xFF7FFFFB)
LOW_REPR_SAT4 = _real4(0xFF7FFFFC)
LOW_INSTR_SAT4 = _real4(0xFF7FFFFD)
HIGH_INSTR_SAT4 = _real4(0xFF7FFFFE)
HIGH_REPR_SAT4 = _real4(0xFF7FFFFF)
FLT_MAX = _real4(0x7F7FFFFF)
SATURATIONS4 = (LOW_REPR_SAT4, LOW_INSTR_SAT4, HIGH_INSTR_SAT4, HIGH_REPR_SAT4)

R = InvalidReason


def reasons(mask: ValidityMask) -> list[int]:
    return [int(code) for code in mask.reason.ravel()]


def f32(*values: float) -> npt.NDArray[np.float32]:
    return np.array(values, dtype=np.float32)


def f64(*values: float) -> npt.NDArray[np.float64]:
    return np.array(values, dtype=np.float64)


# --- The reason-code enum and the module surface ------------------------------------------------


def test_reason_codes_are_fixed() -> None:
    assert {member.name: int(member) for member in InvalidReason} == {
        "VALID": 0,
        "NODATA": 1,
        "NON_FINITE": 2,
        "OUTSIDE_SUPPORT": 3,
        "ISIS_SATURATION": 4,
    }


def test_every_reason_code_is_documented_in_the_module() -> None:
    doc = quality.__doc__
    assert doc is not None
    for member in InvalidReason:
        assert f"``{member.label}``" in doc, member
        assert member.label == member.name.lower()


FORBIDDEN_TOKENS = {"stokes", "s1", "s4", "sc", "oc", "cpr", "sign", "snr", "threshold", "below"}


def test_no_threshold_or_polarimetry_reason_or_function_exists() -> None:
    """Stokes validity and the S4-sign check belong to polarimetry.py (T2-02, T2-05); an SNR or
    quality threshold must be written in config first (PRD 7), and radar.snr_rule is null."""
    names = [member.name for member in InvalidReason] + list(quality.__all__)
    for name in names:
        assert not FORBIDDEN_TOKENS & set(name.lower().split("_")), name
    assert set(quality.__all__) == {
        "ISIS_HALF_SPACING_REAL4",
        "ISIS_NULL_REAL4",
        "ISIS_SATURATION_REAL4",
        "InvalidReason",
        "ValidFraction",
        "ValidityMask",
        "valid_fraction",
        "validity_mask",
    }


def test_validity_mask_takes_no_threshold_parameter() -> None:
    parameters = inspect.signature(validity_mask).parameters
    assert list(parameters) == ["values", "nodata", "support"]
    assert parameters["nodata"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["support"].kind is inspect.Parameter.KEYWORD_ONLY


# --- ISIS constants -----------------------------------------------------------------------------


def test_isis_constants_match_the_published_bit_patterns() -> None:
    assert ISIS_NULL_REAL4 == NULL4
    assert ISIS_SATURATION_REAL4 == SATURATIONS4
    assert math.isclose(NULL4, -3.4e38, rel_tol=1e-3)
    assert ISIS_HALF_SPACING_REAL4 == 2.0**103
    # Half the float32 spacing at this magnitude: adjacent codes are 2**104 apart.
    assert LOW_REPR_SAT4 - NULL4 == -(2.0**104)
    assert NULL4 - VALID_MIN4 == -(2.0**104)


# --- Zero, explicit nodata and dtype-exact comparison -------------------------------------------


@pytest.mark.parametrize(
    "dtype", [np.float32, np.float64, np.uint8, np.int16, np.int32, np.complex64]
)
def test_zero_is_valid_unless_declared(dtype: type[np.generic]) -> None:
    mask = validity_mask(np.array([0, 1, 2], dtype=dtype))
    assert mask.valid.tolist() == [True, True, True]
    assert reasons(mask) == [R.VALID, R.VALID, R.VALID]


@pytest.mark.parametrize("dtype", [np.float32, np.float64, np.uint8, np.int16, np.complex64])
def test_zero_is_nodata_when_the_label_says_so(dtype: type[np.generic]) -> None:
    mask = validity_mask(np.array([0, 1, 2], dtype=dtype), nodata=0)
    assert mask.valid.tolist() == [False, True, True]
    assert reasons(mask) == [R.NODATA, R.VALID, R.VALID]


def test_negative_and_large_values_are_valid() -> None:
    """No range or threshold rule: lunar CPR reaches 2 to 3 and is never truncated (PRD 7)."""
    mask = validity_mask(f64(-9998.0, -1.0, 3.0, 1e30, FLT_MAX, -FLT_MAX / 2))
    assert mask.valid.all()


def test_explicit_nodata_scalar_and_sequence() -> None:
    values = f32(-9999.0, 1.0, -32768.0, 0.0, 5.5)
    assert reasons(validity_mask(values, nodata=-9999.0)) == [
        R.NODATA,
        R.VALID,
        R.VALID,
        R.VALID,
        R.VALID,
    ]
    assert reasons(validity_mask(values, nodata=[-9999, -32768.0])) == [
        R.NODATA,
        R.VALID,
        R.NODATA,
        R.VALID,
        R.VALID,
    ]
    assert reasons(validity_mask(values, nodata=())) == [R.VALID] * 5
    assert reasons(validity_mask(values, nodata=np.float64(5.5))) == [R.VALID] * 4 + [R.NODATA]


def test_nodata_is_compared_in_the_array_dtype() -> None:
    """A float32 band stores float32(nodata); GeoTIFF and PDS labels state it as a double."""
    values = np.array([0.1, 0.2], dtype=np.float32)
    assert reasons(validity_mask(values, nodata=0.1)) == [R.NODATA, R.VALID]
    assert reasons(validity_mask(values.astype(np.float64), nodata=0.1)) == [R.VALID, R.VALID]


def test_integer_nodata_given_as_an_integral_float() -> None:
    values = np.array([255, 0, 7], dtype=np.uint8)
    assert reasons(validity_mask(values, nodata=255.0)) == [R.NODATA, R.VALID, R.VALID]
    assert reasons(validity_mask(values, nodata=np.int64(7))) == [R.VALID, R.VALID, R.NODATA]


# --- Non-finite values --------------------------------------------------------------------------


def test_nan_and_inf_are_non_finite() -> None:
    mask = validity_mask(f32(math.nan, math.inf, -math.inf, 1.0))
    assert reasons(mask) == [R.NON_FINITE, R.NON_FINITE, R.NON_FINITE, R.VALID]


def test_declared_nan_nodata_marks_nan_as_nodata() -> None:
    mask = validity_mask(f64(math.nan, math.inf, 1.0), nodata=math.nan)
    assert reasons(mask) == [R.NODATA, R.NON_FINITE, R.VALID]


def test_declared_infinite_nodata() -> None:
    mask = validity_mask(f32(-math.inf, math.inf, 1.0), nodata=-math.inf)
    assert reasons(mask) == [R.NODATA, R.NON_FINITE, R.VALID]


def test_non_finite_complex_values() -> None:
    values = np.array([complex(1, math.nan), complex(math.inf, 0), 0j, 2 + 1j], np.complex128)
    assert reasons(validity_mask(values)) == [R.NON_FINITE, R.NON_FINITE, R.VALID, R.VALID]


# --- ISIS special pixels ------------------------------------------------------------------------


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_isis_null_is_nodata_without_a_declaration(dtype: type[np.floating[Any]]) -> None:
    mask = validity_mask(np.array([NULL4, 1.0, VALID_MIN4], dtype=dtype))
    assert reasons(mask) == [R.NODATA, R.VALID, R.VALID]


def test_isis_null_written_as_label_text_matches() -> None:
    """A label's decimal text for the null rounds to the float32 code, not to the same double."""
    from_label = float("-3.4028226550889E+38")
    assert from_label != NULL4
    mask = validity_mask(f64(from_label, float("-3.40282266E+38")))
    assert reasons(mask) == [R.NODATA, R.NODATA]


def test_isis_tolerance_is_half_a_float32_spacing() -> None:
    inside = 0.9 * ISIS_HALF_SPACING_REAL4
    outside = 1.1 * ISIS_HALF_SPACING_REAL4
    mask = validity_mask(f64(NULL4 + inside, NULL4 - inside, NULL4 + outside, NULL4 - outside))
    # NULL4 + outside rounds to VALID_MIN4 (valid); NULL4 - outside rounds to the Lrs code.
    assert reasons(mask) == [R.NODATA, R.NODATA, R.VALID, R.ISIS_SATURATION]


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_isis_saturation_codes_are_invalid(dtype: type[np.floating[Any]]) -> None:
    mask = validity_mask(np.array([*SATURATIONS4, -3.0e38], dtype=dtype))
    assert reasons(mask) == [R.ISIS_SATURATION] * 4 + [R.VALID]


def test_declared_nodata_takes_precedence_over_isis_saturation() -> None:
    """GDAL products often declare -FLT_MAX as nodata; it is also the ISIS Hrs code."""
    mask = validity_mask(f32(-FLT_MAX, LOW_REPR_SAT4), nodata=-FLT_MAX)
    assert reasons(mask) == [R.NODATA, R.ISIS_SATURATION]


@pytest.mark.parametrize(
    ("dtype", "isis_null"),
    [(np.uint8, 0), (np.int16, -32768), (np.uint16, 0), (np.int32, -2147483648)],
)
def test_integer_isis_nulls_need_the_label(dtype: type[np.integer[Any]], isis_null: int) -> None:
    """ISIS integer nulls (0 for unsigned types) are valid values unless the label declares them."""
    values = np.array([isis_null, 3], dtype=dtype)
    assert reasons(validity_mask(values)) == [R.VALID, R.VALID]
    assert reasons(validity_mask(values, nodata=isis_null)) == [R.NODATA, R.VALID]


def test_isis_codes_do_not_apply_to_complex_values() -> None:
    """ISIS has no complex pixel type, so only declared nodata and non-finite values apply."""
    values = np.array([complex(NULL4, 0), 1 + 0j], dtype=np.complex64)
    assert reasons(validity_mask(values)) == [R.VALID, R.VALID]


def test_huge_float64_values_raise_no_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        mask = validity_mask(f64(1e300, -1e300, 2.0 * FLT_MAX, -2.0 * FLT_MAX))
    assert mask.valid.all()


# --- Support ------------------------------------------------------------------------------------


def test_outside_support() -> None:
    values = f32(1.0, 2.0, 3.0)
    support = np.array([True, False, True])
    assert reasons(validity_mask(values, support=support)) == [
        R.VALID,
        R.OUTSIDE_SUPPORT,
        R.VALID,
    ]
    none = validity_mask(values, support=np.zeros(3, dtype=bool))
    assert reasons(none) == [R.OUTSIDE_SUPPORT] * 3


def test_data_reasons_take_precedence_over_support() -> None:
    values = f32(-9999.0, math.nan, NULL4, LOW_REPR_SAT4, 1.0)
    mask = validity_mask(values, nodata=-9999.0, support=np.zeros(5, dtype=bool))
    assert reasons(mask) == [
        R.NODATA,
        R.NON_FINITE,
        R.NODATA,
        R.ISIS_SATURATION,
        R.OUTSIDE_SUPPORT,
    ]


def test_support_must_be_a_boolean_array_of_the_same_shape() -> None:
    values = np.ones((2, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="shape"):
        validity_mask(values, support=np.ones((3, 2), dtype=bool))
    with pytest.raises(TypeError, match="bool"):
        validity_mask(values, support=np.ones((2, 3), dtype=np.uint8))
    with pytest.raises(TypeError, match="bool"):
        validity_mask(values, support=[[True] * 3] * 2)  # type: ignore[arg-type]


# --- Input validation ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        [1.0, 2.0],
        np.ma.masked_array([1.0, 2.0], mask=[True, False]),
        np.array([True, False]),
        np.array(["a"]),
        np.array([1.0], dtype=object),
    ],
    ids=["list", "masked", "bool", "str", "object"],
)
def test_values_must_be_a_numeric_ndarray(values: Any) -> None:
    with pytest.raises(TypeError):
        validity_mask(values)


@pytest.mark.parametrize("nodata", [True, np.bool_(False), "0", [0, "x"], 1j, [[0.0]]])
def test_nodata_must_be_real_numbers(nodata: Any) -> None:
    with pytest.raises(TypeError, match="nodata"):
        validity_mask(f32(0.0), nodata=nodata)


@pytest.mark.parametrize(
    ("dtype", "nodata"),
    [
        (np.uint8, -1),
        (np.uint8, 256),
        (np.int16, 2.5),
        (np.int16, math.nan),
        (np.int16, math.inf),
        (np.float32, 1e39),
        (np.float32, -1e39),
        (np.complex64, math.nan),
    ],
)
def test_nodata_not_representable_in_the_dtype_is_refused(dtype: Any, nodata: float) -> None:
    with pytest.raises(ValueError, match="nodata"):
        validity_mask(np.zeros(2, dtype=dtype), nodata=nodata)


def test_shapes_are_preserved() -> None:
    values = np.full((2, 3, 4), 1.0, dtype=np.float32)
    values[1, 2, 3] = NULL4
    mask = validity_mask(values)
    assert mask.valid.shape == mask.reason.shape == (2, 3, 4)
    assert mask.reason[1, 2, 3] == R.NODATA
    assert int(mask.valid.sum()) == 23
    scalar = validity_mask(np.array(math.nan, dtype=np.float64))
    assert scalar.reason.shape == ()
    assert int(scalar.reason) == R.NON_FINITE


# --- Nothing is filled or changed ---------------------------------------------------------------


def test_input_is_unchanged_and_nothing_is_filled() -> None:
    values = f32(-9999.0, math.nan, NULL4, 0.0, 2.0)
    before = values.tobytes()
    mask = validity_mask(values, nodata=-9999.0)
    assert values.tobytes() == before
    assert [field.name for field in dataclasses.fields(ValidityMask)] == ["valid", "reason"]
    assert mask.valid.dtype == np.bool_
    assert mask.reason.dtype == np.uint8
    for array in (mask.valid, mask.reason):
        assert not array.flags.writeable
        assert not np.shares_memory(array, values)


def test_validity_mask_record_enforces_its_invariant() -> None:
    reason = np.array([0, 1], dtype=np.uint8)
    ValidityMask(valid=np.array([True, False]), reason=reason)
    with pytest.raises(ValueError, match="valid"):
        ValidityMask(valid=np.array([True, True]), reason=reason)
    with pytest.raises(ValueError, match="shape"):
        ValidityMask(valid=np.array([True]), reason=reason)
    with pytest.raises(ValueError, match="reason"):
        ValidityMask(valid=np.array([False]), reason=np.array([9], dtype=np.uint8))
    with pytest.raises(TypeError):
        ValidityMask(valid=np.array([1]), reason=np.array([0], dtype=np.uint8))
    with pytest.raises(TypeError):
        ValidityMask(valid=np.array([True]), reason=np.array([0], dtype=np.int64))


def test_reason_counts_cover_every_pixel() -> None:
    values = f32(-9999.0, math.nan, NULL4, LOW_REPR_SAT4, 1.0, 2.0)
    mask = validity_mask(values, nodata=-9999.0, support=np.array([True] * 5 + [False]))
    counts = mask.reason_counts()
    assert counts == {
        R.VALID: 1,
        R.NODATA: 2,
        R.NON_FINITE: 1,
        R.OUTSIDE_SUPPORT: 1,
        R.ISIS_SATURATION: 1,
    }
    assert list(counts) == list(InvalidReason)
    assert all(type(count) is int for count in counts.values())


# --- Property tests against an independent per-pixel oracle -------------------------------------


SPECIALS = (NULL4, *SATURATIONS4, VALID_MIN4, 0.0, -0.0, -9999.0, math.nan, math.inf, -math.inf)
NODATA_CHOICES: tuple[float | None, ...] = (None, 0.0, -9999.0, math.nan, -FLT_MAX)


def _isis_code(value: float) -> int | None:
    """The float32 bit pattern this double rounds to, or None if it overflows float32."""
    try:
        packed = struct.pack("<f", value)
    except OverflowError:
        return None
    bits: int = struct.unpack("<I", packed)[0]
    return bits


def _oracle(value: float, nodata: float | None, inside: bool, *, single: bool) -> InvalidReason:
    if nodata is not None:
        # A float32 array stores float32(nodata); struct rounds the same way.
        declared = struct.unpack("<f", struct.pack("<f", nodata))[0] if single else nodata
        if (math.isnan(declared) and math.isnan(value)) or value == declared:
            return R.NODATA
    code = _isis_code(value) if not math.isnan(value) else None
    if code == 0xFF7FFFFB:
        return R.NODATA
    if code in (0xFF7FFFFC, 0xFF7FFFFD, 0xFF7FFFFE, 0xFF7FFFFF):
        return R.ISIS_SATURATION
    if not math.isfinite(value):
        return R.NON_FINITE
    if not inside:
        return R.OUTSIDE_SUPPORT
    return R.VALID


@st.composite
def _case(
    draw: st.DrawFn, dtype: type[np.floating[Any]], width: Literal[32, 64]
) -> tuple[npt.NDArray[Any], npt.NDArray[np.bool_], float | None]:
    shape = draw(hnp.array_shapes(min_dims=1, max_dims=3, max_side=5))
    elements = st.one_of(st.floats(width=width), st.sampled_from(SPECIALS))
    if width == 64:
        near = st.floats(min_value=-3.40283e38, max_value=-3.40282e38)
        elements = st.one_of(elements, near)
    values = draw(hnp.arrays(dtype, shape, elements=elements))
    support = draw(hnp.arrays(np.bool_, shape))
    nodata = draw(st.sampled_from(NODATA_CHOICES))
    return values, support, nodata


def _check_against_oracle(
    values: npt.NDArray[Any], support: npt.NDArray[np.bool_], nodata: float | None, *, single: bool
) -> None:
    before = values.tobytes()
    mask = validity_mask(values, nodata=nodata, support=support)
    assert values.tobytes() == before
    expected = [
        int(_oracle(float(v), nodata, bool(s), single=single))
        for v, s in zip(values.ravel(), support.ravel(), strict=True)
    ]
    assert reasons(mask) == expected
    assert np.array_equal(mask.valid, mask.reason == R.VALID)


@given(_case(np.float32, 32))
def test_float32_masks_match_the_oracle(
    case: tuple[npt.NDArray[Any], npt.NDArray[np.bool_], float | None],
) -> None:
    _check_against_oracle(*case, single=True)


@given(_case(np.float64, 64))
def test_float64_masks_match_the_oracle(
    case: tuple[npt.NDArray[Any], npt.NDArray[np.bool_], float | None],
) -> None:
    _check_against_oracle(*case, single=False)


# --- Valid fraction -----------------------------------------------------------------------------


def test_valid_fraction_counts() -> None:
    mask = validity_mask(f32(1.0, math.nan, 2.0, 3.0))
    result = valid_fraction(mask)
    assert result == ValidFraction(valid_pixels=3, total_pixels=4)
    assert result.fraction == 0.75
    assert result.invalid_pixels == 1
    assert type(result.valid_pixels) is int
    assert type(result.total_pixels) is int


def test_valid_fraction_of_a_boolean_array() -> None:
    support = np.array([[True, False], [False, False]])
    assert valid_fraction(support) == ValidFraction(valid_pixels=1, total_pixels=4)
    assert valid_fraction(support).fraction == 0.25


def test_valid_fraction_bounds_on_non_empty_windows() -> None:
    assert valid_fraction(np.ones(3, dtype=bool)).fraction == 1.0
    none = valid_fraction(validity_mask(f32(math.nan, NULL4)))
    assert none.fraction == 0.0
    assert (none.valid_pixels, none.total_pixels) == (0, 2)


def test_outside_support_pixels_are_not_covered() -> None:
    mask = validity_mask(f32(1.0, 2.0, 3.0, 4.0), support=np.array([True, True, True, False]))
    assert valid_fraction(mask) == ValidFraction(valid_pixels=3, total_pixels=4)


@pytest.mark.parametrize("shape", [(0,), (0, 3), (4, 0, 2)])
def test_valid_fraction_of_an_empty_window_raises(shape: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="empty"):
        valid_fraction(np.zeros(shape, dtype=bool))
    with pytest.raises(ValueError, match="empty"):
        valid_fraction(validity_mask(np.zeros(shape, dtype=np.float32)))


def test_valid_fraction_refuses_non_boolean_arrays() -> None:
    """A reason array (0 = valid) would otherwise be counted inverted."""
    with pytest.raises(TypeError, match="bool"):
        valid_fraction(np.array([0, 0, 1], dtype=np.uint8))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="bool"):
        valid_fraction([True, False])  # type: ignore[arg-type]


def test_valid_fraction_record_is_consistent() -> None:
    with pytest.raises(ValueError):
        ValidFraction(valid_pixels=3, total_pixels=2)
    with pytest.raises(ValueError):
        ValidFraction(valid_pixels=0, total_pixels=0)
    with pytest.raises(ValueError):
        ValidFraction(valid_pixels=-1, total_pixels=2)
