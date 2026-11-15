"""Synthetic fractal and end-to-end window tests for the terrain fingerprint (PRD 6.3, 7, 9, 12 T3).

These tests close the PRD 12 T3 test list: a synthetic fractal, and a chain from a synthetic
source raster through the D29 window resampler (PRD 7) to :func:`terrain_fingerprint`. Every
input is generated here; no real DEM is read (see ``fixtures/terrain/README.md``). Settings come
from ``config/run.yaml`` through :func:`load_terrain_config`: a 5000 m window on the 90 m grid
(55 x 55 cells) and lags of 270 to 1080 m (3 to 12 cells).

Fractal (fractional Brownian surface, fBm)
    ``fbm_window`` draws a seeded fBm surface by spectral synthesis. Its target is fBm sampled at
    the grid points, ``E (z(p + r) - z(p))^2 = NU_REF_M^2 |r|^(2H)`` with ``r`` in cells, whose
    generalised spectral density is ``S(q) = c_H |q|^-(2H + 2)`` (q in radians per cell) with
    ``c_H = NU_REF_M^2 / (4 pi I(H))`` and ``I(H) = 2^(-2H-1) Gamma(1 - H) / (H Gamma(1 + H))``
    (``integral_0^inf u^(-2H-1) (1 - J0(u)) du``). White Gaussian noise on a periodic
    ``FBM_CELLS`` square is filtered by the square root of the lattice spectrum and the centre
    55 x 55 cells are kept. Two approximations, both fixed before the first run:

    - Point sampling folds every alias ``q + 2 pi m`` into the lattice spectrum. Aliases with
      ``|m_x|, |m_y| <= ALIAS_ORDER`` are summed; the rest enter as their integral over the outside
      of the square of half-width ``a = (2 ALIAS_ORDER + 1) pi``, divided by the cell area
      ``(2 pi)^2``: ``8 J(2H + 2) a^(-2H) / (2H)`` times ``c_H / (2 pi)^2``, with
      ``J(p) = integral_0^1 (1 + t^2)^(-p/2) dt``. Without the fold, the ensemble slope at H = 0.3
      is 0.364 (the power beyond the Nyquist frequency is 26% of nu^2 at 3 cells).
    - Frequencies below the lowest lattice frequency (the central cell ``|q_x|, |q_y| < pi /
      FBM_CELLS``) act on a 55-cell window as a plane. They enter as a random tilt of variance
      ``c_H 4 J(2H) (pi / FBM_CELLS)^(2 - 2H) / (2 - 2H)`` per axis (metres per cell squared).
      Without it, the ensemble nu at H = 0.8 is 9.6% low at 12 cells and its slope is 0.768.

    The generator's ensemble nu is computed exactly from its spectrum and checked against the
    power law to ``GENERATOR_RTOL``; that is what makes H "known". The same seed gives the same
    white noise and tilt draws for every H (common random numbers), so the order test compares
    surfaces that differ only in H. numpy's Generator streams are reproducible for one numpy
    build (uv.lock pins numpy); a feature release may change them.

Chain
    A synthetic equirectangular raster on the Moon sphere (PRD 7 radius 1,737,400 m) holds a
    plane plus a sinusoid, ``z = BASE_M + SLOPE_EAST E + SLOPE_NORTH N + WAVE_M sin(WAVE_K_EAST E
    + WAVE_K_NORTH N + WAVE_PHASE)``, at pixel centres every ``SOURCE_SPACING_M`` with (E, N) = (0,
    0) at the window centre (equator, longitude ``CENTRE_LON_DEG``, standard parallel 0). It is
    resampled onto the 90 m window by :func:`resample_window` with ``body="moon"`` and
    fingerprinted. The analytic nu is the same surface evaluated in closed form at the window's
    pixel centres (x, y), its pair differences taken exactly, as if (E, N) = (x, y).

    Derived tolerance. nu along one direction is a seminorm of the cell values over its pairs, and
    the isotropic summaries (direction mean, pooled pairs) are seminorms of the stacked pair
    differences, so ``|nu(z + e) - nu(z)| <= nu(e) <= 2 max |e|`` for any error field ``e``. Here
    ``max |e| <= E_interp + E_map + FLOAT_ALLOWANCE_M``:

    - ``E_interp = SOURCE_SPACING_M^2 / 8 * WAVE_M (WAVE_K_EAST^2 + WAVE_K_NORTH^2)``: bilinear
      interpolation is exact on the plane, and on a tensor cell its error is at most
      ``h_E^2 / 8 max |z_EE| + h_N^2 / 8 max |z_NN|``.
    - ``E_map = |grad z|_max * shift``: the inverse AEQD at the equator gives E = R atan((x / rho)
      tan c) and N = R asin((y / rho) sin c) with c = rho / R, so ``|E - x| <= 1.001 |x| (rho^2 +
      x^2) / (3 R^2)`` and ``|N - y| <= 1.001 |y| (rho^2 + y^2) / (6 R^2)`` (Taylor remainders;
      the series' next terms are below c^2 = 4e-6 of the first). At the corner pixel that is 5.5
      mm, and ``|grad z| <= hypot(SLOPE_EAST, SLOPE_NORTH) + WAVE_M hypot(WAVE_K_EAST,
      WAVE_K_NORTH)``.
    - ``FLOAT_ALLOWANCE_M = 1e-9 m``: float64 rounding of source values (about 1.4 km, ulp 2.3e-13
      m), window positions (1e-12 m, PRD 7) and the bilinear sums stays near 1e-11 m.

    The tolerance on nu is ``2 (E_interp + E_map + FLOAT_ALLOWANCE_M)``, about 0.012 m against nu
    of 9 to 55 m. A tolerance on relief H follows from it, since ``|ln a - ln b| <= |a - b| /
    min(a, b)`` and the least-squares slope is linear in ln nu.

    Window centres sit midway between source pixel centres, and the sphere moves them by at most
    5.5 mm, so each window cell interpolates exactly the four source pixels at x +- 5 m, y +- 5 m.
    That gives the cells a nodata block in the source must invalidate, independently of the
    resampler.
"""

