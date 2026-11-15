"""Numerical polarimetry: decoding, angles, circular powers, block statistics, hybrid-pol
observables (PRD 6.2, 7, 8.11, D18).

This module is the numerical contract of PRD section 7. Every function works elementwise on
float64 arrays, or per pixel on one 3x3 complex covariance per pixel, and returns
:class:`StatedArray` values: the values together with a per-element observation state
(:class:`ObsState`) and a machine-readable reason (:class:`InvalidReason`).

Conventions that hold for every function here:

- Three states stay distinct (PRD 7): a missing observation, an invalid measurement and a valid
  value. A valid value may be zero or low; it is never confused with missing.
- An element that is not ``VALID`` holds NaN in ``value``, and its meaning is carried only by
  ``state`` and ``reason``. NaN never stands for zero, and 0.0 never stands for missing.
- Analytical quantities are in linear power; dB is for display and summaries only (PRD 6.2).
  dB values come only from :func:`to_db_for_display`, tagged as :class:`DecibelArray`, which no
  analysis function accepts. Block statistics aggregate linear powers first and derive ratios
  afterwards (:func:`aggregate_blocks`).
- Nothing is repaired, clipped or regularized: no epsilon is added to a denominator (PRD 7).
- Work is in float64 (PRD 8.8), and inputs must be finite and supported (PRD 7). An element
  whose result float64 cannot hold is ``INVALID`` with reason ``FLOAT64_OUT_OF_RANGE``, and the
  other elements are unaffected: a result that overflows, or a positive power below the
  smallest normal float64 (underflowed to zero, or a subnormal without full precision). No
  other numerical range is imposed.
- Product validity and noise flags enter only through explicit arguments, such as ``nodata``,
  the noise flags of :func:`x_power`, ``product_masked`` of :func:`circular_from_stokes`,
  :func:`circular_from_covariance`, :func:`hybrid_stokes_from_covariance` and :func:`m_chi`,
  ``validity_mask`` of :func:`derive_circular` and ``valid_mask`` of
  :func:`lunar_s4_sign_check` (PRD 7: keep the product's flags). Array arguments are plain
  numpy arrays (memory-mapped ones included), scalars, or lists and tuples of them nested to
  any depth, all matched by exact type. A numpy masked array is refused with TypeError
  wherever it appears, because converting it to an array drops its mask and a masked element
  would be read as an observation. Any other type is refused as well, whether it converts
  through ``__array__``, is a custom sequence, a subclass of list, tuple or a scalar type, or
  a third-party ndarray subclass, since its conversion can drop a mask in the same way; the
  caller converts it and passes its mask as a flag.
- Hybrid-polarimetric observables (:func:`hybrid_stokes_from_covariance`, :func:`m_chi`) are
  context in Mode B and enter no distance before G2 (PRD 6.2); their records carry
  ``role = "context"``. No cross-band comparison is offered here.
- Gate G1 (PRD 9) is recorded from pytest's own junit report by :func:`record_g1`, run as
  ``python -m terraluna_pipeline.polarimetry record-g1 --junit <path> --out <path>
  [--pytest-command <command>]``. The required tests are listed in
  ``fixtures/polarimetry/g1_required_tests.json``; no record is written by hand, and its notes
  name the commands and the commit. The command, config version, input hashes and run_id
  come from :mod:`terraluna_pipeline.provenance`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal, Self, cast, overload

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from terraluna_pipeline import provenance
from terraluna_pipeline.schema import GateRecord, GateStatus, InputHash

__all__ = [
    "ARITHMETIC_TOLERANCE",
    "CIRCULAR_BASIS",
    "CONTEXT_ROLE",
    "COVARIANCE_HH_HV_VV",
    "COVARIANCE_HH_SQRT2HV_VV",
    "COVARIANCE_INPUT",
    "G1_FAILURE_ACTION",
    "G1_REQUIRED_TESTS_PATH",
    "INTERNAL_ANGLE_UNIT",
    "MINIRF_SIS_CONVENTION",
    "NEISH_SC_LL_CONVENTION",
    "OPPOSITE_SC_CONVENTION",
    "PALSAR2_CALIBRATION_OFFSET_DB",
    "PRIMARY_RADAR_STATISTIC",
    "REASON_DTYPE",
    "STATE_DTYPE",
    "STOKES_INPUT",
    "TRANSMIT_ELLIPTICITIES",
    "AngleUnit",
    "BlockAggregates",
    "BlockSupport",
    "CircularPowers",
    "CovarianceCircularPowers",
    "CovarianceInput",
    "CovarianceOrdering",
    "DecibelArray",
    "G1InputError",
    "G1RequiredTests",
    "HandednessConvention",
    "HybridStokes",
    "InputKind",
    "InvalidReason",
    "LunarSignCheck",
    "MChi",
    "ObsState",
    "ObservableRole",
    "SignCheckVerdict",
    "StatedArray",
    "StokesConvention",
    "StokesInput",
    "Tolerance",
    "aggregate_blocks",
    "angle_to_degrees",
    "circular_from_covariance",
    "circular_from_stokes",
    "decode_db",
    "decode_linear",
    "derive_circular",
    "hybrid_stokes_from_covariance",
    "load_g1_required_tests",
    "lunar_s4_sign_check",
    "m_chi",
    "m_chi_from_hybrid",
    "record_g1",
    "to_db_for_display",
    "x_power",
]


class ObsState(IntEnum):
    """Observation state of one element (PRD 7: three states stay distinct).

    - ``MISSING``: there is no observation, for example a declared nodata value.
    - ``INVALID``: an observation exists but fails a validity rule, so no value is published.
    - ``VALID``: a usable value. Zero and low values are valid values, not missing ones.

    No state is encoded as 0, so a zero-filled state array is never mistaken for a legal one.

    "Unavailable" (the PRD 3 evidence label, and "unavailable" in PRD 6.2 and 7) is not a fourth
    state. A derived value is unavailable wherever its state is not ``VALID``: ``MISSING`` where
    no observation exists, ``INVALID`` where the observation exists but a rule leaves the value
    unsupported, such as CPR with OC = 0 (fixture F5, ``OC_NONPOSITIVE``) or chi with m = 0
    (``M_ZERO``). Its value is NaN and its :class:`InvalidReason` says why; it is never 0.
    """

    MISSING = 1
    INVALID = 2
    VALID = 3


class InvalidReason(StrEnum):
    """Machine-readable cause for an element that is not ``VALID``.

    Each code is its own name. ``NONE`` is reserved for ``VALID`` elements, and every element
    that is ``MISSING`` or ``INVALID`` carries a code other than ``NONE``.
    """

    NONE = "NONE"
    """The element is valid; there is nothing to report."""

    NODATA = "NODATA"
    """The input equals the product's declared nodata value (state ``MISSING``)."""

    NONFINITE = "NONFINITE"
    """The input is NaN or infinite (state ``INVALID``)."""

    DN_NEGATIVE = "DN_NEGATIVE"
    """An amplitude DN is below zero, which no amplitude can be (state ``INVALID``)."""

    DN_ZERO = "DN_ZERO"
    """An amplitude DN of zero has no finite dB value (state ``INVALID``)."""

    DENOMINATOR_INVALID = "DENOMINATOR_INVALID"
    """The denominator of a ratio is zero, nodata, noise-dominated or otherwise not valid."""

    NUMERATOR_INVALID = "NUMERATOR_INVALID"
    """The numerator of a ratio is zero, nodata, noise-dominated or otherwise not valid."""

    FLOAT64_OUT_OF_RANGE = "FLOAT64_OUT_OF_RANGE"
    """The input is valid, but float64 cannot hold the result (state ``INVALID``): it overflows,
    or a positive power falls below the smallest normal float64, or a covariance's parts lie too
    far apart to be carried at one float64 scale. Not a physical threshold."""

    PRODUCT_MASKED = "PRODUCT_MASKED"
    """The product's own validity or noise flag excludes the element (state ``MISSING``)."""

    STOKES_S1_LT_ABS_S4 = "STOKES_S1_LT_ABS_S4"
    """Stokes ``S1 < |S4|`` beyond the tolerance, so no circular powers exist (``INVALID``)."""

    STOKES_NONPHYSICAL = "STOKES_NONPHYSICAL"
    """With all four Stokes parameters, ``S1**2 < S2**2 + S3**2 + S4**2`` beyond the tolerance
    (state ``INVALID``)."""

    OC_NONPOSITIVE = "OC_NONPOSITIVE"
    """The opposite-sense power OC is zero or negative, so CPR = SC/OC is unavailable
    (state ``INVALID``). No epsilon is added (PRD 7)."""

    SC_NEGATIVE = "SC_NEGATIVE"
    """SC is below zero, accepted within the Stokes or covariance tolerance and reported as
    computed, but no ratio is formed from a negative power and none is clipped to zero (state
    ``INVALID``)."""

    COV_NONFINITE = "COV_NONFINITE"
    """A covariance element has a NaN or infinite real or imaginary part (state ``INVALID``)."""

    COV_NOT_HERMITIAN = "COV_NOT_HERMITIAN"
    """The covariance differs from its conjugate transpose beyond the tolerance, off the
    diagonal or by an imaginary diagonal (state ``INVALID``). It is not repaired (PRD 7)."""

    COV_NEGATIVE_DIAGONAL = "COV_NEGATIVE_DIAGONAL"
    """A diagonal power of the covariance is negative beyond the tolerance (state
    ``INVALID``). It is not repaired (PRD 7)."""

    COV_NOT_PSD = "COV_NOT_PSD"
    """The covariance has an eigenvalue below zero beyond the tolerance, so it is not positive
    semidefinite (state ``INVALID``). No eigenvalue is clipped (PRD 7)."""

    BASE_SC_NONPOSITIVE = "BASE_SC_NONPOSITIVE"
    """base_SC is zero or negative, so the handedness ratio ``(Im D + Im E) / base_SC`` is
    unavailable (state ``INVALID``). No epsilon is added."""

    BLOCK_NO_VALID_PIXELS = "BLOCK_NO_VALID_PIXELS"
    """A block has no pixel that a block statistic may use, so the statistic is unavailable.
    The state is ``MISSING`` where every pixel of the block is missing and ``INVALID``
    otherwise; the block's support record counts the pixels by state and reason."""

    DB_OF_NONPOSITIVE = "DB_OF_NONPOSITIVE"
    """A zero or negative linear value has no finite dB value, so none is shown (state
    ``INVALID``). The linear value itself may be valid; only its display in dB is not."""

    STOKES_S1_NONPOSITIVE = "STOKES_S1_NONPOSITIVE"
    """Stokes ``S1 <= 0``: no received power to normalize by, so no degree of polarization or
    ellipticity exists (state ``INVALID``)."""

    M_ZERO = "M_ZERO"
    """The degree of polarization m is exactly 0 (S2 = S3 = S4 = 0), so the ellipticity chi is
    undefined and unavailable (state ``INVALID``, value NaN). m itself is a valid 0; no epsilon
    is added."""


STATE_DTYPE: Final = np.dtype(np.uint8)
"""dtype of every per-element state array; its values are :class:`ObsState` codes."""

REASON_DTYPE: Final = np.dtype("<U32")
"""dtype of every per-element reason array; its values are :class:`InvalidReason` codes."""

if max(len(r.value) for r in InvalidReason) > REASON_DTYPE.itemsize // np.dtype("<U1").itemsize:
    raise RuntimeError("REASON_DTYPE is too narrow for the InvalidReason codes")

_SMALLEST_NORMAL: Final = float(np.finfo(np.float64).smallest_normal)
"""Smallest normal float64. A positive power below it has underflowed or lost precision."""

_STATE_CODES: Final = frozenset(int(s) for s in ObsState)
_REASON_CODES: Final = frozenset(r.value for r in InvalidReason)


@dataclass(frozen=True, slots=True)
class StatedArray:
    """Values with a per-element observation state and reason.

    ``value``, ``state`` and ``reason`` have the same shape. ``value`` is float64 and holds a
    finite number exactly where ``state`` is ``VALID``, and NaN everywhere else. ``reason`` is
    ``NONE`` exactly where ``state`` is ``VALID``. The arrays are made read-only, so a missing or
    invalid element cannot later be overwritten with a number. The units of ``value`` are those
    stated by the function that returns it.

    The arrays are copied on construction, so later changes to the arrays passed in do not
    reach the record. Masked arrays are refused at any nesting depth, since copying would drop
    their masks (:func:`_require_plain`).
    """

    value: npt.NDArray[np.float64]
    state: npt.NDArray[np.uint8]
    reason: npt.NDArray[np.str_]

    def __post_init__(self) -> None:
        # Private copies first, so no alias can later turn a blanked element into a number.
        for name in ("value", "state", "reason"):
            _require_plain(name, getattr(self, name))
            object.__setattr__(self, name, np.array(getattr(self, name), copy=True))
        if self.value.dtype != np.float64:
            raise TypeError(f"value must be float64, got {self.value.dtype}")
        if self.state.dtype != STATE_DTYPE:
            raise TypeError(f"state must be {STATE_DTYPE}, got {self.state.dtype}")
        if self.reason.dtype.kind != "U":
            raise TypeError(f"reason must be a unicode array, got {self.reason.dtype}")
        if not self.value.shape == self.state.shape == self.reason.shape:
            raise ValueError(
                "value, state and reason must have the same shape, got "
                f"{self.value.shape}, {self.state.shape} and {self.reason.shape}"
            )
        unknown_states = set(np.unique(self.state).tolist()) - _STATE_CODES
        if unknown_states:
            raise ValueError(f"unknown state codes {sorted(unknown_states)}")
        unknown_reasons = set(np.unique(self.reason).tolist()) - _REASON_CODES
        if unknown_reasons:
            raise ValueError(f"unknown reason codes {sorted(unknown_reasons)}")

        valid = self.state == ObsState.VALID
        if np.any(valid != (self.reason == InvalidReason.NONE)):
            raise ValueError("reason must be NONE exactly where state is VALID")
        if not np.all(np.isfinite(self.value[valid])):
            raise ValueError("a VALID element must hold a finite value")
        if not np.all(np.isnan(self.value[~valid])):
            raise ValueError("a MISSING or INVALID element must hold NaN, never a number")

        for array in (self.value, self.state, self.reason):
            array.setflags(write=False)


class _Classifier:
    """Builds state and reason arrays where the first rule to match an element wins."""

    def __init__(self, shape: tuple[int, ...]) -> None:
        self.state: npt.NDArray[np.uint8] = np.full(shape, ObsState.VALID, dtype=STATE_DTYPE)
        self.reason: npt.NDArray[np.str_] = np.full(shape, InvalidReason.NONE, dtype=REASON_DTYPE)

    @property
    def valid(self) -> npt.NDArray[np.bool_]:
        return np.equal(self.state, ObsState.VALID)

    def mark(self, where: npt.NDArray[np.bool_], state: ObsState, reason: InvalidReason) -> None:
        """Set ``state`` and ``reason`` on elements in ``where`` that no earlier rule matched."""
        target = where & self.valid
        self.state[target] = state
        self.reason[target] = reason

    def mark_out_of_range(self, result: npt.NDArray[np.float64], *, positive: bool) -> None:
        """Mark elements whose float64 ``result`` is out of range as ``FLOAT64_OUT_OF_RANGE``.

        A result that is not finite has overflowed. Where ``positive`` is set, the exact result
        is a positive quantity, so one below the smallest normal float64 has underflowed to
        zero or lost precision as a subnormal, and is out of range as well. Only elements that
        are still ``VALID`` are marked, so earlier reasons such as ``NONFINITE`` stand.
        """
        with np.errstate(invalid="ignore"):
            out_of_range = ~np.isfinite(result)
            if positive:
                out_of_range |= result < _SMALLEST_NORMAL
        self.mark(out_of_range, ObsState.INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)

    def mark_below_normal(
        self, result: npt.NDArray[np.float64], *, nonzero: npt.NDArray[np.bool_]
    ) -> None:
        """Mark elements whose exact result is nonzero but below the smallest normal float64.

        ``nonzero`` says, from the inputs and not from ``result``, where the exact result is not
        zero. There a float64 ``result`` of magnitude below the smallest normal float64 has
        underflowed to zero or lost precision as a subnormal, so the element is ``INVALID`` with
        ``FLOAT64_OUT_OF_RANGE``. An exact zero stays a valid zero. Only elements that are still
        ``VALID`` are marked.
        """
        with np.errstate(invalid="ignore"):
            below = nonzero & (np.abs(result) < _SMALLEST_NORMAL)
        self.mark(below, ObsState.INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)

    def copy(self) -> _Classifier:
        """Return an independent classifier with the same states and reasons so far."""
        other = _Classifier(self.state.shape)
        other.state[...] = self.state
        other.reason[...] = self.reason
        return other

    def finish(self, value: npt.NDArray[np.float64]) -> StatedArray:
        """Blank every element that is not VALID and freeze the result."""
        value = np.array(value, dtype=np.float64)
        value[~self.valid] = np.nan
        return StatedArray(value=value, state=self.state, reason=self.reason)


_MAX_NESTING: Final = 64
"""numpy's limit on array dimensions; lists or tuples nested deeper cannot form an array."""

_PLAIN_ARRAY_TYPES: Final = (np.ndarray, np.memmap)
"""numpy's own array types that carry no mask. Subclasses are matched exactly, not by isinstance,
so a third-party subclass that keeps a mask or a unit is refused."""

_SCALAR_TYPES: Final = (bool, int, float, complex, str, bytes, ObsState, InvalidReason)
"""Python scalars numpy converts as single elements, matched exactly like containers, plus this
module's own codes (int and str enums). Non-real ones are refused later by dtype checks."""


def _is_numpy_scalar(item: object) -> bool:
    """True for an instance of one of numpy's own scalar types, not of a subclass of one."""
    if not isinstance(item, np.generic):
        return False
    try:
        return np.dtype(type(item)).type is type(item)
    except (TypeError, ValueError):  # a subclass numpy has no dtype for is not one of its own
        return False


def _require_plain(name: str, values: object) -> None:
    """Raise unless ``values`` converts to an array without losing a mask.

    Accepted: a plain numpy array (:data:`_PLAIN_ARRAY_TYPES`), a scalar
    (:data:`_SCALAR_TYPES` or one of numpy's own scalar types), or lists and tuples of them,
    walked to every depth. Every type is matched exactly, never by isinstance. ``np.asarray``
    drops masks without a warning, and ``np.ma.asarray`` reads them only from the immediate
    members of a sequence, so the walk looks at every member itself. A numpy masked array or the
    masked constant anywhere in ``values`` raises TypeError, whatever is masked. So does any
    other type: numpy converts a custom sequence, an ``__array__`` object, an ndarray subclass or
    a list, tuple or scalar subclass by its own protocol, which can drop a mask the same way.
    Nesting deeper than numpy's dimension limit raises ValueError, so a self-referential list
    cannot recurse forever.
    """
    pending: list[tuple[object, int]] = [(values, 0)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, np.ma.MaskedArray):  # the masked constant is one as well
            raise TypeError(
                f"{name} must not be a numpy masked array or hold one: its mask would be "
                "dropped. Pass product flags explicitly (nodata or a noise flag)."
            )
        # A plain array of object dtype passes here; every caller refuses that dtype afterwards.
        if type(item) in _PLAIN_ARRAY_TYPES or type(item) in _SCALAR_TYPES:
            continue
        if _is_numpy_scalar(item):
            continue
        # Exact lists and tuples only: a subclass can convert by its own ``__array__`` instead
        # of by its members, so the walk would not see what numpy converts.
        if type(item) is list or type(item) is tuple:
            if depth >= _MAX_NESTING:
                raise ValueError(
                    f"{name} nests lists or tuples deeper than numpy's {_MAX_NESTING} dimensions"
                )
            pending.extend((member, depth + 1) for member in item)
            continue
        raise TypeError(
            f"{name} must be a plain numpy array, a scalar, or lists and tuples of them, got "
            f"{type(item).__name__}: converting it could drop a mask. Convert it to a plain "
            "numpy array first and pass product flags explicitly (nodata or a noise flag)."
        )


def _real_array(name: str, values: npt.ArrayLike) -> npt.NDArray[Any]:
    """Return ``values`` as an array of integer or floating dtype, or raise TypeError.

    Booleans, complex numbers, strings and objects are refused rather than coerced, and so are
    masked arrays and inputs whose conversion could drop a mask (:func:`_require_plain`).
    """
    _require_plain(name, values)
    array = np.asarray(values)
    if not (np.issubdtype(array.dtype, np.integer) or np.issubdtype(array.dtype, np.floating)):
        raise TypeError(f"{name} must be a real integer or floating array, got {array.dtype}")
    return array


def _bool_mask(name: str, mask: npt.ArrayLike, shape: tuple[int, ...]) -> npt.NDArray[np.bool_]:
    """Return a boolean flag array broadcast to ``shape``, or raise TypeError or ValueError.

    The flags pass :func:`_require_plain` first, so a masked flag array is refused.
    """
    _require_plain(name, mask)
    array = np.asarray(mask)
    if array.dtype != np.bool_:
        raise TypeError(f"{name} must be a boolean flag array, got {array.dtype}")
    return np.broadcast_to(array, shape)


def _check_nodata(nodata: int | None) -> None:
    if nodata is None:
        return
    if isinstance(nodata, bool) or not isinstance(nodata, int | np.integer):
        raise TypeError(f"nodata must be an integer DN or None, got {nodata!r}")


