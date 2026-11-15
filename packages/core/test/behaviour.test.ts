// Behaviour tests independent of the fixtures (T10-02 to T10-06).

import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it, vi } from 'vitest';

import {
  PROBE_MIN_VALID_FRACTION,
  PRD_TERRAIN_CONFIG,
  TERRAIN_REASONS,
  aeqdInverse,
  bilinearResample,
  hurstFit,
  probeQuality,
  structureFunction,
  terrainFingerprint,
  windowGrid,
} from '../src/index.ts';
import { arr, loadFixture, obj, parityFiles, str } from './fixtures.ts';

describe('resampler behaviour', () => {
  it('distances from the centre equal rho', () => {
    const R = 6371008.8;
    const x = [1000, -2500, 0, 1767.7669529663688];
    const y = [0, 1200, -2500, 1767.7669529663688];
    const lat0 = 0.7;
    const { lat, lon } = aeqdInverse(x, y, { lat0Rad: lat0, lon0Rad: 0.3, radiusM: R });
    x.forEach((xi, i) => {
      const rho = Math.hypot(xi, y[i] as number);
      const p = lat[i] as number;
      const dl = (lon[i] as number) - 0.3;
      const h = Math.sin((p - lat0) / 2) ** 2 + Math.cos(lat0) * Math.cos(p) * Math.sin(dl / 2) ** 2;
      const c = 2 * Math.asin(Math.sqrt(h));
      expect(Math.abs(R * c - rho) / rho).toBeLessThan(1e-9);
    });
  });
  it('a plane in pixel space stays a plane, and nodata propagates to its four neighbours', () => {
    const rows = 6;
    const cols = 6;
    const values = new Float64Array(rows * cols);
    for (let r = 0; r < rows; r += 1) for (let c = 0; c < cols; c += 1) values[r * cols + c] = 100 + 2 * c - 3 * r;
    const out = bilinearResample({ values, rows, columns: cols, nodata: null }, [0.25, 2.5, 4.75], [0.5, 3.25, 1]);
    expect(Array.from(out.values)).toEqual([100 + 0.5 - 1.5, 100 + 5 - 9.75, 100 + 9.5 - 3]);
    values[2 * cols + 2] = -32768;
    const masked = bilinearResample({ values, rows, columns: cols, nodata: -32768 }, [1.5, 2.5, 1.5, 2.5, 3.5, 2], [1.5, 1.5, 2.5, 2.5, 3.5, 2]);
    expect(Array.from(masked.valid)).toEqual([0, 0, 0, 0, 1, 0]);
    expect(Number.isNaN(masked.values[0])).toBe(true);
    const outside = bilinearResample({ values, rows, columns: cols, nodata: null }, [-0.1, 5.1], [0, 0]);
    expect(Array.from(outside.inside)).toEqual([0, 0]);
    expect(Number.isNaN(outside.values[0]) && Number.isNaN(outside.values[1])).toBe(true);
  });
});

describe('nu and H behaviour', () => {
  const grid = windowGrid(5000, 90);
  const n = grid.size;
  it('a tilted plane gives relief nu = slope x lag on the axes', () => {
    const values = new Float64Array(n * n);
    for (let r = 0; r < n; r += 1) for (let c = 0; c < n; c += 1) values[r * n + c] = 0.02 * (grid.xM[c] as number);
    const valid = new Uint8Array(n * n).fill(1);
    const sf = structureFunction({ values, valid, rows: n, columns: n }, { spacingM: 90, lagsM: [270, 540], isotropicRule: 'direction_mean' });
    expect(Math.abs((sf.directional.x[0]?.nu_m ?? 0) - 0.02 * 270) / (0.02 * 270)).toBeLessThan(1e-9);
    expect(sf.directional.y[0]?.nu_m).toBe(0);
    expect(sf.directional.diagonal_ne[0]?.reason).toBe('diagonal_lag_not_on_grid');
  });
  it('H is Unavailable with distinct reasons, never a number', () => {
    const opts = { minLagsForH: 4, minLagSpanFactor: 4 };
    expect(hurstFit([270, 360, 540], [1, 2, 3], opts).reason).toBe('insufficient_lags');
    expect(hurstFit([270, 300, 360, 540], [1, 2, 3, 4], opts).reason).toBe('insufficient_lag_span');
    expect(hurstFit([270, 360, 540, 1080], [1, 0, 3, 4], opts).reason).toBe('nu_zero');
    const fit = hurstFit([270, 360, 540, 1080], [270 ** 0.7, 360 ** 0.7, 540 ** 0.7, 1080 ** 0.7], opts);
    expect(Math.abs((fit.H ?? Number.NaN) - 0.7)).toBeLessThan(1e-12);
  });
  it('the reason union equals the reasons the fixtures use and the Python module defines', () => {
    const union = new Set(Object.values(TERRAIN_REASONS).flat());
    const seen = new Set<string>();
    const walk = (v: unknown, key: string): void => {
      if (Array.isArray(v)) v.forEach((x) => { walk(x, key); });
      else if (v !== null && typeof v === 'object') for (const [k, x] of Object.entries(v)) walk(x, k);
      else if (typeof v === 'string' && /reason$/.test(key)) seen.add(v);
    };
    walk(loadFixture('terrain_synthetic.json').cases, '');
    for (const r of seen) expect(union.has(r as never), r).toBe(true);
    const python = readFileSync(join(import.meta.dirname, '../../../pipeline/src/terraluna_pipeline/terrain.py'), 'utf8');
    for (const r of union) expect(python.includes(`"${r}"`), `${r} is not defined in terrain.py`).toBe(true);
  });
});

