// The live probe for one window (PRD 8.7, T13-05): GLO-30 range reads, the D29 resampler, the
// 0.8 valid-fraction stop, the terrain fingerprint and the per-target distances, each family in
// its own field. A plain module so it is testable without a Worker; workers/probe.worker.ts only
// exposes it. No DOM, React or Next import.

import {
  ANALYSIS_GRID,
  EARTH_RADIUS_M,
  ENCODER_GRID,
  encoderChannels,
  toOnnxInput,
  PRD_TERRAIN_CONFIG,
  geographicLookup,
  probeQuality,
  resampleWindow,
  terrainDistance,
  terrainFingerprint,
} from '@terraluna/core';
import type { TerrainFingerprint, TerrainVariant } from '@terraluna/core';

import {
  type ByteMeter,
  type Cop30Mode,
  PRD_DEM_BUDGET_BYTES,
  ReaderError,
  TileMissingError,
  type TileWindow,
  mosaic,
  newByteMeter,
  readTileWindow,
} from './reader.ts';
import {
  DERIVED_LIVE_LABEL,
  type DemRead,
  type DistanceValue,
  ENCODER_NOT_EVALUATED,
  LIVE_PROBE_SENTENCE,
  type ProbeRequest,
  type ProbeResult,
  type ProbeTargetFingerprint,
  type ProbeVariant,
  RADAR_UNAVAILABLE_REASON,
  SURFACE_MODEL_NOTICE,
  type StageTimings,
  type TargetDistances,
} from './result.ts';
import { tilesForBounds, windowBounds } from './tiles.ts';
import { type EmbeddingSession, type EncoderSpec, type ModelBackground, EmbeddingError, cosineDistance, embeddingLabel, modelPercentile } from './embedding.ts';

/** The isotropic rule the pipeline's real cards use (realcard.ISOTROPIC_RULE, t4-real). */
export const PROBE_ISOTROPIC_RULE = 'direction_mean' as const;
/** Source pixels kept around the window so every bilinear neighbour is read. */
export const SOURCE_MARGIN_PX = 2;
export const SOURCE_PRODUCT = 'Copernicus DEM GLO-30 (COP30), public AWS bucket copernicus-dem-30m';
const DEG = 180 / Math.PI;

export interface ProbeDeps {
  readonly mode: Cop30Mode;
  readonly origin?: string;
  readonly fetch?: typeof fetch;
  readonly now?: () => number;
  /** Target terrain fingerprints from the release bundle, or the reason there are none. */
  readonly targets: { fingerprints: ProbeTargetFingerprint[]; reason: null } | { fingerprints: []; reason: string };
  readonly bundleVersion: string | null;
  /**
   * The exported encoder, when the bundle ships one: its spec, a session factory (created once
   * and reused by the worker) and each target's ModelBackground. Null: the encoder distance is
   * Unavailable (encoder_not_evaluated).
   */
  readonly encoder?: {
    readonly spec: EncoderSpec;
    readonly session: () => Promise<EmbeddingSession>;
    readonly backgrounds: Readonly<Record<string, ModelBackground | null>>;
  } | null;
}

function demRead(mode: Cop30Mode, meter: ByteMeter, read: string[], missing: string[]): DemRead {
  return {
    mode,
    tiles_read: read,
    tiles_missing: missing,
    bytes: meter.bytes,
    requests: meter.requests,
    prd_budget_bytes: PRD_DEM_BUDGET_BYTES,
    within_prd_budget: meter.bytes <= PRD_DEM_BUDGET_BYTES,
  };
}

/** The contract TerrainVariant view of a fingerprint variant: nu keyed by lag in metres. */
export function contractVariant(fp: TerrainFingerprint, variant: TerrainVariant, detrend: 'none' | 'plane'): ProbeVariant {
  const nu: Record<string, number | null> = {};
  PRD_TERRAIN_CONFIG.lags_m.forEach((lag, i) => {
    nu[lag.toFixed(1)] = variant.nu_m[i] ?? null;
  });
  return {
    detrend,
    supported_lags_m: [...fp.supported_lags_m],
    nu_m: nu,
    hurst: variant.hurst.H,
    hurst_reason: variant.hurst.reason,
  };
}

function distance(a: Pick<ProbeVariant, 'supported_lags_m' | 'nu_m'> | null, b: Pick<ProbeVariant, 'supported_lags_m' | 'nu_m'> | null, missing: string): DistanceValue {
  const unit = 'RMS of log nu';
  if (a === null || b === null) return { status: 'unavailable', value: null, unit, label: 'Unavailable', shared_lags_m: [], reason: missing };
  const out = terrainDistance(a, b);
  return out.status === 'available'
    ? { status: 'available', value: out.value, unit, label: DERIVED_LIVE_LABEL, shared_lags_m: out.lags_m, reason: null }
    : { status: 'unavailable', value: null, unit, label: 'Unavailable', shared_lags_m: out.lags_m, reason: out.reason };
}

