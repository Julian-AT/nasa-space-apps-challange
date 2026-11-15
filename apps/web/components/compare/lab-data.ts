// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Server-side view data of the compare lab: for each card of the bundle, the two hillshade tile
// layers (read from targets.json tile_layers), the two window rings already projected into each
// layer's own frame, and display names. Nothing here is a result; distances stay on the card.
import type { CardRecord } from '@terraluna/schema';

import { earthRegionName, layerForRegion, layerForTarget, projectorFor, type TileLayerGeo } from '../cards/card-geo.ts';
import { shortName } from '../cards/card-visual.ts';
import type { PulledBundle } from '../../lib/bundle.ts';

/** One map pane: a bundle hillshade tile layer in its own projected frame, and the window in it. */
export interface PaneLayer {
  readonly layer: string;
  readonly body: 'moon' | 'mars' | 'earth';
  readonly crs: string;
  readonly units: string;
  readonly tilePx: number;
  readonly originX: number;
  readonly originY: number;
  /** [minX, minY, maxX, maxY] in the layer frame (metres). */
  readonly extent: readonly [number, number, number, number];
  readonly mppZoomMin: number;
  readonly zoomMin: number;
  readonly zoomMax: number;
  readonly url: string;
  readonly evidenceLabel: string;
  readonly sources: readonly string[];
  readonly hillshade: string;
  /** Projected metres per ground metre at the window (Web Mercator 1/cos(lat), else 1). */
  readonly scaleFactor: number;
  /** The window ring in the layer frame, [x, y] metres. */
  readonly ring: readonly (readonly [number, number])[];
  readonly centre: readonly [number, number];
  readonly centreLonLat: readonly [number, number];
}

export interface LabPair {
  readonly cardId: string;
  readonly a: string;
  readonly b: string;
  readonly body: 'moon' | 'mars';
  readonly targetName: string;
  readonly earthName: string;
  readonly headline: string;
  readonly mock: boolean;
  readonly mode: string;
  readonly target: PaneLayer | null;
  readonly earth: PaneLayer | null;
}

function assetBase(): string {
  const v = process.env.NEXT_PUBLIC_ASSET_BASE;
  return v === undefined || v === '' ? '/assets' : v;
}

interface RawLayer extends TileLayerGeo {
  readonly extent_m?: readonly number[];
  readonly units?: string;
  readonly evidence_label?: string;
  readonly source_product_ids?: readonly string[];
  readonly hillshade?: { readonly altitude_deg: number; readonly azimuth_deg: number };
}

function pane(version: string, layer: TileLayerGeo | null, ring: readonly (readonly number[])[], body: PaneLayer['body']): PaneLayer | null {
  if (layer === null) return null;
  const raw = layer as RawLayer;
  const project = projectorFor(layer.crs);
  const mpp0 = layer.metres_per_pixel_by_zoom[0];
  const ext = raw.extent_m;
  if (project === null || mpp0 === undefined || ext?.length !== 4) return null;
  const pts: [number, number][] = [];
  for (const p of ring) {
    const q = project(p[0] ?? 0, p[1] ?? 0);
    if (q === null) return null;
    pts.push([q[0], q[1]]);
  }
  const open = pts.slice(0, -1);
  const cx = open.reduce((s, p) => s + p[0], 0) / Math.max(1, open.length);
  const cy = open.reduce((s, p) => s + p[1], 0) / Math.max(1, open.length);
  const lons = ring.slice(0, -1).map((p) => p[0] ?? 0);
  const lats = ring.slice(0, -1).map((p) => p[1] ?? 0);
  const lon = lons.reduce((s, v) => s + v, 0) / Math.max(1, lons.length);
  const lat = lats.reduce((s, v) => s + v, 0) / Math.max(1, lats.length);
  const hs = raw.hillshade;
  return {
    layer: layer.layer,
    body,
    crs: layer.crs,
    units: raw.units ?? layer.crs,
    tilePx: layer.tile_px,
    originX: layer.origin_x_m,
    originY: layer.origin_y_m,
    extent: [ext[0] ?? 0, ext[1] ?? 0, ext[2] ?? 0, ext[3] ?? 0],
    mppZoomMin: mpp0,
    zoomMin: layer.zoom_min,
    zoomMax: layer.zoom_max,
    url: `${assetBase()}/${version}/tiles/${layer.layer}/{z}/{x}/{y}.webp`,
    evidenceLabel: raw.evidence_label ?? 'Derived',
    sources: raw.source_product_ids ?? [],
    hillshade: hs === undefined ? 'hillshade' : `hillshade, sun altitude ${String(hs.altitude_deg)}°, azimuth ${String(hs.azimuth_deg)}°`,
    scaleFactor: layer.crs === 'EPSG:3857' ? 1 / Math.cos((lat * Math.PI) / 180) : 1,
    ring: pts,
    centre: [cx, cy],
    centreLonLat: [lon, lat],
  };
}

export function buildLabPair(bundle: PulledBundle, card: CardRecord): LabPair {
  const version = bundle.manifest.bundle_version;
  const rec = (bundle.records.targets ?? []).find((t) => t.target_id === card.target.target_id);
  const layers: readonly TileLayerGeo[] = rec?.tile_layers ?? [];
  const body = card.target.body === 'mars' ? 'mars' : 'moon';
  // Target ids look like "mars-jezero-w000" / "moon-cr-site01-w000"; the layer name carries the middle.
  const key = card.target.target_id.split('-').slice(1, -1).filter((s) => s !== 'cr' && s !== 'sps').join('-');
  const targetName = shortName(rec?.name ?? card.target.target_id);
  const region = card.earth.search_region_id;
  const earthName = earthRegionName(region ?? card.earth.earth_window_id);
  return {
    cardId: card.card_id,
    a: card.comparison_ref.target_window_id,
    b: card.comparison_ref.earth_window_id,
    body,
    targetName,
    earthName,
    headline: `${targetName} ↔ ${earthName}`,
    mock: card.mock,
    mode: card.shipping_mode,
    target: pane(version, layerForTarget(layers, body, key), card.target.polygon.ring_lonlat, body),
    earth: region === null ? null : pane(version, layerForRegion(layers, region), card.earth.polygon.ring_lonlat, 'earth'),
  };
}

/** Every card of the bundle, in bundle order. */
export function labCards(bundle: PulledBundle): readonly CardRecord[] {
  return [...bundle.records.cards.values()];
}

export function cardForPair(bundle: PulledBundle, a: string, b: string): CardRecord | null {
  return labCards(bundle).find((c) => c.comparison_ref.target_window_id === a && c.comparison_ref.earth_window_id === b) ?? null;
}