from __future__ import annotations

import dataclasses
import functools
import math
from collections.abc import Iterator, Mapping, Sequence
from typing import Final

import numpy as np
import numpy.typing as npt
import pytest

from terraluna_pipeline import terrain
from terraluna_pipeline.config import load_terrain_config
from terraluna_pipeline.geometry import (
    EquirectangularLookup,
    ResampledGrid,
    resample_window,
    window_grid,
)
from terraluna_pipeline.terrain import (
    Direction,
    DirectionalNu,
    HurstFit,
    IsotropicNu,
    IsotropicRule,
    LagSupport,
    PlaneFit,
    StructureFunction,
    TerrainFingerprint,
    TerrainVariant,
    terrain_fingerprint,
)

pytestmark = pytest.mark.filterwarnings("error")

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]
Block = tuple[tuple[float, float], tuple[float, float]]

CONFIG = load_terrain_config()
GRID = window_grid(CONFIG.window_m, CONFIG.analysis_grid_m)
LAG_CELLS: Final = tuple(round(lag / CONFIG.analysis_grid_m) for lag in CONFIG.lags_m)
RULES: Final[tuple[IsotropicRule, ...]] = ("direction_mean", "pooled_pairs")


# --- Fractal: declared settings and tolerances (fixed before the first run) -------------------

HURST_VALUES: Final = (0.3, 0.5, 0.8)
"""Generating H values (story T3-06)."""
SEEDS: Final = (0, 1, 2, 3, 4, 5, 6, 7)
"""Fixed seeds: eight surfaces per H (the order test needs at least five)."""
FBM_CELLS: Final = 1024
"""Side of the periodic synthesis square, in 90 m cells (92 km); the window is its centre."""
ALIAS_ORDER: Final = 3
"""Aliases with |m_x|, |m_y| <= 3 are summed; the rest enter as their integral."""
NU_REF_M: Final = 1.0
"""Target nu at one analysis cell (90 m). H does not depend on it."""

GENERATOR_RTOL: Final = 0.03
"""Declared before the first run: the generator's ensemble nu at each configured lag and axis
lies within 3% of ``NU_REF_M (L / 90 m)^H``.

Reason: the alias tail enters as its integral (estimated under 0.5% of nu at 3 cells) and the
central cell as a tilt; the Riemann sum over the first ring of lattice frequencies around it
still misses about 1 to 2% of nu^2 at 12 cells for H = 0.8. A 3% band keeps the generator's own
log-log slope within ln(1.03 / 0.97) / ln 4 = 0.04 of H in the worst case.
"""