const ENCODER_UNAVAILABLE = (reason: string): DistanceValue => ({
  status: 'unavailable',
  value: null,
  unit: 'cosine distance',
  label: 'Unavailable',
  shared_lags_m: [],
  reason,
  background_percentile: null,
  background_percentile_reason: reason,
});

function targetDistances(relief: ProbeVariant, texture: ProbeVariant | null, target: ProbeTargetFingerprint, encoder: DistanceValue): TargetDistances {
  const ruleMismatch = target.isotropic_rule !== PROBE_ISOTROPIC_RULE;
  const mismatch = (unit: string): DistanceValue => ({
    status: 'unavailable',
    value: null,
    unit,
    label: 'Unavailable',
    shared_lags_m: [],
    reason: 'isotropic_rule_mismatch',
  });
  return {
    target_id: target.target_id,
    target_window_id: target.window_id,
    terrain_relief: ruleMismatch ? mismatch('RMS of log nu') : distance(relief, target.relief, 'nu_missing'),
    terrain_texture: ruleMismatch ? mismatch('RMS of log nu') : distance(texture, target.texture, 'texture_unavailable'),
    radar: { status: 'unavailable', value: null, unit: 'CPR Wasserstein-1', label: 'Unavailable', shared_lags_m: [], reason: RADAR_UNAVAILABLE_REASON },
    encoder,
  };
}

function aborted(request: ProbeRequest): ProbeResult {
  return { kind: 'failure', request_id: request.id, reason: 'aborted', detail: 'the probe was aborted' };
}

