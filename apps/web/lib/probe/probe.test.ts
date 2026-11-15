// Live-probe tests with a stub Range transport (T13-04, T13-05). The tile served here is a
// synthetic GeoTIFF built in the test from a deterministic surface and labelled synthetic in its
// citation key; it is test input only and never reaches the app. No network is used.

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { writeArrayBuffer } from 'geotiff';
import { describe, expect, it } from 'vitest';

import { runProbe } from './pipeline.ts';
import { tileUrl } from './reader.ts';
import {
  DERIVED_LIVE_LABEL,
  LIVE_PROBE_SENTENCE,
  RADAR_UNAVAILABLE_REASON,
  type ProbeTargetFingerprint,
  isProbeResult,
  predictedLabel,
} from './result.ts';
import { glo30TileName, tilesForBounds, windowBounds } from './tiles.ts';
import { ANALYSIS_GRID, EARTH_RADIUS_M } from '@terraluna/core';

const PX_LAT = 1 / 720;
const PX_LON = 1 / 360;

/** A synthetic 1 degree GLO-30-like tile (PixelIsPoint, float32), labelled synthetic. */
function syntheticTile(latFloor: number, lonFloor: number): ArrayBuffer {
  const height = 720;
  const width = 360;
  const values = new Float32Array(width * height);
  for (let r = 0; r < height; r += 1) {
    for (let c = 0; c < width; c += 1) {
      const lat = latFloor + 1 - r * PX_LAT;
      const lon = lonFloor + c * PX_LON;
      values[r * width + c] = 600 + 400 * (lat - latFloor) + 30 * Math.sin(lon * 97) * Math.cos(lat * 131);
    }
  }
  return writeArrayBuffer(values, {
    width,
    height,
    ModelPixelScale: [PX_LON, PX_LAT, 0],
    ModelTiepoint: [0, 0, 0, lonFloor, latFloor + 1, 0],
    GeographicTypeGeoKey: 4326,
    GTModelTypeGeoKey: 2,
    GTRasterTypeGeoKey: 2,
    GTCitationGeoKey: 'SYNTHETIC test tile, not Copernicus data',
    SampleFormat: [3],
    BitsPerSample: [32],
  });
}

interface StubLog {
  requests: string[];
  bytes: number;
}

function stubFetch(tiles: Record<string, ArrayBuffer>, log: StubLog, options: { ignoreRange?: boolean } = {}): typeof fetch {
  return (input: string | URL | Request, init?: RequestInit) => {
    const url = input instanceof Request ? input.url : String(input);
    log.requests.push(url);
    const tile = Object.keys(tiles).find((name) => url.endsWith(`/${name}`));
    const body = tile === undefined ? undefined : tiles[tile];
    if (body === undefined) return Promise.resolve(new Response('not found', { status: 404 }));
    if (options.ignoreRange) return Promise.resolve(new Response(body, { status: 200 }));
    const range = new Headers(init?.headers).get('range') ?? '';
    const match = /^bytes=(\d+)-(\d+)$/.exec(range);
    if (!match) return Promise.resolve(new Response('range required', { status: 400 }));
    const first = Number(match[1]);
    const last = Math.min(Number(match[2]), body.byteLength - 1);
    const slice = body.slice(first, last + 1);
    log.bytes += slice.byteLength;
    return Promise.resolve(
      new Response(slice, {
        status: 206,
        headers: { 'content-range': `bytes ${String(first)}-${String(last)}/${String(body.byteLength)}` },
      }),
    );
  };
}

const N64W017 = glo30TileName(64, -17);
const tiles = { [N64W017]: syntheticTile(64, -17) };
const noTargets = { fingerprints: [] as [], reason: 'no target fingerprints in the bundle' };

describe('GLO-30 tile selection', () => {
  it('names tiles by their south-west corner', () => {
    expect(glo30TileName(64, -17)).toBe('Copernicus_DSM_COG_10_N64_00_W017_00_DEM');
    expect(glo30TileName(-13, 33)).toBe('Copernicus_DSM_COG_10_S13_00_E033_00_DEM');
  });
  it('finds one tile inside, two across an edge, four at a corner, and wraps at 180', () => {
    const at = (lat: number, lon: number) => {
      const s = tilesForBounds(windowBounds(lat, lon, EARTH_RADIUS_M, [ANALYSIS_GRID]), 0.01);
      return s.status === 'available' ? s.tiles.map((t) => t.name) : [s.reason];
    };
    expect(at(64.5, -16.5)).toEqual([N64W017]);
    expect(at(64.5, -16.0)).toHaveLength(2);
    expect(at(65.0, -16.0)).toHaveLength(4);
    expect(at(10.5, 179.999)).toEqual(['Copernicus_DSM_COG_10_N10_00_E179_00_DEM', 'Copernicus_DSM_COG_10_N10_00_W180_00_DEM']);
    expect(at(89.99, 0)).toEqual(['window_reaches_a_pole']);
  });
  it('requires an explicit mode', () => {
    expect(tileUrl('proxy', N64W017, 'https://x.test')).toBe(`https://x.test/api/v1/dem/cop30/${N64W017}`);
    expect(() => tileUrl('other' as 'proxy', N64W017)).toThrow(TypeError);
  });
});

