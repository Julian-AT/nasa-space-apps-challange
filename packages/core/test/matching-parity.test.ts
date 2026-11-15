// Parity of terrainDistance and backgroundPercentile against fixtures/parity/matching_synthetic.json
// (T10-08), written from Python by `python -m terraluna_pipeline.matching parity` (t4-logic T4L-03).
import { describe, expect, it } from 'vitest';

import { MATCHING_REASONS, backgroundPercentile, terrainDistance } from '../src/index.ts';
import { arr, loadFixture, num, obj, str } from './fixtures.ts';

const FILE = 'matching_synthetic.json';
const data = loadFixture(FILE);
const tol = obj(data.tolerance, `${FILE}.tolerance`);
const ATOL = num(tol.atol, 'tolerance.atol');

interface Case {
  name: string;
  a: { supported_lags_m: number[]; nu_m: Record<string, number | null> };
  b: { supported_lags_m: number[]; nu_m: Record<string, number | null> };
  expected: { value: number | null; reason: string | null; shared_lags_m?: number[] };
}

const distanceCases = arr(data.terrain_distance, `${FILE}.terrain_distance`) as unknown as Case[];
const percentileCases = arr(data.background_percentile, `${FILE}.background_percentile`) as unknown as {
  name: string;
  d: number;
  bin_edges: number[];
  counts: number[];
  expected: { value: number | null; reason: string | null };
}[];

describe('matching parity', () => {
  it('uses the PRD 8.8 tolerance of 1e-9 absolute', () => {
    expect(ATOL).toBe(1e-9);
    expect(distanceCases.length).toBeGreaterThan(0);
    expect(percentileCases.length).toBeGreaterThan(0);
  });
  for (const c of distanceCases) {
    it(`terrain distance ${str(c.name, 'name')}`, () => {
      const out = terrainDistance(c.a, c.b);
      expect(out.reason).toBe(c.expected.reason);
      if (c.expected.value === null) expect(out.value).toBeNull();
      else expect(Math.abs((out.value ?? Number.NaN) - c.expected.value)).toBeLessThanOrEqual(ATOL);
      if (c.expected.shared_lags_m !== undefined && c.expected.reason === null) expect(out.lags_m).toEqual(c.expected.shared_lags_m);
    });
  }
  for (const c of percentileCases) {
    it(`background percentile ${c.name}`, () => {
      const out = backgroundPercentile(c.d, c.bin_edges, c.counts);
      expect(out.reason).toBe(c.expected.reason);
      if (c.expected.value === null) expect(out.value).toBeNull();
      else expect(Math.abs((out.value ?? Number.NaN) - c.expected.value)).toBeLessThanOrEqual(ATOL);
    });
  }
  it('every fixture reason is in the TypeScript union', () => {
    const reasons = [...distanceCases, ...percentileCases].map((c) => c.expected.reason).filter((r): r is string => r !== null);
    for (const r of reasons) expect(MATCHING_REASONS as readonly string[], r).toContain(r);
  });
});
