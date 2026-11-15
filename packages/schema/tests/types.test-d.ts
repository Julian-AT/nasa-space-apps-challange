// Compile-time checks of the inferred types. `tsc --noEmit` (the typecheck task in make check)
// fails if a generated type drifts from the PRD 8.4 / 8.12 record shapes; Vitest does not run
// this file.
import { expectTypeOf } from 'vitest';
import type { z } from 'zod';

import type {
  BundleManifest,
  BundleManifestSchema,
  CardRecord,
  CardRecordSchema,
  ComparisonRecord,
  ComparisonRecordSchema,
  Comparisons,
  ComparisonsSchema,
  EmbeddingRecord,
  EmbeddingRecordSchema,
  EmbeddingsIndex,
  EmbeddingsIndexSchema,
  FingerprintRecord,
  FingerprintRecordSchema,
  GateRecord,
  GateRecordSchema,
  Metrics,
  MetricsSchema,
  ModelRecord,
  ModelRecordSchema,
  ProductRecord,
  ProductRecordSchema,
  Sources,
  SourcesSchema,
  Targets,
  TargetsSchema,
  Validation,
  ValidationSchema,
  WindowRecord,
  WindowRecordSchema,
  Windows,
  WindowsSchema,
} from '../src/index.ts';

type GateStatus = 'NOT_RUN' | 'PASS' | 'FAIL' | 'PARTIAL' | 'NOT_APPLICABLE';

expectTypeOf<BundleManifest>().toEqualTypeOf<{
  bundle_version: string;
  schema_version: string;
  mock: boolean;
  run_id: string | null;
  git_sha: string | null;
  created_utc: string;
  shipping_mode: 'A' | 'B' | 'C' | null;
  targets: string[];
  model: { id: string; status: 'EXPERIMENTAL'; g5: GateStatus } | null;
  files: { path: string; sha256: string; bytes: number }[];
}>();

expectTypeOf<GateRecord>().toEqualTypeOf<{
  gate_id: 'G0' | 'G1' | 'G2' | 'G3' | 'G4' | 'G5' | 'G6';
  status: GateStatus;
  evidence_path: string | null;
  run_id: string | null;
  scope: string | null;
  result: string | null;
  consequence: string | null;
  // PRD-01 (Option A): the provenance fields of the amended PRD 8.12 GateRecord.
  command: string | null;
  config_version: string | null;
  input_hashes: { path: string; sha256: string }[];
}>();

expectTypeOf<Validation>().toEqualTypeOf<GateRecord[]>();

// The VERIFIED condition (PRD 8.12) is a refinement, so it does not narrow the inferred type.
expectTypeOf<ProductRecord>().toEqualTypeOf<{
  product_id: string;
  provider: string;
  version: string;
  source_url: string | null;
  local_path: string | null;
  checksum: string | null;
  body: 'moon' | 'mars' | 'earth';
  acquisition_time: string | null;
  frequency_or_wavelength: string | null;
  transmit_receive_convention: string | null;
  units: string;
  calibration_type: string | null;
  native_grid: string | null;
  effective_resolution: string | null;
  crs_and_frame: string;
  incidence_source: string | null;
  qa_definition: string | null;
  license_or_attribution: string;
  verification_status: 'NOT_CHECKED' | 'VERIFIED' | 'PARTIAL' | 'BLOCKED' | 'FAILED';
}>();

expectTypeOf<Sources>().toEqualTypeOf<ProductRecord[]>();

// Science records I (CONTRACTS a.4). Cross-field rules are refinements, so they do not narrow
// the inferred types; a Family-keyed map holds any subset of the four families.
type Body = 'moon' | 'mars' | 'earth';
type EvidenceLabel = 'Observed' | 'Derived' | 'Predicted' | 'Assumed' | 'Unavailable';
type Family = 'terrain_relief' | 'terrain_texture' | 'radar_cpr' | 'model_embedding';
type ByFamily<V> = Partial<Record<Family, V>>;

interface Distribution {
  n: number;
  values: number[];
  min: number | null;
  median: number | null;
  p05: number | null;
  p95: number | null;
  max: number | null;
}

interface TerrainVariant {
  detrend: 'none' | 'plane';
  supported_lags_m: number[];
  unsupported_lags: { lag_m: number; reason: string }[];
  nu_m: Record<string, number | null>;
  nu_by_direction_m: Record<string, Record<string, number | null>>;
  hurst: number | null;
  hurst_reason: string | null;
  tilt: number[] | null;
}

