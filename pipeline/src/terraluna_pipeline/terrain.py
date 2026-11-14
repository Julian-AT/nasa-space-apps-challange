"""Terrain observables (PRD 6.3): the structure function nu(L) on a masked metric grid.

``nu(L) = sqrt(mean((z(x + L) - z(x))^2))``, the root-mean-square elevation difference between
cells one lag apart. :func:`structure_function` measures it along both grid axes and both
diagonals and keeps every per-direction value beside an isotropic summary (PRD 6.3). Elevations
and lags are metres in the body's own metric frame, such as a D29 analysis window; nu is metres
and is kept apart from radar and model distances (PRD 6.5, 8.11).

Grid and directions
    The grid is indexed ``[row, column]`` like :class:`terraluna_pipeline.geometry.WindowGrid`:
    columns run east, rows run south and row 0 is at the top, so the window's north (+y) points
    to decreasing rows. Cells are square, ``spacing_m`` on a side. The four directions are unit
    vectors in that frame (:data:`DIRECTION_STEPS` gives one step in whole cells, east and
    north): ``x`` = (1, 0), ``y`` = (0, 1), ``diagonal_ne`` = (1, 1) / sqrt 2 and
    ``diagonal_nw`` = (-1, 1) / sqrt 2. A pair is unordered, so the opposite vector gives the
    same pairs.

Lag to cell offset (a decision for a human; the PRD fixes lags in metres only)
    A lag ``L`` maps to the offset ``k`` steps along a direction, with ``k = round(L / s)`` and
    ``s`` the step length (``spacing_m`` on an axis, ``hypot(spacing_m, spacing_m)`` on a
    diagonal). The offset is used only if ``k >= 1`` and its length in metres (``k * spacing_m``
    on an axis, ``hypot(k * spacing_m, k * spacing_m)`` on a diagonal) equals ``L`` to PRD 7's
    arithmetic tolerance, ``|length - L| <= LAG_ATOL + LAG_RTOL * L``. Otherwise the lag is
    unsupported in that direction with reason ``axis_lag_not_on_grid`` or
    ``diagonal_lag_not_on_grid``. Lags are never rounded to a nearby offset and cells are never
    interpolated (PRD 7 allows no other resampling). On the 90 m grid no diagonal offset has the
    length of a configured lag (270 m would need k = 2.12), so there the diagonals are
    unsupported at every configured lag and the summary rests on the two axes.

Pairs and nodata (PRD 7 Validity rules, PRD 8.12)
    A pair contributes only if both of its cells are valid. A cell is valid if ``valid`` says so
    and, for a numpy masked array, its mask does not; a valid cell must hold a finite value.
    Whatever an invalid cell holds (NaN, 0, a sentinel) never enters. An on-grid offset that
    leaves no pair inside the grid is unsupported with ``lag_exceeds_grid``; one whose pairs all
    touch an invalid cell is unsupported with ``no_valid_pairs``. An unsupported value is None,
    never 0. A supported 0.0 is a real zero: a profile that is constant along that direction.
    Reasons are checked in that order: off the grid, beyond the grid, no valid pairs.

Isotropic summary (a decision for a human; PRD 6.3 says only "summarized isotropically")
    The rule is a required argument and is recorded in the output; the summary is computed from
    the per-direction values and pair counts alone, which it never changes. Over the directions
    supported at a lag:

    - ``direction_mean``: ``sqrt(mean_d nu_d^2)``, each direction weighted equally.
    - ``pooled_pairs``: ``sqrt(sum_d N_d nu_d^2 / sum_d N_d)``, where ``N_d`` is the direction's
      pair count. This is nu over all pairs of those directions pooled together.

    With no supported direction the summary is None with reason ``no_supported_direction``.

Arithmetic
    Pure numpy float64 and the standard library, with no randomness. Differences are taken in
    float64 from the stored values. Sums of squares are scaled by a power of two before
    squaring, which is exact, and summed with :func:`math.fsum`, so the result is the correctly
    rounded sum where plain squaring would neither overflow nor underflow, and relief of any
    float64 magnitude neither overflows nor underflows to 0. A difference beyond the float64
    range raises :class:`TerrainError`.
"""

from __future__ import annotations

import itertools
import math
import numbers
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]

Direction = Literal["x", "y", "diagonal_ne", "diagonal_nw"]
"""Sampling directions in the window frame: both grid axes and both diagonals (PRD 6.3)."""

IsotropicRule = Literal["direction_mean", "pooled_pairs"]
"""How the per-direction values are summarized isotropically (see the module docstring)."""