export async function runProbe(request: ProbeRequest, deps: ProbeDeps, signal?: AbortSignal): Promise<ProbeResult> {
  const now = deps.now ?? (() => performance.now());
  const t0 = now();
  const { latDeg, lonDeg } = request;
  if (!(Number.isFinite(latDeg) && Number.isFinite(lonDeg) && Math.abs(latDeg) <= 90 && Math.abs(lonDeg) <= 180)) {
    return { kind: 'failure', request_id: request.id, reason: 'invalid_request', detail: 'lat must lie in [-90, 90] and lon in [-180, 180]' };
  }
  const lon0 = lonDeg >= 180 ? lonDeg - 360 : lonDeg;
  const bounds = windowBounds(latDeg, lon0, EARTH_RADIUS_M, deps.encoder ? [ANALYSIS_GRID, ENCODER_GRID] : [ANALYSIS_GRID]);
  const selection = tilesForBounds(bounds, 0.01);
  if (selection.status === 'unavailable') {
    return { kind: 'failure', request_id: request.id, reason: 'reader_failed', detail: selection.reason };
  }
  const meter = newByteMeter();
  const config = { mode: deps.mode, meter, ...(deps.origin === undefined ? {} : { origin: deps.origin }), ...(deps.fetch === undefined ? {} : { fetch: deps.fetch }) };
  const windows: TileWindow[] = [];
  const missing: string[] = [];
  try {
    const reads = await Promise.all(
      selection.tiles.map(async (tile) => {
        try {
          return await readTileWindow(tile, bounds, SOURCE_MARGIN_PX, config, signal);
        } catch (error) {
          if (error instanceof TileMissingError) {
            missing.push(tile.name);
            return null;
          }
          throw error;
        }
      }),
    );
    for (const w of reads) if (w !== null) windows.push(w);
  } catch (error) {
    if (signal?.aborted || (error instanceof ReaderError && error.reason === 'aborted')) return aborted(request);
    const detail = error instanceof ReaderError ? `${error.reason}: ${error.message}` : `reader_failed: ${(error as Error).message}`;
    return { kind: 'failure', request_id: request.id, reason: 'reader_failed', detail };
  }
  if (signal?.aborted) return aborted(request);
  const tDem = now();
  const joined = mosaic(windows, missing);
  if ('reason' in joined) {
    const reason = missing.length > 0 && windows.length === 0 ? 'tile_missing' : 'reader_failed';
    return { kind: 'failure', request_id: request.id, reason, detail: `${joined.reason}; missing tiles: ${missing.join(', ') || 'none'}` };
  }
  const lookup = geographicLookup({
    originLonDeg: joined.originLonDeg,
    originLatDeg: joined.originLatDeg,
    pixelWidthDeg: joined.pixelWidthDeg,
    pixelHeightDeg: joined.pixelHeightDeg,
  });
  const resampled = resampleWindow(
    // GLO-30 declares no nodata: only non-finite values (and absent tiles, NaN here) are missing.
    { values: joined.values, rows: joined.rows, columns: joined.columns, nodata: null },
    lookup,
    { grid: ANALYSIS_GRID, body: 'earth', lat0Rad: latDeg / DEG, lon0Rad: lon0 / DEG },
  );
  const tResample = now();
  const quality = probeQuality(resampled);
  const dem = demRead(deps.mode, meter, joined.tilesRead, joined.tilesMissing);
  if (!quality.pass) {
    const t = now();
    return {
      kind: 'stop',
      request_id: request.id,
      centre: { lat_deg: latDeg, lon_deg: lonDeg },
      reason: quality.reason,
      valid_fraction: quality.valid_fraction,
      min_valid_fraction: PRD_TERRAIN_CONFIG.min_valid_fraction,
      dem,
      timings: { dem_read_ms: tDem - t0, resample_ms: tResample - tDem, fingerprint_ms: 0, model_load_ms: null, inference_ms: null, total_ms: t - t0 },
    };
  }
  const fp = terrainFingerprint(resampled.values, resampled.valid, {
    grid: ANALYSIS_GRID,
    config: PRD_TERRAIN_CONFIG,
    isotropicRule: PROBE_ISOTROPIC_RULE,
    coarseningFactor: 1,
  });
  const reliefVariant = fp.variants.none;
  const textureVariant = fp.variants.plane;
  if (reliefVariant === undefined) throw new Error('the PRD config always measures relief');
  const relief = contractVariant(fp, reliefVariant, 'none');
  const texture = textureVariant && textureVariant.reason === null ? contractVariant(fp, textureVariant, 'plane') : null;
  const tFp = now();

  // Encoder (experimental): the 64 x 64 window at 78.125 m from the same D29 resampler.
  let modelLoadMs: number | null = null;
  let inferenceMs: number | null = null;
  const encoderFor = new Map<string, DistanceValue>();
  let encoderReason: string = ENCODER_NOT_EVALUATED;
  if (deps.encoder) {
    const enc = deps.encoder;
    const window64 = resampleWindow(
      { values: joined.values, rows: joined.rows, columns: joined.columns, nodata: null },
      lookup,
      { grid: ENCODER_GRID, body: 'earth', lat0Rad: latDeg / DEG, lon0Rad: lon0 / DEG },
    );
    const channels = encoderChannels(window64.values, window64.valid);
    if (channels.empty) {
      encoderReason = 'encoder_window_empty';
    } else {
      try {
        const tLoad = now();
        const session = await enc.session();
        const tInfer = now();
        modelLoadMs = tInfer - tLoad;
        const embedding = await session.embed(toOnnxInput(channels), 1);
        inferenceMs = now() - tInfer;
        if (signal?.aborted) return aborted(request);
        for (const t of deps.targets.fingerprints) {
          const background = enc.backgrounds[t.target_id] ?? null;
          if (background === null || background.target_embedding.length === 0) {
            encoderFor.set(t.target_id, ENCODER_UNAVAILABLE(ENCODER_NOT_EVALUATED));
            continue;
          }
          const d = cosineDistance(embedding, background.target_embedding);
          if (d.status === 'unavailable') {
            encoderFor.set(t.target_id, ENCODER_UNAVAILABLE(d.reason));
            continue;
          }
          const p = modelPercentile(d.value, t.target_id, background, enc.spec);
          encoderFor.set(t.target_id, {
            status: 'available',
            value: d.value,
            unit: 'cosine distance',
            label: embeddingLabel(enc.spec),
            shared_lags_m: [],
            reason: null,
            background_percentile: p.value,
            background_percentile_reason: p.reason,
          });
        }
      } catch (error) {
        if (error instanceof EmbeddingError) {
          return { kind: 'failure', request_id: request.id, reason: 'model_load_failed', detail: `${error.reason}: ${error.message}` };
        }
        return { kind: 'failure', request_id: request.id, reason: 'model_load_failed', detail: (error as Error).message };
      }
    }
  }
  const targets = deps.targets.fingerprints.map((t) =>
    targetDistances(relief, texture, t, encoderFor.get(t.target_id) ?? ENCODER_UNAVAILABLE(encoderReason)),
  );
  const tEnd = now();
  const timings: StageTimings = {
    dem_read_ms: tDem - t0,
    resample_ms: tResample - tDem,
    fingerprint_ms: tFp - tResample,
    model_load_ms: modelLoadMs,
    inference_ms: inferenceMs,
    total_ms: tEnd - t0,
  };
  return {
    kind: 'result',
    request_id: request.id,
    centre: { lat_deg: latDeg, lon_deg: lonDeg },
    sentence: LIVE_PROBE_SENTENCE,
    surface_notice: SURFACE_MODEL_NOTICE,
    source_product: SOURCE_PRODUCT,
    terrain: {
      label: DERIVED_LIVE_LABEL,
      isotropic_rule: PROBE_ISOTROPIC_RULE,
      analysis_grid_m: ANALYSIS_GRID.spacingM,
      valid_fraction: fp.valid_fraction,
      relief,
      texture,
      texture_reason: texture === null ? (textureVariant?.reason ?? 'texture_unavailable') : null,
    },
    targets,
    targets_reason: deps.targets.reason,
    worldcover: { status: 'unavailable', reason: 'WorldCover is not checked in the live probe (no CORS; G0-03)' },
    dem,
    timings,
    bundle_version: deps.bundleVersion,
    model_id: deps.encoder?.spec.model_id ?? null,
    model_status: deps.encoder?.spec.status ?? null,
    g5_status: deps.encoder?.spec.g5_status ?? null,
  };
}
