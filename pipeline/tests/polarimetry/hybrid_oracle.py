"""Jones-vector oracle for hybrid-polarimetric Stokes parameters and m-chi (PRD 6.2, D18).

Each scatterer is a reciprocal scattering vector ``(HH, HV, VV)`` with the scattering matrix
``S = [[HH, HV], [HV, VV]]`` in the (H, V) basis. A transmit Jones vector ``t`` gives the
received field ``E = S t``, and a covariance ``sum k k^H`` gives the received 2x2 covariance
``J = sum E E^H``, because ``J`` is linear in the covariance. Everything here is computed field
by field from the scatterers; this module never forms a 3x3 or 2x3 transformation and does not
import ``terraluna_pipeline.polarimetry``.

Declared transmit ellipticity: ``t(chi) = (cos chi, i sin chi)``, the Jones vector of a
polarization ellipse of orientation 0 and ellipticity angle ``chi``. So ``chi = -45`` degrees is
``(1, -i)/sqrt2``, the transmit vector of SC_LL (PRD 6.2), and ``chi = +45`` degrees is
``(1, +i)/sqrt2``, that of SC_opp.

Stokes parameters of the received field, in the (H, V) basis:

- ``S1 = |E_H|**2 + |E_V|**2``, the received power;
- ``S2 = |E_H|**2 - |E_V|**2``;
- ``S3 = 2 Re(E_H conj(E_V))``;
- ``S4 = OC - SC`` under the Mini-RF SIS convention (PRD 6.2), with OC and SC relative to the
  transmit: the opposite-sense power ``|t^H E|**2`` minus the same-sense power ``|t^T E|**2``.
  It is computed from those two amplitudes, not from ``E_H conj(E_V)``.

``m = sqrt(S2**2 + S3**2 + S4**2) / S1`` and ``sin(2 chi) = -S4 / (m S1)`` follow PRD 6.2.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Scatterer = tuple[complex, complex, complex]
"""A reciprocal scattering vector ``(HH, HV, VV)``."""

Field = tuple[complex, complex]
"""A received field ``(E_H, E_V)``."""


def transmit_jones(chi_deg: float) -> npt.NDArray[np.complex128]:
    """``(cos chi, i sin chi)`` for the declared ellipticity angle in degrees."""
    if chi_deg == 45.0:
        return np.array([1.0 / math.sqrt(2.0), 1.0j / math.sqrt(2.0)], dtype=np.complex128)
    if chi_deg == -45.0:
        return np.array([1.0 / math.sqrt(2.0), -1.0j / math.sqrt(2.0)], dtype=np.complex128)
    chi = math.radians(chi_deg)
    return np.array([math.cos(chi), 1.0j * math.sin(chi)], dtype=np.complex128)


def received_field(scatterer: Scatterer, transmit: npt.NDArray[np.complex128]) -> Field:
    hh, hv, vv = (complex(x) for x in scatterer)
    t_h, t_v = complex(transmit[0]), complex(transmit[1])
    return (hh * t_h + hv * t_v, hv * t_h + vv * t_v)


@dataclass(frozen=True, slots=True)
class HybridOracle:
    """Received 2x2 covariance and Stokes parameters for one transmit vector."""

    j_hh: float
    j_vv: float
    j_hv: complex
    s1: float
    s2: float
    s3: float
    s4: float
    sc: float
    oc: float


def hybrid_of_fields(fields: Sequence[Field], transmit: npt.NDArray[np.complex128]) -> HybridOracle:
    """Sum the received fields' covariance and Stokes parameters."""
    t = [complex(transmit[0]), complex(transmit[1])]
    j_hh = math.fsum(abs(e_h) ** 2 for e_h, _ in fields)
    j_vv = math.fsum(abs(e_v) ** 2 for _, e_v in fields)
    j_hv = sum((e_h * e_v.conjugate() for e_h, e_v in fields), 0j)
    same = [abs(t[0] * e_h + t[1] * e_v) ** 2 for e_h, e_v in fields]
    opposite = [abs(t[0].conjugate() * e_h + t[1].conjugate() * e_v) ** 2 for e_h, e_v in fields]
    sc = math.fsum(same)
    oc = math.fsum(opposite)
    return HybridOracle(
        j_hh=j_hh,
        j_vv=j_vv,
        j_hv=j_hv,
        s1=math.fsum(abs(e_h) ** 2 + abs(e_v) ** 2 for e_h, e_v in fields),
        s2=math.fsum(abs(e_h) ** 2 - abs(e_v) ** 2 for e_h, e_v in fields),
        s3=math.fsum(2.0 * (e_h * e_v.conjugate()).real for e_h, e_v in fields),
        s4=math.fsum(o - s for o, s in zip(opposite, same, strict=True)),
        sc=sc,
        oc=oc,
    )


def hybrid_of_scatterers(scatterers: Sequence[Scatterer], chi_deg: float) -> HybridOracle:
    """The hybrid-polarimetric record of the covariance ``sum k k^H`` for ``t(chi)``."""
    transmit = transmit_jones(chi_deg)
    return hybrid_of_fields([received_field(s, transmit) for s in scatterers], transmit)


def scatterers_of(covariance: npt.NDArray[np.complex128]) -> list[Scatterer]:
    """Scatterers ``sqrt(lambda) u`` whose sum of ``k k^H`` is a Hermitian PSD covariance in the
    (HH, HV, VV) ordering, from its eigendecomposition. Eigenvalues must not be negative."""
    eigenvalues, vectors = np.linalg.eigh(covariance)
    if eigenvalues.min() < -1e-12 * max(1.0, float(np.abs(eigenvalues).max())):
        raise AssertionError("the oracle needs a positive semidefinite covariance")
    scatterers: list[Scatterer] = []
    for value, vector in zip(eigenvalues, vectors.T, strict=True):
        weight = math.sqrt(max(float(value), 0.0))
        hh, hv, vv = (complex(weight * x) for x in vector)
        scatterers.append((hh, hv, vv))
    return scatterers


@dataclass(frozen=True, slots=True)
class MChiOracle:
    m: float
    sin_2chi: float | None
    """``None`` where ``m`` is zero: the ellipticity is undefined."""


def m_chi(s1: float, s2: float, s3: float, s4: float) -> MChiOracle:
    """PRD 6.2: ``m = sqrt(S2**2 + S3**2 + S4**2) / S1``, ``sin(2 chi) = -S4 / (m S1)``."""
    polarized = math.hypot(s2, s3, s4)
    m = polarized / s1
    return MChiOracle(m=m, sin_2chi=None if polarized == 0.0 else -s4 / (m * s1))
