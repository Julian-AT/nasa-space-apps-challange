"""UAVSAR covariance to circular SC, OC and CPR by an explicit basis transformation (PRD 6.2, 7).

Fixtures F6 and F7 come from PRD 7. The implementation is compared with two oracles in
``pipeline/tests/polarimetry``: the PRD 6.2 expanded formulas and a separately written
single-scatterer Jones-vector calculation. Neither imports the code under test. Comparisons use
the PRD 7 arithmetic tolerances on float64, rtol = 1e-10 and atol = 1e-12, which are also the
default Hermitian and PSD tolerance here; a real-data tolerance is an open decision (PRD 7).
"""

from __future__ import annotations

import dataclasses
import inspect
import math
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from terraluna_pipeline.polarimetry import (
    ARITHMETIC_TOLERANCE,
    CIRCULAR_BASIS,
    COVARIANCE_HH_HV_VV,
    COVARIANCE_HH_SQRT2HV_VV,
    NEISH_SC_LL_CONVENTION,
    OPPOSITE_SC_CONVENTION,
    CovarianceCircularPowers,
    InvalidReason,
    ObsState,
    StatedArray,
    Tolerance,
    circular_from_covariance,
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

LL = NEISH_SC_LL_CONVENTION
OPP = OPPOSITE_SC_CONVENTION
RAW = COVARIANCE_HH_HV_VV
SQRT2 = COVARIANCE_HH_SQRT2HV_VV

POWER_FIELDS = ("total", "sc", "oc", "base_sc", "handedness_term")
RATIO_FIELDS = ("cpr", "handedness_ratio")
ALL_FIELDS = POWER_FIELDS + RATIO_FIELDS

SQRT2_NORMALIZATION = np.array([1.0, math.sqrt(2.0), 1.0])


def convert(
    covariance: npt.ArrayLike,
    *,
    convention: Any = LL,
    ordering: Any = RAW,
    masked: npt.ArrayLike = False,
    tolerance: Tolerance = ARITHMETIC_TOLERANCE,
) -> CovarianceCircularPowers:
    """Convert with declared arguments; a test helper, not a production default."""
    return circular_from_covariance(
        covariance,
        ordering=ordering,
        convention=convention,
        product_masked=masked,
        tolerance=tolerance,
    )


def to_sqrt2_ordering(covariance: npt.NDArray[np.complex128]) -> npt.NDArray[np.complex128]:
    """Express an (HH, HV, VV) covariance in the (HH, sqrt2 HV, VV) ordering, k' = N k."""
    result: npt.NDArray[np.complex128] = covariance * np.outer(
        SQRT2_NORMALIZATION, SQRT2_NORMALIZATION
    )
    return result


def matrix(
    a: float, b: float, v: float, c: complex, d: complex, e: complex
) -> npt.NDArray[np.complex128]:
    """The (HH, HV, VV) covariance with the PRD 6.2 elements, ``C_ij = <k_i k_j*>``."""
    return np.array(
        [
            [a, d, c],
            [np.conj(d), v, e],
            [np.conj(c), np.conj(e), b],
        ],
        dtype=np.complex128,
    )


def assert_hermitian_psd(covariance: npt.NDArray[np.complex128]) -> npt.NDArray[np.float64]:
    """Assert a fixture is Hermitian and PSD (PRD 7) and return its eigenvalues, ascending."""
    np.testing.assert_array_equal(covariance, covariance.conj().T)
    eigenvalues: npt.NDArray[np.float64] = np.linalg.eigvalsh(covariance)
    assert eigenvalues.min() >= -ATOL
    return eigenvalues


def assert_value(result: StatedArray, expected: float) -> None:
    assert np.all(result.state == VALID)
    assert np.all(result.reason == InvalidReason.NONE)
    np.testing.assert_allclose(result.value, expected, rtol=RTOL, atol=ATOL)


def assert_unavailable(result: StatedArray, state: ObsState, reason: InvalidReason) -> None:
    """Only a state and a reason: no finite number, no huge number, no 0.0."""
    assert np.all(result.state == state)
    assert np.all(result.reason == reason)
    assert np.all(np.isnan(result.value))


def pixel(result: StatedArray, index: int) -> StatedArray:
    """One element of a stated array, with its state and reason."""
    return StatedArray(
        value=result.value[index], state=result.state[index], reason=result.reason[index]
    )


def assert_nothing_published(
    result: CovarianceCircularPowers, state: ObsState, reason: InvalidReason
) -> None:
    for name in ALL_FIELDS:
        assert_unavailable(getattr(result, name), state, reason)


# --- Fixtures F6 and F7 (PRD 7), asserted Hermitian PSD before use ----------------------------

F6_SCATTERER: jones_oracle.Scatterer = (1.0, 0.2j, 0.0)
"""PRD 7: F6 corresponds to HH = 1, HV = 0.2i, VV = 0 (rank one)."""


def f6_covariance() -> npt.NDArray[np.complex128]:
    """PRD 7, F6, asserted Hermitian PSD of rank one before every use."""
    covariance = jones_oracle.covariance_of([F6_SCATTERER])
    # |0.2i|**2 is 0.04000000000000001 in float64, so compare to the PRD 7 tolerance.
    expected = matrix(1.0, 0.0, 0.04, 0.0, -0.2j, 0.0)
    np.testing.assert_allclose(covariance, expected, rtol=RTOL, atol=ATOL)
    eigenvalues = assert_hermitian_psd(covariance)
    np.testing.assert_allclose(eigenvalues, [0.0, 0.0, 1.04], rtol=RTOL, atol=ATOL)
    return covariance


F7_EIGENVALUES = {0.9: [0.1, 0.1, 1.9], 0.1: [0.1, 0.9, 1.1]}
"""PRD 7: F7's two matrices have eigenvalues (0.1, 0.1, 1.9) and (0.1, 0.9, 1.1)."""


def f7_covariance(c: float) -> npt.NDArray[np.complex128]:
    """PRD 7, F7: A = B = 1, V = 0.1, D = E = 0 and the given real C, asserted Hermitian PSD
    with the PRD eigenvalues before every use."""
    covariance = matrix(1.0, 1.0, 0.1, c, 0.0, 0.0)
    eigenvalues = assert_hermitian_psd(covariance)
    np.testing.assert_allclose(eigenvalues, F7_EIGENVALUES[c], rtol=RTOL, atol=ATOL)
    return covariance


def f7_scatterers(c: float) -> list[jones_oracle.Scatterer]:
    """F7 as a sum of three scatterers, from its eigenvectors (1, 0, 1)/sqrt2 with eigenvalue
    1 + C, (1, 0, -1)/sqrt2 with 1 - C, and (0, 1, 0) with V = 0.1."""
    plus, minus = math.sqrt((1.0 + c) / 2.0), math.sqrt((1.0 - c) / 2.0)
    return [(plus, 0.0, plus), (minus, 0.0, -minus), (0.0, math.sqrt(0.1), 0.0)]


def test_F6_fixture_is_a_rank_one_hermitian_psd_covariance() -> None:
    covariance = f6_covariance()
    eigenvalues = assert_hermitian_psd(covariance)
    np.testing.assert_allclose(eigenvalues, [0.0, 0.0, 1.04], rtol=RTOL, atol=ATOL)
    assert np.linalg.matrix_rank(covariance) == 1


@pytest.mark.parametrize(
    ("c", "expected"), [(0.9, [0.1, 0.1, 1.9]), (0.1, [0.1, 0.9, 1.1])], ids=["C=0.9", "C=0.1"]
)
def test_F7_fixtures_are_hermitian_psd_with_the_prd_eigenvalues(
    c: float, expected: list[float]
) -> None:
    eigenvalues = assert_hermitian_psd(f7_covariance(c))
    np.testing.assert_allclose(eigenvalues, expected, rtol=RTOL, atol=ATOL)


@pytest.mark.parametrize("ordering", [RAW, SQRT2])
def test_F6_full_covariance_handedness(ordering: str) -> None:
    covariance = f6_covariance()
    if ordering == SQRT2:
        covariance = to_sqrt2_ordering(covariance)
    ll = convert(covariance, convention=LL, ordering=ordering)
    opp = convert(covariance, convention=OPP, ordering=ordering)
    assert_value(ll.oc, 0.25)
    assert_value(ll.sc, 0.49)
    assert_value(opp.oc, 0.25)
    assert_value(opp.sc, 0.09)
    assert_value(ll.cpr, 0.49 / 0.25)
    assert_value(opp.cpr, 0.09 / 0.25)
    for result in (ll, opp):
        assert_value(result.base_sc, 0.29)
        assert_value(result.handedness_term, -0.2)
        assert_value(result.handedness_ratio, -0.2 / 0.29)
    assert_value(ll.total, 0.74)
    assert_value(opp.total, 0.34)


def test_F6_matches_both_oracles() -> None:
    covariance = f6_covariance()
    expanded = expanded_oracle.expanded_from_matrix(covariance)
    for convention, transmit, sc in (
        (LL, jones_oracle.TRANSMIT_LL, expanded.sc_ll),
        (OPP, jones_oracle.TRANSMIT_OPPOSITE, expanded.sc_opp),
    ):
        jones = jones_oracle.single_scatterer_powers(F6_SCATTERER, transmit)
        result = convert(covariance, convention=convention)
        assert_value(result.sc, sc)
        assert_value(result.sc, jones.sc)
        assert_value(result.oc, expanded.oc)
        assert_value(result.oc, jones.oc)
        assert_value(result.total, jones.total)
        assert_value(result.base_sc, expanded.base_sc)
        assert_value(result.handedness_term, expanded.handedness_term)


@pytest.mark.parametrize("c", [0.9, 0.1])
def test_F7_matches_both_oracles(c: float) -> None:
    covariance = f7_covariance(c)
    scatterers = f7_scatterers(c)
    np.testing.assert_allclose(
        jones_oracle.covariance_of(scatterers), covariance, rtol=RTOL, atol=ATOL
    )
    expanded = expanded_oracle.expanded_from_matrix(covariance)
    for convention, transmit, sc in (
        (LL, jones_oracle.TRANSMIT_LL, expanded.sc_ll),
        (OPP, jones_oracle.TRANSMIT_OPPOSITE, expanded.sc_opp),
    ):
        jones = jones_oracle.scatterer_sum_powers(scatterers, transmit)
        result = convert(covariance, convention=convention)
        assert_value(result.sc, sc)
        assert_value(result.sc, jones.sc)
        assert_value(result.oc, expanded.oc)
        assert_value(result.oc, jones.oc)
        assert_value(result.total, jones.total)
        assert_value(result.cpr, jones.sc / jones.oc)
        assert_value(result.base_sc, expanded.base_sc)
        assert_value(result.handedness_term, expanded.handedness_term)


def test_F7_same_hh_and_hv_powers_give_different_cpr() -> None:
    high, low = f7_covariance(0.9), f7_covariance(0.1)
    # Same HH, VV and HV powers: the two-channel (HH, HV) data cannot tell them apart.
    assert high[0, 0] == low[0, 0]
    assert high[1, 1] == low[1, 1]
    assert high[2, 2] == low[2, 2]
    for convention in (LL, OPP):
        assert_value(convert(high, convention=convention).cpr, 3.0 / 19.0)
        assert_value(convert(low, convention=convention).cpr, 1.0)
    # The PRD prints 0.1578947...; agree to the printed digits as well.
    np.testing.assert_allclose(convert(high).cpr.value, 0.1578947, rtol=0.0, atol=5e-8)


def test_F7_two_channel_data_cannot_determine_cpr() -> None:
    high = convert(f7_covariance(0.9)).cpr.value
    low = convert(f7_covariance(0.1)).cpr.value
    assert not np.isclose(high, low, rtol=RTOL, atol=ATOL)
    assert abs(float(low) - float(high)) > 0.8


def test_F7_has_no_handedness_term() -> None:
    for c in (0.9, 0.1):
        result = convert(f7_covariance(c))
        assert_value(result.handedness_term, 0.0)
        assert_value(result.handedness_ratio, 0.0)
        np.testing.assert_allclose(
            result.sc.value, convert(f7_covariance(c), convention=OPP).sc.value, rtol=RTOL
        )


# --- The basis transformation ------------------------------------------------------------------


def test_circular_basis_is_unitary_and_read_only() -> None:
    assert CIRCULAR_BASIS.shape == (3, 3)
    assert CIRCULAR_BASIS.dtype == np.complex128
    np.testing.assert_allclose(
        CIRCULAR_BASIS @ CIRCULAR_BASIS.conj().T, np.eye(3), rtol=RTOL, atol=ATOL
    )
    with pytest.raises(ValueError, match="read-only"):
        CIRCULAR_BASIS[0, 0] = 1.0


def test_circular_basis_rows_are_the_declared_transmit_projections() -> None:
    """Row 0 gives the SC_LL amplitude, row 1 sqrt2 times OC's, row 2 the SC_opp amplitude."""
    hh, hv, vv = 0.3 - 0.4j, 0.25 + 0.1j, -0.7 + 0.2j
    k_sqrt2 = np.array([hh, math.sqrt(2.0) * hv, vv])
    field_ll = jones_oracle.scattering_matrix((hh, hv, vv)) @ jones_oracle.TRANSMIT_LL
    field_opp = jones_oracle.scattering_matrix((hh, hv, vv)) @ jones_oracle.TRANSMIT_OPPOSITE
    amplitudes = CIRCULAR_BASIS @ k_sqrt2
    np.testing.assert_allclose(amplitudes[0], jones_oracle.TRANSMIT_LL @ field_ll, rtol=RTOL)
    np.testing.assert_allclose(
        amplitudes[1] / math.sqrt(2.0), jones_oracle.TRANSMIT_LL.conj() @ field_ll, rtol=RTOL
    )
    np.testing.assert_allclose(
        amplitudes[1] / math.sqrt(2.0),
        jones_oracle.TRANSMIT_OPPOSITE.conj() @ field_opp,
        rtol=RTOL,
    )
    np.testing.assert_allclose(amplitudes[2], jones_oracle.TRANSMIT_OPPOSITE @ field_opp, rtol=RTOL)


def test_docstring_states_the_ordering_transmit_vector_and_convention() -> None:
    doc = inspect.getdoc(circular_from_covariance)
    assert doc is not None
    assert "(HH, sqrt2 HV, VV)" in doc
    assert "(HH, HV, VV)" in doc
    assert "(1, -i)/sqrt2" in doc
    assert "Neish" in doc
    assert "unitary" in doc


def test_orderings_and_conventions_are_named_string_constants() -> None:
    constants = (RAW, SQRT2, LL, OPP)
    assert all(isinstance(constant, str) and constant for constant in constants)
    assert len(set(constants)) == len(constants)
    result = convert(f6_covariance(), convention=OPP, ordering=RAW)
    assert result.convention == OPP
    assert result.ordering == RAW


@pytest.mark.parametrize("name", ["ordering", "convention", "product_masked"])
def test_declarations_are_required_keyword_arguments(name: str) -> None:
    parameter = inspect.signature(circular_from_covariance).parameters[name]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_tolerance_defaults_to_the_arithmetic_tolerance() -> None:
    parameter = inspect.signature(circular_from_covariance).parameters["tolerance"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default == ARITHMETIC_TOLERANCE
    assert convert(f6_covariance()).tolerance == ARITHMETIC_TOLERANCE


def test_missing_convention_raises() -> None:
    with pytest.raises(TypeError):
        circular_from_covariance(  # type: ignore[call-arg]
            f6_covariance(), ordering=RAW, product_masked=False
        )


UNKNOWN_CONVENTIONS: list[object] = [
    None,
    "",
    "LL",
    "SC_LL",
    "LR",
    NEISH_SC_LL_CONVENTION.lower(),
    NEISH_SC_LL_CONVENTION + " ",
    "NEISH_SC_LR_TX_1_PLUS_I",
    1,
]


@pytest.mark.parametrize("convention", UNKNOWN_CONVENTIONS)
def test_unknown_or_missing_convention_raises_and_returns_nothing(convention: object) -> None:
    with pytest.raises(ValueError, match="convention"):
        convert(f6_covariance(), convention=convention)


@pytest.mark.parametrize(
    "ordering", [None, "", "HH_VV_HV", COVARIANCE_HH_HV_VV.lower(), "lexicographic", 0]
)
def test_unknown_or_missing_ordering_raises_and_returns_nothing(ordering: object) -> None:
    with pytest.raises(ValueError, match="ordering"):
        convert(f6_covariance(), ordering=ordering)


def test_handedness_conventions_give_different_sc_and_the_same_oc() -> None:
    covariance = f6_covariance()
    ll, opp = convert(covariance, convention=LL), convert(covariance, convention=OPP)
    assert not np.isclose(ll.sc.value, opp.sc.value)
    np.testing.assert_array_equal(ll.oc.value, opp.oc.value)
    np.testing.assert_array_equal(ll.base_sc.value, opp.base_sc.value)
    np.testing.assert_array_equal(ll.handedness_term.value, opp.handedness_term.value)


# --- Record: handedness is not verified, so nothing is Mini-RF-equivalent (PRD 6.2) -----------


def test_handedness_is_unverified_by_default() -> None:
    result = convert(f6_covariance())
    assert result.handedness_verified is False
    assert "handedness_verified" not in inspect.signature(circular_from_covariance).parameters


def test_only_a_caller_marks_handedness_verified() -> None:
    result = convert(f6_covariance())
    verified = dataclasses.replace(result, handedness_verified=True)
    assert verified.handedness_verified is True
    assert result.handedness_verified is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.handedness_verified = True  # type: ignore[misc]


def test_record_refuses_a_non_boolean_handedness_flag() -> None:
    result = convert(f6_covariance())
    with pytest.raises(TypeError, match="handedness_verified"):
        dataclasses.replace(result, handedness_verified=1)  # type: ignore[arg-type]


def test_record_carries_its_declarations() -> None:
    tolerance = Tolerance(rtol=1e-9, atol=0.0)
    result = convert(f6_covariance(), convention=OPP, ordering=RAW, tolerance=tolerance)
    assert result.convention == OPP
    assert result.ordering == RAW
    assert result.tolerance == tolerance


def stated(value: float, state: ObsState, reason: InvalidReason) -> StatedArray:
    return StatedArray(
        value=np.array(value),
        state=np.array(state, dtype=np.uint8),
        reason=np.array(reason.value),
    )


def test_record_refuses_a_valid_cpr_without_valid_powers() -> None:
    result = convert(f6_covariance())
    oc_missing = stated(math.nan, MISSING, InvalidReason.PRODUCT_MASKED)
    with pytest.raises(ValueError, match="CPR"):
        dataclasses.replace(result, oc=oc_missing)
    with pytest.raises(ValueError, match="CPR"):
        dataclasses.replace(result, oc=stated(0.0, VALID, InvalidReason.NONE))
    with pytest.raises(ValueError, match="CPR"):
        dataclasses.replace(result, sc=stated(-1.0, VALID, InvalidReason.NONE))


def test_record_refuses_a_valid_ratio_without_a_positive_base_sc() -> None:
    result = convert(f6_covariance())
    with pytest.raises(ValueError, match="base_sc"):
        dataclasses.replace(result, base_sc=stated(0.0, VALID, InvalidReason.NONE))
    with pytest.raises(ValueError, match="base_sc"):
        dataclasses.replace(
            result, handedness_term=stated(math.nan, INVALID, InvalidReason.COV_NOT_PSD)
        )


def test_record_refuses_mismatched_shapes_and_undeclared_choices() -> None:
    result = convert(f6_covariance())
    two = convert(np.stack([f6_covariance(), f6_covariance()]))
    with pytest.raises(ValueError, match="shape"):
        dataclasses.replace(result, oc=two.oc)
    with pytest.raises(ValueError, match="convention"):
        dataclasses.replace(result, convention="LL")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ordering"):
        dataclasses.replace(result, ordering="HH_VV_HV")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="tolerance"):
        dataclasses.replace(result, tolerance=(1e-10, 1e-12))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="StatedArray"):
        dataclasses.replace(result, sc=np.array(0.49))  # type: ignore[arg-type]