UnsupportedReason = Literal[
    "axis_lag_not_on_grid",
    "diagonal_lag_not_on_grid",
    "lag_exceeds_grid",
    "no_valid_pairs",
]
"""Machine-readable reasons a lag is unsupported in one direction."""

IsotropicReason = Literal["no_supported_direction"]
"""Machine-readable reason an isotropic summary is unavailable."""

DIRECTIONS: Final[tuple[Direction, ...]] = ("x", "y", "diagonal_ne", "diagonal_nw")
"""Every direction, in output order."""

DIRECTION_STEPS: Final[Mapping[Direction, tuple[int, int]]] = MappingProxyType(
    {"x": (1, 0), "y": (0, 1), "diagonal_ne": (1, 1), "diagonal_nw": (-1, 1)}
)
"""One step along each direction, in whole cells (east, north)."""

ISOTROPIC_RULES: Final[tuple[IsotropicRule, ...]] = ("direction_mean", "pooled_pairs")

LAG_RTOL: Final = 1e-10
"""Relative tolerance for an offset's length to equal a lag (PRD 7 Tolerances)."""
LAG_ATOL: Final = 1e-12
"""Absolute tolerance in metres for an offset's length to equal a lag (PRD 7 Tolerances)."""

_OFF_GRID_REASONS: Final = frozenset({"axis_lag_not_on_grid", "diagonal_lag_not_on_grid"})


class TerrainError(ValueError):
    """Raised for a grid, mask, spacing, lag list or rule the terrain measurements cannot use."""


@dataclass(frozen=True, slots=True)
class DirectionalNu:
    """nu at one lag along one direction.

    ``offset_cells`` is the pair offset in whole cells (east, north) and ``offset_m`` its length
    in metres; both are None when the lag is not on the grid in this direction. ``nu_m`` is None
    exactly when ``reason`` is set, and then ``pair_count`` is 0.
    """

    direction: Direction
    lag_m: float
    offset_cells: tuple[int, int] | None
    offset_m: float | None
    pair_count: int
    nu_m: float | None
    reason: UnsupportedReason | None

    def __post_init__(self) -> None:
        if self.direction not in DIRECTIONS:
            raise TerrainError(f"unknown direction {self.direction!r}")
        if (self.nu_m is None) == (self.reason is None):
            raise TerrainError("nu_m must be None exactly when a reason is given")
        if (self.offset_cells is None) != (self.reason in _OFF_GRID_REASONS):
            raise TerrainError("offset_cells must be None exactly when the lag is off the grid")
        if (self.offset_cells is None) != (self.offset_m is None):
            raise TerrainError("offset_cells and offset_m must both be set or both be None")
        if self.nu_m is None:
            if self.pair_count != 0:
                raise TerrainError("an unsupported value has no pairs")
        elif not (self.pair_count > 0 and math.isfinite(self.nu_m) and self.nu_m >= 0.0):
            raise TerrainError("a supported value needs pairs and a finite nu_m >= 0")

    @property
    def supported(self) -> bool:
        """Whether nu was measured at this lag in this direction."""
        return self.nu_m is not None


@dataclass(frozen=True, slots=True)
class IsotropicNu:
    """The isotropic summary of nu at one lag, under ``rule``.

    ``directions`` lists the supported directions it summarizes, in :data:`DIRECTIONS` order, and
    ``pair_count`` their pairs together. ``nu_m`` is None exactly when ``reason`` is set.
    """

    lag_m: float
    rule: IsotropicRule
    directions: tuple[Direction, ...]
    pair_count: int
    nu_m: float | None
    reason: IsotropicReason | None

    def __post_init__(self) -> None:
        if self.rule not in ISOTROPIC_RULES:
            raise TerrainError(f"unknown isotropic rule {self.rule!r}")
        if (self.nu_m is None) == (self.reason is None):
            raise TerrainError("nu_m must be None exactly when a reason is given")
        if (self.nu_m is None) != (not self.directions):
            raise TerrainError("a summary is available exactly when some direction is supported")

    @property
    def supported(self) -> bool:
        """Whether a summary was available at this lag."""
        return self.nu_m is not None


