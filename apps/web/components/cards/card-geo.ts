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
  readonly x: number;
  readonly y: number;
  /** The window's bounding box inside the tile, as fractions 0..1 of its width and height. */
  readonly box: { readonly left: number; readonly top: number; readonly width: number; readonly height: number };
}

/**
 * The deepest tile (largest zoom) that holds the whole window bounding box with room around it
 * (tile at least 3 times the window), among tiles `exists` reports. Null when no frame fits.
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
  const span = Math.max(maxX - minX, maxY - minY);
  let fallback: TilePick | null = null;
  for (let z = layer.zoom_max; z >= layer.zoom_min; z -= 1) {
    const mpp = layer.metres_per_pixel_by_zoom[z - layer.zoom_min];
    if (mpp === undefined) continue;
    const size = layer.tile_px * mpp;
    const x0 = Math.floor((minX - layer.origin_x_m) / size);
    const x1 = Math.floor((maxX - layer.origin_x_m) / size);
    const y0 = Math.floor((layer.origin_y_m - maxY) / size);
    const y1 = Math.floor((layer.origin_y_m - minY) / size);
    if (x0 !== x1 || y0 !== y1 || !exists(layer.layer, z, x0, y0)) continue;
    const pick: TilePick = {
      layer: layer.layer,
      z,
      x: x0,
      y: y0,
      box: {
        left: (minX - layer.origin_x_m - x0 * size) / size,
        top: (layer.origin_y_m - maxY - y0 * size) / size,
        width: (maxX - minX) / size,
        height: (maxY - minY) / size,
      },
    };
    fallback ??= pick;
    if (size >= 3 * span) return pick;
  }
  if (fallback !== null) return fallback;
  // No single tile holds the whole box (window on a tile seam): use the centroid's tile; the
  // outline is clipped by the frame.
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  for (let z = layer.zoom_min; z <= layer.zoom_max; z += 1) {
    const mpp = layer.metres_per_pixel_by_zoom[z - layer.zoom_min];
    if (mpp === undefined) continue;
    const size = layer.tile_px * mpp;
    const x = Math.floor((cx - layer.origin_x_m) / size);
    const y = Math.floor((layer.origin_y_m - cy) / size);
    if (!exists(layer.layer, z, x, y)) continue;
    fallback = {
      layer: layer.layer, z, x, y,
      box: { left: (minX - layer.origin_x_m - x * size) / size, top: (layer.origin_y_m - maxY - y * size) / size, width: (maxX - minX) / size, height: (maxY - minY) / size },
    };
    if (size >= 3 * span) break;
  }
  return fallback;
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