# --- Invalid covariances: flagged with their cause, never repaired (PRD 7) -------------------


def test_covariance_reason_codes_exist_and_are_distinct() -> None:
    codes = {r.value for r in InvalidReason}
    required = {
        "COV_NONFINITE",
        "COV_NOT_HERMITIAN",
        "COV_NEGATIVE_DIAGONAL",
        "COV_NOT_PSD",
        "OC_NONPOSITIVE",
        "SC_NEGATIVE",
        "BASE_SC_NONPOSITIVE",
        "PRODUCT_MASKED",
    }
    assert required <= codes


def non_hermitian() -> npt.NDArray[np.complex128]:
    covariance = f7_covariance(0.9)
    covariance[0, 2] = 0.5  # C[2, 0] stays 0.9
    return covariance


def imaginary_diagonal() -> npt.NDArray[np.complex128]:
    covariance = f7_covariance(0.9)
    covariance[1, 1] = 0.1 + 0.01j
    return covariance


def negative_eigenvalue() -> npt.NDArray[np.complex128]:
    """Positive diagonal, eigenvalues 1 - 1.1, 0.1 and 1 + 1.1."""
    covariance = matrix(1.0, 1.0, 0.1, 1.1, 0.0, 0.0)
    assert np.all(np.diag(covariance).real > 0)
    assert np.linalg.eigvalsh(covariance).min() < -0.09
    return covariance


