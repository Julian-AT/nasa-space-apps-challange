// Terrain observables (PRD 6.3): the TypeScript mirror of terraluna_pipeline.terrain.
//
// nu(L) = sqrt(mean((z(x + L) - z(x))^2)) over valid pairs along both axes and both diagonals,
// relief (raw) and texture (exact least-squares plane removed), H as the OLS slope of ln nu on
// ln L with its support gate. Records use the Python JSON field names (snake_case) so a probe
// result has the same layout as the pipeline fingerprint. Missing is null with a reason, never 0.

import { type Rational, decompose, divideToFloat, frexpExponent, fsum, pow2, ldexp, rational, rationalOf, roundHalfEven } from './exact.ts';
import type { WindowGrid } from './geometry.ts';

export type Direction = 'x' | 'y' | 'diagonal_ne' | 'diagonal_nw';
export const DIRECTIONS: readonly Direction[] = Object.freeze(['x', 'y', 'diagonal_ne', 'diagonal_nw']);
export const DIRECTION_STEPS: Readonly<Record<Direction, readonly [number, number]>> = Object.freeze({
  x: [1, 0],
  y: [0, 1],
  diagonal_ne: [1, 1],
  diagonal_nw: [-1, 1],
});

export type IsotropicRule = 'direction_mean' | 'pooled_pairs';
export const ISOTROPIC_RULES: readonly IsotropicRule[] = Object.freeze(['direction_mean', 'pooled_pairs']);

export type UnsupportedReason = 'axis_lag_not_on_grid' | 'diagonal_lag_not_on_grid' | 'lag_exceeds_grid' | 'no_valid_pairs';
export type IsotropicReason = 'no_supported_direction';
export type LagReason = 'below_min_lag_cells' | 'exceeds_quarter_window' | 'window_excluded' | 'lag_not_on_grid' | 'no_valid_pairs';
export type WindowReason = 'below_min_valid_fraction';
export type VariantReason = 'window_excluded' | 'plane_fit_degenerate';
export type HurstReason = 'window_excluded' | 'plane_fit_degenerate' | 'insufficient_lags' | 'insufficient_lag_span' | 'nu_zero';

/** Every reason string terraluna_pipeline.terrain defines, by union. */
export const TERRAIN_REASONS = Object.freeze({
  unsupported: ['axis_lag_not_on_grid', 'diagonal_lag_not_on_grid', 'lag_exceeds_grid', 'no_valid_pairs'] as const,
  isotropic: ['no_supported_direction'] as const,
  lag: ['below_min_lag_cells', 'exceeds_quarter_window', 'window_excluded', 'lag_not_on_grid', 'no_valid_pairs'] as const,
  window: ['below_min_valid_fraction'] as const,
  variant: ['window_excluded', 'plane_fit_degenerate'] as const,
  hurst: ['window_excluded', 'plane_fit_degenerate', 'insufficient_lags', 'insufficient_lag_span', 'nu_zero'] as const,
});

export type DetrendPolicy = 'none' | 'plane';
export type VariantName = 'relief' | 'texture';
export const VARIANT_NAMES: Readonly<Record<DetrendPolicy, VariantName>> = Object.freeze({ none: 'relief', plane: 'texture' });
export type EvidenceLabel = 'Derived' | 'Unavailable';

/** PRD 7 arithmetic tolerance for an offset length to equal a lag. */
export const LAG_RTOL = 1e-10;
export const LAG_ATOL = 1e-12;
export const MIN_LAG_CELLS = 3.0;
export const MAX_LAG_WINDOW_FRACTION = 0.25;

export class TerrainError extends Error {
  override name = 'TerrainError';
}

/** A grid of elevations, row-major [row * columns + column], with a validity mask. */
export interface MaskedGrid {
  readonly values: ArrayLike<number>;
  readonly valid: ArrayLike<number | boolean>;
  readonly rows: number;
  readonly columns: number;
}