@dataclass(frozen=True)
class StructureFunction:
    """nu(L) per direction and its isotropic summary (PRD 6.3).

    ``directional[d][i]`` and ``isotropic[i]`` belong to ``lags_m[i]``. ``directional`` is a
    read-only mapping with one entry per direction in :data:`DIRECTIONS` order.
    """

    spacing_m: float
    lags_m: tuple[float, ...]
    isotropic_rule: IsotropicRule
    directional: Mapping[Direction, tuple[DirectionalNu, ...]]
    isotropic: tuple[IsotropicNu, ...]

    def __post_init__(self) -> None:
        directional = dict(self.directional)
        if tuple(directional) != DIRECTIONS:
            raise TerrainError("directional must hold every direction, in DIRECTIONS order")
        for direction, items in directional.items():
            if tuple(item.lag_m for item in items) != self.lags_m:
                raise TerrainError(f"{direction}: one entry per lag, in lag order")
            if any(item.direction != direction for item in items):
                raise TerrainError(f"{direction}: entries of another direction")
        if tuple(item.lag_m for item in self.isotropic) != self.lags_m:
            raise TerrainError("isotropic: one entry per lag, in lag order")
        if any(item.rule != self.isotropic_rule for item in self.isotropic):
            raise TerrainError("isotropic: every entry must use isotropic_rule")
        object.__setattr__(self, "directional", MappingProxyType(directional))


def structure_function(
    values: npt.ArrayLike,
    *,
    valid: npt.ArrayLike,
    spacing_m: float,
    lags_m: Sequence[float],
    isotropic_rule: IsotropicRule,
) -> StructureFunction:
    """Return nu(L) along both axes and both diagonals of a masked grid (PRD 6.3).

    ``values`` is a 2-D grid of real elevations in metres indexed ``[row, column]`` (row 0 at
    the top), or a numpy masked array whose mask marks nodata. ``valid`` is a boolean grid of
    the same shape; a cell is used only where it is True and not masked. ``spacing_m`` is the
    cell size and ``lags_m`` the lags in metres, strictly increasing. ``isotropic_rule`` picks
    the summary rule and is recorded in the result. The module docstring states how lags map to
    cell offsets, which reasons mark an unsupported value and how the summary is formed.

    Raises :class:`TerrainError` for a grid that is not 2-D, empty or not real numbers, a mask of
    another shape or type, a valid cell that is not finite, a spacing or lag that is not finite
    and positive, lags that do not strictly increase, an unknown rule, or a difference beyond
    the float64 range.
    """
    z, use = _grid(values, valid)
    spacing = _positive_length("spacing_m", spacing_m)
    lags = _lags(lags_m, spacing)
    if isotropic_rule not in ISOTROPIC_RULES:
        known = ", ".join(ISOTROPIC_RULES)
        raise TerrainError(f"unknown isotropic rule {isotropic_rule!r}; known rules: {known}")

    directional = {
        direction: tuple(_directional(z, use, direction, lag, spacing) for lag in lags)
        for direction in DIRECTIONS
    }
    isotropic = tuple(
        _isotropic(tuple(directional[d][i] for d in DIRECTIONS), lag, isotropic_rule)
        for i, lag in enumerate(lags)
    )
    return StructureFunction(
        spacing_m=spacing,
        lags_m=lags,
        isotropic_rule=isotropic_rule,
        directional=directional,
        isotropic=isotropic,
    )


# --- Inputs -----------------------------------------------------------------------------------


def _grid(values: npt.ArrayLike, valid: npt.ArrayLike) -> tuple[FloatArray, BoolArray]:
    # Read a masked array's mask first: np.asarray would drop it.
    masked = np.ma.getmaskarray(values)
    data = np.asarray(np.ma.getdata(values))
    if data.ndim != 2:
        raise TerrainError(f"values must be a 2-D grid, got {data.ndim} dimensions")
    if data.size == 0:
        raise TerrainError(f"values must hold at least one cell, got shape {data.shape}")
    if data.dtype == np.bool_ or not (
        np.issubdtype(data.dtype, np.integer) or np.issubdtype(data.dtype, np.floating)
    ):
        raise TerrainError(f"values must be real numbers, got dtype {data.dtype}")
    keep = np.asarray(valid)
    if keep.dtype != np.bool_:
        raise TerrainError(f"valid must be a boolean grid, got dtype {keep.dtype}")
    if keep.shape != data.shape:
        raise TerrainError(f"valid has shape {keep.shape}, values have {data.shape}")
    z = data.astype(np.float64)
    use = keep & ~masked
    if not np.all(np.isfinite(z[use])):
        raise TerrainError("every valid cell must hold a finite value")
    return z, use


def _real(name: str, value: object) -> float:
    if isinstance(value, bool | np.bool_) or not isinstance(value, numbers.Real):
        raise TerrainError(f"{name} must be a real number, got {value!r}")
    return float(value)


def _positive_length(name: str, value: object) -> float:
    length = _real(name, value)
    if not (math.isfinite(length) and length > 0.0):
        raise TerrainError(f"{name} must be finite and positive, got {length!r}")
    return length