expectTypeOf<WindowRecord>().toEqualTypeOf<{
  window_id: string;
  body: Body;
  polygon: {
    frame: 'utm' | 'polar_stereographic' | 'equirectangular' | 'aeqd_sphere';
    crs: string;
    epsg: number | null;
    utm_zone: string | null;
    ring_m: number[][];
    ring_lonlat: number[][];
  };
  area_m2: number;
  analysis_grid_m: number;
  product_ids: string[];
  valid_fraction_by_observable: Record<string, number | null>;
  observed_interpolated_quality: {
    observed_fraction: number | null;
    interpolated_fraction: number | null;
    source: string | null;
    reason: string | null;
  };
  environment_flags: {
    search_region_id: string | null;
    scene_ids: string[];
    acquisition_dates: string[];
    snow_or_freeze: string | null;
    moisture_state: string | null;
    surface_change_since_acquisition: string | null;
    permanent_shadow_fraction: number | null;
    surface_model_note: string | null;
  };
  evidence_status_by_field: Record<string, EvidenceLabel>;
}>();

expectTypeOf<FingerprintRecord>().toEqualTypeOf<{
  window_id: string;
  body: Body;
  kind: 'terrain' | 'radar';
  product_ids: string[];
  evidence_label: EvidenceLabel;
  input_hash: string;
  run_id: string;
  terrain: {
    analysis_grid_m: number;
    coarsening_factor: number | null;
    resampler: 'aeqd_sphere';
    valid_fraction: number;
    relief: TerrainVariant;
    texture: TerrainVariant;
  } | null;
  radar: {
    product_id: string;
    band: string;
    frequency_or_wavelength: string;
    convention: string | null;
    support_m: number;
    aggregation: 'ratio_of_mean_powers';
    blocks_independent: false;
    n_blocks: number;
    n_valid: number;
    valid_fraction: number;
    cpr_ratio_of_mean_powers: Distribution | null;
    cpr_mean_of_pixelwise_ratios: Distribution | null;
    sc: Distribution | null;
    oc: Distribution | null;
    total_power: Distribution | null;
    incidence_deg: { min: number; max: number } | null;
    handedness_ratio: Distribution | null;
    reason: string | null;
  } | null;
}>();

expectTypeOf<Windows>().toEqualTypeOf<
  {
    window: WindowRecord;
    terrain: FingerprintRecord | null;
    radar: FingerprintRecord[];
    status: 'eligible' | 'rejected' | 'unknown';
    reasons: string[];
    grid_index: number[] | null;
    card_id: string | null;
  }[]
>();

interface DistanceEntry {
  value: number | null;
  unit: string;
  evidence_label: EvidenceLabel;
  status: 'available' | 'unavailable' | 'not_comparable';
  reason: string | null;
  reference_spread: { min: number; median: number; max: number; n: number } | null;
  reference_spread_reason: string | null;
  background_percentile: number | null;
  background_percentile_reason: string | null;
  tied: boolean | null;
  tie_group: string | null;
  tie_reason: string | null;
}

interface UncertaintyComponent {
  status: 'available' | 'unavailable';
  evidence_label: EvidenceLabel;
  reason: string | null;
  note: string | null;
  by_family: ByFamily<{
    spread_min: number | null;
    spread_median: number | null;
    spread_max: number | null;
    n: number | null;
  }> | null;
  n_requested: number | null;
  n_used: number | null;
  scenarios:
    | {
        name: string;
        change: string;
        values_by_family: ByFamily<number | null>;
        rank_by_family: ByFamily<number | null>;
      }[]
    | null;
  rank_frequency: ByFamily<{ frequency: number; count: number; n: number }> | null;
}

expectTypeOf<ComparisonRecord>().toEqualTypeOf<{
  target_window_id: string;
  earth_window_id: string;
  shipping_mode: 'A' | 'B' | 'C' | null;
  comparable_observables: string[];
  excluded_observables_and_reasons: { observable: string; reasons: string[] }[];
  distances_by_family: Record<Family, DistanceEntry>;
  uncertainty_components: {
    heterogeneity: UncertaintyComponent;
    measurement_error: UncertaintyComponent;
    statistical_prediction_error: UncertaintyComponent;
    scenario_sensitivity: UncertaintyComponent;
  };
  sort_rule: string | null;
  claim_limits: string[];
  source_ids: string[];
  run_id: string;
}>();

expectTypeOf<Comparisons>().toEqualTypeOf<ComparisonRecord[]>();

// Science records II (CONTRACTS a.4, T0B-02). The legend is a union told apart by `kind`; the
// q5 tie maps name all four families, as distances_by_family does.
type ShippingMode = 'A' | 'B' | 'C';
type Polygon = WindowRecord['polygon'];