export interface DirectionalNu {
  lag_m: number;
  offset_cells: [number, number] | null;
  offset_m: number | null;
  pair_count: number;
  nu_m: number | null;
  log_nu: number | null;
  reason: UnsupportedReason | null;
}

export interface IsotropicNu {
  lag_m: number;
  directions: Direction[];
  pair_count: number;
  nu_m: number | null;
  log_nu: number | null;
  reason: IsotropicReason | null;
}

export interface StructureFunction {
  spacing_m: number;
  lags_m: number[];
  isotropic_rule: IsotropicRule;
  directional: Record<Direction, DirectionalNu[]>;
  isotropic: IsotropicNu[];
}

/** ln nu, or null where nu is unavailable or 0 (terrain._log_nu). */
export function logNu(nu: number | null): number | null {
  return nu === null || nu === 0 ? null : Math.log(nu);
}

function useMask(grid: MaskedGrid): Uint8Array {
  const n = grid.rows * grid.columns;
  if (grid.values.length !== n || grid.valid.length !== n) throw new TerrainError('values and valid must be rows x columns');
  const use = new Uint8Array(n);
  for (let i = 0; i < n; i += 1) {
    if (grid.valid[i]) {
      if (!Number.isFinite(grid.values[i])) throw new TerrainError('every valid cell must hold a finite value');
      use[i] = 1;
    }
  }
  return use;
}

/** terrain._rms: sqrt(sum(w v^2) / sum(w)) with power-of-two scaling and fsum. */
export function rms(values: ArrayLike<number>, weights?: ArrayLike<number>): number {
  let peak = 0;
  for (let i = 0; i < values.length; i += 1) peak = Math.max(peak, Math.abs(values[i] as number));
  if (peak === 0) return 0;
  const exponent = frexpExponent(peak);
  const scale = pow2(-exponent);
  const terms: number[] = new Array<number>(values.length);
  for (let i = 0; i < values.length; i += 1) {
    const s = (values[i] as number) * scale;
    const sq = s * s;
    terms[i] = weights === undefined ? sq : (weights[i] as number) * sq;
  }
  const mean = weights === undefined ? fsum(terms) / terms.length : fsum(terms) / fsum(Array.from(weights));
  return ldexp(Math.sqrt(mean), exponent);
}

function pairDifferences(z: ArrayLike<number>, use: Uint8Array, rows: number, cols: number, east: number, north: number): number[] | null {
  const dRow = -north;
  const dCol = east;
  if (Math.abs(dRow) >= rows || Math.abs(dCol) >= cols) return null;
  const aRow0 = Math.max(0, -dRow);
  const aRow1 = rows - Math.max(0, dRow);
  const aCol0 = Math.max(0, -dCol);
  const aCol1 = cols - Math.max(0, dCol);
  const out: number[] = [];
  for (let r = aRow0; r < aRow1; r += 1) {
    for (let c = aCol0; c < aCol1; c += 1) {
      const a = r * cols + c;
      const b = (r + dRow) * cols + (c + dCol);
      if (use[a] && use[b]) {
        const d = (z[b] as number) - (z[a] as number);
        if (!Number.isFinite(d)) throw new TerrainError('an elevation difference would overflow float64');
        out.push(d);
      }
    }
  }
  return out;
}

function directional(z: ArrayLike<number>, use: Uint8Array, rows: number, cols: number, direction: Direction, lag: number, spacing: number): DirectionalNu {
  const [east, north] = DIRECTION_STEPS[direction];
  const diagonal = east !== 0 && north !== 0;
  const step = diagonal ? Math.hypot(spacing, spacing) : spacing;
  const k = roundHalfEven(lag / step);
  const length = diagonal ? Math.hypot(k * spacing, k * spacing) : k * spacing;
  if (k < 1 || !(Math.abs(length - lag) <= LAG_ATOL + LAG_RTOL * lag)) {
    return {
      lag_m: lag,
      offset_cells: null,
      offset_m: null,
      pair_count: 0,
      nu_m: null,
      log_nu: null,
      reason: diagonal ? 'diagonal_lag_not_on_grid' : 'axis_lag_not_on_grid',
    };
  }
  const cells: [number, number] = [k * east, k * north];
  const differences = pairDifferences(z, use, rows, cols, cells[0], cells[1]);
  if (differences === null || differences.length === 0) {
    return {
      lag_m: lag,
      offset_cells: cells,
      offset_m: length,
      pair_count: 0,
      nu_m: null,
      log_nu: null,
      reason: differences === null ? 'lag_exceeds_grid' : 'no_valid_pairs',
    };
  }
  const nu = rms(differences);
  return { lag_m: lag, offset_cells: cells, offset_m: length, pair_count: differences.length, nu_m: nu, log_nu: logNu(nu), reason: null };
}

