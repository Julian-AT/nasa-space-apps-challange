// probeTargets reads target window fingerprints from bundle files (synthetic records in the
// CONTRACTS WindowEntry and TargetRecord shapes; the values are test input only).
import { describe, expect, it } from 'vitest';

import type { PulledBundle } from '../bundle.ts';
import { probeTargets } from './targets.ts';

const enc = (v: unknown) => new TextEncoder().encode(JSON.stringify(v));
const variant = { supported_lags_m: [270, 360], nu_m: { '270': 4, '360': 5, '540': null }, detrend: 'none', hurst: null, hurst_reason: 'insufficient_lags' };

function bundle(files: Record<string, unknown>): PulledBundle {
  return { manifest: {} as PulledBundle['manifest'], validation: {} as PulledBundle['validation'], checksumOnly: new Map(Object.entries(files).map(([k, v]) => [k, enc(v)])) };
}

describe('probeTargets', () => {
  it('joins targets.json to the primary window fingerprints of windows/<body>.json', () => {
    const out = probeTargets(
      bundle({
        'targets.json': [{ target_id: 't1', name: 'Ridge', body: 'moon', primary_window_id: 'w1', extra: true }],
        'windows/moon.json': [{ window: { window_id: 'w1' }, terrain: { terrain: { relief: variant, texture: null } } }],
      }),
    );
    expect(out.reason).toBeNull();
    expect(out.fingerprints).toEqual([
      { target_id: 't1', name: 'Ridge', window_id: 'w1', body: 'moon', isotropic_rule: 'direction_mean', relief: { supported_lags_m: [270, 360], nu_m: { '270': 4, '360': 5, '540': null } }, texture: null },
    ]);
  });
  it('states a reason instead of an empty default', () => {
    expect(probeTargets(bundle({})).reason).toBe('the bundle lists no targets.json');
    const out = probeTargets(bundle({ 'targets.json': [{ target_id: 't1', name: 'R', body: 'mars', primary_window_id: 'w9' }] }));
    expect(out.fingerprints).toEqual([]);
    expect(out.reason).toContain('t1');
  });
});
