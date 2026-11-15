// The live-probe result contract (PRD 8.7 step 7, PRD 3 wording; T13-05). Every value is plain
// data so it crosses the worker boundary by structured clone. No field is labelled Observed:
// terrain numbers are Derived in the browser, the encoder is Predicted, radar is Unavailable.

export const LIVE_PROBE_SENTENCE = 'Live estimate from public elevation data; not a site assessment.';
export const SURFACE_MODEL_NOTICE =
  'Copernicus GLO-30 is a surface model: vegetation and buildings are part of the heights. No land-cover check (WorldCover) runs in the live probe.';
export const DERIVED_LIVE_LABEL = 'Derived (live, in browser)';
export const RADAR_UNAVAILABLE_REASON = 'Unavailable for live probes';
export const ENCODER_NOT_EVALUATED = 'encoder_not_evaluated';

export function predictedLabel(g5Status: string): string {
  return `Predicted (encoder v1, G5 ${g5Status})`;
}

export interface ProbeRequest {
  readonly id: number;
  readonly latDeg: number;
  readonly lonDeg: number;
}

/** A terrain variant as the comparison reads it (CONTRACTS FingerprintRecord TerrainVariant). */
export interface ProbeVariant {
  detrend: 'none' | 'plane';
  supported_lags_m: number[];
  nu_m: Record<string, number | null>;
  hurst: number | null;
  hurst_reason: string | null;
}

export interface ProbeTerrain {
  label: typeof DERIVED_LIVE_LABEL;
  isotropic_rule: 'direction_mean' | 'pooled_pairs';
  analysis_grid_m: number;
  valid_fraction: number;
  relief: ProbeVariant;
  texture: ProbeVariant | null;
  texture_reason: string | null;
}

export type DistanceValue =
  | { status: 'available'; value: number; unit: string; label: string; shared_lags_m: number[]; reason: null }
  | { status: 'unavailable'; value: null; unit: string; label: 'Unavailable'; shared_lags_m: number[]; reason: string };

/** Three separate families per target (PRD 6.5, 11.10): never combined, summed or ranked. */
export interface TargetDistances {
  target_id: string;
  target_window_id: string;
  terrain_relief: DistanceValue;
  terrain_texture: DistanceValue;
  radar: DistanceValue;
  encoder: DistanceValue;
}

export interface StageTimings {
  dem_read_ms: number;
  resample_ms: number;
  fingerprint_ms: number;
  model_load_ms: number | null;
  inference_ms: number | null;
  total_ms: number;
}

export interface DemRead {
  mode: 'direct' | 'proxy';
  tiles_read: string[];
  tiles_missing: string[];
  bytes: number;
  requests: number;
  prd_budget_bytes: number;
  within_prd_budget: boolean;
}

export interface ProbeSuccess {
  kind: 'result';
  request_id: number;
  centre: { lat_deg: number; lon_deg: number };
  sentence: typeof LIVE_PROBE_SENTENCE;
  surface_notice: typeof SURFACE_MODEL_NOTICE;
  source_product: string;
  terrain: ProbeTerrain;
  targets: TargetDistances[];
  targets_reason: string | null;
  worldcover: { status: 'unavailable'; reason: string };
  dem: DemRead;
  timings: StageTimings;
  bundle_version: string | null;
  model_id: string | null;
}

export interface ProbeStop {
  kind: 'stop';
  request_id: number;
  centre: { lat_deg: number; lon_deg: number };
  reason: 'below_min_valid_fraction';
  valid_fraction: number;
  min_valid_fraction: number;
  dem: DemRead;
  timings: StageTimings;
}

export type ProbeFailureReason =
  | 'tile_missing'
  | 'reader_failed'
  | 'model_load_failed'
  | 'invalid_request'
  | 'aborted'
  | 'superseded';

export interface ProbeFailure {
  kind: 'failure';
  request_id: number;
  reason: ProbeFailureReason;
  detail: string;
}

export type ProbeResult = ProbeSuccess | ProbeStop | ProbeFailure;

/** A runtime check that a message from the worker is a ProbeResult (no functions, no classes). */
export function isProbeResult(value: unknown): value is ProbeResult {
  if (value === null || typeof value !== 'object') return false;
  const v = value as { kind?: unknown; request_id?: unknown };
  if (typeof v.request_id !== 'number') return false;
  if (v.kind === 'failure') return typeof (value as ProbeFailure).reason === 'string';
  if (v.kind === 'stop') return typeof (value as ProbeStop).valid_fraction === 'number';
  if (v.kind === 'result') {
    const r = value as { sentence?: unknown; terrain?: { label?: unknown }; targets?: unknown };
    return r.sentence === LIVE_PROBE_SENTENCE && r.terrain?.label === DERIVED_LIVE_LABEL && Array.isArray(r.targets);
  }
  return false;
}

/** A target's terrain fingerprint as the bundle carries it (FingerprintRecord.terrain). */
export interface ProbeTargetFingerprint {
  target_id: string;
  window_id: string;
  body: string;
  isotropic_rule: 'direction_mean' | 'pooled_pairs';
  relief: Pick<ProbeVariant, 'supported_lags_m' | 'nu_m'>;
  texture: Pick<ProbeVariant, 'supported_lags_m' | 'nu_m'> | null;
}
