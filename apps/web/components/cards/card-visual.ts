// Per-card view data for the atlas and the detail hero: display names, the two hillshade tiles
// (target and Earth) and their window markers. Everything is read from the loaded bundle; the
// only literals are display names of Earth regions (card-geo.ts).
import { existsSync } from 'node:fs';
import { join } from 'node:path';

import type { CardRecord } from '@terraluna/schema';

import type { PulledBundle } from '../../lib/bundle.ts';
import { earthRegionName, layerForRegion, layerForTarget, pickTile, type TilePick, type TileLayerGeo } from './card-geo.ts';

export interface TileView {
  readonly tiles: readonly { readonly src: string; readonly x: number; readonly y: number }[];
  readonly pick: TilePick;
  readonly caption: string;
}

export interface CardVisual {
  readonly cardId: string;
  readonly body: 'moon' | 'mars';
  readonly targetKey: string;
  readonly targetName: string;
  readonly earthName: string;
  readonly headline: string;
  readonly targetTile: TileView | null;
  readonly earthTile: TileView | null;
}

/** "Connecting Ridge (PGDA78 Site01)" shows as "Connecting Ridge". */
export const shortName = (name: string): string => name.replace(/\s*\(.*\)\s*$/u, '');

function assetBase(): string {
  const v = process.env.NEXT_PUBLIC_ASSET_BASE;
  return v === undefined || v === '' ? '/assets' : v;
}

function tileExistsFn(version: string): (layer: string, z: number, x: number, y: number) => boolean {
  const root = join(/* turbopackIgnore: true */ process.cwd(), 'public', 'assets', version, 'tiles');
  if (!existsSync(root)) return () => true;
  return (layer, z, x, y) => existsSync(`${root}/${layer}/${String(z)}/${String(x)}/${String(y)}.webp`);
}

export function buildCardVisual(bundle: PulledBundle, card: CardRecord): CardVisual {
  const version = bundle.manifest.bundle_version;
  const targets = bundle.records.targets ?? [];
  const rec = targets.find((t) => t.target_id === card.target.target_id);
  const layers: readonly TileLayerGeo[] = (rec?.tile_layers ?? []);
  const exists = tileExistsFn(version);
  const targetName = shortName(rec?.name ?? card.target.target_id);
  const earthName = earthRegionName(card.earth.search_region_id ?? card.earth.earth_window_id);
  const body = card.target.body === 'mars' ? 'mars' : 'moon';
  // Target ids look like "mars-jezero-w000" / "moon-cr-site01-w000"; the layer name carries the middle.
  const key = card.target.target_id.split('-').slice(1, -1).filter((s) => s !== 'cr' && s !== 'sps').join('-');
  const mk = (layer: TileLayerGeo | null, ring: readonly (readonly number[])[], caption: string): TileView | null => {
    if (layer === null) return null;
    const pick = pickTile(layer, ring, exists);
    if (pick === null) return null;
    return {
      tiles: pick.tiles.map((t) => ({
        ...t,
        src: `${assetBase()}/${version}/tiles/${layer.layer}/${String(pick.z)}/${String(t.x)}/${String(t.y)}.webp`,
      })),
      pick,
      caption,
    };
  };
  return {
    cardId: card.card_id,
    body,
    targetKey: card.target.target_id,
    targetName,
    earthName,
    headline: `${targetName} ↔ ${earthName}`,
    targetTile: mk(layerForTarget(layers, body, key), card.target.polygon.ring_lonlat, `${targetName} hillshade`),
    earthTile: mk(card.earth.search_region_id === null ? null : layerForRegion(layers, card.earth.search_region_id), card.earth.polygon.ring_lonlat, `${earthName} hillshade`),
  };
}
