// Compile-time checks of the inferred types. `tsc --noEmit` (the typecheck task in make check)
// fails if a generated type drifts from the PRD 8.4 / 8.12 record shapes; Vitest does not run
// this file.
import { expectTypeOf } from 'vitest';
import type { z } from 'zod';

import type {
  BundleManifest,
  BundleManifestSchema,
  ComparisonRecord,
  ComparisonRecordSchema,
  Comparisons,
  ComparisonsSchema,
  FingerprintRecord,
  FingerprintRecordSchema,
  GateRecord,
  GateRecordSchema,
  ProductRecord,
  ProductRecordSchema,
  Sources,
  SourcesSchema,
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
