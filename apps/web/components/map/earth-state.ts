// Earth view state in the query string: `family` (one distance family at a time) and `target`.
// An invalid value falls back to the default; parsing never throws.
import { FAMILIES, type Family } from './explore-data.ts';

export interface EarthView {
  readonly family: Family;
  readonly target: string | null;
}

export const DEFAULT_EARTH_VIEW: EarthView = { family: 'radar_cpr', target: null };

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