interface TileLayer {
  layer: string;
  body: Body;
  crs: string;
  units: string | null;
  evidence_label: EvidenceLabel;
  source_product_ids: string[];
  origin_x_m: number;
  origin_y_m: number;
  y_direction: 'down' | 'up';
  tile_px: 512;
  metres_per_pixel_by_zoom: number[];
  zoom_min: number;
  zoom_max: number;
  extent_m: number[];
  omitted_tiles: number[][];
  legend:
    | { kind: 'classes'; classes: { code: number; label: string; rgba: number[] }[] }
    | { kind: 'numeric'; min: number; max: number; scale: 'viridis' | 'cividis'; unit: string | null };
  hillshade: { azimuth_deg: number; altitude_deg: number } | null;
  encoding: { format: 'webp'; lossless: boolean; quality: number | null };
  bytes_total: number;
  mock: boolean;
}

expectTypeOf<Targets>().toEqualTypeOf<
  {
    target_id: string;
    mock: boolean;
    run_id: string | null;
    body: Body;
    name: string;
    description: string;
    product_ids: string[];
    crs: string;
    projection: {
      kind: 'polar_stereographic' | 'equirectangular' | 'geographic';
      params: Record<string, number | string>;
    };
    footprint: Polygon | null;
    primary_window_id: string | null;
    window_grid: {
      window_m: number;
      stride_m: number;
      n_generated: number | null;
      n_eligible: number | null;
    };
    reference_window_ids: string[];
    shortlist: { earth_window_ids: string[]; rule: string; n_compared: number | null } | null;
    shortlist_reason: string | null;
    shipping_mode: ShippingMode | null;
    tile_layers: TileLayer[];
  }[]
>();

interface CampaignEntry {
  value: string;
  source: string | null;
}

expectTypeOf<CardRecord>().toEqualTypeOf<{
  card_id: string;
  mock: boolean;
  run_id: string;
  shipping_mode: ShippingMode;
  title: string;
  wording: string;
  target: {
    target_id: string;
    target_window_id: string;
    body: Body;
    polygon: Polygon;
    product_ids: string[];
  };
  earth: {
    earth_window_id: string;
    polygon: Polygon;
    product_ids: string[];
    search_region_id: string | null;
  };
  comparison_ref: { path: string; target_window_id: string; earth_window_id: string };
  q1_compared: string;
  q2_interpretation: { text: string; evidence_label: 'Assumed' };
  q3_measurements: {
    observable: string;
    value: number | null;
    unit: string | null;
    evidence_label: EvidenceLabel;
    support: string;
    product_id: string | null;
    band: string | null;
    geometry: string | null;
    reason: string | null;
  }[];
  q4_differences: {
    limits: string[];
    differing: string[];
    ambiguous: string[];
    not_observed: string[];
  };
  q5_stability: {
    uncertainty_components: ComparisonRecord['uncertainty_components'];
    tied_by_family: Record<Family, boolean | null>;
    tie_group_by_family: Record<Family, string | null>;
  };
  q6_campaign: {
    access: CampaignEntry;
    permits: CampaignEntry;
    season: CampaignEntry;
    land_status: CampaignEntry;
    logistics: CampaignEntry;
  };
  q7_field_experiment: { text: string; evidence_label: 'Assumed'; status: 'proposed' };
  distances_by_family: Record<Family, DistanceEntry>;
  radar_context: {
    product_id: string;
    band: string;
    observable: string;
    statistic: string;
    value: number | null;
    unit: string | null;
    evidence_label: EvidenceLabel;
    support_m: number | null;
    reason: string | null;
  }[];
  figures: { figure: 'A' | 'B' | 'C'; path: string | null; alt: string; reason: string | null }[];
  g5_status: GateStatus;
  claim_limits: string[];
}>();

type MetricRecord = ModelRecord['metrics_with_ci'][number];

expectTypeOf<MetricRecord>().toEqualTypeOf<{
  test_id:
    | 'physics_probe'
    | 'morphology_probe'
    | 'body_invariance'
    | 'physics_agreement'
    | 'target_retrieval'
    | 'seed_stability'
    | 'held_out_body'
    | 'out_of_distribution';
  measure: string;
  target: string | null;
  body: Body | 'pooled' | null;
  value: number | null;
  ci_low: number | null;
  ci_high: number | null;
  n_blocks: number | null;
  n_bootstrap: number | null;
  unavailable_reason: string | null;
  evidence_label: 'Predicted';
  baselines: {
    name: string;
    value: number | null;
    ci_low: number | null;
    ci_high: number | null;
    paired_diff_ci_low: number | null;
    paired_diff_ci_high: number | null;
    unavailable_reason: string | null;
  }[];
}>();

