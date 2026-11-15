// Server-side projection of the bundle's records into the small serialisable props the explorer
// needs (PRD 8.6, 10). Everything comes from targets.json, windows/<body>.json,
// comparisons/<target_id>.json, cards/ and validation.json; a record or file that the bundle does
// not hold is reported by name, never replaced with a default. Pure functions: pages call them with
// loadBundle() and tests call them with small synthetic records.
import type { CardRecord, Comparisons, Sources, Targets, Windows } from '@terraluna/schema';

import type { EligibilityStatus } from './eligibility.ts';
import { resolveLayers, type LayerProblem, type TileLayerRecord } from './tile-frame.ts';

export type Body = 'moon' | 'mars' | 'earth';
type WindowEntry = Windows[number];
type ComparisonRecord = Comparisons[number];

/** The slice of the loaded bundle the explorer reads. */
export interface ExplorerBundle {
  readonly manifest: { readonly bundle_version: string; readonly mock: boolean };
  readonly validation: readonly { readonly gate_id: string; readonly status: string }[];
  readonly records: {
    readonly targets: Targets | null;
    readonly sources?: Sources | null;
    readonly windows: ReadonlyMap<string, Windows>;
    readonly comparisons: ReadonlyMap<string, Comparisons>;
    readonly cards: ReadonlyMap<string, CardRecord>;
    readonly modelCard: unknown;
    readonly figures?: ReadonlyMap<string, string>;
  };
}

export interface MapWindow {
  readonly id: string;
  readonly ringM: readonly (readonly [number, number])[];
  readonly status: EligibilityStatus;
  readonly reasons: readonly string[];
  readonly isPrimary: boolean;
  readonly isReference: boolean;
  readonly cardId: string | null;
}

export interface NuRow {
  readonly lagM: string;
  readonly relief: number | null;
  readonly texture: number | null;
}

export type NuSummary =
  | {
      readonly kind: 'present';
      readonly windowId: string;
      readonly evidenceLabel: 'Observed' | 'Derived' | 'Predicted' | 'Assumed' | 'Unavailable';
      readonly productIds: readonly string[];
      readonly validFraction: number;
      readonly analysisGridM: number;
      readonly rows: readonly NuRow[];
      readonly hurstRelief: number | null;
    }
  | { readonly kind: 'unavailable'; readonly reason: string };

export interface ProductInfo {
  readonly id: string;
  readonly provider: string;
  readonly version: string;
  /** frequency_or_wavelength of the ProductRecord: the band, when it has one. */
  readonly band: string | null;
  readonly qa: string | null;
  readonly frame: string;
  readonly licence: string;
}

export interface MapTarget {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly mock: boolean;
  readonly crs: string;
  readonly projectionKind: string;
  readonly productIds: readonly string[];
  readonly shippingMode: string | null;
  readonly primaryWindowId: string | null;
  readonly footprintRingM: readonly (readonly [number, number])[] | null;
  readonly windowGrid: Targets[number]['window_grid'];
  readonly layers: readonly TileLayerRecord[];
  readonly layerProblems: readonly LayerProblem[];
  /** ProductRecords of the layers' source products; ids missing from sources.json are listed in productsMissing. */
  readonly products: readonly ProductInfo[];
  readonly productsMissing: readonly string[];
  readonly windows: readonly MapWindow[];
  readonly windowsFile: string;
  readonly windowsFilePresent: boolean;
  readonly nu: NuSummary;
  readonly cardId: string | null;
}

export interface BodyData {
  readonly body: Body;
  readonly bundleVersion: string;
  readonly mock: boolean;
  /** Set when targets.json is not in the bundle. */
  readonly targetsMissing: boolean;
  readonly targets: readonly MapTarget[];
}

function inExtent(extent: readonly number[], [x, y]: readonly [number, number]): boolean {
  const [x0, y0, x1, y1] = extent as [number, number, number, number];
  return x >= Math.min(x0, x1) && x <= Math.max(x0, x1) && y >= Math.min(y0, y1) && y <= Math.max(y0, y1);
}

function centroid(ring: readonly (readonly number[])[]): [number, number] {
  let x = 0;
  let y = 0;
  for (const point of ring) {
    x += point[0] as number;
    y += point[1] as number;
  }
  return [x / ring.length, y / ring.length];
}

function toPairs(ring: readonly (readonly number[])[]): [number, number][] {
  return ring.map((p) => [p[0] as number, p[1] as number]);
}

