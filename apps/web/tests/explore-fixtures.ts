// Test-local synthetic records for the explorer tests. Every id is obviously fake ("test") and
// every record is checked against the real @terraluna/schema schema, so a schema change breaks
// the builder instead of silently diverging. Nothing here is written to fixtures/ or the app.
import {
  ComparisonsSchema,
  TargetsSchema,
  WindowsSchema,
  type Comparisons,
  type Targets,
  type Windows,
} from '@terraluna/schema';

import type { ExplorerBundle } from '../components/map/explore-data.ts';

type Layer = Targets[number]['tile_layers'][number];

export function testLayer(over: Partial<Layer> = {}): Layer {
  return {
    layer: 'test-hillshade',
    body: 'moon',
    crs: 'TEST-CRS',
    units: null,
    evidence_label: 'Derived',
    source_product_ids: ['test-product'],
    origin_x_m: -1000,
    origin_y_m: 1000,
    y_direction: 'down',
    tile_px: 512,
    metres_per_pixel_by_zoom: [4, 2, 1],
    zoom_min: 0,
    zoom_max: 2,
    extent_m: [-1000, -1000, 1000, 1000],
    omitted_tiles: [],
    legend: { kind: 'numeric', min: 0, max: 10, scale: 'viridis', unit: 'test-unit' },
    hillshade: null,
    encoding: { format: 'webp', lossless: false, quality: 80 },
    bytes_total: 0,
    mock: true,
    ...over,
  };
}

export function testTarget(over: Record<string, unknown> = {}): Targets {
  return TargetsSchema.parse([
    {
      target_id: 'moon-test-target',
      mock: true,
      run_id: null,
      body: 'moon',
      name: 'Test target',
      description: 'A synthetic target for tests.',
      product_ids: ['test-product'],
      crs: 'TEST-CRS',
      projection: { kind: 'polar_stereographic', params: {} },
      footprint: null,
      primary_window_id: 'moon-test-window-a',
      window_grid: { window_m: 100, stride_m: 50, n_generated: null, n_eligible: null },
      reference_window_ids: [],
      shortlist: null,
      shortlist_reason: 'not_built',
      shipping_mode: 'C',
      tile_layers: [testLayer()],
      ...over,
    },
  ]);
}

export function testWindow(id: string, status: 'eligible' | 'rejected' | 'unknown', reasons: string[], x = 0): Windows[number] {
  const ring = [
    [x, 0],
    [x + 100, 0],
    [x + 100, 100],
    [x, 100],
    [x, 0],
  ];
  return WindowsSchema.parse([
    {
      window: {
        window_id: id,
        body: id.split('-')[0],
        polygon: {
          frame: 'polar_stereographic',
          crs: 'TEST-CRS',
          epsg: null,
          utm_zone: null,
          ring_m: ring,
          ring_lonlat: ring,
        },
        area_m2: 10000,
        analysis_grid_m: 10,
        product_ids: ['test-product'],
        valid_fraction_by_observable: {},
        observed_interpolated_quality: { observed_fraction: null, interpolated_fraction: null, source: null, reason: 'not_built' },
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
      },
      terrain: null,
      radar: [],
      status,
      reasons,
      grid_index: null,
      card_id: null,
    },
  ])[0] as Windows[number];
}

type DistanceInput = Record<string, unknown>;

export function testDistance(over: DistanceInput = {}): DistanceInput {
  return {
    value: 1.5,
    unit: 'test-unit',
    evidence_label: 'Derived',
    status: 'available',
    reason: null,
    reference_spread: { min: 1, median: 2, max: 3, n: 3 },
    reference_spread_reason: null,
    background_percentile: 12.5,
    background_percentile_reason: null,
    tied: false,
    tie_group: null,
    tie_reason: null,
    ...over,
  };
}

export const UNAVAILABLE_DISTANCE: DistanceInput = {
  value: null,
  unit: 'test-unit',
  evidence_label: 'Unavailable',
  status: 'unavailable',
  reason: 'radar_not_observed',
  reference_spread: null,
  reference_spread_reason: 'spread_unavailable',
  background_percentile: null,
  background_percentile_reason: 'spread_unavailable',
  tied: null,
  tie_group: null,
  tie_reason: 'spread_unavailable',
};

const UNAVAILABLE_COMPONENT = {
  status: 'unavailable',
  evidence_label: 'Unavailable',
  reason: 'not_built',
  note: null,
  by_family: null,
  n_requested: null,
  n_used: null,
  scenarios: null,
  rank_frequency: null,
};

export function testComparison(
  earthId: string,
  distances: Partial<Record<'terrain_relief' | 'terrain_texture' | 'radar_cpr' | 'model_embedding', DistanceInput>>,
  exclusions: { observable: string; reasons: string[] }[] = [],
): Comparisons[number] {
  const make = (v: DistanceInput | undefined) => v ?? UNAVAILABLE_DISTANCE;
  return ComparisonsSchema.parse([
    {
      target_window_id: 'moon-test-window-a',
      earth_window_id: earthId,
      shipping_mode: 'C',
      comparable_observables: ['terrain'],
      excluded_observables_and_reasons: exclusions,
      distances_by_family: {
        terrain_relief: make(distances.terrain_relief),
        terrain_texture: make(distances.terrain_texture),
        radar_cpr: make(distances.radar_cpr),
        model_embedding: make(distances.model_embedding),
      },
      uncertainty_components: {
        heterogeneity: UNAVAILABLE_COMPONENT,
        measurement_error: UNAVAILABLE_COMPONENT,
        statistical_prediction_error: UNAVAILABLE_COMPONENT,
        scenario_sensitivity: UNAVAILABLE_COMPONENT,
      },
      sort_rule: null,
      claim_limits: ['test claim limit'],
      source_ids: ['test-product'],
      run_id: `sha256:${'0'.repeat(64)}`,
    },
  ])[0] as Comparisons[number];
}

export function testBundle(over: Partial<ExplorerBundle['records']> = {}, g5 = 'NOT_RUN'): ExplorerBundle {
  return {
    manifest: { bundle_version: 'test-bundle', mock: true },
    validation: [{ gate_id: 'G5', status: g5 }],
    records: {
      targets: null,
      windows: new Map(),
      comparisons: new Map(),
      cards: new Map(),
      modelCard: null,
      ...over,
    },
  };
}
