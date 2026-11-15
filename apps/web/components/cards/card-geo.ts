// Which bundle hillshade tile shows a card's window, and where the window sits inside it.
// Pure geometry on the bundle's own tile-layer records (targets.json tile_layers): no result is
// computed here, only a picture location. Layer frames: EPSG:3857, a local AEQD sphere (Mars) or
// a south polar stereographic frame (Moon), each read from the layer's crs string.
import type { CardRecord } from '@terraluna/schema';

export interface TileLayerGeo {
  readonly layer: string;
  readonly crs: string;
  readonly body: string;
  readonly tile_px: number;
  readonly origin_x_m: number;
  readonly origin_y_m: number;
  readonly metres_per_pixel_by_zoom: readonly number[];
  readonly zoom_min: number;
  readonly zoom_max: number;
}

type Project = (lon: number, lat: number) => readonly [number, number] | null;

const RAD = Math.PI / 180;
const WEB_MERCATOR_R = 6378137;

function num(crs: string, key: string): number | null {
  const m = new RegExp(`\\+${key}=(-?[0-9.eE+-]+)`, 'u').exec(crs);
  return m?.[1] === undefined ? null : Number(m[1]);
}

/** Forward projection of a lon/lat (degrees) into a layer's frame, or null for an unknown frame. */
export function projectorFor(crs: string): Project | null {
  if (crs === 'EPSG:3857') {
    return (lon, lat) => [WEB_MERCATOR_R * lon * RAD, WEB_MERCATOR_R * Math.log(Math.tan(Math.PI / 4 + (lat * RAD) / 2))];
  }
  if (crs.startsWith('+proj=aeqd')) {
    const lat0 = num(crs, 'lat_0');
    const lon0 = num(crs, 'lon_0');
    const R = num(crs, 'R');
    if (lat0 === null || lon0 === null || R === null) return null;
    return (lon, lat) => {
      const p = lat * RAD;
      const p0 = lat0 * RAD;
      const dl = (lon - lon0) * RAD;
      const cosC = Math.sin(p0) * Math.sin(p) + Math.cos(p0) * Math.cos(p) * Math.cos(dl);
      const c = Math.acos(Math.min(1, Math.max(-1, cosC)));
      const k = c === 0 ? 1 : c / Math.sin(c);
      return [R * k * Math.cos(p) * Math.sin(dl), R * k * (Math.cos(p0) * Math.sin(p) - Math.sin(p0) * Math.cos(p) * Math.cos(dl))];
    };
  }
  if (crs.includes('Polar_Stereographic') && /latitude_of_origin",-90/u.test(crs)) {
    const R = Number(/SPHEROID\["[^"]*",([0-9.]+)/u.exec(crs)?.[1]);
    const lon0 = Number(/central_meridian",(-?[0-9.]+)/u.exec(crs)?.[1] ?? '0');
    if (!Number.isFinite(R)) return null;
    return (lon, lat) => {
      const rho = 2 * R * Math.tan(Math.PI / 4 + (lat * RAD) / 2);
      const dl = (lon - lon0) * RAD;
      return [rho * Math.sin(dl), rho * Math.cos(dl)];
    };
  }
  return null;
}

export interface TilePick {
  readonly layer: string;
  readonly z: number;
  /** The square view, in tile units of zoom z (1 = one tile edge): its top-left corner and side. */
  readonly view: { readonly x0: number; readonly y0: number; readonly side: number };
  /** The existing tiles that overlap the view. */
  readonly tiles: readonly { readonly x: number; readonly y: number }[];
  /** The window's bounding box as fractions 0..1 of the view. */
  readonly box: { readonly left: number; readonly top: number; readonly width: number; readonly height: number };
}

/**
 * A square view around the window, about three window widths wide, drawn from the tiles of the
 * deepest zoom whose tile is at least that wide (else the widest zoom). Tiles that do not exist
 * are left out (the frame stays dark there). Null when no tile overlaps the view.
 */
export function pickTile(
  layer: TileLayerGeo,
  ring: readonly (readonly number[])[],
  exists: (layer: string, z: number, x: number, y: number) => boolean,
): TilePick | null {
  const project = projectorFor(layer.crs);
  if (project === null) return null;
  const pts = ring.map((p) => project(p[0] ?? 0, p[1] ?? 0));
  if (pts.some((p) => p === null)) return null;
  const xs = pts.map((p) => p?.[0] ?? 0);
  const ys = pts.map((p) => p?.[1] ?? 0);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const want = 3 * Math.max(maxX - minX, maxY - minY);
  let z = layer.zoom_min;
  for (let k = layer.zoom_max; k >= layer.zoom_min; k -= 1) {
    const mpp = layer.metres_per_pixel_by_zoom[k - layer.zoom_min];
    if (mpp !== undefined && layer.tile_px * mpp >= want) {
      z = k;
      break;
    }
  }
  const mpp = layer.metres_per_pixel_by_zoom[z - layer.zoom_min];
  if (mpp === undefined) return null;
  const size = layer.tile_px * mpp;
  const side = want / size;
  const gx = (minX - layer.origin_x_m) / size;
  const gy = (layer.origin_y_m - maxY) / size;
  const gw = (maxX - minX) / size;
  const gh = (maxY - minY) / size;
  const x0 = gx + gw / 2 - side / 2;
  const y0 = gy + gh / 2 - side / 2;
  const tiles: { x: number; y: number }[] = [];
  for (let x = Math.floor(x0); x < x0 + side; x += 1) {
    for (let y = Math.floor(y0); y < y0 + side; y += 1) {
      if (x >= 0 && y >= 0 && exists(layer.layer, z, x, y)) tiles.push({ x, y });
    }
  }
  if (tiles.length === 0) return null;
  return {
    layer: layer.layer,
    z,
    view: { x0, y0, side },
    tiles,
    box: { left: (gx - x0) / side, top: (gy - y0) / side, width: gw / side, height: gh / side },
  };
}

const EARTH_REGIONS: Readonly<Record<string, string>> = {
  holuhraun: 'Holuhraun, Iceland',
  'rosamond-dry-lake': 'Rosamond Dry Lake, California',
};

/** Display name of an Earth search region (a label only; the id is the bundle's). */
export function earthRegionName(searchRegionId: string): string {
  return (
    EARTH_REGIONS[searchRegionId] ??
    searchRegionId.replaceAll('-', ' ').replace(/^./u, (c) => c.toUpperCase())
  );
}

export const BODY_COLOURS = { moon: '#C9C5BC', mars: '#E08A5F', earth: '#8FB4EA' } as const;

/** The hillshade layer's name stem for a target, matched by name against the bundle's layers. */
export function layerForTarget(layers: readonly TileLayerGeo[], body: string, targetKey: string): TileLayerGeo | null {
  return layers.find((l) => l.body === body && l.layer.includes(targetKey)) ?? null;
}

export function layerForRegion(layers: readonly TileLayerGeo[], searchRegionId: string): TileLayerGeo | null {
  return layers.find((l) => l.body === 'earth' && l.layer.includes(searchRegionId)) ?? null;
}

export type CardTarget = CardRecord['target'];