def _amplitude_scale(calibration_offset_db: float) -> float:
    """Return ``10**(-calibration_offset_db / 20)``, the calibration factor on amplitude DN.

    Raises TypeError for a non-real offset and ValueError for a non-finite one, or for one whose
    factor is not a normal float64: the factor would be zero, infinite or a subnormal without
    full precision, so no DN could decode reliably with it (about +-6150 dB).
    """
    if isinstance(calibration_offset_db, bool) or not isinstance(
        calibration_offset_db, int | float | np.integer | np.floating
    ):
        raise TypeError(
            f"calibration_offset_db must be a real number, got {calibration_offset_db!r}"
        )
    if not np.isfinite(calibration_offset_db):
        raise ValueError(f"calibration_offset_db must be finite, got {calibration_offset_db!r}")
    try:
        scale = math.pow(10.0, -float(calibration_offset_db) / 20.0)
    except OverflowError:
        scale = math.inf
    if not (math.isfinite(scale) and scale >= _SMALLEST_NORMAL):
        raise ValueError(
            f"calibration_offset_db {calibration_offset_db!r} gives an amplitude factor "
            "10**(-offset/20) that is not a normal float64"
        )
    return scale


# --- PALSAR-2 yearly mosaic decoding (PRD 7, Decoding rules) ---------------------------------

PALSAR2_CALIBRATION_OFFSET_DB: Final = 83.0
"""Calibration offset of the PALSAR-2 yearly mosaic in dB (PRD 7, Decoding rules).

``gamma0_db = 20*log10(DN) - 83`` and ``gamma0_linear = DN**2 * 10**(-83/10)``.
"""


def _classify_dn(
    dn: npt.ArrayLike, nodata: int | None
) -> tuple[npt.NDArray[np.float64], _Classifier]:
    """Classify amplitude DN. Rules in order, the first match wins:

    1. ``DN == nodata`` (only when ``nodata`` is declared): ``MISSING``, ``NODATA``.
    2. NaN or infinite: ``INVALID``, ``NONFINITE``.
    3. ``DN < 0``: ``INVALID``, ``DN_NEGATIVE``.
    4. ``DN == 0``: ``INVALID``, ``DN_ZERO``. A zero amplitude has no finite dB value.
    5. Otherwise ``VALID``.
    """
    _check_nodata(nodata)
    raw = _real_array("dn", dn)
    values = raw.astype(np.float64)
    classifier = _Classifier(raw.shape)
    with np.errstate(invalid="ignore"):
        if nodata is not None:
            # Compared on the source dtype, so a large integer nodata is matched exactly.
            classifier.mark(raw == nodata, ObsState.MISSING, InvalidReason.NODATA)
        classifier.mark(~np.isfinite(values), ObsState.INVALID, InvalidReason.NONFINITE)
        classifier.mark(values < 0, ObsState.INVALID, InvalidReason.DN_NEGATIVE)
        classifier.mark(values == 0, ObsState.INVALID, InvalidReason.DN_ZERO)
    return values, classifier


def _decode_power(
    dn: npt.ArrayLike, nodata: int | None, calibration_offset_db: float
) -> tuple[npt.NDArray[np.float64], _Classifier, npt.NDArray[np.float64]]:
    """Classify DN and compute gamma-zero in linear power, shared by both decode forms.

    ``gamma0_linear = DN**2 * 10**(-offset/10)`` is computed as ``(DN * 10**(-offset/20))**2``,
    the same quantity, so a DN whose square alone overflows float64 still decodes when its
    calibrated power is representable. A valid DN whose power float64 cannot hold is then
    ``INVALID`` with ``FLOAT64_OUT_OF_RANGE``, so the dB and linear forms agree on every pixel.
    Returns the DN as float64, the classifier and the power (NaN where not valid).
    """
    scale = _amplitude_scale(calibration_offset_db)
    values, classifier = _classify_dn(dn, nodata)
    power = np.full(values.shape, np.nan)
    valid = classifier.valid
    with np.errstate(over="ignore", under="ignore"):
        power[valid] = np.square(values[valid] * scale)
    classifier.mark_out_of_range(power, positive=True)
    return values, classifier, power


def decode_db(
    dn: npt.ArrayLike,
    *,
    nodata: int | None,
    calibration_offset_db: float = PALSAR2_CALIBRATION_OFFSET_DB,
) -> StatedArray:
    """Decode PALSAR-2 yearly-mosaic amplitude DN to gamma-zero in dB (PRD 7, F8).

    ``gamma0_db = 20*log10(DN) - calibration_offset_db``, with the PRD 7 offset of 83 dB as the
    default. Input is the product's amplitude DN, one value per pixel as delivered: amplitude is
    never averaged as if it were power (PRD 7), and this function does no averaging. The output
    is power in dB, for display and summaries only; analysis uses :func:`decode_linear`.

    ``nodata`` is the product's declared nodata DN, or ``None`` if the product declares none. It
    must be given explicitly. A pixel equal to it is ``MISSING`` with reason ``NODATA``. A DN of
    zero that is not the declared nodata is ``INVALID`` with reason ``DN_ZERO``, never -inf dB.
    Negative DN is ``INVALID`` (``DN_NEGATIVE``) and NaN or infinite DN is ``INVALID``
    (``NONFINITE``). A DN whose linear power float64 cannot hold (see :func:`decode_linear`) is
    ``INVALID`` with ``FLOAT64_OUT_OF_RANGE`` here as well, although its dB value is finite: a
    dB value is shown only where its linear power can be analysed. An offset whose amplitude
    factor ``10**(-offset/20)`` is not a normal float64 raises ValueError. Units: input DN
    (dimensionless amplitude), output dB.
    """
    values, classifier, _ = _decode_power(dn, nodata, calibration_offset_db)
    out = np.full(values.shape, np.nan)
    valid = classifier.valid
    out[valid] = 20.0 * np.log10(values[valid]) - calibration_offset_db
    return classifier.finish(out)


def decode_linear(
    dn: npt.ArrayLike,
    *,
    nodata: int | None,
    calibration_offset_db: float = PALSAR2_CALIBRATION_OFFSET_DB,
) -> StatedArray:
    """Decode PALSAR-2 yearly-mosaic amplitude DN to gamma-zero in linear power (PRD 7, F8).

    ``gamma0_linear = DN**2 * 10**(-calibration_offset_db / 10)``, which with the PRD 7 offset of
    83 dB is ``DN**2 * 10**(-8.3)``. It equals ``10**(gamma0_db / 10)`` for :func:`decode_db`.
    Input is amplitude DN per pixel; squaring turns amplitude into power, so averages belong
    after this step, over linear power, never over DN (PRD 7). This function does no averaging.

    It is computed as ``(DN * 10**(-offset/20))**2``, the same quantity, so ``DN**2`` cannot
    overflow ahead of the calibration.

    States and reasons are those of :func:`decode_db`: declared nodata is ``MISSING``
    (``NODATA``); zero, negative and non-finite DN are ``INVALID``. A DN of zero is invalid here
    as well, so the linear and dB forms always agree on which pixels are valid. A valid DN
    whose power overflows float64, or falls below the smallest normal float64, is ``INVALID``
    with ``FLOAT64_OUT_OF_RANGE`` in both forms, never infinite and never a valid 0.0. Units:
    input DN (dimensionless amplitude), output linear power ratio (gamma-zero, dimensionless).
    """
    _, classifier, power = _decode_power(dn, nodata, calibration_offset_db)
    return classifier.finish(power)


def x_power(
    dn_hh: npt.ArrayLike,
    dn_hv: npt.ArrayLike,
    *,
    nodata: int | None,
    hh_noise_dominated: npt.ArrayLike,
    hv_noise_dominated: npt.ArrayLike,
) -> StatedArray:
    """Cross-polarized power ratio of the PALSAR-2 yearly mosaic (PRD 7, F9).

    ``x_power = (DN_HV / DN_HH)**2``: the ratio of HV to HH gamma-zero in linear power. The
    calibration offset cancels because both channels share the stated calibration (PRD 7); this
    function is only valid under that assumption. The amplitude ratio ``DN_HV / DN_HH`` is not
    returned and is not a power ratio.

    ``dn_hh`` and ``dn_hv`` are amplitude DN of the same shape. ``nodata`` is the product's
    declared nodata DN or ``None``. ``hh_noise_dominated`` and ``hv_noise_dominated`` are the
    product's noise flags or a configured SNR rule (PRD 7: any added SNR rule is written in
    config), given as boolean arrays that broadcast to the channel shape. They must be given;
    pass ``False`` only where the product has no such flag and none is configured.

    Zero, nodata and noise-dominated values are not valid denominators (PRD 7), and no epsilon
    is added. Rules in order, the first match wins:

    1. HH is not a valid denominator (nodata, zero, negative or non-finite DN, or flagged
       noise-dominated): reason ``DENOMINATOR_INVALID``. The state is ``MISSING`` where HH is
       the declared nodata and ``INVALID`` otherwise.
    2. HV is not a valid numerator, by the same tests: reason ``NUMERATOR_INVALID``, with
       ``MISSING`` for nodata HV and ``INVALID`` otherwise.
    3. The ratio overflows float64, or falls below the smallest normal float64 although both
       channels are positive: ``INVALID``, ``FLOAT64_OUT_OF_RANGE``.
    4. Otherwise ``VALID``. Units: output linear power ratio (dimensionless).
    """
    hh_values, hh = _classify_dn(dn_hh, nodata)
    hv_values, hv = _classify_dn(dn_hv, nodata)
    if hh_values.shape != hv_values.shape:
        raise ValueError(
            f"dn_hh and dn_hv must have the same shape, got {hh_values.shape} and {hv_values.shape}"
        )
    shape = hh_values.shape
    hh_noise = _bool_mask("hh_noise_dominated", hh_noise_dominated, shape)
    hv_noise = _bool_mask("hv_noise_dominated", hv_noise_dominated, shape)

    classifier = _Classifier(shape)
    hh_missing = hh.state == ObsState.MISSING
    hv_missing = hv.state == ObsState.MISSING
    bad_hh = ~hh.valid | hh_noise
    bad_hv = ~hv.valid | hv_noise
    classifier.mark(bad_hh & hh_missing, ObsState.MISSING, InvalidReason.DENOMINATOR_INVALID)
    classifier.mark(bad_hh, ObsState.INVALID, InvalidReason.DENOMINATOR_INVALID)
    classifier.mark(bad_hv & hv_missing, ObsState.MISSING, InvalidReason.NUMERATOR_INVALID)
    classifier.mark(bad_hv, ObsState.INVALID, InvalidReason.NUMERATOR_INVALID)

    out = np.full(shape, np.nan)
    valid = classifier.valid
    with np.errstate(over="ignore", under="ignore"):
        # A ratio that overflows or underflows has an exact square out of range as well.
        out[valid] = np.square(hv_values[valid] / hh_values[valid])
    classifier.mark_out_of_range(out, positive=True)
    return classifier.finish(out)


# --- Angles (PRD 7, Decoding rules; F10) -----------------------------------------------------

AngleUnit = Literal["radians", "degrees"]
"""Units an angle may be declared in. Source units are kept on ingest (UAVSAR radians, PALSAR
degrees) and declared explicitly; they are never inferred from magnitude (PRD 7)."""

INTERNAL_ANGLE_UNIT: Final = "degrees"
"""The one internal angle unit (PRD 7, F10). Every angle is converted to it explicitly."""

_ANGLE_UNITS: Final[tuple[str, ...]] = ("radians", "degrees")


def angle_to_degrees(angle: npt.ArrayLike, *, unit: AngleUnit) -> StatedArray:
    """Convert angles in a declared source unit to the internal unit, degrees (PRD 7, F10).

    ``unit`` must be exactly ``"radians"`` or ``"degrees"``. Any other value, including
    ``None``, raises ValueError and nothing is returned: units are never inferred from
    magnitude, so ``1.0`` declared in radians and ``1.0`` declared in degrees give different
    results. Radians are converted with ``degrees = radians * 180 / pi``; degrees are kept.

    NaN or infinite angles are ``INVALID`` with reason ``NONFINITE``. A finite angle in radians
    whose degree value overflows float64 is ``INVALID`` with ``FLOAT64_OUT_OF_RANGE``. Zero and
    subnormal angles are valid angles, not underflows. Units: output degrees.
    """
    if unit not in _ANGLE_UNITS:
        raise ValueError(f"angle unit must be declared as one of {_ANGLE_UNITS}, got {unit!r}")
    values = _real_array("angle", angle).astype(np.float64)
    classifier = _Classifier(values.shape)
    classifier.mark(~np.isfinite(values), ObsState.INVALID, InvalidReason.NONFINITE)
    with np.errstate(over="ignore"):
        out = np.degrees(values) if unit == "radians" else values.copy()
    classifier.mark_out_of_range(out, positive=False)
    return classifier.finish(out)


# --- Tolerances (PRD 7, Tolerances) ----------------------------------------------------------


def _check_tolerance_part(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | np.integer | np.floating):
        raise TypeError(f"{name} must be a real number, got {value!r}")
    number = float(value)
    if not (math.isfinite(number) and number >= 0.0):
        raise ValueError(f"{name} must be finite and not negative, got {value!r}")
    return number


@dataclass(frozen=True, slots=True)
class Tolerance:
    """A comparison tolerance: ``a`` may exceed ``b`` by at most ``atol + rtol * |b|``.

    ``rtol`` (relative, dimensionless) and ``atol`` (absolute, in the units of the compared
    quantity) are finite and not negative. They are stored as float.
    """

    rtol: float
    atol: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "rtol", _check_tolerance_part("rtol", self.rtol))
        object.__setattr__(self, "atol", _check_tolerance_part("atol", self.atol))


ARITHMETIC_TOLERANCE: Final = Tolerance(rtol=1e-10, atol=1e-12)
"""The PRD 7 arithmetic tolerance for normalized float64 fixtures (``rtol = 1e-10``,
``atol = 1e-12``). It is not an instrument accuracy: real-data tolerances come from the product,
its calibration and the reference-reproduction conditions (PRD 7), and are not yet decided."""


# --- Mini-RF Stokes parameters to circular powers (PRD 6.2, 7; F1-F5) ------------------------

StokesConvention = Literal["MINIRF_SIS_S4_EQ_OC_MINUS_SC"]
"""Stokes sign conventions this module knows. Only the Mini-RF SIS convention is declared."""

MINIRF_SIS_CONVENTION: Final[StokesConvention] = "MINIRF_SIS_S4_EQ_OC_MINUS_SC"
"""The Mini-RF data product SIS convention, ``S4 = OC - SC`` (PRD 6.2), so that
``SC = (S1 - S4) / 2`` and ``OC = (S1 + S4) / 2``. Ordinary lunar terrain with CPR below 1 then
has ``S4 > 0``."""

_STOKES_CONVENTIONS: Final[tuple[str, ...]] = (MINIRF_SIS_CONVENTION,)


def _check_stokes_convention(convention: object) -> None:
    if type(convention) is not str or convention not in _STOKES_CONVENTIONS:
        raise ValueError(
            f"Stokes convention must be declared as one of {_STOKES_CONVENTIONS}, got "
            f"{convention!r}; an undeclared sign convention is refused, never guessed"
        )


@dataclass(frozen=True, slots=True)
class CircularPowers:
    """Total power, same-sense power SC, opposite-sense power OC and CPR = SC/OC.

    Each field is a :class:`StatedArray` with its own states, and all have the same shape. Total
    power, SC and OC are kept beside CPR (PRD 6.2), so they stay available where CPR is not.
    ``total`` is S1 = SC + OC. Total, SC and OC are linear powers in the units the product's
    label defines for its Stokes parameters. CPR is a dimensionless linear ratio, not the share
    of total echo, which is ``CPR / (1 + CPR)`` (PRD 6.2). A ``VALID`` CPR needs a ``VALID`` SC
    and a ``VALID``, positive OC. ``convention`` is the declared Stokes convention and
    ``tolerance`` the one the Stokes validity rules used.
    """

    total: StatedArray
    sc: StatedArray
    oc: StatedArray
    cpr: StatedArray
    convention: StokesConvention
    tolerance: Tolerance

    def __post_init__(self) -> None:
        fields = ("total", "sc", "oc", "cpr")
        for name in fields:
            if type(getattr(self, name)) is not StatedArray:
                raise TypeError(f"{name} must be a StatedArray")
        shapes = [getattr(self, name).value.shape for name in fields]
        if any(shape != shapes[0] for shape in shapes):
            raise ValueError(f"total, sc, oc and cpr must have the same shape, got {shapes}")
        _check_stokes_convention(self.convention)
        if type(self.tolerance) is not Tolerance:
            raise TypeError(f"tolerance must be a Tolerance, got {self.tolerance!r}")
        cpr_valid = np.equal(self.cpr.state, ObsState.VALID)
        powers_valid = np.equal(self.sc.state, ObsState.VALID) & np.equal(
            self.oc.state, ObsState.VALID
        )
        with np.errstate(invalid="ignore"):
            oc_positive = self.oc.value > 0.0
        if np.any(cpr_valid & ~(powers_valid & oc_positive)):
            raise ValueError("a VALID CPR needs a VALID SC and a VALID, positive OC")


def _stokes_array(
    name: str, values: npt.ArrayLike, shape: tuple[int, ...] | None
) -> npt.NDArray[np.float64]:
    array = _real_array(name, values).astype(np.float64)
    if shape is not None and array.shape != shape:
        raise ValueError(f"{name} must have the shape of s1, {shape}, got {array.shape}")
    return array


def _beyond_squared_bound(
    s1: npt.NDArray[np.float64],
    s2: npt.NDArray[np.float64],
    s3: npt.NDArray[np.float64],
    s4: npt.NDArray[np.float64],
    tolerance: Tolerance,
) -> npt.NDArray[np.bool_]:
    """Return where ``S2**2 + S3**2 + S4**2 - S1**2 > atol + rtol * S1**2``.

    The tolerance applies to the squared inequality itself, so ``atol`` is in squared power
    units here. Each pixel's four parameters are first scaled by one power of two that brings
    the largest magnitude into [0.5, 1), and ``atol`` by its square. The scaling is exact, so
    no square overflows or underflows enough to change the outcome. Where the scaled ``atol``
    overflows to infinity, all four squares are far below ``atol``, so the exact bound holds and
    the infinite allowance gives the same answer. An all-zero pixel holds the bound. Elements
    with non-finite input give an arbitrary answer; callers classify them first.
    """
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        largest = np.maximum(np.maximum(np.abs(s1), np.abs(s2)), np.maximum(np.abs(s3), np.abs(s4)))
        _, exponent = np.frexp(largest)
        p1, p2, p3, p4 = (np.ldexp(stokes, -exponent) for stokes in (s1, s2, s3, s4))
        total = p1 * p1
        polarized = p2 * p2 + p3 * p3 + p4 * p4
        allowed = np.ldexp(tolerance.atol, -2 * exponent) + tolerance.rtol * total
        beyond: npt.NDArray[np.bool_] = polarized - total > allowed
    return beyond