def negative_diagonal() -> npt.NDArray[np.complex128]:
    return matrix(1.0, 1.0, -0.1, 0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    ("covariance", "reason"),
    [
        (non_hermitian(), InvalidReason.COV_NOT_HERMITIAN),
        (imaginary_diagonal(), InvalidReason.COV_NOT_HERMITIAN),
        (negative_eigenvalue(), InvalidReason.COV_NOT_PSD),
        (negative_diagonal(), InvalidReason.COV_NEGATIVE_DIAGONAL),
    ],
    ids=["off-diagonal", "imaginary-diagonal", "negative-eigenvalue", "negative-diagonal"],
)
def test_nonphysical_covariance_is_invalid_with_its_cause(
    covariance: npt.NDArray[np.complex128], reason: InvalidReason
) -> None:
    assert_nothing_published(convert(covariance), INVALID, reason)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("part", ["real", "imag"])
@pytest.mark.parametrize("position", [(0, 0), (1, 1), (0, 1), (1, 0), (2, 0), (1, 2)])
def test_nonfinite_covariance_is_invalid(
    value: float, part: str, position: tuple[int, int]
) -> None:
    covariance = f6_covariance()
    element = covariance[position]
    covariance[position] = (
        complex(value, element.imag) if part == "real" else complex(element.real, value)
    )
    assert_nothing_published(convert(covariance), INVALID, InvalidReason.COV_NONFINITE)