function isotropic(items: DirectionalNu[], directions: readonly Direction[], lag: number, rule: IsotropicRule): IsotropicNu {
  const used: { d: Direction; item: DirectionalNu }[] = [];
  items.forEach((item, i) => {
    if (item.nu_m !== null) used.push({ d: directions[i] as Direction, item });
  });
  if (used.length === 0) {
    return { lag_m: lag, directions: [], pair_count: 0, nu_m: null, log_nu: null, reason: 'no_supported_direction' };
  }
  const nus = used.map((u) => u.item.nu_m as number);
  const counts = used.map((u) => u.item.pair_count);
  const nu = rule === 'direction_mean' ? rms(nus) : rms(nus, counts);
  return {
    lag_m: lag,
    directions: used.map((u) => u.d),
    pair_count: counts.reduce((a, b) => a + b, 0),
    nu_m: nu,
    log_nu: logNu(nu),
    reason: null,
  };
}

function checkLags(lags: readonly number[], spacing: number): void {
  if (lags.length === 0) throw new TerrainError('lags_m must hold at least one lag');
  lags.forEach((lag, i) => {
    if (!(Number.isFinite(lag) && lag > 0)) throw new TerrainError(`lags_m[${String(i)}] must be finite and positive`);
    if (i > 0 && lag <= (lags[i - 1] as number)) throw new TerrainError('lags_m must be strictly increasing');
    if (!Number.isFinite(lag / spacing)) throw new TerrainError('lag is more cells than float64 holds');
  });
}

/** terrain.structure_function: nu(L) per direction and its isotropic summary. */
export function structureFunction(
  grid: MaskedGrid,
  options: { spacingM: number; lagsM: readonly number[]; isotropicRule: IsotropicRule },
): StructureFunction {
  const use = useMask(grid);
  const { spacingM: spacing, lagsM, isotropicRule } = options;
  if (!(Number.isFinite(spacing) && spacing > 0)) throw new TerrainError('spacing_m must be finite and positive');
  checkLags(lagsM, spacing);
  if (!ISOTROPIC_RULES.includes(isotropicRule)) throw new TerrainError(`unknown isotropic rule ${JSON.stringify(isotropicRule)}`);
  const dir = {} as Record<Direction, DirectionalNu[]>;
  for (const d of DIRECTIONS) {
    dir[d] = lagsM.map((lag) => directional(grid.values, use, grid.rows, grid.columns, d, lag, spacing));
  }
  const iso = lagsM.map((lag, i) =>
    isotropic(
      DIRECTIONS.map((d) => dir[d][i] as DirectionalNu),
      DIRECTIONS,
      lag,
      isotropicRule,
    ),
  );
  return { spacing_m: spacing, lags_m: [...lagsM], isotropic_rule: isotropicRule, directional: dir, isotropic: iso };
}

export interface HurstFit {
  H: number | null;
  log_intercept: number | null;
  lags_m: number[];
  nu_m: number[];
  reason: HurstReason | null;
  label: EvidenceLabel;
}

function unavailableHurst(lags: number[], nus: number[], reason: HurstReason): HurstFit {
  return { H: null, log_intercept: null, lags_m: lags, nu_m: nus, reason, label: 'Unavailable' };
}

