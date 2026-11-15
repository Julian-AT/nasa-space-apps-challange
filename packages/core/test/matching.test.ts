// Behaviour tests for the terrain distance and the background percentile (T10-08), and parity
// against the Python reference outputs in fixtures/parity/matching_synthetic.json (T4L-03).

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { PARITY_DIR } from './fixtures.ts';
import { backgroundPercentile, familyPercentile, terrainDistance } from '../src/index.ts';

const v = (lags: number[], nus: (number | null)[]) => ({
  supported_lags_m: lags,
  nu_m: Object.fromEntries(lags.map((l, i) => [l.toFixed(1), nus[i] ?? null])),
});

describe('terrainDistance', () => {
  it('is the RMS of log nu over the shared lags', () => {
    const d = terrainDistance(v([270, 360, 540], [1, 2, 4]), v([360, 540, 720], [1, 1, 1]));
    expect(d.status).toBe('available');
    expect(d.lags_m).toEqual([360, 540]);
    expect(d.value).toBeCloseTo(Math.sqrt((Math.log(2) ** 2 + Math.log(4) ** 2) / 2), 15);
  });
  it('identical fingerprints give a real zero', () => {
    expect(terrainDistance(v([270], [3]), v([270], [3])).value).toBe(0);
  });
  it('no shared lags, a zero nu and a missing nu are Unavailable, never 0', () => {
    expect(terrainDistance(v([270], [1]), v([360], [1]))).toMatchObject({ value: null, reason: 'no_shared_lags' });
    expect(terrainDistance(v([270], [0]), v([270], [1]))).toMatchObject({ value: null, reason: 'nu_zero' });
    expect(terrainDistance(v([270], [null]), v([270], [1]))).toMatchObject({ value: null, reason: 'nu_missing' });
  });
});

describe('backgroundPercentile (linear_within_bin_v1)', () => {
  const edges = [0, 1, 2, 4];
  const counts = [10, 20, 10];
  it('interpolates within the bin and clamps at the edges', () => {
    expect(backgroundPercentile(-1, edges, counts).value).toBe(0);
    expect(backgroundPercentile(4, edges, counts).value).toBe(100);
    expect(backgroundPercentile(1.5, edges, counts).value).toBeCloseTo(50, 12);
    expect(backgroundPercentile(0, edges, counts).value).toBe(0);
  });
  it('is monotone in distance', () => {
    let last = -1;
    for (let d = -0.5; d <= 4.5; d += 0.05) {
      const p = backgroundPercentile(d, edges, counts).value ?? Number.NaN;
      expect(p).toBeGreaterThanOrEqual(last);
      last = p;
    }
  });
  it('an empty histogram and another family are Unavailable', () => {
    expect(backgroundPercentile(1, edges, [0, 0, 0])).toMatchObject({ value: null, reason: 'background_empty' });
    const bg = { family: 'terrain_texture' as const, bin_edges: edges, counts, percentile_definition: 'linear_within_bin_v1' as const, direction: 'smaller_is_closer' as const };
    expect(familyPercentile(1, 'terrain_relief', bg)).toMatchObject({ value: null, reason: 'family_mismatch' });
    expect(familyPercentile(1, 'terrain_texture', bg).value).toBeCloseTo(25, 12);
  });
});

interface Expected { value: number | null; reason: string | null; shared_lags_m?: number[] }
interface DistanceCase { name: string; a: Parameters<typeof terrainDistance>[0]; b: Parameters<typeof terrainDistance>[1]; expected: Expected }
interface PercentileCase { name: string; d: number; bin_edges: number[]; counts: number[]; expected: Expected }
const matching = JSON.parse(readFileSync(join(PARITY_DIR, 'matching_synthetic.json'), 'utf8')) as {
  tolerance: { atol: number; rtol: number };
  terrain_distance: DistanceCase[];
  background_percentile: PercentileCase[];
};

function expectSame(got: ReturnType<typeof terrainDistance>, want: Expected, tol: { atol: number; rtol: number }) {
  expect(got.reason).toBe(want.reason);
  if (want.value === null) expect(got.value).toBeNull();
  else expect(Math.abs((got.value ?? Number.NaN) - want.value)).toBeLessThanOrEqual(tol.atol + tol.rtol * Math.abs(want.value));
}

describe('parity with fixtures/parity/matching_synthetic.json', () => {
  it('terrainDistance reproduces every Python case', () => {
    expect(matching.terrain_distance.length).toBeGreaterThan(0);
    for (const c of matching.terrain_distance) {
      const got = terrainDistance(c.a, c.b);
      expectSame(got, c.expected, matching.tolerance);
      if (c.expected.shared_lags_m !== undefined) expect(got.lags_m, c.name).toEqual(c.expected.shared_lags_m);
    }
  });
  it('backgroundPercentile reproduces every Python case', () => {
    expect(matching.background_percentile.length).toBeGreaterThan(0);
    for (const c of matching.background_percentile) {
      expectSame(backgroundPercentile(c.d, c.bin_edges, c.counts), c.expected, matching.tolerance);
    }
  });
});
