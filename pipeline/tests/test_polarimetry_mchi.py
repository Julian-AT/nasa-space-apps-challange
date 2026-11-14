"""Hybrid-polarimetric Stokes synthesis, m-chi and the lunar S4-sign check (PRD 6.2, 7, D18).

The synthesis is compared with a separately written Jones-vector oracle in
``pipeline/tests/polarimetry/hybrid_oracle.py``, which builds the received fields ``E = S t``
scatterer by scatterer and does not import the code under test, and with the story 3
covariance-to-circular conversion. Comparisons use the PRD 7 arithmetic tolerances on float64,
rtol = 1e-10 and atol = 1e-12. Only synthetic arrays are used: the real Site01 sign check
belongs to T1.
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

import terraluna_pipeline.polarimetry as polarimetry
from terraluna_pipeline.polarimetry import (
    ARITHMETIC_TOLERANCE,
    CONTEXT_ROLE,
    COVARIANCE_HH_HV_VV,
    COVARIANCE_HH_SQRT2HV_VV,
    MINIRF_SIS_CONVENTION,
    NEISH_SC_LL_CONVENTION,
    OPPOSITE_SC_CONVENTION,
    TRANSMIT_ELLIPTICITIES,
    HybridStokes,
    InvalidReason,
    LunarSignCheck,
    MChi,
    ObsState,
    SignCheckVerdict,
    StatedArray,
    Tolerance,
    circular_from_covariance,
    circular_from_stokes,
    hybrid_stokes_from_covariance,
    lunar_s4_sign_check,
    m_chi,
    m_chi_from_hybrid,
)

if TYPE_CHECKING:  # mypy's name for the oracle package; see pipeline/tests/polarimetry
    from pipeline.tests.polarimetry import hybrid_oracle, jones_oracle
else:  # pytest's name for the same files
    from polarimetry import hybrid_oracle, jones_oracle

RTOL = 1e-10
ATOL = 1e-12

MISSING = ObsState.MISSING
INVALID = ObsState.INVALID
VALID = ObsState.VALID

SMALLEST_NORMAL = float(np.finfo(np.float64).smallest_normal)

RAW = COVARIANCE_HH_HV_VV
SQRT2 = COVARIANCE_HH_SQRT2HV_VV
SIS = MINIRF_SIS_CONVENTION
LL = NEISH_SC_LL_CONVENTION
OPP = OPPOSITE_SC_CONVENTION

CHI_LL = -45.0
"""The declared ellipticity of ``(1, -i)/sqrt2``, the SC_LL transmit vector (PRD 6.2)."""
CHI_OPP = 45.0
"""The declared ellipticity of ``(1, +i)/sqrt2``, the SC_opp transmit vector."""
HANDEDNESS = {CHI_LL: LL, CHI_OPP: OPP}

J_FIELDS = ("j_hh", "j_vv", "j_hv_real", "j_hv_imag")
STOKES_FIELDS = ("s1", "s2", "s3", "s4")
HYBRID_FIELDS = J_FIELDS + STOKES_FIELDS
MCHI_FIELDS = ("m", "sin_2chi", "chi")

SQRT2_NORMALIZATION = np.array([1.0, math.sqrt(2.0), 1.0])


def synthesize(
    covariance: npt.ArrayLike,
    *,
    chi_tx: Any = CHI_LL,
    ordering: Any = RAW,
    masked: npt.ArrayLike = False,
    tolerance: Tolerance = ARITHMETIC_TOLERANCE,
) -> HybridStokes:
    """Synthesize with declared arguments; a test helper, not a production default."""
    return hybrid_stokes_from_covariance(
        covariance,
        ordering=ordering,
        chi_tx=chi_tx,
        product_masked=masked,
        tolerance=tolerance,
    )


def mchi(
    s1: npt.ArrayLike,
    s2: npt.ArrayLike,
    s3: npt.ArrayLike,
    s4: npt.ArrayLike,
    *,
    masked: npt.ArrayLike = False,
    tolerance: Tolerance = ARITHMETIC_TOLERANCE,
) -> MChi:
    """m-chi of product Stokes under the declared SIS convention; a test helper."""
    return m_chi(s1, s2, s3, s4, convention=SIS, product_masked=masked, tolerance=tolerance)


def to_sqrt2_ordering(covariance: npt.NDArray[np.complex128]) -> npt.NDArray[np.complex128]:
    """Express an (HH, HV, VV) covariance in the (HH, sqrt2 HV, VV) ordering, k' = N k."""
    result: npt.NDArray[np.complex128] = covariance * np.outer(
        SQRT2_NORMALIZATION, SQRT2_NORMALIZATION
    )
    return result


def in_ordering(
    covariance: npt.NDArray[np.complex128], ordering: str
) -> npt.NDArray[np.complex128]:
    return covariance if ordering == RAW else to_sqrt2_ordering(covariance)


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


def assert_value(result: StatedArray, expected: npt.ArrayLike) -> None:
    assert np.all(result.state == VALID)
    assert np.all(result.reason == InvalidReason.NONE)
    np.testing.assert_allclose(result.value, expected, rtol=RTOL, atol=ATOL)


def assert_unavailable(result: StatedArray, state: ObsState, reason: InvalidReason) -> None:
    """Only a state and a reason: no finite number, no huge number, no 0.0."""
    assert np.all(result.state == state)
    assert np.all(result.reason == reason)
    assert np.all(np.isnan(result.value))


def assert_nothing_published(
    record: object, fields: tuple[str, ...], state: ObsState, reason: InvalidReason
) -> None:
    for name in fields:
        assert_unavailable(getattr(record, name), state, reason)


def pixel(result: StatedArray, index: int | tuple[int, ...]) -> StatedArray:
    """One element of a stated array, with its state and reason."""
    return StatedArray(
        value=result.value[index], state=result.state[index], reason=result.reason[index]
    )


def assert_matches_oracle(record: HybridStokes, expected: hybrid_oracle.HybridOracle) -> None:
    scale = max(expected.s1, 1.0)
    for name, value in (
        ("j_hh", expected.j_hh),
        ("j_vv", expected.j_vv),
        ("j_hv_real", expected.j_hv.real),
        ("j_hv_imag", expected.j_hv.imag),
        ("s1", expected.s1),
        ("s2", expected.s2),
        ("s3", expected.s3),
        ("s4", expected.s4),
    ):
        result = getattr(record, name)
        assert np.all(result.state == VALID), name
        np.testing.assert_allclose(result.value, value, rtol=RTOL, atol=ATOL * scale, err_msg=name)


# --- Fixtures -----------------------------------------------------------------------------------

F6_SCATTERER: jones_oracle.Scatterer = (1.0, 0.2j, 0.0)
"""PRD 7: F6 corresponds to HH = 1, HV = 0.2i, VV = 0 (rank one)."""