def test_each_invalid_case_has_a_distinct_cause() -> None:
    nan = f6_covariance()
    nan[0, 0] = math.nan
    reasons = {
        str(convert(c).sc.reason)
        for c in (non_hermitian(), negative_eigenvalue(), negative_diagonal(), nan)
    }
    assert len(reasons) == 4


def test_rules_apply_in_order_and_the_product_mask_wins() -> None:
    nan_and_not_hermitian = non_hermitian()
    nan_and_not_hermitian[1, 1] = math.nan
    assert_nothing_published(convert(nan_and_not_hermitian), INVALID, InvalidReason.COV_NONFINITE)
    hermitian_and_negative = negative_diagonal()
    hermitian_and_negative[0, 2] = 2.0  # also not Hermitian
    assert_nothing_published(
        convert(hermitian_and_negative), INVALID, InvalidReason.COV_NOT_HERMITIAN
    )
    for covariance in (nan_and_not_hermitian, negative_eigenvalue(), f6_covariance()):
        assert_nothing_published(
            convert(covariance, masked=True), MISSING, InvalidReason.PRODUCT_MASKED
        )


def test_hermitian_mismatch_within_the_tolerance_is_accepted() -> None:
    covariance = f7_covariance(0.9)
    covariance[0, 2] += 5e-11  # beside rtol * scale + atol = 1e-10 * 1 + 1e-12
    result = convert(covariance)
    assert np.all(result.cpr.state == VALID)
    np.testing.assert_allclose(result.cpr.value, 3.0 / 19.0, rtol=1e-9)


