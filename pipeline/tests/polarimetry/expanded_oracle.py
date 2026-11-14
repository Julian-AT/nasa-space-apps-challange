"""Expanded-formula oracle for UAVSAR covariance to circular powers (PRD 6.2).

The PRD 6.2 formulas, written out literally on the six covariance elements::

    A = <HH HH*>   B = <VV VV*>   V = <HV HV*>
    C = <HH VV*>   D = <HH HV*>   E = <HV VV*>

    base_SC = (A + B - 2 Re C) / 4 + V
    OC      = (A + B + 2 Re C) / 4
    SC_LL   = base_SC - Im D - Im E    # LL/LR convention of Neish et al.
    SC_opp  = base_SC + Im D + Im E    # opposite transmit handedness, same basis

This is a test oracle only (PRD 6.2: use the expanded formulas only as a test oracle). It does
not import ``terraluna_pipeline.polarimetry``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True, slots=True)
class CovarianceElements:
    """The six elements of a covariance in the (HH, HV, VV) ordering, ``C_ij = <k_i k_j*>``."""

    a: float
    b: float
    v: float
    c: complex
    d: complex
    e: complex


@dataclass(frozen=True, slots=True)
class ExpandedPowers:
    """PRD 6.2 circular powers, in the linear power units of the covariance."""

    base_sc: float
    oc: float
    sc_ll: float
    sc_opp: float
    handedness_term: float
    """``Im D + Im E``, the handedness-dependent term (PRD 6.2)."""

    @property
    def cpr_ll(self) -> float:
        return self.sc_ll / self.oc

    @property
    def cpr_opp(self) -> float:
        return self.sc_opp / self.oc


def elements_of(covariance: npt.NDArray[np.complex128]) -> CovarianceElements:
    """Read A, B, V, C, D, E from a 3x3 covariance in the (HH, HV, VV) ordering."""
    if covariance.shape != (3, 3):
        raise ValueError(f"expected one 3x3 covariance, got shape {covariance.shape}")
    return CovarianceElements(
        a=float(covariance[0, 0].real),
        b=float(covariance[2, 2].real),
        v=float(covariance[1, 1].real),
        c=complex(covariance[0, 2]),
        d=complex(covariance[0, 1]),
        e=complex(covariance[1, 2]),
    )


def expanded_powers(elements: CovarianceElements) -> ExpandedPowers:
    """Apply the PRD 6.2 expanded formulas as written."""
    a, b, v = elements.a, elements.b, elements.v
    c, d, e = elements.c, elements.d, elements.e
    base_sc = (a + b - 2.0 * c.real) / 4.0 + v
    oc = (a + b + 2.0 * c.real) / 4.0
    sc_ll = base_sc - d.imag - e.imag
    sc_opp = base_sc + d.imag + e.imag
    return ExpandedPowers(
        base_sc=base_sc,
        oc=oc,
        sc_ll=sc_ll,
        sc_opp=sc_opp,
        handedness_term=d.imag + e.imag,
    )


def expanded_from_matrix(covariance: npt.NDArray[np.complex128]) -> ExpandedPowers:
    """Expanded-formula powers of a 3x3 covariance in the (HH, HV, VV) ordering."""
    return expanded_powers(elements_of(covariance))