def f6_covariance() -> npt.NDArray[np.complex128]:
    """PRD 7, F6, asserted Hermitian PSD of rank one before every use."""
    covariance = jones_oracle.covariance_of([F6_SCATTERER])
    expected = matrix(1.0, 0.0, 0.04, 0.0, -0.2j, 0.0)
    np.testing.assert_allclose(covariance, expected, rtol=RTOL, atol=ATOL)
    eigenvalues = assert_hermitian_psd(covariance)
    np.testing.assert_allclose(eigenvalues, [0.0, 0.0, 1.04], rtol=RTOL, atol=ATOL)
    return covariance


def random_volume() -> npt.NDArray[np.complex128]:
    """A reflection-symmetric volume with A = B = 1, C = V = 1/2 and D = E = 0, asserted PSD.

    Its received 2x2 covariance under either circular transmit is ``(3/4) I``: the HV power is
    received in both channels, and C = V makes it cancel against the co-polarized correlation,
    so the hybrid return is unpolarized. Every value is a power of two, so the arithmetic is
    exact."""
    covariance = matrix(1.0, 1.0, 0.5, 0.5, 0.0, 0.0)
    eigenvalues = assert_hermitian_psd(covariance)
    np.testing.assert_allclose(eigenvalues, [0.5, 0.5, 1.5], rtol=RTOL, atol=ATOL)
    return covariance


def unpolarized_fields() -> list[hybrid_oracle.Field]:
    """Two orthogonal, uncorrelated received fields of equal power: J is the 2x2 identity."""
    return [(1.0 + 0.0j, 0.0j), (0.0j, 1.0 + 0.0j)]


# --- Synthesis: F6 against story 3 and the oracle -----------------------------------------------


@pytest.mark.parametrize("ordering", [RAW, SQRT2])
def test_F6_hybrid_stokes_reproduce_story_3_sc_ll_and_oc(ordering: str) -> None:
    covariance = in_ordering(f6_covariance(), ordering)
    hybrid = synthesize(covariance, chi_tx=CHI_LL, ordering=ordering)
    story3 = circular_from_covariance(
        covariance, ordering=ordering, convention=LL, product_masked=False
    )
    # PRD 7, F6: SC_LL = 0.49 and OC = 0.25, so S1 = SC + OC and S4 = OC - SC (SIS).
    assert_value(story3.sc, 0.49)
    assert_value(story3.oc, 0.25)
    assert_value(hybrid.s1, 0.74)
    assert_value(hybrid.s4, -0.24)
    assert_value(hybrid.s1, story3.sc.value + story3.oc.value)
    assert_value(hybrid.s4, story3.oc.value - story3.sc.value)
    again = circular_from_stokes(
        hybrid.s1.value, hybrid.s4.value, s2=None, s3=None, convention=SIS, product_masked=False
    )
    assert_value(again.sc, 0.49)
    assert_value(again.oc, 0.25)


@pytest.mark.parametrize("ordering", [RAW, SQRT2])
def test_F6_opposite_ellipticity_gives_sc_opp(ordering: str) -> None:
    covariance = in_ordering(f6_covariance(), ordering)
    hybrid = synthesize(covariance, chi_tx=CHI_OPP, ordering=ordering)
    story3 = circular_from_covariance(
        covariance, ordering=ordering, convention=OPP, product_masked=False
    )
    # PRD 7, F6: SC_opp = 0.09 and OC = 0.25.
    assert_value(story3.sc, 0.09)
    assert_value(hybrid.s1, 0.34)
    assert_value(hybrid.s4, 0.16)
    again = circular_from_stokes(
        hybrid.s1.value, hybrid.s4.value, s2=None, s3=None, convention=SIS, product_masked=False
    )
    assert_value(again.sc, 0.09)
    assert_value(again.oc, 0.25)


@pytest.mark.parametrize("chi_tx", [CHI_LL, CHI_OPP])
@pytest.mark.parametrize("ordering", [RAW, SQRT2])
def test_F6_matches_the_jones_oracle(chi_tx: float, ordering: str) -> None:
    hybrid = synthesize(in_ordering(f6_covariance(), ordering), chi_tx=chi_tx, ordering=ordering)
    expected = hybrid_oracle.hybrid_of_scatterers([F6_SCATTERER], chi_tx)
    assert_matches_oracle(hybrid, expected)
    # HH = 1, HV = 0.2i, VV = 0 and t = (1, -i)/sqrt2 give E = (1.2, 0.2i)/sqrt2.
    if chi_tx == CHI_LL:
        assert_value(hybrid.j_hh, 0.72)
        assert_value(hybrid.j_vv, 0.02)
        assert_value(hybrid.j_hv_real, 0.0)
        assert_value(hybrid.j_hv_imag, -0.12)


def test_oracle_declares_the_story_3_transmit_vectors() -> None:
    np.testing.assert_array_equal(hybrid_oracle.transmit_jones(CHI_LL), jones_oracle.TRANSMIT_LL)
    np.testing.assert_array_equal(
        hybrid_oracle.transmit_jones(CHI_OPP), jones_oracle.TRANSMIT_OPPOSITE
    )


COMPONENT = st.floats(min_value=-2.0, max_value=2.0).map(lambda x: x if abs(x) >= 1e-6 else 0.0)
"""A scattering-vector component: zero or of magnitude at least 1e-6, so every fixture is
normalized enough for the absolute part of the PRD 7 tolerance."""

SCATTERER = st.tuples(*(st.builds(complex, COMPONENT, COMPONENT) for _ in range(3)))
SCATTERERS = st.lists(SCATTERER, min_size=1, max_size=3)
"""One to three scatterers: covariances of rank 1 to 3."""


@settings(deadline=None, max_examples=200)
@given(SCATTERERS)
def test_random_psd_covariances_match_the_oracle_and_story_3(
    scatterers: list[jones_oracle.Scatterer],
) -> None:
    covariance = jones_oracle.covariance_of(scatterers)
    for ordering in (RAW, SQRT2):
        given_covariance = in_ordering(covariance, ordering)
        for chi_tx in (CHI_LL, CHI_OPP):
            hybrid = synthesize(given_covariance, chi_tx=chi_tx, ordering=ordering)
            assert_matches_oracle(hybrid, hybrid_oracle.hybrid_of_scatterers(scatterers, chi_tx))
            story3 = circular_from_covariance(
                given_covariance,
                ordering=ordering,
                convention=HANDEDNESS[chi_tx],
                product_masked=False,
            )
            scale = max(float(story3.total.value), 1.0)
            np.testing.assert_allclose(
                hybrid.s1.value, story3.sc.value + story3.oc.value, rtol=RTOL, atol=ATOL * scale
            )
            np.testing.assert_allclose(
                hybrid.s4.value, story3.oc.value - story3.sc.value, rtol=RTOL, atol=ATOL * scale
            )