H_TOLERANCE: Final = 0.1
"""Declared before the first run and not to be changed after seeing results (story T3-06).

The PRD sets no tolerance for recovered H. Recovered H is the mean, over ``SEEDS``, of the relief
H that :func:`terrain_fingerprint` fits on one 5000 m window at 90 m. Why it cannot be tighter:

- Finite window: 55 cells hold only 4.6 lengths of the longest lag, so the large-scale modes of
  one fBm realisation tilt and bend nu at 720 to 1080 m. At H = 0.8 increments are long-range
  correlated and one window's H spreads by an estimated 0.05 to 0.1; eight seeds divide that by
  sqrt(8).
- Lag range: five lags spanning only a factor of 4 (ln 4 = 1.39), so a 5% error in nu at one end
  of the range moves the least-squares slope by about 0.03.
- The logarithm of a mean of squares is biased low where few independent pairs exist (the long
  lags), which lowers H by an estimated 0.01; and the generator itself is fBm only to
  ``GENERATOR_RTOL``.

0.1 is half the smallest gap between generating values (0.3 and 0.5), so a recovered H within it
is nearer its own generating value than any other.
"""


# --- Fractal: generator (spectral synthesis) --------------------------------------------------


def octant_integral(exponent: float) -> float:
    """``J(p) = integral_0^1 (1 + t^2)^(-p/2) dt``, by 40-point Gauss-Legendre quadrature.

    Over the outside (or inside) of a square of half-width ``a``, ``|q|^-p`` integrates to
    ``8 J(p) a^(2 - p) / (p - 2)`` (or ``8 J(p) a^(2 - p) / (2 - p)``): one octant is x from a
    (or 0) on, y from 0 to x, and y = x t separates the variables. The integrand is analytic on
    [0, 1] with its nearest poles at t = +-i, so 40 nodes are exact to float64.
    """
    nodes, weights = np.polynomial.legendre.leggauss(40)
    t = (nodes + 1.0) / 2.0
    return float(np.sum(weights * (1.0 + t * t) ** (-exponent / 2.0))) / 2.0


def fbm_scale(hurst: float) -> float:
    """``c_H`` in ``S(q) = c_H |q|^-(2H + 2)`` for nu = NU_REF_M at one cell (module docstring)."""
    integral = (
        math.pow(2.0, -2.0 * hurst - 1.0)
        * math.gamma(1.0 - hurst)
        / (hurst * math.gamma(1.0 + hurst))
    )
    return NU_REF_M**2 / (4.0 * math.pi * integral)


@functools.cache
def lattice_amplitude(hurst: float) -> FloatArray:
    """Filter ``2 pi sqrt(S_lattice)`` on the rfft2 half-plane of the synthesis square.

    ``S_lattice(k)`` is the fBm spectrum folded onto the lattice frequencies k (radians per cell,
    axis 0 from ``fftfreq``, axis 1 from ``rfftfreq``): the aliases with ``|m| <= ALIAS_ORDER``
    summed and the rest as their integral. White noise of unit variance has ``E |rfft2|^2 =
    FBM_CELLS^2`` in every bin, so this filter gives each lattice mode the variance
    ``S_lattice(k) (2 pi / FBM_CELLS)^2`` of its frequency cell. The k = 0 mode is the window
    mean and is set to 0; the frequencies of its cell are drawn as a tilt (:func:`tilt_sd`).
    """
    beta = 2.0 * hurst + 2.0
    two_pi = 2.0 * math.pi
    kx = two_pi * np.fft.fftfreq(FBM_CELLS)
    ky = two_pi * np.fft.rfftfreq(FBM_CELLS)
    folded = np.zeros((kx.size, ky.size), dtype=np.float64)
    for mx in range(-ALIAS_ORDER, ALIAS_ORDER + 1):
        qx2 = ((kx + two_pi * mx) ** 2)[:, np.newaxis]
        for my in range(-ALIAS_ORDER, ALIAS_ORDER + 1):
            q2 = qx2 + ((ky + two_pi * my) ** 2)[np.newaxis, :]
            if mx == 0 and my == 0:
                q2[0, 0] = 1.0  # q = 0 has no power of its own; the k = 0 mode is zeroed below
            folded += q2 ** (-beta / 2.0)
    half_width = (2 * ALIAS_ORDER + 1) * math.pi
    tail = 8.0 * octant_integral(beta) * math.pow(half_width, -2.0 * hurst) / (2.0 * hurst)
    spectrum = fbm_scale(hurst) * (folded + tail / two_pi**2)
    spectrum[0, 0] = 0.0
    amplitude: FloatArray = two_pi * np.sqrt(spectrum)
    amplitude.setflags(write=False)
    return amplitude


