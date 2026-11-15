// Earth view state in the query string: `family` (one distance family at a time) and `target`.
// An invalid value falls back to the default; parsing never throws.
import { FAMILIES, type Family } from './explore-data.ts';

export interface EarthView {
  readonly family: Family;
  readonly target: string | null;
}

export const DEFAULT_EARTH_VIEW: EarthView = { family: 'terrain_relief', target: null };

const TARGET_ID = /^(moon|mars|earth)-[a-z0-9][a-z0-9-]*$/u;

export function parseEarthView(search: string): EarthView {
  const params = new URLSearchParams(search);
  const family = params.get('family');
  const target = params.get('target');
  return {
    family: (FAMILIES as readonly string[]).includes(family ?? '') ? (family as Family) : DEFAULT_EARTH_VIEW.family,
    target: target !== null && TARGET_ID.test(target) ? target : null,
  };
}

export function serializeEarthView(view: EarthView): string {
  const params = new URLSearchParams();
  if (view.target !== null) params.set('target', view.target);
  if (view.family !== DEFAULT_EARTH_VIEW.family) params.set('family', view.family);
  return params.toString();
}

export interface GlobeFocus {
  readonly longitude: number;
  readonly latitude: number;
  readonly zoom: number;
}

const WHOLE_GLOBE_SPAN_DEG = 120;
const SINGLE_WINDOW_ZOOM = 6;

/** A globe view that frames the given [lon, lat] points; one point gets a close zoom. */
export function focusOn(points: readonly (readonly [number, number])[]): GlobeFocus {
  if (points.length === 0) return { longitude: 0, latitude: 20, zoom: 1 };
  const lons = points.map((p) => p[0]);
  const lats = points.map((p) => p[1]);
  const span = Math.max(Math.max(...lons) - Math.min(...lons), Math.max(...lats) - Math.min(...lats));
  const zoom = span < 0.5 ? SINGLE_WINDOW_ZOOM : Math.min(SINGLE_WINDOW_ZOOM, Math.max(1, Math.log2(WHOLE_GLOBE_SPAN_DEG / span) + 1));
  return {
    longitude: (Math.max(...lons) + Math.min(...lons)) / 2,
    latitude: (Math.max(...lats) + Math.min(...lats)) / 2,
    zoom,
  };
}