def test_hybrid_covariance_matrix_is_the_synthesized_2x2_covariance() -> None:
    hybrid = synthesize(f6_covariance(), chi_tx=CHI_LL)
    j = hybrid.covariance_matrix()
    assert j.shape == (2, 2)
    assert j.dtype == np.complex128
    np.testing.assert_allclose(j, [[0.72, -0.12j], [0.12j, 0.02]], rtol=RTOL, atol=ATOL)
    np.testing.assert_array_equal(j, j.conj().T)
    # The Stokes parameters are the same covariance in another form.
    np.testing.assert_allclose(hybrid.s1.value, j[0, 0].real + j[1, 1].real, rtol=RTOL)
    np.testing.assert_allclose(hybrid.s2.value, j[0, 0].real - j[1, 1].real, rtol=RTOL)
    np.testing.assert_allclose(hybrid.s3.value, 2.0 * j[0, 1].real, atol=ATOL)


def test_hybrid_covariance_matrix_is_nan_where_the_pixel_is_not_valid() -> None:
    stack = np.stack([f6_covariance(), f6_covariance()])
    hybrid = synthesize(stack, masked=np.array([False, True]))
    j = hybrid.covariance_matrix()
    assert j.shape == (2, 2, 2)
    assert np.all(np.isfinite(j[0]))
    assert np.all(np.isnan(j[1].real)) and np.all(np.isnan(j[1].imag))


# --- Declared transmit ellipticity and the record -----------------------------------------------


def test_transmit_ellipticities_are_the_two_declared_values() -> None:
    assert sorted(TRANSMIT_ELLIPTICITIES) == [-45.0, 45.0]


@pytest.mark.parametrize("chi_tx", [CHI_LL, CHI_OPP])
def test_record_states_the_handedness_used(chi_tx: float) -> None:
    hybrid = synthesize(f6_covariance(), chi_tx=chi_tx)
    assert hybrid.chi_tx == chi_tx
    assert hybrid.handedness == HANDEDNESS[chi_tx]
    expected_jones = hybrid_oracle.transmit_jones(chi_tx)
    assert hybrid.transmit_jones == (complex(expected_jones[0]), complex(expected_jones[1]))
    assert hybrid.stokes_convention == SIS
    assert hybrid.ordering == RAW
    assert hybrid.tolerance == ARITHMETIC_TOLERANCE


def test_chi_tx_and_the_declarations_are_required_keyword_arguments() -> None:
    parameters = inspect.signature(hybrid_stokes_from_covariance).parameters
    for name in ("ordering", "chi_tx", "product_masked"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty
    assert parameters["tolerance"].default is ARITHMETIC_TOLERANCE


def test_missing_chi_tx_raises() -> None:
    with pytest.raises(TypeError):
        hybrid_stokes_from_covariance(  # type: ignore[call-arg]
            f6_covariance(), ordering=RAW, product_masked=False
        )


UNDECLARED_CHI = [
    None,
    0,
    0.0,
    90.0,
    -90.0,
    44.999999,
    45.000001,
    math.pi / 4,
    -math.pi / 4,
    "45",
    "-45",
    "+45",
    True,
    math.nan,
    math.inf,
    np.array(45.0),
    [45.0],
    complex(45.0, 0.0),
]


@pytest.mark.parametrize("chi_tx", UNDECLARED_CHI)
def test_undeclared_chi_tx_raises_and_returns_nothing(chi_tx: object) -> None:
    with pytest.raises(ValueError, match="chi_tx"):
        synthesize(f6_covariance(), chi_tx=chi_tx)


@pytest.mark.parametrize(
    ("chi_tx", "expected"),
    [(45, 45.0), (-45, -45.0), (45.0, 45.0), (np.float64(-45.0), -45.0), (np.int64(45), 45.0)],
)
def test_declared_chi_tx_accepts_real_numbers_equal_to_45_degrees(
    chi_tx: object, expected: float
) -> None:
    hybrid = synthesize(f6_covariance(), chi_tx=chi_tx)
    assert type(hybrid.chi_tx) is float
    assert hybrid.chi_tx == expected


@pytest.mark.parametrize("ordering", [None, "HH_VV_HV", "hh_hv_vv"])
def test_undeclared_ordering_raises(ordering: object) -> None:
    with pytest.raises(ValueError, match="ordering"):
        synthesize(f6_covariance(), ordering=ordering)


def test_hybrid_record_is_context_and_unverified_by_default() -> None:
    hybrid = synthesize(f6_covariance())
    assert CONTEXT_ROLE == "context"
    assert hybrid.role == "context"
    assert hybrid.handedness_verified is False


def test_hybrid_record_refuses_another_role_or_inconsistent_declarations() -> None:
    hybrid = synthesize(f6_covariance(), chi_tx=CHI_LL)
    with pytest.raises(ValueError, match="role"):
        dataclasses.replace(hybrid, role="distance")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="handedness"):
        dataclasses.replace(hybrid, handedness=OPP)
    with pytest.raises(ValueError, match="transmit"):
        dataclasses.replace(hybrid, transmit_jones=(complex(1.0), complex(0.0)))
    with pytest.raises(ValueError, match="chi_tx"):
        dataclasses.replace(hybrid, chi_tx=0.0)
    with pytest.raises(ValueError, match="convention"):
        dataclasses.replace(hybrid, stokes_convention="OTHER")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="handedness_verified"):
        dataclasses.replace(hybrid, handedness_verified=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="StatedArray"):
        dataclasses.replace(hybrid, s1=hybrid.s1.value)  # type: ignore[arg-type]


def test_only_a_caller_marks_handedness_verified() -> None:
    hybrid = synthesize(f6_covariance())
    verified = dataclasses.replace(hybrid, handedness_verified=True)
    assert verified.handedness_verified is True
    assert hybrid.handedness_verified is False


# --- Synthesis: covariance validity, inputs and range -------------------------------------------


def non_hermitian() -> npt.NDArray[np.complex128]:
    covariance = matrix(1.0, 1.0, 0.1, 0.0, 0.0, 0.0)
    covariance[0, 1] = 0.5
    return covariance


def negative_eigenvalue() -> npt.NDArray[np.complex128]:
    covariance = matrix(1.0, 1.0, 0.1, 2.0, 0.0, 0.0)
    assert np.linalg.eigvalsh(covariance).min() < -0.5
    return covariance