def tilt_sd(hurst: float) -> float:
    """Standard deviation of each tilt component, metres per cell.

    The central frequency cell ``|q_x|, |q_y| < eps = pi / FBM_CELLS`` adds ``integral S(q) (q .
    r)^2 d^2q = r^2 integral S q_x^2 d^2q`` to the ensemble ``(z(p + r) - z(p))^2`` at small
    ``q r``, which is the variance of ``g . r`` for a random gradient g with ``E g_x^2 = E g_y^2 =
    c_H / 2 integral |q|^(-2H) d^2q = c_H 4 J(2H) eps^(2 - 2H) / (2 - 2H)``.
    """
    half_cell = math.pi / FBM_CELLS
    variance = (
        fbm_scale(hurst)
        * 4.0
        * octant_integral(2.0 * hurst)
        * math.pow(half_cell, 2.0 - 2.0 * hurst)
        / (2.0 - 2.0 * hurst)
    )
    return math.sqrt(variance)


def draw_fbm_window(hurst: float, seed: int) -> FloatArray:
    """A seeded fBm window, metres on the 55 x 55 analysis grid (read-only).

    White noise from ``numpy.random.default_rng(seed)`` is filtered by
    :func:`lattice_amplitude` on the periodic synthesis square, the centre window is cut out,
    and a tilt drawn from the same generator is added. The draws do not depend on H.
    """
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal((FBM_CELLS, FBM_CELLS))
    field = np.fft.irfft2(np.fft.rfft2(noise) * lattice_amplitude(hurst), s=(FBM_CELLS, FBM_CELLS))
    size = GRID.size
    start = (FBM_CELLS - size) // 2
    gradient_east, gradient_north = rng.standard_normal(2) * tilt_sd(hurst)
    offsets = np.arange(size, dtype=np.float64) - (size - 1) / 2.0
    window: FloatArray = (
        field[start : start + size, start : start + size]
        + gradient_east * offsets[np.newaxis, :]
        - gradient_north * offsets[:, np.newaxis]
    )
    window.setflags(write=False)
    return window


@functools.cache
def fbm_window(hurst: float, seed: int) -> FloatArray:
    return draw_fbm_window(hurst, seed)


@functools.cache
def generator_covariance(hurst: float) -> FloatArray:
    """Ensemble covariance of the periodic field by lag in cells, ``[row, column]``."""
    covariance: FloatArray = np.fft.irfft2(lattice_amplitude(hurst) ** 2, s=(FBM_CELLS, FBM_CELLS))
    return covariance


def generator_expected_nu(hurst: float, cells: int, axis: str) -> float:
    """The generator's ensemble nu at an axis lag of ``cells``: periodic field plus tilt."""
    covariance = generator_covariance(hurst)
    shifted = covariance[0, cells] if axis == "x" else covariance[cells, 0]
    variance = 2.0 * (covariance[0, 0] - shifted) + tilt_sd(hurst) ** 2 * cells**2
    return math.sqrt(variance)


@functools.cache
def fractal_fingerprint(hurst: float, seed: int) -> TerrainFingerprint:
    values = fbm_window(hurst, seed)
    return terrain_fingerprint(
        values,
        valid=np.ones(values.shape, dtype=np.bool_),
        grid=GRID,
        config=CONFIG,
        isotropic_rule="direction_mean",
        coarsening_factor=1.0,
    )


def relief_h(hurst: float, seed: int) -> float:
    fit = fractal_fingerprint(hurst, seed).variants["none"].hurst
    assert fit.H is not None, f"H = {hurst}, seed {seed}: H unavailable ({fit.reason})"
    return fit.H


# --- Fractal: tests ---------------------------------------------------------------------------


def test_fractal_runs_on_the_configured_window() -> None:
    assert CONFIG.window_m == 5000.0
    assert CONFIG.analysis_grid_m == 90.0
    assert GRID.size == 55
    assert LAG_CELLS == (3, 4, 6, 8, 12)
    assert all(cells * 90.0 == lag for cells, lag in zip(LAG_CELLS, CONFIG.lags_m, strict=True))


def test_generator_is_seeded() -> None:
    first = fbm_window(0.5, SEEDS[0])
    assert first.shape == (GRID.size, GRID.size)
    assert first.dtype == np.float64
    assert np.all(np.isfinite(first))
    assert not first.flags.writeable
    again = draw_fbm_window(0.5, SEEDS[0])
    assert again is not first
    np.testing.assert_array_equal(again, first)
    assert not np.array_equal(fbm_window(0.5, SEEDS[1]), first)