def circular_from_stokes(
    s1: npt.ArrayLike,
    s4: npt.ArrayLike,
    *,
    s2: npt.ArrayLike | None,
    s3: npt.ArrayLike | None,
    convention: StokesConvention,
    product_masked: npt.ArrayLike,
    tolerance: Tolerance = ARITHMETIC_TOLERANCE,
) -> CircularPowers:
    """Convert Mini-RF Stokes parameters to total power, SC, OC and CPR (PRD 6.2, 7; F1-F5).

    Under the declared SIS convention ``S4 = OC - SC`` (:data:`MINIRF_SIS_CONVENTION`)::

        SC  = (S1 - S4) / 2
        OC  = (S1 + S4) / 2
        CPR = SC / OC

    The factor 2 cancels in CPR but not in absolute powers: SC and OC follow the SIS definition
    above, and the label of the selected product defines what its Stokes powers are (PRD 6.2).
    A product whose label states another power definition or sign convention needs its own
    declared convention, which this module does not yet have. ``convention`` is required;
    anything other than :data:`MINIRF_SIS_CONVENTION`, ``None`` included, raises ValueError and
    nothing is returned. SC and OC are computed as ``S1/2 - S4/2`` and ``S1/2 + S4/2``, the same
    quantities, so no intermediate sum overflows float64. CPR is SC/OC, not the share of total
    echo, which is ``CPR / (1 + CPR)``; a valid high CPR is never truncated (PRD 7).

    ``s1`` and ``s4`` are real arrays of the same shape, in the product's linear power units.
    ``s2`` and ``s3`` are the other two Stokes parameters, given together with that shape, or
    both as ``None`` where the product has no such fields; they must be given explicitly, so a
    product with all four cannot skip the full test by omission. ``product_masked`` is a boolean
    array that broadcasts to the shape and is True where the product's own validity or noise
    flag excludes the pixel, or where a configured SNR rule does (PRD 7: any added SNR rule is
    written in config); no SNR rule is applied here. ``tolerance`` is the Stokes validity
    tolerance. It defaults to :data:`ARITHMETIC_TOLERANCE`, the PRD 7 arithmetic tolerance; a
    real-data Stokes tolerance is an open decision (PRD 7: it comes from the product).

    Rules shared by SC, OC and CPR, in order (the first match wins):

    1. ``product_masked``: ``MISSING``, ``PRODUCT_MASKED``.
    2. Any given Stokes parameter is NaN or infinite: ``INVALID``, ``NONFINITE``.
    3. ``|S4| - S1 > atol + rtol * |S1|``, that is S1 >= |S4| fails beyond the tolerance:
       ``INVALID``, ``STOKES_S1_LT_ABS_S4``.
    4. With S2 and S3: ``S2**2 + S3**2 + S4**2 - S1**2 > atol + rtol * S1**2``, that is
       S1**2 >= S2**2 + S3**2 + S4**2 fails beyond the same tolerance, applied to the squared
       inequality, so ``atol`` is in squared power units here: ``INVALID``,
       ``STOKES_NONPHYSICAL``. It is evaluated after scaling each pixel's four parameters by
       one exact power of two, so no square overflows or underflows to change the outcome.

    Then total power (S1), SC and OC each: ``INVALID`` with ``FLOAT64_OUT_OF_RANGE`` where the
    exact power is not zero but its float64 magnitude is below the smallest normal float64. An
    exact zero is a valid zero (F1), and the sums cannot overflow. Total power is kept where CPR
    is unavailable (PRD 6.2). Within the tolerance S4 may exceed S1 or fall
    below -S1 slightly; SC or OC is then negative by at most half the tolerance and is reported
    as computed, never clipped or repaired (PRD 7).

    Then CPR, after the shared rules:

    5. ``S1 + S4 <= 0`` (decided exactly from the inputs as ``S1 <= -S4``): ``INVALID``,
       ``OC_NONPOSITIVE``. No epsilon is added to a zero or near-zero OC (PRD 7, F5), so a
       small positive OC gives the large CPR it implies.
    6. ``S1 < S4``, a negative SC accepted within the tolerance: ``INVALID``, ``SC_NEGATIVE``.
       A ratio of a negative power is not a CPR, and it is not clipped to a valid zero.
    7. SC or OC is out of float64 range (above): ``INVALID``, ``FLOAT64_OUT_OF_RANGE``.
    8. A CPR float64 cannot hold: ``INVALID``, ``FLOAT64_OUT_OF_RANGE``. For in-range SC and OC
       this does not occur, since CPR is then 0 or within about ``2**+-55``; it is kept as a
       guard so every pixel still ends with a state.

    Missing, invalid and valid low CPR stay distinct (PRD 7): a pixel that is not ``VALID``
    holds NaN with its state and reason, never 0.0. Units: total power, SC and OC in the linear
    power units of the Stokes parameters; CPR dimensionless.
    """
    _check_stokes_convention(convention)
    if type(tolerance) is not Tolerance:
        raise TypeError(f"tolerance must be a Tolerance, got {tolerance!r}")
    if (s2 is None) != (s3 is None):
        raise ValueError("s2 and s3 must be given together, or both as None")

    s1_values = _stokes_array("s1", s1, None)
    shape = s1_values.shape
    s4_values = _stokes_array("s4", s4, shape)
    others = (
        None
        if s2 is None or s3 is None
        else (_stokes_array("s2", s2, shape), _stokes_array("s3", s3, shape))
    )
    masked = _bool_mask("product_masked", product_masked, shape)

    base = _Classifier(shape)
    base.mark(masked, ObsState.MISSING, InvalidReason.PRODUCT_MASKED)
    finite = np.isfinite(s1_values) & np.isfinite(s4_values)
    if others is not None:
        finite &= np.isfinite(others[0]) & np.isfinite(others[1])
    base.mark(~finite, ObsState.INVALID, InvalidReason.NONFINITE)
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        allowed = tolerance.atol + tolerance.rtol * np.abs(s1_values)
        # |S4| - S1 overflows only to +inf, far beyond any tolerance; it never wraps to -inf.
        excess = np.abs(s4_values) - s1_values
        base.mark(excess > allowed, ObsState.INVALID, InvalidReason.STOKES_S1_LT_ABS_S4)
        if others is not None:
            base.mark(
                _beyond_squared_bound(s1_values, others[0], others[1], s4_values, tolerance),
                ObsState.INVALID,
                InvalidReason.STOKES_NONPHYSICAL,
            )

    valid = base.valid
    half_s1 = np.full(shape, np.nan)
    half_s4 = np.full(shape, np.nan)
    with np.errstate(under="ignore"):
        half_s1[valid] = 0.5 * s1_values[valid]
        half_s4[valid] = 0.5 * s4_values[valid]
    sc = half_s1 - half_s4
    oc = half_s1 + half_s4

    total = np.where(valid, s1_values, np.nan)
    total_classifier = base.copy()
    total_classifier.mark_below_normal(total, nonzero=s1_values != 0.0)
    sc_classifier = base.copy()
    sc_classifier.mark_below_normal(sc, nonzero=s1_values != s4_values)
    oc_classifier = base.copy()
    oc_classifier.mark_below_normal(oc, nonzero=s1_values != -s4_values)

    cpr_classifier = base.copy()
    cpr_classifier.mark(s1_values <= -s4_values, ObsState.INVALID, InvalidReason.OC_NONPOSITIVE)
    cpr_classifier.mark(s1_values < s4_values, ObsState.INVALID, InvalidReason.SC_NEGATIVE)
    cpr_classifier.mark(
        ~(sc_classifier.valid & oc_classifier.valid),
        ObsState.INVALID,
        InvalidReason.FLOAT64_OUT_OF_RANGE,
    )
    cpr = np.full(shape, np.nan)
    ratio = cpr_classifier.valid
    with np.errstate(over="ignore", under="ignore"):
        cpr[ratio] = sc[ratio] / oc[ratio]
    cpr_classifier.mark_out_of_range(cpr, positive=False)
    cpr_classifier.mark_below_normal(cpr, nonzero=s1_values != s4_values)

    return CircularPowers(
        total=total_classifier.finish(total),
        sc=sc_classifier.finish(sc),
        oc=oc_classifier.finish(oc),
        cpr=cpr_classifier.finish(cpr),
        convention=convention,
        tolerance=tolerance,
    )


# --- UAVSAR covariance to circular powers (PRD 6.2, 7; F6, F7) -------------------------------

CovarianceOrdering = Literal["HH_HV_VV", "HH_SQRT2HV_VV"]
"""Vector orderings a 3x3 covariance may be declared in; none is assumed."""

COVARIANCE_HH_HV_VV: Final[CovarianceOrdering] = "HH_HV_VV"
"""Covariance of the scattering vector ``k = (HH, HV, VV)``, ``C_ij = <k_i k_j*>``, with the
elements of PRD 6.2, as the UAVSAR products store them::

    [[A,  D,  C],      A = <HH HH*>   B = <VV VV*>   V = <HV HV*>
     [D*, V,  E],      C = <HH VV*>   D = <HH HV*>   E = <HV VV*>
     [C*, E*, B]]
"""

COVARIANCE_HH_SQRT2HV_VV: Final[CovarianceOrdering] = "HH_SQRT2HV_VV"
"""Covariance of ``k = (HH, sqrt2 HV, VV)``, the lexicographic C3 matrix: the
:data:`COVARIANCE_HH_HV_VV` matrix with its middle row and column multiplied by sqrt2, so the
middle element is ``2 V``."""

_ORDERINGS: Final[tuple[str, ...]] = (COVARIANCE_HH_HV_VV, COVARIANCE_HH_SQRT2HV_VV)

_SQRT2: Final = math.sqrt(2.0)
_SQRT_HALF: Final = math.sqrt(0.5)


def _read_only(array: npt.NDArray[Any]) -> npt.NDArray[Any]:
    array.setflags(write=False)
    return array


_TO_SQRT2_ORDERING: Final[dict[str, npt.NDArray[np.float64]]] = {
    COVARIANCE_HH_HV_VV: _read_only(
        np.array([[1.0, _SQRT2, 1.0], [_SQRT2, 2.0, _SQRT2], [1.0, _SQRT2, 1.0]])
    ),
    COVARIANCE_HH_SQRT2HV_VV: _read_only(np.ones((3, 3))),
}
"""Elementwise factors ``n_i n_j`` with ``n = (1, sqrt2, 1)`` that express a covariance in the
(HH, sqrt2 HV, VV) ordering, ``C' = N C N`` with ``N = diag(n)``. This is the change of vector
normalization between the two declared orderings, not a repair. The middle factor is exactly 2."""

HandednessConvention = Literal["NEISH_SC_LL_TX_1_MINUS_I", "SC_OPP_TX_1_PLUS_I"]
"""Transmit handedness conventions this module knows (PRD 6.2); none is assumed."""

NEISH_SC_LL_CONVENTION: Final[HandednessConvention] = "NEISH_SC_LL_TX_1_MINUS_I"
"""SC is SC_LL, the LL/LR convention of Neish et al. (PRD 6.2): the transmit Jones vector is
``(1, -i)/sqrt2`` in the (H, V) basis of the covariance."""

OPPOSITE_SC_CONVENTION: Final[HandednessConvention] = "SC_OPP_TX_1_PLUS_I"
"""SC is SC_opp, the opposite transmit handedness in the same basis (PRD 6.2): the transmit
Jones vector is ``(1, +i)/sqrt2``."""

_HANDEDNESS_CONVENTIONS: Final[tuple[str, ...]] = (NEISH_SC_LL_CONVENTION, OPPOSITE_SC_CONVENTION)

CIRCULAR_BASIS: Final[npt.NDArray[np.complex128]] = _read_only(
    np.array(
        [
            [0.5, complex(0.0, -_SQRT_HALF), -0.5],
            [_SQRT_HALF, 0.0, _SQRT_HALF],
            [0.5, complex(0.0, _SQRT_HALF), -0.5],
        ],
        dtype=np.complex128,
    )
)
"""The unitary 3x3 transformation from the (HH, sqrt2 HV, VV) basis to circular amplitudes.

For a reciprocal scatterer ``S = [[HH, HV], [HV, VV]]`` and a transmit Jones vector ``t`` in
the (H, V) basis, the same-sense amplitude is ``t^T S t`` and the opposite-sense amplitude is
``t^H S t``, with transmit and receive in the same basis. With ``k = (HH, sqrt2 HV, VV)``:

- row 0, ``(1, -i sqrt2, -1) / 2``: ``t^T S t = (HH - 2i HV - VV) / 2`` for
  ``t = (1, -i)/sqrt2``, the SC_LL amplitude (LL/LR convention of Neish et al., PRD 6.2);
- row 1, ``(1, 0, 1) / sqrt2``: sqrt2 times ``t^H S t = (HH + VV) / 2``, the OC amplitude,
  which is the same for both transmit handednesses;
- row 2, ``(1, i sqrt2, -1) / 2``: ``t^T S t = (HH + 2i HV - VV) / 2`` for
  ``t = (1, +i)/sqrt2``, the SC_opp amplitude.

The rows are orthonormal, so the matrix is unitary. The array is read-only."""


def _quadratic_form(row: npt.NDArray[np.complex128]) -> npt.NDArray[np.complex128]:
    """``M`` with ``sum(C * M) = u C u^H`` for the row ``u``: ``M_ij = u_i conj(u_j)``."""
    return np.outer(row, row.conj())


_HANDEDNESS_FORM: Final[npt.NDArray[np.complex128]] = _read_only(
    0.5 * (_quadratic_form(CIRCULAR_BASIS[2]) - _quadratic_form(CIRCULAR_BASIS[0]))
)
"""``K`` with ``Re sum(C' * K) = (SC_opp - SC_LL) / 2`` for a covariance ``C'`` in the
(HH, sqrt2 HV, VV) ordering: half the difference of the quadratic forms of the SC_opp and
SC_LL rows of :data:`CIRCULAR_BASIS`, taken as one linear functional.

Rows 0 and 2 have the same HH and VV entries and conjugate HV entries, so every entry of ``K``
that pairs HH and VV with each other is exactly zero, and so is its HV diagonal entry. The
handedness term is then formed from the HH-HV and HV-VV elements alone (``Im D + Im E``) and
keeps their own precision, instead of being the difference of two rounded powers that can be
far larger."""


def _check_declared(kind: str, value: object, allowed: tuple[str, ...]) -> None:
    if type(value) is not str or value not in allowed:
        raise ValueError(
            f"{kind} must be declared as one of {allowed}, got {value!r}; an undeclared "
            f"{kind} is refused, never guessed"
        )


def _check_handedness_convention(convention: object) -> None:
    _check_declared("handedness convention", convention, _HANDEDNESS_CONVENTIONS)


def _check_ordering(ordering: object) -> None:
    _check_declared("covariance vector ordering", ordering, _ORDERINGS)


def _is_valid(stated: StatedArray) -> npt.NDArray[np.bool_]:
    return np.equal(stated.state, ObsState.VALID)


@dataclass(frozen=True, slots=True)
class CovarianceCircularPowers:
    """Circular powers of a UAVSAR covariance under a declared transmit handedness (PRD 6.2).

    Each field is a :class:`StatedArray` with its own states, and all have the pixel shape of
    the covariance. Powers are linear, in the units of the covariance:

    - ``sc``: same-sense power under ``convention``, SC_LL or SC_opp;
    - ``oc``: opposite-sense power, the same for both handednesses;
    - ``total``: ``SC + OC``, the received power for the declared circular transmit, kept
      beside CPR (PRD 6.2). It is not the quad-pol span ``A + 2 V + B``;
    - ``base_sc``: the handedness-independent part of SC, ``(SC_LL + SC_opp) / 2``;
    - ``handedness_term``: ``Im D + Im E = (SC_opp - SC_LL) / 2`` (PRD 6.2), so that
      ``SC_LL = base_SC - Im D - Im E`` and ``SC_opp = base_SC + Im D + Im E``.

    ``cpr`` is SC/OC, a dimensionless linear ratio, not the share of total echo, which is
    ``CPR / (1 + CPR)`` (PRD 6.2). ``handedness_ratio`` is ``handedness_term / base_sc``,
    dimensionless, reported per pixel without a threshold. A ``VALID`` CPR needs a ``VALID``,
    non-negative SC and a ``VALID``, positive OC; a ``VALID`` handedness ratio needs a ``VALID``
    handedness term and a ``VALID``, positive base_SC.

    ``convention``, ``ordering`` and ``tolerance`` are the declarations the conversion used.
    ``handedness_verified`` is False: no Earth result is Mini-RF-equivalent until handedness
    and basis are verified on both sides (PRD 6.2). The conversion never sets it; a caller that
    has verified both may record that with ``dataclasses.replace``.
    """

    total: StatedArray
    sc: StatedArray
    oc: StatedArray
    cpr: StatedArray
    base_sc: StatedArray
    handedness_term: StatedArray
    handedness_ratio: StatedArray
    convention: HandednessConvention
    ordering: CovarianceOrdering
    tolerance: Tolerance
    handedness_verified: bool = False

    def __post_init__(self) -> None:
        fields = (
            "total",
            "sc",
            "oc",
            "cpr",
            "base_sc",
            "handedness_term",
            "handedness_ratio",
        )
        for name in fields:
            if type(getattr(self, name)) is not StatedArray:
                raise TypeError(f"{name} must be a StatedArray")
        shapes = [getattr(self, name).value.shape for name in fields]
        if any(shape != shapes[0] for shape in shapes):
            raise ValueError(f"{', '.join(fields)} must have the same shape, got {shapes}")
        _check_handedness_convention(self.convention)
        _check_ordering(self.ordering)
        if type(self.tolerance) is not Tolerance:
            raise TypeError(f"tolerance must be a Tolerance, got {self.tolerance!r}")
        if type(self.handedness_verified) is not bool:
            raise TypeError(f"handedness_verified must be a bool, got {self.handedness_verified!r}")
        with np.errstate(invalid="ignore"):
            sc_usable = _is_valid(self.sc) & (self.sc.value >= 0.0)
            oc_usable = _is_valid(self.oc) & (self.oc.value > 0.0)
            base_usable = _is_valid(self.base_sc) & (self.base_sc.value > 0.0)
        if np.any(_is_valid(self.cpr) & ~(sc_usable & oc_usable)):
            raise ValueError("a VALID CPR needs a VALID, non-negative SC and a VALID, positive OC")
        if np.any(
            _is_valid(self.handedness_ratio) & ~(_is_valid(self.handedness_term) & base_usable)
        ):
            raise ValueError(
                "a VALID handedness_ratio needs a VALID handedness_term and a VALID, positive "
                "base_sc"
            )


def _covariance_array(name: str, values: npt.ArrayLike) -> npt.NDArray[np.complex128]:
    """Return ``values`` as a complex128 array of shape ``(..., 3, 3)``, or raise.

    Integer, floating and complex dtypes are accepted (a real covariance is a complex one with
    zero imaginary parts); booleans, strings and objects raise TypeError, and so do masked
    arrays and inputs whose conversion could drop a mask (:func:`_require_plain`). The result
    is a new array, so the caller's input is never modified.
    """
    _require_plain(name, values)
    array = np.asarray(values)
    if not (
        np.issubdtype(array.dtype, np.integer)
        or np.issubdtype(array.dtype, np.floating)
        or np.issubdtype(array.dtype, np.complexfloating)
    ):
        raise TypeError(f"{name} must be a real or complex numeric array, got {array.dtype}")
    if array.ndim < 2 or array.shape[-2:] != (3, 3):
        raise ValueError(f"{name} must end in a 3x3 matrix, shape (..., 3, 3), got {array.shape}")
    return array.astype(np.complex128)


_CARRIED_RANGE: Final = 1016
"""A nonzero real or imaginary part of ``C'`` below ``2**-_CARRIED_RANGE`` times the largest
part of ``C'`` cannot be carried through the transformation at the matrix's scale.

After the power-of-two scaling the largest part of ``C'`` lies in [0.5, 2), so every part at
least ``2**-1016`` times as large is at least ``2**-1017``. On its way into a result a part is
multiplied by factors whose product is at least 1/16: the 1/2 of the Hermitian part, two
basis entries of magnitude 1/2 or 1/sqrt2 and the 1/2 of OC or base_SC (for the handedness
term, the 1/2 and an entry 1/(2 sqrt2) of ``_HANDEDNESS_FORM``). Each product so stays at
least ``2**-1021``, a normal float64, and keeps its full precision. A smaller part
would round to a subnormal or to zero and could leave a VALID zero where the exact result is
not zero. This is float64 representability, not a physical threshold: it binds only matrices
whose parts span about 300 decimal orders of magnitude.

The hybrid-polarimetric synthesis (:func:`hybrid_stokes_from_covariance`) relies on the same
guard. A part of ``C'`` that passes it is at least ``2**-1017``. On its way into a hybrid
output it is multiplied by factors whose product is at least 1/8: the 1/2 of the Hermitian
part, at least 1/2 to express it in the (HH, HV, VV) ordering (1/sqrt2 or 1/2), and an entry
of magnitude 1/2 or 1 of the forms. A part of a declared (HH, HV, VV) matrix is at least half
the corresponding part of ``C'`` and meets the same 1/2 of the Hermitian part and of the
forms. Each product so stays at least ``2**-1020``, also a normal float64, and is exact."""


def _scale_by_power_of_two(
    covariance: npt.NDArray[np.complex128],
) -> tuple[npt.NDArray[np.complex128], npt.NDArray[np.int32]]:
    """Scale each matrix by one power of two so its largest real or imaginary part lies in
    [0.5, 1), and return the scaled matrices with each matrix's exponent ``e``.

    ``covariance == ldexp(scaled, e)``. The scaling is exact for every part within a factor of
    about ``2**1021`` of the matrix's largest part; a smaller part is below float64 resolution
    at the matrix's scale and may round to a subnormal or to zero, so the caller refuses such a
    matrix (:data:`_CARRIED_RANGE`). An all-zero matrix keeps exponent 0. Inputs must be finite.
    """
    with np.errstate(under="ignore"):
        largest = np.asarray(
            np.maximum(np.abs(covariance.real), np.abs(covariance.imag)).max(axis=(-2, -1))
        )
        _, exponent = np.frexp(largest)
        exponent = np.asarray(exponent)
        shift = (-exponent)[..., np.newaxis, np.newaxis]
        scaled = np.empty_like(covariance)
        scaled.real = np.ldexp(covariance.real, shift)
        scaled.imag = np.ldexp(covariance.imag, shift)
    return scaled, exponent


def _unscaled_power(
    base: _Classifier, scaled: npt.NDArray[np.float64], exponent: npt.NDArray[np.int32]
) -> tuple[_Classifier, npt.NDArray[np.float64]]:
    """Undo the power-of-two scaling of one result and mark the elements float64 cannot hold.

    The result is ``ldexp(scaled, e)``. Where it overflows, or where ``scaled`` is not zero but
    the result falls below the smallest normal float64, the element is ``INVALID`` with
    ``FLOAT64_OUT_OF_RANGE``. A zero at the matrix's scale stays a valid zero.
    """
    classifier = base.copy()
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        value = np.asarray(np.ldexp(scaled, exponent), dtype=np.float64)
        nonzero = np.asarray(scaled != 0.0)
    classifier.mark_out_of_range(value, positive=False)
    classifier.mark_below_normal(value, nonzero=nonzero)
    return classifier, value


def _ratio(
    base: _Classifier,
    numerator: npt.NDArray[np.float64],
    denominator: npt.NDArray[np.float64],
    *,
    usable: npt.NDArray[np.bool_],
) -> tuple[_Classifier, npt.NDArray[np.float64]]:
    """Divide where ``base`` is still valid, then mark ratios float64 cannot hold.

    Both operands are taken at the matrix's scale, where the ratio is the same as for the
    unscaled powers. ``usable`` must already be folded into ``base`` by the caller's rules.
    """
    out = np.full(base.state.shape, np.nan)
    with np.errstate(over="ignore", under="ignore", divide="ignore", invalid="ignore"):
        np.divide(numerator, denominator, out=out, where=usable & base.valid)
    base.mark_out_of_range(out, positive=False)
    base.mark_below_normal(out, nonzero=np.asarray(numerator != 0.0))
    return base, out