def test_hermitian_mismatch_beyond_the_tolerance_is_refused() -> None:
    covariance = f7_covariance(0.9)
    covariance[0, 2] += 2e-10
    assert_nothing_published(convert(covariance), INVALID, InvalidReason.COV_NOT_HERMITIAN)


@pytest.mark.parametrize("exponent", [-1000, -600, -40, 0, 40, 600, 1000])
def test_hermitian_tolerance_scales_with_the_matrix(exponent: int) -> None:
    scale = 2.0**exponent
    accepted = f7_covariance(0.9) * scale
    accepted[0, 2] += 5e-11 * scale
    refused = f7_covariance(0.9) * scale
    refused[0, 2] += 2e-10 * scale
    tolerance = Tolerance(rtol=1e-10, atol=0.0)
    assert np.all(convert(accepted, tolerance=tolerance).oc.state == VALID)
    assert_nothing_published(
        convert(refused, tolerance=tolerance), INVALID, InvalidReason.COV_NOT_HERMITIAN
    )


def test_absolute_hermitian_tolerance_applies_near_zero() -> None:
    small = f7_covariance(0.9) * 1e-14
    small[0, 2] += 5e-13
    assert np.all(convert(small).oc.state == VALID)
    small[0, 2] += 2e-12
    assert_nothing_published(convert(small), INVALID, InvalidReason.COV_NOT_HERMITIAN)


def near_plane(excess: float) -> npt.NDArray[np.complex128]:
    """Eigenvalues 2 + excess, 0 and -excess; diagonal (1, 0, 1); SC_LL = -excess / 2."""
    return matrix(1.0, 1.0, 0.0, 1.0 + excess, 0.0, 0.0)


def test_negative_eigenvalue_within_the_tolerance_is_not_clipped() -> None:
    covariance = near_plane(5e-11)
    expanded = expanded_oracle.expanded_from_matrix(covariance)
    assert expanded.sc_ll < 0
    result = convert(covariance)
    # Accepted and reported as computed: a negative SC, never clipped to zero.
    assert result.sc.state == VALID
    assert result.sc.value < 0
    np.testing.assert_allclose(result.sc.value, expanded.sc_ll, rtol=1e-6, atol=0.0)
    assert_unavailable(result.cpr, INVALID, InvalidReason.SC_NEGATIVE)
    assert_value(result.oc, expanded.oc)


def test_negative_eigenvalue_beyond_the_tolerance_is_not_psd() -> None:
    assert_nothing_published(convert(near_plane(2e-10)), INVALID, InvalidReason.COV_NOT_PSD)


def test_negative_diagonal_within_the_tolerance_is_reported_as_computed() -> None:
    result = convert(matrix(1.0, 1.0, -5e-11, 0.0, 0.0, 0.0))
    assert_value(result.base_sc, 0.5 - 5e-11)
    assert_value(result.oc, 0.5)


def test_negative_diagonal_beyond_the_tolerance_is_its_own_cause() -> None:
    assert_nothing_published(
        convert(matrix(1.0, 1.0, -2e-10, 0.0, 0.0, 0.0)),
        INVALID,
        InvalidReason.COV_NEGATIVE_DIAGONAL,
    )


