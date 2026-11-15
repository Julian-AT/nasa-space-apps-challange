"""Terrain observables (PRD 6.3): the structure function nu(L) and the window fingerprint.

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

Window fingerprint (PRD 6.3, 7 Validity rules, 8.11)
    :func:`terrain_fingerprint` measures one D29 analysis window
    (:class:`terraluna_pipeline.geometry.WindowGrid`) once per detrend policy in
    ``terrain.detrend``, in file order: ``none`` gives **relief** (the raw values, tilt included)
    and ``plane`` gives **texture** (the values minus their least-squares plane). Both are kept
    and named; neither replaces the other. Every threshold is read from the
    :class:`terraluna_pipeline.config.TerrainConfig` it is given, never from a copy here.

    Window validity. ``valid_fraction`` is ``valid_cells / total_cells`` rounded once to float64,
    with the counts kept beside it. The window is excluded with ``below_min_valid_fraction`` if
    ``valid_fraction < min_valid_fraction``; a fraction equal to the threshold passes (``>=``).
    An excluded window is not measured: no plane, no nu, no H.

    Lag support. Each configured lag is checked in this order and the first failure is its
    reason: ``lag_m / spacing_m >= 3`` (``below_min_lag_cells``), ``lag_m / window_m <= 0.25``
    (``exceeds_quarter_window``), the window is not excluded (``window_excluded``), the lag has a
    whole-cell offset in some direction (``lag_not_on_grid``) and that offset has valid pairs
    (``no_valid_pairs``). Each quotient is rounded once to float64 and compared without an added
    tolerance. A lag passing the first two rules is at most a quarter of the window, so its
    offset always has pairs inside the grid and ``lag_exceeds_grid`` cannot arise here. Support
    depends only on the mask, so relief and texture share one list. Dropped lags keep their
    entries with a reason; their nu is None, never 0.

    Plane removal (a decision for a human; the PRD says "a best-fit plane"). The plane
    ``z = a + b x + c y`` minimises the sum of squared residuals over the valid cells, in the
    D29 frame (x east, y north, metres from the window centre). It is solved exactly: every
    stored float64 value is an exact rational, the normal equations are solved in integers and
    each residual is rounded once to float64. So an exact plane leaves residuals of exactly 0,
    and texture nu is then a true 0 rather than rounding noise; nodata cells never enter.
    :class:`PlaneFit` records the tilt (``slope_east`` = dz/dx, ``slope_north`` = dz/dy, both
    metres per metre) and the plane's value at the window centre. Valid cells that are all on
    one line do not determine a plane: texture is then unavailable with
    ``plane_fit_degenerate``.

    Coarsening (PRD 6.3). The caller supplies ``coarsening_factor`` (1 for none); the window grid
    spacing must equal ``analysis_grid_m * coarsening_factor``. A factor below 1 would be
    upsampling and is rejected. The effective support of the source is not inferred here.

H estimator (a decision for a human; PRD 6.3 does not name one)
    :func:`hurst_fit` fits H as the ordinary least-squares slope of ``ln nu`` against ``ln L``
    over the supported lags, each lag weighted equally, on the isotropic summary of nu. For
    ``nu = c L^H`` that slope is H. The gate, in this order: at least ``min_lags_for_H``
    supported lags (``insufficient_lags``), ``max / min >= min_lag_span_factor`` with one
    rounded quotient (``insufficient_lag_span``), and nu above 0 at every used lag
    (``nu_zero``). A zero nu is never offset by an epsilon and its logarithm is never taken.
    Unavailable H is None with its reason. Window exclusion and a degenerate plane are reported
    as ``window_excluded`` and ``plane_fit_degenerate`` before the gate.

Evidence labels (PRD 3)
    Supported nu and fitted H are ``Derived``; unsupported nu and unavailable H are
    ``Unavailable``.

Synthetic parity fixtures (PRD 8.8, 12 T3 and T10)
    :func:`write_parity_fixtures` (``python -m terraluna_pipeline.terrain [--output DIR]``)
    writes ``fixtures/parity/terrain_synthetic.json`` and ``geometry_synthetic.json``: Python
    reference outputs for the TypeScript mirror. Every input is synthetic and stored exactly with
    its sha256: geographic source rasters holding a plane, a sinusoid, a seeded fractal
    (:func:`weierstrass_mandelbrot`, seeded by :func:`splitmix64`; no random number library is
    used) and three nodata patterns (hole, edge strip, checkerboard), each resampled by the D29
    resampler and measured under both isotropic rules, plus inverse and forward AEQD cases for
    each body. Floats are written as their shortest round-trip repr and checked on reload. Each
    file names its producing commit (the last commit to :data:`PARITY_SOURCES`, which must be
    unmodified), the sha256 of the run config and the PRD 8.8 tolerances. Real windows are
    added by a later task.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import numbers
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal

import numpy as np
import numpy.typing as npt

from terraluna_pipeline.config import (
    DEFAULT_RUN_CONFIG,
    DETREND_POLICIES,
    DetrendPolicy,
    TerrainConfig,
    load_run_config,
    load_terrain_config,
)
from terraluna_pipeline.geometry import (
    BODY_RADII_M,
    GeographicLookup,
    WindowGrid,
    aeqd_forward,
    aeqd_inverse,
    body_radius_m,
    resample_window,
    window_grid,
)

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


VariantName = Literal["relief", "texture"]
"""PRD 6.3 names: ``relief`` is detrend ``none``, ``texture`` is detrend ``plane``."""

LagReason = Literal[
    "below_min_lag_cells",
    "exceeds_quarter_window",
    "window_excluded",
    "lag_not_on_grid",
    "no_valid_pairs",
]
"""Machine-readable reasons a configured lag is not supported in a window fingerprint."""

WindowReason = Literal["below_min_valid_fraction"]
"""Machine-readable reason a window is excluded from measurement."""

VariantReason = Literal["window_excluded", "plane_fit_degenerate"]
"""Machine-readable reasons a detrend variant is unavailable as a whole."""

HurstReason = Literal[
    "window_excluded",
    "plane_fit_degenerate",
    "insufficient_lags",
    "insufficient_lag_span",
    "nu_zero",
]
"""Machine-readable reasons H is unavailable."""

EvidenceLabel = Literal["Derived", "Unavailable"]
"""PRD 3 evidence labels used by terrain values."""

VARIANT_NAMES: Final[Mapping[DetrendPolicy, VariantName]] = MappingProxyType(
    {"none": "relief", "plane": "texture"}
)
"""The PRD 6.3 name of each detrend policy."""

MIN_LAG_CELLS: Final = 3.0
"""A supported lag is at least this many analysis cells (PRD 6.3)."""
MAX_LAG_WINDOW_FRACTION: Final = 0.25
"""A supported lag is at most this fraction of the window width (PRD 6.3)."""


class TerrainError(ValueError):
    """Raised for a grid, mask, spacing, lag list or rule the terrain measurements cannot use."""


class UnknownDetrendPolicyError(TerrainError):
    """Raised when a config names a detrend policy outside ``none`` and ``plane``."""


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


# --- Window fingerprint (PRD 6.3, 7 Validity rules, 8.11) -------------------------------------


@dataclass(frozen=True, slots=True)
class LagSupport:
    """Whether one configured lag is supported in a window, and if not, why.

    ``lag_cells`` is ``lag_m / spacing_m`` rounded once. ``pair_count`` is the number of pairs
    behind the isotropic summary; it is 0 exactly when ``reason`` is set.
    """

    lag_m: float
    lag_cells: float
    pair_count: int
    reason: LagReason | None

    def __post_init__(self) -> None:
        if (self.reason is None) != (self.pair_count > 0):
            raise TerrainError("a supported lag needs pairs and an unsupported one has none")

    @property
    def supported(self) -> bool:
        """Whether nu is measured at this lag."""
        return self.reason is None


@dataclass(frozen=True, slots=True)
class PlaneFit:
    """The least-squares plane removed for texture, solved exactly over the valid cells.

    ``z = intercept_m + slope_east * x + slope_north * y`` with x east and y north in metres
    from the window centre (the D29 frame). Slopes are metres per metre. ``cell_count`` is the
    number of valid cells the plane was fitted to.
    """

    intercept_m: float
    slope_east: float
    slope_north: float
    cell_count: int


@dataclass(frozen=True, slots=True)
class HurstFit:
    """H from the least-squares slope of ln nu on ln L, or the reason it is unavailable.

    ``lags_m`` and ``nu_m`` are the supported lags and their isotropic nu offered to the fit.
    ``log_intercept`` is the fitted ln nu at L = 1 m (nu in metres). ``H`` and
    ``log_intercept`` are None exactly when ``reason`` is set.
    """

    H: float | None
    log_intercept: float | None
    lags_m: tuple[float, ...]
    nu_m: tuple[float, ...]
    reason: HurstReason | None

    def __post_init__(self) -> None:
        if (self.H is None) == (self.reason is None):
            raise TerrainError("H must be None exactly when a reason is given")
        if (self.log_intercept is None) != (self.H is None):
            raise TerrainError("log_intercept must be set exactly when H is")
        if len(self.lags_m) != len(self.nu_m):
            raise TerrainError("one nu per lag")

    @property
    def label(self) -> EvidenceLabel:
        """``Derived`` for a fitted H, ``Unavailable`` otherwise (PRD 3)."""
        return "Unavailable" if self.H is None else "Derived"


@dataclass(frozen=True, eq=False)
class TerrainVariant:
    """One detrend variant of a window: relief (``none``) or texture (``plane``).

    ``surface_m`` is the read-only grid that was measured (raw values for relief, plane
    residuals for texture, NaN at invalid cells). ``structure`` is nu(L) at the lags that pass
    the PRD 6.3 lag rules, None if none does. ``nu_m`` (isotropic) and ``directional_nu_m``
    line up with the configured lags and hold None wherever a value is unsupported, never 0.
    ``reason`` is set when the whole variant is unavailable; then every value is None.
    """

    detrend: DetrendPolicy
    name: VariantName
    reason: VariantReason | None
    plane: PlaneFit | None
    surface_m: FloatArray | None
    structure: StructureFunction | None
    nu_m: tuple[float | None, ...]
    directional_nu_m: Mapping[Direction, tuple[float | None, ...]]
    hurst: HurstFit

    def __post_init__(self) -> None:
        if self.detrend not in VARIANT_NAMES or self.name != VARIANT_NAMES[self.detrend]:
            raise TerrainError(f"detrend {self.detrend!r} is not named {self.name!r}")
        if (self.reason is None) != (self.surface_m is not None):
            raise TerrainError("a variant has a surface exactly when it is available")
        if (self.plane is not None) != (self.detrend == "plane" and self.reason is None):
            raise TerrainError("only an available texture variant has a plane")
        directional = dict(self.directional_nu_m)
        if tuple(directional) != DIRECTIONS:
            raise TerrainError("directional_nu_m must hold every direction, in DIRECTIONS order")
        if any(len(values) != len(self.nu_m) for values in directional.values()):
            raise TerrainError("directional_nu_m: one value per configured lag")
        if self.reason is not None and (
            self.structure is not None or any(nu is not None for nu in self.nu_m)
        ):
            raise TerrainError("an unavailable variant has no values")
        object.__setattr__(self, "directional_nu_m", MappingProxyType(directional))

    @property
    def labels(self) -> tuple[EvidenceLabel, ...]:
        """``Derived`` or ``Unavailable`` for each entry of ``nu_m`` (PRD 3)."""
        return tuple("Unavailable" if nu is None else "Derived" for nu in self.nu_m)


@dataclass(frozen=True, eq=False)
class TerrainFingerprint:
    """The terrain fingerprint of one analysis window (PRD 6.3, 8.11).

    ``analysis_grid_m`` is the spacing measured on, ``configured_analysis_grid_m`` the
    configured grid and ``coarsening_factor`` their ratio as supplied. ``lags`` has one entry
    per configured lag (``lags_m``); ``supported_lags_m`` lists those kept. ``variants`` is a
    read-only mapping with one entry per detrend policy, in ``detrend`` order. The settings
    used (``min_valid_fraction``, ``min_lags_for_H``, ``min_lag_span_factor``,
    ``isotropic_rule``) are recorded beside the values.
    """

    window_m: float
    analysis_grid_m: float
    configured_analysis_grid_m: float
    coarsening_factor: float
    grid_size: int
    total_cells: int
    valid_cells: int
    valid_fraction: float
    min_valid_fraction: float
    excluded_reason: WindowReason | None
    lags_m: tuple[float, ...]
    lags: tuple[LagSupport, ...]
    supported_lags_m: tuple[float, ...]
    isotropic_rule: IsotropicRule
    min_lags_for_H: int
    min_lag_span_factor: float
    detrend: tuple[DetrendPolicy, ...]
    variants: Mapping[DetrendPolicy, TerrainVariant]

    def __post_init__(self) -> None:
        variants = dict(self.variants)
        if tuple(variants) != self.detrend:
            raise TerrainError("variants must hold one entry per detrend policy, in order")
        if any(variant.detrend != policy for policy, variant in variants.items()):
            raise TerrainError("variants: entry of another detrend policy")
        if tuple(item.lag_m for item in self.lags) != self.lags_m:
            raise TerrainError("lags: one entry per configured lag, in order")
        if self.supported_lags_m != tuple(item.lag_m for item in self.lags if item.supported):
            raise TerrainError("supported_lags_m must list the supported lags")
        if self.excluded_reason is not None and self.supported_lags_m:
            raise TerrainError("an excluded window has no supported lag")
        if any(len(variant.nu_m) != len(self.lags_m) for variant in variants.values()):
            raise TerrainError("variants: one nu per configured lag")
        object.__setattr__(self, "variants", MappingProxyType(variants))

    @property
    def excluded(self) -> bool:
        """Whether the window was excluded from measurement."""
        return self.excluded_reason is not None


def hurst_fit(
    lags_m: Sequence[float],
    nu_m: Sequence[float],
    *,
    min_lags_for_H: int,
    min_lag_span_factor: float,
) -> HurstFit:
    """Fit H on supported lags and their nu, or return the reason it is unavailable.

    ``lags_m`` are strictly increasing positive lags in metres (possibly none) and ``nu_m`` the
    finite nu >= 0 at each. The gate and the estimator are stated in the module docstring: at
    least ``min_lags_for_H`` lags, ``lags_m[-1] / lags_m[0] >= min_lag_span_factor``, and no
    zero nu; then H is the least-squares slope of ln nu on ln L. Raises :class:`TerrainError`
    for inputs or settings outside those domains.
    """
    min_lags = _min_lags_for_h(min_lags_for_H)
    span = _span_factor(min_lag_span_factor)
    lags = _fit_lags(lags_m)
    nus = _fit_nus(nu_m, len(lags))
    if len(lags) < min_lags:
        return HurstFit(None, None, lags, nus, "insufficient_lags")
    if not lags[-1] / lags[0] >= span:
        return HurstFit(None, None, lags, nus, "insufficient_lag_span")
    if any(nu == 0.0 for nu in nus):
        return HurstFit(None, None, lags, nus, "nu_zero")
    x = [math.log(lag) for lag in lags]
    y = [math.log(nu) for nu in nus]
    x_mean = math.fsum(x) / len(x)
    y_mean = math.fsum(y) / len(y)
    dx = [value - x_mean for value in x]
    sxx = math.fsum(d * d for d in dx)
    if sxx == 0.0:
        # Lags so close that their logarithms coincide in float64 span no range at all.
        return HurstFit(None, None, lags, nus, "insufficient_lag_span")
    sxy = math.fsum(d * (value - y_mean) for d, value in zip(dx, y, strict=True))
    slope = sxy / sxx
    return HurstFit(slope, y_mean - slope * x_mean, lags, nus, None)


def terrain_fingerprint(
    values: npt.ArrayLike,
    *,
    valid: npt.ArrayLike,
    grid: WindowGrid,
    config: TerrainConfig,
    isotropic_rule: IsotropicRule,
    coarsening_factor: float,
) -> TerrainFingerprint:
    """Measure relief and texture of one analysis window (PRD 6.3, 8.11).

    ``values`` are elevations in metres on ``grid`` (``grid.size`` square, indexed
    ``[row, column]``, row 0 at the top), or a numpy masked array whose mask marks nodata.
    ``valid`` combines the effective-support and quality masks: a cell is used only where it is
    True and not masked, and must then be finite. Lags, the H gate, the valid-fraction
    threshold and the detrend policies come from ``config``. ``isotropic_rule`` picks the
    summary that H is fitted on. ``coarsening_factor`` (>= 1) states how much coarser than
    ``config.analysis_grid_m`` the grid is; it is recorded, not inferred. The module docstring
    states every rule and reason.

    Raises :class:`TerrainError` for a grid or config the fingerprint cannot use, and
    :class:`UnknownDetrendPolicyError` for an unknown detrend policy.
    """
    if not isinstance(grid, WindowGrid):
        raise TerrainError(f"grid must be a WindowGrid, got {type(grid).__name__}")
    if not isinstance(config, TerrainConfig):
        raise TerrainError(f"config must be a TerrainConfig, got {type(config).__name__}")
    policies = _detrend_policies(config.detrend)
    min_lags = _min_lags_for_h(config.min_lags_for_H)
    span = _span_factor(config.min_lag_span_factor)
    min_fraction = _min_valid_fraction(config.min_valid_fraction)
    configured = _positive_length("analysis_grid_m", config.analysis_grid_m)
    factor = _coarsening_factor(coarsening_factor)
    spacing = _positive_length("grid.spacing_m", grid.spacing_m)
    window = _positive_length("grid.width_m", grid.width_m)
    if not math.isclose(spacing, configured * factor, rel_tol=LAG_RTOL, abs_tol=LAG_ATOL):
        raise TerrainError(
            f"grid spacing {spacing!r} m is not analysis_grid_m {configured!r} m times the "
            f"coarsening factor {factor!r}"
        )
    lags = _lags(config.lags_m, spacing)
    if isotropic_rule not in ISOTROPIC_RULES:
        known = ", ".join(ISOTROPIC_RULES)
        raise TerrainError(f"unknown isotropic rule {isotropic_rule!r}; known rules: {known}")
    z, use = _grid(values, valid)
    if z.shape != (grid.size, grid.size):
        raise TerrainError(f"values have shape {z.shape}, the grid is {grid.size} x {grid.size}")

    total = int(use.size)
    count = int(np.count_nonzero(use))
    fraction = count / total
    excluded: WindowReason | None = (
        None if fraction >= min_fraction else ("below_min_valid_fraction")
    )
    rule_reasons = [_lag_rule(lag, spacing, window) for lag in lags]
    measured = tuple(lag for lag, reason in zip(lags, rule_reasons, strict=True) if reason is None)
    relief = None
    if excluded is None and measured:
        relief = structure_function(
            z, valid=use, spacing_m=spacing, lags_m=measured, isotropic_rule=isotropic_rule
        )
    support = tuple(
        _lag_support(lag, spacing, reason, excluded, relief)
        for lag, reason in zip(lags, rule_reasons, strict=True)
    )
    supported = tuple(item.lag_m for item in support if item.supported)

    variants: dict[DetrendPolicy, TerrainVariant] = {}
    for policy in policies:
        if excluded is not None:
            variants[policy] = _unavailable_variant(policy, "window_excluded", len(lags))
            continue
        plane: PlaneFit | None = None
        if policy == "none":
            surface = np.where(use, z, np.nan)
            structure = relief
        else:
            fitted = _plane_fit(z, use, grid)
            if fitted is None:
                variants[policy] = _unavailable_variant(policy, "plane_fit_degenerate", len(lags))
                continue
            plane, surface = fitted
            structure = None
            if measured:
                structure = structure_function(
                    surface,
                    valid=use,
                    spacing_m=spacing,
                    lags_m=measured,
                    isotropic_rule=isotropic_rule,
                )
        variants[policy] = _variant(
            policy, plane, surface, structure, lags, supported, min_lags, span
        )

    return TerrainFingerprint(
        window_m=window,
        analysis_grid_m=spacing,
        configured_analysis_grid_m=configured,
        coarsening_factor=factor,
        grid_size=grid.size,
        total_cells=total,
        valid_cells=count,
        valid_fraction=fraction,
        min_valid_fraction=min_fraction,
        excluded_reason=excluded,
        lags_m=lags,
        lags=support,
        supported_lags_m=supported,
        isotropic_rule=isotropic_rule,
        min_lags_for_H=min_lags,
        min_lag_span_factor=span,
        detrend=policies,
        variants=variants,
    )


# --- Fingerprint settings ---------------------------------------------------------------------


def _detrend_policies(value: object) -> tuple[DetrendPolicy, ...]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise TerrainError(f"detrend must be a sequence of policies, got {value!r}")
    if not value:
        raise TerrainError("detrend must name at least one policy")
    policies: list[DetrendPolicy] = []
    for policy in value:
        if policy == "none" or policy == "plane":
            policies.append(policy)
        else:
            known = ", ".join(DETREND_POLICIES)
            raise UnknownDetrendPolicyError(f"unknown detrend policy {policy!r}; known: {known}")
    if len(set(policies)) != len(policies):
        raise TerrainError(f"detrend lists a policy twice: {tuple(value)!r}")
    return tuple(policies)


def _min_lags_for_h(value: object) -> int:
    if isinstance(value, bool | np.bool_) or not isinstance(value, numbers.Integral):
        raise TerrainError(f"min_lags_for_H must be an integer, got {value!r}")
    if int(value) < 2:
        raise TerrainError(f"min_lags_for_H must be at least 2 to fit a slope, got {value!r}")
    return int(value)


def _span_factor(value: object) -> float:
    factor = _real("min_lag_span_factor", value)
    if not (math.isfinite(factor) and factor >= 1.0):
        raise TerrainError(f"min_lag_span_factor must be finite and at least 1, got {factor!r}")
    return factor


def _min_valid_fraction(value: object) -> float:
    fraction = _real("min_valid_fraction", value)
    if not 0.0 < fraction <= 1.0:
        raise TerrainError(f"min_valid_fraction must lie in (0, 1], got {fraction!r}")
    return fraction


def _coarsening_factor(value: object) -> float:
    factor = _real("coarsening_factor", value)
    if not (math.isfinite(factor) and factor >= 1.0):
        raise TerrainError(
            f"coarsening_factor must be finite and at least 1 (no upsampling), got {factor!r}"
        )
    return factor


def _fit_lags(lags_m: Sequence[float]) -> tuple[float, ...]:
    if isinstance(lags_m, str | bytes) or not isinstance(lags_m, Sequence | np.ndarray):
        raise TerrainError(f"lags_m must be a sequence of lags, got {lags_m!r}")
    lags = tuple(_positive_length(f"lags_m[{i}]", lag) for i, lag in enumerate(lags_m))
    if any(later <= earlier for earlier, later in itertools.pairwise(lags)):
        raise TerrainError(f"lags_m must be strictly increasing, got {lags}")
    return lags


def _fit_nus(nu_m: Sequence[float], count: int) -> tuple[float, ...]:
    if isinstance(nu_m, str | bytes) or not isinstance(nu_m, Sequence | np.ndarray):
        raise TerrainError(f"nu_m must be a sequence of values, got {nu_m!r}")
    nus = tuple(_real(f"nu_m[{i}]", nu) for i, nu in enumerate(nu_m))
    if len(nus) != count:
        raise TerrainError(f"nu_m has {len(nus)} values for {count} lags")
    for i, nu in enumerate(nus):
        if not (math.isfinite(nu) and nu >= 0.0):
            raise TerrainError(f"nu_m[{i}] must be finite and >= 0, got {nu!r}")
    return nus


# --- Fingerprint measurement ------------------------------------------------------------------


def _lag_rule(lag: float, spacing: float, window: float) -> LagReason | None:
    if not lag / spacing >= MIN_LAG_CELLS:
        return "below_min_lag_cells"
    if not lag / window <= MAX_LAG_WINDOW_FRACTION:
        return "exceeds_quarter_window"
    return None


def _lag_support(
    lag: float,
    spacing: float,
    rule_reason: LagReason | None,
    excluded: WindowReason | None,
    relief: StructureFunction | None,
) -> LagSupport:
    cells = lag / spacing
    if rule_reason is not None:
        return LagSupport(lag, cells, 0, rule_reason)
    if excluded is not None or relief is None:
        return LagSupport(lag, cells, 0, "window_excluded")
    index = relief.lags_m.index(lag)
    summary = relief.isotropic[index]
    if summary.nu_m is not None:
        return LagSupport(lag, cells, summary.pair_count, None)
    on_grid = any(relief.directional[d][index].offset_cells is not None for d in DIRECTIONS)
    return LagSupport(lag, cells, 0, "no_valid_pairs" if on_grid else "lag_not_on_grid")


def _unavailable_variant(policy: DetrendPolicy, reason: VariantReason, lags: int) -> TerrainVariant:
    empty: tuple[float | None, ...] = (None,) * lags
    return TerrainVariant(
        detrend=policy,
        name=VARIANT_NAMES[policy],
        reason=reason,
        plane=None,
        surface_m=None,
        structure=None,
        nu_m=empty,
        directional_nu_m={direction: empty for direction in DIRECTIONS},
        hurst=HurstFit(None, None, (), (), reason),
    )


def _variant(
    policy: DetrendPolicy,
    plane: PlaneFit | None,
    surface: FloatArray,
    structure: StructureFunction | None,
    lags: tuple[float, ...],
    supported: tuple[float, ...],
    min_lags: int,
    span: float,
) -> TerrainVariant:
    def aligned(measured: Sequence[float | None]) -> tuple[float | None, ...]:
        if structure is None:
            return (None,) * len(lags)
        by_lag = dict(zip(structure.lags_m, measured, strict=True))
        return tuple(by_lag.get(lag) for lag in lags)

    nu = aligned([item.nu_m for item in structure.isotropic] if structure else [])
    directional = {
        d: aligned([item.nu_m for item in structure.directional[d]] if structure else [])
        for d in DIRECTIONS
    }
    used = [nu[lags.index(lag)] for lag in supported]
    if any(value is None for value in used):
        raise TerrainError("a supported lag has no nu in this variant")
    fit_nu = [value for value in used if value is not None]
    surface.setflags(write=False)
    return TerrainVariant(
        detrend=policy,
        name=VARIANT_NAMES[policy],
        reason=None,
        plane=plane,
        surface_m=surface,
        structure=structure,
        nu_m=nu,
        directional_nu_m=directional,
        hurst=hurst_fit(supported, fit_nu, min_lags_for_H=min_lags, min_lag_span_factor=span),
    )


def _det3(m: Sequence[Sequence[int]]) -> int:
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def _plane_fit(
    z: FloatArray, use: BoolArray, grid: WindowGrid
) -> tuple[PlaneFit, FloatArray] | None:
    """The exact least-squares plane over the valid cells and its residuals, or None.

    Cells are placed at doubled, centred whole-cell coordinates ``u = 2 column - (n - 1)`` and
    ``v = (n - 1) - 2 row`` (east and north), an exact affine image of the D29 frame, so the
    residuals are the same as in metres. Each value is the exact rational ``num / 2^k`` that
    float64 stores, scaled to an integer by one common power of two; the normal equations are
    then integers and are solved by Cramer's rule. Each residual is one integer quotient,
    which Python rounds correctly to float64. Returns None when the valid cells lie on one line
    (the determinant is 0), so no plane is determined.
    """
    rows, cols = z.shape
    row_index, col_index = np.nonzero(use)
    if row_index.size == 0:
        return None
    us = (2 * col_index - (cols - 1)).tolist()
    vs = ((rows - 1) - 2 * row_index).tolist()
    ratios = [value.as_integer_ratio() for value in z[row_index, col_index].tolist()]
    scale = max(denominator for _, denominator in ratios)
    zs = [numerator * (scale // denominator) for numerator, denominator in ratios]
    count = len(zs)
    su, sv = sum(us), sum(vs)
    suu = sum(u * u for u in us)
    svv = sum(v * v for v in vs)
    suv = sum(u * v for u, v in zip(us, vs, strict=True))
    sz = sum(zs)
    suz = sum(u * value for u, value in zip(us, zs, strict=True))
    svz = sum(v * value for v, value in zip(vs, zs, strict=True))
    normal = ((count, su, sv), (su, suu, suv), (sv, suv, svv))
    det = _det3(normal)
    if det == 0:
        return None
    rhs = (sz, suz, svz)

    def replaced(column: int) -> int:
        return _det3(
            tuple(
                tuple(rhs[r] if c == column else normal[r][c] for c in range(3)) for r in range(3)
            )
        )

    a_num, p_num, q_num = replaced(0), replaced(1), replaced(2)
    denominator = det * scale
    residuals = np.full(z.shape, np.nan, dtype=np.float64)
    try:
        residuals[row_index, col_index] = [
            (value * det - a_num - p_num * u - q_num * v) / denominator
            for value, u, v in zip(zs, us, vs, strict=True)
        ]
    except OverflowError:
        raise TerrainError("a plane residual would overflow float64") from None

    # Back to the D29 frame: x = (u / 2) s + x0 and y = (v / 2) s + y0, where (x0, y0) is the
    # centre of the cell grid, ((n s - W) / 2, (W - n s) / 2).
    spacing = Fraction(grid.spacing_m)
    width = Fraction(grid.width_m)
    slope_east = Fraction(2 * p_num, denominator) / spacing
    slope_north = Fraction(2 * q_num, denominator) / spacing
    x0 = (cols * spacing - width) / 2
    y0 = (width - rows * spacing) / 2
    intercept = Fraction(a_num, denominator) - slope_east * x0 - slope_north * y0
    return PlaneFit(float(intercept), float(slope_east), float(slope_north), count), residuals


# --- Synthetic parity fixtures (PRD 8.8, 12 T3 and T10) ---------------------------------------


class ParityFixtureError(RuntimeError):
    """Raised when the parity fixtures cannot be produced with a clear provenance."""


PARITY_SCHEMA_VERSION: Final = 1
"""Version of the layout of ``fixtures/parity/*.json`` written here."""

PARITY_FILE_NAMES: Final = ("terrain_synthetic.json", "geometry_synthetic.json")
"""The files :func:`write_parity_fixtures` writes, in order."""

REPOSITORY_ROOT: Final[Path] = DEFAULT_RUN_CONFIG.parents[1]
"""The repository root, the parent of ``config/`` (PRD 8.3)."""

DEFAULT_PARITY_DIR: Final[Path] = REPOSITORY_ROOT / "fixtures" / "parity"
"""Where ``python -m terraluna_pipeline.terrain`` writes by default (PRD 8.8)."""

PARITY_SOURCES: Final = (
    "pipeline/src/terraluna_pipeline/config.py",
    "pipeline/src/terraluna_pipeline/geometry.py",
    "pipeline/src/terraluna_pipeline/terrain.py",
)
"""The modules that produce the fixtures; ``producing_commit`` is the last commit to them."""

PARITY_TOLERANCES: Final[Mapping[str, Mapping[str, float]]] = MappingProxyType(
    {
        "resampled_grid": {"rtol": 1e-9},
        "log_nu": {"atol": 1e-9},
        "H": {"atol": 1e-9},
        "distance_m": {"atol": 1e-9},
    }
)
"""PRD 8.8 parity tolerances, stored in every fixture file for the TypeScript tests."""

PARITY_NODATA: Final = -32768.0
"""The declared nodata sentinel of every synthetic source raster."""

PARITY_ISOTROPIC_RULES: Final[tuple[IsotropicRule, ...]] = ("direction_mean", "pooled_pairs")
"""Both isotropic rules are written: the choice is still open (T3-04 decision 2)."""

PARITY_DIAGONAL_LAG_CELLS: Final = (3, 4, 6, 8)
"""Extra structure-function lags k * hypot(D, D), so the diagonals are exercised (T3-04)."""

PARITY_TOLERANCE_FIELDS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "resampled_grid": ("cases[].resampled.values_m (relative to the Python value)",),
        "log_nu": (
            "cases[].structure.*.directional.*[].log_nu",
            "cases[].structure.*.isotropic[].log_nu",
            "cases[].fingerprint.*.variants.*.log_nu",
            "cases[].fingerprint.*.variants.*.directional_log_nu.*",
        ),
        "H": ("cases[].fingerprint.*.variants.*.hurst.H",),
        "distance_m": (
            "grid.x_m",
            "grid.y_m",
            "window_grids[].x_m",
            "window_grids[].y_m",
            "aeqd_cases[].forward.x_m",
            "aeqd_cases[].forward.y_m",
            "aeqd_cases[].rho_m",
            "aeqd_cases[].inverse.lat_rad as radius_m * |dlat|",
            "aeqd_cases[].inverse.lon_rad as radius_m * cos(lat) * |dlon|, dlon taken modulo 2 pi",
        ),
    }
)
"""Which fields each PRD 8.8 tolerance applies to. Masks, counts, reasons and nulls are exact."""

_SHA256_RULE: Final = (
    "sha256 of each input as little-endian IEEE 754 float64 values in row-major order"
)
_UINT64_MASK: Final = (1 << 64) - 1


def splitmix64(seed: int, count: int) -> tuple[int, ...]:
    """The first ``count`` outputs of Vigna's SplitMix64 from state ``seed`` (unsigned 64 bit).

    Integer arithmetic only, as in prng.di.unimi.it/splitmix64.c, so every platform and the
    TypeScript mirror (with BigInt) produce the same sequence. It seeds the synthetic fractal;
    it is not used by any measurement.
    """
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= _UINT64_MASK:
        raise TerrainError(f"seed must be an integer in [0, 2**64), got {seed!r}")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise TerrainError(f"count must be a non-negative integer, got {count!r}")
    state = seed
    out: list[int] = []
    for _ in range(count):
        state = (state + 0x9E3779B97F4A7C15) & _UINT64_MASK
        z = state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _UINT64_MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _UINT64_MASK
        out.append(z ^ (z >> 31))
    return tuple(out)


def _unit_interval(values: Sequence[int]) -> list[float]:
    """Each 64-bit integer's top 53 bits as a float in [0, 1), exactly."""
    return [math.ldexp(value >> 11, -53) for value in values]


def weierstrass_mandelbrot(
    east_m: npt.ArrayLike,
    north_m: npt.ArrayLike,
    *,
    hurst: float,
    seed: int,
    directions: int,
    octaves: int,
    ratio: float,
    longest_wavelength_m: float,
    amplitude_m: float,
) -> FloatArray:
    """A seeded, band-limited Weierstrass-Mandelbrot surface in metres (synthetic input only).

    ``z = amplitude_m * sum_m sum_n ratio^(-H n) sin(2 pi ratio^n s_m / longest_wavelength_m +
    phi_mn)`` with ``s_m = E cos(theta_m) + N sin(theta_m)``, after the two-dimensional form of
    Ausloos and Berman (1985), for m < ``directions`` and n < ``octaves``. The direction jitter
    and phases come from :func:`splitmix64`: the first ``directions`` draws u_m give
    ``theta_m = pi (m + u_m) / directions``, the next ``directions * octaves`` give
    ``phi_mn = 2 pi u`` (m outer, n inner). Between its shortest and longest wavelengths nu
    grows roughly as L^H. It is a parity input with stored values; recovery of a known H is
    tested on the spectral fBm of T3-06, not on this surface.
    """
    east, north = np.broadcast_arrays(
        np.asarray(east_m, dtype=np.float64), np.asarray(north_m, dtype=np.float64)
    )
    h = _real("hurst", hurst)
    if not 0.0 < h < 1.0:
        raise TerrainError(f"hurst must lie in (0, 1), got {h!r}")
    for name, count in (("directions", directions), ("octaves", octaves)):
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise TerrainError(f"{name} must be a positive integer, got {count!r}")
    factor = _real("ratio", ratio)
    if not (math.isfinite(factor) and factor > 1.0):
        raise TerrainError(f"ratio must be finite and above 1, got {factor!r}")
    longest = _positive_length("longest_wavelength_m", longest_wavelength_m)
    amplitude = _real("amplitude_m", amplitude_m)
    draws = _unit_interval(splitmix64(seed, directions * (1 + octaves)))
    total = np.zeros(east.shape, dtype=np.float64)
    for m in range(directions):
        theta = math.pi * (m + draws[m]) / directions
        along = east * math.cos(theta) + north * math.sin(theta)
        for n in range(octaves):
            phase = 2.0 * math.pi * draws[directions + m * octaves + n]
            wavenumber = 2.0 * math.pi * math.pow(factor, n) / longest
            total += math.pow(factor, -h * n) * np.sin(wavenumber * along + phase)
    surface: FloatArray = amplitude * total
    return surface


@dataclass(frozen=True, slots=True)
class _ParityCase:
    """A synthetic geographic source raster around a window centre (degrees)."""

    name: str
    nodata_pattern: Literal["none", "hole", "edge_strip", "checkerboard"]
    body: str
    lat0_deg: float
    lon0_deg: float
    pixel_deg: float
    size: int
    surface: Mapping[str, object]
    pattern: Mapping[str, int]


def _fractal(seed: int, hurst: float, base_m: float) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "kind": "weierstrass_mandelbrot",
            "prng": "splitmix64",
            "seed": seed,
            "hurst": hurst,
            "directions": 8,
            "octaves": 9,
            "ratio": 1.5,
            "longest_wavelength_m": 8000.0,
            "amplitude_m": 4.0,
            "base_m": base_m,
        }
    )


_PARITY_CASES: Final[tuple[_ParityCase, ...]] = (
    _ParityCase(
        "plane",
        "none",
        "moon",
        -12.5,
        33.25,
        1.0 / 256.0,
        50,
        MappingProxyType(
            {"kind": "plane", "base_m": 1200.0, "slope_east": 0.031, "slope_north": -0.017}
        ),
        MappingProxyType({}),
    ),
    _ParityCase(
        "sinusoid",
        "none",
        "mars",
        22.75,
        179.97,
        1.0 / 512.0,
        50,
        MappingProxyType(
            {
                "kind": "sinusoid",
                "base_m": 2100.0,
                "amplitude_m": 35.0,
                "wavelength_east_m": 1300.0,
                "wavelength_north_m": 2100.0,
                "phase_rad": 0.4,
            }
        ),
        MappingProxyType({}),
    ),
    _ParityCase(
        "fractal",
        "none",
        "moon",
        5.5,
        -71.5,
        1.0 / 256.0,
        50,
        _fractal(20261115, 0.7, 1500.0),
        MappingProxyType({}),
    ),
    _ParityCase(
        "nodata_hole",
        "hole",
        "earth",
        8.25,
        -12.0,
        1.0 / 1024.0,
        50,
        _fractal(7, 0.4, 800.0),
        MappingProxyType({"row_start": 20, "row_stop": 28, "column_start": 21, "column_stop": 29}),
    ),
    _ParityCase(
        "nodata_edge_strip",
        "edge_strip",
        "moon",
        -20.0,
        140.0,
        1.0 / 256.0,
        50,
        MappingProxyType(
            {
                "kind": "plane_plus_sinusoid",
                "base_m": 950.0,
                "slope_east": 0.012,
                "slope_north": 0.024,
                "amplitude_m": 6.0,
                "wavelength_east_m": 900.0,
                "wavelength_north_m": -1500.0,
                "phase_rad": 1.1,
            }
        ),
        MappingProxyType({"column_stop": 8}),
    ),
    _ParityCase(
        "nodata_checkerboard",
        "checkerboard",
        "mars",
        -15.0,
        -100.0,
        1.0 / 512.0,
        50,
        MappingProxyType(
            {
                "kind": "sinusoid",
                "base_m": 300.0,
                "amplitude_m": 20.0,
                "wavelength_east_m": 1700.0,
                "wavelength_north_m": 1100.0,
                "phase_rad": 2.0,
            }
        ),
        MappingProxyType({"block_pixels": 6}),
    ),
)


def _parity_case(name: str) -> _ParityCase:
    for spec in _PARITY_CASES:
        if spec.name == name:
            return spec
    known = ", ".join(spec.name for spec in _PARITY_CASES)
    raise ParityFixtureError(f"no parity case {name!r}; known cases: {known}")


def _parity_lookup(spec: _ParityCase) -> GeographicLookup:
    """North-up geographic raster whose centre corner is the window centre."""
    half = spec.size / 2.0
    return GeographicLookup(
        origin_lon_deg=spec.lon0_deg - half * spec.pixel_deg,
        origin_lat_deg=spec.lat0_deg + half * spec.pixel_deg,
        pixel_width_deg=spec.pixel_deg,
        pixel_height_deg=-spec.pixel_deg,
    )


def _float_param(surface: Mapping[str, object], key: str) -> float:
    return _real(key, surface[key])


def _int_param(surface: Mapping[str, object], key: str) -> int:
    value = surface[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParityFixtureError(f"{key} must be an integer, got {value!r}")
    return value


def parity_source(name: str) -> FloatArray:
    """The synthetic source raster of parity case ``name``, metres, nodata as the sentinel.

    Pixel centres of the geographic raster are placed in local metres about the window centre,
    ``E = R cos(lat0) (lon - lon0)`` and ``N = R (lat - lat0)`` (angles in radians, R the D29
    radius of the case's body), and the case's surface is evaluated there. Then the case's
    nodata pattern sets pixels to :data:`PARITY_NODATA`. The array is read-only.
    """
    spec = _parity_case(name)
    lookup = _parity_lookup(spec)
    radius = body_radius_m(spec.body)
    index = np.arange(spec.size, dtype=np.float64) + 0.5
    lon = lookup.origin_lon_deg + index * lookup.pixel_width_deg
    lat = lookup.origin_lat_deg + index * lookup.pixel_height_deg
    east_1d = radius * math.cos(math.radians(spec.lat0_deg)) * np.radians(lon - spec.lon0_deg)
    north_1d = radius * np.radians(lat - spec.lat0_deg)
    east, north = np.meshgrid(east_1d, north_1d, indexing="xy")
    surface = spec.surface
    kind = surface["kind"]
    base = _float_param(surface, "base_m")
    values = np.full(east.shape, base, dtype=np.float64)
    if kind in ("plane", "plane_plus_sinusoid"):
        values = (
            values
            + _float_param(surface, "slope_east") * east
            + _float_param(surface, "slope_north") * north
        )
    if kind in ("sinusoid", "plane_plus_sinusoid"):
        argument = 2.0 * math.pi * (
            east / _float_param(surface, "wavelength_east_m")
            + north / _float_param(surface, "wavelength_north_m")
        ) + _float_param(surface, "phase_rad")
        values = values + _float_param(surface, "amplitude_m") * np.sin(argument)
    if kind == "weierstrass_mandelbrot":
        values = values + weierstrass_mandelbrot(
            east,
            north,
            hurst=_float_param(surface, "hurst"),
            seed=_int_param(surface, "seed"),
            directions=_int_param(surface, "directions"),
            octaves=_int_param(surface, "octaves"),
            ratio=_float_param(surface, "ratio"),
            longest_wavelength_m=_float_param(surface, "longest_wavelength_m"),
            amplitude_m=_float_param(surface, "amplitude_m"),
        )
    rows, columns = np.indices(values.shape)
    pattern = spec.pattern
    if spec.nodata_pattern == "hole":
        missing = (
            (rows >= pattern["row_start"])
            & (rows < pattern["row_stop"])
            & (columns >= pattern["column_start"])
            & (columns < pattern["column_stop"])
        )
    elif spec.nodata_pattern == "edge_strip":
        missing = columns < pattern["column_stop"]
    elif spec.nodata_pattern == "checkerboard":
        block = pattern["block_pixels"]
        missing = (rows // block + columns // block) % 2 == 1
    else:
        missing = np.zeros(values.shape, dtype=np.bool_)
    source: FloatArray = np.where(missing, PARITY_NODATA, values)
    source.setflags(write=False)
    return source


def _sha256_f8(value: npt.ArrayLike) -> str:
    return hashlib.sha256(np.ascontiguousarray(value, dtype="<f8").tobytes()).hexdigest()


def _log_nu(nu: float | None) -> float | None:
    """ln nu, or None where nu is unavailable or 0 (the logarithm of 0 is not finite)."""
    return None if nu is None or nu == 0.0 else math.log(nu)


def _grid_json(values: FloatArray) -> list[list[float | None]]:
    return [[None if math.isnan(v) else v for v in row] for row in values.tolist()]


def _structure_json(result: StructureFunction) -> dict[str, object]:
    return {
        "spacing_m": result.spacing_m,
        "lags_m": list(result.lags_m),
        "isotropic_rule": result.isotropic_rule,
        "directional": {
            direction: [
                {
                    "lag_m": item.lag_m,
                    "offset_cells": None if item.offset_cells is None else list(item.offset_cells),
                    "offset_m": item.offset_m,
                    "pair_count": item.pair_count,
                    "nu_m": item.nu_m,
                    "log_nu": _log_nu(item.nu_m),
                    "reason": item.reason,
                }
                for item in items
            ]
            for direction, items in result.directional.items()
        },
        "isotropic": [
            {
                "lag_m": item.lag_m,
                "directions": list(item.directions),
                "pair_count": item.pair_count,
                "nu_m": item.nu_m,
                "log_nu": _log_nu(item.nu_m),
                "reason": item.reason,
            }
            for item in result.isotropic
        ],
    }


def _variant_json(variant: TerrainVariant) -> dict[str, object]:
    plane = variant.plane
    hurst = variant.hurst
    return {
        "name": variant.name,
        "reason": variant.reason,
        "plane": None
        if plane is None
        else {
            "intercept_m": plane.intercept_m,
            "slope_east": plane.slope_east,
            "slope_north": plane.slope_north,
            "cell_count": plane.cell_count,
        },
        "nu_m": list(variant.nu_m),
        "log_nu": [_log_nu(nu) for nu in variant.nu_m],
        "labels": list(variant.labels),
        "directional_nu_m": {d: list(nus) for d, nus in variant.directional_nu_m.items()},
        "directional_log_nu": {
            d: [_log_nu(nu) for nu in nus] for d, nus in variant.directional_nu_m.items()
        },
        "structure": None if variant.structure is None else _structure_json(variant.structure),
        "hurst": {
            "H": hurst.H,
            "log_intercept": hurst.log_intercept,
            "lags_m": list(hurst.lags_m),
            "nu_m": list(hurst.nu_m),
            "reason": hurst.reason,
            "label": hurst.label,
        },
    }


def _fingerprint_json(fingerprint: TerrainFingerprint) -> dict[str, object]:
    return {
        "isotropic_rule": fingerprint.isotropic_rule,
        "window_m": fingerprint.window_m,
        "analysis_grid_m": fingerprint.analysis_grid_m,
        "configured_analysis_grid_m": fingerprint.configured_analysis_grid_m,
        "coarsening_factor": fingerprint.coarsening_factor,
        "grid_size": fingerprint.grid_size,
        "total_cells": fingerprint.total_cells,
        "valid_cells": fingerprint.valid_cells,
        "valid_fraction": fingerprint.valid_fraction,
        "min_valid_fraction": fingerprint.min_valid_fraction,
        "excluded_reason": fingerprint.excluded_reason,
        "lags": [
            {
                "lag_m": item.lag_m,
                "lag_cells": item.lag_cells,
                "pair_count": item.pair_count,
                "reason": item.reason,
            }
            for item in fingerprint.lags
        ],
        "supported_lags_m": list(fingerprint.supported_lags_m),
        "min_lags_for_H": fingerprint.min_lags_for_H,
        "min_lag_span_factor": fingerprint.min_lag_span_factor,
        "detrend": list(fingerprint.detrend),
        "variants": {
            policy: _variant_json(variant) for policy, variant in fingerprint.variants.items()
        },
    }


def _structure_lags(config: TerrainConfig) -> tuple[float, ...]:
    diagonal = math.hypot(config.analysis_grid_m, config.analysis_grid_m)
    return tuple(sorted({*config.lags_m, *(k * diagonal for k in PARITY_DIAGONAL_LAG_CELLS)}))


def _terrain_case_json(
    spec: _ParityCase, config: TerrainConfig, grid: WindowGrid
) -> dict[str, object]:
    lookup = _parity_lookup(spec)
    source = parity_source(spec.name)
    lat0 = math.radians(spec.lat0_deg)
    lon0 = math.radians(spec.lon0_deg)
    resampled = resample_window(
        source,
        lookup,
        grid=grid,
        body=spec.body,
        lat0_rad=lat0,
        lon0_rad=lon0,
        nodata=PARITY_NODATA,
    )
    lags = _structure_lags(config)
    inputs: dict[str, object] = {
        "lat0_rad": lat0,
        "lon0_rad": lon0,
        "source_m": source.tolist(),
    }
    return {
        "name": spec.name,
        "nodata_pattern": spec.nodata_pattern,
        "nodata_pattern_parameters": dict(spec.pattern),
        "surface": dict(spec.surface),
        "body": spec.body,
        "radius_m": body_radius_m(spec.body),
        "centre_deg": {"lat": spec.lat0_deg, "lon": spec.lon0_deg},
        "inputs": inputs,
        "input_sha256": {
            "lat0_rad": _sha256_f8(lat0),
            "lon0_rad": _sha256_f8(lon0),
            "source_m": _sha256_f8(source),
        },
        "source": {
            "lookup": {
                "form": "geographic",
                "origin_lon_deg": lookup.origin_lon_deg,
                "origin_lat_deg": lookup.origin_lat_deg,
                "pixel_width_deg": lookup.pixel_width_deg,
                "pixel_height_deg": lookup.pixel_height_deg,
            },
            "nodata": PARITY_NODATA,
            "rows": int(source.shape[0]),
            "columns": int(source.shape[1]),
            "nodata_pixels": int(np.count_nonzero(source == PARITY_NODATA)),
        },
        "resampled": {
            "values_m": _grid_json(resampled.values),
            "valid": resampled.valid.tolist(),
            "inside": resampled.inside.tolist(),
            "valid_cells": int(np.count_nonzero(resampled.valid)),
        },
        "structure": {
            rule: _structure_json(
                structure_function(
                    resampled.values,
                    valid=resampled.valid,
                    spacing_m=grid.spacing_m,
                    lags_m=lags,
                    isotropic_rule=rule,
                )
            )
            for rule in PARITY_ISOTROPIC_RULES
        },
        "fingerprint": {
            rule: _fingerprint_json(
                terrain_fingerprint(
                    resampled.values,
                    valid=resampled.valid,
                    grid=grid,
                    config=config,
                    isotropic_rule=rule,
                    coarsening_factor=1.0,
                )
            )
            for rule in PARITY_ISOTROPIC_RULES
        },
    }


def _aeqd_points(grid: WindowGrid) -> tuple[FloatArray, FloatArray]:
    """Window offsets for the AEQD cases: both diagonals, the axes, corners and the centre."""
    half = grid.width_m / 2.0
    xs: list[float] = [0.0]
    ys: list[float] = [0.0]
    n = grid.size
    for i in range(n):
        xs += [float(grid.x_m[i]), float(grid.x_m[i])]
        ys += [float(grid.y_m[i]), float(grid.y_m[n - 1 - i])]
    for i in range(0, n, 6):
        xs += [float(grid.x_m[i]), 0.0]
        ys += [0.0, float(grid.y_m[i])]
    for sx, sy in ((1, 1), (1, -1), (-1, 1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)):
        xs.append(sx * half)
        ys.append(sy * half)
    return np.array(xs, dtype=np.float64), np.array(ys, dtype=np.float64)


def _aeqd_centres(radius: float) -> tuple[tuple[str, float, float], ...]:
    """(kind, lat0, lon0) in radians; the antimeridian and pole windows cross the line or pole."""
    antimeridian_lat = math.radians(15.0)
    return (
        ("equator", 0.0, math.radians(12.5)),
        ("mid_latitude", math.radians(-37.25), math.radians(-121.75)),
        (
            "antimeridian",
            antimeridian_lat,
            math.pi - 1000.0 / (radius * math.cos(antimeridian_lat)),
        ),
        ("north_pole", math.pi / 2.0 - 2000.0 / radius, math.radians(45.0)),
        ("south_pole", -(math.pi / 2.0 - 1500.0 / radius), math.radians(-160.0)),
    )


def _aeqd_cases_json(grid: WindowGrid) -> list[dict[str, object]]:
    x, y = _aeqd_points(grid)
    cases: list[dict[str, object]] = []
    for body, radius in BODY_RADII_M.items():
        for kind, lat0, lon0 in _aeqd_centres(radius):
            lat, lon = aeqd_inverse(x, y, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
            x_back, y_back = aeqd_forward(lat, lon, lat0_rad=lat0, lon0_rad=lon0, radius_m=radius)
            cases.append(
                {
                    "name": f"{body}_{kind}",
                    "body": body,
                    "centre_kind": kind,
                    "radius_m": radius,
                    "inputs": {
                        "lat0_rad": lat0,
                        "lon0_rad": lon0,
                        "x_m": x.tolist(),
                        "y_m": y.tolist(),
                    },
                    "input_sha256": {
                        "lat0_rad": _sha256_f8(lat0),
                        "lon0_rad": _sha256_f8(lon0),
                        "x_m": _sha256_f8(x),
                        "y_m": _sha256_f8(y),
                    },
                    "inverse": {"lat_rad": lat.tolist(), "lon_rad": lon.tolist()},
                    "forward": {"x_m": x_back.tolist(), "y_m": y_back.tolist()},
                    "rho_m": np.hypot(x, y).tolist(),
                }
            )
    return cases


def _window_grid_json(name: str, grid: WindowGrid, keys: Sequence[str]) -> dict[str, object]:
    return {
        "name": name,
        "config_keys": list(keys),
        "width_m": grid.width_m,
        "spacing_m": grid.spacing_m,
        "size": grid.size,
        "uncovered_m": grid.uncovered_m,
        "x_m": grid.x_m.tolist(),
        "y_m": grid.y_m.tolist(),
    }


def _git(repo_root: Path, *args: str) -> str:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ParityFixtureError(f"git {' '.join(args)} failed in {repo_root}: {error}") from None
    return completed.stdout.strip()


def producing_commit(repo_root: Path = REPOSITORY_ROOT) -> str:
    """The last commit that changed :data:`PARITY_SOURCES`, which must be unmodified.

    Raises :class:`ParityFixtureError` outside a git repository, when no commit holds the
    sources, or when any of them differs from that commit (commit the sources first, so the
    fixtures always name the code that produced them).
    """
    status = _git(
        repo_root, "status", "--porcelain", "--untracked-files=all", "--", *PARITY_SOURCES
    )
    if status:
        raise ParityFixtureError(
            "the producing sources differ from their last commit; commit them before writing "
            f"parity fixtures:\n{status}"
        )
    commit = _git(repo_root, "log", "-1", "--format=%H", "--", *PARITY_SOURCES)
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise ParityFixtureError(f"no commit holds the producing sources (got {commit!r})")
    return commit


def _same_json(original: object, reloaded: object, path: str) -> None:
    """Raise unless ``reloaded`` equals ``original`` exactly, floats bit for bit."""
    if isinstance(original, float):
        if not (
            isinstance(reloaded, float)
            and np.float64(original).tobytes() == np.float64(reloaded).tobytes()
        ):
            raise ParityFixtureError(f"{path}: {original!r} did not survive JSON ({reloaded!r})")
    elif isinstance(original, Mapping):
        if not isinstance(reloaded, dict) or list(reloaded) != list(original):
            raise ParityFixtureError(f"{path}: keys changed in JSON")
        for key, value in original.items():
            _same_json(value, reloaded[key], f"{path}.{key}")
    elif isinstance(original, list | tuple):
        if not isinstance(reloaded, list) or len(reloaded) != len(original):
            raise ParityFixtureError(f"{path}: list changed in JSON")
        for index, (a, b) in enumerate(zip(original, reloaded, strict=True)):
            _same_json(a, b, f"{path}[{index}]")
    elif type(original) is not type(reloaded) or original != reloaded:
        raise ParityFixtureError(f"{path}: {original!r} did not survive JSON ({reloaded!r})")


def _dump(document: Mapping[str, object]) -> str:
    """Compact JSON with each float written as its shortest round-trip repr, checked on reload."""
    text = json.dumps(document, allow_nan=False, ensure_ascii=True, separators=(",", ":")) + "\n"
    _same_json(document, json.loads(text), "$")
    return text


def parity_fixture_texts(
    *, config_path: Path = DEFAULT_RUN_CONFIG, repo_root: Path = REPOSITORY_ROOT
) -> dict[str, str]:
    """The contents of :data:`PARITY_FILE_NAMES`, keyed by file name (PRD 8.8).

    Every input is synthetic (``data_kind`` is ``synthetic``). Each file carries the schema
    version, the producing commit (:func:`producing_commit`), the sha256 of the run config,
    the numpy version, the PRD 8.8 tolerances and the sha256 of every input. Settings come from
    ``config_path`` through :func:`terraluna_pipeline.config.load_terrain_config`.
    """
    config = load_terrain_config(config_path)
    run_config = load_run_config(config_path)
    commit = producing_commit(repo_root)
    resolved = config_path.resolve()
    try:
        config_name = resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        config_name = resolved.as_posix()

    def header(description: str) -> dict[str, object]:
        return {
            "schema_version": PARITY_SCHEMA_VERSION,
            "data_kind": "synthetic",
            "description": description,
            "generator": "python -m terraluna_pipeline.terrain",
            "producing_commit": commit,
            "producing_sources": list(PARITY_SOURCES),
            "config_path": config_name,
            "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "numpy_version": np.__version__,
            "sha256_rule": _SHA256_RULE,
            "tolerances": {key: dict(value) for key, value in PARITY_TOLERANCES.items()},
            "tolerance_fields": {
                key: list(paths) for key, paths in PARITY_TOLERANCE_FIELDS.items()
            },
            "exact_fields": (
                "every mask, count, reason, label, offset and null is compared exactly"
            ),
        }

    grid = window_grid(config.window_m, config.analysis_grid_m)
    terrain_document = header(
        "Python reference outputs on synthetic geographic source rasters: D29 resampled grids, "
        "nu per lag and direction, ln nu, H or its reason, supported lags and valid fraction. "
        "Null marks an unavailable value, never 0; ln nu is null where nu is null or 0."
    )
    terrain_document |= {
        "config": {
            "window_m": config.window_m,
            "analysis_grid_m": config.analysis_grid_m,
            "lags_m": list(config.lags_m),
            "min_lags_for_H": config.min_lags_for_H,
            "min_lag_span_factor": config.min_lag_span_factor,
            "min_valid_fraction": config.min_valid_fraction,
            "detrend": list(config.detrend),
            "resampler": config.resampler,
            "coarsening_factor": 1.0,
            "isotropic_rules": list(PARITY_ISOTROPIC_RULES),
        },
        "grid": _window_grid_json(
            "analysis", grid, ("targets.window_m", "terrain.analysis_grid_m")
        ),
        "structure_lags_m": list(_structure_lags(config)),
        "cases": [_terrain_case_json(spec, config, grid) for spec in _PARITY_CASES],
    }

    input_px = run_config.value("ml.input_px")
    input_m_per_px = run_config.value("ml.input_m_per_px")
    if isinstance(input_px, bool) or not isinstance(input_px, int):
        raise ParityFixtureError(f"ml.input_px must be an integer, got {input_px!r}")
    encoder_spacing = _positive_length("ml.input_m_per_px", input_m_per_px)
    encoder = window_grid(input_px * encoder_spacing, encoder_spacing)
    if encoder.size != input_px:
        raise ParityFixtureError(f"the encoder grid has {encoder.size} cells, not {input_px}")
    geometry_document = header(
        "Python reference outputs of the D29 geometry: sphere radii, window pixel centres and "
        "inverse then forward spherical AEQD round trips about centres on each body."
    )
    geometry_document |= {
        "radii_m": dict(BODY_RADII_M),
        "window_grids": [
            _window_grid_json("analysis", grid, ("targets.window_m", "terrain.analysis_grid_m")),
            _window_grid_json("encoder", encoder, ("ml.input_px", "ml.input_m_per_px")),
        ],
        "aeqd_cases": _aeqd_cases_json(grid),
    }
    terrain_name, geometry_name = PARITY_FILE_NAMES
    return {terrain_name: _dump(terrain_document), geometry_name: _dump(geometry_document)}


def write_parity_fixtures(
    output_dir: Path = DEFAULT_PARITY_DIR,
    *,
    config_path: Path = DEFAULT_RUN_CONFIG,
    repo_root: Path = REPOSITORY_ROOT,
) -> tuple[Path, ...]:
    """Write :data:`PARITY_FILE_NAMES` into ``output_dir`` and return their paths, in order."""
    texts = parity_fixture_texts(config_path=config_path, repo_root=repo_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, text in texts.items():
        path = output_dir / name
        path.write_bytes(text.encode("utf-8"))
        written.append(path)
    return tuple(written)


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m terraluna_pipeline.terrain [--output DIR]``: write the parity fixtures."""
    parser = argparse.ArgumentParser(
        prog="python -m terraluna_pipeline.terrain",
        description="Write the synthetic parity fixtures (PRD 8.8) for the TypeScript mirror.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_PARITY_DIR)
    arguments = parser.parse_args(argv)
    try:
        written = write_parity_fixtures(arguments.output)
    except ParityFixtureError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
