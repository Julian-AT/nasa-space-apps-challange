// Bounded Copernicus GLO-30 COG reads for the live probe (PRD 8.7 step 2, 8.5; T13-04).
//
// geotiff.js reads only the internal COG tiles a window needs. Its byte ranges go through a
// custom client that splits each range into requests of at most 4 MB (the proxy's limit),
// counts every byte against the probe's budget, and turns every failure into a typed reason.
// The mode ('direct' or 'proxy', cop30_mode) is a required parameter with no default: G0-03 chose
// proxy because the bucket sends no CORS headers. Runs in the probe worker: no DOM or Node API.

import { BaseClient, BaseResponse, fromCustomClient } from 'geotiff';

import { DEM_PROXY_MAX_RANGE_BYTES, DEM_PROXY_PATH, DEM_TILE_PATTERN, DEM_UPSTREAM_BASE } from '../../server/api/dem-contract.ts';
import type { GeoBounds, TileRef } from './tiles.ts';

export type Cop30Mode = 'direct' | 'proxy';

/** PRD 8.7: the first live probe reads at most 4 MB of DEM. Measured and reported per probe. */
export const PRD_DEM_BUDGET_BYTES = 4 * 1024 * 1024;
/**
 * Hard safety cap: a probe aborts (reason dem_byte_cap_exceeded) before reading more than this.
 * GLO-30 internal tiles are 1024 x 1024 deflate blocks of up to about 2.5 MB, so a window that
 * straddles internal tiles can need more than the PRD budget; that is reported, not hidden.
 */
export const DEM_BYTE_CAP = 12 * 1024 * 1024;
/** Retries of a request that met a 502, 503 or 504 or a network failure. */
export const DEM_TRANSIENT_RETRIES = 1;
/** Per-request timeout. */
export const DEM_REQUEST_TIMEOUT_MS = 20_000;

export type ReaderFailureReason =
  | 'http_error'
  | 'range_not_honoured'
  | 'network_error'
  | 'timeout'
  | 'aborted'
  | 'dem_byte_cap_exceeded'
  | 'invalid_tile_name'
  | 'unsupported_cog_layout';

export class ReaderError extends Error {
  override name = 'ReaderError';
  readonly reason: ReaderFailureReason;
  constructor(reason: ReaderFailureReason, message: string) {
    super(message);
    this.reason = reason;
  }
}

export interface ByteMeter {
  bytes: number;
  requests: number;
  readonly cap: number;
}

export function newByteMeter(cap = DEM_BYTE_CAP): ByteMeter {
  return { bytes: 0, requests: 0, cap };
}

export interface ReaderConfig {
  readonly mode: Cop30Mode;
  /** Origin for proxy mode (location.origin in the worker); ignored in direct mode. */
  readonly origin?: string;
  readonly fetch?: typeof fetch;
  readonly meter: ByteMeter;
}

/** The URL a tile is read from in the given mode. Throws for an undefined or other mode. */
export function tileUrl(mode: Cop30Mode, tile: string, origin?: string): string {
  if (!DEM_TILE_PATTERN.test(tile)) throw new ReaderError('invalid_tile_name', `not a GLO-30 tile name: ${tile}`);
  if (mode === 'proxy') return `${origin ?? ''}${DEM_PROXY_PATH}/${tile}`;
  if ((mode as string) === 'direct') return `${DEM_UPSTREAM_BASE}/${tile}/${tile}.tif`;
  throw new TypeError(`cop30 mode must be 'direct' or 'proxy', got ${JSON.stringify(mode)}`);
}

class ChunkResponse extends BaseResponse {
  private readonly data: ArrayBuffer;
  private readonly headers: Record<string, string>;
  constructor(data: ArrayBuffer, headers: Record<string, string>) {
    super();
    this.data = data;
    this.headers = headers;
  }
  override get status(): number {
    return 206;
  }
  override getHeader(name: string): string | undefined {
    return this.headers[name.toLowerCase()];
  }
  override getData(): Promise<ArrayBuffer> {
    return Promise.resolve(this.data);
  }
}

function linkSignals(outer: AbortSignal | undefined): AbortSignal {
  const timeout = AbortSignal.timeout(DEM_REQUEST_TIMEOUT_MS);
  return outer ? AbortSignal.any([outer, timeout]) : timeout;
}

/** geotiff.js client: one Range at a time, split into <= 4 MB requests, all bytes metered. */
class MeteredRangeClient extends BaseClient {
  /** The typed failure of the last request; geotiff.js wraps transport errors in its own. */
  failure: ReaderError | TileMissingError | null = null;

  private readonly config: ReaderConfig;
  private readonly outer: AbortSignal | undefined;
  constructor(url: string, config: ReaderConfig, outer: AbortSignal | undefined) {
    super(url);
    this.config = config;
    this.outer = outer;
  }

  override async request(options?: RequestInit): Promise<BaseResponse> {
    try {
      return await this.requestRange(options);
    } catch (error) {
      if (error instanceof ReaderError || error instanceof TileMissingError) this.failure = error;
      throw error;
    }
  }

