// Small schema-valid synthetic records for the /compare, /model and /paper tests. Ids are
// obviously fake (fake-*), every record is parsed with its schema, and nothing here is written
// to fixtures/ or shipped by the app.
import {
  ComparisonRecordSchema,
  type ComparisonRecord,
  FingerprintRecordSchema,
  type FingerprintRecord,
  MetricsSchema,
  type Metrics,
  ModelRecordSchema,
  type ModelRecord,
  WindowsSchema,
  type Windows,
} from '@terraluna/schema';

import type { PairRecords } from '../components/charts/compare-data.ts';

const HASH = 'a'.repeat(64);
export const RUN_ID = `sha256:${'b'.repeat(64)}`;

export function variant(nu: Record<string, number | null>, unsupported: { lag_m: number; reason: string }[] = []) {
  return {
    detrend: 'plane' as const,
    supported_lags_m: Object.keys(nu).map(Number),
    unsupported_lags: unsupported,
    nu_m: nu,
    nu_by_direction_m: {},
    hurst: null,
    hurst_reason: 'fake_hurst_unsupported',
    tilt: null,
  };
}

export function terrainRecord(
  windowId: string,
  relief: ReturnType<typeof variant>,
  texture: ReturnType<typeof variant>,
): FingerprintRecord {
  return FingerprintRecordSchema.parse({
    window_id: windowId,
    body: windowId.split('-')[0],
    kind: 'terrain',
    product_ids: ['fake-product'],
    evidence_label: 'Derived',
    input_hash: HASH,
    run_id: RUN_ID,
    terrain: { analysis_grid_m: 90, coarsening_factor: null, resampler: 'aeqd_sphere', valid_fraction: 0.9, relief, texture },
    radar: null,
  });
}

export function radarRecord(windowId: string, band: string): FingerprintRecord {
  return FingerprintRecordSchema.parse({
    window_id: windowId,
    body: windowId.split('-')[0],
    kind: 'radar',
    product_ids: [`fake-radar-${band}`],
    evidence_label: 'Observed',
    input_hash: HASH,
    run_id: RUN_ID,
    terrain: null,
    radar: {
      product_id: `fake-radar-${band}`,
      band,
      frequency_or_wavelength: `fake ${band}`,
      convention: null,
      support_m: 1000,
      aggregation: 'ratio_of_mean_powers',
      blocks_independent: false,
      n_blocks: 4,
      n_valid: 4,
      valid_fraction: 1,
      cpr_ratio_of_mean_powers: { n: 4, values: [0.5, 0.6, 0.7, 0.8], min: 0.5, median: 0.65, p05: 0.5, p95: 0.8, max: 0.8 },
      cpr_mean_of_pixelwise_ratios: null,
      sc: null,
      oc: null,
      total_power: null,
      incidence_deg: null,
      handedness_ratio: null,
      reason: null,
    },
  });
}

function windowRecord(id: string) {
  return {
    window_id: id,
    body: id.split('-')[0],
    polygon: {
      frame: 'aeqd_sphere',
      crs: 'fake-crs',
      epsg: null,
      utm_zone: null,
      ring_m: [[0, 0], [1, 0], [1, 1], [0, 0]],
      ring_lonlat: [[0, 0], [1, 0], [1, 1], [0, 0]],
    },
    area_m2: 25_000_000,
    analysis_grid_m: 90,
    product_ids: ['fake-product'],
    valid_fraction_by_observable: {},
    observed_interpolated_quality: { observed_fraction: null, interpolated_fraction: null, source: null, reason: 'fake_not_measured' },
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
  };
}

export function windowEntries(
  id: string,
  terrain: FingerprintRecord | null,
  radar: FingerprintRecord[] = [],
): Windows {
  return WindowsSchema.parse([{ window: windowRecord(id), terrain, radar, status: 'eligible', reasons: [], grid_index: null, card_id: null }]);
}

function entry(value: number | null, label: string, status: string, reason: string | null) {
  return {
    value,
    unit: 'fake-unit',
    evidence_label: label,
    status,
    reason,
    reference_spread: null,
    reference_spread_reason: 'fake_no_spread',
    background_percentile: null,
    background_percentile_reason: 'fake_no_background',
    tied: null,
    tie_group: null,
    tie_reason: 'fake_tie_not_assessed',
  };
}

