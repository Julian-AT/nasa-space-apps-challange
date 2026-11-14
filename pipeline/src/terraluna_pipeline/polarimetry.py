"""Numerical polarimetry: decoding, angle conversion and validity states (PRD 6.2, 7, 8.11).

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
- Product validity and noise flags enter only through explicit arguments, such as ``nodata``
  and the noise flags of :func:`x_power` (PRD 7: keep the product's flags). Array arguments
  are plain numpy arrays (memory-mapped ones included), scalars, or lists and tuples of them
  nested to any depth. A numpy masked array is refused with TypeError wherever it appears,
  because converting it to an array drops its mask and a masked element would be read as an
  observation. Any other container is refused as well, whether it converts through
  ``__array__``, is a custom sequence or is a third-party ndarray subclass, since its conversion
  can drop a mask in the same way; the caller converts it and passes its mask as a flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any, Final, Literal

import numpy as np
import numpy.typing as npt

__all__ = [
    "INTERNAL_ANGLE_UNIT",
    "PALSAR2_CALIBRATION_OFFSET_DB",
    "REASON_DTYPE",
    "STATE_DTYPE",
    "AngleUnit",
    "InvalidReason",
    "ObsState",
    "StatedArray",
    "angle_to_degrees",
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


STATE_DTYPE: Final = np.dtype(np.uint8)
"""dtype of every per-element state array; its values are :class:`ObsState` codes."""

REASON_DTYPE: Final = np.dtype("<U32")
"""dtype of every per-element reason array; its values are :class:`InvalidReason` codes."""

if max(len(r.value) for r in InvalidReason) > REASON_DTYPE.itemsize // np.dtype("<U1").itemsize:
    raise RuntimeError("REASON_DTYPE is too narrow for the InvalidReason codes")

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

_SCALAR_TYPES: Final = (bool, int, float, complex, str, bytes, np.generic)
"""Scalars numpy converts as single elements. Non-real ones are refused later by dtype checks."""


def _require_plain(name: str, values: object) -> None:
    """Raise unless ``values`` converts to an array without losing a mask.

    Accepted: a plain numpy array (:data:`_PLAIN_ARRAY_TYPES`), a scalar, or lists and tuples of
    them, walked to every depth. ``np.asarray`` drops masks without a warning, and
    ``np.ma.asarray`` reads them only from the immediate members of a sequence, so the walk
    looks at every member itself. A numpy masked array or the masked constant anywhere in
    ``values`` raises TypeError, whatever is masked. So does any other type: numpy converts a
    custom sequence, an ``__array__`` object or an ndarray subclass by its own protocol, which
    can drop a mask the same way. Nesting deeper than numpy's dimension limit raises ValueError,
    so a self-referential list cannot recurse forever.
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
        if type(item) in _PLAIN_ARRAY_TYPES or isinstance(item, _SCALAR_TYPES):
            continue
        if isinstance(item, list | tuple):
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


def _check_offset(calibration_offset_db: float) -> None:
    if isinstance(calibration_offset_db, bool) or not isinstance(
        calibration_offset_db, int | float | np.integer | np.floating
    ):
        raise TypeError(
            f"calibration_offset_db must be a real number, got {calibration_offset_db!r}"
        )
    if not np.isfinite(calibration_offset_db):
        raise ValueError(f"calibration_offset_db must be finite, got {calibration_offset_db!r}")


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
    (``NONFINITE``). Units: input DN (dimensionless amplitude), output dB.
    """
    _check_offset(calibration_offset_db)
    values, classifier = _classify_dn(dn, nodata)
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

    States and reasons are those of :func:`decode_db`: declared nodata is ``MISSING``
    (``NODATA``); zero, negative and non-finite DN are ``INVALID``. A DN of zero is invalid here
    as well, so the linear and dB forms always agree on which pixels are valid. Units: input DN
    (dimensionless amplitude), output linear power ratio (gamma-zero, dimensionless).
    """
    _check_offset(calibration_offset_db)
    values, classifier = _classify_dn(dn, nodata)
    out = np.full(values.shape, np.nan)
    valid = classifier.valid
    out[valid] = np.square(values[valid]) * 10.0 ** (-calibration_offset_db / 10.0)
    return classifier.finish(out)


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
    3. Otherwise ``VALID``. Units: output linear power ratio (dimensionless).
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
    out[valid] = np.square(hv_values[valid] / hh_values[valid])
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

    NaN or infinite angles are ``INVALID`` with reason ``NONFINITE``. Units: output degrees.
    """
    if unit not in _ANGLE_UNITS:
        raise ValueError(f"angle unit must be declared as one of {_ANGLE_UNITS}, got {unit!r}")
    values = _real_array("angle", angle).astype(np.float64)
    classifier = _Classifier(values.shape)
    classifier.mark(~np.isfinite(values), ObsState.INVALID, InvalidReason.NONFINITE)
    out = np.degrees(values) if unit == "radians" else values.copy()
    return classifier.finish(out)
