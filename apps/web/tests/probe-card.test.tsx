// The /probe result card (T13-07): labels, Unavailable reasons and the stop message.
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { ProbeCard } from '../app/probe/probe-card.tsx';
import {
  DERIVED_LIVE_LABEL,
  LIVE_PROBE_SENTENCE,
  RADAR_UNAVAILABLE_REASON,
  SURFACE_MODEL_NOTICE,
  type ProbeResult,
} from '../lib/probe/result.ts';

const dem = { mode: 'proxy' as const, tiles_read: ['T'], tiles_missing: [], bytes: 2_000_000, requests: 3, prd_budget_bytes: 4194304, within_prd_budget: true };
const unavailable = (unit: string, reason: string) => ({ status: 'unavailable' as const, value: null, unit, label: 'Unavailable' as const, shared_lags_m: [], reason });

const success: ProbeResult = {
  kind: 'result',
  request_id: 1,
  centre: { lat_deg: 64.9, lon_deg: -16.7 },
  sentence: LIVE_PROBE_SENTENCE,
  surface_notice: SURFACE_MODEL_NOTICE,
  source_product: 'Copernicus DEM GLO-30',
  terrain: {
    label: DERIVED_LIVE_LABEL,
    isotropic_rule: 'direction_mean',
    analysis_grid_m: 90,
    valid_fraction: 1,
    relief: { detrend: 'none', supported_lags_m: [270], nu_m: { '270.0': 1.5 }, hurst: null, hurst_reason: 'insufficient_lags' },
    texture: null,
    texture_reason: 'plane_fit_degenerate',
  },
  targets: [
    {
      target_id: 'moon-x',
      target_window_id: 'w',
      target_relief_nu_m: { '270': 40 },
      target_texture_nu_m: null,
      terrain_relief: { status: 'available', value: 3.372, unit: 'RMS of log nu', label: DERIVED_LIVE_LABEL, shared_lags_m: [270], reason: null },
      terrain_texture: unavailable('RMS of log nu', 'texture_unavailable'),
      radar: unavailable('CPR Wasserstein-1', RADAR_UNAVAILABLE_REASON),
      encoder: unavailable('cosine distance', 'encoder_not_evaluated'),
    },
  ],
  targets_reason: null,
  worldcover: { status: 'unavailable', reason: 'not checked' },
  dem,
  timings: { dem_read_ms: 400, resample_ms: 3, fingerprint_ms: 10, model_load_ms: null, inference_ms: null, total_ms: 413 },
  bundle_version: 'b',
  model_id: null,
  model_status: null,
  g5_status: null,
};

describe('ProbeCard', () => {
  it('shows every label and reason, never a 0 for a missing value', () => {
    const html = renderToStaticMarkup(<ProbeCard result={success} names={{ 'moon-x': 'Connecting Ridge' }} />).replace(/<[^>]+>/g, '');
    for (const text of [LIVE_PROBE_SENTENCE, DERIVED_LIVE_LABEL, 'Unavailable: Unavailable for live probes', 'Unavailable: encoder_not_evaluated', 'Unavailable: texture_unavailable', 'Unavailable: insufficient_lags', 'Connecting Ridge', '3.372']) {
      expect(html).toContain(text);
    }
    expect(html).not.toMatch(/Observed|rank #|score/);
  });
  it('shows the stop reason and the measured fraction below 0.8', () => {
    const html = renderToStaticMarkup(
      <ProbeCard
        result={{ kind: 'stop', request_id: 2, centre: { lat_deg: 0, lon_deg: 0 }, reason: 'below_min_valid_fraction', valid_fraction: 0.5, min_valid_fraction: 0.8, dem, timings: success.timings }}
        names={{}}
      />,
    );
    expect(html).toContain('50.0%');
    expect(html).toContain('80%');
    expect(html).toContain('below_min_valid_fraction');
  });
});
