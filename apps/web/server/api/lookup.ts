// Lookups over the validated bundle records (PulledBundle.records). Nothing here computes a value:
// a window or comparison that is not stored is absent (null or undefined), never a default.
import type { ComparisonRecord, Windows } from '@terraluna/schema';

import type { PulledBundle } from '../../lib/bundle.ts';

export type WindowEntryRecord = Windows[number];

export interface FoundWindow {
  /** The body of the windows/<body>.json file that holds the entry. */
  readonly body: string;
  readonly entry: WindowEntryRecord;
}

/** The window with this id, from the first windows/<body>.json that holds it, or null. */
export function findWindow(bundle: PulledBundle, id: string): FoundWindow | null {
  for (const [body, entries] of bundle.records.windows) {
    const entry = entries.find((candidate) => candidate.window.window_id === id);
    if (entry !== undefined) return { body, entry };
  }
  return null;
}

/** The stored comparison of a pair of windows, in either order, or null. */
export function findComparison(bundle: PulledBundle, a: string, b: string): ComparisonRecord | null {
  for (const records of bundle.records.comparisons.values()) {
    const hit = records.find(
      (r) =>
        (r.target_window_id === a && r.earth_window_id === b) ||
        (r.target_window_id === b && r.earth_window_id === a),
    );
    if (hit !== undefined) return hit;
  }
  return null;
}
