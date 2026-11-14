"""Single-scatterer Jones-vector oracle for circular powers (PRD 6.2, 7).

A reciprocal scatterer has the scattering matrix ``S = [[HH, HV], [HV, VV]]`` in the (H, V)
basis. A transmit Jones vector ``t`` gives the backscattered field ``E = S t``. With transmit
and receive in the same (H, V) basis, the same-sense power is ``|t^T E|**2`` and the
opposite-sense power is ``|t^H E|**2``; their sum is ``|E|**2``, since ``t`` and ``conj(t)`` are
orthonormal for a circular ``t``. A plane mirror (``S`` the identity) returns only opposite-sense
power. PRD 6.2 states that the transmit vector ``(1, -i)/sqrt2`` reproduces SC_LL, the LL/LR
convention of Neish et al.; ``(1, +i)/sqrt2`` is the opposite handedness.

A covariance built as a sum of rank-one terms ``k k^H`` with ``k = (HH, HV, VV)`` has powers
equal to the sum of the scatterers' powers, because each power is linear in the covariance.

This module works on the 2x2 scattering matrix and never forms the 3x3 basis transformation or
the PRD 6.2 expanded formulas. It does not import ``terraluna_pipeline.polarimetry``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Scatterer = tuple[complex, complex, complex]
"""A reciprocal scattering vector ``(HH, HV, VV)``."""

_HALF_ROOT = 1.0 / math.sqrt(2.0)

TRANSMIT_LL: npt.NDArray[np.complex128] = np.array(
    [_HALF_ROOT, -1.0j * _HALF_ROOT], dtype=np.complex128
)
"""Transmit Jones vector ``(1, -i)/sqrt2`` in the (H, V) basis: SC_LL (PRD 6.2)."""

TRANSMIT_OPPOSITE: npt.NDArray[np.complex128] = np.array(
    [_HALF_ROOT, 1.0j * _HALF_ROOT], dtype=np.complex128
)
"""Transmit Jones vector ``(1, +i)/sqrt2``: the opposite handedness, SC_opp (PRD 6.2)."""


@dataclass(frozen=True, slots=True)
class JonesPowers:
    """Received powers for one transmit vector, in the linear power units of ``|S|**2``."""

    sc: float
    oc: float
    total: float
    """``|S t|**2``, computed from the field itself, not as ``sc + oc``."""


def scattering_matrix(scatterer: Scatterer) -> npt.NDArray[np.complex128]:
    hh, hv, vv = scatterer
    return np.array([[hh, hv], [hv, vv]], dtype=np.complex128)


def single_scatterer_powers(
    scatterer: Scatterer, transmit: npt.NDArray[np.complex128]
) -> JonesPowers:
    """Same-sense, opposite-sense and total received power of one scatterer."""
    field = scattering_matrix(scatterer) @ transmit
    same_sense = complex(transmit @ field)
    opposite_sense = complex(transmit.conj() @ field)
    return JonesPowers(
        sc=abs(same_sense) ** 2,
        oc=abs(opposite_sense) ** 2,
        total=float(np.vdot(field, field).real),
    )


def scatterer_sum_powers(
    scatterers: Sequence[Scatterer], transmit: npt.NDArray[np.complex128]
) -> JonesPowers:
    """Powers of the covariance ``sum k k^H``, as the sum of each scatterer's powers."""
    parts = [single_scatterer_powers(s, transmit) for s in scatterers]
    return JonesPowers(
        sc=math.fsum(p.sc for p in parts),
        oc=math.fsum(p.oc for p in parts),
        total=math.fsum(p.total for p in parts),
    )


def covariance_of(scatterers: Sequence[Scatterer]) -> npt.NDArray[np.complex128]:
    """The covariance ``sum k k^H`` in the (HH, HV, VV) ordering, ``C_ij = <k_i k_j*>``.

    The upper triangle is ``sum k_i conj(k_j)``, the lower triangle its exact conjugate and the
    diagonal ``sum |k_i|**2``, so the fixture is exactly Hermitian. ``np.outer(k, k.conj())``
    is not: with fused multiply-add, ``k_i * conj(k_i)`` can keep an imaginary part of about
    1e-17. The function checks the result before returning it.
    """
    covariance = np.zeros((3, 3), dtype=np.complex128)
    for i in range(3):
        covariance[i, i] = math.fsum(abs(complex(s[i])) ** 2 for s in scatterers)
        for j in range(i + 1, 3):
            upper = sum((complex(s[i]) * complex(s[j]).conjugate() for s in scatterers), 0j)
            covariance[i, j] = upper
            covariance[j, i] = upper.conjugate()
    if not np.array_equal(covariance, covariance.conj().T):
        raise AssertionError("the fixture covariance must be exactly Hermitian")
    return covariance