def _checked_covariance(
    covariance: npt.ArrayLike,
    *,
    ordering: CovarianceOrdering,
    product_masked: npt.ArrayLike,
    tolerance: Tolerance,
) -> tuple[
    _Classifier,
    npt.NDArray[np.complex128],
    npt.NDArray[np.complex128],
    npt.NDArray[np.int32],
]:
    """Apply the shared covariance rules 1-6 of :func:`circular_from_covariance`.

    Returns ``(base, scaled, hermitian, exponent)``: the classifier after the rules; each
    matrix in its declared ordering scaled by ``2**-e``, with non-finite matrices replaced by
    zeros (rule 2 has marked them); the Hermitian part of the (HH, sqrt2 HV, VV) form ``C'``
    at that scale; and each matrix's exponent ``e``. Every conversion of a 3x3 covariance runs
    these rules, so the same covariance gets the same shared states in each.
    """
    matrices = _covariance_array("covariance", covariance)
    shape = matrices.shape[:-2]
    masked = _bool_mask("product_masked", product_masked, shape)

    base = _Classifier(shape)
    base.mark(masked, ObsState.MISSING, InvalidReason.PRODUCT_MASKED)
    finite = np.asarray(np.all(np.isfinite(matrices), axis=(-2, -1)))
    base.mark(~finite, ObsState.INVALID, InvalidReason.COV_NONFINITE)
    # Non-finite matrices are already invalid; zeros keep the arithmetic below quiet for them.
    finite_matrices = np.where(finite[..., np.newaxis, np.newaxis], matrices, 0.0)

    scaled, exponent = _scale_by_power_of_two(finite_matrices)
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        # The rules run on the (HH, sqrt2 HV, VV) form at the matrix's scale, so they do not
        # depend on the declared ordering.
        normalized = scaled * _TO_SQRT2_ORDERING[ordering]
        # Rule 3: parts too far below the largest part to be carried at the matrix's scale.
        largest_part = np.maximum(np.abs(normalized.real), np.abs(normalized.imag)).max(
            axis=(-2, -1)
        )
        smallest_carried = np.ldexp(largest_part, -_CARRIED_RANGE)[..., np.newaxis, np.newaxis]
        lost_real = (finite_matrices.real != 0.0) & (np.abs(normalized.real) < smallest_carried)
        lost_imag = (finite_matrices.imag != 0.0) & (np.abs(normalized.imag) < smallest_carried)
        base.mark(
            np.asarray(np.any(lost_real | lost_imag, axis=(-2, -1))),
            ObsState.INVALID,
            InvalidReason.FLOAT64_OUT_OF_RANGE,
        )
        adjoint = np.conj(np.swapaxes(normalized, -1, -2))
        largest = np.abs(normalized).max(axis=(-2, -1))
        allowance = np.ldexp(tolerance.atol, -exponent) + tolerance.rtol * largest
        mismatch = np.abs(normalized - adjoint).max(axis=(-2, -1))
        base.mark(
            np.asarray(mismatch > allowance), ObsState.INVALID, InvalidReason.COV_NOT_HERMITIAN
        )
        hermitian = 0.5 * (normalized + adjoint)
        diagonal = np.diagonal(hermitian, axis1=-2, axis2=-1).real
        base.mark(
            np.asarray(diagonal.min(axis=-1) < -allowance),
            ObsState.INVALID,
            InvalidReason.COV_NEGATIVE_DIAGONAL,
        )
        smallest_eigenvalue = np.linalg.eigvalsh(hermitian)[..., 0]
        base.mark(
            np.asarray(smallest_eigenvalue < -allowance),
            ObsState.INVALID,
            InvalidReason.COV_NOT_PSD,
        )
    return base, scaled, hermitian, exponent


def circular_from_covariance(
    covariance: npt.ArrayLike,
    *,
    ordering: CovarianceOrdering,
    convention: HandednessConvention,
    product_masked: npt.ArrayLike,
    tolerance: Tolerance = ARITHMETIC_TOLERANCE,
) -> CovarianceCircularPowers:
    """Convert a UAVSAR 3x3 covariance to circular SC, OC and CPR (PRD 6.2, 7; F6, F7).

    The conversion is an explicit unitary basis transformation; the PRD 6.2 expanded formulas
    are used only as a test oracle. ``covariance`` has shape ``(..., 3, 3)``, one matrix per
    pixel, in the declared vector ``ordering``:

    - :data:`COVARIANCE_HH_HV_VV`: ``k = (HH, HV, VV)``, as UAVSAR stores it;
    - :data:`COVARIANCE_HH_SQRT2HV_VV`: ``k = (HH, sqrt2 HV, VV)``.

    with ``C_ij = <k_i k_j*>``. Reciprocity (``HV = VH``) is assumed, as in the product. A
    covariance in the (HH, HV, VV) ordering is first expressed in the (HH, sqrt2 HV, VV)
    ordering, ``C' = N C N`` with ``N = diag(1, sqrt2, 1)``, a change of vector normalization.
    Then the unitary :data:`CIRCULAR_BASIS` ``U`` maps it to the circular covariance
    ``U C' U^H``, whose diagonal is ``(SC_LL, 2 OC, SC_opp)``. Its rows are the amplitudes
    ``t^T S t`` (same sense) and ``t^H S t`` (opposite sense) for the transmit Jones vector
    ``t = (1, -i)/sqrt2`` in the (H, V) basis, which gives SC_LL in the LL/LR convention of
    Neish et al., and for ``(1, +i)/sqrt2``, the opposite handedness, which gives SC_opp.

    ``convention`` selects SC: :data:`NEISH_SC_LL_CONVENTION` (SC_LL) or
    :data:`OPPOSITE_SC_CONVENTION` (SC_opp). ``ordering`` and ``convention`` are required, and
    anything other than the named constants, ``None`` included, raises ValueError and nothing is
    returned. The record's ``handedness_verified`` is False: no result is labelled
    Mini-RF-equivalent until handedness and basis are verified on both sides (PRD 6.2).

    The handedness term ``Im D + Im E = (SC_opp - SC_LL) / 2`` and the handedness-independent
    ``base_SC = (SC_LL + SC_opp) / 2`` come from the same transformation and are returned beside
    SC, with their ratio. The term is the difference of the SC_opp and SC_LL quadratic forms
    taken as one linear functional of ``C'`` (``_HANDEDNESS_FORM``), not the difference of the
    two rounded powers, so it keeps the precision of the HH-HV and HV-VV elements however large
    the powers are. For natural ground near reflection symmetry the term is small. The
    threshold for "not small" is set on development data (PRD 6.2), so none is applied here.

    ``product_masked`` is a boolean array that broadcasts to the pixel shape and is True where
    the product's own validity or noise flag, or a configured SNR rule, excludes the pixel
    (PRD 7). ``tolerance`` is the Hermitian and positive-semidefinite tolerance. It defaults to
    :data:`ARITHMETIC_TOLERANCE`, the PRD 7 arithmetic tolerance; a real-data tolerance is an
    open decision (PRD 7: it comes from the product). The rules below run on the covariance in
    the (HH, sqrt2 HV, VV) ordering, ``C'``, whatever ordering was declared, so the same
    covariance gets the same outcome in either ordering. ``C'`` is unitarily similar to the
    circular covariance ``U C' U^H``, so its eigenvalues are those of the circular covariance.
    For each pixel the allowance is ``atol + rtol * s``, where ``s = max |C'_ij|`` is the
    largest element magnitude of ``C'`` (for a PSD matrix, its largest diagonal power). The
    relative part is taken at the matrix's scale because rounding in a covariance and in its
    eigenvalues scales with the matrix norm, not with each element. ``atol`` is in the power
    units of the covariance.

    Each matrix is first scaled by one exact power of two (its largest real or imaginary part
    into [0.5, 1)); the rules and the transformation run on the scaled matrix and the powers are
    scaled back, so no intermediate overflows or underflows. A matrix whose parts lie too far
    apart for one float64 scale to carry them all is refused (rule 3), so no part is lost to
    the scaling. Every power still carries the transformation's rounding error, of the order of
    float64 precision times ``s``.

    Rules shared by every output, in order (the first match wins):

    1. ``product_masked``: ``MISSING``, ``PRODUCT_MASKED``.
    2. Any element has a NaN or infinite real or imaginary part: ``INVALID``, ``COV_NONFINITE``.
    3. A nonzero real or imaginary part of ``C'`` is below ``2**-1016`` times the largest part
       of ``C'``, so float64 cannot carry it through the transformation at the matrix's scale
       (``_CARRIED_RANGE``): ``INVALID``, ``FLOAT64_OUT_OF_RANGE``. This is representability,
       not a physical threshold; it also keeps a lost part from deciding rules 4 to 6.
    4. ``max |C'_ij - conj(C'_ji)| > allowance``, an off-diagonal mismatch or an imaginary
       diagonal beyond the tolerance: ``INVALID``, ``COV_NOT_HERMITIAN``.
    5. A diagonal power of ``C'`` below ``-allowance``: ``INVALID``, ``COV_NEGATIVE_DIAGONAL``.
    6. The smallest eigenvalue of ``C'`` below ``-allowance``: ``INVALID``, ``COV_NOT_PSD``.

    A materially nonphysical covariance is flagged with its cause and nothing is published for
    it; the input is not repaired and no eigenvalue is clipped (PRD 7). A matrix inside the
    tolerance is not altered either. Its eigenvalues and powers are evaluated on the Hermitian
    part ``(C' + C'^H) / 2``, which is ``C'`` for a Hermitian input; for a power this is exactly
    the real part of the quadratic form ``u C' u^H`` that defines it. A power that is negative
    within the tolerance is reported as computed, never clipped to zero.

    Then total, SC, OC, base_SC and the handedness term each: ``INVALID`` with
    ``FLOAT64_OUT_OF_RANGE`` where the scaled-back value overflows, or is not zero at the
    matrix's scale but falls below the smallest normal float64.

    Then CPR:

    7. ``OC <= 0``: ``INVALID``, ``OC_NONPOSITIVE``. No epsilon is added to a zero or
       near-zero OC (PRD 7). OC carries the transformation's rounding error, of the order of
       float64 precision times ``s``, and a small positive OC gives the large CPR it implies;
       whether such an OC is reliable is the product's flag to set (``product_masked``).
    8. ``SC < 0``, a negative SC accepted within the tolerance: ``INVALID``, ``SC_NEGATIVE``.
    9. SC or OC out of float64 range, or a CPR float64 cannot hold: ``INVALID``,
       ``FLOAT64_OUT_OF_RANGE``. A valid high CPR is never truncated (PRD 7).

    And the handedness ratio:

    10. ``base_SC <= 0``: ``INVALID``, ``BASE_SC_NONPOSITIVE``. No epsilon is added.
    11. The handedness term or base_SC out of float64 range, or a ratio float64 cannot hold:
        ``INVALID``, ``FLOAT64_OUT_OF_RANGE``.

    Missing, invalid and valid low CPR stay distinct (PRD 7): a pixel that is not ``VALID``
    holds NaN with its state and reason, never 0.0. Units: total, SC, OC, base_SC and the
    handedness term in the linear power units of the covariance; CPR and the handedness ratio
    dimensionless.
    """
    _check_ordering(ordering)
    _check_handedness_convention(convention)
    if type(tolerance) is not Tolerance:
        raise TypeError(f"tolerance must be a Tolerance, got {tolerance!r}")
    base, _, hermitian, exponent = _checked_covariance(
        covariance, ordering=ordering, product_masked=product_masked, tolerance=tolerance
    )

    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        # The basis transformation, at the matrix's scale.
        circular = CIRCULAR_BASIS @ hermitian @ CIRCULAR_BASIS.conj().T
        diagonal_powers = np.diagonal(circular, axis1=-2, axis2=-1).real
        sc_ll = diagonal_powers[..., 0]
        oc = 0.5 * diagonal_powers[..., 1]
        sc_opp = diagonal_powers[..., 2]
        sc = sc_ll if convention == NEISH_SC_LL_CONVENTION else sc_opp
        base_sc = 0.5 * (sc_ll + sc_opp)
        # One linear functional, not sc_opp - sc_ll: the difference of two rounded powers loses
        # a term far below the powers themselves.
        handedness_term = np.sum(hermitian * _HANDEDNESS_FORM, axis=(-2, -1)).real
        total = sc + oc

    total_classifier, total_value = _unscaled_power(base, total, exponent)
    sc_classifier, sc_value = _unscaled_power(base, sc, exponent)
    oc_classifier, oc_value = _unscaled_power(base, oc, exponent)
    base_classifier, base_value = _unscaled_power(base, base_sc, exponent)
    term_classifier, term_value = _unscaled_power(base, handedness_term, exponent)

    cpr_classifier = base.copy()
    cpr_classifier.mark(np.asarray(oc <= 0.0), ObsState.INVALID, InvalidReason.OC_NONPOSITIVE)
    cpr_classifier.mark(np.asarray(sc < 0.0), ObsState.INVALID, InvalidReason.SC_NEGATIVE)
    powers_in_range = sc_classifier.valid & oc_classifier.valid
    cpr_classifier.mark(~powers_in_range, ObsState.INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)
    cpr_classifier, cpr = _ratio(cpr_classifier, sc, oc, usable=powers_in_range)

    ratio_classifier = base.copy()
    ratio_classifier.mark(
        np.asarray(base_sc <= 0.0), ObsState.INVALID, InvalidReason.BASE_SC_NONPOSITIVE
    )
    parts_in_range = term_classifier.valid & base_classifier.valid
    ratio_classifier.mark(~parts_in_range, ObsState.INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE)
    ratio_classifier, ratio = _ratio(
        ratio_classifier, handedness_term, base_sc, usable=parts_in_range
    )

    return CovarianceCircularPowers(
        total=total_classifier.finish(total_value),
        sc=sc_classifier.finish(sc_value),
        oc=oc_classifier.finish(oc_value),
        cpr=cpr_classifier.finish(cpr),
        base_sc=base_classifier.finish(base_value),
        handedness_term=term_classifier.finish(term_value),
        handedness_ratio=ratio_classifier.finish(ratio),
        convention=convention,
        ordering=ordering,
        tolerance=tolerance,
    )


# --- derive_circular: one interface for Stokes and covariance input (PRD 8.11) ---------------

InputKind = Literal["STOKES", "COVARIANCE"]
"""Input kinds :func:`derive_circular` accepts; none is assumed."""

STOKES_INPUT: Final[Literal["STOKES"]] = "STOKES"
"""Stokes parameters (:class:`StokesInput`), converted by :func:`circular_from_stokes`."""

COVARIANCE_INPUT: Final[Literal["COVARIANCE"]] = "COVARIANCE"
"""A real or complex 3x3 covariance per pixel (:class:`CovarianceInput`), converted by
:func:`circular_from_covariance`."""

_INPUT_KINDS: Final[tuple[str, ...]] = (STOKES_INPUT, COVARIANCE_INPUT)


@dataclass(frozen=True, slots=True)
class StokesInput:
    """Stokes parameters for :func:`derive_circular`, in the product's linear power units.

    ``s1`` and ``s4`` are real arrays of one shape. ``s2`` and ``s3`` are given together with
    that shape, or both as ``None`` where the product has no such fields; all four must be
    stated, so a product with all four cannot skip the full Stokes test by omission. The arrays
    are read and checked when :func:`derive_circular` runs (:func:`circular_from_stokes`).
    """

    s1: npt.ArrayLike
    s4: npt.ArrayLike
    s2: npt.ArrayLike | None
    s3: npt.ArrayLike | None


@dataclass(frozen=True, slots=True)
class CovarianceInput:
    """A covariance for :func:`derive_circular`: one 3x3 matrix per pixel, shape ``(..., 3, 3)``.

    ``ordering`` declares the scattering-vector ordering the matrices are stored in,
    :data:`COVARIANCE_HH_HV_VV` (as UAVSAR stores it) or :data:`COVARIANCE_HH_SQRT2HV_VV`;
    anything else raises ValueError here. The matrices are read and checked when
    :func:`derive_circular` runs (:func:`circular_from_covariance`).
    """

    covariance: npt.ArrayLike
    ordering: CovarianceOrdering

    def __post_init__(self) -> None:
        _check_ordering(self.ordering)


def _validity_flags(validity_mask: npt.ArrayLike) -> npt.NDArray[np.bool_]:
    """Return the product's validity mask as a boolean array, or raise TypeError.

    Masked arrays and inputs whose conversion could drop a mask are refused
    (:func:`_require_plain`), and so is any dtype other than bool: an integer or float mask has
    no declared polarity.
    """
    _require_plain("validity_mask", validity_mask)
    flags = np.asarray(validity_mask)
    if flags.dtype != np.bool_:
        raise TypeError(
            f"validity_mask must be a boolean array, True where the product marks a pixel "
            f"valid, got {flags.dtype}"
        )
    return flags


@overload
def derive_circular(
    input_kind: Literal["STOKES"],
    data: StokesInput,
    convention: StokesConvention,
    validity_mask: npt.ArrayLike,
    *,
    tolerance: Tolerance = ...,
) -> CircularPowers: ...


@overload
def derive_circular(
    input_kind: Literal["COVARIANCE"],
    data: CovarianceInput,
    convention: HandednessConvention,
    validity_mask: npt.ArrayLike,
    *,
    tolerance: Tolerance = ...,
) -> CovarianceCircularPowers: ...


def derive_circular(
    input_kind: InputKind,
    data: StokesInput | CovarianceInput,
    convention: StokesConvention | HandednessConvention,
    validity_mask: npt.ArrayLike,
    *,
    tolerance: Tolerance = ARITHMETIC_TOLERANCE,
) -> CircularPowers | CovarianceCircularPowers:
    """Derive SC, OC and CPR in linear units from Stokes or covariance input (PRD 8.11).

    One interface for both radar inputs; it wraps the conversions and adds no rule of its own:

    - :data:`STOKES_INPUT` with a :class:`StokesInput` and a Stokes convention
      (:data:`MINIRF_SIS_CONVENTION`) runs :func:`circular_from_stokes` and returns its
      :class:`CircularPowers`;
    - :data:`COVARIANCE_INPUT` with a :class:`CovarianceInput` (real or complex covariance in a
      declared ordering) and a handedness convention (:data:`NEISH_SC_LL_CONVENTION` or
      :data:`OPPOSITE_SC_CONVENTION`) runs :func:`circular_from_covariance` and returns its
      :class:`CovarianceCircularPowers`, with ``handedness_verified`` False (PRD 6.2).

    ``input_kind``, ``data``, ``convention`` and ``validity_mask`` are required. An unknown input
    kind, or an undeclared convention or one of the other input's family, raises ValueError and
    nothing is returned; data of the wrong type for the declared kind raises TypeError.

    ``validity_mask`` is the product's validity and noise flags as one boolean array that
    broadcasts to the pixel shape: True where the product marks the pixel as a usable
    observation, False where its flags, or a configured SNR rule, exclude it (PRD 7: keep the
    product's flags; any added SNR rule is written in config). An excluded pixel is ``MISSING``
    with reason ``PRODUCT_MASKED`` in every output. A mask of any other dtype raises TypeError,
    so a mask of unstated polarity is never guessed. ``tolerance`` is passed to the conversion.

    Results: total power, SC and OC in the linear power units of the input, and CPR = SC/OC, a
    dimensionless linear ratio, each a :class:`StatedArray` whose ``state`` and ``reason``
    arrays give the per-pixel observation state and invalid reason. No dB value is produced.
    """
    _check_declared("input kind", input_kind, _INPUT_KINDS)
    product_masked = ~_validity_flags(validity_mask)
    if input_kind == STOKES_INPUT:
        if type(data) is not StokesInput:
            raise TypeError(
                f"input kind {STOKES_INPUT!r} takes a StokesInput, got {type(data).__name__}"
            )
        return circular_from_stokes(
            data.s1,
            data.s4,
            s2=data.s2,
            s3=data.s3,
            convention=cast(StokesConvention, convention),
            product_masked=product_masked,
            tolerance=tolerance,
        )
    if type(data) is not CovarianceInput:
        raise TypeError(
            f"input kind {COVARIANCE_INPUT!r} takes a CovarianceInput, got {type(data).__name__}"
        )
    return circular_from_covariance(
        data.covariance,
        ordering=data.ordering,
        convention=cast(HandednessConvention, convention),
        product_masked=product_masked,
        tolerance=tolerance,
    )


# --- dB for display only (PRD 6.2, Aggregation) ----------------------------------------------


@dataclass(frozen=True, slots=True)
class DecibelArray:
    """Values in dB, tagged for display and summaries only (PRD 6.2).

    ``db`` holds ``10 * log10`` of linear powers or power ratios, with their states and reasons.
    No analysis function accepts a DecibelArray: :func:`aggregate_blocks` and the conversions
    take linear power, and the records that hold powers accept only :class:`StatedArray`
    fields, so dB values cannot enter an analysis by mistake. Made by :func:`to_db_for_display`.
    """

    db: StatedArray

    def __post_init__(self) -> None:
        if type(self.db) is not StatedArray:
            raise TypeError(f"db must be a StatedArray, got {type(self.db).__name__}")