def test_tolerance_argument_decides_the_psd_outcome() -> None:
    covariance = near_plane(1e-9)
    assert_nothing_published(convert(covariance), INVALID, InvalidReason.COV_NOT_PSD)
    loose = convert(covariance, tolerance=Tolerance(rtol=1e-8, atol=0.0))
    assert loose.oc.state == VALID


@pytest.mark.parametrize("exponent", [-1000, -500, 0, 500, 1000])
def test_psd_tolerance_scales_with_the_matrix(exponent: int) -> None:
    scale = 2.0**exponent
    tolerance = Tolerance(rtol=1e-10, atol=0.0)
    rank_one = jones_oracle.covariance_of([(0.3 + 0.1j, -0.2 + 0.5j, 0.7 - 0.4j)]) * scale
    assert np.all(convert(rank_one, tolerance=tolerance).oc.state == VALID)
    assert_nothing_published(
        convert(near_plane(2e-10) * scale, tolerance=tolerance), INVALID, InvalidReason.COV_NOT_PSD
    )


def test_validity_does_not_depend_on_the_declared_ordering() -> None:
    """The rules run on the (HH, sqrt2 HV, VV) form, so both orderings decide alike.

    HV dominates: max |C_ij| is V = 1 in the (HH, HV, VV) ordering but 2 V = 2 in the
    (HH, sqrt2 HV, VV) ordering. The smallest eigenvalue, -1.5e-10, lies between the two
    allowances 1e-12 + 1e-10 * 1 and 1e-12 + 1e-10 * 2.
    """
    covariance = matrix(0.01, 0.01, 1.0, 0.01 + 1.5e-10, 0.0, 0.0)
    raw = convert(covariance, ordering=RAW)
    sqrt2 = convert(to_sqrt2_ordering(covariance), ordering=SQRT2)
    for name in ALL_FIELDS:
        np.testing.assert_array_equal(getattr(raw, name).state, getattr(sqrt2, name).state)
        np.testing.assert_array_equal(getattr(raw, name).reason, getattr(sqrt2, name).reason)
    assert raw.oc.state == VALID


MODERATE = st.floats(min_value=-1e100, max_value=1e100, allow_subnormal=False)


@settings(deadline=None)
@given(
    st.tuples(MODERATE, MODERATE, MODERATE),
    st.tuples(*(st.builds(complex, MODERATE, MODERATE) for _ in range(3))),
)
def test_both_orderings_give_the_same_states_for_any_hermitian_matrix(
    diagonal: tuple[float, float, float], off_diagonal: tuple[complex, complex, complex]
) -> None:
    a, v, b = diagonal
    c, d, e = off_diagonal
    covariance = matrix(a, b, v, c, d, e)
    raw = convert(covariance, ordering=RAW)
    sqrt2 = convert(to_sqrt2_ordering(covariance), ordering=SQRT2)
    for name in ALL_FIELDS:
        np.testing.assert_array_equal(getattr(raw, name).state, getattr(sqrt2, name).state)
        np.testing.assert_array_equal(getattr(raw, name).reason, getattr(sqrt2, name).reason)
        np.testing.assert_allclose(
            getattr(raw, name).value, getattr(sqrt2, name).value, rtol=RTOL, atol=0.0
        )


def test_input_is_not_modified() -> None:
    covariance = near_plane(5e-11)
    before = covariance.copy()
    convert(covariance)
    convert(to_sqrt2_ordering(covariance), ordering=SQRT2)
    np.testing.assert_array_equal(covariance, before)


# --- OC, CPR and the handedness ratio --------------------------------------------------------


def test_zero_oc_leaves_cpr_unavailable_without_an_epsilon() -> None:
    dihedral = jones_oracle.covariance_of([(1.0, 0.0, -1.0)])
    result = convert(dihedral)
    assert_value(result.oc, 0.0)
    assert_value(result.sc, 1.0)
    assert_unavailable(result.cpr, INVALID, InvalidReason.OC_NONPOSITIVE)


def test_near_zero_oc_gets_no_epsilon() -> None:
    scatterers: list[jones_oracle.Scatterer] = [(1.0, 0.0, -1.0), (1e-6, 0.0, 1e-6)]
    covariance = jones_oracle.covariance_of(scatterers)
    jones = jones_oracle.scatterer_sum_powers(scatterers, jones_oracle.TRANSMIT_LL)
    result = convert(covariance)
    assert result.cpr.state == VALID
    # OC = 1e-12 is a cancellation of terms near 1, so it carries a relative rounding error of
    # about 1e-16 / 1e-12 in both the code and the oracle; the point here is the large CPR.
    np.testing.assert_allclose(result.cpr.value, jones.sc / jones.oc, rtol=1e-3)
    assert result.cpr.value > 1e11


def test_plane_gives_a_valid_zero_cpr_and_no_handedness_ratio() -> None:
    plane = jones_oracle.covariance_of([(1.0, 0.0, 1.0)])
    result = convert(plane)
    assert_value(result.cpr, 0.0)
    assert result.cpr.value == 0.0  # a valid low CPR, distinct from missing
    assert_value(result.base_sc, 0.0)
    assert_value(result.handedness_term, 0.0)
    assert_unavailable(result.handedness_ratio, INVALID, InvalidReason.BASE_SC_NONPOSITIVE)


def test_high_cpr_is_never_truncated() -> None:
    # Eigenvalues 0.2, 1 and 1.8; base_SC = (2 + 1.6) / 4 + 1 = 1.9 and OC = (2 - 1.6) / 4 = 0.1.
    covariance = matrix(1.0, 1.0, 1.0, -0.8, 0.0, 0.0)
    assert_hermitian_psd(covariance)
    assert_value(convert(covariance).cpr, 19.0)


