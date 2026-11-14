"""Numerical polarimetry: decoding, angles, Stokes conversion and validity (PRD 6.2, 7, 8.11).

This module is the numerical contract of PRD section 7. Every function works elementwise on
float64 arrays and returns a :class:`StatedArray`: the values together with a per-element
observation state (:class:`ObsState`) and a machine-readable reason (:class:`InvalidReason`).

Conventions that hold for every function here:

- Three states stay distinct (PRD 7): a missing observation, an invalid measurement and a valid
  value. A valid value may be zero or low; it is never confused with missing.
- An element that is not ``VALID`` holds NaN in ``value``, and its meaning is carried only by
  ``state`` and ``reason``. NaN never stands for zero, and 0.0 never stands for missing.
- Analytical quantities are in linear power; dB is for display and summaries only (PRD 6.2).
- Nothing is repaired, clipped or regularized: no epsilon is added to a denominator (PRD 7).
- Work is in float64 (PRD 8.8), and inputs must be finite and supported (PRD 7). An element
  whose result float64 cannot hold is ``INVALID`` with reason ``FLOAT64_OUT_OF_RANGE``, and the
  other elements are unaffected: a result that overflows, or a positive power below the
  smallest normal float64 (underflowed to zero, or a subnormal without full precision). No
  other numerical range is imposed.
- Product validity and noise flags enter only through explicit arguments, such as ``nodata``,
  the noise flags of :func:`x_power` and ``product_masked`` of :func:`circular_from_stokes`
  (PRD 7: keep the product's flags). Array arguments
  are plain numpy arrays (memory-mapped ones included), scalars, or lists and tuples of them
  nested to any depth, all matched by exact type. A numpy masked array is refused with
  TypeError wherever it appears, because converting it to an array drops its mask and a masked
  element would be read as an observation. Any other type is refused as well, whether it
  converts through ``__array__``, is a custom sequence, a subclass of list, tuple or a scalar
  type, or a third-party ndarray subclass, since its conversion can drop a mask in the same
  way; the caller converts it and passes its mask as a flag.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any, Final, Literal

import numpy as np
import numpy.typing as npt

__all__ = [
    "ARITHMETIC_TOLERANCE",
    "INTERNAL_ANGLE_UNIT",
    "MINIRF_SIS_CONVENTION",
    "PALSAR2_CALIBRATION_OFFSET_DB",
    "REASON_DTYPE",
    "STATE_DTYPE",
    "AngleUnit",
    "CircularPowers",
    "InvalidReason",
    "ObsState",
    "StatedArray",
    "StokesConvention",
    "Tolerance",
    "angle_to_degrees",
    "circular_from_stokes",
    "decode_db",
    "decode_linear",
    "x_power",
]


class ObsState(IntEnum):
    """Observation state of one element (PRD 7: three states stay distinct).

    - ``MISSING``: there is no observation, for example a declared nodata value.
    - ``INVALID``: an observation exists but fails a validity rule, so no value is published.
    - ``VALID``: a usable value. Zero and low values are valid values, not missing ones.

    No state is encoded as 0, so a zero-filled state array is never mistaken for a legal one.
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
    or a positive power falls below the smallest normal float64. Not a physical threshold."""

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
    """SC is below zero, accepted within the Stokes tolerance and reported as computed, but no
    ratio is formed from a negative power and none is clipped to zero (state ``INVALID``)."""


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
    """Same-sense power SC, opposite-sense power OC and CPR = SC/OC, each with its own states.

    ``sc``, ``oc`` and ``cpr`` have the same shape. SC and OC are linear powers in the units the
    product's label defines for its Stokes parameters; CPR is a dimensionless linear ratio, not
    the share of total echo, which is ``CPR / (1 + CPR)`` (PRD 6.2). A ``VALID`` CPR needs a
    ``VALID`` SC and a ``VALID``, positive OC. ``convention`` is the declared Stokes convention
    and ``tolerance`` the one the Stokes validity rules used.
    """

    sc: StatedArray
    oc: StatedArray
    cpr: StatedArray
    convention: StokesConvention
    tolerance: Tolerance

    def __post_init__(self) -> None:
        for name in ("sc", "oc", "cpr"):
            if type(getattr(self, name)) is not StatedArray:
                raise TypeError(f"{name} must be a StatedArray")
        if not self.sc.value.shape == self.oc.value.shape == self.cpr.value.shape:
            raise ValueError(
                "sc, oc and cpr must have the same shape, got "
                f"{self.sc.value.shape}, {self.oc.value.shape} and {self.cpr.value.shape}"
            )
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
    """Convert Mini-RF Stokes parameters to SC, OC and CPR (PRD 6.2, 7; F1-F5).

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
    4. With S2 and S3: ``sqrt(S2**2 + S3**2 + S4**2) - S1 > atol + rtol * |S1|``, which is
       S1**2 >= S2**2 + S3**2 + S4**2 for S1 >= 0 with the tolerance in the units of S1 as in
       rule 3, evaluated on halved inputs so no square overflows: ``INVALID``,
       ``STOKES_NONPHYSICAL``.

    Then SC and OC each: ``INVALID`` with ``FLOAT64_OUT_OF_RANGE`` where the exact power is not
    zero but its float64 magnitude is below the smallest normal float64. An exact zero is a
    valid zero (F1), and the sums cannot overflow. Within the tolerance S4 may exceed S1 or fall
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
    holds NaN with its state and reason, never 0.0. Units: SC and OC in the linear power units
    of the Stokes parameters; CPR dimensionless.
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
            half_norm = np.hypot(np.hypot(0.5 * others[0], 0.5 * others[1]), 0.5 * s4_values)
            half_excess = half_norm - 0.5 * s1_values
            base.mark(
                half_excess > 0.5 * allowed, ObsState.INVALID, InvalidReason.STOKES_NONPHYSICAL
            )

    valid = base.valid
    half_s1 = np.full(shape, np.nan)
    half_s4 = np.full(shape, np.nan)
    with np.errstate(under="ignore"):
        half_s1[valid] = 0.5 * s1_values[valid]
        half_s4[valid] = 0.5 * s4_values[valid]
    sc = half_s1 - half_s4
    oc = half_s1 + half_s4

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
        sc=sc_classifier.finish(sc),
        oc=oc_classifier.finish(oc),
        cpr=cpr_classifier.finish(cpr),
        convention=convention,
        tolerance=tolerance,
    )