def to_db_for_display(values: StatedArray) -> DecibelArray:
    """dB summary helper, for display only: ``10 * log10`` of a linear power or power ratio.

    ``values`` is a :class:`StatedArray` of linear powers (SC, OC, total power) or of a ratio of
    powers (CPR, a block statistic). Analysis stays in linear power (PRD 6.2); the result is a
    :class:`DecibelArray`, which no analysis function accepts. Amplitudes are not powers and
    are not passed here. States and reasons of elements that are not ``VALID`` are kept. A
    ``VALID`` value of zero or below has no finite dB value and is ``INVALID`` with reason
    ``DB_OF_NONPOSITIVE`` in the dB array, never -inf; the linear value stays valid. Units: dB.
    """
    if type(values) is not StatedArray:
        raise TypeError(
            f"to_db_for_display takes a StatedArray of linear values, got {type(values).__name__}"
        )
    classifier = _Classifier(values.value.shape)
    classifier.state[...] = values.state
    classifier.reason[...] = values.reason
    with np.errstate(invalid="ignore"):
        classifier.mark(
            np.asarray(values.value <= 0.0), ObsState.INVALID, InvalidReason.DB_OF_NONPOSITIVE
        )
    out = np.full(values.value.shape, np.nan)
    shown = classifier.valid
    out[shown] = 10.0 * np.log10(values.value[shown])
    return DecibelArray(db=classifier.finish(out))


# --- Block statistics (PRD 6.2, Aggregation) -------------------------------------------------

PRIMARY_RADAR_STATISTIC: Final = "ratio_of_mean_powers"
"""The primary radar statistic per common-support block: the ratio of mean powers (PRD 6.2)."""


def _frozen_counts(name: str, values: object) -> npt.NDArray[np.int64]:
    _require_plain(name, values)
    array = np.array(values, copy=True)
    if array.dtype != np.int64:
        raise TypeError(f"{name} must be an int64 array, got {array.dtype}")
    if np.any(array < 0):
        raise ValueError(f"{name} must not be negative")
    array.setflags(write=False)
    return array


@dataclass(frozen=True, slots=True)
class BlockSupport:
    """The pixels behind one block statistic, counted per block (PRD 6.2: support recorded).

    ``n_pixels`` is the number of pixels in every block. As arrays over the block grid:
    ``n_valid``, the pixels the statistic used; ``n_missing``, missing pixels;
    ``n_invalid_by_reason``, invalid pixels per reason code, a read-only mapping that holds the
    reasons occurring among the input's invalid pixels; and ``valid_fraction``,
    ``n_valid / n_pixels``. Every pixel of a block is counted once, so ``n_valid + n_missing``
    plus the invalid counts is ``n_pixels``. Missing and invalid pixels are excluded from the
    statistic and counted here, never entered as zero. No minimum valid fraction is imposed;
    the PRD sets none, so the fraction is reported for the consumer to judge.
    """

    n_pixels: int
    n_valid: npt.NDArray[np.int64]
    n_missing: npt.NDArray[np.int64]
    n_invalid_by_reason: Mapping[InvalidReason, npt.NDArray[np.int64]]
    valid_fraction: npt.NDArray[np.float64]

    def __post_init__(self) -> None:
        if type(self.n_pixels) is not int or self.n_pixels < 1:
            raise ValueError(f"n_pixels must be a positive int, got {self.n_pixels!r}")
        n_valid = _frozen_counts("n_valid", self.n_valid)
        n_missing = _frozen_counts("n_missing", self.n_missing)
        if n_missing.shape != n_valid.shape:
            raise ValueError("n_valid and n_missing must have the same shape")
        if not isinstance(self.n_invalid_by_reason, Mapping):
            raise TypeError("n_invalid_by_reason must be a mapping of reason codes to counts")
        by_reason: dict[InvalidReason, npt.NDArray[np.int64]] = {}
        for reason, count in self.n_invalid_by_reason.items():
            if type(reason) is not InvalidReason or reason is InvalidReason.NONE:
                raise ValueError(f"n_invalid_by_reason needs InvalidReason codes, got {reason!r}")
            counts = _frozen_counts(f"n_invalid_by_reason[{reason}]", count)
            if counts.shape != n_valid.shape:
                raise ValueError(f"counts for {reason} must have the shape of n_valid")
            by_reason[reason] = counts
        total = n_valid + n_missing
        for counts in by_reason.values():
            total = total + counts
        if np.any(total != self.n_pixels):
            raise ValueError("every pixel of a block must be counted exactly once")
        fraction = np.array(self.valid_fraction, dtype=np.float64, copy=True)
        if not np.array_equal(fraction, n_valid / self.n_pixels):
            raise ValueError("valid_fraction must be n_valid / n_pixels")
        fraction.setflags(write=False)
        object.__setattr__(self, "n_valid", n_valid)
        object.__setattr__(self, "n_missing", n_missing)
        object.__setattr__(self, "n_invalid_by_reason", MappingProxyType(by_reason))
        object.__setattr__(self, "valid_fraction", fraction)


@dataclass(frozen=True, slots=True)
class BlockAggregates:
    """Block statistics of linear SC, OC and CPR over a pixel grid (PRD 6.2, Aggregation).

    The pixel grid is tiled from its first pixel into whole blocks of ``block_size``, ``(rows,
    cols)`` pixels; ``block_origin[i, j]`` is the (row, col) of the first pixel of block
    ``(i, j)``, as int64. Each statistic is a :class:`StatedArray` over the block grid:

    - ``mean_sc`` and ``mean_oc``: the mean linear SC and OC over the block's power support,
      the pixels whose SC and OC are both ``VALID`` (``power_support``), in the power units of
      the input;
    - ``ratio_of_mean_powers``: ``mean_sc / mean_oc``, dimensionless, the primary radar
      statistic (:data:`PRIMARY_RADAR_STATISTIC`, also in ``primary_statistic``);
    - ``mean_of_pixelwise_ratios``: the mean of the ``VALID`` pixelwise CPR values
      (``ratio_support``), dimensionless, reported as a separate, named statistic.

    ``blocks_independent`` is False and cannot be set otherwise: neighbouring blocks share
    looks and terrain and are not independent samples (PRD 6.2). ``input_kind`` and
    ``convention`` are the declarations of the circular record. ``handedness_verified`` is the
    covariance record's flag, False until handedness and basis are verified on both sides
    (PRD 6.2), and None for Stokes input, which is the Mini-RF product's own convention.
    """

    block_size: tuple[int, int]
    block_origin: npt.NDArray[np.int64]
    mean_sc: StatedArray
    mean_oc: StatedArray
    ratio_of_mean_powers: StatedArray
    mean_of_pixelwise_ratios: StatedArray
    power_support: BlockSupport
    ratio_support: BlockSupport
    input_kind: InputKind
    convention: StokesConvention | HandednessConvention
    handedness_verified: bool | None
    blocks_independent: bool = False
    primary_statistic: str = PRIMARY_RADAR_STATISTIC

    def __post_init__(self) -> None:
        if self.blocks_independent is not False:
            raise ValueError(
                "blocks_independent must be False: blocks are not independent samples (PRD 6.2)"
            )
        if self.primary_statistic != PRIMARY_RADAR_STATISTIC:
            raise ValueError(f"primary_statistic must be {PRIMARY_RADAR_STATISTIC!r}")
        rows, cols = _block_shape(self.block_size)
        statistics = (
            "mean_sc",
            "mean_oc",
            "ratio_of_mean_powers",
            "mean_of_pixelwise_ratios",
        )
        for name in statistics:
            if type(getattr(self, name)) is not StatedArray:
                raise TypeError(f"{name} must be a StatedArray")
        grid = self.mean_sc.value.shape
        if len(grid) != 2 or any(getattr(self, name).value.shape != grid for name in statistics):
            raise ValueError("every statistic must have the shape of one two-dimensional grid")
        for name in ("power_support", "ratio_support"):
            support = getattr(self, name)
            if type(support) is not BlockSupport:
                raise TypeError(f"{name} must be a BlockSupport")
            if support.n_pixels != rows * cols or support.n_valid.shape != grid:
                raise ValueError(f"{name} must count the pixels of each block of the grid")
        origin = _frozen_counts("block_origin", self.block_origin)
        if origin.shape != (*grid, 2):
            raise ValueError(f"block_origin must have shape {(*grid, 2)}, got {origin.shape}")
        object.__setattr__(self, "block_size", (rows, cols))
        object.__setattr__(self, "block_origin", origin)
        _check_declared("input kind", self.input_kind, _INPUT_KINDS)
        if self.input_kind == STOKES_INPUT:
            _check_stokes_convention(self.convention)
            if self.handedness_verified is not None:
                raise ValueError("handedness_verified is None for Stokes input")
        else:
            _check_handedness_convention(self.convention)
            if type(self.handedness_verified) is not bool:
                raise TypeError("handedness_verified must be a bool for covariance input")

        power_used = self.power_support.n_valid > 0
        ratio_used = self.ratio_support.n_valid > 0
        for name in ("mean_sc", "mean_oc", "ratio_of_mean_powers"):
            if np.any(_is_valid(getattr(self, name)) & ~power_used):
                raise ValueError(f"a VALID {name} needs a block with a valid pixel")
        if np.any(_is_valid(self.mean_of_pixelwise_ratios) & ~ratio_used):
            raise ValueError("a VALID mean_of_pixelwise_ratios needs a block with a valid CPR")
        with np.errstate(invalid="ignore"):
            sc_usable = _is_valid(self.mean_sc) & (self.mean_sc.value >= 0.0)
            oc_usable = _is_valid(self.mean_oc) & (self.mean_oc.value > 0.0)
        if np.any(_is_valid(self.ratio_of_mean_powers) & ~(sc_usable & oc_usable)):
            raise ValueError(
                "a VALID ratio_of_mean_powers needs a VALID, non-negative mean_sc and a VALID, "
                "positive mean_oc"
            )


def _block_shape(block_size: object) -> tuple[int, int]:
    """Return ``block_size`` as ``(rows, cols)`` in pixels, or raise TypeError or ValueError."""
    if type(block_size) is int:
        size = (block_size, block_size)
    elif (
        type(block_size) is tuple
        and len(block_size) == 2
        and all(type(n) is int for n in block_size)
    ):
        size = (int(block_size[0]), int(block_size[1]))
    else:
        raise TypeError(
            "block_size must be a positive int or a (rows, cols) pair of positive ints, in "
            f"pixels, got {block_size!r}"
        )
    if size[0] < 1 or size[1] < 1:
        raise ValueError(f"block_size must be positive in both directions, got {block_size!r}")
    return size


def _to_blocks(array: npt.NDArray[Any], rows: int, cols: int) -> npt.NDArray[Any]:
    """``(R, C)`` to ``(R // rows, C // cols, rows * cols)``: each block's pixels in one axis."""
    grid_rows, grid_cols = array.shape[0] // rows, array.shape[1] // cols
    blocks: npt.NDArray[Any] = (
        array.reshape(grid_rows, rows, grid_cols, cols)
        .swapaxes(1, 2)
        .reshape(grid_rows, grid_cols, rows * cols)
    )
    return blocks


def _support(
    state: npt.NDArray[np.uint8], reason: npt.NDArray[np.str_], n_pixels: int
) -> BlockSupport:
    """Count each block's pixels by state, and its invalid pixels by reason."""
    valid = state == ObsState.VALID
    invalid = state == ObsState.INVALID
    codes = sorted(set(np.unique(reason[invalid]).tolist()))
    n_valid = np.asarray(np.sum(valid, axis=-1, dtype=np.int64))
    return BlockSupport(
        n_pixels=n_pixels,
        n_valid=n_valid,
        n_missing=np.asarray(np.sum(state == ObsState.MISSING, axis=-1, dtype=np.int64)),
        n_invalid_by_reason={
            InvalidReason(code): np.asarray(
                np.sum(invalid & (reason == code), axis=-1, dtype=np.int64)
            )
            for code in codes
        },
        valid_fraction=n_valid / n_pixels,
    )


def _no_valid_pixels(support: BlockSupport) -> _Classifier:
    """The first rule of every block statistic: a block with no usable pixel has no value."""
    classifier = _Classifier(support.n_valid.shape)
    empty = support.n_valid == 0
    all_missing = support.n_missing == support.n_pixels
    classifier.mark(empty & all_missing, ObsState.MISSING, InvalidReason.BLOCK_NO_VALID_PIXELS)
    classifier.mark(empty, ObsState.INVALID, InvalidReason.BLOCK_NO_VALID_PIXELS)
    return classifier


_ULP_EXPONENT: Final = 1074
"""Every finite float64 is an integer multiple of ``2**-1074``, the smallest subnormal."""