  private async requestRange(options?: RequestInit): Promise<BaseResponse> {
    const headers = new Headers(options?.headers);
    const match = /^bytes=(\d+)-(\d+)$/.exec(headers.get('range') ?? '');
    if (!match) throw new ReaderError('unsupported_cog_layout', 'the COG reader asked for a request without a single closed Range');
    const first = Number(match[1]);
    const last = Number(match[2]);
    const parts: ArrayBuffer[] = [];
    let total = '*';
    let end = first - 1;
    for (let start = first; start <= last; start += DEM_PROXY_MAX_RANGE_BYTES) {
      const want = Math.min(last, start + DEM_PROXY_MAX_RANGE_BYTES - 1);
      const chunk = await this.fetchRange(start, want, options?.signal ?? this.outer);
      parts.push(chunk.data);
      total = chunk.total;
      end = start + chunk.data.byteLength - 1;
      if (end < want) break; // the file ended (the server clamps a range at its end)
    }
    const joined = new Uint8Array(end - first + 1);
    let offset = 0;
    for (const part of parts) {
      joined.set(new Uint8Array(part), offset);
      offset += part.byteLength;
    }
    return new ChunkResponse(joined.buffer, {
      'content-range': `bytes ${String(first)}-${String(end)}/${total}`,
      'content-type': 'image/tiff',
    });
  }

  private async fetchRange(start: number, end: number, outer: AbortSignal | null | undefined): Promise<{ data: ArrayBuffer; total: string }> {
    const meter = this.config.meter;
    const length = end - start + 1;
    if (meter.bytes + length > meter.cap) {
      throw new ReaderError('dem_byte_cap_exceeded', `reading ${String(length)} more bytes would pass the ${String(meter.cap)} byte cap`);
    }
    const doFetch = this.config.fetch ?? fetch;
    let response: Response | null = null;
    // One retry, after a short pause, for a transient gateway answer or network failure.
    for (let attempt = 0; attempt <= DEM_TRANSIENT_RETRIES; attempt += 1) {
      if (attempt > 0) await new Promise((resolve) => setTimeout(resolve, 250));
      try {
        response = await doFetch(this.url, {
          headers: { Range: `bytes=${String(start)}-${String(end)}` },
          signal: linkSignals(outer ?? undefined),
        });
      } catch (error) {
        const name = (error as { name?: string }).name;
        if (name === 'TimeoutError') throw new ReaderError('timeout', `DEM request timed out after ${String(DEM_REQUEST_TIMEOUT_MS)} ms`);
        if (name === 'AbortError') throw new ReaderError('aborted', 'the probe was aborted');
        if (attempt < DEM_TRANSIENT_RETRIES) continue;
        throw new ReaderError('network_error', `the DEM request failed (network or CORS): ${(error as Error).message}`);
      }
      if (![502, 503, 504].includes(response.status) || attempt === DEM_TRANSIENT_RETRIES) break;
      meter.requests += 1;
      await response.body?.cancel();
    }
    if (response === null) throw new ReaderError('network_error', 'the DEM request failed');
    meter.requests += 1;
    if (response.status === 404) {
      await response.body?.cancel();
      throw new TileMissingError(this.url);
    }
    if (response.status === 200) {
      // A server that ignores Range would send the whole file: stop before reading it.
      await response.body?.cancel();
      throw new ReaderError('range_not_honoured', 'the DEM server answered 200 instead of 206 (Range ignored)');
    }
    if (response.status !== 206) {
      await response.body?.cancel();
      throw new ReaderError('http_error', `the DEM server answered HTTP ${String(response.status)}`);
    }
    const contentRange = /^bytes (\d+)-(\d+)\/(\d+|\*)$/.exec(response.headers.get('content-range') ?? '');
    if (!contentRange || Number(contentRange[1]) !== start || Number(contentRange[2]) > end) {
      await response.body?.cancel();
      throw new ReaderError('range_not_honoured', 'the DEM server returned a different byte range');
    }
    const data = await response.arrayBuffer();
    meter.bytes += data.byteLength;
    if (data.byteLength !== Number(contentRange[2]) - start + 1) {
      throw new ReaderError('range_not_honoured', 'the DEM response length does not match its Content-Range');
    }
    return { data, total: contentRange[3] ?? '*' };
  }
}

/** GLO-30 has no tile here (ocean or outside coverage): a missing region, never zeros. */
export class TileMissingError extends Error {
  override name = 'TileMissingError';
}

/** A geographic raster window in GDAL's convention (origin = outer corner of pixel (0, 0)). */
export interface TileWindow {
  tile: string;
  /** Float64 copy of the float32 heights, row-major. */
  values: Float64Array;
  rows: number;
  columns: number;
  /** Global GLO-30 pixel indices of this window's pixel (0, 0) in its latitude band. */
  globalColumn: number;
  globalRow: number;
  resLonDeg: number;
  resLatDeg: number;
}

/** GeoTIFF GTRasterTypeGeoKey value for PixelIsPoint (GLO-30 declares AREA_OR_POINT=Point). */
const PIXEL_IS_POINT = 2;

/**
 * Read the part of one tile covering the bounds plus `marginPx` source pixels. Pixel centres
 * follow GDAL's reading of a PixelIsPoint raster: the tie point is a pixel centre.
 */
