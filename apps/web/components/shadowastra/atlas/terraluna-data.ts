// Terraluna's own layer on top of the ported ShadowAstra Atlas (PRD 8.6, 10). Server-side: it
// reuses the bundle projections of components/atlas/atlas-data.ts and keeps only what the Atlas
// overlay draws: target and card-window pins, the evidence cards with their relief values and
// evidence labels, and the bundle's Web Mercator hillshade pyramids. Nothing here is typed in;
// a value the bundle does not hold stays null and is shown as unavailable.
import { buildAtlasBody, buildAtlasEarth, type AtlasData, type BundleTileLayer } from '@/components/atlas/atlas-data';
import type { WindowRelief } from '@/components/map/explore-data';
import type { ExplorerBundle } from '@/components/map/explore-data';

export type TlBody = 'earth' | 'moon' | 'mars';

export interface TlPin {
  readonly id: string;
  readonly body: TlBody;
  readonly lat: number;
  readonly lon: number;
  readonly label: string;
  readonly href: string | null;
  readonly cardIds: readonly string[];
  readonly kind: 'target' | 'card-window';
}

export interface TlCard {
  readonly cardId: string;
  readonly title: string;
  readonly targetBody: TlBody;
  readonly targetName: string;
  readonly earthWindowId: string;
  readonly shippingMode: string;
  readonly targetRelief: WindowRelief | null;
  readonly earthRelief: WindowRelief | null;
  readonly earthPinId: string | null;
  readonly targetPinId: string | null;
}

export interface TerralunaAtlas {
  readonly bundleVersion: string;
  readonly mock: boolean;
  readonly pins: readonly TlPin[];
  readonly cards: readonly TlCard[];
  /** Earth hillshade pyramids (EPSG:3857); Moon/Mars pyramids are in local metre frames. */
  readonly earthHillshade: readonly BundleTileLayer[];
  readonly projectedHillshade: readonly { readonly name: string; readonly body: TlBody }[];
}

export function buildTerralunaAtlas(bundle: ExplorerBundle): TerralunaAtlas {
  const parts: AtlasData[] = [buildAtlasEarth(bundle), buildAtlasBody(bundle, 'moon'), buildAtlasBody(bundle, 'mars')];
  const earth = parts[0] as AtlasData;
  const cards = [...bundle.records.cards.values()].sort((a, b) => a.card_id.localeCompare(b.card_id));
  const windowById = new Map(parts.flatMap((p) => p.windows.map((w) => [w.id, w] as const)));
  const targetById = new Map(parts.flatMap((p) => p.targets.map((t) => [t.id, { t, body: p.body }] as const)));

  const pins: TlPin[] = [];
  for (const p of parts.slice(1)) {
    for (const t of p.targets) {
      if (t.lat === null || t.lon === null) continue;
      const own = cards.filter((c) => c.target.target_id === t.id).map((c) => c.card_id);
      pins.push({ id: `tl:${t.id}`, body: p.body, lat: t.lat, lon: t.lon, label: t.name, href: t.cardId === null ? null : `/site/${t.cardId}`, cardIds: own, kind: 'target' });
    }
  }
  for (const w of earth.windows) {
    const own = cards.filter((c) => c.comparison_ref.earth_window_id === w.id).map((c) => c.card_id);
    if (own.length === 0 || w.lat === null || w.lon === null) continue;
    pins.push({ id: `tl:${w.id}`, body: 'earth', lat: w.lat, lon: w.lon, label: `Card window ${w.id}`, href: `/site/${own[0] as string}`, cardIds: own, kind: 'card-window' });
  }

  const tlCards = cards.map((c): TlCard => {
    const target = targetById.get(c.target.target_id);
    const ew = c.comparison_ref.earth_window_id;
    return {
      cardId: c.card_id,
      title: c.title,
      targetBody: c.target.body as TlBody,
      targetName: target?.t.name ?? c.target.target_id,
      earthWindowId: ew,
      shippingMode: c.shipping_mode,
      targetRelief: windowById.get(c.comparison_ref.target_window_id)?.relief ?? null,
      earthRelief: windowById.get(ew)?.relief ?? null,
      earthPinId: pins.some((p) => p.id === `tl:${ew}`) ? `tl:${ew}` : null,
      targetPinId: pins.some((p) => p.id === `tl:${c.target.target_id}`) ? `tl:${c.target.target_id}` : null,
    };
  });

  return {
    bundleVersion: earth.bundleVersion,
    mock: earth.mock,
    pins,
    cards: tlCards,
    earthHillshade: earth.tileLayers.filter((l) => l.kind === 'mercator'),
    projectedHillshade: parts.slice(1).flatMap((p) => p.tileLayers.map((l) => ({ name: l.name, body: p.body }))),
  };
}
