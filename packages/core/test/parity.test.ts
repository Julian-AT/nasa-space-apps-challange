// The parity gate (PRD 8.8, T10-02 to T10-06): every case in fixtures/parity/*.json against the
// TypeScript mirror. Inputs, expected outputs and tolerances come only from the Python fixtures.

import { describe, expect, it } from 'vitest';

import {
  BODY_RADII_M,
  aeqdInverse,
  geographicLookup,
  resampleWindow,
  structureFunction,
  terrainFingerprint,
  windowGrid,
} from '../src/index.ts';
import type { IsotropicRule, TerrainConfig } from '../src/index.ts';
import { DISTANCE_ATOL_M, RESAMPLED_GRID_RTOL, compareRecord } from './compare.ts';
import type { Json } from './compare.ts';
import { REQUIRED_TERRAIN_CASES, arr, geometryFixture, num, obj, parityFiles, requireCase, str, terrainFixture } from './fixtures.ts';

const terrain = terrainFixture();
const geometry = geometryFixture();
const grid = windowGrid(num(terrain.grid.width_m, 'grid.width_m'), num(terrain.grid.spacing_m, 'grid.spacing_m'));
const config: TerrainConfig = {
  window_m: num(terrain.config.window_m, 'window_m'),
  analysis_grid_m: num(terrain.config.analysis_grid_m, 'analysis_grid_m'),
  lags_m: arr(terrain.config.lags_m, 'lags_m').map((v) => num(v, 'lags_m')),
  min_lags_for_H: num(terrain.config.min_lags_for_H, 'min_lags_for_H'),
  min_lag_span_factor: num(terrain.config.min_lag_span_factor, 'min_lag_span_factor'),
  min_valid_fraction: num(terrain.config.min_valid_fraction, 'min_valid_fraction'),
  detrend: arr(terrain.config.detrend, 'detrend').map((v) => {
    const s = str(v, 'detrend');
    if (s !== 'none' && s !== 'plane') throw new Error(`unknown detrend ${s}`);
    return s;
  }),
};
const rules = arr(terrain.config.isotropic_rules, 'isotropic_rules').map((r) => str(r, 'rule') as IsotropicRule);

describe('fixture presence', () => {
  it('fixtures/parity holds the T3-07 files and every required synthetic case', () => {
    const files = parityFiles();
    expect(files).toContain('terrain_synthetic.json');
    expect(files).toContain('geometry_synthetic.json');
    const missing = REQUIRED_TERRAIN_CASES.filter((name) => !terrain.cases.some((c) => c.name === name));
    expect(missing, `terrain_synthetic.json lacks cases: ${missing.join(', ')}`).toEqual([]);
  });
});

describe('window grids', () => {
  it('match the Python pixel centres for the analysis and encoder grids', () => {
    for (const g of geometry.windowGrids) {
      const ts = windowGrid(num(g.width_m, 'width_m'), num(g.spacing_m, 'spacing_m'));
      expect(ts.size).toBe(num(g.size, 'size'));
      const errors = [
        ...compareRecord(Array.from(ts.xM), g.x_m ?? null, `${str(g.name, 'name')}.x_m`),
        ...compareRecord(Array.from(ts.yM), g.y_m ?? null, `${str(g.name, 'name')}.y_m`),
      ];
      expect(errors).toEqual([]);
    }
  });
  it('uses the D29 radii', () => {
    expect(geometry.radii).toEqual({ ...BODY_RADII_M });
  });
});

describe('inverse AEQD parity (Python forward is the oracle)', () => {
  for (const c of geometry.aeqdCases) {
    it(c.name, () => {
      const { lat, lon } = aeqdInverse(c.x, c.y, { lat0Rad: c.lat0Rad, lon0Rad: c.lon0Rad, radiusM: c.radiusM });
      const bad: string[] = [];
      c.lat.forEach((expected, i) => {
        const dLat = c.radiusM * Math.abs((lat[i] as number) - expected);
        let dLon = Math.abs((lon[i] as number) - (c.lon[i] as number)) % (2 * Math.PI);
        dLon = Math.min(dLon, 2 * Math.PI - dLon);
        const dEast = c.radiusM * Math.cos(expected) * dLon;
        if (!(dLat <= DISTANCE_ATOL_M && dEast <= DISTANCE_ATOL_M)) bad.push(`${String(i)}: ${String(dLat)} m, ${String(dEast)} m`);
      });
      expect(bad).toEqual([]);
    });
  }
});

