// Tile addressing and the projected frame (PRD 8.4, 8.10, D28). Everything here is read from
// TargetRecord.tile_layers exactly as the bundle stores it: origin, y direction, pixel sizes per
// zoom, extent, omitted tiles. Nothing is assumed about the body or the projection; there is no
// radius or other physical constant. The map works in "pyramid units": one unit is one tile pixel
// at zoom 0, so tile (z, x, y) covers 512 / 2^z units, which is what deck.gl's TileLayer indexes in
// an OrthographicView. A layer whose pyramid is not dyadic, or whose origin disagrees with the
// frame of the others, is reported Unavailable with the reason instead of being drawn wrongly.
import type { Targets } from '@terraluna/schema';

export type TileLayerRecord = Targets[number]['tile_layers'][number];
export type TargetRecord = Targets[number];

export interface Frame {
  readonly originX: number;
  readonly originY: number;
  /** Metres per pyramid unit, i.e. the pixel size at zoom 0 (extrapolated from zoom_min). */
  readonly metresPerUnit: number;
  /** True when tile y grows downward (y_direction "down"); deck.gl then flips the y axis. */
  readonly flipY: boolean;
  readonly zoomMin: number;
  readonly zoomMax: number;
}

export interface LayerProblem {
  readonly layer: string;
  readonly reason: string;
}

const DYADIC_TOLERANCE = 1e-6;

/** The reason a layer's pyramid cannot be addressed, or null when it can. */
export function pyramidProblem(layer: TileLayerRecord): string | null {
  const levels = layer.zoom_max - layer.zoom_min + 1;
  if (layer.zoom_max < layer.zoom_min) return 'zoom_max is below zoom_min in TileLayer';
  if (layer.metres_per_pixel_by_zoom.length !== levels) {
    return `metres_per_pixel_by_zoom has ${String(layer.metres_per_pixel_by_zoom.length)} entries for ${String(levels)} zoom levels`;
  }
  const first = layer.metres_per_pixel_by_zoom[0] as number;
  for (const [i, value] of layer.metres_per_pixel_by_zoom.entries()) {
    const expected = first / 2 ** i;
    if (Math.abs(value - expected) > DYADIC_TOLERANCE * expected) {
      return 'metres_per_pixel_by_zoom is not halved at each zoom level, so the tiles cannot be addressed as a standard pyramid';
    }
  }
  return null;
}

export function frameOf(layer: TileLayerRecord): Frame {
  const first = layer.metres_per_pixel_by_zoom[0] as number;
  return {
    originX: layer.origin_x_m,
    originY: layer.origin_y_m,
    metresPerUnit: first * 2 ** layer.zoom_min,
    flipY: layer.y_direction === 'down',
    zoomMin: layer.zoom_min,
    zoomMax: layer.zoom_max,
  };
}

function sameFrame(a: Frame, b: Frame): boolean {
  return (
    a.originX === b.originX &&
    a.originY === b.originY &&
    a.flipY === b.flipY &&
    Math.abs(a.metresPerUnit - b.metresPerUnit) <= DYADIC_TOLERANCE * a.metresPerUnit
  );
}

export interface ResolvedLayers {
  /** The shared frame, or null when no layer can be drawn. */
  readonly frame: Frame | null;
  readonly drawable: readonly TileLayerRecord[];
  readonly problems: readonly LayerProblem[];
}

/** Splits a target's tile layers into those that share one addressable frame and those that cannot be drawn. */
export function resolveLayers(layers: readonly TileLayerRecord[]): ResolvedLayers {
  const drawable: TileLayerRecord[] = [];
  const problems: LayerProblem[] = [];
  let frame: Frame | null = null;
  for (const layer of layers) {
    const problem = pyramidProblem(layer);
    if (problem !== null) {
      problems.push({ layer: layer.layer, reason: problem });
      continue;
    }
    const own = frameOf(layer);
    if (frame === null) {
      frame = own;
    } else if (!sameFrame(frame, own)) {
      problems.push({
        layer: layer.layer,
        reason: 'origin, y direction or pixel size differ from the other layers of this target, so it cannot share their map frame',
      });
      continue;
    }
    drawable.push(layer);
  }
  return { frame, drawable, problems };
}

export type Point = readonly [number, number];

/** Projected metres to pyramid units (the deck.gl world coordinates). */
export function toWorld(frame: Frame, [x, y]: Point): Point {
  const u = (x - frame.originX) / frame.metresPerUnit;
  const v = (frame.flipY ? frame.originY - y : y - frame.originY) / frame.metresPerUnit;
  return [u, v];
}

/** Pyramid units back to projected metres. */
export function fromWorld(frame: Frame, [u, v]: Point): Point {
  return [
    frame.originX + u * frame.metresPerUnit,
    frame.flipY ? frame.originY - v * frame.metresPerUnit : frame.originY + v * frame.metresPerUnit,
  ];
}

/** [minU, minV, maxU, maxV] of an [minX, minY, maxX, maxY] extent in metres. */
export function extentToWorld(frame: Frame, extent: readonly number[]): [number, number, number, number] {
  const [x0, y0, x1, y1] = extent as [number, number, number, number];
  const a = toWorld(frame, [x0, y0]);
  const b = toWorld(frame, [x1, y1]);
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
}

/** The zoom at which an extent just fits a viewport of the given size in pixels. */
export function fitZoom(
  worldExtent: readonly [number, number, number, number],
  widthPx: number,
  heightPx: number,
): number {
  const spanU = Math.max(worldExtent[2] - worldExtent[0], Number.EPSILON);
  const spanV = Math.max(worldExtent[3] - worldExtent[1], Number.EPSILON);
  return Math.log2(Math.min(widthPx / spanU, heightPx / spanV));
}

/** The URL of one tile under the asset base (PRD 8.4); the version names the bundle. */
export function tileUrl(
  assetBase: string,
  bundleVersion: string,
  layer: string,
  tile: { readonly z: number; readonly x: number; readonly y: number },
): string {
  const base = assetBase.replace(/\/+$/u, '');
  return `${base}/${bundleVersion}/tiles/${layer}/${String(tile.z)}/${String(tile.x)}/${String(tile.y)}.webp`;
}

export function isOmittedTile(
  layer: Pick<TileLayerRecord, 'omitted_tiles'>,
  tile: { readonly z: number; readonly x: number; readonly y: number },
): boolean {
  return layer.omitted_tiles.some(([z, x, y]) => z === tile.z && x === tile.x && y === tile.y);
}

/**
 * Where to draw a tile image given deck.gl's bbox (left, top < bottom in tile index space): the
 * image's top row sits at the smaller row index when y grows downward, and at the larger one when
 * y grows upward.
 */
export function tileBounds(
  frame: Pick<Frame, 'flipY'>,
  bbox: { readonly left: number; readonly top: number; readonly right: number; readonly bottom: number },
): [number, number, number, number] {
  return frame.flipY
    ? [bbox.left, bbox.bottom, bbox.right, bbox.top]
    : [bbox.left, bbox.top, bbox.right, bbox.bottom];
}