def _lags(lags_m: Sequence[float], spacing: float) -> tuple[float, ...]:
    if isinstance(lags_m, str | bytes) or not isinstance(lags_m, Sequence | np.ndarray):
        raise TerrainError(f"lags_m must be a sequence of lags, got {lags_m!r}")
    lags = tuple(_positive_length(f"lags_m[{i}]", lag) for i, lag in enumerate(lags_m))
    if not lags:
        raise TerrainError("lags_m must hold at least one lag")
    if any(later <= earlier for earlier, later in itertools.pairwise(lags)):
        raise TerrainError(f"lags_m must be strictly increasing, got {lags}")
    for lag in lags:
        if not math.isfinite(lag / spacing):
            raise TerrainError(f"lag {lag!r} m is more cells than float64 holds at {spacing!r} m")
    return lags


# --- Measurement ------------------------------------------------------------------------------


def _directional(
    z: FloatArray, use: BoolArray, direction: Direction, lag: float, spacing: float
) -> DirectionalNu:
    east, north = DIRECTION_STEPS[direction]
    diagonal = east != 0 and north != 0
    step = math.hypot(spacing, spacing) if diagonal else spacing
    k = round(lag / step)
    length = math.hypot(k * spacing, k * spacing) if diagonal else k * spacing
    if k < 1 or not abs(length - lag) <= LAG_ATOL + LAG_RTOL * lag:
        reason: UnsupportedReason = (
            "diagonal_lag_not_on_grid" if diagonal else "axis_lag_not_on_grid"
        )
        return DirectionalNu(direction, lag, None, None, 0, None, reason)
    cells = (k * east, k * north)
    differences = _pair_differences(z, use, *cells)
    if differences is None or differences.size == 0:
        reason = "lag_exceeds_grid" if differences is None else "no_valid_pairs"
        return DirectionalNu(direction, lag, cells, length, 0, None, reason)
    return DirectionalNu(
        direction, lag, cells, length, int(differences.size), _rms(differences), None
    )


def _pair_differences(z: FloatArray, use: BoolArray, east: int, north: int) -> FloatArray | None:
    """Differences z(b) - z(a) over pairs b = a + (east, north) cells with both cells valid.

    Returns None when the offset leaves no pair inside the grid.
    """
    rows, cols = z.shape
    d_row, d_col = -north, east
    if abs(d_row) >= rows or abs(d_col) >= cols:
        return None
    a_rows = slice(max(0, -d_row), rows - max(0, d_row))
    b_rows = slice(max(0, d_row), rows - max(0, -d_row))
    a_cols = slice(max(0, -d_col), cols - max(0, d_col))
    b_cols = slice(max(0, d_col), cols - max(0, -d_col))
    both = use[a_rows, a_cols] & use[b_rows, b_cols]
    with np.errstate(over="ignore", invalid="ignore"):
        differences: FloatArray = z[b_rows, b_cols][both] - z[a_rows, a_cols][both]
    if not np.all(np.isfinite(differences)):
        raise TerrainError("an elevation difference would overflow float64")
    return differences


def _isotropic(items: tuple[DirectionalNu, ...], lag: float, rule: IsotropicRule) -> IsotropicNu:
    used = tuple(item for item in items if item.nu_m is not None)
    if not used:
        return IsotropicNu(lag, rule, (), 0, None, "no_supported_direction")
    nus = np.array([item.nu_m for item in used], dtype=np.float64)
    counts = np.array([item.pair_count for item in used], dtype=np.float64)
    nu = _rms(nus, weights=None if rule == "direction_mean" else counts)
    return IsotropicNu(
        lag,
        rule,
        tuple(item.direction for item in used),
        sum(item.pair_count for item in used),
        nu,
        None,
    )


def _rms(values: FloatArray, weights: FloatArray | None = None) -> float:
    """``sqrt(sum(w * v^2) / sum(w))`` (all weights 1 if None), without overflow or underflow.

    The values are scaled by the power of two just above their largest magnitude, which is
    exact, so the squares lie in [0, 1); small squares that underflow there are below the
    rounding of the sum. Sums use :func:`math.fsum`.
    """
    peak = float(np.max(np.abs(values)))
    if peak == 0.0:
        return 0.0
    _, exponent = math.frexp(peak)
    with np.errstate(under="ignore"):
        scaled = np.ldexp(values, -exponent)
        squares = scaled * scaled
        if weights is None:
            mean = math.fsum(squares.tolist()) / squares.size
        else:
            mean = math.fsum((weights * squares).tolist()) / math.fsum(weights.tolist())
    return math.ldexp(math.sqrt(mean), exponent)
