// Build-time data for /pitch. Every number on the deck is read here from shared artifacts: the
// release bundle (cards, windows, model card, manifest) and the paper's generated numbers.tex
// (wasm parity and throughput, generated from outputs/probe and the run records). Nothing is typed in;
// a value that cannot be read stays null and the slide shows it as Unavailable.
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { buildCardVisual, type TileView } from '@/components/cards/card-visual';
import { buildNuSeries, variantOf } from '@/components/charts/series';
import type { PulledBundle } from '@/lib/bundle';
import type { ProbeTargetFingerprint } from '@/lib/probe/result';
import { probeTargets } from '@/lib/probe/targets';

export interface NuCurve {
  readonly id: string;
  readonly role: string;
  readonly points: readonly { lag_m: number; nu: number }[];
}

export interface CardRange {
  readonly targetId: string;
  readonly targetName: string;
  readonly earthRegions: readonly string[];
  readonly min: number | null;
  readonly max: number | null;
  readonly unit: string | null;
  readonly n: number;
}

export interface LonLat {
  readonly lon: number;
  readonly lat: number;
}

export interface TargetTile {
  readonly targetId: string;
  readonly name: string;
  readonly body: 'moon' | 'mars';
  readonly tile: TileView | null;
}

export interface ResultCard {
  readonly cardId: string;
  readonly targetName: string;
  readonly earthName: string;
  readonly body: 'moon' | 'mars';
  readonly relief: number;
  readonly unit: string;
  /** 100 - background_percentile, floored (as the card atlas shows it), or null. */
  readonly closerThan: number | null;
  readonly label: string;
  readonly targetTile: TileView | null;
  readonly earthTile: TileView | null;
}

export interface PitchData {
  readonly bundleVersion: string;
  readonly mock: boolean;
  readonly shippingMode: string | null;
  readonly g5Status: string | null;
  readonly cardCount: number;
  readonly ranges: readonly CardRange[];
  readonly best: { readonly targetId: string; readonly earthRegion: string; readonly value: number; readonly unit: string } | null;
  readonly curves: readonly NuCurve[];
  readonly curvesFrom: string | null;
  readonly lags: readonly number[];
  readonly model: {
    readonly parameters: number | null;
    readonly trainWindows: number | null;
    readonly totalWindows: number | null;
    readonly embeddingDim: number | null;
    readonly probeR2Low: number | null;
    readonly wasmMinCosine: number | null;
    readonly mpsMedian: number | null;
    readonly cpuMedian: number | null;
    readonly version: string | null;
  };
  readonly twin: { readonly earth: LonLat; readonly target: LonLat; readonly earthName: string; readonly targetName: string } | null;
  readonly paperHref: string | null;
  readonly targets: readonly TargetTile[];
  readonly results: readonly ResultCard[];
  /** The finder slide: one evidence card's Earth window centre, probed live against its target. */
  readonly finder: {
    readonly lat: number;
    readonly lon: number;
    readonly earthName: string;
    readonly targetId: string;
    readonly targetName: string;
    readonly relief: number | null;
    readonly texture: number | null;
    readonly closerThan: number | null;
    readonly fingerprints: ProbeTargetFingerprint[];
    readonly bundleVersion: string;
  } | null;
  /** The Training slide: report/curves.json loss terms, mean over seeds per epoch. */
  readonly training: { readonly mock: boolean; readonly terms: readonly { readonly term: string; readonly points: readonly { epoch: number; mean: number }[] }[] } | null;
  /** The Lab slide: an Earth window centre ("@lat,lon") and the Lab's target site id. */
  readonly lab: { readonly earth: string; readonly earthName: string; readonly target: string; readonly targetName: string } | null;
}

const TARGET_NAMES: Record<string, string> = {
  'mars-jezero-w000': 'Jezero crater',
  'mars-arcadia-w000': 'Arcadia Planitia',
  'moon-cr-site01-w000': 'Connecting Ridge',
};

export const targetName = (id: string): string => TARGET_NAMES[id] ?? id;

function regionName(id: string): string {
  return id
    .split('-')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/** `\newcommand{\Name}{value}` lines of paper/generated/numbers.tex, or an empty map. */
function readNumbersTex(): Map<string, string> {
  const out = new Map<string, string>();
  const candidates = [join(process.cwd(), '../../paper/generated/numbers.tex'), join(process.cwd(), 'paper/generated/numbers.tex')];
  const path = candidates.find((p) => existsSync(p));
  if (path === undefined) return out;
  for (const m of readFileSync(path, 'utf8').matchAll(/\\newcommand\{\\([A-Za-z]+)\}\{([^}]*)\}/g)) {
    const [, name, value] = m;
    if (name !== undefined && value !== undefined) out.set(name, value);
  }
  return out;
}