describe('D29 resampler parity (geographic form)', () => {
  const geographic = terrain.cases.filter((c) => c.lookupForm === 'geographic');
  it('has geographic-form cases', () => {
    expect(geographic.length, 'no geographic-form resampling case in terrain_synthetic.json').toBeGreaterThan(0);
  });
  for (const c of geographic) {
    it(c.name, () => {
      const out = resampleWindow(
        { values: c.source, rows: c.rows, columns: c.columns, nodata: c.nodata },
        geographicLookup(c.lookup),
        { grid, body: c.body, lat0Rad: c.lat0Rad, lon0Rad: c.lon0Rad },
      );
      expect(Array.from(out.valid)).toEqual(Array.from(c.resampledValid));
      expect(Array.from(out.inside)).toEqual(Array.from(c.resampledInside));
      expect(out.validCells).toBe(c.validCells);
      const bad: string[] = [];
      c.resampledValues.forEach((e, i) => {
        const a = out.values[i] as number;
        if (Number.isNaN(e) !== Number.isNaN(a)) bad.push(`${String(i)}: nodata state differs`);
        else if (!Number.isNaN(e) && !(Math.abs(a - e) <= RESAMPLED_GRID_RTOL * Math.abs(e))) bad.push(`${String(i)}: ${String(a)} vs ${String(e)}`);
      });
      expect(bad).toEqual([]);
    });
  }
});

describe('nu(L) parity on the recorded Python-resampled grid', () => {
  for (const name of REQUIRED_TERRAIN_CASES) {
    const c = requireCase(terrain.cases, name, 'terrain_synthetic.json');
    for (const rule of rules) {
      it(`${name} ${rule}`, () => {
        const expected = obj(c.structure[rule], `${name}.structure.${rule}`);
        const result = structureFunction(
          { values: c.resampledValues, valid: c.resampledValid, rows: grid.size, columns: grid.size },
          { spacingM: grid.spacingM, lagsM: terrain.structureLags, isotropicRule: rule },
        );
        expect(compareRecord(result, expected, `${name}.structure.${rule}`)).toEqual([]);
      });
    }
  }
});

describe('fingerprint parity (resampled grid, nu, H, valid fraction, reasons)', () => {
  for (const name of REQUIRED_TERRAIN_CASES) {
    const c = requireCase(terrain.cases, name, 'terrain_synthetic.json');
    for (const rule of rules) {
      it(`${name} ${rule}`, () => {
        const expected = obj(c.fingerprint[rule], `${name}.fingerprint.${rule}`);
        const result = terrainFingerprint(c.resampledValues, c.resampledValid, { grid, config, isotropicRule: rule, coarseningFactor: 1 });
        expect(compareRecord(result, expected, `${name}.fingerprint.${rule}`)).toEqual([]);
      });
    }
  }
});

describe('validity reasons', () => {
  it('are identical for every case, reporting every mismatch', () => {
    const mismatches: string[] = [];
    const collect = (a: unknown, e: Json, path: string): void => {
      if (e !== null && typeof e === 'object' && !Array.isArray(e)) {
        for (const [k, v] of Object.entries(e)) collect((a as Record<string, unknown> | null)?.[k], v, `${path}.${k}`);
      } else if (Array.isArray(e)) {
        e.forEach((v, i) => {
          collect((a as unknown[] | null)?.[i], v, `${path}[${String(i)}]`);
        });
      } else if (/reason$/.test(path) && a !== e) {
        mismatches.push(`${path}: ${JSON.stringify(a)} vs ${JSON.stringify(e)}`);
      }
    };
    for (const c of terrain.cases) {
      for (const rule of rules) {
        const result = terrainFingerprint(c.resampledValues, c.resampledValid, { grid, config, isotropicRule: rule, coarseningFactor: 1 });
        collect(result, obj(c.fingerprint[rule], 'fp'), `${c.name}.${rule}`);
      }
    }
    expect(mismatches).toEqual([]);
  });
});