function nuSummary(target: Targets[number], entries: readonly WindowEntry[] | undefined): NuSummary {
  if (target.primary_window_id === null) {
    return { kind: 'unavailable', reason: 'primary_window_id is null in this target record (G0 has not frozen the window)' };
  }
  const entry = entries?.find((e) => e.window.window_id === target.primary_window_id);
  if (entry === undefined) {
    return { kind: 'unavailable', reason: `${target.primary_window_id} is not in windows/${target.body}.json` };
  }
  const fingerprint = entry.terrain;
  if (fingerprint === null || fingerprint.terrain === null) {
    return { kind: 'unavailable', reason: `no terrain fingerprint for ${target.primary_window_id} in the bundle` };
  }
  const { relief, texture } = fingerprint.terrain;
  const lags = [...new Set([...Object.keys(relief.nu_m), ...Object.keys(texture.nu_m)])].sort(
    (a, b) => Number(a) - Number(b),
  );
  return {
    kind: 'present',
    windowId: target.primary_window_id,
    evidenceLabel: fingerprint.evidence_label,
    productIds: fingerprint.product_ids,
    validFraction: fingerprint.terrain.valid_fraction,
    analysisGridM: fingerprint.terrain.analysis_grid_m,
    rows: lags.map((lag) => ({ lagM: lag, relief: relief.nu_m[lag] ?? null, texture: texture.nu_m[lag] ?? null })),
    hurstRelief: relief.hurst,
  };
}

function targetWindows(
  target: Targets[number],
  entries: readonly WindowEntry[],
  extents: readonly (readonly number[])[],
): MapWindow[] {
  const references = new Set(target.reference_window_ids);
  // A bodywide windows file holds every target of the body. Window ids end in -x<col>-y<row>, and
  // the part before that names the source product, so only the primary window's product is kept.
  const grid = (id: string): string | null => /^(.*)-x\d+-y\d+$/u.exec(id)?.[1] ?? null;
  const stem = target.primary_window_id === null ? null : grid(target.primary_window_id);
  const out: MapWindow[] = [];
  for (const entry of entries) {
    const id = entry.window.window_id;
    if (stem !== null && grid(id) !== null && grid(id) !== stem) continue;
    const isPrimary = id === target.primary_window_id;
    const isReference = references.has(id);
    const ring = entry.window.polygon.ring_m;
    const inside = extents.some((extent) => inExtent(extent, centroid(ring)));
    if (!(isPrimary || isReference || inside)) continue;
    out.push({
      id,
      ringM: toPairs(ring),
      status: entry.status,
      reasons: entry.reasons,
      isPrimary,
      isReference,
      cardId: entry.card_id,
    });
  }
  return out;
}

function productsOf(
  target: Targets[number],
  sources: Sources | null,
): { products: ProductInfo[]; productsMissing: string[] } {
  const ids = [...new Set([...target.product_ids, ...target.tile_layers.flatMap((l) => l.source_product_ids)])];
  const products: ProductInfo[] = [];
  const productsMissing: string[] = [];
  for (const id of ids) {
    const record = sources?.find((s) => s.product_id === id);
    if (record === undefined) {
      productsMissing.push(id);
      continue;
    }
    products.push({
      id,
      provider: record.provider,
      version: record.version,
      band: record.frequency_or_wavelength,
      qa: record.qa_definition,
      frame: record.crs_and_frame,
      licence: record.license_or_attribution,
    });
  }
  return { products, productsMissing };
}

export function buildBodyData(bundle: ExplorerBundle, body: Body): BodyData {
  const base = { body, bundleVersion: bundle.manifest.bundle_version, mock: bundle.manifest.mock };
  const all = bundle.records.targets;
  if (all === null) return { ...base, targetsMissing: true, targets: [] };
  const windowsFile = `windows/${body}.json`;
  const entries = bundle.records.windows.get(body);
  const targets = all
    .filter((t) => t.body === body)
    .map((target): MapTarget => {
      const { frame, drawable, problems } = resolveLayers(target.tile_layers);
      const extents = frame === null ? [] : drawable.map((l) => l.extent_m);
      const card = [...bundle.records.cards.values()].find((c) => c.target.target_id === target.target_id);
      return {
        id: target.target_id,
        name: target.name,
        description: target.description,
        mock: target.mock,
        crs: target.crs,
        projectionKind: target.projection.kind,
        productIds: target.product_ids,
        shippingMode: target.shipping_mode,
        primaryWindowId: target.primary_window_id,
        footprintRingM: target.footprint === null ? null : toPairs(target.footprint.ring_m),
        windowGrid: target.window_grid,
        layers: target.tile_layers,
        layerProblems: problems,
        ...productsOf(target, bundle.records.sources ?? null),
        windows: entries === undefined ? [] : targetWindows(target, entries, extents),
        windowsFile,
        windowsFilePresent: entries !== undefined,
        nu: nuSummary(target, entries),
        cardId: card?.card_id ?? null,
      };
    });
  return { ...base, targetsMissing: false, targets };
}

