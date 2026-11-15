// Server-side projection of the release bundle into the serialisable props of the Maps atlas
// (PRD 8.6, 10). Every pin, polygon, relief value and tile address is read from targets.json,
// windows/<body>.json, comparisons/ and cards/; a record the bundle does not hold becomes an
// Unavailable entry with the missing file named, never a default value.
import { ringCentroid } from '@/components/globe/geo';

import {
  buildBodyData,
  buildEarthData,
  windowRelief,
  type ExplorerBundle,
  type WindowRelief,
} from '../map/explore-data.ts';
import type { EligibilityStatus } from '../map/eligibility.ts';
import { frameOf, resolveLayers, type TileLayerRecord } from '../map/tile-frame.ts';

export type AtlasBody = 'earth' | 'moon' | 'mars';
type Ring = readonly (readonly [number, number])[];

/** One of the bundle's own hillshade pyramids, addressed exactly as TargetRecord.tile_layers stores it. */
export interface BundleTileLayer {
  readonly id: string;
  readonly layer: string;
  readonly targetId: string | null;
  readonly name: string;
  /** 'mercator': EPSG:3857 XYZ tiles, drawn over the NASA Earth layers; 'projected': the target's own metre frame. */
  readonly kind: 'mercator' | 'projected';
  readonly urlTemplate: string;
  readonly thumbUrl: string;
  readonly tilePx: number;
  readonly zoomMin: number;
  readonly zoomMax: number;
  readonly originX: number;
  readonly originY: number;
  readonly metresPerUnit: number;
  readonly flipY: boolean;
  readonly extentM: readonly [number, number, number, number];
  readonly finestMetresPerPixel: number;
  readonly units: string;
  readonly evidenceLabel: string;
  readonly sourceProductIds: readonly string[];
}

export interface AtlasWindow {
  readonly id: string;
  readonly targetId: string | null;
  readonly regionId: string | null;
  readonly status: EligibilityStatus | null;
  readonly reasons: readonly string[];
  readonly isPrimary: boolean;
  readonly isReference: boolean;
  readonly cardId: string | null;
  readonly relief: WindowRelief | null;
  readonly ringLonLat: Ring | null;
  readonly ringM: Ring | null;
  readonly lat: number | null;
  readonly lon: number | null;
  /** Earth only: the Moon or Mars targets this Earth window was compared against. */
  readonly comparedWith: readonly string[];
}

export interface AtlasTarget {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly shippingMode: string | null;
  readonly cardId: string | null;
  readonly primaryWindowId: string | null;
  readonly lat: number | null;
  readonly lon: number | null;
  readonly windowCount: number;
  readonly eligibleCount: number;
  readonly primaryRelief: WindowRelief | null;
  readonly tileLayerId: string | null;
  readonly layerProblems: readonly { readonly layer: string; readonly reason: string }[];
  readonly windowsFile: string;
  readonly windowsFilePresent: boolean;
}

export interface AtlasUnavailable {
  readonly title: string;
  readonly reason: string;
  readonly missing?: string | undefined;
}

export interface AtlasData {
  readonly body: AtlasBody;
  readonly bundleVersion: string;
  readonly mock: boolean;
  readonly targets: readonly AtlasTarget[];
  readonly windows: readonly AtlasWindow[];
  readonly tileLayers: readonly BundleTileLayer[];
  readonly unavailable: readonly AtlasUnavailable[];
  /** Earth: G5 status from validation.json; the encoder stays Predicted, unranked until it passes. */
  readonly g5: string | null;
  readonly modelInBundle: boolean;
}

function assetBase(): string {
  const value = process.env.NEXT_PUBLIC_ASSET_BASE;
  return value === undefined || value === '' ? '/assets' : value;
}

function centre(ring: Ring | null): { lat: number | null; lon: number | null } {
  const c = ring === null ? null : ringCentroid(ring);
  return c === null ? { lat: null, lon: null } : { lat: c.lat, lon: c.lon };
}

