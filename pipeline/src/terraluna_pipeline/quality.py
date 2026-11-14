"""Validity masks and valid fractions for decoded product arrays.

PRD 5 (Mini-RF rows), 7 (validity rules), 8.3 and 8.11. :func:`validity_mask` classifies every
pixel of an array exactly as decoded by io.py and returns a boolean ``valid`` array with an integer
``reason`` array (:class:`InvalidReason`, stored as uint8). It never returns, fills or changes a
data value: a missing pixel is never replaced by 0, and no fill value is invented.

Reason codes, in precedence order when several apply to one pixel (the data's own state comes
before the caller's support mask). PRD 7 keeps three states distinct: missing observation,
invalid measurement and valid value, so each code names its state.

- ``nodata`` (1), missing observation: the value equals a nodata value the product declares, or
  it is the ISIS 4-byte Null special pixel (real floating arrays only, see below). A declared
  value is compared in the array's dtype, and its exact value is kept until then, never passed
  through a Python float. An integer array compares the exact integer. A floating or complex
  array compares the declared double rounded once to its own type, because a band stores it
  that way (a float32 band holds float32(nodata)). A declared value that is not exactly a
  binary64 double (an integer above 2**53 that no double holds, a fraction such as 1/3) is
  refused, because rounding it to a double first could match a different pixel. A declared NaN
  matches NaN pixels. A declared value the dtype cannot hold (a fraction or an out-of-range
  number for an integer array, or a finite value that overflows a float32 array) is refused,
  not approximated.
- ``isis_saturation`` (4), invalid measurement: one of the ISIS 4-byte saturation special pixels
  (Lrs, Lis, His, Hrs), saturated on the instrument or in a computation. ISIS excludes special
  pixels from computations, so these are never measured values.
- ``non_finite`` (2), invalid measurement: NaN or +-inf that the product does not declare as
  nodata. A complex value is non-finite when either part is.
- ``outside_support`` (3), missing observation: the pixel lies outside the support mask the
  caller passes, for example the product footprint or the effective support from a count map.
- ``valid`` (0): every other pixel. A value of 0 is valid unless the product's label declares it
  a null; negative and large values are valid, and a valid high CPR is never truncated.

ISIS special pixels. The values come from ISIS ``SpecialPixel.h`` (USGS Astrogeology,
``isis/src/core/include/SpecialPixel.h``): for 4-byte reals, Null is the float32 bit pattern
0xFF7FFFFB (about -3.4028227e38) and the saturations Lrs, Lis, His and Hrs are 0xFF7FFFFC to
0xFF7FFFFF (Hrs is -FLT_MAX); 0xFF7FFFFA is the smallest valid value. They are matched in every
real floating array, with a documented tolerance: a value matches a code when it rounds to that
float32 value (round to nearest, ties to even), that is, within half a float32 spacing at this
magnitude, ``ISIS_HALF_SPACING_REAL4`` = 2**103 (about 1.01e31, relative 3.0e-8). A float32
array therefore matches exactly, and a double that went through decimal label text still
matches, while the neighbouring codes, 2**104 apart, stay distinct. ISIS integer nulls (0 for
unsigned types, the most negative value for signed ones) are ordinary values unless the label
declares them as nodata, and ISIS has no complex pixel type, so complex arrays get no ISIS
matching.

No threshold. There is no SNR, quality or range rule here. PRD 7 requires any added SNR rule to
be written in config first, and ``radar.snr_rule`` in config/run.yaml is still null.

Not here. Stokes validity (S1 >= abs(S4), reason ``STOKES_S1_LT_ABS_S4``) and SC, OC and CPR
belong to polarimetry.py (T2-02), and the lunar S4-sign check is
``polarimetry.lunar_s4_sign_check`` (T2-05), so the repository has one vocabulary and one
tolerance for each of those rules.
"""

from __future__ import annotations

import math
import numbers
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
from fractions import Fraction
from typing import Any, Final

import numpy as np
import numpy.typing as npt

__all__ = [
    "ISIS_HALF_SPACING_REAL4",
    "ISIS_NULL_REAL4",
    "ISIS_SATURATION_REAL4",
    "InvalidReason",
    "ValidFraction",
    "ValidityMask",
    "valid_fraction",
    "validity_mask",
]