/** terrain.hurst_fit: OLS slope of ln nu on ln L over supported lags, or the reason it is unavailable. */
export function hurstFit(
  lagsM: readonly number[],
  nuM: readonly number[],
  options: { minLagsForH: number; minLagSpanFactor: number },
): HurstFit {
  const { minLagsForH, minLagSpanFactor } = options;
  if (!Number.isInteger(minLagsForH) || minLagsForH < 2) throw new TerrainError('min_lags_for_H must be an integer >= 2');
  if (!(Number.isFinite(minLagSpanFactor) && minLagSpanFactor >= 1)) throw new TerrainError('min_lag_span_factor must be >= 1');
  const lags = [...lagsM];
  const nus = [...nuM];
  if (nus.length !== lags.length) throw new TerrainError('one nu per lag');
  lags.forEach((lag, i) => {
    if (!(Number.isFinite(lag) && lag > 0)) throw new TerrainError('lags must be finite and positive');
    if (i > 0 && lag <= (lags[i - 1] as number)) throw new TerrainError('lags must be strictly increasing');
  });
  for (const nu of nus) if (!(Number.isFinite(nu) && nu >= 0)) throw new TerrainError('nu must be finite and >= 0');
  if (lags.length < minLagsForH) return unavailableHurst(lags, nus, 'insufficient_lags');
  if (!((lags[lags.length - 1] as number) / (lags[0] as number) >= minLagSpanFactor)) {
    return unavailableHurst(lags, nus, 'insufficient_lag_span');
  }
  if (nus.some((nu) => nu === 0)) return unavailableHurst(lags, nus, 'nu_zero');
  const x = lags.map((l) => Math.log(l));
  const y = nus.map((nu) => Math.log(nu));
  const xMean = fsum(x) / x.length;
  const yMean = fsum(y) / y.length;
  const dx = x.map((v) => v - xMean);
  const sxx = fsum(dx.map((d) => d * d));
  if (sxx === 0) return unavailableHurst(lags, nus, 'insufficient_lag_span');
  const sxy = fsum(dx.map((d, i) => d * ((y[i] as number) - yMean)));
  const slope = sxy / sxx;
  return { H: slope, log_intercept: yMean - slope * xMean, lags_m: lags, nu_m: nus, reason: null, label: 'Derived' };
}

export interface PlaneFit {
  intercept_m: number;
  slope_east: number;
  slope_north: number;
  cell_count: number;
}

function det3(m: readonly (readonly bigint[])[]): bigint {
  const g = (r: number, c: number): bigint => (m[r] as readonly bigint[])[c] as bigint;
  return (
    g(0, 0) * (g(1, 1) * g(2, 2) - g(1, 2) * g(2, 1)) -
    g(0, 1) * (g(1, 0) * g(2, 2) - g(1, 2) * g(2, 0)) +
    g(0, 2) * (g(1, 0) * g(2, 1) - g(1, 1) * g(2, 0))
  );
}

/**
 * terrain._plane_fit: the exact least-squares plane over the valid cells, solved in integers by
 * Cramer's rule on doubled centred cell coordinates; each residual rounded once. Returns null
 * when the valid cells do not determine a plane.
 */
