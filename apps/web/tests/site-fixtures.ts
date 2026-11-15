// Small schema-valid CardRecords for the /site tests. Every id and number is obviously synthetic
// (SYNTH, 987.125): nothing here is a result, and nothing is written to fixtures/ or data/.
import { CardRecordSchema, type CardRecord } from '@terraluna/schema';

type Family = 'terrain_relief' | 'terrain_texture' | 'radar_cpr' | 'model_embedding';
const FAMILIES: readonly Family[] = ['terrain_relief', 'terrain_texture', 'radar_cpr', 'model_embedding'];

function distance(family: Family, available: boolean): Record<string, unknown> {
  const label = family === 'model_embedding' ? 'Predicted' : 'Derived';
  const base = {
    unit: family === 'radar_cpr' ? 'dimensionless' : `${family}-synth-unit`,
    reference_spread: null,
    reference_spread_reason: 'synthetic_no_reference',
    background_percentile: null,
    background_percentile_reason: 'synthetic_no_background',
    tied: null,
    tie_group: null,
    tie_reason: 'synthetic_not_assessed',
  };
  if (available) {
    return { ...base, value: 987.125, evidence_label: label, status: 'available', reason: null };
  }
  return { ...base, value: null, evidence_label: 'Unavailable', status: 'unavailable', reason: 'synth_not_run' };
}

function component(): Record<string, unknown> {
  return {
    status: 'unavailable',
    evidence_label: 'Unavailable',
    reason: 'synth_not_run',
    note: null,
    by_family: null,
    n_requested: null,
    n_used: null,
    scenarios: null,
    rank_frequency: null,
  };
}

export interface SynthOptions {
  readonly mode?: 'A' | 'B' | 'C';
  readonly mock?: boolean;
  readonly g5?: CardRecord['g5_status'];
  readonly available?: readonly Family[];
}

export function synthCard(options: SynthOptions = {}): CardRecord {
  const available = options.available ?? ['terrain_relief'];
  const ring = [
    [0, 0],
    [1, 0],
    [1, 1],
    [0, 0],
  ];
  const polygon = {
    frame: 'utm',
    crs: 'SYNTH-CRS',
    epsg: null,
    utm_zone: null,
    ring_m: ring,
    ring_lonlat: ring,
  };
  const distances = Object.fromEntries(
    FAMILIES.map((family) => [family, distance(family, available.includes(family))]),
  );
  const campaign = (value: string): Record<string, unknown> =>
    value === 'unknown' ? { value, source: null } : { value, source: 'synthetic source' };
  const record = {
    card_id: 'moon-synth-a__earth-synth-b',
    mock: options.mock ?? false,
    run_id: `sha256:${'0'.repeat(64)}`,
    shipping_mode: options.mode ?? 'C',
    title: 'SYNTH card title',
    wording: 'candidate window identified by this search',
    target: {
      target_id: 'moon-synth-a',
      target_window_id: 'moon-synth-a',
      body: 'moon',
      polygon,
      product_ids: ['synth-product'],
    },
    earth: {
      earth_window_id: 'earth-synth-b',
      polygon,
      product_ids: ['synth-product-e'],
      search_region_id: null,
    },
    comparison_ref: {
      path: 'comparisons/moon-synth-a.json',
      target_window_id: 'moon-synth-a',
      earth_window_id: 'earth-synth-b',
    },
    q1_compared: 'SYNTH compared windows',
    q2_interpretation: { text: 'SYNTH interpretation', evidence_label: 'Assumed' },
    q3_measurements: [
      {
        observable: 'synth_observable',
        value: 987.125,
        unit: 'synth-unit',
        evidence_label: 'Derived',
        support: '30 m window',
        product_id: 'synth-product',
        band: null,
        geometry: null,
        reason: null,
      },
    ],
    q4_differences: {
      limits: ['SYNTH limit one', 'SYNTH limit two'],
      differing: ['SYNTH differing'],
      ambiguous: [],
      not_observed: ['SYNTH not observed'],
    },
    q5_stability: {
      uncertainty_components: {
        heterogeneity: component(),
        measurement_error: component(),
        statistical_prediction_error: component(),
        scenario_sensitivity: component(),
      },
      tied_by_family: { terrain_relief: null, terrain_texture: null, radar_cpr: null, model_embedding: null },
      tie_group_by_family: { terrain_relief: null, terrain_texture: null, radar_cpr: null, model_embedding: null },
    },
    q6_campaign: {
      access: campaign('unknown'),
      permits: campaign('unknown'),
      season: campaign('SYNTH season'),
      land_status: campaign('unknown'),
      logistics: campaign('unknown'),
    },
    q7_field_experiment: { text: 'SYNTH field proposal', evidence_label: 'Assumed', status: 'proposed' },
    distances_by_family: distances,
    radar_context: [],
    figures: [
      { figure: 'A', path: 'figures/synth-a.svg', alt: 'SYNTH alt A', reason: null },
      { figure: 'B', path: null, alt: 'SYNTH alt B', reason: 'synth_not_rendered' },
      { figure: 'C', path: null, alt: 'SYNTH alt C', reason: 'synth_not_rendered' },
    ],
    g5_status: options.g5 ?? 'NOT_RUN',
    claim_limits: ['SYNTH claim limit'],
  };
  return CardRecordSchema.parse(record);
}

export const SYNTH_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><rect width="4" height="4"/></svg>';