def _real4(bits: int) -> float:
    """The float32 value with the given IEEE-754 bit pattern, as a Python float."""
    return float(np.array(bits, dtype=np.uint32).view(np.float32))


_ISIS_NULL_BITS: Final = 0xFF7FFFFB
_ISIS_SATURATION_BITS: Final = (0xFF7FFFFC, 0xFF7FFFFD, 0xFF7FFFFE, 0xFF7FFFFF)

ISIS_NULL_REAL4: Final[float] = _real4(_ISIS_NULL_BITS)
"""ISIS 4-byte Null (bit pattern 0xFF7FFFFB): the pixel has no data."""

ISIS_SATURATION_REAL4: Final[tuple[float, ...]] = tuple(_real4(b) for b in _ISIS_SATURATION_BITS)
"""ISIS 4-byte saturations Lrs, Lis, His and Hrs (bit patterns 0xFF7FFFFC to 0xFF7FFFFF)."""

ISIS_HALF_SPACING_REAL4: Final[float] = 2.0**103
"""The matching tolerance: half the float32 spacing (2**104) at the ISIS special-pixel values."""


class InvalidReason(IntEnum):
    """Why a pixel is invalid; the codes and their precedence are in the module docstring."""

    VALID = 0
    NODATA = 1
    NON_FINITE = 2
    OUTSIDE_SUPPORT = 3
    ISIS_SATURATION = 4

    @property
    def label(self) -> str:
        """The code's name as written in records, such as ``"nodata"``."""
        return self.name.lower()


_MAX_REASON: Final = max(InvalidReason)


def _plain_ndarray(value: object) -> bool:
    return isinstance(value, np.ndarray) and not isinstance(value, np.ma.MaskedArray)


def _frozen_copy(array: npt.NDArray[Any]) -> npt.NDArray[Any]:
    copy = np.array(array, copy=True)
    copy.setflags(write=False)
    return copy


@dataclass(frozen=True, eq=False)
class ValidityMask:
    """Per-pixel validity: ``valid`` is True exactly where ``reason`` is ``InvalidReason.VALID``.

    Both arrays are read-only copies with the shape of the classified array. The record holds no
    data values.
    """

    valid: npt.NDArray[np.bool_]
    reason: npt.NDArray[np.uint8]

    def __post_init__(self) -> None:
        if not _plain_ndarray(self.valid) or self.valid.dtype != np.bool_:
            raise TypeError("ValidityMask.valid must be a bool ndarray")
        if not _plain_ndarray(self.reason) or self.reason.dtype != np.uint8:
            raise TypeError("ValidityMask.reason must be a uint8 ndarray of InvalidReason codes")
        if self.valid.shape != self.reason.shape:
            raise ValueError(
                f"ValidityMask shape mismatch: valid {self.valid.shape}, reason {self.reason.shape}"
            )
        if self.reason.size and int(self.reason.max()) > _MAX_REASON:
            raise ValueError(f"ValidityMask.reason holds a code above {int(_MAX_REASON)}")
        if not np.array_equal(self.valid, self.reason == InvalidReason.VALID):
            raise ValueError("ValidityMask.valid must equal reason == InvalidReason.VALID")
        object.__setattr__(self, "valid", _frozen_copy(self.valid))
        object.__setattr__(self, "reason", _frozen_copy(self.reason))

    def reason_counts(self) -> dict[InvalidReason, int]:
        """The number of pixels with each code, for every code in enum order (zeros included)."""
        counts = np.bincount(self.reason.ravel(), minlength=len(InvalidReason))
        return {member: int(counts[member]) for member in InvalidReason}


@dataclass(frozen=True)
class ValidFraction:
    """The covered fraction of a non-empty window and the pixel counts it comes from."""

    valid_pixels: int
    total_pixels: int

    def __post_init__(self) -> None:
        for name in ("valid_pixels", "total_pixels"):
            if type(getattr(self, name)) is not int:
                raise TypeError(f"ValidFraction.{name} must be an int")
        if self.total_pixels <= 0:
            raise ValueError("ValidFraction of an empty window is undefined")
        if not 0 <= self.valid_pixels <= self.total_pixels:
            raise ValueError(
                f"ValidFraction needs 0 <= valid_pixels <= total_pixels, got "
                f"{self.valid_pixels} of {self.total_pixels}"
            )

    @property
    def fraction(self) -> float:
        """valid_pixels / total_pixels, in [0, 1]."""
        return self.valid_pixels / self.total_pixels

    @property
    def invalid_pixels(self) -> int:
        return self.total_pixels - self.valid_pixels


