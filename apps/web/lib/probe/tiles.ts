// Copernicus GLO-30 tiles touched by a D29 window (PRD 8.7 step 2). Runs in the probe worker:
// no DOM or Node API. Tile names follow the public bucket's key pattern
// Copernicus_DSM_COG_10_{N|S}{lat:02}_00_{E|W}{lon:03}_00_DEM, named by the south-west corner
// (floor of latitude and longitude) of the 1 degree tile (Copernicus DEM product handbook).

import { type WindowGrid, aeqdInverse, gridMesh } from '@terraluna/core';

import { DEM_TILE_PATTERN } from '../../server/api/dem-contract.ts';

const DEG = 180 / Math.PI;

export function glo30TileName(latFloor: number, lonFloor: number): string {
  const ns = latFloor >= 0 ? 'N' : 'S';
  const ew = lonFloor >= 0 ? 'E' : 'W';
  const name = `Copernicus_DSM_COG_10_${ns}${String(Math.abs(latFloor)).padStart(2, '0')}_00_${ew}${String(Math.abs(lonFloor)).padStart(3, '0')}_00_DEM`;
  if (!DEM_TILE_PATTERN.test(name)) throw new RangeError(`no GLO-30 tile name for ${String(latFloor)}, ${String(lonFloor)}`);
  return name;
}

/** Geographic bounds in degrees; lonMin/lonMax are unwrapped around the centre (may pass +-180). */
export interface GeoBounds {
  latMin: number;
  latMax: number;
  lonMin: number;
  lonMax: number;
}

function wrapDeg(d: number): number {
  return ((((d + 180) % 360) + 360) % 360) - 180;
}

/** Bounds of every pixel centre of the grids about (lat0, lon0) on the sphere of radius R. */
export function windowBounds(lat0Deg: number, lon0Deg: number, radiusM: number, grids: readonly WindowGrid[]): GeoBounds {
  const bounds: GeoBounds = { latMin: Infinity, latMax: -Infinity, lonMin: Infinity, lonMax: -Infinity };
  for (const grid of grids) {
    const { x, y } = gridMesh(grid);
    const { lat, lon } = aeqdInverse(x, y, { lat0Rad: lat0Deg / DEG, lon0Rad: wrapDeg(lon0Deg) / DEG, radiusM });
    for (let i = 0; i < lat.length; i += 1) {
      const la = (lat[i] as number) * DEG;
      const lo = lon0Deg + wrapDeg((lon[i] as number) * DEG - lon0Deg);
      bounds.latMin = Math.min(bounds.latMin, la);
      bounds.latMax = Math.max(bounds.latMax, la);
      bounds.lonMin = Math.min(bounds.lonMin, lo);
      bounds.lonMax = Math.max(bounds.lonMax, lo);
    }
  }
  return bounds;
}

export interface TileRef {
  name: string;
  latFloor: number;
  /** Longitude floor in [-180, 180). */
  lonFloor: number;
  /** Turns added to bring the tile next to the window centre (0, or +-360 across the antimeridian). */
  lonShiftDeg: number;
}

/** GLO-30 latitude coverage: tiles exist from 90 S to 90 N (ocean tiles are absent). */
export const MAX_TILES_PER_PROBE = 4;

export type TileSelection = { status: 'available'; tiles: TileRef[] } | { status: 'unavailable'; reason: string };

/** The 1 degree tiles the bounds (plus a margin in degrees) touch. */
export function tilesForBounds(bounds: GeoBounds, marginDeg: number): TileSelection {
  const latLo = Math.floor(bounds.latMin - marginDeg);
  const latHi = Math.floor(bounds.latMax + marginDeg);
  const lonLo = Math.floor(bounds.lonMin - marginDeg);
  const lonHi = Math.floor(bounds.lonMax + marginDeg);
  if (latLo < -90 || latHi > 89) return { status: 'unavailable', reason: 'window_reaches_a_pole' };
  const tiles: TileRef[] = [];
  for (let la = latHi; la >= latLo; la -= 1) {
    for (let lo = lonLo; lo <= lonHi; lo += 1) {
      const wrapped = wrapDeg(lo);
      tiles.push({ name: glo30TileName(la, wrapped), latFloor: la, lonFloor: wrapped, lonShiftDeg: lo - wrapped });
    }
  }
  if (tiles.length > MAX_TILES_PER_PROBE) return { status: 'unavailable', reason: 'window_touches_too_many_tiles' };
  return { status: 'available', tiles };
}