/** Web Mercator metres to the XYZ tile column/row at zoom z for a 512 px pyramid with this origin. */
function mercatorTile(layer: TileLayerRecord, z: number): { x: number; y: number } {
  const [x0, y0, x1, y1] = layer.extent_m as [number, number, number, number];
  const mpp = layer.metres_per_pixel_by_zoom[z - layer.zoom_min] as number;
  const span = mpp * layer.tile_px;
  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  return { x: Math.floor((cx - layer.origin_x_m) / span), y: Math.floor((layer.origin_y_m - cy) / span) };
}

function tileLayerOf(layer: TileLayerRecord, targetId: string | null, name: string, version: string): BundleTileLayer {
  const frame = frameOf(layer);
  const base = `${assetBase()}/${version}/tiles/${layer.layer}`;
  const mercator = layer.crs === 'EPSG:3857';
  const thumb = mercator ? mercatorTile(layer, layer.zoom_min) : { x: 0, y: 0 };
  return {
    id: `bundle:${layer.layer}`,
    layer: layer.layer,
    targetId,
    name,
    kind: mercator ? 'mercator' : 'projected',
    urlTemplate: `${base}/{z}/{x}/{y}.webp`,
    thumbUrl: `${base}/${String(layer.zoom_min)}/${String(thumb.x)}/${String(thumb.y)}.webp`,
    tilePx: layer.tile_px,
    zoomMin: layer.zoom_min,
    zoomMax: layer.zoom_max,
    originX: frame.originX,
    originY: frame.originY,
    metresPerUnit: frame.metresPerUnit,
    flipY: frame.flipY,
    extentM: layer.extent_m as [number, number, number, number],
    finestMetresPerPixel: layer.metres_per_pixel_by_zoom.at(-1) as number,
    units: layer.units ?? 'frame units not stated in the TileLayer record',
    evidenceLabel: layer.evidence_label,
    sourceProductIds: layer.source_product_ids,
  };
}

function hillshadeName(layer: string): string {
  return layer
    .replace(/-hillshade$/u, '')
    .split('-')
    .slice(1)
    .map((w) => (w.length > 0 ? w[0]?.toUpperCase() + w.slice(1) : w))
    .join(' ');
}

export function buildAtlasBody(bundle: ExplorerBundle, body: 'moon' | 'mars'): AtlasData {
  const data = buildBodyData(bundle, body);
  const unavailable: AtlasUnavailable[] = [];
  if (data.targetsMissing) unavailable.push({ title: 'Targets', reason: 'targets.json is not in the bundle', missing: 'targets.json' });
  const tileLayers: BundleTileLayer[] = [];
  const windows: AtlasWindow[] = [];
  // A card names its target window in comparison_ref; the windows file may leave card_id null.
  const cardByWindow = new Map<string, string>();
  for (const card of bundle.records.cards.values()) {
    const id = card.comparison_ref.target_window_id;
    if (!cardByWindow.has(id)) cardByWindow.set(id, card.card_id);
  }
  const targets = data.targets.map((t): AtlasTarget => {
    const own = t.layers.filter((l) => l.body === body);
    const { drawable, problems } = resolveLayers(own);
    const first = drawable[0];
    let tileLayerId: string | null = null;
    if (first !== undefined) {
      const tl = tileLayerOf(first, t.id, `${t.name} · bundle hillshade`, data.bundleVersion);
      tileLayers.push(tl);
      tileLayerId = tl.id;
    } else {
      unavailable.push({ title: `${t.name} hillshade`, reason: problems[0]?.reason ?? 'no hillshade tile layer for this target', missing: `${t.id} tile_layers entry` });
    }
    if (!t.windowsFilePresent) unavailable.push({ title: `${t.name} windows`, reason: `${t.windowsFile} is not in the bundle`, missing: t.windowsFile });
    for (const w of t.windows) {
      const ringLonLat = w.ringLonLat ?? null;
      windows.push({
        id: w.id,
        targetId: t.id,
        regionId: null,
        status: w.status,
        reasons: w.reasons,
        isPrimary: w.isPrimary,
        isReference: w.isReference,
        cardId: w.cardId ?? cardByWindow.get(w.id) ?? null,
        relief: w.relief ?? null,
        ringLonLat,
        ringM: w.ringM,
        ...centre(ringLonLat),
        comparedWith: [],
      });
    }
    const primary = t.windows.find((w) => w.isPrimary);
    return {
      id: t.id,
      name: t.name,
      description: t.description,
      shippingMode: t.shippingMode,
      cardId: t.cardId,
      primaryWindowId: t.primaryWindowId,
      ...centre(primary?.ringLonLat ?? null),
      windowCount: t.windows.length,
      eligibleCount: t.windows.filter((w) => w.status === 'eligible').length,
      primaryRelief: primary?.relief ?? null,
      tileLayerId,
      layerProblems: problems,
      windowsFile: t.windowsFile,
      windowsFilePresent: t.windowsFilePresent,
    };
  });
  return {
    body,
    bundleVersion: data.bundleVersion,
    mock: data.mock,
    targets,
    windows,
    tileLayers,
    unavailable,
    g5: bundle.validation.find((g) => g.gate_id === 'G5')?.status ?? null,
    modelInBundle: bundle.records.modelCard !== null,
  };
}