@pytest.mark.parametrize("hurst", HURST_VALUES)
def test_generator_ensemble_nu_follows_the_power_law(hurst: float) -> None:
    for cells in LAG_CELLS:
        target = NU_REF_M * cells**hurst
        for axis in ("x", "y"):
            expected = generator_expected_nu(hurst, cells, axis)
            assert abs(expected / target - 1.0) <= GENERATOR_RTOL, (hurst, cells, axis, expected)


@pytest.mark.parametrize("hurst", HURST_VALUES)
def test_recovered_h_is_within_the_declared_tolerance(hurst: float) -> None:
    recovered = []
    for seed in SEEDS:
        fingerprint = fractal_fingerprint(hurst, seed)
        relief = fingerprint.variants["none"]
        assert relief.name == "relief"
        assert fingerprint.supported_lags_m == CONFIG.lags_m
        assert relief.hurst.label == "Derived"
        recovered.append(relief_h(hurst, seed))
    mean = math.fsum(recovered) / len(recovered)
    assert abs(mean - hurst) <= H_TOLERANCE, (hurst, mean, recovered)


@pytest.mark.parametrize("seed", SEEDS)
def test_recovered_h_is_monotone_in_the_generating_h(seed: int) -> None:
    recovered = [relief_h(hurst, seed) for hurst in HURST_VALUES]
    assert recovered[0] < recovered[1] < recovered[2], (seed, recovered)


# --- Chain: declared source and tolerance -----------------------------------------------------

MOON_RADIUS_M: Final = 1737400.0
"""The D29 Moon sphere (PRD 7), used for the source raster's own sphere."""
SOURCE_SPACING_M: Final = 10.0
SOURCE_HALF_PIXELS: Final = 260
"""The source has 2 * 260 + 1 pixel centres per side, at -2600 m to 2600 m."""
CENTRE_LON_DEG: Final = 30.0
BASE_M: Final = 1200.0
SLOPE_EAST: Final = 0.04
SLOPE_NORTH: Final = -0.025
WAVE_M: Final = 12.0
WAVE_K_EAST: Final = 2.0 * math.pi / 1250.0
WAVE_K_NORTH: Final = 2.0 * math.pi / 2000.0
WAVE_PHASE: Final = 0.7
NODATA: Final = -32768.0
FLOAT_ALLOWANCE_M: Final = 1e-9

SMALL_BLOCK: Final[Block] = ((-600.0, 0.0), (-300.0, 600.0))
"""Source pixel centres set to nodata, ((east min, east max), (north min, north max)) in metres:
70 window cells (7 columns x 10 rows) lose a neighbour, so the window stays measured."""
LARGE_BLOCK: Final[Block] = ((-2000.0, 1000.0), (-1000.0, 1000.0))
"""748 window cells (34 x 22) lose a neighbour: 2277 / 3025 valid, below min_valid_fraction."""


def plane_and_wave_m(east: FloatArray, north: FloatArray) -> FloatArray:
    """The synthetic surface in metres at source coordinates (E, N) in metres."""
    wave = WAVE_M * np.sin(WAVE_K_EAST * east + WAVE_K_NORTH * north + WAVE_PHASE)
    surface: FloatArray = BASE_M + SLOPE_EAST * east + SLOPE_NORTH * north + wave
    return surface


def equirectangular_source(block: Block | None) -> tuple[FloatArray, EquirectangularLookup]:
    """The synthetic source raster and its lookup, with ``block`` set to NODATA.

    Pixel centres sit at E = (column - 260) * 10 m and N = (260 - row) * 10 m, so the north-up
    geotransform's outer corner is at (-2605 m, 2605 m). The raster's sphere is the Moon's, its
    centre longitude the window's, and the standard parallel and latitude of origin 0.
    """
    centres = (
        np.arange(2 * SOURCE_HALF_PIXELS + 1, dtype=np.float64) - SOURCE_HALF_PIXELS
    ) * SOURCE_SPACING_M
    east = np.broadcast_to(centres[np.newaxis, :], (centres.size, centres.size))
    north = np.broadcast_to(-centres[:, np.newaxis], (centres.size, centres.size))
    raster = plane_and_wave_m(east, north)
    if block is not None:
        (east_min, east_max), (north_min, north_max) = block
        hit = (east >= east_min) & (east <= east_max) & (north >= north_min) & (north <= north_max)
        raster = np.where(hit, NODATA, raster)
    corner = (SOURCE_HALF_PIXELS + 0.5) * SOURCE_SPACING_M
    lookup = EquirectangularLookup(
        center_longitude_deg=CENTRE_LON_DEG,
        standard_parallel_deg=0.0,
        latitude_of_origin_deg=0.0,
        radius_m=MOON_RADIUS_M,
        false_easting_m=0.0,
        false_northing_m=0.0,
        origin_x_m=-corner,
        origin_y_m=corner,
        pixel_width_m=SOURCE_SPACING_M,
        pixel_height_m=-SOURCE_SPACING_M,
    )
    return raster, lookup