export function planeFit(
  z: ArrayLike<number>,
  use: ArrayLike<number | boolean>,
  rows: number,
  cols: number,
  grid: { widthM: number; spacingM: number },
): { plane: PlaneFit; residuals: Float64Array } | null {
  const idx: number[] = [];
  for (let i = 0; i < rows * cols; i += 1) if (use[i]) idx.push(i);
  if (idx.length === 0) return null;
  const parts = idx.map((i) => decompose(z[i] as number));
  let minExp = 0;
  for (const p of parts) minExp = Math.min(minExp, p.exponent);
  const zs = parts.map((p) => p.mantissa << BigInt(p.exponent - minExp));
  const scale = 1n << BigInt(-minExp);
  const us = idx.map((i) => BigInt(2 * (i % cols) - (cols - 1)));
  const vs = idx.map((i) => BigInt(rows - 1 - 2 * Math.floor(i / cols)));
  const count = BigInt(idx.length);
  let su = 0n, sv = 0n, suu = 0n, svv = 0n, suv = 0n, sz = 0n, suz = 0n, svz = 0n;
  for (let k = 0; k < idx.length; k += 1) {
    const u = us[k] as bigint;
    const v = vs[k] as bigint;
    const w = zs[k] as bigint;
    su += u;
    sv += v;
    suu += u * u;
    svv += v * v;
    suv += u * v;
    sz += w;
    suz += u * w;
    svz += v * w;
  }
  const normal = [
    [count, su, sv],
    [su, suu, suv],
    [sv, suv, svv],
  ];
  const det = det3(normal);
  if (det === 0n) return null;
  const rhs = [sz, suz, svz];
  const replaced = (column: number): bigint =>
    det3(normal.map((row, r) => row.map((value, c) => (c === column ? (rhs[r] as bigint) : value))));
  const aNum = replaced(0);
  const pNum = replaced(1);
  const qNum = replaced(2);
  const denominator = det * scale;
  const residuals = new Float64Array(rows * cols).fill(Number.NaN);
  for (let k = 0; k < idx.length; k += 1) {
    const value = (zs[k] as bigint) * det - aNum - pNum * (us[k] as bigint) - qNum * (vs[k] as bigint);
    const r = divideToFloat(value, denominator);
    if (!Number.isFinite(r)) throw new TerrainError('a plane residual would overflow float64');
    residuals[idx[k] as number] = r;
  }
  const spacing = rationalOf(grid.spacingM);
  const width = rationalOf(grid.widthM);
  const two: Rational = { n: 2n, d: 1n };
  const slopeEast = rational.div({ n: 2n * pNum, d: 1n }, rational.mul({ n: denominator, d: 1n }, spacing));
  const slopeNorth = rational.div({ n: 2n * qNum, d: 1n }, rational.mul({ n: denominator, d: 1n }, spacing));
  const x0 = rational.div(rational.sub(rational.mul({ n: BigInt(cols), d: 1n }, spacing), width), two);
  const y0 = rational.div(rational.sub(width, rational.mul({ n: BigInt(rows), d: 1n }, spacing)), two);
  const intercept = rational.sub(rational.sub({ n: aNum, d: denominator }, rational.mul(slopeEast, x0)), rational.mul(slopeNorth, y0));
  return {
    plane: {
      intercept_m: rational.toFloat(intercept),
      slope_east: rational.toFloat(slopeEast),
      slope_north: rational.toFloat(slopeNorth),
      cell_count: idx.length,
    },
    residuals,
  };
}

/** The terrain settings of config/run.yaml that the fingerprint reads (config.TerrainConfig). */
export interface TerrainConfig {
  readonly window_m: number;
  readonly analysis_grid_m: number;
  readonly lags_m: readonly number[];
  readonly min_lags_for_H: number;
  readonly min_lag_span_factor: number;
  readonly min_valid_fraction: number;
  readonly detrend: readonly DetrendPolicy[];
}

export interface LagSupport {
  lag_m: number;
  lag_cells: number;
  pair_count: number;
  reason: LagReason | null;
}

export interface TerrainVariant {
  name: VariantName;
  reason: VariantReason | null;
  plane: PlaneFit | null;
  nu_m: (number | null)[];
  log_nu: (number | null)[];
  labels: EvidenceLabel[];
  directional_nu_m: Record<Direction, (number | null)[]>;
  directional_log_nu: Record<Direction, (number | null)[]>;
  structure: StructureFunction | null;
  hurst: HurstFit;
}

export interface TerrainFingerprint {
  isotropic_rule: IsotropicRule;
  window_m: number;
  analysis_grid_m: number;
  configured_analysis_grid_m: number;
  coarsening_factor: number;
  grid_size: number;
  total_cells: number;
  valid_cells: number;
  valid_fraction: number;
  min_valid_fraction: number;
  excluded_reason: WindowReason | null;
  lags: LagSupport[];
  supported_lags_m: number[];
  min_lags_for_H: number;
  min_lag_span_factor: number;
  detrend: DetrendPolicy[];
  variants: Partial<Record<DetrendPolicy, TerrainVariant>>;
}