def test_no_handedness_threshold_is_applied() -> None:
    """PRD 6.2: the 'not small' threshold is set on development data, so none is in code."""
    result = convert(f6_covariance())
    assert abs(float(result.handedness_ratio.value)) > 0.5
    for name in ALL_FIELDS:
        assert np.all(getattr(result, name).state == VALID)
    parameters = inspect.signature(circular_from_covariance).parameters
    assert not any("threshold" in name for name in parameters)


# --- Arrays, shapes, inputs and masks ---------------------------------------------------------


def test_array_of_covariances_gives_per_pixel_results() -> None:
    nan = f6_covariance()
    nan[2, 2] = math.nan
    stack = np.stack(
        [
            np.stack([f6_covariance(), f7_covariance(0.9), f7_covariance(0.1)]),
            np.stack([non_hermitian(), nan, f6_covariance()]),
        ]
    )
    assert stack.shape == (2, 3, 3, 3)
    masked = np.array([[False, False, False], [False, False, True]])
    result = convert(stack, masked=masked)
    for name in ALL_FIELDS:
        assert getattr(result, name).value.shape == (2, 3)
    np.testing.assert_allclose(
        result.cpr.value[0], [0.49 / 0.25, 3.0 / 19.0, 1.0], rtol=RTOL, atol=ATOL
    )
    assert list(result.cpr.reason[1]) == ["COV_NOT_HERMITIAN", "COV_NONFINITE", "PRODUCT_MASKED"]
    assert list(result.cpr.state[1]) == [INVALID, INVALID, MISSING]
    for name in ALL_FIELDS:
        values = getattr(result, name).value
        assert np.all(np.isnan(values[1]))
        assert not np.any(values[1] == 0.0)


def test_single_covariance_gives_zero_dimensional_results() -> None:
    result = convert(f6_covariance())
    for name in ALL_FIELDS:
        assert getattr(result, name).value.shape == ()


def test_empty_stack_gives_empty_results() -> None:
    result = convert(np.zeros((0, 3, 3), dtype=np.complex128), masked=np.zeros(0, dtype=bool))
    for name in ALL_FIELDS:
        assert getattr(result, name).value.shape == (0,)


@pytest.mark.parametrize("shape", [(3,), (2, 2), (3, 4), (4, 3), (2, 3, 2)])
def test_covariance_must_end_in_a_three_by_three_matrix(shape: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="3x3"):
        convert(np.zeros(shape, dtype=np.complex128))


def test_real_and_integer_covariances_are_accepted_as_complex128() -> None:
    real = f7_covariance(0.9).real.copy()
    assert_value(convert(real).cpr, 3.0 / 19.0)
    integer = np.array([[2, 0, 1], [0, 1, 0], [1, 0, 2]])
    expected = expanded_oracle.expanded_from_matrix(integer.astype(np.complex128))
    assert_value(convert(integer).sc, expected.sc_ll)
    # complex64 input is widened to complex128; this matrix is exact in both.
    single = np.array([[2, 0, 1], [0, 1, 0], [1, 0, 2]], dtype=np.complex64)
    assert_value(convert(single).oc, 1.5)


def test_nested_lists_of_complex_scalars_are_accepted() -> None:
    assert_value(convert(f6_covariance().tolist()).sc, 0.49)


@pytest.mark.parametrize(
    "covariance",
    [
        np.eye(3, dtype=bool),
        np.full((3, 3), "1"),
        np.eye(3).astype(object),
    ],
    ids=["bool", "str", "object"],
)
def test_non_numeric_covariances_are_refused(covariance: npt.NDArray[np.generic]) -> None:
    with pytest.raises(TypeError):
        convert(covariance)


def test_product_mask_broadcasts_and_must_be_boolean() -> None:
    stack = np.stack([f6_covariance(), f7_covariance(0.1)])
    result = convert(stack, masked=np.array(True))
    assert np.all(result.sc.state == MISSING)
    with pytest.raises(TypeError, match="product_masked"):
        convert(stack, masked=np.array([0, 1]))
    with pytest.raises(ValueError):
        convert(stack, masked=np.array([True, False, True]))


def test_masked_covariance_or_mask_is_refused() -> None:
    covariance = np.ma.array(f6_covariance(), mask=np.eye(3, dtype=bool))
    with pytest.raises(TypeError, match="masked"):
        convert(covariance)
    with pytest.raises(TypeError, match="masked"):
        convert([covariance], masked=np.array([False]))
    with pytest.raises(TypeError, match="masked"):
        convert(f6_covariance(), masked=np.ma.array(False, mask=True))


def test_tolerance_must_be_a_tolerance() -> None:
    with pytest.raises(TypeError, match="tolerance"):
        convert(f6_covariance(), tolerance=(1e-10, 1e-12))  # type: ignore[arg-type]


def test_results_are_read_only() -> None:
    result = convert(f6_covariance())
    for name in ALL_FIELDS:
        with pytest.raises(ValueError, match="read-only"):
            getattr(result, name).value[...] = 0.0


# --- float64 range: per-pixel states, never whole-array failures ------------------------------


@pytest.mark.parametrize("exponent", [-1000, -500, 500, 1000])
def test_scaled_covariances_scale_their_powers_exactly(exponent: int) -> None:
    scale = 2.0**exponent
    base = convert(f6_covariance())
    scaled = convert(f6_covariance() * scale)
    for name in POWER_FIELDS:
        assert np.all(getattr(scaled, name).state == VALID)
        np.testing.assert_allclose(
            getattr(scaled, name).value, getattr(base, name).value * scale, rtol=RTOL, atol=0.0
        )
    for name in RATIO_FIELDS:
        np.testing.assert_allclose(
            getattr(scaled, name).value, getattr(base, name).value, rtol=RTOL
        )