describe('runProbe', () => {
  it('returns the Derived fingerprint, separate distance families and the PRD 3 wording', async () => {
    const log: StubLog = { requests: [], bytes: 0 };
    const target: ProbeTargetFingerprint = {
      target_id: 't1',
      window_id: 'w1',
      body: 'moon',
      isotropic_rule: 'direction_mean',
      relief: { supported_lags_m: [270, 360, 540], nu_m: { '270.0': 2, '360.0': 3, '540.0': 4 } },
      texture: null,
    };
    const result = await runProbe(
      { id: 7, latDeg: 64.5, lonDeg: -16.5 },
      { mode: 'proxy', origin: 'https://x.test', fetch: stubFetch(tiles, log), targets: { fingerprints: [target], reason: null }, bundleVersion: null },
    );
    expect(result.kind, JSON.stringify(result)).toBe('result');
    if (result.kind !== 'result') return;
    expect(isProbeResult(structuredClone(result))).toBe(true);
    expect(result.sentence).toBe('Live estimate from public elevation data; not a site assessment.');
    expect(result.terrain.label).toBe('Derived (live, in browser)');
    expect(result.terrain.valid_fraction).toBe(1);
    expect(result.terrain.relief.supported_lags_m).toEqual([270, 360, 540, 720, 1080]);
    expect(result.terrain.relief.hurst).not.toBeNull();
    const d = result.targets[0];
    expect(d?.terrain_relief.status).toBe('available');
    expect(d?.terrain_relief.shared_lags_m).toEqual([270, 360, 540]);
    expect(d?.terrain_texture).toMatchObject({ status: 'unavailable', value: null, reason: 'texture_unavailable' });
    expect(d?.radar).toMatchObject({ value: null, reason: RADAR_UNAVAILABLE_REASON });
    expect(d?.encoder).toMatchObject({ value: null, reason: 'encoder_not_evaluated' });
    expect(Object.keys(d ?? {}).sort()).toEqual(['encoder', 'radar', 'target_id', 'target_window_id', 'terrain_relief', 'terrain_texture']);
    expect(JSON.stringify(result)).not.toMatch(/Observed|rank|score|combined/);
    expect(log.requests.every((u) => u === `https://x.test/api/v1/dem/cop30/${N64W017}`)).toBe(true);
    expect(result.dem.bytes).toBe(log.bytes);
    expect(result.dem.bytes).toBeLessThanOrEqual(result.dem.prd_budget_bytes);
  });

  it('stops below a valid fraction of 0.8 before anything else, reporting the fraction', async () => {
    // At 64.99 N the window reaches into N65, which the stub does not have (ocean-like 404).
    const result = await runProbe(
      { id: 1, latDeg: 64.995, lonDeg: -16.5 },
      { mode: 'proxy', fetch: stubFetch(tiles, { requests: [], bytes: 0 }), targets: noTargets, bundleVersion: null },
    );
    expect(result.kind).toBe('stop');
    if (result.kind !== 'stop') return;
    expect(result.reason).toBe('below_min_valid_fraction');
    expect(result.valid_fraction).toBeLessThan(0.8);
    expect(result.valid_fraction).toBeGreaterThan(0);
    expect(result.dem.tiles_missing).toContain('Copernicus_DSM_COG_10_N65_00_W017_00_DEM');
  });

  it('a missing tile, a server that ignores Range and an abort each fail with their own reason', async () => {
    const ocean = await runProbe({ id: 2, latDeg: 47.5, lonDeg: -30.5 }, { mode: 'proxy', fetch: stubFetch(tiles, { requests: [], bytes: 0 }), targets: noTargets, bundleVersion: null });
    expect(ocean).toMatchObject({ kind: 'failure', reason: 'tile_missing' });
    const full = await runProbe({ id: 3, latDeg: 64.5, lonDeg: -16.5 }, { mode: 'proxy', fetch: stubFetch(tiles, { requests: [], bytes: 0 }, { ignoreRange: true }), targets: noTargets, bundleVersion: null });
    expect(full).toMatchObject({ kind: 'failure', reason: 'reader_failed' });
    expect(full.kind === 'failure' && full.detail).toMatch(/^range_not_honoured/);
    const controller = new AbortController();
    controller.abort();
    const aborted = await runProbe({ id: 4, latDeg: 64.5, lonDeg: -16.5 }, { mode: 'proxy', fetch: stubFetch(tiles, { requests: [], bytes: 0 }), targets: noTargets, bundleVersion: null }, controller.signal);
    expect(aborted).toMatchObject({ kind: 'failure', reason: 'aborted' });
    const later = await runProbe({ id: 5, latDeg: 64.5, lonDeg: -16.5 }, { mode: 'proxy', fetch: stubFetch(tiles, { requests: [], bytes: 0 }), targets: noTargets, bundleVersion: null });
    expect(later.kind).toBe('result');
  });
});

describe('labels and worker safety', () => {
  it('uses the exact PRD strings', () => {
    expect(LIVE_PROBE_SENTENCE).toBe('Live estimate from public elevation data; not a site assessment.');
    expect(DERIVED_LIVE_LABEL).toBe('Derived (live, in browser)');
    expect(RADAR_UNAVAILABLE_REASON).toBe('Unavailable for live probes');
    expect(predictedLabel('NOT_RUN')).toBe('Predicted (encoder v1, G5 NOT_RUN)');
  });
  it('lib/probe and workers import no DOM, storage, React or Next module', () => {
    const root = join(import.meta.dirname, '..', '..');
    const files: string[] = [];
    for (const dir of ['lib/probe', 'workers']) {
      for (const f of readdirSync(join(root, dir))) {
        const p = join(root, dir, f);
        if (statSync(p).isFile() && /\.ts$/.test(f) && !/\.test\.ts$/.test(f)) files.push(p);
      }
    }
    expect(files.length).toBeGreaterThan(0);
    for (const p of files) {
      const text = readFileSync(p, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
      expect(text, p).not.toMatch(/from ['"](react|next|react-dom)(\/|['"])/);
      expect(text, p).not.toMatch(/\b(window|document|localStorage)\./);
    }
  });
});