export function buildAtlasEarth(bundle: ExplorerBundle): AtlasData {
  const data = buildEarthData(bundle);
  const unavailable: AtlasUnavailable[] = [];
  if (data.targetsMissing) unavailable.push({ title: 'Targets', reason: 'targets.json is not in the bundle', missing: 'targets.json' });
  if (!data.earthWindowsPresent) unavailable.push({ title: 'Earth windows', reason: 'windows/earth.json is not in the bundle', missing: 'windows/earth.json' });
  for (const t of data.targets) {
    if (!t.comparisonsPresent) unavailable.push({ title: `${t.name} comparisons`, reason: `${t.comparisonsFile} is not in the bundle`, missing: t.comparisonsFile });
  }
  const compared = new Map<string, Set<string>>();
  const cardOf = new Map<string, string>();
  for (const t of data.targets) {
    for (const c of t.candidates) {
      const set = compared.get(c.earthWindowId) ?? new Set<string>();
      set.add(t.id);
      compared.set(c.earthWindowId, set);
      if (c.cardId !== null && !cardOf.has(c.earthWindowId)) cardOf.set(c.earthWindowId, c.cardId);
    }
  }
  const entries = bundle.records.windows.get('earth') ?? [];
  const windows = entries.map((entry): AtlasWindow => {
    const id = entry.window.window_id;
    const ringLonLat = entry.window.polygon.ring_lonlat.map((p) => [p[0] as number, p[1] as number] as const);
    return {
      id,
      targetId: null,
      regionId: entry.window.environment_flags.search_region_id ?? null,
      status: entry.status,
      reasons: entry.reasons,
      isPrimary: false,
      isReference: false,
      cardId: cardOf.get(id) ?? entry.card_id,
      relief: windowRelief(entry),
      ringLonLat,
      ringM: null,
      ...centre(ringLonLat),
      comparedWith: [...(compared.get(id) ?? [])],
    };
  });
  const targets = data.targets.map((t): AtlasTarget => ({
    id: t.id,
    name: t.name,
    description: '',
    shippingMode: t.candidates[0]?.shippingMode ?? null,
    cardId: null,
    primaryWindowId: null,
    lat: null,
    lon: null,
    windowCount: t.candidates.length,
    eligibleCount: t.candidates.filter((c) => c.windowStatus === 'eligible').length,
    primaryRelief: null,
    tileLayerId: null,
    layerProblems: [],
    windowsFile: t.comparisonsFile,
    windowsFilePresent: t.comparisonsPresent,
  }));
  // The Earth hillshade pyramids are EPSG:3857 XYZ tiles; they sit on the Moon/Mars TargetRecords.
  const seen = new Set<string>();
  const tileLayers: BundleTileLayer[] = [];
  for (const target of bundle.records.targets ?? []) {
    for (const l of target.tile_layers) {
      if (l.body !== 'earth' || seen.has(l.layer)) continue;
      seen.add(l.layer);
      tileLayers.push(tileLayerOf(l, null, `${hillshadeName(l.layer)} · bundle hillshade`, data.bundleVersion));
    }
  }
  return {
    body: 'earth',
    bundleVersion: data.bundleVersion,
    mock: data.mock,
    targets,
    windows,
    tileLayers,
    unavailable,
    g5: data.g5,
    modelInBundle: data.modelInBundle,
  };
}
