// Live-probe quality gate and the composed fingerprint entry point (PRD 8.7 steps 3 and 4).

import type { Body, GeographicLookup, ResampledGrid, SourceRaster, WindowGrid } from './geometry.ts';
import { resampleWindow, windowGrid } from './geometry.ts';
import type { IsotropicRule, TerrainConfig, TerrainFingerprint } from './terrain.ts';
import { terrainFingerprint } from './terrain.ts';

/**
 * PRD 8.7 step 4: a probe stops below this valid fraction of the resampled 90 m window. It is
 * the same rule as terrain.terrain_fingerprint's below_min_valid_fraction (config
 * terrain.min_valid_fraction = 0.8), so a window equal to the threshold passes.
 */
export const PROBE_MIN_VALID_FRACTION = 0.8;
export const PROBE_STOP_REASON = 'below_min_valid_fraction' as const;

export type ProbeQuality =
  | { pass: true; valid_fraction: number; valid_cells: number; total_cells: number; reason: null }
  | { pass: false; valid_fraction: number; valid_cells: number; total_cells: number; reason: typeof PROBE_STOP_REASON };

export function probeQuality(resampled: Pick<ResampledGrid, 'valid'>): ProbeQuality {
  const total = resampled.valid.length;
  if (total === 0) throw new RangeError('an empty window has no valid fraction');
  let count = 0;
  for (const v of resampled.valid) count += v ? 1 : 0;
  const fraction = count / total;
  return fraction >= PROBE_MIN_VALID_FRACTION
    ? { pass: true, valid_fraction: fraction, valid_cells: count, total_cells: total, reason: null }
    : { pass: false, valid_fraction: fraction, valid_cells: count, total_cells: total, reason: PROBE_STOP_REASON };
}

/** PRD terrain settings (config/run.yaml terrain block) used by the live probe. */
export const PRD_TERRAIN_CONFIG: TerrainConfig = Object.freeze({
  window_m: 5000,
  analysis_grid_m: 90,
  lags_m: Object.freeze([270, 360, 540, 720, 1080]),
  min_lags_for_H: 4,
  min_lag_span_factor: 4,
  min_valid_fraction: PROBE_MIN_VALID_FRACTION,
  detrend: Object.freeze(['none', 'plane'] as const),
});

export const ANALYSIS_GRID: WindowGrid = windowGrid(5000, 90);
export const ENCODER_GRID: WindowGrid = windowGrid(5000, 78.125);

/** Resample a window and fingerprint it in one call (resampler, quality, nu, H). */
export function windowFingerprint(
  source: SourceRaster,
  lookup: GeographicLookup,
  options: { body: Body; lat0Rad: number; lon0Rad: number; isotropicRule: IsotropicRule; config?: TerrainConfig },
): { resampled: ResampledGrid; quality: ProbeQuality; fingerprint: TerrainFingerprint } {
  const config = options.config ?? PRD_TERRAIN_CONFIG;
  const grid = windowGrid(config.window_m, config.analysis_grid_m);
  const resampled = resampleWindow(source, lookup, { grid, body: options.body, lat0Rad: options.lat0Rad, lon0Rad: options.lon0Rad });
  const quality = probeQuality(resampled);
  const fingerprint = terrainFingerprint(resampled.values, resampled.valid, {
    grid,
    config,
    isotropicRule: options.isotropicRule,
    coarseningFactor: 1,
  });
  return { resampled, quality, fingerprint };
}