function lagRule(lag: number, spacing: number, window: number): LagReason | null {
  if (!(lag / spacing >= MIN_LAG_CELLS)) return 'below_min_lag_cells';
  if (!(lag / window <= MAX_LAG_WINDOW_FRACTION)) return 'exceeds_quarter_window';
  return null;
}

function perDirection<T>(make: (d: Direction) => T): Record<Direction, T> {
  return { x: make('x'), y: make('y'), diagonal_ne: make('diagonal_ne'), diagonal_nw: make('diagonal_nw') };
}

function unavailableVariant(policy: DetrendPolicy, reason: VariantReason, lags: number): TerrainVariant {
  const empty = (): null[] => new Array<null>(lags).fill(null);
  return {
    name: VARIANT_NAMES[policy],
    reason,
    plane: null,
    nu_m: empty(),
    log_nu: empty(),
    labels: new Array<EvidenceLabel>(lags).fill('Unavailable'),
    directional_nu_m: perDirection(empty),
    directional_log_nu: perDirection(empty),
    structure: null,
    hurst: unavailableHurst([], [], reason),
  };
}

function variant(
  policy: DetrendPolicy,
  plane: PlaneFit | null,
  structure: StructureFunction | null,
  lags: readonly number[],
  supported: readonly number[],
  config: TerrainConfig,
): TerrainVariant {
  const aligned = (measured: (number | null)[]): (number | null)[] => {
    if (structure === null) return lags.map(() => null);
    const byLag = new Map<number, number | null>();
    structure.lags_m.forEach((lag, i) => byLag.set(lag, measured[i] ?? null));
    return lags.map((lag) => byLag.get(lag) ?? null);
  };
  const nu = aligned(structure ? structure.isotropic.map((item) => item.nu_m) : []);
  const directionalNu = perDirection((d) => aligned(structure ? structure.directional[d].map((item) => item.nu_m) : []));
  const used = supported.map((lag) => nu[lags.indexOf(lag)] ?? null);
  if (used.some((v) => v === null)) throw new TerrainError('a supported lag has no nu in this variant');
  return {
    name: VARIANT_NAMES[policy],
    reason: null,
    plane,
    nu_m: nu,
    log_nu: nu.map(logNu),
    labels: nu.map((v) => (v === null ? 'Unavailable' : 'Derived')),
    directional_nu_m: directionalNu,
    directional_log_nu: perDirection((d) => directionalNu[d].map(logNu)),
    structure,
    hurst: hurstFit(supported, used as number[], {
      minLagsForH: config.min_lags_for_H,
      minLagSpanFactor: config.min_lag_span_factor,
    }),
  };
}

/**
 * terrain.terrain_fingerprint: relief and texture of one analysis window. values/valid are the
 * resampled grid (row-major grid.size x grid.size); the record layout matches the Python JSON.
 */
