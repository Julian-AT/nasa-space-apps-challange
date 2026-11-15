// Terrain distances and background percentiles (PRD 6.5 step 3, CONTRACTS a.4): the TypeScript
// mirror of terraluna_pipeline.matching.terrain_distance and background_percentile. Terrain
// distance is RMS of log nu and is kept apart from radar and model distances (PRD 6.5, 8.11);
// no epsilon is added anywhere and Unavailable is never a number.
//
// The Python reference is being written in t4-logic (T4L-03); parity against
// fixtures/parity/matching_synthetic.json is added when that fixture lands (T10-08).

export type MatchingReason = 'no_shared_lags' | 'nu_zero' | 'nu_missing' | 'background_empty' | 'family_mismatch';
export const MATCHING_REASONS: readonly MatchingReason[] = Object.freeze([
  'no_shared_lags',
  'nu_zero',
  'nu_missing',
  'background_empty',
  'family_mismatch',
]);

export type Outcome =
  | { status: 'available'; value: number; reason: null; lags_m: number[] }
  | { status: 'unavailable'; value: null; reason: MatchingReason; lags_m: number[] };

/** The terrain variant a distance reads: supported lags and nu keyed by lag in metres (T3 keys). */
export interface DistanceVariant {
  readonly supported_lags_m: readonly number[];
  readonly nu_m: Readonly<Record<string, number | null>>;
}

/** The supported lags present in both lists, ascending (matching.shared_lags). */
export function sharedLags(a: readonly number[], b: readonly number[]): number[] {
  const bs = new Set(b);
  return [...new Set(a)].filter((lag) => bs.has(lag)).sort((x, y) => x - y);
}

function nuAt(variant: DistanceVariant, lag: number): number | null {
  const matches = Object.entries(variant.nu_m).filter(([key]) => Number.parseFloat(key) === lag);
  if (matches.length > 1) throw new RangeError(`nu_m has ${String(matches.length)} keys for lag ${String(lag)}`);
  return matches[0]?.[1] ?? null;
}

function unavailable(reason: MatchingReason, lags: number[] = []): Outcome {
  return { status: 'unavailable', value: null, reason, lags_m: lags };
}

/**
 * matching.terrain_distance: sqrt(sum((ln nu_a - ln nu_b)^2) / n) over the shared supported lags,
 * summed left to right in ascending lag order. Unit "RMS of log nu". H is never read.
 */
export function terrainDistance(a: DistanceVariant, b: DistanceVariant): Outcome {
  const lags = sharedLags(a.supported_lags_m, b.supported_lags_m);
  if (lags.length === 0) return unavailable('no_shared_lags');
  let total = 0;
  for (const lag of lags) {
    const nuA = nuAt(a, lag);
    const nuB = nuAt(b, lag);
    if (nuA === null || nuB === null) return unavailable('nu_missing', lags);
    if (!(Number.isFinite(nuA) && Number.isFinite(nuB)) || nuA <= 0 || nuB <= 0) return unavailable('nu_zero', lags);
    const difference = Math.log(nuA) - Math.log(nuB);
    total += difference * difference;
  }
  return { status: 'available', value: Math.sqrt(total / lags.length), reason: null, lags_m: lags };
}

export type Family = 'terrain_relief' | 'terrain_texture' | 'radar_cpr' | 'model_embedding';

export interface BackgroundHistogram {
  readonly bin_edges: readonly number[];
  readonly counts: readonly number[];
  readonly percentile_definition: 'linear_within_bin_v1';
  readonly direction: 'smaller_is_closer';
}

/**
 * matching.background_percentile (linear_within_bin_v1, smaller_is_closer):
 * p = 100 (sum(counts[:k]) + counts[k] (d - lo_k) / (hi_k - lo_k)) / n for the bin [lo_k, hi_k)
 * holding d; 0 below the first edge and 100 at or above the last.
 */
export function backgroundPercentile(d: number, binEdges: readonly number[], counts: readonly number[]): Outcome {
  if (binEdges.length < 2) throw new RangeError('bin edges need at least two values');
  if (!binEdges.every((e) => Number.isFinite(e))) throw new RangeError('bin edges must be finite');
  for (let i = 1; i < binEdges.length; i += 1) {
    if ((binEdges[i] as number) <= (binEdges[i - 1] as number)) throw new RangeError('bin edges must be strictly ascending');
  }
  if (counts.length !== binEdges.length - 1) throw new RangeError('one count per bin');
  if (counts.some((c) => !Number.isInteger(c) || c < 0)) throw new RangeError('counts must be non-negative integers');
  if (!Number.isFinite(d)) throw new RangeError('the distance must be finite');
  const n = counts.reduce((s, c) => s + c, 0);
  if (n === 0) return unavailable('background_empty');
  if (d < (binEdges[0] as number)) return { status: 'available', value: 0, reason: null, lags_m: [] };
  if (d >= (binEdges[binEdges.length - 1] as number)) return { status: 'available', value: 100, reason: null, lags_m: [] };
  // bisect_right(edges, d) - 1
  let k = 0;
  while (k + 1 < binEdges.length && (binEdges[k + 1] as number) <= d) k += 1;
  const low = binEdges[k] as number;
  const high = binEdges[k + 1] as number;
  let below = 0;
  for (let i = 0; i < k; i += 1) below += counts[i] as number;
  return { status: 'available', value: (100 * (below + ((counts[k] as number) * (d - low)) / (high - low))) / n, reason: null, lags_m: [] };
}

/** The percentile against a histogram of the same family only; another family is Unavailable. */
export function familyPercentile(d: number, distanceFamily: Family, background: BackgroundHistogram & { family: Family }): Outcome {
  if (background.family !== distanceFamily) return unavailable('family_mismatch');
  return backgroundPercentile(d, background.bin_edges, background.counts);
}