def window_positions() -> tuple[FloatArray, FloatArray]:
    """Pixel-centre x (columns) and y (rows) of the 90 m window, from PRD 7's grid formula."""
    centres = (np.arange(GRID.size, dtype=np.float64) + 0.5) * CONFIG.analysis_grid_m
    return centres - CONFIG.window_m / 2.0, CONFIG.window_m / 2.0 - centres


def expected_valid(block: Block | None) -> BoolArray:
    """Window cells none of whose four source neighbours (x +- 5 m, y +- 5 m) is in ``block``."""
    x, y = window_positions()
    if block is None:
        return np.ones((GRID.size, GRID.size), dtype=np.bool_)
    (east_min, east_max), (north_min, north_max) = block
    half = SOURCE_SPACING_M / 2.0
    columns = (x - half <= east_max) & (x + half >= east_min)
    rows = (y - half <= north_max) & (y + half >= north_min)
    hit: BoolArray = rows[:, np.newaxis] & columns[np.newaxis, :]
    return ~hit


def chain_window(block: Block | None = None, body: str = "moon") -> ResampledGrid:
    raster, lookup = equirectangular_source(block)
    return resample_window(
        raster,
        lookup,
        grid=GRID,
        body=body,
        lat0_rad=0.0,
        lon0_rad=math.radians(CENTRE_LON_DEG),
        nodata=NODATA,
    )


def chain_fingerprint(window: ResampledGrid, rule: IsotropicRule) -> TerrainFingerprint:
    return terrain_fingerprint(
        window.values,
        valid=window.valid,
        grid=GRID,
        config=CONFIG,
        isotropic_rule=rule,
        coarsening_factor=1.0,
    )


def _rms(values: FloatArray) -> float:
    return math.sqrt(math.fsum((values * values).tolist()) / values.size)


def analytic_nu(cells: int, valid: BoolArray, rule: IsotropicRule) -> tuple[float, float, float]:
    """Analytic (nu_x, nu_y, isotropic nu) of the surface over the window pairs both valid."""
    x, y = window_positions()
    phase = WAVE_K_EAST * x[np.newaxis, :] + WAVE_K_NORTH * y[:, np.newaxis] + WAVE_PHASE
    wave = WAVE_M * np.sin(phase)
    lag = cells * CONFIG.analysis_grid_m
    # East pairs: (row, column) and (row, column + cells). North pairs: the north cell (row) minus
    # the south cell (row + cells), whose y is lower by the lag.
    east = SLOPE_EAST * lag + (wave[:, cells:] - wave[:, :-cells])
    east_pairs = valid[:, cells:] & valid[:, :-cells]
    north = SLOPE_NORTH * lag + (wave[:-cells, :] - wave[cells:, :])
    north_pairs = valid[:-cells, :] & valid[cells:, :]
    nu_x = _rms(east[east_pairs])
    nu_y = _rms(north[north_pairs])
    if rule == "direction_mean":
        nu = math.sqrt((nu_x**2 + nu_y**2) / 2.0)
    else:
        n_x, n_y = int(np.count_nonzero(east_pairs)), int(np.count_nonzero(north_pairs))
        nu = math.sqrt((n_x * nu_x**2 + n_y * nu_y**2) / (n_x + n_y))
    return nu_x, nu_y, nu


def chain_nu_tolerance() -> float:
    """``2 (E_interp + E_map + FLOAT_ALLOWANCE_M)`` in metres (module docstring)."""
    interpolation = SOURCE_SPACING_M**2 / 8.0 * WAVE_M * (WAVE_K_EAST**2 + WAVE_K_NORTH**2)
    x, y = window_positions()
    east = np.abs(x)[np.newaxis, :]
    north = np.abs(y)[:, np.newaxis]
    rho2 = east**2 + north**2
    shift_east = 1.001 * east * (rho2 + east**2) / (3.0 * MOON_RADIUS_M**2)
    shift_north = 1.001 * north * (rho2 + north**2) / (6.0 * MOON_RADIUS_M**2)
    shift = float(np.max(np.hypot(shift_east, shift_north)))
    gradient = math.hypot(SLOPE_EAST, SLOPE_NORTH) + WAVE_M * math.hypot(WAVE_K_EAST, WAVE_K_NORTH)
    return 2.0 * (interpolation + gradient * shift + FLOAT_ALLOWANCE_M)