def test_powers_beyond_the_float64_range_are_out_of_range_per_pixel() -> None:
    huge = 1.9 * 2.0**1023
    covariance = np.stack([matrix(huge, huge, huge, 0.0, 0.0, 0.0), f6_covariance()])
    result = convert(covariance)
    # base_SC = (A + B) / 4 + V = 2.85 * 2**1023 overflows; OC = 0.95 * 2**1023 does not.
    for name in ("sc", "base_sc", "total", "cpr", "handedness_ratio"):
        assert_unavailable(
            pixel(getattr(result, name), 0), INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE
        )
    assert result.oc.state[0] == VALID
    np.testing.assert_allclose(result.oc.value[0], 0.95 * 2.0**1023, rtol=RTOL)
    assert_value(pixel(result.handedness_term, 0), 0.0)
    assert_value(pixel(result.sc, 1), 0.49)


def test_powers_below_the_smallest_normal_float64_are_out_of_range() -> None:
    tiny = jones_oracle.covariance_of([(1.0, 0.0, 0.0)]) * 2.0**-1070
    result = convert(tiny)
    for name in ("sc", "oc", "base_sc", "total", "cpr", "handedness_ratio"):
        assert_unavailable(getattr(result, name), INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)
    # An exact zero stays a valid zero.
    assert_value(result.handedness_term, 0.0)


def test_exact_zero_powers_stay_valid_zeros_at_any_magnitude() -> None:
    for scale in (2.0**-1070, 1.0, 2.0**1000):
        plane = jones_oracle.covariance_of([(1.0, 0.0, 1.0)]) * scale
        result = convert(plane)
        assert result.sc.state == VALID
        assert result.sc.value == 0.0


# --- Properties against both oracles ---------------------------------------------------------

COMPONENT = st.floats(min_value=-2.0, max_value=2.0).map(lambda x: x if abs(x) >= 1e-6 else 0.0)
"""A scattering-vector component part: normal float64, or exactly zero."""

SCATTERER = st.tuples(*(st.builds(complex, COMPONENT, COMPONENT) for _ in range(3)))
SCATTERERS = st.lists(SCATTERER, min_size=1, max_size=3)
"""One to three scatterers: a covariance of rank one to three."""


@settings(max_examples=200, deadline=None)
@given(SCATTERERS)
def test_random_psd_covariances_match_both_oracles(
    scatterers: list[jones_oracle.Scatterer],
) -> None:
    covariance = jones_oracle.covariance_of(scatterers)
    assert_hermitian_psd(covariance)
    expanded = expanded_oracle.expanded_from_matrix(covariance)
    for convention, transmit, sc_expanded in (
        (LL, jones_oracle.TRANSMIT_LL, expanded.sc_ll),
        (OPP, jones_oracle.TRANSMIT_OPPOSITE, expanded.sc_opp),
    ):
        jones = jones_oracle.scatterer_sum_powers(scatterers, transmit)
        for ordering, given_covariance in (
            (RAW, covariance),
            (SQRT2, to_sqrt2_ordering(covariance)),
        ):
            result = convert(given_covariance, convention=convention, ordering=ordering)
            for name in POWER_FIELDS:
                assert getattr(result, name).state == VALID
            for actual, expected in (
                (result.sc, sc_expanded),
                (result.sc, jones.sc),
                (result.oc, expanded.oc),
                (result.oc, jones.oc),
                (result.total, jones.total),
                (result.base_sc, expanded.base_sc),
                (result.handedness_term, expanded.handedness_term),
            ):
                np.testing.assert_allclose(actual.value, expected, rtol=RTOL, atol=ATOL)
            # Ratios are compared where the denominator is well above rounding, with the power
            # tolerance carried through the division (atol on the numerator becomes atol / den).
            if jones.oc > 1e-3 * jones.total:
                if result.cpr.state != VALID:
                    # An SC that is zero exactly may round below zero: unavailable, not clipped.
                    assert result.cpr.reason == InvalidReason.SC_NEGATIVE
                    assert abs(jones.sc) <= ATOL
                else:
                    np.testing.assert_allclose(
                        result.cpr.value, jones.sc / jones.oc, rtol=RTOL, atol=ATOL / jones.oc
                    )
            if expanded.base_sc > 1e-3 * jones.total:
                assert result.handedness_ratio.state == VALID
                np.testing.assert_allclose(
                    result.handedness_ratio.value,
                    expanded.handedness_term / expanded.base_sc,
                    rtol=RTOL,
                    atol=ATOL / expanded.base_sc,
                )


ANY_FINITE = st.floats(allow_nan=False, allow_infinity=False)


@settings(deadline=None)
@given(
    st.tuples(ANY_FINITE, ANY_FINITE, ANY_FINITE),
    st.tuples(*(st.builds(complex, ANY_FINITE, ANY_FINITE) for _ in range(3))),
)
def test_any_finite_hermitian_matrix_gives_a_state_for_every_field(
    diagonal: tuple[float, float, float], off_diagonal: tuple[complex, complex, complex]
) -> None:
    a, v, b = diagonal
    c, d, e = off_diagonal
    result = convert(matrix(a, b, v, c, d, e))
    for name in ALL_FIELDS:
        field = getattr(result, name)
        assert field.state in (VALID, INVALID)
        if field.state == VALID:
            assert np.isfinite(field.value)
            if name in POWER_FIELDS and field.value != 0.0:
                assert abs(float(field.value)) >= SMALLEST_NORMAL