export async function readTileWindow(
  tile: TileRef,
  bounds: GeoBounds,
  marginPx: number,
  config: ReaderConfig,
  signal?: AbortSignal,
): Promise<TileWindow | null> {
  const url = tileUrl(config.mode, tile.name, config.origin);
  const client = new MeteredRangeClient(url, config, signal);
  try {
    const tiff = await fromCustomClient(client, {}, signal);
    const image = await tiff.getImage(0);
    const width = image.getWidth();
    const height = image.getHeight();
    const [tieLon, tieLat] = image.getOrigin() as [number, number];
    const [resLon, resLatSigned] = image.getResolution() as [number, number];
    const resLat = Math.abs(resLatSigned);
    const keys = image.getGeoKeys() ?? {};
    const isPoint = keys.GTRasterTypeGeoKey === PIXEL_IS_POINT;
    // Centre of pixel (0, 0) in degrees.
    const lon00 = isPoint ? tieLon : tieLon + resLon / 2;
    const lat00 = isPoint ? tieLat : tieLat - resLat / 2;
    const lonMin = bounds.lonMin - tile.lonShiftDeg;
    const lonMax = bounds.lonMax - tile.lonShiftDeg;
    const c0 = Math.max(0, Math.floor((lonMin - lon00) / resLon) - marginPx);
    const c1 = Math.min(width, Math.ceil((lonMax - lon00) / resLon) + marginPx + 1);
    const r0 = Math.max(0, Math.floor((lat00 - bounds.latMax) / resLat) - marginPx);
    const r1 = Math.min(height, Math.ceil((lat00 - bounds.latMin) / resLat) + marginPx + 1);
    if (c1 <= c0 || r1 <= r0) return null;
    const raster = await image.readRasters({ window: [c0, r0, c1, r1], samples: [0], interleave: true, signal });
    const values = Float64Array.from(raster as ArrayLike<number>);
    return {
      tile: tile.name,
      values,
      rows: r1 - r0,
      columns: c1 - c0,
      globalColumn: Math.round((lon00 + tile.lonShiftDeg + 180) / resLon) + c0,
      globalRow: Math.round((90 - lat00) / resLat) + r0,
      resLonDeg: resLon,
      resLatDeg: resLat,
    };
  } catch (error) {
    if (error instanceof TileMissingError) throw error;
    if (error instanceof ReaderError) throw error;
    if (client.failure !== null) throw client.failure;
    // geotiff.js wraps transport errors; keep our typed reason when one is the cause.
    const cause = (error as { cause?: unknown }).cause;
    if (cause instanceof ReaderError || cause instanceof TileMissingError) throw cause;
    if ((error as { name?: string }).name === 'AbortError') throw new ReaderError('aborted', 'the probe was aborted');
    throw new ReaderError('unsupported_cog_layout', `the COG could not be decoded: ${(error as Error).message}`);
  }
}

export interface Mosaic {
  values: Float64Array;
  rows: number;
  columns: number;
  originLonDeg: number;
  originLatDeg: number;
  pixelWidthDeg: number;
  pixelHeightDeg: number;
  tilesRead: string[];
  tilesMissing: string[];
}

/**
 * Join tile windows of one GLO-30 latitude band on their shared global pixel grid. Pixels no
 * tile covers (a missing ocean tile) are NaN, so they are missing, never zero.
 */
export function mosaic(windows: TileWindow[], missing: string[]): Mosaic | { reason: string } {
  if (windows.length === 0) return { reason: 'no_glo30_tile_covers_the_window' };
  const first = windows[0] as TileWindow;
  if (windows.some((w) => w.resLonDeg !== first.resLonDeg || w.resLatDeg !== first.resLatDeg)) {
    return { reason: 'window_spans_a_glo30_spacing_change' };
  }
  const c0 = Math.min(...windows.map((w) => w.globalColumn));
  const r0 = Math.min(...windows.map((w) => w.globalRow));
  const c1 = Math.max(...windows.map((w) => w.globalColumn + w.columns));
  const r1 = Math.max(...windows.map((w) => w.globalRow + w.rows));
  const columns = c1 - c0;
  const rows = r1 - r0;
  const values = new Float64Array(rows * columns).fill(Number.NaN);
  for (const w of windows) {
    for (let r = 0; r < w.rows; r += 1) {
      const row = w.globalRow - r0 + r;
      for (let c = 0; c < w.columns; c += 1) values[row * columns + (w.globalColumn - c0 + c)] = w.values[r * w.columns + c] as number;
    }
  }
  return {
    values,
    rows,
    columns,
    // Outer corner of pixel (0, 0): its centre minus half a pixel, as GDAL reports it.
    originLonDeg: -180 + c0 * first.resLonDeg - first.resLonDeg / 2,
    originLatDeg: 90 - r0 * first.resLatDeg + first.resLatDeg / 2,
    pixelWidthDeg: first.resLonDeg,
    pixelHeightDeg: -first.resLatDeg,
    tilesRead: windows.map((w) => w.tile),
    tilesMissing: missing,
  };
}