def chain_h_tolerance(nu: Sequence[float], nu_tolerance: float) -> float:
    """Bound on |H(nu') - H(nu)| when every |nu'_i - nu_i| <= nu_tolerance."""
    logs = np.log(np.asarray(CONFIG.lags_m, dtype=np.float64))
    centred = logs - logs.mean()
    weights = centred / float(np.sum(centred**2))
    log_errors = nu_tolerance / (np.asarray(nu, dtype=np.float64) - nu_tolerance)
    return float(np.sum(np.abs(weights) * log_errors)) + 1e-12


NU_TOLERANCE_M: Final = chain_nu_tolerance()


# --- Chain: tests -----------------------------------------------------------------------------


def test_chain_tolerance_is_tight_against_nu() -> None:
    # The derivation gives about 0.012 m; every analytic nu is at least 9 m.
    full = expected_valid(None)
    smallest = min(min(analytic_nu(cells, full, "direction_mean")) for cells in LAG_CELLS)
    assert 0.0 < NU_TOLERANCE_M < 0.01 * smallest


def test_chain_window_is_inside_the_source_and_fully_valid() -> None:
    window = chain_window()
    assert window.values.shape == (GRID.size, GRID.size)
    assert np.all(window.inside)
    np.testing.assert_array_equal(window.valid, expected_valid(None))


@pytest.mark.parametrize("rule", RULES)
def test_chain_relief_nu_matches_the_analytic_surface(rule: IsotropicRule) -> None:
    fingerprint = chain_fingerprint(chain_window(), rule)
    assert fingerprint.excluded_reason is None
    assert fingerprint.valid_fraction == 1.0
    assert fingerprint.supported_lags_m == CONFIG.lags_m
    relief = fingerprint.variants["none"]
    full = expected_valid(None)
    for i, cells in enumerate(LAG_CELLS):
        nu_x, nu_y, nu = analytic_nu(cells, full, rule)
        measured = relief.nu_m[i]
        assert measured is not None
        assert abs(measured - nu) <= NU_TOLERANCE_M, (cells, measured, nu)
        axes: tuple[tuple[Direction, float], ...] = (("x", nu_x), ("y", nu_y))
        for direction, expected in axes:
            value = relief.directional_nu_m[direction][i]
            assert value is not None
            assert abs(value - expected) <= NU_TOLERANCE_M, (direction, cells, value, expected)
        # No diagonal offset has a configured lag's length on the 90 m grid (T3-04).
        assert relief.directional_nu_m["diagonal_ne"][i] is None
        assert relief.directional_nu_m["diagonal_nw"][i] is None


def test_chain_relief_h_matches_the_analytic_surface() -> None:
    fingerprint = chain_fingerprint(chain_window(), "direction_mean")
    full = expected_valid(None)
    nu = [analytic_nu(cells, full, "direction_mean")[2] for cells in LAG_CELLS]
    analytic_h = float(np.polyfit(np.log(CONFIG.lags_m), np.log(nu), 1)[0])
    measured = fingerprint.variants["none"].hurst.H
    assert measured is not None
    assert abs(measured - analytic_h) <= chain_h_tolerance(nu, NU_TOLERANCE_M)


@pytest.mark.parametrize("rule", RULES)
def test_chain_nodata_block_lowers_the_valid_fraction(rule: IsotropicRule) -> None:
    window = chain_window(SMALL_BLOCK)
    valid = expected_valid(SMALL_BLOCK)
    assert int(np.count_nonzero(~valid)) == 70
    np.testing.assert_array_equal(window.valid, valid)
    assert np.all(np.isnan(window.values[~valid]))

    fingerprint = chain_fingerprint(window, rule)
    assert fingerprint.valid_cells == GRID.size**2 - 70
    assert fingerprint.valid_fraction == (GRID.size**2 - 70) / GRID.size**2
    assert fingerprint.valid_fraction >= CONFIG.min_valid_fraction
    assert fingerprint.excluded_reason is None
    assert fingerprint.supported_lags_m == CONFIG.lags_m
    relief = fingerprint.variants["none"]
    for i, cells in enumerate(LAG_CELLS):
        _, _, nu = analytic_nu(cells, valid, rule)
        measured = relief.nu_m[i]
        assert measured is not None
        assert abs(measured - nu) <= NU_TOLERANCE_M, (cells, measured, nu)