function num(v: string | undefined): number | null {
  if (v === undefined) return null;
  const n = Number(v.replace(/,/g, ''));
  return Number.isFinite(n) ? n : null;
}

function centroid(ring: readonly (readonly number[])[]): LonLat | null {
  const pts = ring.slice(0, -1).filter((p) => p.length >= 2);
  if (pts.length === 0) return null;
  const lon = pts.reduce((s, p) => s + (p[0] ?? 0), 0) / pts.length;
  const lat = pts.reduce((s, p) => s + (p[1] ?? 0), 0) / pts.length;
  return { lon, lat };
}

export function buildPitchData(bundle: PulledBundle): PitchData {
  const { manifest, records } = bundle;
  const cards = [...records.cards.values()];

  // per target: the range of terrain-relief distances over its cards
  const byTarget = new Map<string, { values: number[]; regions: Set<string>; unit: string | null }>();
  let best: PitchData['best'] = null;
  let bestCard: (typeof cards)[number] | null = null;
  for (const card of cards) {
    const id = card.target.target_id;
    const slot = byTarget.get(id) ?? { values: [], regions: new Set<string>(), unit: null };
    const rel = card.distances_by_family.terrain_relief;
    if (rel.status === 'available' && rel.value !== null) {
      slot.values.push(rel.value);
      slot.unit = rel.unit;
      if (best === null || rel.value < best.value) {
        best = { targetId: id, earthRegion: regionName(card.earth.search_region_id ?? card.earth.earth_window_id), value: rel.value, unit: rel.unit };
        bestCard = card;
      }
    }
    if (card.earth.search_region_id != null) slot.regions.add(regionName(card.earth.search_region_id));
    byTarget.set(id, slot);
  }
  const order = ['moon-cr-site01-w000', 'mars-jezero-w000', 'mars-arcadia-w000'];
  const ranges: CardRange[] = [...byTarget.entries()]
    .sort(([a], [b]) => (order.indexOf(a) + 99) % 100 - ((order.indexOf(b) + 99) % 100))
    .map(([id, s]) => ({
      targetId: id,
      targetName: targetName(id),
      earthRegions: [...s.regions],
      min: s.values.length ? Math.min(...s.values) : null,
      max: s.values.length ? Math.max(...s.values) : null,
      unit: s.unit,
      n: s.values.length,
    }));

  // nu(L) relief curves of the finder story's card (Jezero and Rosamond Dry Lake, the closest such
  // card; the overall best card if there is none), from windows/<body>.json
  const jezRos = cards
    .filter((c) => c.target.target_id === 'mars-jezero-w000' && c.earth.search_region_id === 'rosamond-dry-lake' && c.distances_by_family.terrain_relief.value !== null)
    .sort((a, b) => (a.distances_by_family.terrain_relief.value ?? 0) - (b.distances_by_family.terrain_relief.value ?? 0))[0];
  const curveCard = jezRos ?? bestCard;
  const curves: NuCurve[] = [];
  let curvesFrom: string | null = null;
  if (curveCard !== null) {
    const pick = (body: string, windowId: string, role: string) => {
      const list = records.windows.get(body);
      const entry = list?.find((e) => e.window.window_id === windowId);
      const variant = variantOf(entry?.terrain ?? null, 'relief');
      if (variant === null) return;
      const pts = buildNuSeries(variant).points.filter((p) => p.nu > 0 && p.lag_m > 0);
      if (pts.length >= 2) curves.push({ id: windowId, role, points: pts.map((p) => ({ lag_m: p.lag_m, nu: p.nu })) });
    };
    pick(curveCard.target.body, curveCard.target.target_window_id, targetName(curveCard.target.target_id));
    pick('earth', curveCard.earth.earth_window_id, regionName(curveCard.earth.search_region_id ?? 'earth'));
    curvesFrom = curves.length ? 'windows/<body>.json, relief nu_m' : null;
  }
  const lags = [...new Set(curves.flatMap((c) => c.points.map((p) => p.lag_m)))].sort((a, b) => a - b);

  // model card
  const mc = records.modelCard;
  let trainWindows: number | null = null;
  let totalWindows: number | null = null;
  if (mc !== null) {
    const counts = Object.values(mc.training_data.window_counts) as { train?: number; val?: number; test?: number }[];
    trainWindows = counts.reduce((s, c) => s + (c.train ?? 0), 0);
    totalWindows = counts.reduce((s, c) => s + (c.train ?? 0) + (c.val ?? 0) + (c.test ?? 0), 0);
  }
  const relief = (mc?.metrics_with_ci ?? []).filter(
    (m) => m.test_id === 'physics_probe' && m.body === 'pooled' && typeof m.target === 'string' && m.target.startsWith('log_nu_relief_') && m.ci_low !== null,
  );
  const probeR2Low = relief.length ? Math.min(...relief.map((m) => m.ci_low as number)) : null;
  const dimMatch = mc?.architecture.match(/(\d+)-d/);

  const tex = readNumbersTex();

  // the twin: centroids of the best card's two window polygons
  let twin: PitchData['twin'] = null;
  if (bestCard !== null) {
    const e = centroid(bestCard.earth.polygon.ring_lonlat);
    const t = centroid(bestCard.target.polygon.ring_lonlat);
    if (e !== null && t !== null) {
      twin = { earth: e, target: t, earthName: regionName(bestCard.earth.search_region_id ?? 'earth'), targetName: targetName(bestCard.target.target_id) };
    }
  }

  // Problem slide: one hillshade per target, from its first card. Results slide: Jezero's
  // Rosamond Dry Lake card and Arcadia's closest card, values read from the cards.
  const targets: TargetTile[] = order.flatMap((id) => {
    const card = cards.find((c) => c.target.target_id === id);
    if (card === undefined) return [];
    const v = buildCardVisual(bundle, card);
    return [{ targetId: id, name: v.targetName, body: v.body, tile: v.targetTile }];
  });
  const relOf = (c: (typeof cards)[number]) => c.distances_by_family.terrain_relief;
  const closest = (list: typeof cards) =>
    list.filter((c) => relOf(c).value !== null).sort((a, b) => (relOf(a).value ?? 0) - (relOf(b).value ?? 0))[0];
  const picks = [
    closest(cards.filter((c) => c.target.target_id === 'mars-jezero-w000' && c.earth.search_region_id === 'rosamond-dry-lake')),
    closest(cards.filter((c) => c.target.target_id === 'mars-arcadia-w000')),
  ];
  const results: ResultCard[] = picks.flatMap((card) => {
    if (card === undefined) return [];
    const r = relOf(card);
    if (r.value === null) return [];
    const v = buildCardVisual(bundle, card);
    return [{
      cardId: card.card_id,
      targetName: v.targetName,
      earthName: v.earthName,
      body: v.body,
      relief: r.value,
      unit: r.unit ?? '',
      closerThan: r.background_percentile === null ? null : Math.floor(100 - r.background_percentile),
      label: r.evidence_label,
      targetTile: v.targetTile,
      earthTile: v.earthTile,
    }];
  });
  const fc = picks[0];
  const fcC = fc === undefined ? null : centroid(fc.earth.polygon.ring_lonlat);
  const finder: PitchData['finder'] =
    fc === undefined || fcC === null
      ? null
      : {
          lat: fcC.lat,
          lon: fcC.lon,
          earthName: buildCardVisual(bundle, fc).earthName,
          targetId: fc.target.target_id,
          targetName: targetName(fc.target.target_id),
          relief: relOf(fc).value,
          texture: fc.distances_by_family.terrain_texture.value,
          closerThan: relOf(fc).background_percentile === null ? null : Math.floor(100 - (relOf(fc).background_percentile ?? 0)),
          fingerprints: probeTargets(bundle).fingerprints.filter((f) => f.target_id === fc.target.target_id),
          bundleVersion: manifest.bundle_version,
        };
  const holu = cards.find((c) => c.earth.search_region_id === 'holuhraun');
  const holuC = holu === undefined ? null : centroid(holu.earth.polygon.ring_lonlat);
  const lab = holuC === null ? null : { earth: `@${holuC.lat.toFixed(2)},${holuC.lon.toFixed(2)}`, earthName: 'Holuhraun, Iceland', target: 'jezero', targetName: 'Jezero crater' };

  const paper = join(process.cwd(), 'public/paper/terraluna-paper.pdf');

  const cv = records.curves;
  const training: PitchData['training'] =
    cv === null
      ? null
      : {
          mock: cv.mock,
          terms: cv.loss_terms
            .map((t) => ({ term: t.term, points: t.epochs.filter((e) => e.mean > 0).map((e) => ({ epoch: e.epoch, mean: e.mean })) }))
            .filter((t) => t.points.length >= 2),
        };

  return {
    bundleVersion: manifest.bundle_version,
    mock: manifest.mock,
    shippingMode: manifest.shipping_mode,
    g5Status: mc?.g5_status ?? null,
    cardCount: cards.length,
    ranges,
    best,
    curves,
    curvesFrom,
    lags,
    model: {
      parameters: mc?.parameter_count ?? null,
      trainWindows,
      totalWindows,
      embeddingDim: dimMatch?.[1] !== undefined ? Number(dimMatch[1]) : null,
      probeR2Low,
      wasmMinCosine: num(tex.get('WasmMinCosine')),
      mpsMedian: num(tex.get('ThruMpsTwelveMedian')),
      cpuMedian: num(tex.get('ThruCpuMedian')),
      version: mc?.version ?? null,
    },
    twin,
    paperHref: existsSync(paper) ? '/paper/terraluna-paper.pdf' : null,
    targets,
    results,
    finder,
    training,
    lab,
  };
}