// ---------------------------------------------------------------- Earth

export const FAMILIES = ['terrain_relief', 'terrain_texture', 'radar_cpr', 'model_embedding'] as const;
export type Family = (typeof FAMILIES)[number];
export type DistanceEntry = ComparisonRecord['distances_by_family'][Family];

export interface Candidate {
  readonly earthWindowId: string;
  readonly targetWindowId: string;
  readonly distances: Readonly<Record<Family, DistanceEntry>>;
  readonly exclusions: readonly { readonly observable: string; readonly reasons: readonly string[] }[];
  readonly sortRule: string | null;
  readonly claimLimits: readonly string[];
  readonly cardId: string | null;
  readonly windowStatus: EligibilityStatus | null;
  readonly windowReasons: readonly string[];
  readonly ringLonLat: readonly (readonly [number, number])[] | null;
  readonly regionId: string | null;
  readonly shippingMode: string | null;
  /** A bundle figure (figures/*.svg) whose path names this window, as an SVG data URL; null when none. */
  readonly figureDataUrl: string | null;
  readonly figurePath: string | null;
  /** Centroid of the footprint in degrees [lon, lat]. */
  readonly centroidLonLat: readonly [number, number] | null;
}

export interface EarthTarget {
  readonly id: string;
  readonly name: string;
  readonly comparisonsFile: string;
  readonly comparisonsPresent: boolean;
  readonly candidates: readonly Candidate[];
}

export interface EarthData {
  readonly bundleVersion: string;
  readonly mock: boolean;
  readonly targetsMissing: boolean;
  readonly earthWindowsPresent: boolean;
  readonly g5: string | null;
  readonly modelInBundle: boolean;
  readonly targets: readonly EarthTarget[];
}

const MAX_FIGURE_CHARS = 30_000;

function figureFor(
  figures: ReadonlyMap<string, string> | undefined,
  windowId: string,
): { figureDataUrl: string | null; figurePath: string | null } {
  for (const [path, svg] of figures ?? []) {
    if (path.includes(windowId) && svg.length <= MAX_FIGURE_CHARS) {
      return { figureDataUrl: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`, figurePath: path };
    }
  }
  return { figureDataUrl: null, figurePath: null };
}

export function buildEarthData(bundle: ExplorerBundle): EarthData {
  const all = bundle.records.targets;
  const earthWindows = bundle.records.windows.get('earth');
  const g5 = bundle.validation.find((g) => g.gate_id === 'G5')?.status ?? null;
  const base = {
    bundleVersion: bundle.manifest.bundle_version,
    mock: bundle.manifest.mock,
    earthWindowsPresent: earthWindows !== undefined,
    g5,
    modelInBundle: bundle.records.modelCard !== null,
  };
  if (all === null) return { ...base, targetsMissing: true, targets: [] };
  const cards = [...bundle.records.cards.values()];
  const targets = all
    .filter((t) => t.body !== 'earth')
    .map((target): EarthTarget => {
      const records = bundle.records.comparisons.get(target.target_id);
      const candidates = (records ?? []).map((record): Candidate => {
        const entry = earthWindows?.find((e) => e.window.window_id === record.earth_window_id);
        const card = cards.find(
          (c) =>
            c.comparison_ref.target_window_id === record.target_window_id &&
            c.comparison_ref.earth_window_id === record.earth_window_id,
        );
        return {
          earthWindowId: record.earth_window_id,
          targetWindowId: record.target_window_id,
          distances: record.distances_by_family,
          exclusions: record.excluded_observables_and_reasons,
          sortRule: record.sort_rule,
          claimLimits: record.claim_limits,
          cardId: card?.card_id ?? entry?.card_id ?? null,
          windowStatus: entry?.status ?? null,
          windowReasons: entry?.reasons ?? [],
          ringLonLat: entry === undefined ? null : toPairs(entry.window.polygon.ring_lonlat),
          regionId: entry?.window.environment_flags.search_region_id ?? null,
          shippingMode: record.shipping_mode,
          ...figureFor(bundle.records.figures, record.earth_window_id),
          centroidLonLat: entry === undefined ? null : centroid(entry.window.polygon.ring_lonlat),
        };
      });
      return {
        id: target.target_id,
        name: target.name,
        comparisonsFile: `comparisons/${target.target_id}.json`,
        comparisonsPresent: records !== undefined,
        candidates,
      };
    });
  return { ...base, targetsMissing: false, targets };
}