const COMPONENT = {
  status: 'unavailable',
  evidence_label: 'Unavailable',
  reason: 'fake_not_run',
  note: null,
  by_family: null,
  n_requested: null,
  n_used: null,
  scenarios: null,
  rank_frequency: null,
};

export function comparisonRecord(
  target: string,
  earth: string,
  mode: 'A' | 'B' | 'C',
  values: { relief: number; texture: number; radar?: number | null; model?: number | null },
): ComparisonRecord {
  return ComparisonRecordSchema.parse({
    target_window_id: target,
    earth_window_id: earth,
    shipping_mode: mode,
    comparable_observables: ['terrain_relief', 'terrain_texture'],
    excluded_observables_and_reasons: [],
    distances_by_family: {
      terrain_relief: entry(values.relief, 'Derived', 'available', null),
      terrain_texture: entry(values.texture, 'Derived', 'available', null),
      radar_cpr:
        values.radar == null ? entry(null, 'Unavailable', 'unavailable', 'fake_radar_not_compared') : entry(values.radar, 'Derived', 'available', null),
      model_embedding:
        values.model == null ? entry(null, 'Unavailable', 'unavailable', 'fake_no_model') : entry(values.model, 'Predicted', 'available', null),
    },
    uncertainty_components: {
      heterogeneity: COMPONENT,
      measurement_error: COMPONENT,
      statistical_prediction_error: COMPONENT,
      scenario_sensitivity: COMPONENT,
    },
    sort_rule: null,
    claim_limits: ['fake claim limit'],
    source_ids: ['fake-product'],
    run_id: RUN_ID,
  });
}

export function pairRecords(parts: { comparison: ComparisonRecord; windows: Windows[] }): PairRecords {
  return {
    comparisons: new Map([[parts.comparison.target_window_id, [parts.comparison]]]),
    windows: new Map(parts.windows.map((w, i) => [`fake-body-${String(i)}`, w])),
  };
}

export function metricsRecord(overrides: { failed: string[]; status: Metrics['g5']['status'] }): Metrics {
  return MetricsSchema.parse({
    mock: false,
    run_id: RUN_ID,
    model_id: 'fake-model-id',
    evaluated_split: 'val',
    g5: {
      status: overrides.status,
      conditions: [
        {
          condition: 1,
          test_id: 'physics_probe',
          statistic: 'lower_95',
          threshold: 0.5,
          observed: 0.25,
          passed: false,
          reason: 'fake below threshold',
          components: [],
        },
      ],
      failed_tests: overrides.failed,
    },
    ablations: [
      { run_name: 'fake-ablation-a', status: 'unavailable', reason: 'fake_not_trained', metrics: [] },
    ],
    run_record: {
      config_hash: HASH,
      data_manifest_sha256: null,
      split_map_sha256: null,
      checkpoint_sha256s: [],
      git_sha: null,
      uv_lock_sha256: HASH,
      hardware: 'fake-hardware',
      gpu_hours_total: null,
      gpu_hours_partial: false,
    },
    tests: [
      {
        test_id: 'physics_probe',
        measure: 'fake_r2',
        target: 'fake_target',
        body: 'pooled',
        value: 0.3,
        ci_low: 0.2,
        ci_high: 0.4,
        n_blocks: 10,
        n_bootstrap: 100,
        unavailable_reason: null,
        evidence_label: 'Predicted',
        baselines: [],
      },
      {
        test_id: 'seed_stability',
        measure: 'fake_stability',
        target: null,
        body: null,
        value: null,
        ci_low: null,
        ci_high: null,
        n_blocks: null,
        n_bootstrap: null,
        unavailable_reason: 'fake_one_seed_only',
        evidence_label: 'Predicted',
        baselines: [],
      },
    ],
  });
}

export function modelRecord(): ModelRecord {
  return ModelRecordSchema.parse({
    model_id: 'fake-model-id',
    version: 'fake-0',
    architecture: 'fake-arch',
    parameter_count: 12345,
    training_data: { bodies: ['moon'], product_ids: ['fake-product'], window_counts: { moon: { train: 3, val: null } }, split_groups: { moon: 'fake-block-split' } },
    objectives_and_weights: { vicreg: null, alpha_phys: 1, lambda_max: 1, schedule: 'fake' },
    seeds: [1],
    metrics_with_ci: [],
    g5_status: 'FAIL',
    intended_use: 'fake intended use',
    limitations: 'fake limitations',
    licence: 'fake-licence',
    onnx_sha256: null,
  });
}
