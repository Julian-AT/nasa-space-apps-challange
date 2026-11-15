// Read-only loader for fixtures/parity/*.json, written from Python by
// `uv run python -m terraluna_pipeline.terrain` (T3-07). TypeScript never writes these files.
// A missing file or case fails with its name; nothing is skipped, defaulted or synthesized.

import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Json } from './compare.ts';

export const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
export const PARITY_DIR = join(REPO_ROOT, 'fixtures', 'parity');
export const GENERATOR = 'uv run python -m terraluna_pipeline.terrain';

export class FixtureError extends Error {
  override name = 'FixtureError';
}

type JsonObject = Record<string, Json>;

export function parityFiles(): string[] {
  if (!existsSync(PARITY_DIR)) throw new FixtureError(`fixtures/parity/ is missing; regenerate with ${GENERATOR}`);
  const files = readdirSync(PARITY_DIR)
    .filter((f) => f.endsWith('.json'))
    .sort();
  if (files.length === 0) throw new FixtureError(`fixtures/parity/ holds no JSON fixture; regenerate with ${GENERATOR}`);
  return files;
}

const cache = new Map<string, JsonObject>();

export function loadFixture(file: string): JsonObject {
  const hit = cache.get(file);
  if (hit !== undefined) return hit;
  const path = join(PARITY_DIR, file);
  if (!existsSync(path)) throw new FixtureError(`missing parity fixture fixtures/parity/${file}; regenerate with ${GENERATOR}`);
  const data = JSON.parse(readFileSync(path, 'utf8')) as Json;
  if (data === null || typeof data !== 'object' || Array.isArray(data)) throw new FixtureError(`${file}: not a JSON object`);
  for (const key of ['schema_version', 'data_kind', 'generator']) {
    if (!(key in data)) throw new FixtureError(`${file}: missing header field ${key}`);
  }
  if (!('tolerances' in data) && !('tolerance' in data)) throw new FixtureError(`${file}: missing header field tolerances`);
  cache.set(file, data);
  return data;
}

export function obj(value: Json | undefined, where: string): JsonObject {
  if (value === null || value === undefined || typeof value !== 'object' || Array.isArray(value)) {
    throw new FixtureError(`${where}: expected an object`);
  }
  return value;
}
export function arr(value: Json | undefined, where: string): Json[] {
  if (!Array.isArray(value)) throw new FixtureError(`${where}: expected an array`);
  return value;
}
export function num(value: Json | undefined, where: string): number {
  if (typeof value !== 'number') throw new FixtureError(`${where}: expected a number`);
  return value;
}
export function str(value: Json | undefined, where: string): string {
  if (typeof value !== 'string') throw new FixtureError(`${where}: expected a string`);
  return value;
}

export interface Tolerances {
  resampledGridRtol: number;
  logNuAtol: number;
  hAtol: number;
  distanceAtol: number;
}

export function tolerancesOf(file: string): Tolerances {
  const t = obj(loadFixture(file).tolerances, `${file}.tolerances`);
  return {
    resampledGridRtol: num(obj(t.resampled_grid, 'resampled_grid').rtol, 'resampled_grid.rtol'),
    logNuAtol: num(obj(t.log_nu, 'log_nu').atol, 'log_nu.atol'),
    hAtol: num(obj(t.H, 'H').atol, 'H.atol'),
    distanceAtol: num(obj(t.distance_m, 'distance_m').atol, 'distance_m.atol'),
  };
}

export interface TerrainCase {
  name: string;
  nodataPattern: string;
  body: 'moon' | 'mars' | 'earth';
  lat0Rad: number;
  lon0Rad: number;
  lookupForm: string;
  lookup: { originLonDeg: number; originLatDeg: number; pixelWidthDeg: number; pixelHeightDeg: number };
  nodata: number | null;
  rows: number;
  columns: number;
  source: Float64Array;
  resampledValues: Float64Array;
  resampledValid: Uint8Array;
  resampledInside: Uint8Array;
  validCells: number;
  structure: JsonObject;
  fingerprint: JsonObject;
}

export interface TerrainFixture {
  header: JsonObject;
  config: JsonObject;
  grid: JsonObject;
  structureLags: number[];
  cases: TerrainCase[];
}