def negative_diagonal() -> npt.NDArray[np.complex128]:
    return matrix(1.0, -0.5, 0.1, 0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    ("covariance", "reason"),
    [
        (non_hermitian(), InvalidReason.COV_NOT_HERMITIAN),
        (negative_diagonal(), InvalidReason.COV_NEGATIVE_DIAGONAL),
        (negative_eigenvalue(), InvalidReason.COV_NOT_PSD),
        (matrix(math.nan, 1.0, 0.1, 0.0, 0.0, 0.0), InvalidReason.COV_NONFINITE),
    ],
)
@pytest.mark.parametrize("ordering", [RAW, SQRT2])
def test_nonphysical_covariance_publishes_nothing_with_story_3_cause(
    covariance: npt.NDArray[np.complex128], reason: InvalidReason, ordering: str
) -> None:
    given_covariance = in_ordering(covariance, ordering)
    hybrid = synthesize(given_covariance, ordering=ordering)
    assert_nothing_published(hybrid, HYBRID_FIELDS, INVALID, reason)
    story3 = circular_from_covariance(
        given_covariance, ordering=ordering, convention=LL, product_masked=False
    )
    assert story3.sc.reason == reason


def test_product_mask_gives_missing_in_every_output() -> None:
    hybrid = synthesize(np.stack([f6_covariance()] * 2), masked=np.array([True, False]))
    for name in HYBRID_FIELDS:
        result = getattr(hybrid, name)
        assert_unavailable(pixel(result, 0), MISSING, InvalidReason.PRODUCT_MASKED)
        assert result.state[1] == VALID


@settings(deadline=None)
@given(
    st.tuples(*(st.floats(allow_nan=False, allow_infinity=False) for _ in range(3))),
    st.tuples(
        *(
            st.builds(
                complex,
                st.floats(allow_nan=False, allow_infinity=False),
                st.floats(allow_nan=False, allow_infinity=False),
            )
            for _ in range(3)
        )
    ),
)
def test_validity_states_follow_story_3_for_any_hermitian_matrix(
    diagonal: tuple[float, float, float], upper: tuple[complex, complex, complex]
) -> None:
    covariance = matrix(diagonal[0], diagonal[2], diagonal[1], upper[1], upper[0], upper[2])
    hybrid = synthesize(covariance)
    story3 = circular_from_covariance(covariance, ordering=RAW, convention=LL, product_masked=False)
    shared = {
        InvalidReason.PRODUCT_MASKED,
        InvalidReason.COV_NONFINITE,
        InvalidReason.COV_NOT_HERMITIAN,
        InvalidReason.COV_NEGATIVE_DIAGONAL,
        InvalidReason.COV_NOT_PSD,
    }
    for name in HYBRID_FIELDS:
        result = getattr(hybrid, name)
        assert result.state in (MISSING, INVALID, VALID)
        if story3.oc.reason in shared:
            assert result.reason == story3.oc.reason
        else:
            assert result.reason in (InvalidReason.NONE, InvalidReason.FLOAT64_OUT_OF_RANGE)


def test_input_is_not_modified() -> None:
    covariance = f6_covariance()
    copy = covariance.copy()
    synthesize(covariance)
    np.testing.assert_array_equal(covariance, copy)


def test_results_are_read_only() -> None:
    hybrid = synthesize(f6_covariance())
    for name in HYBRID_FIELDS:
        with pytest.raises(ValueError, match="read-only"):
            getattr(hybrid, name).value[...] = 0.0


def test_masked_covariance_or_mask_is_refused() -> None:
    covariance = f6_covariance()
    with pytest.raises(TypeError, match="masked"):
        synthesize(np.ma.array(covariance, mask=False))
    with pytest.raises(TypeError, match="masked"):
        synthesize(covariance, masked=np.ma.array(False, mask=True))


def test_product_mask_must_be_boolean() -> None:
    with pytest.raises(TypeError, match="product_masked"):
        synthesize(f6_covariance(), masked=np.array(0))


def test_tolerance_must_be_a_tolerance() -> None:
    with pytest.raises(TypeError, match="tolerance"):
        synthesize(f6_covariance(), tolerance=(1e-10, 1e-12))  # type: ignore[arg-type]


@pytest.mark.parametrize("exponent", [-1000, -500, 500, 1000])
@pytest.mark.parametrize("ordering", [RAW, SQRT2])
def test_scaled_covariances_scale_their_stokes_exactly(exponent: int, ordering: str) -> None:
    base = in_ordering(f6_covariance(), ordering)
    unscaled = synthesize(base, ordering=ordering)
    scaled = synthesize(base * 2.0**exponent, ordering=ordering)
    for name in HYBRID_FIELDS:
        result = getattr(scaled, name)
        assert result.state == VALID, name
        assert result.value == np.ldexp(getattr(unscaled, name).value, exponent), name


def test_stokes_beyond_the_float64_range_are_out_of_range_per_pixel() -> None:
    huge = float(np.finfo(np.float64).max)
    stack = np.stack([matrix(huge, huge, huge, 0.0, 0.0, 0.0), f6_covariance()])
    hybrid = synthesize(stack)
    # A diagonal covariance gives S1 = (A + B) / 2 + V, here twice the largest float64, while
    # J_HH = (A + V) / 2, J_VV = (V + B) / 2 and S4 = -V stay in range.
    assert_unavailable(pixel(hybrid.s1, 0), INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)
    for name in ("j_hh", "j_vv", "s2", "s3", "s4"):
        assert getattr(hybrid, name).state[0] == VALID, name
    for name in HYBRID_FIELDS:
        assert getattr(hybrid, name).state[1] == VALID


def test_stokes_parameter_is_not_lost_beside_large_powers() -> None:
    """S2 = (A - B) / 2 for a diagonal covariance, however large A and B are: the Stokes
    parameter is formed as one linear functional, not as a difference of rounded powers."""
    large = 2.0**50
    covariance = matrix(large + 2.0, large, 1.0 / 3.0, 0.0, 0.0, 0.0)
    for chi_tx in (CHI_LL, CHI_OPP):
        hybrid = synthesize(covariance, chi_tx=chi_tx)
        assert hybrid.s2.state == VALID
        assert hybrid.s2.value == 1.0
        assert hybrid.s3.value == 0.0
        # J_HV = -i V / 2 for t = (1, -i)/sqrt2 and +i V / 2 for (1, +i)/sqrt2; S4 = -V.
        np.testing.assert_allclose(hybrid.s4.value, -1.0 / 3.0, rtol=RTOL)


