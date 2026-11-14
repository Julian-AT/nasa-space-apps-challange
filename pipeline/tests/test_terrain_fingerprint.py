"""Tests for terrain_fingerprint and the H fit (PRD 6.3, 7 Validity rules, 8.11, 12 T3, 3).

``terrain_fingerprint`` measures one metric analysis window twice, raw (**relief**, detrend
``none``) and with a best-fit plane removed (**texture**, detrend ``plane``). It keeps only
locally supported lags, fits the Hurst exponent H where PRD 6.3 allows and otherwise says why
not, with machine-readable reasons. Every threshold comes from ``config/run.yaml`` through
:func:`load_terrain_config`; the tests change one by building a new ``TerrainConfig`` with
:func:`dataclasses.replace`, never by editing the file.

Comparison operators, stated here because the PRD gives thresholds, not operators:

- A lag is at least three analysis cells if ``lag_m / spacing_m >= 3`` and at most a quarter of
  the window width if ``lag_m / window_m <= 0.25`` (PRD 6.3). Both quotients are rounded once to
  float64 and compared without any added tolerance.
- H needs ``count >= min_lags_for_H`` supported lags and ``max / min >= min_lag_span_factor``
  (PRD 6.3), again one rounded quotient and no tolerance. 270..1080 m is exactly 4x and passes.
- A window passes if ``valid_cells / total_cells >= min_valid_fraction`` (PRD 7 Validity rules
  and 8.13). A window whose fraction is exactly 0.8 (2420 of 3025 cells) passes.

The H estimator is a human decision (the PRD names none): the ordinary least-squares slope of
ln nu against ln L over the supported lags, every lag weighted equally. On an exact power law
nu = c L^H it returns H to PRD 7's arithmetic tolerance (rtol 1e-10).

References are written independently of the implementation: the exact plane fit is solved here
with ``fractions.Fraction`` by Gaussian elimination in metre coordinates (the module may use any
frame; least-squares residuals do not depend on it), the H fit is checked against
``numpy.polyfit`` and against exact power laws, and nu is checked against the T3-04
``structure_function`` on independently built grids.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
import yaml

from terraluna_pipeline.config import (
    TerrainConfig,
    UnknownDetrendError,
    load_terrain_config,
)
from terraluna_pipeline.geometry import WindowGrid, window_grid
from terraluna_pipeline.terrain import (
    DIRECTIONS,
    HurstFit,
    TerrainError,
    TerrainFingerprint,
    UnknownDetrendPolicyError,
    hurst_fit,
    structure_function,
    terrain_fingerprint,
)

pytestmark = pytest.mark.filterwarnings("error")

Array = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]

RTOL = 1e-10
ATOL = 1e-12

CONFIG = load_terrain_config()
REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_YAML = REPO_ROOT / "config" / "run.yaml"

FINGERPRINT_LAG_REASONS = {
    "below_min_lag_cells",
    "exceeds_quarter_window",
    "window_excluded",
    "lag_not_on_grid",
    "no_valid_pairs",
}
HURST_REASONS = {
    "window_excluded",
    "plane_fit_degenerate",
    "insufficient_lags",
    "insufficient_lag_span",
    "nu_zero",
}


def close(actual: float, expected: float, *, rtol: float = RTOL, atol: float = ATOL) -> bool:
    return abs(actual - expected) <= atol + rtol * abs(expected)


def grid_for(
    config: TerrainConfig = CONFIG, *, width_m: float | None = None, factor: float = 1.0
) -> WindowGrid:
    width = config.window_m if width_m is None else width_m
    return window_grid(width, config.analysis_grid_m * factor)


def run(
    values: npt.ArrayLike,
    valid: npt.ArrayLike | None = None,
    *,
    config: TerrainConfig = CONFIG,
    width_m: float | None = None,
    factor: float = 1.0,
    rule: str = "direction_mean",
) -> TerrainFingerprint:
    grid = grid_for(config, width_m=width_m, factor=factor)
    shape = np.shape(np.ma.getdata(values))
    keep = np.ones(shape, dtype=np.bool_) if valid is None else valid
    return terrain_fingerprint(
        values,
        valid=keep,
        grid=grid,
        config=config,
        isotropic_rule=rule,  # type: ignore[arg-type]
        coarsening_factor=factor,
    )


def mesh(
    config: TerrainConfig = CONFIG, *, width_m: float | None = None, factor: float = 1.0
) -> tuple[Array, Array]:
    return grid_for(config, width_m=width_m, factor=factor).mesh_m()


def rough_field(size: int, seed: int = 20261115) -> Array:
    """A deterministic rough surface with a tilt: noise plus a plane, in metres."""
    rng = np.random.default_rng(seed)
    rows, cols = np.mgrid[0:size, 0:size].astype(np.float64)
    noise = rng.normal(0.0, 7.5, size=(size, size))
    walk = np.cumsum(np.cumsum(rng.normal(0.0, 1.0, size=(size, size)), axis=0), axis=1)
    return 1200.0 + 0.8 * cols - 0.3 * rows + noise + walk


def size_of(
    config: TerrainConfig = CONFIG, *, width_m: float | None = None, factor: float = 1.0
) -> int:
    return grid_for(config, width_m=width_m, factor=factor).size


def with_config(**changes: Any) -> TerrainConfig:
    return dataclasses.replace(CONFIG, **changes)


# --- Configuration ----------------------------------------------------------------------------


def test_config_supplies_the_fingerprint_settings() -> None:
    # The tests below assume the PRD 8.13 values; they read them from the config file.
    assert CONFIG.analysis_grid_m == 90.0
    assert CONFIG.window_m == 5000.0
    assert CONFIG.lags_m == (270.0, 360.0, 540.0, 720.0, 1080.0)
    assert CONFIG.min_lags_for_H == 4
    assert CONFIG.min_lag_span_factor == 4.0
    assert CONFIG.min_valid_fraction == 0.8
    assert CONFIG.detrend == ("none", "plane")
    assert size_of() == 55


# --- Lag support ------------------------------------------------------------------------------


def test_all_configured_lags_are_kept_on_a_full_valid_5_km_window_at_90_m() -> None:
    result = run(rough_field(55))
    assert result.lags_m == CONFIG.lags_m
    assert result.supported_lags_m == CONFIG.lags_m
    assert [item.lag_m for item in result.lags] == list(CONFIG.lags_m)
    for item, cells in zip(result.lags, (3, 4, 6, 8, 12), strict=True):
        assert item.supported
        assert item.reason is None
        assert item.lag_cells == float(cells)
        # x and y each give 55 * (55 - k) pairs; the diagonals are off the 90 m grid.
        assert item.pair_count == 2 * 55 * (55 - cells)


def test_lag_below_three_cells_is_dropped_and_exactly_three_cells_is_kept() -> None:
    config = with_config(lags_m=(180.0, 269.99, 270.0, 360.0, 540.0, 720.0, 1080.0))
    result = run(rough_field(55), config=config)
    reasons = {item.lag_m: item.reason for item in result.lags}
    assert reasons[180.0] == "below_min_lag_cells"
    assert reasons[269.99] == "below_min_lag_cells"
    assert reasons[270.0] is None  # 270 / 90 == 3.0 and the rule is >= 3
    assert result.supported_lags_m == (270.0, 360.0, 540.0, 720.0, 1080.0)


def test_lag_beyond_a_quarter_of_the_window_is_dropped_and_exactly_a_quarter_is_kept() -> None:
    # A 3600 m window at 90 m: W / 4 = 900 m = 10 cells, and 990 m = 11 cells lies beyond it.
    config = with_config(lags_m=(270.0, 360.0, 540.0, 720.0, 900.0, 990.0))
    size = size_of(config, width_m=3600.0)
    assert size == 40
    result = run(rough_field(size), config=config, width_m=3600.0)
    reasons = {item.lag_m: item.reason for item in result.lags}
    assert reasons[900.0] is None  # 900 / 3600 == 0.25 and the rule is <= 0.25
    assert reasons[990.0] == "exceeds_quarter_window"
    assert result.supported_lags_m == (270.0, 360.0, 540.0, 720.0, 900.0)


def test_quarter_window_uses_the_nominal_5000_m_width() -> None:
    # The 55-cell grid covers 4950 m, but the rule is a quarter of the window width (1250 m).
    config = with_config(lags_m=(270.0, 1170.0, 1260.0))
    result = run(rough_field(55), config=config)
    reasons = {item.lag_m: item.reason for item in result.lags}
    assert reasons[1170.0] is None
    assert reasons[1260.0] == "exceeds_quarter_window"


def test_window_too_small_for_any_lag_keeps_no_lag_and_no_h() -> None:
    # 1000 m at 90 m is 11 cells, and a quarter window (250 m) is under three cells (270 m).
    size = size_of(width_m=1000.0)
    assert size == 11
    result = run(rough_field(size), width_m=1000.0)
    assert result.excluded_reason is None
    assert result.valid_fraction == 1.0
    assert result.supported_lags_m == ()
    assert [item.reason for item in result.lags] == ["exceeds_quarter_window"] * 5
    for variant in result.variants.values():
        assert variant.reason is None
        assert variant.structure is None
        assert variant.nu_m == (None,) * 5
        assert all(values == (None,) * 5 for values in variant.directional_nu_m.values())
        assert variant.hurst.H is None
        assert variant.hurst.reason == "insufficient_lags"


def test_lag_without_valid_pairs_is_dropped_with_a_reason() -> None:
    # A checkerboard pairs a valid cell only with an invalid one at odd axis offsets: 270 m is
    # 3 cells. The other lags are even and keep pairs. min_valid_fraction is lowered to 0.5 so
    # the window itself passes (1513 of 3025 cells).
    config = with_config(min_valid_fraction=0.5)
    rows, cols = np.indices((55, 55))
    valid = (rows + cols) % 2 == 0
    result = run(rough_field(55), valid, config=config)
    assert result.excluded_reason is None
    reasons = {item.lag_m: item.reason for item in result.lags}
    assert reasons[270.0] == "no_valid_pairs"
    assert result.supported_lags_m == (360.0, 540.0, 720.0, 1080.0)
    for variant in result.variants.values():
        assert variant.nu_m[0] is None
        assert all(nu is not None and nu > 0.0 for nu in variant.nu_m[1:])
        # Four lags, but 1080 / 360 = 3 < 4.
        assert variant.hurst.reason == "insufficient_lag_span"


def test_lag_not_on_a_coarsened_grid_is_dropped_with_a_reason() -> None:
    # Coarsened 1.5x to 135 m: 270 and 360 m are under 3 cells, 540 m is 4 cells, 720 m is
    # 5.33 cells (no whole-cell offset) and 1080 m is 8 cells.
    result = run(rough_field(size_of(factor=1.5)), factor=1.5)
    assert result.analysis_grid_m == 135.0
    assert result.configured_analysis_grid_m == 90.0
    assert result.coarsening_factor == 1.5
    reasons = [item.reason for item in result.lags]
    assert reasons == [
        "below_min_lag_cells",
        "below_min_lag_cells",
        None,
        "lag_not_on_grid",
        None,
    ]
    assert result.supported_lags_m == (540.0, 1080.0)
    for variant in result.variants.values():
        assert variant.hurst.reason == "insufficient_lags"


def test_coarsening_factor_is_recorded_and_used_for_lag_support() -> None:
    # Coarsened 2x to 180 m (27 cells): 270 and 360 m fall under three cells.
    size = size_of(factor=2.0)
    assert size == 27
    result = run(rough_field(size), factor=2.0)
    assert result.coarsening_factor == 2.0
    assert result.analysis_grid_m == 180.0
    assert result.grid_size == 27
    assert result.total_cells == 27 * 27
    assert result.supported_lags_m == (540.0, 720.0, 1080.0)
    assert [item.lag_cells for item in result.lags] == [1.5, 2.0, 3.0, 4.0, 6.0]
    for variant in result.variants.values():
        assert variant.hurst.reason == "insufficient_lags"  # 3 lags < 4


@pytest.mark.parametrize("factor", [0.5, 0.999, math.inf, math.nan, -1.0, 0.0])
def test_coarsening_factor_below_one_or_not_finite_raises(factor: float) -> None:
    # Below 1 would be upsampling, which is never treated as information (PRD 6.3).
    grid = window_grid(5000.0, 90.0)
    with pytest.raises(TerrainError):
        terrain_fingerprint(
            rough_field(55),
            valid=np.ones((55, 55), dtype=np.bool_),
            grid=grid,
            config=CONFIG,
            isotropic_rule="direction_mean",
            coarsening_factor=factor,
        )


@pytest.mark.parametrize("factor", [True, "2", None])
def test_coarsening_factor_must_be_a_real_number(factor: object) -> None:
    with pytest.raises(TerrainError):
        terrain_fingerprint(
            rough_field(55),
            valid=np.ones((55, 55), dtype=np.bool_),
            grid=window_grid(5000.0, 90.0),
            config=CONFIG,
            isotropic_rule="direction_mean",
            coarsening_factor=factor,  # type: ignore[arg-type]
        )


def test_grid_spacing_must_be_the_configured_grid_times_the_coarsening_factor() -> None:
    grid = window_grid(5000.0, 180.0)  # 2x coarser than configured
    with pytest.raises(TerrainError, match="spacing"):
        terrain_fingerprint(
            rough_field(grid.size),
            valid=np.ones((grid.size, grid.size), dtype=np.bool_),
            grid=grid,
            config=CONFIG,
            isotropic_rule="direction_mean",
            coarsening_factor=1.0,
        )


def test_values_must_have_the_grid_shape() -> None:
    with pytest.raises(TerrainError, match="shape"):
        terrain_fingerprint(
            rough_field(54),
            valid=np.ones((54, 54), dtype=np.bool_),
            grid=window_grid(5000.0, 90.0),
            config=CONFIG,
            isotropic_rule="direction_mean",
            coarsening_factor=1.0,
        )


# --- H gate -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lags", "reason"),
    [
        ((270.0, 540.0, 1080.0), "insufficient_lags"),  # 3 lags, span exactly 4
        ((270.0, 450.0, 630.0, 900.0), "insufficient_lag_span"),  # 4 lags, 900 / 270 = 3.33
        ((270.0, 360.0, 540.0, 720.0), "insufficient_lag_span"),  # 4 lags, 720 / 270 = 2.67
        ((270.0, 360.0, 540.0, 1080.0), None),  # 4 lags, 1080 / 270 == 4.0 exactly
        ((270.0, 360.0, 540.0, 720.0, 1080.0), None),  # the configured lags
    ],
)
def test_h_gate_on_lag_count_and_span(lags: tuple[float, ...], reason: str | None) -> None:
    result = run(rough_field(55), config=with_config(lags_m=lags))
    assert result.supported_lags_m == lags
    for variant in result.variants.values():
        assert variant.hurst.reason == reason
        if reason is None:
            assert variant.hurst.H is not None and math.isfinite(variant.hurst.H)
            assert variant.hurst.lags_m == lags
        else:
            assert variant.hurst.H is None
            assert variant.hurst.log_intercept is None


def test_h_gate_thresholds_are_read_from_config() -> None:
    field = rough_field(55)
    five = run(field, config=with_config(min_lags_for_H=6))
    assert all(v.hurst.reason == "insufficient_lags" for v in five.variants.values())
    assert five.min_lags_for_H == 6
    span = run(field, config=with_config(min_lag_span_factor=4.5))
    assert all(v.hurst.reason == "insufficient_lag_span" for v in span.variants.values())
    assert span.min_lag_span_factor == 4.5
    relaxed = with_config(lags_m=(270.0, 450.0, 630.0, 900.0), min_lag_span_factor=3.0)
    assert all(v.hurst.reason is None for v in run(field, config=relaxed).variants.values())


# --- H estimator ------------------------------------------------------------------------------


@pytest.mark.parametrize("hurst", [0.3, 0.5, 0.8])
@pytest.mark.parametrize("prefactor", [0.01, 1.7, 250.0])
@pytest.mark.parametrize(
    "lags", [CONFIG.lags_m, (270.0, 360.0, 540.0, 1080.0), (1.0, 4.0, 9.0, 16.0, 100.0)]
)
def test_h_fit_recovers_exact_power_laws(
    hurst: float, prefactor: float, lags: tuple[float, ...]
) -> None:
    nu = [prefactor * lag**hurst for lag in lags]
    fit = hurst_fit(lags, nu, min_lags_for_H=4, min_lag_span_factor=4.0)
    assert fit.reason is None
    assert fit.H is not None and fit.log_intercept is not None
    assert close(fit.H, hurst)
    assert close(fit.log_intercept, math.log(prefactor))
    assert fit.lags_m == tuple(lags)
    assert fit.nu_m == tuple(nu)


def test_h_fit_is_the_least_squares_slope_of_log_nu_on_log_lag() -> None:
    lags = (270.0, 360.0, 540.0, 720.0, 1080.0)
    nu = (3.1, 3.9, 6.0, 6.2, 9.7)  # not a power law
    slope, intercept = np.polyfit(np.log(lags), np.log(nu), 1)
    fit = hurst_fit(lags, nu, min_lags_for_H=4, min_lag_span_factor=4.0)
    assert fit.H is not None and fit.log_intercept is not None
    assert close(fit.H, float(slope))
    assert close(fit.log_intercept, float(intercept))


@pytest.mark.parametrize(
    ("lags", "nu", "reason"),
    [
        ((), (), "insufficient_lags"),
        ((270.0, 540.0, 1080.0), (1.0, 2.0, 3.0), "insufficient_lags"),
        ((270.0, 450.0, 630.0, 900.0), (1.0, 2.0, 3.0, 4.0), "insufficient_lag_span"),
        ((270.0, 360.0, 540.0, 1080.0), (1.0, 0.0, 3.0, 4.0), "nu_zero"),
        ((270.0, 360.0, 540.0, 1080.0), (0.0, 0.0, 0.0, 0.0), "nu_zero"),
    ],
)
def test_h_fit_gate_reasons(lags: tuple[float, ...], nu: tuple[float, ...], reason: str) -> None:
    fit = hurst_fit(lags, nu, min_lags_for_H=4, min_lag_span_factor=4.0)
    assert fit.reason == reason
    assert fit.H is None
    assert fit.log_intercept is None
    assert fit.label == "Unavailable"


def test_h_fit_precedence_is_count_then_span_then_zero() -> None:
    lags = (270.0, 360.0, 540.0)
    assert hurst_fit(lags, (0.0, 0.0, 0.0), min_lags_for_H=4, min_lag_span_factor=4.0).reason == (
        "insufficient_lags"
    )
    lags4 = (270.0, 360.0, 540.0, 720.0)
    zeros = (0.0,) * 4
    assert hurst_fit(lags4, zeros, min_lags_for_H=4, min_lag_span_factor=4.0).reason == (
        "insufficient_lag_span"
    )


def test_h_fit_never_evaluates_log_of_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []
    real_math_log = math.log
    real_np_log = np.log

    def spy_math(value: float, *args: float) -> float:
        seen.append(float(value))
        return real_math_log(value, *args)

    def spy_np(value: Any, *args: Any, **kwargs: Any) -> Any:
        seen.extend(np.ravel(np.asarray(value, dtype=np.float64)).tolist())
        return real_np_log(value, *args, **kwargs)

    monkeypatch.setattr(math, "log", spy_math)
    monkeypatch.setattr(np, "log", spy_np)
    lags = (270.0, 360.0, 540.0, 1080.0)
    fit = hurst_fit(lags, (1.0, 0.0, 3.0, 4.0), min_lags_for_H=4, min_lag_span_factor=4.0)
    assert fit.reason == "nu_zero"
    run(np.full((55, 55), 7.0))  # a constant window: nu is 0 at every lag
    assert 0.0 not in seen


@pytest.mark.parametrize(
    ("lags", "nu"),
    [
        ((270.0, 360.0), (1.0,)),  # lengths differ
        ((360.0, 270.0, 540.0, 1080.0), (1.0, 2.0, 3.0, 4.0)),  # not increasing
        ((270.0, 270.0, 540.0, 1080.0), (1.0, 2.0, 3.0, 4.0)),  # repeated
        ((0.0, 270.0, 540.0, 1080.0), (1.0, 2.0, 3.0, 4.0)),  # zero lag
        ((270.0, 360.0, 540.0, 1080.0), (1.0, -2.0, 3.0, 4.0)),  # negative nu
        ((270.0, 360.0, 540.0, 1080.0), (1.0, math.nan, 3.0, 4.0)),  # NaN nu
        ((270.0, 360.0, 540.0, 1080.0), (1.0, math.inf, 3.0, 4.0)),  # infinite nu
        ((270.0, 360.0, 540.0, 1080.0), (1.0, None, 3.0, 4.0)),  # missing nu
    ],
)
def test_h_fit_rejects_bad_inputs(lags: Sequence[float], nu: Sequence[Any]) -> None:
    with pytest.raises(TerrainError):
        hurst_fit(lags, nu, min_lags_for_H=4, min_lag_span_factor=4.0)


@pytest.mark.parametrize(
    ("min_lags", "factor"), [(1, 4.0), (True, 4.0), (4.0, 4.0), (4, 0.5), (4, math.nan)]
)
def test_h_fit_rejects_bad_gate_settings(min_lags: Any, factor: Any) -> None:
    lags = CONFIG.lags_m
    with pytest.raises(TerrainError):
        hurst_fit(lags, (1.0,) * 5, min_lags_for_H=min_lags, min_lag_span_factor=factor)


# --- Zero nu, planes and the two variants -----------------------------------------------------


def test_constant_window_gives_supported_zeros_and_h_nu_zero_for_both_variants() -> None:
    result = run(np.full((55, 55), 1234.5))
    assert result.supported_lags_m == CONFIG.lags_m
    for variant in result.variants.values():
        assert variant.nu_m == (0.0,) * 5
        assert variant.hurst.H is None
        assert variant.hurst.reason == "nu_zero"


def test_plane_has_relief_h_of_one_and_texture_h_unavailable_for_nu_zero() -> None:
    x, y = mesh()
    plane = 1000.0 + 0.5 * x + 0.25 * y  # exact in float64 on this grid
    result = run(plane)
    relief, texture = result.variants["none"], result.variants["plane"]
    assert relief.name == "relief" and texture.name == "texture"
    # Relief: nu_x = 0.5 L and nu_y = 0.25 L, so nu = sqrt((0.5^2 + 0.25^2) / 2) L.
    expected = [math.sqrt((0.5**2 + 0.25**2) / 2.0) * lag for lag in CONFIG.lags_m]
    assert relief.nu_m is not None
    for actual, wanted in zip(relief.nu_m, expected, strict=True):
        assert actual is not None and close(actual, wanted)
    assert relief.hurst.reason is None
    assert relief.hurst.H is not None and close(relief.hurst.H, 1.0)
    assert relief.plane is None
    # Texture: the plane is removed exactly, so nu is a supported 0 at every lag.
    assert texture.nu_m == (0.0,) * 5
    assert texture.hurst.H is None
    assert texture.hurst.reason == "nu_zero"
    assert texture.plane is not None
    assert texture.plane.slope_east == 0.5
    assert texture.plane.slope_north == 0.25
    assert texture.plane.intercept_m == 1000.0
    assert texture.plane.cell_count == 55 * 55


@pytest.mark.parametrize("filler", [math.nan, 0.0, -9999.0, 1e6, -1e300])
def test_plane_removal_ignores_nodata_cells(filler: float) -> None:
    x, y = mesh()
    plane = 1000.0 - 0.125 * x + 0.75 * y
    valid = np.ones((55, 55), dtype=np.bool_)
    valid[20:27, 30:38] = False  # 56 cells, fraction 0.981
    values = np.where(valid, plane, filler)
    result = run(values, valid)
    texture = result.variants["plane"]
    assert texture.plane is not None
    assert texture.plane.slope_east == -0.125
    assert texture.plane.slope_north == 0.75
    assert texture.plane.intercept_m == 1000.0
    assert texture.plane.cell_count == 55 * 55 - 56
    assert texture.nu_m == (0.0,) * 5
    assert texture.hurst.reason == "nu_zero"
    assert np.all(np.isnan(texture.surface_m[~valid]))


def exact_plane_residuals(
    values: Array, valid: BoolArray, x: Array, y: Array
) -> tuple[Array, tuple[Fraction, Fraction, Fraction]]:
    """Least-squares plane z = a + b x + c y over valid cells, solved exactly with Fractions.

    Returns every residual rounded once to float64 (NaN at invalid cells) and (a, b, c).
    """
    cells = [(r, c) for r in range(values.shape[0]) for c in range(values.shape[1]) if valid[r, c]]
    rows = [(Fraction(1), Fraction(float(x[r, c])), Fraction(float(y[r, c]))) for r, c in cells]
    zs = [Fraction(float(values[r, c])) for r, c in cells]
    matrix = [[sum(p[i] * p[j] for p in rows) for j in range(3)] for i in range(3)]
    rhs = [sum(p[i] * z for p, z in zip(rows, zs, strict=True)) for i in range(3)]
    # Gaussian elimination with exact arithmetic.
    aug = [[*matrix[i], rhs[i]] for i in range(3)]
    for col in range(3):
        pivot = next(r for r in range(col, 3) if aug[r][col] != 0)
        aug[col], aug[pivot] = aug[pivot], aug[col]
        for r in range(3):
            if r != col and aug[r][col] != 0:
                factor = aug[r][col] / aug[col][col]
                aug[r] = [aug[r][k] - factor * aug[col][k] for k in range(4)]
    a, b, c = (aug[i][3] / aug[i][i] for i in range(3))
    residuals = np.full(values.shape, np.nan)
    for (r, col), p, z in zip(cells, rows, zs, strict=True):
        residuals[r, col] = float(z - (a + b * p[1] + c * p[2]))
    return residuals, (a, b, c)


def test_texture_surface_is_the_exact_least_squares_residual_rounded_once() -> None:
    x, y = mesh()
    values = rough_field(55)
    valid = np.ones((55, 55), dtype=np.bool_)
    valid[5:9, 40:52] = False
    valid[44, :] = False
    residuals, (a, b, c) = exact_plane_residuals(values, valid, x, y)
    texture = run(values, valid).variants["plane"]
    assert texture.plane is not None
    np.testing.assert_array_equal(texture.surface_m, residuals)
    assert texture.plane.slope_east == float(b)
    assert texture.plane.slope_north == float(c)
    # The intercept is the plane's value at the window centre, x = y = 0.
    assert texture.plane.intercept_m == float(a)


def test_texture_nu_is_the_structure_function_of_the_residuals() -> None:
    x, y = mesh()
    values = rough_field(55, seed=7)
    valid = np.ones((55, 55), dtype=np.bool_)
    valid[30:40, 3:9] = False
    residuals, _ = exact_plane_residuals(values, valid, x, y)
    expected = structure_function(
        residuals,
        valid=valid,
        spacing_m=90.0,
        lags_m=CONFIG.lags_m,
        isotropic_rule="direction_mean",
    )
    result = run(values, valid)
    texture = result.variants["plane"]
    assert texture.structure == expected
    assert texture.nu_m == tuple(item.nu_m for item in expected.isotropic)


def test_relief_is_the_structure_function_of_the_raw_values() -> None:
    values = rough_field(55, seed=11)
    valid = np.ones((55, 55), dtype=np.bool_)
    valid[0:3, 0:55] = False
    for rule in ("direction_mean", "pooled_pairs"):
        expected = structure_function(
            values, valid=valid, spacing_m=90.0, lags_m=CONFIG.lags_m, isotropic_rule=rule
        )
        relief = run(values, valid, rule=rule).variants["none"]
        assert relief.structure == expected
        assert relief.nu_m == tuple(item.nu_m for item in expected.isotropic)
        for direction in DIRECTIONS:
            assert relief.directional_nu_m[direction] == tuple(
                item.nu_m for item in expected.directional[direction]
            )
        surface = np.where(valid, values, np.nan)
        np.testing.assert_array_equal(relief.surface_m, surface)


def test_relief_and_texture_are_kept_separately_when_only_one_has_zero_nu() -> None:
    # A pattern repeating every 6 columns (540 m) and constant along rows: relief nu is a true 0
    # at 540 and 1080 m, so relief H is unavailable; the plane fit tilts the residuals, so
    # texture nu stays positive and texture H is fitted.
    pattern = np.array([0.0, 3.0, 1.0, 4.0, 1.0, 5.0])
    values = np.tile(np.resize(pattern, 55), (55, 1)) + 100.0
    result = run(values)
    relief, texture = result.variants["none"], result.variants["plane"]
    assert relief.nu_m[2] == 0.0 and relief.nu_m[4] == 0.0
    assert relief.nu_m[0] is not None and relief.nu_m[0] > 0.0
    assert relief.hurst.reason == "nu_zero"
    assert texture.plane is not None and texture.plane.slope_east != 0.0
    assert all(nu is not None and nu > 0.0 for nu in texture.nu_m)
    assert texture.hurst.reason is None
    assert texture.hurst.H is not None


def test_plane_fit_on_collinear_cells_is_degenerate_not_zero() -> None:
    # One valid row: the plane's north slope is not determined. Relief is still measured.
    config = with_config(min_valid_fraction=0.01)
    valid = np.zeros((55, 55), dtype=np.bool_)
    valid[27, :] = True
    result = run(rough_field(55), valid, config=config)
    assert result.excluded_reason is None
    texture = result.variants["plane"]
    assert texture.reason == "plane_fit_degenerate"
    assert texture.plane is None
    assert texture.structure is None
    assert texture.nu_m == (None,) * 5
    assert texture.hurst.reason == "plane_fit_degenerate"
    relief = result.variants["none"]
    assert relief.reason is None
    assert all(nu is not None and nu > 0.0 for nu in relief.nu_m)
    assert relief.hurst.reason is None


# --- Valid fraction ---------------------------------------------------------------------------


def first_cells_valid(count: int, size: int = 55) -> BoolArray:
    valid = np.zeros(size * size, dtype=np.bool_)
    valid[:count] = True
    return valid.reshape(size, size)


def test_window_at_exactly_the_minimum_valid_fraction_passes() -> None:
    # 2420 / 3025 == 0.8 exactly; the rule is valid_fraction >= min_valid_fraction.
    result = run(rough_field(55), first_cells_valid(2420))
    assert result.valid_cells == 2420
    assert result.total_cells == 3025
    assert result.valid_fraction == 0.8
    assert result.min_valid_fraction == 0.8
    assert result.excluded_reason is None
    assert not result.excluded
    assert result.supported_lags_m == CONFIG.lags_m


def test_window_below_the_minimum_valid_fraction_is_excluded_with_its_fraction() -> None:
    result = run(rough_field(55), first_cells_valid(2419))
    assert result.excluded
    assert result.excluded_reason == "below_min_valid_fraction"
    assert result.valid_cells == 2419
    assert result.valid_fraction == 2419 / 3025
    assert result.supported_lags_m == ()
    assert [item.reason for item in result.lags] == ["window_excluded"] * 5
    assert all(item.pair_count == 0 for item in result.lags)
    for variant in result.variants.values():
        assert variant.reason == "window_excluded"
        assert variant.structure is None
        assert variant.plane is None
        assert variant.surface_m is None
        assert variant.nu_m == (None,) * 5
        assert variant.hurst.reason == "window_excluded"
        assert variant.hurst.H is None


def test_min_valid_fraction_is_read_from_config() -> None:
    result = run(
        rough_field(55), first_cells_valid(2419), config=with_config(min_valid_fraction=0.7)
    )
    assert result.excluded_reason is None
    assert result.min_valid_fraction == 0.7


def test_all_nodata_window_is_excluded() -> None:
    values = np.full((55, 55), np.nan)
    result = run(values, np.zeros((55, 55), dtype=np.bool_))
    assert result.excluded_reason == "below_min_valid_fraction"
    assert result.valid_cells == 0
    assert result.valid_fraction == 0.0
    assert all(v.nu_m == (None,) * 5 for v in result.variants.values())


def test_rule_reasons_come_before_window_exclusion() -> None:
    config = with_config(lags_m=(180.0, 270.0, 1260.0))
    result = run(rough_field(55), first_cells_valid(100), config=config)
    assert [item.reason for item in result.lags] == [
        "below_min_lag_cells",
        "window_excluded",
        "exceeds_quarter_window",
    ]


def test_numpy_masked_array_mask_counts_as_nodata() -> None:
    values = np.ma.masked_array(rough_field(55), mask=~first_cells_valid(2419))
    result = run(values)
    assert result.valid_cells == 2419
    assert result.excluded_reason == "below_min_valid_fraction"


def test_valid_cell_must_be_finite() -> None:
    values = rough_field(55)
    values[3, 3] = math.nan
    with pytest.raises(TerrainError):
        run(values)


# --- Detrend policies -------------------------------------------------------------------------


@pytest.mark.parametrize("policies", [("none", "plane"), ("plane", "none"), ("none",), ("plane",)])
def test_detrend_policies_come_from_config_in_order(policies: tuple[str, ...]) -> None:
    result = run(rough_field(55), config=with_config(detrend=policies))
    assert result.detrend == policies
    assert tuple(result.variants) == policies
    names = {"none": "relief", "plane": "texture"}
    for policy, variant in result.variants.items():
        assert variant.detrend == policy
        assert variant.name == names[policy]
        assert (variant.plane is None) == (policy == "none")


@pytest.mark.parametrize(
    "policies", [("none", "quadratic"), ("Plane",), (), ("none", "none"), ("linear",)]
)
def test_unknown_or_bad_detrend_policy_raises(policies: tuple[str, ...]) -> None:
    with pytest.raises(TerrainError):
        run(rough_field(55), config=with_config(detrend=policies))


def test_unknown_detrend_policy_has_its_own_error() -> None:
    with pytest.raises(UnknownDetrendPolicyError, match="quadratic"):
        run(rough_field(55), config=with_config(detrend=("none", "quadratic")))


def test_unknown_detrend_policy_in_the_run_config_raises(tmp_path: Path) -> None:
    document = yaml.safe_load(RUN_YAML.read_text(encoding="utf-8"))
    document["terrain"]["detrend"] = ["none", "quadratic"]
    path = tmp_path / "run.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(UnknownDetrendError):
        load_terrain_config(path)


# --- Output record ----------------------------------------------------------------------------


def test_output_carries_grid_detrend_settings_and_per_direction_values() -> None:
    result = run(rough_field(55), rule="pooled_pairs")
    assert result.window_m == 5000.0
    assert result.analysis_grid_m == 90.0
    assert result.grid_size == 55
    assert result.coarsening_factor == 1.0
    assert result.isotropic_rule == "pooled_pairs"
    assert result.detrend == ("none", "plane")
    for variant in result.variants.values():
        assert variant.structure is not None
        assert variant.structure.isotropic_rule == "pooled_pairs"
        assert tuple(variant.directional_nu_m) == DIRECTIONS
        for direction in ("x", "y"):
            assert all(nu is not None and nu > 0.0 for nu in variant.directional_nu_m[direction])
        # The diagonals are off the 90 m grid at every configured lag: None, never 0.
        for direction in ("diagonal_ne", "diagonal_nw"):
            assert variant.directional_nu_m[direction] == (None,) * 5
        assert variant.labels == ("Derived",) * 5
        assert variant.hurst.label == "Derived"


def test_unsupported_values_are_none_with_unavailable_labels() -> None:
    config = with_config(lags_m=(180.0, 270.0, 360.0, 540.0, 720.0, 1080.0, 1260.0))
    result = run(rough_field(55), config=config)
    for variant in result.variants.values():
        assert variant.nu_m[0] is None and variant.nu_m[-1] is None
        assert variant.labels[0] == "Unavailable" and variant.labels[-1] == "Unavailable"
        assert variant.labels[1:-1] == ("Derived",) * 5
        assert all(values[0] is None for values in variant.directional_nu_m.values())


def test_every_reason_is_a_documented_machine_readable_string() -> None:
    cases = [
        run(rough_field(55)),
        run(np.full((55, 55), 3.0)),
        run(rough_field(55), first_cells_valid(10)),
        run(rough_field(11), width_m=1000.0),
        run(rough_field(size_of(factor=1.5)), factor=1.5),
    ]
    for result in cases:
        for item in result.lags:
            assert item.reason is None or item.reason in FINGERPRINT_LAG_REASONS
        for variant in result.variants.values():
            assert variant.hurst.reason is None or variant.hurst.reason in HURST_REASONS


def test_outputs_are_frozen_and_inputs_unchanged() -> None:
    values = rough_field(55)
    valid = first_cells_valid(2900)
    before_values, before_valid = values.copy(), valid.copy()
    result = run(values, valid)
    np.testing.assert_array_equal(values, before_values)
    np.testing.assert_array_equal(valid, before_valid)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.valid_fraction = 1.0  # type: ignore[misc]
    variant = result.variants["plane"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        variant.nu_m = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.variants["none"] = variant  # type: ignore[index]
    assert variant.surface_m is not None
    with pytest.raises(ValueError):
        variant.surface_m[0, 0] = 1.0
    assert isinstance(result.variants["plane"].hurst, HurstFit)


def test_repeated_runs_are_identical() -> None:
    values = rough_field(55, seed=3)
    valid = first_cells_valid(2800)

    def snapshot(result: TerrainFingerprint) -> tuple[object, ...]:
        parts: list[object] = [
            result.lags,
            result.supported_lags_m,
            result.valid_fraction,
            result.excluded_reason,
        ]
        for variant in result.variants.values():
            parts += [variant.plane, variant.structure, variant.nu_m, variant.hurst]
            assert variant.surface_m is not None
            parts.append(variant.surface_m.tobytes())
        return tuple(parts)

    first = snapshot(run(values, valid))
    assert all(snapshot(run(values, valid)) == first for _ in range(3))


def test_unknown_isotropic_rule_raises() -> None:
    with pytest.raises(TerrainError):
        run(rough_field(55), rule="median")


@pytest.mark.parametrize(
    "change",
    [
        {"min_lags_for_H": 1},
        {"min_lags_for_H": True},
        {"min_lag_span_factor": 0.5},
        {"min_valid_fraction": 0.0},
        {"min_valid_fraction": 1.5},
        {"lags_m": ()},
        {"lags_m": (360.0, 270.0)},
    ],
)
def test_bad_config_values_raise(change: dict[str, Any]) -> None:
    with pytest.raises(TerrainError):
        run(rough_field(55), config=with_config(**change))
