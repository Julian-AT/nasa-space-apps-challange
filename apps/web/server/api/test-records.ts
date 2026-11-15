// Schema-valid synthetic records for the route tests (never imported by the app). Every id is
// obviously fake (moon-test-x0-y0, earth-test-x1-y1) and every number is test input, not an
// observation. Each builder parses its result with the @terraluna/schema schema, so a record that
// drifts from the generated contract fails here, not in a route. Nothing is written to fixtures/
// or to a bundle.
import {
  ComparisonRecordSchema,
  FingerprintRecordSchema,
  MetricsSchema,
  ModelRecordSchema,
  TargetsSchema,
  WindowsSchema,
  type ComparisonRecord,
  type Metrics,
  type ModelRecord,
  type WindowRecord,
  type Windows,
  type FingerprintRecord,
  type Targets,
} from '@terraluna/schema';

import { type PulledBundle } from '../../lib/bundle.ts';
import { mockReader } from './test-support.ts';

export const RUN_ID = `sha256:${'a'.repeat(64)}`;
export const TARGET_ID = 'moon-test-x0-y0';
export const TARGET_WINDOW_ID = 'moon-test-x0-y0';
export const EARTH_ID = (n: number) => `earth-test-x${String(n)}-y${String(n)}`;

const ring = [
  [0, 0],
  [1000, 0],
  [1000, 1000],
  [0, 1000],
  [0, 0],
];

function polygon(body: string) {
  return {
    frame: body === 'earth' ? 'utm' : 'polar_stereographic',
    crs: 'TEST-CRS',
    epsg: null,
    utm_zone: null,
    ring_m: ring,
    ring_lonlat: ring,
  };
}

export function windowRecord(id: string): WindowRecord {
  const body = id.split('-')[0] ?? 'earth';
  return {
    window_id: id,
    body,
    polygon: polygon(body),
    area_m2: 1_000_000,
    analysis_grid_m: 10,
    product_ids: ['test-product'],
    valid_fraction_by_observable: { relief: 1 },
    observed_interpolated_quality: {
      observed_fraction: null,
      interpolated_fraction: null,
      source: null,
      reason: 'not_assessed',
    },
    environment_flags: {
      search_region_id: null,
      scene_ids: [],
      acquisition_dates: [],
      snow_or_freeze: null,
      moisture_state: null,
      surface_change_since_acquisition: null,
      permanent_shadow_fraction: null,
      surface_model_note: null,
    },
    evidence_status_by_field: {},
  } as unknown as WindowRecord;
}

export interface RadarOptions {
  /** No radar data: the stored fingerprint has `radar` null. */
  readonly none?: boolean;
  /** A stored reason: the fingerprint carries it and no distributions. */
  readonly reason?: string;
}

/** A radar FingerprintRecord of a window. */
export function radarFingerprint(id: string, options: RadarOptions = {}): FingerprintRecord {
  const body = id.split('-')[0] ?? 'moon';
  const radar =
    options.none === true
      ? null
      : {
          product_id: 'test-radar-product',
          band: 'S',
          frequency_or_wavelength: 'test',
          convention: null,
          support_m: 100,
          aggregation: 'ratio_of_mean_powers',
          blocks_independent: false,
          n_blocks: 0,
          n_valid: 0,
          valid_fraction: 0,
          cpr_ratio_of_mean_powers: options.reason === undefined ? { n: 1, values: [0.5], min: 0.5, median: 0.5, p05: 0.5, p95: 0.5, max: 0.5 } : null,
          cpr_mean_of_pixelwise_ratios: null,
          sc: null,
          oc: null,
          total_power: null,
          incidence_deg: null,
          handedness_ratio: null,
          reason: options.reason ?? null,
        };
  return FingerprintRecordSchema.parse({
    window_id: id,
    body,
    kind: 'radar',
    product_ids: ['test-radar-product'],
    evidence_label: radar === null ? 'Unavailable' : 'Observed',
    input_hash: 'b'.repeat(64),
    run_id: RUN_ID,
    terrain: null,
    radar,
  });
}

export function windowEntry(
  id: string,
  extra: { readonly radar?: FingerprintRecord[]; readonly card_id?: string | null } = {},
): Windows[number] {
  return WindowsSchema.element.parse({
    window: windowRecord(id),
    terrain: null,
    radar: extra.radar ?? [],
    status: 'eligible',
    reasons: [],
    grid_index: [0, 0],
    card_id: extra.card_id ?? null,
  });
}

export function targetRecord(
  overrides: Readonly<Record<string, unknown>> = {},
): Targets[number] {
  return TargetsSchema.element.parse({
    target_id: TARGET_ID,
    mock: false,
    run_id: null,
    body: 'moon',
    name: 'Test target',
    description: 'A synthetic target for route tests',
    product_ids: ['test-product'],
    crs: 'TEST-CRS',
    projection: { kind: 'polar_stereographic', params: {} },
    footprint: null,
    primary_window_id: null,
    window_grid: { window_m: 1000, stride_m: 500, n_generated: null, n_eligible: null },
    reference_window_ids: [],
    shortlist: null,
    shortlist_reason: 'test_no_shortlist',
    shipping_mode: null,
    tile_layers: [],
    ...overrides,
  });
}

type Entry = Record<string, unknown>;