@pytest.mark.parametrize("ordering", [RAW, SQRT2])
def test_identity_covariance_is_partially_polarized_under_circular_transmit(
    ordering: str,
) -> None:
    """A 3x3 identity is not an unpolarized hybrid return: HV is received in both channels."""
    identity = np.eye(3, dtype=np.complex128)
    raw = (
        identity
        if ordering == RAW
        else identity / np.outer(SQRT2_NORMALIZATION, SQRT2_NORMALIZATION)
    )
    scatterers = hybrid_oracle.scatterers_of(raw)
    for chi_tx in (CHI_LL, CHI_OPP):
        hybrid = synthesize(identity, chi_tx=chi_tx, ordering=ordering)
        assert_matches_oracle(hybrid, hybrid_oracle.hybrid_of_scatterers(scatterers, chi_tx))
        result = m_chi_from_hybrid(hybrid)
        assert_value(result.m, 0.5 if ordering == RAW else 1.0 / 3.0)


# --- m and chi ----------------------------------------------------------------------------------


RANK_ONE = [
    F6_SCATTERER,
    (1.0, 0.0, 1.0),  # plane or sphere: odd bounce
    (1.0, 0.0, -1.0),  # dihedral: even bounce
    (0.0, 1.0, 0.0),  # pure HV
    (0.3 - 0.2j, 0.1j, -0.7 + 0.4j),
]


@pytest.mark.parametrize("scatterer", RANK_ONE)
@pytest.mark.parametrize("chi_tx", [CHI_LL, CHI_OPP])
@pytest.mark.parametrize("ordering", [RAW, SQRT2])
def test_fully_polarized_single_scatterer_has_unit_m(
    scatterer: jones_oracle.Scatterer, chi_tx: float, ordering: str
) -> None:
    covariance = in_ordering(jones_oracle.covariance_of([scatterer]), ordering)
    result = m_chi_from_hybrid(synthesize(covariance, chi_tx=chi_tx, ordering=ordering))
    assert_value(result.m, 1.0)
    expected = hybrid_oracle.hybrid_of_scatterers([scatterer], chi_tx)
    oracle = hybrid_oracle.m_chi(expected.s1, expected.s2, expected.s3, expected.s4)
    assert oracle.sin_2chi is not None
    assert_value(result.sin_2chi, oracle.sin_2chi)
    assert_value(result.chi, math.degrees(math.asin(oracle.sin_2chi)) / 2.0)


@settings(deadline=None, max_examples=200)
@given(SCATTERER, st.sampled_from([CHI_LL, CHI_OPP]))
def test_any_single_scatterer_has_unit_m(scatterer: jones_oracle.Scatterer, chi_tx: float) -> None:
    covariance = jones_oracle.covariance_of([scatterer])
    hybrid = synthesize(covariance, chi_tx=chi_tx)
    if hybrid.s1.value == 0.0:
        return  # no received power: S1 <= 0 is covered below
    result = m_chi_from_hybrid(hybrid)
    # m = P / S1 carries rounding of the order of float64 precision times the scatterer power
    # |k|**2, so near a null of the received power (S1 << |k|**2) it is less well determined.
    power = sum(abs(complex(x)) ** 2 for x in scatterer)
    scale = max(power, 1.0) / float(hybrid.s1.value)
    np.testing.assert_allclose(result.m.value, 1.0, rtol=RTOL, atol=ATOL * scale)


def test_unpolarized_hybrid_covariance_has_zero_m_and_no_chi() -> None:
    # The 2x2 identity covariance: S = (2, 0, 0, 0) by the oracle.
    expected = hybrid_oracle.hybrid_of_fields(
        unpolarized_fields(), hybrid_oracle.transmit_jones(CHI_LL)
    )
    assert (expected.j_hh, expected.j_vv, expected.j_hv) == (1.0, 1.0, 0j)
    result = mchi(expected.s1, expected.s2, expected.s3, expected.s4)
    assert_value(result.m, 0.0)
    assert result.m.value == 0.0
    assert_unavailable(result.sin_2chi, INVALID, InvalidReason.M_ZERO)
    assert_unavailable(result.chi, INVALID, InvalidReason.M_ZERO)


@pytest.mark.parametrize("chi_tx", [CHI_LL, CHI_OPP])
@pytest.mark.parametrize("ordering", [RAW, SQRT2])
def test_random_volume_gives_an_unpolarized_hybrid_return(chi_tx: float, ordering: str) -> None:
    hybrid = synthesize(in_ordering(random_volume(), ordering), chi_tx=chi_tx, ordering=ordering)
    np.testing.assert_array_equal(hybrid.covariance_matrix(), 0.75 * np.eye(2))
    result = m_chi_from_hybrid(hybrid)
    assert result.m.state == VALID
    assert result.m.value == 0.0
    assert_unavailable(result.sin_2chi, INVALID, InvalidReason.M_ZERO)
    assert_unavailable(result.chi, INVALID, InvalidReason.M_ZERO)


@pytest.mark.parametrize(
    ("stokes", "m", "sin_2chi"),
    [
        ((1.0, 0.0, 0.0, 0.5), 0.5, -1.0),
        ((1.0, 0.0, 0.0, -0.5), 0.5, 1.0),
        ((2.0, 0.0, 1.0, -1.0), math.sqrt(0.5), math.sqrt(0.5)),
        ((4.0, 2.0, 0.0, 0.0), 0.5, 0.0),
        ((5.0, 0.0, 3.0, 4.0), 1.0, -0.8),
    ],
)
def test_m_and_sin_2chi_follow_the_prd_formulas(
    stokes: tuple[float, float, float, float], m: float, sin_2chi: float
) -> None:
    result = mchi(*stokes)
    assert_value(result.m, m)
    assert_value(result.sin_2chi, sin_2chi)
    assert_value(result.chi, math.degrees(math.asin(sin_2chi)) / 2.0)
    oracle = hybrid_oracle.m_chi(*stokes)
    np.testing.assert_allclose(result.m.value, oracle.m, rtol=RTOL)