describe('probe quality gate (PRD 8.7 step 4)', () => {
  it('passes at exactly 0.8 and stops below with the reason', () => {
    expect(PROBE_MIN_VALID_FRACTION).toBe(0.8);
    const at = new Uint8Array(10).fill(1, 0, 8);
    expect(probeQuality({ valid: at })).toMatchObject({ pass: true, valid_fraction: 0.8, reason: null });
    const below = new Uint8Array(10).fill(1, 0, 7);
    expect(probeQuality({ valid: below })).toMatchObject({ pass: false, valid_fraction: 0.7, reason: 'below_min_valid_fraction' });
  });
  it('a failing window has no nu or H, only Unavailable reasons', () => {
    const grid = windowGrid(5000, 90);
    const n = grid.size * grid.size;
    const values = new Float64Array(n).fill(Number.NaN);
    const valid = new Uint8Array(n);
    for (let i = 0; i < n / 2; i += 1) {
      values[i] = i;
      valid[i] = 1;
    }
    const fp = terrainFingerprint(values, valid, { grid, config: PRD_TERRAIN_CONFIG, isotropicRule: 'direction_mean', coarseningFactor: 1 });
    expect(fp.excluded_reason).toBe('below_min_valid_fraction');
    expect(fp.variants.none?.nu_m.every((v) => v === null)).toBe(true);
    expect(fp.variants.plane?.hurst).toMatchObject({ H: null, reason: 'window_excluded' });
  });
});

describe('determinism and worker safety', () => {
  it('the same fingerprint twice is bit-identical, also from a fresh module instance', async () => {
    const grid = windowGrid(5000, 90);
    const n = grid.size;
    const values = new Float64Array(n * n).map((_, i) => Math.sin(i * 0.37) * 40 + (i % n) * 0.3);
    const valid = new Uint8Array(n * n).fill(1);
    const opts = { grid, config: PRD_TERRAIN_CONFIG, isotropicRule: 'pooled_pairs' as const, coarseningFactor: 1 };
    const a = JSON.stringify(terrainFingerprint(values, valid, opts));
    const b = JSON.stringify(terrainFingerprint(values, valid, opts));
    vi.resetModules();
    const fresh = await import('../src/terrain.ts');
    const c = JSON.stringify(fresh.terrainFingerprint(values, valid, opts));
    expect(b).toBe(a);
    expect(c).toBe(a);
  });
  it('src/ imports no Node, DOM or storage API and uses no Float32Array', () => {
    const dir = join(import.meta.dirname, '../src');
    for (const file of readdirSync(dir).filter((f) => f.endsWith('.ts'))) {
      const text = readFileSync(join(dir, file), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
      expect(text, file).not.toMatch(/from ['"](node:|fs|path|process)/);
      expect(text, file).not.toMatch(/\b(window|document|localStorage|process)\./);
      expect(text, file).not.toMatch(/Float32Array/);
    }
  });
});

describe('coverage report (T10-06)', () => {
  it('lists fixtures that are not yet covered by name, never as passed', () => {
    const files = parityFiles();
    const covered = new Set(['terrain_synthetic.json', 'geometry_synthetic.json']);
    const pending: Record<string, string> = {
      'encoder_channels.json': 'not covered (waiting on t11-dataset T11-06)',
      'matching_synthetic.json': 'not covered (waiting on t4-logic T4L-03)',
    };
    const unknown = files.filter((f) => !covered.has(f) && !(f in pending) && !/^terrain_real_/.test(f));
    expect(unknown, `fixtures without a TypeScript counterpart: ${unknown.join(', ')}`).toEqual([]);
    for (const f of Object.keys(pending)) {
      if (files.includes(f)) throw new Error(`${f} has landed: add its parity test (${pending[f] ?? ''})`);
    }
    const cases = arr(loadFixture('terrain_synthetic.json').cases, 'cases').map((c) => str(obj(c, 'case').name, 'name'));
    expect(cases.length).toBeGreaterThanOrEqual(6);
  });
});