def valid_fraction(mask: ValidityMask | npt.NDArray[np.bool_]) -> ValidFraction:
    """The fraction of all window pixels that are valid, with the counts.

    Takes a :class:`ValidityMask` or a bool array (True = covered). Pixels outside the support
    count as not covered. An empty window has no valid fraction, so it raises ``ValueError``
    rather than returning 0 or 1. Integer arrays are refused: a reason array, where 0 means
    valid, would be counted inverted.
    """
    if isinstance(mask, ValidityMask):
        valid = mask.valid
    elif _plain_ndarray(mask) and mask.dtype == np.bool_:
        valid = mask
    else:
        raise TypeError(
            f"valid_fraction needs a ValidityMask or a bool ndarray, got {type(mask).__name__}"
            + (f" of dtype {mask.dtype}" if isinstance(mask, np.ndarray) else "")
        )
    if valid.size == 0:
        raise ValueError(f"valid_fraction of an empty window (shape {valid.shape}) is undefined")
    return ValidFraction(valid_pixels=int(np.count_nonzero(valid)), total_pixels=int(valid.size))


NodataValue = float | np.number[Any]
"""A declared nodata value: a real number (int, float or numpy real scalar), never a bool."""


@dataclass(frozen=True)
class _Declared:
    """One declared nodata value: as given (for messages) and its exact value.

    ``exact`` is a Fraction for a finite value and a float NaN or +-inf otherwise, so no
    precision is lost before the value meets the array's dtype.
    """

    given: Any
    exact: Fraction | float


def _exact_value(item: Any) -> Fraction | float:
    if isinstance(item, numbers.Integral):
        return Fraction(int(item))
    if item != item:
        return math.nan
    if item == math.inf or item == -math.inf:
        return float(item)
    as_integer_ratio = getattr(item, "as_integer_ratio", None)
    if not callable(as_integer_ratio):
        raise TypeError(
            f"nodata {item!r} of type {type(item).__name__} has no exact value "
            "(as_integer_ratio); pass an int, a float or a numpy real scalar"
        )
    numerator, denominator = as_integer_ratio()
    return Fraction(numerator, denominator)


def _nodata_values(nodata: NodataValue | Iterable[NodataValue] | None) -> tuple[_Declared, ...]:
    if nodata is None:
        return ()
    if isinstance(nodata, np.ndarray):
        items: list[Any] = list(nodata.ravel())
    elif isinstance(nodata, str | bytes) or not isinstance(nodata, Iterable):
        items = [nodata]
    else:
        items = list(nodata)
    for item in items:
        if isinstance(item, bool | np.bool_) or not isinstance(item, numbers.Real):
            raise TypeError(
                f"nodata values must be real numbers (int or float), got {item!r} "
                f"of type {type(item).__name__}"
            )
    return tuple(_Declared(given=item, exact=_exact_value(item)) for item in items)


def _matches_declared(values: npt.NDArray[Any], declared: _Declared) -> npt.NDArray[np.bool_]:
    """Pixels equal to one declared nodata value, compared in the array's dtype."""
    dtype = values.dtype
    given, exact = declared.given, declared.exact
    if dtype.kind in "iu":
        if not (isinstance(exact, Fraction) and exact.denominator == 1):
            raise ValueError(f"nodata {given!r} is not an integer; {dtype} cannot hold it")
        whole = exact.numerator
        info = np.iinfo(dtype)
        if not info.min <= whole <= info.max:
            raise ValueError(f"nodata {given!r} is outside the range of {dtype}")
        result: npt.NDArray[np.bool_] = values == dtype.type(whole)
        return result
    if isinstance(exact, float):
        if not math.isnan(exact):
            infinite: npt.NDArray[np.bool_] = values == dtype.type(exact)
            return infinite
        if dtype.kind == "c":
            raise ValueError(f"a NaN nodata is ambiguous for complex {dtype}; declare it per part")
        nan_hits: npt.NDArray[np.bool_] = np.isnan(values)
        return nan_hits
    try:
        double = float(exact)
    except OverflowError:
        raise ValueError(f"nodata {given!r} overflows {dtype}") from None
    if Fraction(double) != exact:
        raise ValueError(
            f"nodata {given!r} is not exactly a binary64 double, so {dtype} could only be "
            "compared with a rounded guess; declare the double the product states"
        )
    with np.errstate(over="ignore"):
        stored = dtype.type(double)
    if not np.isfinite(stored):
        raise ValueError(f"nodata {given!r} overflows {dtype}")
    equal: npt.NDArray[np.bool_] = values == stored
    return equal