export function terrainFingerprint(
  values: ArrayLike<number>,
  valid: ArrayLike<number | boolean>,
  options: { grid: WindowGrid; config: TerrainConfig; isotropicRule: IsotropicRule; coarseningFactor: number },
): TerrainFingerprint {
  const { grid, config, isotropicRule, coarseningFactor: factor } = options;
  const policies = [...config.detrend];
  if (policies.length === 0 || new Set(policies).size !== policies.length || policies.some((p) => !(p in VARIANT_NAMES))) {
    throw new TerrainError('detrend must list distinct policies among none and plane');
  }
  if (!Number.isInteger(config.min_lags_for_H) || config.min_lags_for_H < 2) throw new TerrainError('min_lags_for_H must be >= 2');
  if (!(config.min_valid_fraction > 0 && config.min_valid_fraction <= 1)) throw new TerrainError('min_valid_fraction must lie in (0, 1]');
  if (!(Number.isFinite(factor) && factor >= 1)) throw new TerrainError('coarsening_factor must be >= 1 (no upsampling)');
  const spacing = grid.spacingM;
  const windowM = grid.widthM;
  const configured = config.analysis_grid_m;
  const expected = configured * factor;
  if (!(Math.abs(spacing - expected) <= Math.max(LAG_RTOL * Math.max(Math.abs(spacing), Math.abs(expected)), LAG_ATOL))) {
    throw new TerrainError(`grid spacing ${String(spacing)} m is not analysis_grid_m times the coarsening factor`);
  }
  const lags = [...config.lags_m];
  checkLags(lags, spacing);
  if (!ISOTROPIC_RULES.includes(isotropicRule)) throw new TerrainError(`unknown isotropic rule ${JSON.stringify(isotropicRule)}`);
  const n = grid.size;
  const masked: MaskedGrid = { values, valid, rows: n, columns: n };
  const use = useMask(masked);
  const total = n * n;
  let count = 0;
  for (const u of use) count += u;
  const fraction = count / total;
  const excluded: WindowReason | null = fraction >= config.min_valid_fraction ? null : 'below_min_valid_fraction';
  const ruleReasons = lags.map((lag) => lagRule(lag, spacing, windowM));
  const measured = lags.filter((_, i) => ruleReasons[i] === null);
  let relief: StructureFunction | null = null;
  if (excluded === null && measured.length > 0) {
    relief = structureFunction({ values, valid: use, rows: n, columns: n }, { spacingM: spacing, lagsM: measured, isotropicRule });
  }
  const support: LagSupport[] = lags.map((lag, i) => {
    const cells = lag / spacing;
    const rule = ruleReasons[i] ?? null;
    if (rule !== null) return { lag_m: lag, lag_cells: cells, pair_count: 0, reason: rule };
    if (excluded !== null || relief === null) return { lag_m: lag, lag_cells: cells, pair_count: 0, reason: 'window_excluded' };
    const index = relief.lags_m.indexOf(lag);
    const summary = relief.isotropic[index] as IsotropicNu;
    if (summary.nu_m !== null) return { lag_m: lag, lag_cells: cells, pair_count: summary.pair_count, reason: null };
    const measuredRelief = relief;
    const onGrid = DIRECTIONS.some((d) => measuredRelief.directional[d][index]?.offset_cells !== null);
    return { lag_m: lag, lag_cells: cells, pair_count: 0, reason: onGrid ? 'no_valid_pairs' : 'lag_not_on_grid' };
  });
  const supported = support.filter((s) => s.reason === null).map((s) => s.lag_m);

  const variants: Partial<Record<DetrendPolicy, TerrainVariant>> = {};
  for (const policy of policies) {
    if (excluded !== null) {
      variants[policy] = unavailableVariant(policy, 'window_excluded', lags.length);
      continue;
    }
    if (policy === 'none') {
      variants[policy] = variant(policy, null, relief, lags, supported, config);
      continue;
    }
    const fitted = planeFit(values, use, n, n, grid);
    if (fitted === null) {
      variants[policy] = unavailableVariant(policy, 'plane_fit_degenerate', lags.length);
      continue;
    }
    const structure =
      measured.length > 0
        ? structureFunction({ values: fitted.residuals, valid: use, rows: n, columns: n }, { spacingM: spacing, lagsM: measured, isotropicRule })
        : null;
    variants[policy] = variant(policy, fitted.plane, structure, lags, supported, config);
  }

  return {
    isotropic_rule: isotropicRule,
    window_m: windowM,
    analysis_grid_m: spacing,
    configured_analysis_grid_m: configured,
    coarsening_factor: factor,
    grid_size: n,
    total_cells: total,
    valid_cells: count,
    valid_fraction: fraction,
    min_valid_fraction: config.min_valid_fraction,
    excluded_reason: excluded,
    lags: support,
    supported_lags_m: supported,
    min_lags_for_H: config.min_lags_for_H,
    min_lag_span_factor: config.min_lag_span_factor,
    detrend: policies,
    variants,
  };
}
