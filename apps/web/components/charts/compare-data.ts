// Lookup of a compared pair in the loaded bundle. Nothing is computed: a pair is shown only when
// comparisons/<target_id>.json holds a ComparisonRecord for it (PRD 8.4, 8.6).
import type { ComparisonRecord, Windows } from '@terraluna/schema';

export type WindowEntry = Windows[number];

export interface PairRecords {
  readonly comparisons: ReadonlyMap<string, readonly ComparisonRecord[]>;
  readonly windows: ReadonlyMap<string, readonly WindowEntry[]>;
}

export interface Pair {
  readonly a: string;
  readonly b: string;
}

export function pairsInBundle(records: PairRecords): Pair[] {
  const pairs: Pair[] = [];
  for (const list of records.comparisons.values()) {
    for (const record of list) pairs.push({ a: record.target_window_id, b: record.earth_window_id });
  }
  return pairs;
}

export function findComparison(records: PairRecords, a: string, b: string): ComparisonRecord | null {
  for (const list of records.comparisons.get(a) ?? []) {
    if (list.target_window_id === a && list.earth_window_id === b) return list;
  }
  for (const list of records.comparisons.values()) {
    for (const record of list) {
      if (record.target_window_id === a && record.earth_window_id === b) return record;
    }
  }
  return null;
}

export function findWindow(records: PairRecords, id: string): WindowEntry | null {
  for (const list of records.windows.values()) {
    const found = list.find((entry) => entry.window.window_id === id);
    if (found !== undefined) return found;
  }
  return null;
}