/** A DistanceEntry: available with a value, or unavailable with a reason. */
export function distance(value: number | null, reason = 'test_unavailable'): Entry {
  const available = value !== null;
  return {
    value,
    unit: 'test_unit',
    evidence_label: available ? 'Derived' : 'Unavailable',
    status: available ? 'available' : 'unavailable',
    reason: available ? null : reason,
    reference_spread: null,
    reference_spread_reason: 'test_no_spread',
    background_percentile: null,
    background_percentile_reason: 'test_no_background',
    tied: null,
    tie_group: null,
    tie_reason: null,
  };
}

export interface ComparisonOptions {
  readonly earth: string;
  readonly relief: number | null;
  readonly texture: number | null;
  readonly radar?: number | null;
  readonly model?: number | null;
  readonly mode?: 'A' | 'B' | 'C' | null;
}

const COMPONENT = {
  status: 'unavailable',
  evidence_label: 'Unavailable',
  reason: 'test_not_computed',
  note: null,
  by_family: null,
  n_requested: null,
  n_used: null,
  scenarios: null,
  rank_frequency: null,
};

export function comparison(options: ComparisonOptions): ComparisonRecord {
  const model = options.model ?? null;
  return ComparisonRecordSchema.parse({
    target_window_id: TARGET_WINDOW_ID,
    earth_window_id: options.earth,
    shipping_mode: options.mode ?? 'A',
    comparable_observables: ['relief'],
    excluded_observables_and_reasons: [],
    distances_by_family: {
      terrain_relief: distance(options.relief),
      terrain_texture: distance(options.texture),
      radar_cpr: distance(options.radar ?? null, 'test_radar_not_in_mode'),
      model_embedding: {
        ...distance(model),
        evidence_label: model === null ? 'Unavailable' : 'Predicted',
      },
    },
    uncertainty_components: {
      heterogeneity: COMPONENT,
      measurement_error: COMPONENT,
      statistical_prediction_error: COMPONENT,
      scenario_sensitivity: COMPONENT,
    },
    sort_rule: 'test_rule',
    claim_limits: ['test claim limit'],
    source_ids: ['test-product'],
    run_id: RUN_ID,
  });
}

export function modelRecord(g5 = 'NOT_RUN'): ModelRecord {
  return ModelRecordSchema.parse({
    model_id: 'test-model-alpha',
    version: 'test',
    architecture: 'test architecture',
    parameter_count: 1,
    training_data: { bodies: ['moon'], product_ids: ['test-product'], window_counts: { moon: { train: 1, val: null, test: null } }, split_groups: {} },
    objectives_and_weights: {
      vicreg: { invariance: 1, variance: 1, covariance: 1 },
      alpha_phys: 1,
      lambda_max: 1,
      schedule: 'test',
    },
    seeds: [0],
    metrics_with_ci: [],
    g5_status: g5,
    intended_use: 'test',
    limitations: 'test',
    licence: 'test',
    onnx_sha256: null,
  });
}

export function metricsFile(g5: string): Metrics {
  return MetricsSchema.parse({
    mock: false,
    run_id: null,
    model_id: 'test-model-alpha',
    evaluated_split: 'test',
    g5: { status: g5, conditions: [], failed_tests: [] },
    tests: [],
    ablations: [],
    run_record: {
      config_hash: 'f'.repeat(64),
      data_manifest_sha256: 'c'.repeat(64),
      split_map_sha256: 'd'.repeat(64),
      checkpoint_sha256s: [],
      git_sha: 'test',
      uv_lock_sha256: 'e'.repeat(64),
      hardware: 'test',
      gpu_hours_total: null,
      gpu_hours_partial: false,
    },
  });
}

export interface MemoryBundle {
  readonly targets?: Targets | null;
  readonly windows?: Readonly<Record<string, Windows>>;
  readonly comparisons?: Readonly<Record<string, ComparisonRecord[]>>;
  readonly modelCard?: ModelRecord | null;
  readonly metrics?: Metrics | null;
  readonly cards?: readonly string[];
  /** manifest.json overrides, such as model. */
  readonly manifest?: Readonly<Record<string, unknown>>;
  /** G5 status of validation.json; null for no G5 record. */
  readonly g5?: string | null;
}

/**
 * A bundle held in memory: the mock bundle's manifest and validation with these records in place
 * of its (absent) optional files. The records are test input; the API reads them as it reads a
 * loaded bundle.
 */
export function memoryReader(options: MemoryBundle = {}): () => PulledBundle {
  const base = mockReader();
  const template = base.validation[0];
  if (template === undefined) throw new Error('the mock validation.json has no gates');
  const g5 = options.g5 === null ? [] : [{ ...template, gate_id: 'G5', status: options.g5 ?? 'NOT_RUN' }];
  const validation = [
    ...base.validation.filter((r) => r.gate_id !== 'G5'),
    ...g5,
  ] as unknown as PulledBundle['validation'];
  return () =>
    ({
      ...base,
      manifest: { ...base.manifest, mock: false, ...options.manifest },
      validation,
      records: {
        ...base.records,
        targets: options.targets ?? null,
        windows: new Map(Object.entries(options.windows ?? {})),
        comparisons: new Map(Object.entries(options.comparisons ?? {})),
        cards: new Map((options.cards ?? []).map((id) => [id, {}])),
        modelCard: options.modelCard ?? null,
        metrics: options.metrics ?? null,
      },
    }) as unknown as PulledBundle;
}