def _exact_sums(values: npt.NDArray[np.float64], used: npt.NDArray[np.bool_]) -> list[int]:
    """Per block, the exact sum of the used values, as an integer count of ``2**-1074``.

    ``values`` and ``used`` hold each block's pixels in their last axis (:func:`_to_blocks`);
    the sums list the blocks in row-major order. Every finite float64 is an integer multiple
    of ``2**-1074``, so each sum is an exact Python int: it has no rounding error and cannot
    overflow, cancellation between powers of opposite sign loses nothing, and the sum is the
    same for every order of the pixels. The used values are ``VALID``, hence finite. The loop
    runs in Python, at about 0.2 s per million used values on the build machine.
    """
    n_pixels = values.shape[-1]
    sums = [0] * (values.size // n_pixels)
    blocks = (np.flatnonzero(used) // n_pixels).tolist()
    for block, value in zip(blocks, values[used].tolist(), strict=True):
        numerator, denominator = value.as_integer_ratio()
        # denominator is 2**k with k <= 1074, so the shift 1074 - k is never negative.
        sums[block] += numerator << (_ULP_EXPONENT + 1 - denominator.bit_length())
    return sums


def _signs(sums: list[int], grid: tuple[int, ...]) -> npt.NDArray[np.int8]:
    """The exact sign of each block's sum, -1, 0 or 1, over the block grid."""
    signs = np.array([(total > 0) - (total < 0) for total in sums], dtype=np.int8)
    return signs.reshape(grid)


def _rounded_quotients(
    numerators: list[int], denominators: list[int], use: npt.NDArray[np.bool_]
) -> npt.NDArray[np.float64]:
    """``numerator / denominator`` per block where ``use`` is set, NaN elsewhere.

    The denominators are positive. Python's int division rounds the exact quotient once,
    correctly, subnormal results included; a quotient that rounds past the largest float64
    becomes an infinity of the numerator's sign, which the range rules then mark.
    """
    result = np.full(use.shape, np.nan)
    flat = result.reshape(-1)
    for index in np.flatnonzero(use).tolist():
        try:
            flat[index] = numerators[index] / denominators[index]
        except OverflowError:
            flat[index] = math.inf if numerators[index] > 0 else -math.inf
    return result


def _block_mean(
    base: _Classifier, sums: list[int], count: npt.NDArray[np.int64]
) -> tuple[_Classifier, npt.NDArray[np.float64]]:
    """The mean, the exact sum over ``count`` rounded once, where ``base`` is valid.

    A mean float64 cannot hold is ``INVALID`` with ``FLOAT64_OUT_OF_RANGE``: one that rounds
    past the largest float64, or one whose exact sum is not zero but whose mean is below the
    smallest normal float64. A zero sum gives a valid zero mean.
    """
    classifier = base.copy()
    denominators = [int(n) << _ULP_EXPONENT for n in count.ravel().tolist()]
    mean = _rounded_quotients(sums, denominators, classifier.valid)
    classifier.mark_out_of_range(mean, positive=False)
    classifier.mark_below_normal(mean, nonzero=_signs(sums, count.shape) != 0)
    return classifier, mean


def aggregate_blocks(
    circular: CircularPowers | CovarianceCircularPowers,
    *,
    block_size: int | tuple[int, int],
) -> BlockAggregates:
    """Aggregate linear SC and OC per block first, then derive the ratios (PRD 6.2).

    ``circular`` is the linear-power record of :func:`derive_circular` (:class:`CircularPowers`
    or :class:`CovarianceCircularPowers`) on a two-dimensional pixel grid. Nothing else is
    accepted: amplitude DN, bare arrays, other stated arrays and dB values
    (:class:`DecibelArray`, display only) raise TypeError, so amplitude is never averaged as
    power and dB never enters an average (PRD 6.2, 7). ``block_size`` is the block size in
    pixels, an int for square blocks or ``(rows, cols)``, and is required. The PRD's common
    support of about 250 m applies only where the products' effective resolution allows, so the
    caller converts it to pixels; no size is built in. The grid must divide into whole blocks,
    otherwise ValueError: a partial block would have a different support, and no pixel is
    dropped silently.

    Per block, two separately named statistics (PRD 6.2):

    - **ratio of mean powers**, the primary radar statistic: mean SC and mean OC over the power
      support, the pixels whose SC and OC are both ``VALID``, and then their ratio. SC and OC
      are averaged over the same pixels, so the ratio is consistent. A pixel whose OC is a
      valid zero (F5), or negative within the conversion's tolerance, has no pixelwise CPR
      but still has valid powers, so its SC and OC enter both means; no epsilon or
      regularized value is formed for it. A valid SC negative within the conversion's
      tolerance is averaged as computed, never clipped.
      For covariance input the mean powers equal the powers of the mean covariance over the
      same pixels, because each power is a linear functional of the covariance, so this is
      aggregating the covariance first.
    - **mean of pixelwise ratios**: the mean of the ``VALID`` pixelwise CPR values. A pixel
      whose CPR is unavailable (OC_NONPOSITIVE, SC_NEGATIVE and the others) is excluded.

    Missing and invalid pixels are excluded from every mean and counted in ``power_support``
    and ``ratio_support`` (:class:`BlockSupport`). For the power support a pixel takes the
    state and reason of its SC, or of its OC where SC is valid.

    Rules for each statistic, in order (the first match wins):

    1. No usable pixel in the block: ``BLOCK_NO_VALID_PIXELS``, with state ``MISSING`` where
       every pixel of the block is missing and ``INVALID`` otherwise. No value, never 0.
    2. Means: a mean float64 cannot hold (above the largest float64, or nonzero below the
       smallest normal float64): ``INVALID``, ``FLOAT64_OUT_OF_RANGE``.

    and for the ratio of mean powers, after rule 1:

    3. Mean OC zero or negative: ``INVALID``, ``OC_NONPOSITIVE``. No epsilon is added (PRD 7).
    4. Mean SC negative: ``INVALID``, ``SC_NEGATIVE``.
    5. Mean SC or mean OC out of float64 range, or a ratio float64 cannot hold: ``INVALID``,
       ``FLOAT64_OUT_OF_RANGE``. A valid high ratio is never truncated.

    Each block's values are summed exactly, as integers counting ``2**-1074`` (every finite
    float64 is such a multiple). A sum therefore has no rounding error and no overflow, powers
    of opposite sign (accepted within the conversion's tolerance) cancel without losing their
    residual at any magnitude, and no statistic, state or reason depends on the order of the
    pixels. Rules 3 and 4 test the signs of the exact sums. Each statistic is then rounded once
    to float64: a mean is the exact sum over the count, and the ratio of mean powers is the
    exact SC sum over the exact OC sum. Blocks are not independent samples
    (``blocks_independent`` is False). Units: mean SC and OC in the linear power units of the
    input; both ratio statistics dimensionless.
    """
    if type(circular) not in (CircularPowers, CovarianceCircularPowers):
        raise TypeError(
            "aggregate_blocks takes the linear-power record of derive_circular (CircularPowers "
            f"or CovarianceCircularPowers), got {type(circular).__name__}: amplitude DN, bare "
            "arrays and dB values (DecibelArray, display only) are not linear powers"
        )
    rows, cols = _block_shape(block_size)
    shape = circular.sc.value.shape
    if len(shape) != 2:
        raise ValueError(f"aggregate_blocks needs a two-dimensional pixel grid, got {shape}")
    if shape[0] % rows or shape[1] % cols:
        raise ValueError(
            f"the pixel grid {shape} does not divide into whole blocks of {(rows, cols)} pixels; "
            "crop it to a multiple of the block size first"
        )
    n_pixels = rows * cols
    grid = (shape[0] // rows, shape[1] // cols)
    origin_rows, origin_cols = np.meshgrid(
        np.arange(grid[0], dtype=np.int64) * rows,
        np.arange(grid[1], dtype=np.int64) * cols,
        indexing="ij",
    )
    block_origin = np.stack([origin_rows, origin_cols], axis=-1)

    # Power support: a pixel is used where SC and OC are both VALID.
    sc_valid = _is_valid(circular.sc)
    pair_state = np.where(sc_valid, circular.oc.state, circular.sc.state)
    pair_reason = np.where(sc_valid, circular.oc.reason, circular.sc.reason)
    power_support = _support(
        _to_blocks(pair_state, rows, cols), _to_blocks(pair_reason, rows, cols), n_pixels
    )
    power_used = _to_blocks(np.equal(pair_state, ObsState.VALID), rows, cols)
    power_base = _no_valid_pixels(power_support)
    sc_sums = _exact_sums(_to_blocks(circular.sc.value, rows, cols), power_used)
    oc_sums = _exact_sums(_to_blocks(circular.oc.value, rows, cols), power_used)
    sc_classifier, mean_sc = _block_mean(power_base, sc_sums, power_support.n_valid)
    oc_classifier, mean_oc = _block_mean(power_base, oc_sums, power_support.n_valid)

    # Signs and zeros come from the exact sums, so no rounding can flip a decision.
    sc_sign = _signs(sc_sums, grid)
    oc_sign = _signs(oc_sums, grid)
    ratio_classifier = power_base.copy()
    ratio_classifier.mark(oc_sign <= 0, ObsState.INVALID, InvalidReason.OC_NONPOSITIVE)
    ratio_classifier.mark(sc_sign < 0, ObsState.INVALID, InvalidReason.SC_NEGATIVE)
    ratio_classifier.mark(
        ~(sc_classifier.valid & oc_classifier.valid),
        ObsState.INVALID,
        InvalidReason.FLOAT64_OUT_OF_RANGE,
    )
    # The count and the 2**-1074 unit cancel: the ratio is the exact SC sum over the exact OC sum.
    ratio = _rounded_quotients(sc_sums, oc_sums, ratio_classifier.valid)
    ratio_classifier.mark_out_of_range(ratio, positive=False)
    ratio_classifier.mark_below_normal(ratio, nonzero=sc_sign != 0)

    # Ratio support: a pixel is used where its CPR is VALID.
    ratio_support = _support(
        _to_blocks(circular.cpr.state, rows, cols),
        _to_blocks(circular.cpr.reason, rows, cols),
        n_pixels,
    )
    cpr_used = _to_blocks(_is_valid(circular.cpr), rows, cols)
    cpr_sums = _exact_sums(_to_blocks(circular.cpr.value, rows, cols), cpr_used)
    pixelwise_classifier, pixelwise = _block_mean(
        _no_valid_pixels(ratio_support), cpr_sums, ratio_support.n_valid
    )

    if type(circular) is CovarianceCircularPowers:
        input_kind: InputKind = COVARIANCE_INPUT
        handedness_verified: bool | None = circular.handedness_verified
    else:
        input_kind = STOKES_INPUT
        handedness_verified = None
    return BlockAggregates(
        block_size=(rows, cols),
        block_origin=block_origin,
        mean_sc=sc_classifier.finish(mean_sc),
        mean_oc=oc_classifier.finish(mean_oc),
        ratio_of_mean_powers=ratio_classifier.finish(ratio),
        mean_of_pixelwise_ratios=pixelwise_classifier.finish(pixelwise),
        power_support=power_support,
        ratio_support=ratio_support,
        input_kind=input_kind,
        convention=circular.convention,
        handedness_verified=handedness_verified,
    )


# --- Hybrid-polarimetric Stokes parameters and m-chi (PRD 6.2, D18) --------------------------

TRANSMIT_ELLIPTICITIES: Final[tuple[float, float]] = (45.0, -45.0)
"""The transmit ellipticity angles ``chi_tx`` that may be declared, in degrees (PRD 6.2).

The transmit Jones vector in the (H, V) basis of the covariance is ``t(chi) = (cos chi,
i sin chi)``, the Jones vector of a polarization ellipse of orientation 0 and ellipticity angle
``chi`` in the usual orientation-ellipticity parameterization; its own Stokes parameter
``-2 Im(t_H conj(t_V))`` is ``sin(2 chi)``. So ``chi_tx = -45`` is ``(1, -i)/sqrt2``, the SC_LL
transmit vector of the LL/LR convention of Neish et al. (:data:`NEISH_SC_LL_CONVENTION`), and
``chi_tx = +45`` is ``(1, +i)/sqrt2``, the opposite handedness (:data:`OPPOSITE_SC_CONVENTION`).
Which one a sensor or polsartools calls left or right circular is not decided here: the
polsartools ``simulate_CP`` docstring names +45 right circular and -45 left circular, and the
real-scene check (T2-07) has to confirm that its convention matches this one. No other
ellipticity is accepted, and none is assumed."""

CONTEXT_ROLE: Final = "context"
"""The role of the hybrid-polarimetric observables: context in Mode B, never a distance input
before G2 (PRD 6.2)."""

ObservableRole = Literal["context"]
"""Roles a hybrid-polarimetric record may carry; only :data:`CONTEXT_ROLE` before G2."""

_FROM_SQRT2_ORDERING: Final[npt.NDArray[np.float64]] = _read_only(
    np.array([[1.0, _SQRT_HALF, 1.0], [_SQRT_HALF, 0.5, _SQRT_HALF], [1.0, _SQRT_HALF, 1.0]])
)
"""Elementwise factors ``1 / (n_i n_j)`` with ``n = (1, sqrt2, 1)`` that express a covariance
in the (HH, sqrt2 HV, VV) ordering in the (HH, HV, VV) ordering. The middle factor is exactly
1/2."""


def _hybrid_forms(s: complex, sin_2chi_tx: float) -> dict[str, npt.NDArray[np.complex128]]:
    """Matrices ``K`` with ``Re sum(R * K)`` equal to each hybrid output, for a covariance ``R``
    in the (HH, HV, VV) ordering and the transmit ``t = (1, s)/sqrt2``.

    The received field is ``E = S t = A k`` with ``k = (HH, HV, VV)`` and
    ``A = [[t_H, t_V, 0], [0, t_H, t_V]] = B / sqrt2``, ``B = [[1, s, 0], [0, 1, s]]``. So the
    received 2x2 covariance is ``J = A R A^H = B R B^H / 2``, and ``J_ab = sum(R * K_ab)`` with
    ``K_ab = outer(B_a, conj(B_b)) / 2``. Each Stokes parameter is one such linear functional:
    ``K_S1 = K_HH + K_VV``, ``K_S2 = K_HH - K_VV``, ``K_S3 = 2 K_HV`` and
    ``K_S4 = 2i sin(2 chi_tx) K_HV``, which gives ``S4 = -2 sin(2 chi_tx) Im J_HV = OC - SC``
    relative to the transmit (the SIS convention). Every entry of ``B`` is 0, 1 or ``+-i``, so
    every entry of ``K`` is 0, ``+-1/2``, ``+-1``, ``+-i/2`` or ``+-i``, purely real or purely
    imaginary: each product with a part of ``R`` is exact, and a term that cancels in ``K``
    (such as the HV power in S2) is exactly zero instead of a difference of rounded powers.
    """
    rows = np.array([[1.0, s, 0.0], [0.0, 1.0, s]], dtype=np.complex128)
    k_hh = 0.5 * _quadratic_form(rows[0])
    k_vv = 0.5 * _quadratic_form(rows[1])
    k_hv = 0.5 * np.outer(rows[0], rows[1].conj())
    forms = {
        "j_hh": k_hh,
        "j_vv": k_vv,
        "j_hv_real": k_hv,
        "j_hv_imag": -1j * k_hv,
        "s1": k_hh + k_vv,
        "s2": k_hh - k_vv,
        "s3": 2.0 * k_hv,
        "s4": 2j * sin_2chi_tx * k_hv,
    }
    return {name: _read_only(form) for name, form in forms.items()}


_HYBRID_TRANSMIT: Final[
    dict[float, tuple[HandednessConvention, tuple[complex, complex], dict[str, Any]]]
] = {
    -45.0: (
        NEISH_SC_LL_CONVENTION,
        (complex(_SQRT_HALF, 0.0), complex(0.0, -_SQRT_HALF)),
        _hybrid_forms(-1j, -1.0),
    ),
    45.0: (
        OPPOSITE_SC_CONVENTION,
        (complex(_SQRT_HALF, 0.0), complex(0.0, _SQRT_HALF)),
        _hybrid_forms(1j, 1.0),
    ),
}
"""For each declared ``chi_tx``: its handedness convention, its transmit Jones vector and the
forms of :func:`_hybrid_forms`."""

_HYBRID_FIELDS: Final = ("j_hh", "j_vv", "j_hv_real", "j_hv_imag", "s1", "s2", "s3", "s4")


def _check_chi_tx(chi_tx: object) -> float:
    """Return a declared transmit ellipticity as a float, or raise ValueError.

    Only a real number (a Python int or float, or a numpy integer or floating scalar, not a
    bool or an array) equal to +45 or -45 degrees is declared. Units are not inferred: +-pi/4
    is refused, not read as radians."""
    real = type(chi_tx) in (int, float) or (
        _is_numpy_scalar(chi_tx) and isinstance(chi_tx, np.integer | np.floating)
    )
    if not real or float(cast(float, chi_tx)) not in TRANSMIT_ELLIPTICITIES:
        raise ValueError(
            f"chi_tx must be declared as one of {TRANSMIT_ELLIPTICITIES} degrees, got "
            f"{chi_tx!r}; an undeclared transmit ellipticity is refused, never guessed"
        )
    return float(cast(float, chi_tx))


def _check_role(role: object) -> None:
    if type(role) is not str or role != CONTEXT_ROLE:
        raise ValueError(
            f"role must be {CONTEXT_ROLE!r}: hybrid-polarimetric observables are context in "
            f"Mode B and enter no distance before G2 (PRD 6.2), got {role!r}"
        )


def _check_stated_fields(record: object, fields: tuple[str, ...]) -> None:
    for name in fields:
        if type(getattr(record, name)) is not StatedArray:
            raise TypeError(f"{name} must be a StatedArray")
    shapes = [getattr(record, name).value.shape for name in fields]
    if any(shape != shapes[0] for shape in shapes):
        raise ValueError(f"{', '.join(fields)} must have the same shape, got {shapes}")


@dataclass(frozen=True, slots=True)
class HybridStokes:
    """Hybrid-polarimetric 2x2 covariance and Stokes parameters synthesized from a UAVSAR
    covariance for a declared transmit ellipticity (PRD 6.2, D18).

    The received field is ``E = S t`` for the transmit Jones vector ``t`` of ``chi_tx``
    (:data:`TRANSMIT_ELLIPTICITIES`), with ``E = (E_H, E_V)`` in the (H, V) basis of the
    covariance. Each field is a :class:`StatedArray` over the pixel shape, in the linear power
    units of the covariance:

    - the 2x2 covariance ``J = <E E^H>``: ``j_hh = <|E_H|**2>``, ``j_vv = <|E_V|**2>`` and
      ``j_hv_real``, ``j_hv_imag``, the parts of ``J_HV = <E_H conj(E_V)>``
      (:meth:`covariance_matrix` assembles them);
    - the Stokes parameters ``s1 = J_HH + J_VV`` (received power), ``s2 = J_HH - J_VV``,
      ``s3 = 2 Re J_HV``, and ``s4 = OC - SC`` under the Mini-RF SIS convention
      (:data:`MINIRF_SIS_CONVENTION`), with OC and SC relative to the declared transmit:
      ``s4 = -2 sin(2 chi_tx) Im J_HV``. So ``s1 = SC + OC`` and
      :func:`circular_from_stokes` on ``s1`` and ``s4`` returns the SC and OC of
      :func:`circular_from_covariance` for ``handedness``.

    ``chi_tx`` is the declared transmit ellipticity in degrees, ``handedness`` the matching
    convention (:data:`NEISH_SC_LL_CONVENTION` for -45, :data:`OPPOSITE_SC_CONVENTION` for +45)
    and ``transmit_jones`` its Jones vector ``(t_H, t_V)``; ``stokes_convention``, ``ordering``
    and ``tolerance`` are the declarations used. ``handedness_verified`` is False: no Earth
    result is Mini-RF-equivalent until handedness and basis are verified on both sides
    (PRD 6.2); a caller that has verified both may record that with ``dataclasses.replace``.
    ``role`` is :data:`CONTEXT_ROLE`: these observables are context in Mode B and enter no
    distance before G2 (PRD 6.2).
    """

    j_hh: StatedArray
    j_vv: StatedArray
    j_hv_real: StatedArray
    j_hv_imag: StatedArray
    s1: StatedArray
    s2: StatedArray
    s3: StatedArray
    s4: StatedArray
    chi_tx: float
    handedness: HandednessConvention
    transmit_jones: tuple[complex, complex]
    stokes_convention: StokesConvention
    ordering: CovarianceOrdering
    tolerance: Tolerance
    handedness_verified: bool = False
    role: ObservableRole = CONTEXT_ROLE

    def __post_init__(self) -> None:
        _check_stated_fields(self, _HYBRID_FIELDS)
        chi_tx = _check_chi_tx(self.chi_tx)
        object.__setattr__(self, "chi_tx", chi_tx)
        handedness, transmit, _ = _HYBRID_TRANSMIT[chi_tx]
        if self.handedness != handedness:
            raise ValueError(
                f"handedness must be {handedness!r} for chi_tx {chi_tx}, got {self.handedness!r}"
            )
        if type(self.transmit_jones) is not tuple or self.transmit_jones != transmit:
            raise ValueError(
                f"transmit_jones must be {transmit} for chi_tx {chi_tx}, got "
                f"{self.transmit_jones!r}"
            )
        _check_stokes_convention(self.stokes_convention)
        _check_ordering(self.ordering)
        if type(self.tolerance) is not Tolerance:
            raise TypeError(f"tolerance must be a Tolerance, got {self.tolerance!r}")
        if type(self.handedness_verified) is not bool:
            raise TypeError(f"handedness_verified must be a bool, got {self.handedness_verified!r}")
        _check_role(self.role)

    def covariance_matrix(self) -> npt.NDArray[np.complex128]:
        """The synthesized 2x2 covariance ``J`` per pixel, shape ``(..., 2, 2)``, complex128.

        ``J = [[J_HH, J_HV], [conj(J_HV), J_VV]]``, exactly Hermitian. A pixel where any of
        the four parts is not ``VALID`` holds NaN in every real and imaginary part; its state
        and reason are in the part fields. The array is a new, writable copy.
        """
        shape = self.j_hh.value.shape
        valid = (
            _is_valid(self.j_hh)
            & _is_valid(self.j_vv)
            & _is_valid(self.j_hv_real)
            & _is_valid(self.j_hv_imag)
        )
        j_hv = np.empty(shape, dtype=np.complex128)
        j_hv.real = self.j_hv_real.value
        j_hv.imag = self.j_hv_imag.value
        out = np.empty((*shape, 2, 2), dtype=np.complex128)
        out[..., 0, 0] = self.j_hh.value
        out[..., 0, 1] = j_hv
        out[..., 1, 0] = np.conj(j_hv)
        out[..., 1, 1] = self.j_vv.value
        out[~valid] = complex(math.nan, math.nan)
        return out


def hybrid_stokes_from_covariance(
    covariance: npt.ArrayLike,
    *,
    ordering: CovarianceOrdering,
    chi_tx: float,
    product_masked: npt.ArrayLike,
    tolerance: Tolerance = ARITHMETIC_TOLERANCE,
) -> HybridStokes:
    """Synthesize the hybrid-polarimetric 2x2 covariance and Stokes parameters (PRD 6.2, D18).

    ``covariance`` is a UAVSAR 3x3 covariance per pixel, shape ``(..., 3, 3)``, in the declared
    ``ordering`` (:data:`COVARIANCE_HH_HV_VV` as UAVSAR stores it, or
    :data:`COVARIANCE_HH_SQRT2HV_VV`). ``chi_tx`` is the declared transmit ellipticity in
    degrees, +45 or -45 (:data:`TRANSMIT_ELLIPTICITIES`, which also states the Jones vector
    ``t(chi) = (cos chi, i sin chi)``); anything else, ``None`` included, raises ValueError and
    nothing is returned. ``ordering``, ``chi_tx`` and ``product_masked`` are required.

    The synthesis is an explicit transformation: the covariance is expressed in the
    (HH, HV, VV) ordering, ``R``, and the received field ``E = S t = A k`` with
    ``A = [[t_H, t_V, 0], [0, t_H, t_V]]`` gives ``J = A R A^H``. Each output is formed as one
    linear functional of ``R`` (``_hybrid_forms``), so S2, S3 and S4 keep the precision of the
    parts they depend on however large the powers are. With ``S4 = OC - SC`` relative to the
    transmit (:data:`MINIRF_SIS_CONVENTION`), ``S1 = SC + OC`` and ``S4`` reproduce the SC and
    OC of :func:`circular_from_covariance` for the matching handedness (SC_LL for -45, SC_opp
    for +45). The record states ``chi_tx``, the handedness and the Jones vector used, and has
    ``handedness_verified`` False and ``role`` :data:`CONTEXT_ROLE` (PRD 6.2). This module adds
    no cross-band comparison.

    The covariance first passes the shared rules 1-6 of :func:`circular_from_covariance`
    (product mask, non-finite parts, parts float64 cannot carry at one scale, Hermitian,
    negative diagonal, positive semidefinite), with ``tolerance`` as the Hermitian and PSD
    tolerance and the same states and reasons; nothing is repaired (PRD 7). Its Hermitian part
    is used, at one exact power-of-two scale per matrix. Each of the eight outputs is then
    ``INVALID`` with ``FLOAT64_OUT_OF_RANGE`` where its scaled-back value overflows, or is not
    zero at the matrix's scale but falls below the smallest normal float64. A zero at the
    matrix's scale stays a valid zero. The outputs carry the transformation's rounding error, of
    the order of float64 precision times the covariance's largest element.

    Units: all outputs in the linear power units of the covariance.
    """
    _check_ordering(ordering)
    chi = _check_chi_tx(chi_tx)
    if type(tolerance) is not Tolerance:
        raise TypeError(f"tolerance must be a Tolerance, got {tolerance!r}")
    base, scaled, hermitian, exponent = _checked_covariance(
        covariance, ordering=ordering, product_masked=product_masked, tolerance=tolerance
    )
    handedness, transmit, forms = _HYBRID_TRANSMIT[chi]

    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        if ordering == COVARIANCE_HH_HV_VV:
            # The declared (HH, HV, VV) matrix itself, so its parts reach the forms unrounded.
            raw = 0.5 * (scaled + np.conj(np.swapaxes(scaled, -1, -2)))
        else:
            raw = hermitian * _FROM_SQRT2_ORDERING
        at_scale = {
            name: np.asarray(np.sum(raw * forms[name], axis=(-2, -1)).real, dtype=np.float64)
            for name in _HYBRID_FIELDS
        }

    outputs: dict[str, StatedArray] = {}
    for name in _HYBRID_FIELDS:
        classifier, value = _unscaled_power(base, at_scale[name], exponent)
        outputs[name] = classifier.finish(value)
    return HybridStokes(
        j_hh=outputs["j_hh"],
        j_vv=outputs["j_vv"],
        j_hv_real=outputs["j_hv_real"],
        j_hv_imag=outputs["j_hv_imag"],
        s1=outputs["s1"],
        s2=outputs["s2"],
        s3=outputs["s3"],
        s4=outputs["s4"],
        chi_tx=chi,
        handedness=handedness,
        transmit_jones=transmit,
        stokes_convention=MINIRF_SIS_CONVENTION,
        ordering=ordering,
        tolerance=tolerance,
    )


_MCHI_FIELDS: Final = ("m", "sin_2chi", "chi")


@dataclass(frozen=True, slots=True)
class MChi:
    """Degree of polarization ``m`` and ellipticity ``chi`` from four Stokes parameters
    (PRD 6.2, D18).

    Each field is a :class:`StatedArray` over the pixel shape: ``m`` and ``sin_2chi``
    dimensionless, ``chi`` in degrees (:data:`INTERNAL_ANGLE_UNIT`), within [-45, 45]. ``chi``
    and ``sin_2chi`` have the same states and reasons. A ``VALID`` ``sin_2chi`` needs a
    ``VALID``, positive ``m``. ``convention`` is the Stokes convention of the inputs and
    ``tolerance`` the one the Stokes validity rules used. ``chi_tx`` and ``handedness`` are the
    declared transmit ellipticity and handedness of a synthesized hybrid record
    (:func:`m_chi_from_hybrid`), or both ``None`` for product Stokes parameters, whose label
    defines the handedness. ``role`` is :data:`CONTEXT_ROLE` (PRD 6.2). The m-chi decomposition
    into single-bounce, double-bounce and random parts is not part of the record (see
    :func:`m_chi`).
    """

    m: StatedArray
    sin_2chi: StatedArray
    chi: StatedArray
    convention: StokesConvention
    tolerance: Tolerance
    chi_tx: float | None = None
    handedness: HandednessConvention | None = None
    role: ObservableRole = CONTEXT_ROLE

    def __post_init__(self) -> None:
        _check_stated_fields(self, _MCHI_FIELDS)
        _check_stokes_convention(self.convention)
        if type(self.tolerance) is not Tolerance:
            raise TypeError(f"tolerance must be a Tolerance, got {self.tolerance!r}")
        if self.chi_tx is None:
            if self.handedness is not None:
                raise ValueError("handedness needs a declared chi_tx; both are None or both set")
        else:
            chi_tx = _check_chi_tx(self.chi_tx)
            object.__setattr__(self, "chi_tx", chi_tx)
            handedness = _HYBRID_TRANSMIT[chi_tx][0]
            if self.handedness != handedness:
                raise ValueError(
                    f"handedness must be {handedness!r} for chi_tx {chi_tx}, got "
                    f"{self.handedness!r}"
                )
        _check_role(self.role)
        with np.errstate(invalid="ignore"):
            m_positive = _is_valid(self.m) & (self.m.value > 0.0)
        if np.any(_is_valid(self.sin_2chi) & ~m_positive):
            raise ValueError("a VALID sin_2chi needs a VALID, positive m")
        if not (
            np.array_equal(self.chi.state, self.sin_2chi.state)
            and np.array_equal(self.chi.reason, self.sin_2chi.reason)
        ):
            raise ValueError("chi must have the same states and reasons as sin_2chi")


def _m_chi(
    base: _Classifier,
    stokes: tuple[
        npt.NDArray[np.float64],
        npt.NDArray[np.float64],
        npt.NDArray[np.float64],
        npt.NDArray[np.float64],
    ],
    tolerance: Tolerance,
) -> tuple[StatedArray, StatedArray, StatedArray]:
    """Rules 3-5 of :func:`m_chi` after the caller's rules in ``base``, then m, sin 2chi, chi."""
    valid = base.valid
    # Elements already marked hold zeros, so the arithmetic below stays quiet for them.
    s1, s2, s3, s4 = (np.where(valid, x, 0.0) for x in stokes)
    with np.errstate(over="ignore", under="ignore", invalid="ignore", divide="ignore"):
        base.mark(s1 <= 0.0, ObsState.INVALID, InvalidReason.STOKES_S1_NONPOSITIVE)
        base.mark(
            _beyond_squared_bound(s1, s2, s3, s4, tolerance),
            ObsState.INVALID,
            InvalidReason.STOKES_NONPHYSICAL,
        )
        allowed = tolerance.atol + tolerance.rtol * np.abs(s1)
        base.mark(np.abs(s4) - s1 > allowed, ObsState.INVALID, InvalidReason.STOKES_S1_LT_ABS_S4)

        # The polarized norm at its own power-of-two scale, so no square overflows and the
        # largest polarized part keeps a normal square however small m is.
        largest = np.maximum(np.maximum(np.abs(s2), np.abs(s3)), np.abs(s4))
        _, polarized_exponent = np.frexp(largest)
        q2, q3, q4 = (np.ldexp(x, -polarized_exponent) for x in (s2, s3, s4))
        norm = np.sqrt(q2 * q2 + q3 * q3 + q4 * q4)
        s1_mantissa, s1_exponent = np.frexp(s1)
        usable = base.valid
        ratio = np.zeros(s1.shape)
        np.divide(norm, s1_mantissa, out=ratio, where=usable)
        m = np.asarray(np.ldexp(ratio, polarized_exponent - s1_exponent), dtype=np.float64)
        polarized = np.asarray((s2 != 0.0) | (s3 != 0.0) | (s4 != 0.0))

        m_classifier = base.copy()
        m_classifier.mark_out_of_range(m, positive=False)
        m_classifier.mark_below_normal(m, nonzero=polarized)

        sin_classifier = base.copy()
        sin_classifier.mark(~polarized, ObsState.INVALID, InvalidReason.M_ZERO)
        sin_classifier.mark(
            ~m_classifier.valid, ObsState.INVALID, InvalidReason.FLOAT64_OUT_OF_RANGE
        )
        sin_2chi = np.full(s1.shape, np.nan)
        np.divide(-q4, norm, out=sin_2chi, where=sin_classifier.valid)
        chi = 0.5 * np.degrees(np.arcsin(sin_2chi))
    chi_classifier = sin_classifier.copy()
    return (
        m_classifier.finish(m),
        sin_classifier.finish(sin_2chi),
        chi_classifier.finish(chi),
    )


def m_chi(
    s1: npt.ArrayLike,
    s2: npt.ArrayLike,
    s3: npt.ArrayLike,
    s4: npt.ArrayLike,
    *,
    convention: StokesConvention,
    product_masked: npt.ArrayLike,
    tolerance: Tolerance = ARITHMETIC_TOLERANCE,
) -> MChi:
    """Degree of polarization m and ellipticity chi from all four Stokes parameters (PRD 6.2).

    PRD 6.2, where all four Stokes parameters are available::

        m          = sqrt(S2**2 + S3**2 + S4**2) / S1    # degree of polarization
        sin(2 chi) = -S4 / (m * S1)                     # sign depends on transmit handedness

    ``s1`` to ``s4`` are real arrays of one shape in the product's linear power units, under the
    declared Stokes ``convention``; only :data:`MINIRF_SIS_CONVENTION` (``S4 = OC - SC``) is
    declared, and anything else, ``None`` included, raises ValueError. Under it S4 is relative
    to the transmit, so ``chi < 0`` for an OC-dominant (odd-bounce) return and ``chi > 0`` for
    an SC-dominant (even-bounce) one, whichever handedness was transmitted. ``product_masked``
    is a boolean array that broadcasts to the shape, True where the product's validity or noise
    flag, or a configured SNR rule, excludes the pixel (PRD 7). ``tolerance`` is the Stokes
    validity tolerance, by default :data:`ARITHMETIC_TOLERANCE`; a real-data value is open.

    The m-chi decomposition into single-bounce, double-bounce and randomly polarized parts
    (Raney et al. 2012) is not implemented: PRD 6.2 names it but gives no formula, and the
    formulas have not been read from the cited source, so only m and chi are returned and the
    decomposition is deferred.

    Rules shared by every output, in order (the first match wins):

    1. ``product_masked``: ``MISSING``, ``PRODUCT_MASKED``.
    2. Any Stokes parameter is NaN or infinite: ``INVALID``, ``NONFINITE``.
    3. ``S1 <= 0``: no received power to normalize by: ``INVALID``, ``STOKES_S1_NONPOSITIVE``.
    4. ``S2**2 + S3**2 + S4**2 - S1**2 > atol + rtol * S1**2``, that is m > 1 beyond the
       tolerance applied to the squared inequality (PRD 7): ``INVALID``,
       ``STOKES_NONPHYSICAL``. ``atol`` is in squared power units here, as in
       :func:`circular_from_stokes`.
    5. ``|S4| - S1 > atol + rtol * |S1|``, S1 >= |S4| failing beyond the tolerance (PRD 7):
       ``INVALID``, ``STOKES_S1_LT_ABS_S4``. Rule 4 comes first, so every m > 1 beyond the
       squared bound is ``STOKES_NONPHYSICAL``; this rule decides only where the linear bound
       is the stricter one, near S1 = 0.

    Then ``m``: an m above 1 within the tolerance is reported as computed, never clipped. Where
    S2 = S3 = S4 = 0 exactly, m is a valid 0. ``INVALID`` with ``FLOAT64_OUT_OF_RANGE`` where m
    overflows, or is not zero but below the smallest normal float64.

    Then ``sin_2chi`` and ``chi`` (``chi = asin(sin 2chi) / 2`` in degrees):

    6. m = 0 (S2 = S3 = S4 = 0 exactly): the ellipticity is undefined: ``INVALID``,
       ``M_ZERO``. No epsilon is added and no 0 is published.
    7. m out of float64 range: ``INVALID``, ``FLOAT64_OUT_OF_RANGE``.

    Each pixel is computed at exact power-of-two scales: the polarized norm
    ``sqrt(S2**2 + S3**2 + S4**2)`` at the scale of the largest of S2, S3 and S4, and S1 at its
    own. So no square overflows or underflows, and ``sin(2 chi)`` is formed as ``-S4`` over that
    norm, which equals ``-S4 / (m S1)`` and lies within [-1, 1] after rounding.
    """
    _check_stokes_convention(convention)
    if type(tolerance) is not Tolerance:
        raise TypeError(f"tolerance must be a Tolerance, got {tolerance!r}")
    s1_values = _stokes_array("s1", s1, None)
    shape = s1_values.shape
    others = tuple(
        _stokes_array(name, x, shape) for name, x in (("s2", s2), ("s3", s3), ("s4", s4))
    )
    masked = _bool_mask("product_masked", product_masked, shape)
    base = _Classifier(shape)
    base.mark(masked, ObsState.MISSING, InvalidReason.PRODUCT_MASKED)
    finite = np.isfinite(s1_values) & np.isfinite(others[0])
    finite &= np.isfinite(others[1]) & np.isfinite(others[2])
    base.mark(~finite, ObsState.INVALID, InvalidReason.NONFINITE)
    m, sin_2chi, chi = _m_chi(base, (s1_values, others[0], others[1], others[2]), tolerance)
    return MChi(m=m, sin_2chi=sin_2chi, chi=chi, convention=convention, tolerance=tolerance)


def m_chi_from_hybrid(hybrid: HybridStokes, *, tolerance: Tolerance = ARITHMETIC_TOLERANCE) -> MChi:
    """m and chi of synthesized hybrid-polarimetric Stokes parameters (PRD 6.2, D18).

    Runs the rules of :func:`m_chi` on ``hybrid``'s S1 to S4, which are in the SIS convention
    relative to the declared transmit. A pixel where any of them is not ``VALID`` keeps the
    state and reason of the first such parameter (S1, S2, S3, S4), for example
    ``PRODUCT_MASKED`` or ``COV_NOT_PSD``, in every output. The record carries ``hybrid``'s
    ``chi_tx`` and handedness. ``tolerance`` is the Stokes validity tolerance of rules 3-5.
    """
    if type(hybrid) is not HybridStokes:
        raise TypeError(f"m_chi_from_hybrid takes a HybridStokes, got {type(hybrid).__name__}")
    if type(tolerance) is not Tolerance:
        raise TypeError(f"tolerance must be a Tolerance, got {tolerance!r}")
    parts = (hybrid.s1, hybrid.s2, hybrid.s3, hybrid.s4)
    base = _Classifier(hybrid.s1.value.shape)
    for part in parts:
        inherited = ~_is_valid(part) & base.valid
        base.state[inherited] = part.state[inherited]
        base.reason[inherited] = part.reason[inherited]
    m, sin_2chi, chi = _m_chi(
        base, (parts[0].value, parts[1].value, parts[2].value, parts[3].value), tolerance
    )
    return MChi(
        m=m,
        sin_2chi=sin_2chi,
        chi=chi,
        convention=hybrid.stokes_convention,
        tolerance=tolerance,
        chi_tx=hybrid.chi_tx,
        handedness=hybrid.handedness,
    )


# --- Lunar S4-sign check (PRD 6.2) -----------------------------------------------------------


class SignCheckVerdict(StrEnum):
    """Outcome of :func:`lunar_s4_sign_check`. Only ``PASS`` lets a comparison proceed."""

    PASS = "PASS"
    """More than half of the used pixels have S4 > 0, as ordinary lunar terrain with CPR below
    1 has under the SIS convention."""

    SIGN_SUSPECT = "SIGN_SUSPECT"
    """More than half of the used pixels have CPR above 1: the product may use another S4 sign.
    Stop and resolve before any comparison (PRD 6.2). The sign is never flipped here."""

    INCONCLUSIVE = "INCONCLUSIVE"
    """Neither more than half with S4 > 0 nor more than half with CPR above 1. Not a pass."""

    NO_VALID_PIXELS = "NO_VALID_PIXELS"
    """No pixel has a valid CPR, so there is nothing to check. Not a pass."""


def _sign_verdict(n_used: int, n_cpr_above_1: int, n_s4_positive: int) -> SignCheckVerdict:
    """ "Most" is more than half (PRD 6.2), decided on integer counts."""
    if n_used == 0:
        return SignCheckVerdict.NO_VALID_PIXELS
    if 2 * n_cpr_above_1 > n_used:
        return SignCheckVerdict.SIGN_SUSPECT
    if 2 * n_s4_positive > n_used:
        return SignCheckVerdict.PASS
    return SignCheckVerdict.INCONCLUSIVE


def _check_count(name: str, value: object) -> int:
    if type(value) is not int or value < 0:
        raise TypeError(f"{name} must be a non-negative int, got {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class LunarSignCheck:
    """Result of :func:`lunar_s4_sign_check`: counts, fractions and a verdict, no data.

    ``n_pixels`` is the number of pixels checked and ``n_used`` the number with a ``VALID`` CPR
    under the SIS convention; the others are counted by reason in ``n_excluded_by_reason``
    (read-only). ``n_cpr_above_1`` and ``n_s4_positive`` count used pixels with CPR > 1 and
    with S4 > 0. ``fraction_cpr_above_1`` and ``fraction_s4_positive`` are those counts over
    ``n_used``, or ``None`` when no pixel is used. ``verdict`` follows from the counts and is
    checked against them, so a record cannot claim ``PASS`` its counts do not support. The
    record holds no Stokes values: the helper never returns a sign-corrected S4.
    """

    verdict: SignCheckVerdict
    n_pixels: int
    n_used: int
    n_cpr_above_1: int
    n_s4_positive: int
    fraction_cpr_above_1: float | None
    fraction_s4_positive: float | None
    n_excluded_by_reason: Mapping[str, int]
    convention: StokesConvention
    tolerance: Tolerance

    def __post_init__(self) -> None:
        if type(self.verdict) is not SignCheckVerdict:
            raise TypeError(f"verdict must be a SignCheckVerdict, got {self.verdict!r}")
        for name in ("n_pixels", "n_used", "n_cpr_above_1", "n_s4_positive"):
            _check_count(name, getattr(self, name))
        counts = dict(self.n_excluded_by_reason)
        for reason, count in counts.items():
            if type(reason) is not str or reason not in _REASON_CODES or reason == "NONE":
                raise ValueError(f"n_excluded_by_reason has an unknown reason {reason!r}")
            _check_count(f"n_excluded_by_reason[{reason!r}]", count)
        object.__setattr__(self, "n_excluded_by_reason", MappingProxyType(counts))
        if self.n_used + sum(counts.values()) != self.n_pixels:
            raise ValueError("n_used and the excluded counts must add up to n_pixels")
        if self.n_cpr_above_1 + self.n_s4_positive > self.n_used:
            raise ValueError("a used pixel cannot have both CPR above 1 and S4 > 0")
        expected = (
            (None, None)
            if self.n_used == 0
            else (self.n_cpr_above_1 / self.n_used, self.n_s4_positive / self.n_used)
        )
        if (self.fraction_cpr_above_1, self.fraction_s4_positive) != expected:
            raise ValueError("the fractions must be the counts over n_used, or None without any")
        verdict = _sign_verdict(self.n_used, self.n_cpr_above_1, self.n_s4_positive)
        if self.verdict != verdict:
            raise ValueError(f"the counts give the verdict {verdict}, not {self.verdict}")
        _check_stokes_convention(self.convention)
        if type(self.tolerance) is not Tolerance:
            raise TypeError(f"tolerance must be a Tolerance, got {self.tolerance!r}")


def lunar_s4_sign_check(
    s1: npt.ArrayLike,
    s4: npt.ArrayLike,
    valid_mask: npt.ArrayLike,
    *,
    tolerance: Tolerance = ARITHMETIC_TOLERANCE,
) -> LunarSignCheck:
    """Check that lunar Stokes data follow the SIS S4 sign (PRD 6.2, lunar sign check, T1).

    Under the SIS convention ``S4 = OC - SC`` (:data:`MINIRF_SIS_CONVENTION`), ordinary lunar
    terrain with CPR below 1 has S4 > 0. If most pixels come out with CPR above 1, the selected
    product uses a different S4 sign: stop and resolve before any comparison (PRD 6.2).

    ``s1`` and ``s4`` are real arrays of one shape; ``valid_mask`` is a boolean array that
    broadcasts to it, True where the product marks the pixel valid (the polarity of
    :func:`derive_circular`'s ``validity_mask``); any other dtype raises TypeError. CPR comes
    from :func:`circular_from_stokes` under the SIS convention with ``tolerance``, so a masked
    pixel, a non-finite one, one with S1 < |S4| beyond the tolerance, or one without a valid CPR
    (OC <= 0, for example) is not used and is counted under its reason. Over the used pixels
    the result gives the fraction with CPR above 1 and the fraction with S4 > 0 (S4 as given).

    Verdict, with "most" meaning more than half (PRD 6.2) and no other threshold:
    ``SIGN_SUSPECT`` where more than half of the used pixels have CPR above 1; else ``PASS``
    where more than half have S4 > 0; else ``INCONCLUSIVE``; ``NO_VALID_PIXELS`` where no pixel
    is used. Only ``PASS`` lets a comparison proceed. The helper never flips the sign or returns
    corrected data: it reports what the product gives, and resolving a suspect sign is a
    decision for whoever owns the product convention.
    """
    _require_plain("valid_mask", valid_mask)
    flags = np.asarray(valid_mask)
    if flags.dtype != np.bool_:
        raise TypeError(
            f"valid_mask must be a boolean array, True where the product marks a pixel valid, "
            f"got {flags.dtype}"
        )
    powers = circular_from_stokes(
        s1,
        s4,
        s2=None,
        s3=None,
        convention=MINIRF_SIS_CONVENTION,
        product_masked=~flags,
        tolerance=tolerance,
    )
    s4_values = _stokes_array("s4", s4, powers.cpr.value.shape)
    used = _is_valid(powers.cpr)
    with np.errstate(invalid="ignore"):
        n_cpr_above_1 = int(np.count_nonzero(used & (powers.cpr.value > 1.0)))
        n_s4_positive = int(np.count_nonzero(used & (s4_values > 0.0)))
    n_used = int(np.count_nonzero(used))
    reasons, counts = np.unique(powers.cpr.reason[~used], return_counts=True)
    excluded = {str(reason): int(count) for reason, count in zip(reasons, counts, strict=True)}
    return LunarSignCheck(
        verdict=_sign_verdict(n_used, n_cpr_above_1, n_s4_positive),
        n_pixels=int(used.size),
        n_used=n_used,
        n_cpr_above_1=n_cpr_above_1,
        n_s4_positive=n_s4_positive,
        fraction_cpr_above_1=None if n_used == 0 else n_cpr_above_1 / n_used,
        fraction_s4_positive=None if n_used == 0 else n_s4_positive / n_used,
        n_excluded_by_reason=excluded,
        convention=MINIRF_SIS_CONVENTION,
        tolerance=tolerance,
    )


# --- G1 gate record (PRD 9 G1, 8.12 GateRecord, 7 Fixtures) -----------------------------------
#
# ``python -m terraluna_pipeline.polarimetry record-g1 --junit <path> --out <path>`` reads the
# junit XML report that pytest writes itself (``uv run pytest --junitxml=<path>``) and writes
# one GateRecord for G1. Nothing here runs a test or decides a result: the status follows only
# from the outcomes in the report and the list of required tests. Status rules, first match:
#
# 1. FAIL: a required test failed or errored in any of its cases (a strict XPASS is a failure
#    in pytest's report), or any other test in a module that holds a required test failed,
#    collection errors of that module included.
# 2. NOT_RUN: no required test passed in full; every one is absent, skipped or incomplete.
# 3. PARTIAL: some required tests passed, none failed, but at least one is absent, skipped (an
#    xfail is reported as skipped) or incomplete (one of its listed cases is absent).
# 4. PASS: every required test passed in every one of its listed cases.
#
# A parametrized function's cases are listed in ``parametrized_cases``; every other function
# is expected as one testcase without parameters. A duplicate of one case never stands in for
# another, and cases beyond the listed ones decide nothing unless they fail.
#
# The real-scene polsartools check of T2-07 is a required test, so G1 is not PASS before it has
# passed on a real scene (PRD 12 adds it to T2); the scope states whether it was included.

_REPO_ROOT = Path(__file__).resolve().parents[3]
"""The repository root: this file is ``pipeline/src/terraluna_pipeline/polarimetry.py``."""

_G1_FIXTURES_DIR: Final = "fixtures/polarimetry"
_G1_REQUIRED_TESTS: Final = "fixtures/polarimetry/g1_required_tests.json"
_G1_RECORD: Final = "fixtures/polarimetry/g1-record.json"
_CONFIG: Final = "config/run.yaml"
_G1_JUNIT_PRODUCT: Final = "g1-junit-report"

G1_CONFIG_PATH: Final = _REPO_ROOT / _CONFIG
"""config/run.yaml, hashed into the record's ``config_version`` and ``run_id`` (PRD 8.13)."""

G1_REQUIRED_TESTS_PATH: Final = _REPO_ROOT / _G1_REQUIRED_TESTS
"""The tests G1 requires, as function-level pytest node ids (read by :func:`record_g1`)."""

G1_FAILURE_ACTION: Final = "No quantitative CPR output until fixed; terrain work continues"
"""PRD 9's failure action for G1: the record's consequence for every status but PASS."""

_G1_FIXTURE_IDS: Final = tuple(f"F{n}" for n in range(1, 11))
_G1_STORIES: Final = ("T2-01", "T2-02", "T2-03")
_G1_COUNT_KEYS: Final = ("tests", "passed", "failed", "skipped", "incomplete", "absent", "cases")

_RequiredTestId = Annotated[
    str, Field(pattern=r"^pipeline/tests/test_polarimetry[a-z0-9_]*\.py::test_[A-Za-z0-9_]+$")
]
_RequiredTestList = Annotated[tuple[_RequiredTestId, ...], Field(min_length=1)]
_CaseId = Annotated[str, Field(pattern=r"^[^\r\n]*$")]
_CaseList = Annotated[tuple[_CaseId, ...], Field(min_length=1)]


class G1InputError(ValueError):
    """An input to :func:`record_g1` that cannot be read or trusted; no record is written."""


class G1RequiredTests(BaseModel):
    """``fixtures/polarimetry/g1_required_tests.json``: the tests G1 requires.

    Every id is a test function in ``pipeline/tests/test_polarimetry*.py``, written as a pytest
    node id without parameters, so every parametrized case of it counts. ``fixtures`` maps F1
    to F10 (PRD 7) to the tests named ``test_F<n>_`` or ``test_F<a>_to_F<b>_``; a test covering
    several fixtures is listed under each. ``conventions``, ``invalid_data`` and ``other`` hold,
    per story T2-01 to T2-03, the tests that conventions are declared and labelled, the
    invalid-data tests (PRD 9 G1: "conventions labelled; invalid-data behaviour tested") and
    every other test of the story's test file, so that every test of the three stories is
    required.
    ``real_scene_check`` is the T2-07 oracle check on a real UAVSAR scene.
    ``parametrized_cases`` maps each listed function that pytest parametrizes to its case ids
    (the text between the brackets of a node id, possibly empty), so that every case counts;
    every other listed function is one testcase without parameters.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, regex_engine="rust-regex")

    description: Annotated[str, Field(min_length=1)]
    fixtures: dict[str, _RequiredTestList]
    conventions: dict[str, _RequiredTestList]
    invalid_data: dict[str, _RequiredTestList]
    other: dict[str, _RequiredTestList]
    real_scene_check: _RequiredTestList
    parametrized_cases: dict[_RequiredTestId, _CaseList]

    @model_validator(mode="after")
    def _check_groups(self) -> Self:
        if set(self.fixtures) != set(_G1_FIXTURE_IDS):
            raise ValueError(f"fixtures must name exactly {', '.join(_G1_FIXTURE_IDS)}")
        for name, group in self.story_groups().items():
            if set(group) != set(_G1_STORIES):
                raise ValueError(f"{name} must name exactly {', '.join(_G1_STORIES)}")
        groups = [ids for group in self.story_groups().values() for ids in group.values()]
        groups += self.fixtures.values()
        for ids in [*groups, self.real_scene_check]:
            if len(set(ids)) != len(ids):
                raise ValueError(f"a test is listed twice in one group: {ids}")
        listed = set(self.all_ids())
        for nodeid, cases in self.parametrized_cases.items():
            if nodeid not in listed:
                raise ValueError(f"parametrized_cases names a test that is not listed: {nodeid}")
            if len(set(cases)) != len(cases):
                raise ValueError(f"a case of {nodeid} is listed twice")
            if any("::" in case for case in cases):
                raise ValueError(f"a case of {nodeid} holds '::', which pytest's junit splits")
        return self

    def story_groups(self) -> dict[str, dict[str, tuple[str, ...]]]:
        """The per-story groups by name: conventions, invalid_data and other."""
        return {
            "conventions": self.conventions,
            "invalid_data": self.invalid_data,
            "other": self.other,
        }

    def all_ids(self) -> tuple[str, ...]:
        """Every required test once, in list order."""
        ids = [nodeid for group in self.fixtures.values() for nodeid in group]
        for group in self.story_groups().values():
            ids += [nodeid for story in group.values() for nodeid in story]
        return tuple(dict.fromkeys([*ids, *self.real_scene_check]))

    def junit_names(self, nodeid: str) -> tuple[str, ...]:
        """The junit testcase names a listed function must have: one per listed case."""
        function = nodeid.partition("::")[2]
        cases = self.parametrized_cases.get(nodeid)
        return (function,) if cases is None else tuple(f"{function}[{case}]" for case in cases)


def _read_input(path: Path, what: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise G1InputError(f"cannot read the {what} {path}: {error.strerror or error}") from error


def _parse_required(data: bytes, path: Path) -> G1RequiredTests:
    try:
        return G1RequiredTests.model_validate_json(data)
    except ValidationError as error:
        raise G1InputError(f"the required test list {path} is malformed: {error}") from error


def load_g1_required_tests(
    path: str | os.PathLike[str] = G1_REQUIRED_TESTS_PATH,
) -> G1RequiredTests:
    """Read and check the list of tests G1 requires; raise :class:`G1InputError` otherwise."""
    required_path = Path(path)
    return _parse_required(_read_input(required_path, "required test list"), required_path)


_JunitOutcome = Literal["passed", "failed", "skipped"]
_G1Outcome = Literal["passed", "failed", "skipped", "incomplete", "absent"]


@dataclass(frozen=True)
class _JunitCase:
    """One ``<testcase>`` element of a pytest junit report."""

    classname: str
    name: str
    outcome: _JunitOutcome

    def label(self) -> str:
        return f"{self.classname}::{self.name}" if self.classname else self.name


def _junit_count(suite: ET.Element, attribute: str, path: Path) -> int:
    value = suite.get(attribute, "0")
    if not (value.isascii() and value.isdigit()):
        raise G1InputError(f"{path}: testsuite attribute {attribute}={value!r} is not a count")
    return int(value)


def _parse_junit(data: bytes, path: Path) -> tuple[list[_JunitCase], list[str]]:
    """The testcases and suite timestamps of a pytest junit report.

    The layout is pytest's (``<testsuites>`` holding ``<testsuite>`` elements, or one
    ``<testsuite>``). A testcase with a ``failure`` or ``error`` child failed, one with only a
    ``skipped`` child (skips and xfails) was skipped, and one without either passed. A suite
    that counts failures or errors but holds no failing testcase is refused as inconsistent,
    since pytest writes a failing testcase for each. A DOCTYPE or entity declaration is refused
    before parsing: pytest never writes one.
    """
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise G1InputError(f"{path} declares a DOCTYPE or entities, which pytest never writes")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise G1InputError(f"{path} is not well-formed XML: {error}") from error
    if root.tag == "testsuites":
        suites = list(root)
    elif root.tag == "testsuite":
        suites = [root]
    else:
        raise G1InputError(f"{path} is not a pytest junit report (root element <{root.tag}>)")
    cases: list[_JunitCase] = []
    timestamps: list[str] = []
    for suite in suites:
        if suite.tag != "testsuite":
            raise G1InputError(f"{path}: unexpected <{suite.tag}> in <testsuites>")
        reported = _junit_count(suite, "failures", path) + _junit_count(suite, "errors", path)
        failing = 0
        for element in suite:
            if element.tag == "properties":
                continue
            if element.tag != "testcase":
                raise G1InputError(f"{path}: unexpected <{element.tag}> in <testsuite>")
            classname = element.get("classname")
            name = element.get("name")
            if classname is None or not name:
                raise G1InputError(f"{path}: a testcase lacks its classname or name")
            tags = {child.tag for child in element}
            outcome: _JunitOutcome
            if tags & {"failure", "error"}:
                outcome = "failed"
                failing += 1
            elif "skipped" in tags:
                outcome = "skipped"
            else:
                outcome = "passed"
            cases.append(_JunitCase(classname, name, outcome))
        if reported and not failing:
            raise G1InputError(
                f"{path} is inconsistent: a testsuite counts {reported} failures or errors"
                " but holds no failing testcase"
            )
        timestamp = suite.get("timestamp")
        if timestamp:
            timestamps.append(timestamp)
    return cases, timestamps


def _junit_address(nodeid: str) -> tuple[str, str]:
    """The junit classname (dotted module) and test name pytest writes for a function id."""
    path, _, function = nodeid.partition("::")
    return path.removesuffix(".py").replace("/", "."), function


def _required_outcomes(
    required: G1RequiredTests, cases: list[_JunitCase]
) -> tuple[dict[str, tuple[_G1Outcome, int]], list[str], list[str]]:
    """Each required test's outcome and number of cases, the listed cases absent from the
    tests that were reported, and other failures in the modules of the required tests.

    A test is absent when none of its cases is reported, failed when any reported case
    failed, skipped when any was skipped, incomplete when a listed case is absent, and passed
    otherwise.
    """
    by_class: dict[str, list[int]] = {}
    for index, case in enumerate(cases):
        by_class.setdefault(case.classname, []).append(index)
    outcomes: dict[str, tuple[_G1Outcome, int]] = {}
    absent_cases: list[str] = []
    matched: set[int] = set()
    for nodeid in required.all_ids():
        module, function = _junit_address(nodeid)
        own = [
            index
            for index in by_class.get(module, [])
            if cases[index].name == function or cases[index].name.startswith(function + "[")
        ]
        matched.update(own)
        found = {cases[index].outcome for index in own}
        reported = {cases[index].name for index in own}
        missing = [name for name in required.junit_names(nodeid) if name not in reported]
        outcome: _G1Outcome
        if not own:
            outcome = "absent"
        elif "failed" in found:
            outcome = "failed"
        elif "skipped" in found:
            outcome = "skipped"
        elif missing:
            outcome = "incomplete"
        else:
            outcome = "passed"
        outcomes[nodeid] = (outcome, len(own))
        if own:
            path = nodeid.partition("::")[0]
            absent_cases += [f"{path}::{name}" for name in missing]
    modules = {_junit_address(nodeid)[0] for nodeid in required.all_ids()}
    other_failed = sorted(
        case.label()
        for index, case in enumerate(cases)
        if case.outcome == "failed"
        and index not in matched
        and (
            case.classname in modules
            or any(case.classname.startswith(module + ".") for module in modules)
            or (case.classname == "" and case.name in modules)
        )
    )
    return outcomes, absent_cases, other_failed


def _g1_counts(
    ids: tuple[str, ...], outcomes: Mapping[str, tuple[_G1Outcome, int]]
) -> dict[str, int]:
    counts = dict.fromkeys(_G1_COUNT_KEYS, 0)
    counts["tests"] = len(ids)
    for nodeid in ids:
        outcome, n_cases = outcomes[nodeid]
        counts[outcome] += 1
        counts["cases"] += n_cases
    return counts


def _g1_status(
    outcomes: Mapping[str, tuple[_G1Outcome, int]], other_failed: list[str]
) -> GateStatus:
    found = [outcome for outcome, _ in outcomes.values()]
    if other_failed or "failed" in found:
        return "FAIL"
    if "passed" not in found:
        return "NOT_RUN"
    if any(outcome != "passed" for outcome in found):
        return "PARTIAL"
    return "PASS"


def _real_scene_phrase(counts: Mapping[str, int]) -> str:
    n = counts["tests"]
    if counts["failed"]:
        return "run and failed"
    if counts["passed"] == n:
        return "included and passed"
    parts = []
    if counts["absent"]:
        parts.append(f"{counts['absent']} of {n} absent from the junit report")
    if counts["skipped"]:
        parts.append(f"{counts['skipped']} of {n} skipped")
    if counts["incomplete"]:
        parts.append(f"{counts['incomplete']} of {n} missing a listed case")
    return f"not included ({', '.join(parts)})"


def _g1_scope(real_scene: Mapping[str, int]) -> str:
    return (
        "G1 conversion correctness (PRD 9): fixtures F1-F10 against an independent basis"
        " transformation, and every test of T2-01 to T2-03 (the convention tests, the invalid-data"
        f" tests and the others), in every listed case, as listed in {_G1_REQUIRED_TESTS} and read"
        " from a pytest junit report. The real-scene polsartools oracle check (T2-07, PRD 12):"
        f" {_real_scene_phrase(real_scene)}."
    )


def _g1_evidence_path(junit: Path) -> str:
    """The junit path for the notes: relative inside the repository, absolute outside it.

    Inside the repository the report must lie under ``fixtures/polarimetry/``, the only place
    there that this gate's evidence may be kept.
    """
    resolved = junit.resolve()
    root = _REPO_ROOT.resolve()
    if resolved.is_relative_to(root):
        if not resolved.is_relative_to(root / _G1_FIXTURES_DIR):
            raise G1InputError(
                f"the junit report {junit} is inside the repository but not under"
                f" {_G1_FIXTURES_DIR}/; write it outside the repository or under"
                f" {_G1_FIXTURES_DIR}/"
            )
        return resolved.relative_to(root).as_posix()
    return os.path.abspath(junit)


_GIT_SHA: Final = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_JUNIT_OPTIONS: Final = ("--junitxml", "--junit-xml")


def _git_state(root: Path) -> tuple[str | None, list[str] | None]:
    """The commit checked out at ``root`` and the paths that differ from it.

    Both are None when ``root`` is not the top of a git work tree, has no commit, or git cannot
    be run: the record then names no commit rather than guess one. The paths, relative to
    ``root`` and sorted, are every file whose index or work tree differs from the commit and
    every untracked file git does not ignore. git only reads (``--no-optional-locks``) and runs
    without the caller's ``GIT_*`` variables, so it reads the repository at ``root``.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    def git(*args: str) -> bytes | None:
        try:
            completed = subprocess.run(
                ["git", "--no-optional-locks", "-C", os.fspath(root), *args],
                capture_output=True,
                env=env,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout if completed.returncode == 0 else None

    top = git("rev-parse", "--show-toplevel")
    if top is None or Path(os.fsdecode(top.rstrip(b"\n"))).resolve() != root.resolve():
        return None, None
    head = git("rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    if head is None or status is None:
        return None, None
    commit = head.decode("ascii", "replace").strip()
    if not _GIT_SHA.fullmatch(commit):
        return None, None
    # Porcelain v1 with -z and no renames: one "XY <path>" entry per path, each ended by NUL.
    return commit, sorted({os.fsdecode(entry[3:]) for entry in status.split(b"\0") if entry})


def _writes_report(pytest_command: str, junit: Path) -> bool:
    """Whether a pytest command line writes its junit report to ``junit``.

    The report must be named by ``--junitxml`` or ``--junit-xml``, as ``--junitxml=<path>`` or
    ``--junitxml <path>``, with a relative path read from the current directory.
    """
    try:
        words = shlex.split(pytest_command)
    except ValueError:
        return False
    target = junit.resolve()
    for index, word in enumerate(words):
        option, equals, value = word.partition("=")
        if option not in _JUNIT_OPTIONS:
            continue
        if not equals:
            value = words[index + 1] if index + 1 < len(words) else ""
        if value and Path(value).expanduser().resolve() == target:
            return True
    return False


def record_g1(
    junit: str | os.PathLike[str],
    out: str | os.PathLike[str],
    *,
    command: str,
    pytest_command: str | None = None,
    config_path: str | os.PathLike[str] = G1_CONFIG_PATH,
) -> GateRecord:
    """Write the G1 :class:`GateRecord` for a pytest junit report to ``out`` and return it.

    ``junit`` is pytest's own report (``uv run pytest --junitxml=<path>``), outside the
    repository or under ``fixtures/polarimetry/``. The required tests come from
    :data:`G1_REQUIRED_TESTS_PATH`; the status follows the rules above this function, so a
    required test that is absent, failed, skipped, xfailed or missing a listed case never gives
    PASS. ``command`` is this command's line to record, ``pytest_command`` the pytest command
    line that wrote the report (it must name the report with ``--junitxml``; null when not
    given), and ``config_path`` is config/run.yaml.

    Provenance (PRD 8.12 as amended by PRD-01; PRD 8.13), all from :mod:`provenance`:
    ``command``; ``config_version``, the SHA-256 of ``config_path`` as read;
    ``input_hashes``, every repository file read (config/run.yaml, the required list and the
    report when it lies under ``fixtures/polarimetry/``), sorted by path; and ``run_id``,
    ``provenance.run_id`` over the config and the checksums of the required list and the
    report. ``evidence_path`` is the report when it is a repository path, otherwise the
    committed G1 record ``fixtures/polarimetry/g1-record.json``, whose ``result`` holds the
    report's SHA-256 and counts; a report outside the repository is no repository path.

    The record holds ``gate_id`` G1, the status, ``evidence_path``, the
    scope (stating whether the real-scene check was included), ``consequence`` (PRD 9's failure
    action unless PASS, else null) and ``result``. GateRecord has no notes field and its
    ``result`` is a string, so ``result`` holds a JSON object. Its ``notes`` are the
    provenance: ``command``, ``pytest_command``, ``commit`` (the commit checked out in the
    repository when the record is written) and ``uncommitted_changes`` (the paths that differ
    from that commit; the tested code is the commit exactly when only the record itself is
    listed); ``commit`` and ``uncommitted_changes`` are null outside a git checkout. Run
    record-g1 right after pytest, on the same tree. The rest of ``result`` holds the SHA-256 of
    the report and of the required list, the report timestamps, the counts per fixture, per
    story (conventions, invalid data and other)
    and for the real-scene check (tests, passed, failed, skipped, incomplete, absent and junit
    cases), the ids that failed, were skipped, were incomplete or were absent, the listed cases
    absent from tests that were reported (``absent_cases``, as node ids with parameters), and
    other failing tests in the same modules (``other_failed``, as junit ``classname::name``).

    Raises :class:`G1InputError`, writing nothing, if an input cannot be read or trusted, and
    OSError if ``out`` cannot be written.
    """
    if type(command) is not str or not command:
        raise G1InputError("the command to record must be a non-empty string")
    junit_path = Path(junit)
    out_path = Path(out)
    if pytest_command is not None and (
        type(pytest_command) is not str or not _writes_report(pytest_command, junit_path)
    ):
        raise G1InputError(
            f"the pytest command {pytest_command!r} does not write the junit report"
            f" {junit_path} (name it with --junitxml=<path>)"
        )
    junit_name = _g1_evidence_path(junit_path)
    junit_in_repository = not os.path.isabs(junit_name)
    if out_path.resolve() in {junit_path.resolve(), G1_REQUIRED_TESTS_PATH.resolve()}:
        raise G1InputError(f"the output path {out_path} would overwrite an input")
    if not out_path.parent.is_dir():
        raise G1InputError(f"the output directory {out_path.parent} does not exist")
    required_bytes = _read_input(G1_REQUIRED_TESTS_PATH, "required test list")
    required = _parse_required(required_bytes, G1_REQUIRED_TESTS_PATH)
    junit_bytes = _read_input(junit_path, "junit report")
    cases, timestamps = _parse_junit(junit_bytes, junit_path)
    outcomes, absent_cases, other_failed = _required_outcomes(required, cases)
    real_scene = _g1_counts(required.real_scene_check, outcomes)

    def ids_with(outcome: _G1Outcome) -> list[str]:
        return [nodeid for nodeid, (found, _) in outcomes.items() if found == outcome]

    try:
        config_version = provenance.config_version(Path(config_path))
    except OSError as error:
        raise G1InputError(f"cannot read the config {config_path}: {error}") from error
    required_sha256 = hashlib.sha256(required_bytes).hexdigest()
    junit_sha256 = hashlib.sha256(junit_bytes).hexdigest()
    hashes = {_CONFIG: config_version, _G1_REQUIRED_TESTS: required_sha256}
    if junit_in_repository:
        hashes[junit_name] = junit_sha256
    input_hashes = [InputHash(path=path, sha256=hashes[path]) for path in sorted(hashes)]
    run_id = provenance.run_id(
        Path(config_path),
        [(_G1_REQUIRED_TESTS, required_sha256), (_G1_JUNIT_PRODUCT, junit_sha256)],
    )

    commit, uncommitted_changes = _git_state(_REPO_ROOT)
    result = {
        "notes": {
            "command": command,
            "pytest_command": pytest_command,
            "commit": commit,
            "uncommitted_changes": uncommitted_changes,
        },
        "junit_sha256": junit_sha256,
        "junit_timestamps": timestamps,
        "required_tests": _G1_REQUIRED_TESTS,
        "required_tests_sha256": required_sha256,
        "fixtures": {key: _g1_counts(required.fixtures[key], outcomes) for key in _G1_FIXTURE_IDS},
        **{
            name: {key: _g1_counts(group[key], outcomes) for key in _G1_STORIES}
            for name, group in required.story_groups().items()
        },
        "real_scene_check": real_scene,
        "failed": ids_with("failed"),
        "skipped": ids_with("skipped"),
        "incomplete": ids_with("incomplete"),
        "absent": ids_with("absent"),
        "absent_cases": absent_cases,
        "other_failed": other_failed,
    }
    status = _g1_status(outcomes, other_failed)
    record = GateRecord(
        gate_id="G1",
        status=status,
        evidence_path=junit_name if junit_in_repository else _G1_RECORD,
        run_id=run_id,
        scope=_g1_scope(real_scene),
        result=json.dumps(result),
        consequence=None if status == "PASS" else G1_FAILURE_ACTION,
        command=command,
        config_version=config_version,
        input_hashes=input_hashes,
    )
    out_path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return record


def main(argv: Sequence[str] | None = None) -> int:
    """Command line: ``python -m terraluna_pipeline.polarimetry record-g1 ...``.

    Exit status: 0 when the record says PASS, 1 when it says anything else (FAIL, PARTIAL or
    NOT_RUN; the record is still written), 2 when nothing was written (a usage error or an
    input that cannot be read or trusted).
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="python -m terraluna_pipeline.polarimetry",
        description="Polarimetry tools. record-g1 writes the G1 gate record (PRD 9).",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    record = commands.add_parser(
        "record-g1",
        help="write the G1 GateRecord from a pytest junit report",
        description=(
            "Read a pytest junit report (uv run pytest --junitxml=<path>) and write the G1"
            f" GateRecord. Required tests: {_G1_REQUIRED_TESTS}. Exit status 0 for PASS, 1 for"
            " any other recorded status, 2 when nothing was written."
        ),
    )
    record.add_argument(
        "--junit",
        required=True,
        help=f"pytest junit XML report, outside the repository or under {_G1_FIXTURES_DIR}/",
    )
    record.add_argument("--out", required=True, help="path of the GateRecord JSON to write")
    record.add_argument(
        "--pytest-command",
        default=None,
        help=(
            "the pytest command line that wrote the report, naming it with --junitxml=<path>"
            " (null when omitted)"
        ),
    )
    args = parser.parse_args(arguments)
    command = provenance.command_line("terraluna_pipeline.polarimetry", arguments)
    try:
        gate = record_g1(
            args.junit,
            args.out,
            command=command,
            pytest_command=args.pytest_command,
        )
    except (G1InputError, OSError) as error:
        print(f"record-g1: {error}", file=sys.stderr)
        return 2
    print(f"G1 {gate.status}: wrote {args.out}")
    return 0 if gate.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