def _isis_codes(
    values: npt.NDArray[np.floating[Any]],
) -> tuple[npt.NDArray[np.bool_], npt.NDArray[np.bool_]]:
    """Pixels that round to the ISIS 4-byte Null, and to an ISIS 4-byte saturation code."""
    if values.dtype == np.float32:
        single = values
    else:
        # Values beyond the float32 range become +-inf, which matches no code.
        with np.errstate(over="ignore"):
            single = values.astype(np.float32)
    bits = single.view(np.uint32)
    null: npt.NDArray[np.bool_] = bits == _ISIS_NULL_BITS
    saturation: npt.NDArray[np.bool_] = (bits >= _ISIS_SATURATION_BITS[0]) & (
        bits <= _ISIS_SATURATION_BITS[-1]
    )
    return null, saturation


def validity_mask(
    values: npt.NDArray[Any],
    *,
    nodata: NodataValue | Iterable[NodataValue] | None = None,
    support: npt.NDArray[np.bool_] | None = None,
) -> ValidityMask:
    """Classify every pixel of ``values`` (see the module docstring for the codes).

    ``values`` is a real or complex numeric ndarray of any shape, as decoded; masked arrays are
    refused because their mask would be ignored. Long double and complex long double arrays are
    refused too: their precision depends on the platform (numpy uses the C ``long double``, 80
    bits on most x86 builds and 64 bits on others), and no GDAL band type is wider than Float64
    and no ISIS pixel type wider than Double. ``nodata`` is the product's declared nodata
    value or values, from its label or file; None declares none. ``support`` is a bool array of
    the same shape, True inside the support; None means every pixel is inside. ``values`` is
    never modified.
    """
    if not _plain_ndarray(values):
        raise TypeError(
            f"values must be a plain numpy ndarray, got {type(values).__name__}; pass a masked "
            "array's data with its declared nodata instead"
        )
    kind = values.dtype.kind
    if kind not in "iufc":
        raise TypeError(f"values must have a real or complex numeric dtype, got {values.dtype}")
    if values.dtype.type in (np.longdouble, np.clongdouble):
        raise TypeError(
            f"values must not be long double ({values.dtype.type.__name__}): its precision "
            "depends on the platform and no GDAL or ISIS pixel type stores it; pass the array "
            "in the dtype the product stores"
        )
    if support is not None:
        if not _plain_ndarray(support) or support.dtype != np.bool_:
            raise TypeError(f"support must be a bool ndarray, got {type(support).__name__}")
        if support.shape != values.shape:
            raise ValueError(
                f"support shape {support.shape} does not match values shape {values.shape}"
            )

    none = np.zeros(values.shape, dtype=np.bool_)
    declared = none.copy()
    for value in _nodata_values(nodata):
        declared |= _matches_declared(values, value)
    isis_null, isis_saturation = _isis_codes(values) if kind == "f" else (none, none)
    non_finite = ~np.isfinite(values) if kind in "fc" else none
    outside = ~support if support is not None else none

    reason = np.select(
        [declared | isis_null, isis_saturation, non_finite, outside],
        [
            InvalidReason.NODATA,
            InvalidReason.ISIS_SATURATION,
            InvalidReason.NON_FINITE,
            InvalidReason.OUTSIDE_SUPPORT,
        ],
        default=InvalidReason.VALID,
    ).astype(np.uint8)
    return ValidityMask(valid=np.asarray(reason == InvalidReason.VALID), reason=reason)