def test_chain_nodata_block_drives_the_documented_exclusion() -> None:
    window = chain_window(LARGE_BLOCK)
    valid = expected_valid(LARGE_BLOCK)
    assert int(np.count_nonzero(~valid)) == 748
    np.testing.assert_array_equal(window.valid, valid)

    fingerprint = chain_fingerprint(window, "direction_mean")
    assert fingerprint.valid_cells == 2277
    assert fingerprint.valid_fraction == 2277 / 3025
    assert fingerprint.valid_fraction < CONFIG.min_valid_fraction
    assert fingerprint.excluded_reason == "below_min_valid_fraction"
    assert fingerprint.excluded
    assert fingerprint.supported_lags_m == ()
    assert all(item.reason == "window_excluded" for item in fingerprint.lags)
    for variant in fingerprint.variants.values():
        assert variant.reason == "window_excluded"
        assert variant.surface_m is None
        assert all(nu is None for nu in variant.nu_m)
        assert variant.hurst.H is None
        assert variant.hurst.reason == "window_excluded"
        assert variant.hurst.label == "Unavailable"
    # The reasons are the ones terrain.py documents.
    assert terrain.__doc__ is not None
    assert "below_min_valid_fraction" in terrain.__doc__
    assert "window_excluded" in terrain.__doc__


def test_chain_distances_are_metres_on_the_moon_sphere() -> None:
    fingerprint = chain_fingerprint(chain_window(), "direction_mean")
    assert fingerprint.window_m == 5000.0
    assert fingerprint.analysis_grid_m == 90.0
    assert fingerprint.lags_m == CONFIG.lags_m
    relief = fingerprint.variants["none"]
    assert relief.structure is not None
    for i, cells in enumerate(LAG_CELLS):
        assert relief.structure.directional["x"][i].offset_m == cells * 90.0
        assert relief.structure.directional["y"][i].offset_m == cells * 90.0
    # The same source resampled on the Mars sphere covers a different stretch of it, so the metre
    # lags of the window are those of the stated body's sphere.
    mars = chain_fingerprint(chain_window(body="mars"), "direction_mean")
    full = expected_valid(None)
    for i, cells in enumerate(LAG_CELLS):
        _, _, nu = analytic_nu(cells, full, "direction_mean")
        measured = mars.variants["none"].nu_m[i]
        assert measured is not None
        assert abs(measured - nu) > NU_TOLERANCE_M


RADAR_OR_MODEL_TOKENS: Final = frozenset(
    {
        "radar",
        "cpr",
        "sc",
        "oc",
        "sigma0",
        "gamma0",
        "dn",
        "stokes",
        "covariance",
        "incidence",
        "band",
        "polarization",
        "model",
        "encoder",
        "embedding",
        "onnx",
        "ood",
        "distance",
        "wasserstein",
        "score",
    }
)
"""Words of radar observables (PRD 6.2, 7), comparisons (6.5) and the encoder (8.12, 11)."""
NON_METRE_SUFFIXES: Final = ("_deg", "_rad", "_px", "_db", "_km")


def _walk(value: object) -> Iterator[tuple[type, str]]:
    """Every (record type, field name) in a fingerprint's tree of records."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield type(value), field.name
            yield from _walk(getattr(value, field.name))
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, tuple):
        for item in value:
            yield from _walk(item)


def test_fingerprint_has_no_radar_or_model_fields() -> None:
    fingerprint = chain_fingerprint(chain_window(), "direction_mean")
    fields = set(_walk(fingerprint))
    assert {owner for owner, _ in fields} == {
        TerrainFingerprint,
        LagSupport,
        TerrainVariant,
        PlaneFit,
        StructureFunction,
        DirectionalNu,
        IsotropicNu,
        HurstFit,
    }
    names = {name for _, name in fields}
    assert {"nu_m", "lags_m", "window_m", "analysis_grid_m", "offset_m"} <= names
    for name in names:
        assert not set(name.lower().split("_")) & RADAR_OR_MODEL_TOKENS, name
        assert not name.endswith(NON_METRE_SUFFIXES), name