function terrainCase(raw: Json, i: number, file: string): TerrainCase {
  const c = obj(raw, `${file}.cases[${String(i)}]`);
  const name = str(c.name, `cases[${String(i)}].name`);
  const where = `${file} case ${name}`;
  const inputs = obj(c.inputs, `${where}.inputs`);
  if (!Array.isArray(inputs.source_m)) throw new FixtureError(`${where}: lacks the exact input array inputs.source_m`);
  const src = obj(c.source, `${where}.source`);
  const lookup = obj(src.lookup, `${where}.source.lookup`);
  const rows = num(src.rows, `${where}.rows`);
  const columns = num(src.columns, `${where}.columns`);
  const flat = arr(inputs.source_m, `${where}.source_m`).flatMap((r) => arr(r, `${where}.source_m row`));
  if (flat.length !== rows * columns) throw new FixtureError(`${where}: source_m is not rows x columns`);
  const source = Float64Array.from(flat.map((v) => (v === null ? Number.NaN : num(v, `${where}.source_m value`))));
  const resampled = obj(c.resampled, `${where}.resampled`);
  const flatten = (key: string): Json[] => arr(resampled[key], `${where}.resampled.${key}`).flatMap((r) => arr(r, key));
  const body = str(c.body, `${where}.body`);
  if (body !== 'moon' && body !== 'mars' && body !== 'earth') throw new FixtureError(`${where}: unknown body ${body}`);
  const nodata = src.nodata;
  return {
    name,
    nodataPattern: str(c.nodata_pattern, `${where}.nodata_pattern`),
    body,
    lat0Rad: num(inputs.lat0_rad, `${where}.lat0_rad`),
    lon0Rad: num(inputs.lon0_rad, `${where}.lon0_rad`),
    lookupForm: str(lookup.form, `${where}.lookup.form`),
    lookup: {
      originLonDeg: num(lookup.origin_lon_deg, 'origin_lon_deg'),
      originLatDeg: num(lookup.origin_lat_deg, 'origin_lat_deg'),
      pixelWidthDeg: num(lookup.pixel_width_deg, 'pixel_width_deg'),
      pixelHeightDeg: num(lookup.pixel_height_deg, 'pixel_height_deg'),
    },
    nodata: nodata === null || nodata === undefined ? null : num(nodata, `${where}.nodata`),
    rows,
    columns,
    source,
    resampledValues: Float64Array.from(flatten('values_m').map((v) => (v === null ? Number.NaN : num(v, 'values_m')))),
    resampledValid: Uint8Array.from(flatten('valid').map((v) => (v === true ? 1 : 0))),
    resampledInside: Uint8Array.from(flatten('inside').map((v) => (v === true ? 1 : 0))),
    validCells: num(resampled.valid_cells, `${where}.valid_cells`),
    structure: obj(c.structure, `${where}.structure`),
    fingerprint: obj(c.fingerprint, `${where}.fingerprint`),
  };
}

export function terrainFixture(): TerrainFixture {
  const file = 'terrain_synthetic.json';
  const data = loadFixture(file);
  return {
    header: data,
    config: obj(data.config, `${file}.config`),
    grid: obj(data.grid, `${file}.grid`),
    structureLags: arr(data.structure_lags_m, `${file}.structure_lags_m`).map((v) => num(v, 'structure_lags_m')),
    cases: arr(data.cases, `${file}.cases`).map((raw, i) => terrainCase(raw, i, file)),
  };
}

export interface AeqdCase {
  name: string;
  body: string;
  radiusM: number;
  lat0Rad: number;
  lon0Rad: number;
  x: number[];
  y: number[];
  lat: number[];
  lon: number[];
  rho: number[];
}

export interface GeometryFixture {
  header: JsonObject;
  radii: Record<string, number>;
  windowGrids: JsonObject[];
  aeqdCases: AeqdCase[];
}

export function geometryFixture(): GeometryFixture {
  const file = 'geometry_synthetic.json';
  const data = loadFixture(file);
  const radii: Record<string, number> = {};
  for (const [k, v] of Object.entries(obj(data.radii_m, `${file}.radii_m`))) radii[k] = num(v, `radii_m.${k}`);
  const nums = (v: Json | undefined, where: string): number[] => arr(v, where).map((x) => num(x, where));
  const aeqdCases = arr(data.aeqd_cases, `${file}.aeqd_cases`).map((raw, i) => {
    const c = obj(raw, `aeqd_cases[${String(i)}]`);
    const name = str(c.name, 'name');
    const inputs = obj(c.inputs, `${name}.inputs`);
    const inverse = obj(c.inverse, `${name}.inverse`);
    return {
      name,
      body: str(c.body, `${name}.body`),
      radiusM: num(c.radius_m, `${name}.radius_m`),
      lat0Rad: num(inputs.lat0_rad, `${name}.lat0_rad`),
      lon0Rad: num(inputs.lon0_rad, `${name}.lon0_rad`),
      x: nums(inputs.x_m, `${name}.x_m`),
      y: nums(inputs.y_m, `${name}.y_m`),
      lat: nums(inverse.lat_rad, `${name}.lat_rad`),
      lon: nums(inverse.lon_rad, `${name}.lon_rad`),
      rho: nums(c.rho_m, `${name}.rho_m`),
    };
  });
  return {
    header: data,
    radii,
    windowGrids: arr(data.window_grids, `${file}.window_grids`).map((g) => obj(g, 'window_grids[]')),
    aeqdCases,
  };
}

export const REQUIRED_TERRAIN_CASES = [
  'plane',
  'sinusoid',
  'fractal',
  'nodata_hole',
  'nodata_edge_strip',
  'nodata_checkerboard',
] as const;

export function requireCase<T extends { name: string }>(cases: T[], name: string, file: string): T {
  const found = cases.find((c) => c.name === name);
  if (found === undefined) {
    throw new FixtureError(`fixtures/parity/${file} lacks the required case ${name}; regenerate with ${GENERATOR}`);
  }
  return found;
}