def test_chi_is_in_degrees_within_plus_minus_45() -> None:
    result = mchi([1.0, 1.0, 1.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [1.0, -1.0, 0.0])
    np.testing.assert_allclose(result.chi.value[:2], [-45.0, 45.0], rtol=RTOL)
    assert_unavailable(pixel(result.chi, 2), INVALID, InvalidReason.M_ZERO)
    assert polarimetry.INTERNAL_ANGLE_UNIT == "degrees"


@pytest.mark.parametrize("chi_tx", [CHI_LL, CHI_OPP])
def test_odd_and_even_bounce_give_opposite_chi_whatever_the_transmit(chi_tx: float) -> None:
    """With S4 = OC - SC relative to the transmit, an OC-dominant (odd-bounce) return has
    sin(2 chi) = -1 and an SC-dominant (even-bounce) one +1, for either declared ellipticity."""
    plane = synthesize(jones_oracle.covariance_of([(1.0, 0.0, 1.0)]), chi_tx=chi_tx)
    dihedral = synthesize(jones_oracle.covariance_of([(1.0, 0.0, -1.0)]), chi_tx=chi_tx)
    assert_value(plane.s4, plane.s1.value)
    assert_value(dihedral.s4, -dihedral.s1.value)
    assert_value(m_chi_from_hybrid(plane).sin_2chi, -1.0)
    assert_value(m_chi_from_hybrid(dihedral).sin_2chi, 1.0)
    assert_value(m_chi_from_hybrid(plane).chi, -45.0)


@pytest.mark.parametrize("s1", [0.0, -0.0, -1.0, -1e-300])
def test_nonpositive_s1_is_invalid(s1: float) -> None:
    result = mchi(s1, 0.0, 0.0, 0.0)
    assert_nothing_published(result, MCHI_FIELDS, INVALID, InvalidReason.STOKES_S1_NONPOSITIVE)


@pytest.mark.parametrize(
    "stokes",
    [
        (1.0, 1.5, 0.0, 0.0),
        (1.0, 0.0, 1.0, 1.0),
        (1.0, 0.0, 0.0, 2.0),
        (1.0, 0.0, 0.0, -2.0),
        (2.0, 1.2, 1.2, 1.2),
    ],
)
def test_m_greater_than_one_beyond_the_tolerance_is_nonphysical(
    stokes: tuple[float, float, float, float],
) -> None:
    assert hybrid_oracle.m_chi(*stokes).m > 1.0
    result = mchi(*stokes)
    assert_nothing_published(result, MCHI_FIELDS, INVALID, InvalidReason.STOKES_NONPHYSICAL)


def test_m_above_one_within_the_tolerance_is_reported_as_computed() -> None:
    # S2**2 - S1**2 is about 2e-11, within atol + rtol * S1**2 = 1.01e-10: not clipped to 1.
    result = mchi(1.0, 1.0 + 1e-11, 0.0, 0.0)
    assert result.m.state == VALID
    assert result.m.value > 1.0
    np.testing.assert_allclose(result.m.value, 1.0 + 1e-11, rtol=1e-15)
    assert result.sin_2chi.value == 0.0


def test_the_tolerance_argument_decides_the_nonphysical_outcome() -> None:
    stokes = (1.0, 1.001, 0.0, 0.0)
    assert mchi(*stokes).m.reason == InvalidReason.STOKES_NONPHYSICAL
    assert mchi(*stokes, tolerance=Tolerance(rtol=0.01, atol=0.0)).m.state == VALID


def test_s4_beyond_s1_within_the_squared_bound_is_still_refused() -> None:
    """PRD 7: S1 >= |S4| within tolerance holds as well. Near S1 = 0 the squared bound admits
    more than the linear one, so the linear rule still decides."""
    s1, s4 = 1e-3, 1e-3 + 1e-10
    # Linear: |S4| - S1 = 1e-10 > atol + rtol * S1. Squared: about 2e-13 <= atol + rtol * S1**2.
    assert abs(s4) - s1 > ATOL + RTOL * s1
    assert s4**2 - s1**2 <= ATOL + RTOL * s1**2
    result = mchi(s1, 0.0, 0.0, s4)
    assert_nothing_published(result, MCHI_FIELDS, INVALID, InvalidReason.STOKES_S1_LT_ABS_S4)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("position", [0, 1, 2, 3])
def test_nonfinite_stokes_are_invalid(value: float, position: int) -> None:
    stokes = [2.0, 0.5, 0.5, 0.5]
    stokes[position] = value
    result = mchi(*stokes)
    assert_nothing_published(result, MCHI_FIELDS, INVALID, InvalidReason.NONFINITE)


def test_product_mask_gives_missing_and_wins() -> None:
    result = mchi([2.0, math.nan], [0.5, 0.0], [0.5, 0.0], [0.5, 0.0], masked=True)
    assert_nothing_published(result, MCHI_FIELDS, MISSING, InvalidReason.PRODUCT_MASKED)


def test_m_chi_declarations_are_required() -> None:
    parameters = inspect.signature(m_chi).parameters
    for name in ("convention", "product_masked"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters[name].default is inspect.Parameter.empty
    assert parameters["tolerance"].default is ARITHMETIC_TOLERANCE


@pytest.mark.parametrize("convention", [None, "MINIRF_SIS", "S4_EQ_SC_MINUS_OC", LL])
def test_m_chi_refuses_an_undeclared_stokes_convention(convention: object) -> None:
    with pytest.raises(ValueError, match="convention"):
        m_chi(1.0, 0.0, 0.0, 0.5, convention=convention, product_masked=False)  # type: ignore[arg-type]


def test_m_chi_refuses_mismatched_shapes_and_masked_input() -> None:
    with pytest.raises(ValueError, match="shape"):
        mchi([1.0, 1.0], [0.0], [0.0, 0.0], [0.0, 0.0])
    with pytest.raises(TypeError, match="masked"):
        mchi(np.ma.array([1.0], mask=[True]), [0.0], [0.0], [0.0])
    with pytest.raises(TypeError):
        mchi(np.array([1 + 0j]), [0.0], [0.0], [0.0])


def test_m_chi_record_is_context_and_carries_its_declarations() -> None:
    result = mchi(1.0, 0.0, 0.0, 0.5)
    assert result.role == "context"
    assert result.convention == SIS
    assert result.tolerance == ARITHMETIC_TOLERANCE
    assert result.chi_tx is None
    assert result.handedness is None
    hybrid = synthesize(f6_covariance(), chi_tx=CHI_OPP)
    from_hybrid = m_chi_from_hybrid(hybrid)
    assert from_hybrid.role == "context"
    assert from_hybrid.chi_tx == CHI_OPP
    assert from_hybrid.handedness == OPP


def test_m_chi_record_refuses_inconsistent_values() -> None:
    result = mchi([1.0, 1.0], [0.0, 0.0], [0.0, 0.0], [0.5, 0.0])
    with pytest.raises(ValueError, match="role"):
        dataclasses.replace(result, role="distance")  # type: ignore[arg-type]
    # Pixel 1 has m = 0, so its sin(2 chi) is M_ZERO; a VALID one there is refused.
    assert result.m.value[1] == 0.0
    with pytest.raises(ValueError, match="sin_2chi"):
        dataclasses.replace(result, sin_2chi=with_valid(result.sin_2chi, 1, 0.3))
    with pytest.raises(ValueError, match="chi"):
        dataclasses.replace(result, chi=result.m)
    with pytest.raises(ValueError, match="handedness"):
        dataclasses.replace(result, chi_tx=CHI_LL, handedness=OPP)
    with pytest.raises(TypeError, match="StatedArray"):
        dataclasses.replace(result, m=result.m.value)  # type: ignore[arg-type]


def with_valid(result: StatedArray, index: int, value: float) -> StatedArray:
    """A copy of ``result`` whose element ``index`` is VALID with ``value``."""
    values, state, reason = (np.array(a) for a in (result.value, result.state, result.reason))
    values[index], state[index], reason[index] = value, VALID, InvalidReason.NONE
    return StatedArray(value=values, state=state, reason=reason)


def test_decomposition_is_deferred_and_only_m_and_chi_are_returned() -> None:
    doc = inspect.getdoc(m_chi) or ""
    assert "not implemented" in doc
    assert "Raney" in doc
    names = {field.name for field in dataclasses.fields(MChi)}
    assert not names & {"single_bounce", "double_bounce", "random", "volume", "even", "odd"}
    assert {"m", "sin_2chi", "chi"} <= names


def test_no_cross_band_comparison_function_is_public() -> None:
    for name in polarimetry.__all__:
        lowered = name.lower()
        assert "cross_band" not in lowered
        assert "distance" not in lowered
        assert "compare" not in lowered


def test_m_chi_from_hybrid_keeps_the_hybrid_states_and_reasons() -> None:
    stack = np.stack([f6_covariance(), negative_eigenvalue(), f6_covariance()])
    hybrid = synthesize(stack, masked=np.array([False, False, True]))
    result = m_chi_from_hybrid(hybrid)
    assert result.m.state[0] == VALID
    for name in MCHI_FIELDS:
        assert_unavailable(pixel(getattr(result, name), 1), INVALID, InvalidReason.COV_NOT_PSD)
        assert_unavailable(pixel(getattr(result, name), 2), MISSING, InvalidReason.PRODUCT_MASKED)


def test_m_chi_from_hybrid_takes_only_a_hybrid_record() -> None:
    with pytest.raises(TypeError, match="HybridStokes"):
        m_chi_from_hybrid(mchi(1.0, 0.0, 0.0, 0.5))  # type: ignore[arg-type]


def test_zero_covariance_has_no_m() -> None:
    hybrid = synthesize(np.zeros((3, 3)))
    assert_value(hybrid.s1, 0.0)
    result = m_chi_from_hybrid(hybrid)
    assert_nothing_published(result, MCHI_FIELDS, INVALID, InvalidReason.STOKES_S1_NONPOSITIVE)


@pytest.mark.parametrize("exponent", [-1070, -1000, -500, 500, 1000, 1020])
def test_m_and_chi_do_not_depend_on_the_power_scale(exponent: int) -> None:
    stokes = np.array([5.0, 0.0, 3.0, 4.0]) * 0.25
    scaled = np.ldexp(stokes, exponent)
    result = mchi(*scaled)
    assert_value(result.m, 1.0)
    assert_value(result.sin_2chi, -0.8)


def test_m_below_the_smallest_normal_float64_is_out_of_range() -> None:
    result = mchi(1e300, 0.0, 0.0, 1e-300)
    assert_unavailable(result.m, INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)
    # Not M_ZERO: the polarized part is not zero, it only cannot be carried in float64.
    assert_unavailable(result.sin_2chi, INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)
    assert_unavailable(result.chi, INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)


ANY_FINITE = st.floats(allow_nan=False, allow_infinity=False)


@settings(deadline=None, max_examples=300)
@given(st.tuples(ANY_FINITE, ANY_FINITE, ANY_FINITE, ANY_FINITE))
def test_any_finite_stokes_give_a_state_and_a_bounded_chi(
    stokes: tuple[float, float, float, float],
) -> None:
    result = mchi(*stokes)
    for name in MCHI_FIELDS:
        assert getattr(result, name).state in (INVALID, VALID)
    np.testing.assert_array_equal(result.sin_2chi.state, result.chi.state)
    if result.sin_2chi.state == VALID:
        assert result.m.state == VALID
        assert result.m.value > 0.0
        assert -1.0 <= result.sin_2chi.value <= 1.0
        assert -45.0 <= result.chi.value <= 45.0
    if result.m.state == VALID:
        assert result.m.value >= 0.0
        s1, s2, s3, s4 = stokes
        polarized = math.hypot(s2, s3, s4)
        if math.isfinite(polarized) and polarized / s1 >= SMALLEST_NORMAL:
            np.testing.assert_allclose(result.m.value, polarized / s1, rtol=1e-14)


MANTISSA = st.floats(min_value=-1.0, max_value=1.0)


@settings(deadline=None, max_examples=300)
@given(
    st.floats(min_value=1.0, max_value=2.0),
    st.tuples(MANTISSA, MANTISSA, MANTISSA),
    st.integers(min_value=-1000, max_value=1000),
)
def test_m_and_sin_2chi_match_the_oracle_at_any_scale(
    s1: float, polarized: tuple[float, float, float], exponent: int
) -> None:
    s2, s3, s4 = (0.5 * x for x in polarized)  # m <= sqrt(3)/2, so always physical
    scaled = [math.ldexp(x, exponent) for x in (s1, s2, s3, s4)]
    # The oracle sees the same (possibly subnormal) inputs as the code under test.
    expected = hybrid_oracle.m_chi(*scaled)
    result = mchi(*scaled)
    if expected.m == 0.0 and math.hypot(*scaled[1:]) == 0.0:
        assert result.m.value == 0.0
        assert result.sin_2chi.reason == InvalidReason.M_ZERO
        return
    if expected.m < SMALLEST_NORMAL:
        assert result.m.reason == InvalidReason.FLOAT64_OUT_OF_RANGE
        return
    np.testing.assert_allclose(result.m.value, expected.m, rtol=1e-14)
    assert expected.sin_2chi is not None
    np.testing.assert_allclose(result.sin_2chi.value, expected.sin_2chi, rtol=1e-13, atol=1e-15)


def test_m_chi_reason_codes_exist() -> None:
    assert InvalidReason.M_ZERO.value == "M_ZERO"
    assert InvalidReason.STOKES_S1_NONPOSITIVE.value == "STOKES_S1_NONPOSITIVE"


# --- Lunar S4-sign check (PRD 6.2) --------------------------------------------------------------


def sign_check_arrays(
    n_cpr_half: int, n_cpr_two: int, n_cpr_one: int = 0
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """S1 and S4 for pixels with CPR 0.5 (S1 = 3, S4 = 1), CPR 2 (S4 = -1) and CPR 1 (S4 = 0)."""
    s4 = np.array([1.0] * n_cpr_half + [-1.0] * n_cpr_two + [0.0] * n_cpr_one)
    return np.full(s4.shape, 3.0), s4


def all_valid(shape: tuple[int, ...]) -> npt.NDArray[np.bool_]:
    return np.ones(shape, dtype=bool)


def test_most_pixels_with_cpr_below_one_pass() -> None:
    s1, s4 = sign_check_arrays(95, 5)
    cpr = circular_from_stokes(s1, s4, s2=None, s3=None, convention=SIS, product_masked=False)
    assert np.count_nonzero(cpr.cpr.value == 0.5) == 95
    result = lunar_s4_sign_check(s1, s4, all_valid(s1.shape))
    assert result.verdict == SignCheckVerdict.PASS
    assert result.n_used == 100
    assert result.fraction_cpr_above_1 == 0.05
    assert result.fraction_s4_positive == 0.95


def test_negated_s4_gives_sign_suspect_and_is_never_flipped() -> None:
    s1, s4 = sign_check_arrays(95, 5)
    negated = -s4
    before = negated.copy()
    result = lunar_s4_sign_check(s1, negated, all_valid(s1.shape))
    assert result.verdict == SignCheckVerdict.SIGN_SUSPECT
    assert result.fraction_cpr_above_1 == 0.95
    assert result.fraction_s4_positive == 0.05
    # The helper reports what it sees: the input is unchanged and no corrected S4 is returned.
    np.testing.assert_array_equal(negated, before)
    names = {field.name for field in dataclasses.fields(LunarSignCheck)}
    assert not any(name in names for name in ("s4", "s1", "corrected_s4", "flipped", "cpr"))
    assert "flip" not in " ".join(names)
    # Negating again by hand is the caller's decision; the helper never does it for them.
    assert lunar_s4_sign_check(s1, s4, all_valid(s1.shape)).verdict == SignCheckVerdict.PASS


@pytest.mark.parametrize(
    ("n_half", "n_two", "n_one", "verdict"),
    [
        (50, 50, 0, SignCheckVerdict.INCONCLUSIVE),
        (49, 51, 0, SignCheckVerdict.SIGN_SUSPECT),
        (51, 49, 0, SignCheckVerdict.PASS),
        (40, 30, 30, SignCheckVerdict.INCONCLUSIVE),
        (1, 0, 0, SignCheckVerdict.PASS),
        (0, 1, 0, SignCheckVerdict.SIGN_SUSPECT),
        (0, 0, 1, SignCheckVerdict.INCONCLUSIVE),
    ],
)
def test_most_means_more_than_half(
    n_half: int, n_two: int, n_one: int, verdict: SignCheckVerdict
) -> None:
    s1, s4 = sign_check_arrays(n_half, n_two, n_one)
    result = lunar_s4_sign_check(s1, s4, all_valid(s1.shape))
    assert result.verdict == verdict
    assert result.n_cpr_above_1 == n_two
    assert result.n_s4_positive == n_half


@settings(deadline=None)
@given(
    st.integers(min_value=0, max_value=40),
    st.integers(min_value=0, max_value=40),
    st.integers(min_value=0, max_value=40),
)
def test_verdict_is_a_strict_majority_of_used_pixels(n_half: int, n_two: int, n_one: int) -> None:
    s1, s4 = sign_check_arrays(n_half, n_two, n_one)
    result = lunar_s4_sign_check(s1, s4, all_valid(s1.shape))
    n = n_half + n_two + n_one
    if n == 0:
        assert result.verdict == SignCheckVerdict.NO_VALID_PIXELS
    elif 2 * n_two > n:
        assert result.verdict == SignCheckVerdict.SIGN_SUSPECT
    elif 2 * n_half > n:
        assert result.verdict == SignCheckVerdict.PASS
    else:
        assert result.verdict == SignCheckVerdict.INCONCLUSIVE


def test_masked_and_invalid_pixels_are_excluded_and_counted() -> None:
    s1 = np.array([3.0, 3.0, 3.0, 3.0, 3.0, math.nan, 1.0, 2.0])
    s4 = np.array([1.0, 1.0, 1.0, -1.0, -1.0, 1.0, 2.0, -2.0])
    valid = np.array([True, True, True, True, False, True, True, True])
    result = lunar_s4_sign_check(s1, s4, valid)
    assert result.n_pixels == 8
    assert result.n_used == 4
    assert dict(result.n_excluded_by_reason) == {
        "PRODUCT_MASKED": 1,
        "NONFINITE": 1,
        "STOKES_S1_LT_ABS_S4": 1,
        "OC_NONPOSITIVE": 1,
    }
    assert result.fraction_cpr_above_1 == 0.25
    assert result.fraction_s4_positive == 0.75
    assert result.verdict == SignCheckVerdict.PASS


def test_no_valid_pixels_gives_no_fraction_and_never_passes() -> None:
    s1, s4 = sign_check_arrays(3, 0)
    result = lunar_s4_sign_check(s1, s4, np.zeros(s1.shape, dtype=bool))
    assert result.verdict == SignCheckVerdict.NO_VALID_PIXELS
    assert result.n_used == 0
    assert result.fraction_cpr_above_1 is None
    assert result.fraction_s4_positive is None
    assert dict(result.n_excluded_by_reason) == {"PRODUCT_MASKED": 3}


def test_sign_check_takes_a_boolean_validity_mask() -> None:
    s1, s4 = sign_check_arrays(3, 0)
    with pytest.raises(TypeError, match="validity_mask"):
        lunar_s4_sign_check(s1, s4, np.ones(s1.shape, dtype=np.int8))
    with pytest.raises(TypeError, match="masked"):
        lunar_s4_sign_check(s1, s4, np.ma.array(all_valid(s1.shape), mask=False))
    with pytest.raises(TypeError, match="masked"):
        lunar_s4_sign_check(np.ma.array(s1, mask=False), s4, all_valid(s1.shape))


def test_sign_check_record_carries_its_convention_and_is_read_only() -> None:
    s1, s4 = sign_check_arrays(3, 1)
    result = lunar_s4_sign_check(s1, s4, all_valid(s1.shape))
    assert result.convention == SIS
    assert result.tolerance == ARITHMETIC_TOLERANCE
    with pytest.raises(TypeError):
        result.n_excluded_by_reason["NONE"] = 1  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.verdict = SignCheckVerdict.PASS  # type: ignore[misc]


def test_sign_check_verdicts_are_named_codes() -> None:
    assert {v.value for v in SignCheckVerdict} == {
        "PASS",
        "SIGN_SUSPECT",
        "INCONCLUSIVE",
        "NO_VALID_PIXELS",
    }


def test_sign_check_works_on_two_dimensional_arrays() -> None:
    s1, s4 = sign_check_arrays(95, 5)
    result = lunar_s4_sign_check(s1.reshape(10, 10), -s4.reshape(10, 10), all_valid((10, 10)))
    assert result.verdict == SignCheckVerdict.SIGN_SUSPECT
    assert result.n_pixels == 100