expectTypeOf<ModelRecord>().toEqualTypeOf<{
  model_id: string;
  version: string;
  architecture: string;
  parameter_count: number;
  training_data: {
    bodies: Body[];
    product_ids: string[];
    window_counts: Partial<Record<Body, Partial<Record<'train' | 'val' | 'test', number | null>>>>;
    split_groups: Partial<Record<Body, string>>;
  };
  objectives_and_weights: {
    vicreg: { invariance: number; variance: number; covariance: number } | null;
    alpha_phys: number;
    lambda_max: number;
    schedule: string;
  };
  seeds: number[];
  metrics_with_ci: MetricRecord[];
  g5_status: GateStatus;
  intended_use: string;
  limitations: string;
  licence: string;
  onnx_sha256: string | null;
}>();

expectTypeOf<Metrics>().toEqualTypeOf<{
  mock: boolean;
  run_id: string | null;
  model_id: string;
  evaluated_split: 'val' | 'test';
  g5: {
    status: GateStatus;
    conditions: {
      condition: number;
      test_id: string;
      statistic: 'lower_95' | 'point' | 'paired_interval';
      threshold: number | null;
      observed: number | null;
      passed: boolean | null;
      reason: string | null;
      components: {
        component: string;
        observed: number | null;
        ci_low: number | null;
        ci_high: number | null;
        passed: boolean | null;
        reason: string | null;
      }[];
    }[];
    failed_tests: string[];
  };
  tests: MetricRecord[];
  ablations: {
    run_name: string;
    status: 'available' | 'unavailable';
    reason: string | null;
    metrics: MetricRecord[];
  }[];
  run_record: {
    config_hash: string;
    data_manifest_sha256: string | null;
    split_map_sha256: string | null;
    checkpoint_sha256s: string[];
    git_sha: string | null;
    uv_lock_sha256: string;
    hardware: string;
    gpu_hours_total: number | null;
    gpu_hours_partial: boolean;
  };
}>();

expectTypeOf<EmbeddingRecord>().toEqualTypeOf<{
  window_id: string;
  body: Body;
  model_id: string;
  embedding_f16: number[];
  input_hash: string;
  valid_fraction: number;
  ood_score: number | null;
}>();

expectTypeOf<EmbeddingsIndex>().toEqualTypeOf<{
  model_id: string;
  onnx_sha256: string | null;
  mock: boolean;
  dim: 256;
  dtype: 'float16';
  byte_order: 'little';
  n_rows: number;
  rows: {
    row: number;
    window_id: string;
    body: Body;
    split: 'test';
    input_hash: string;
    valid_fraction: number;
    ood_score: number | null;
    ood_flag: boolean | null;
  }[];
}>();

// Parsing transforms nothing, so a schema's input type is its output type.
expectTypeOf<z.input<typeof BundleManifestSchema>>().toEqualTypeOf<BundleManifest>();
expectTypeOf<z.input<typeof GateRecordSchema>>().toEqualTypeOf<GateRecord>();
expectTypeOf<z.input<typeof ValidationSchema>>().toEqualTypeOf<Validation>();
expectTypeOf<z.input<typeof ProductRecordSchema>>().toEqualTypeOf<ProductRecord>();
expectTypeOf<z.input<typeof SourcesSchema>>().toEqualTypeOf<Sources>();
expectTypeOf<z.input<typeof WindowRecordSchema>>().toEqualTypeOf<WindowRecord>();
expectTypeOf<z.input<typeof FingerprintRecordSchema>>().toEqualTypeOf<FingerprintRecord>();
expectTypeOf<z.input<typeof WindowsSchema>>().toEqualTypeOf<Windows>();
expectTypeOf<z.input<typeof ComparisonRecordSchema>>().toEqualTypeOf<ComparisonRecord>();
expectTypeOf<z.input<typeof ComparisonsSchema>>().toEqualTypeOf<Comparisons>();
expectTypeOf<z.input<typeof TargetsSchema>>().toEqualTypeOf<Targets>();
expectTypeOf<z.input<typeof CardRecordSchema>>().toEqualTypeOf<CardRecord>();
expectTypeOf<z.input<typeof ModelRecordSchema>>().toEqualTypeOf<ModelRecord>();
expectTypeOf<z.input<typeof MetricsSchema>>().toEqualTypeOf<Metrics>();
expectTypeOf<z.input<typeof EmbeddingRecordSchema>>().toEqualTypeOf<EmbeddingRecord>();
expectTypeOf<z.input<typeof EmbeddingsIndexSchema>>().toEqualTypeOf<EmbeddingsIndex>();
