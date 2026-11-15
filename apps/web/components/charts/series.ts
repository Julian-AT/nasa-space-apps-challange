// Pure series building for the nu(L) curves on /compare (PRD 6.3, 8.6, 10). The plotted points are
// the entries of a stored TerrainVariant.nu_m, nothing resampled or smoothed. A lag that is
// unsupported, or whose stored value is null, is a gap carrying the record's reason: it is never
// drawn as zero and never interpolated across (the line breaks at it).
import type { FingerprintRecord } from '@terraluna/schema';

type TerrainFingerprint = NonNullable<FingerprintRecord['terrain']>;
export type TerrainVariant = TerrainFingerprint['relief'];
export type Observable = 'relief' | 'texture';

export const NU_NULL_REASON = 'nu_null_in_record';

export interface SeriesPoint {
  readonly lag_m: number;
  readonly nu: number;
  /** Index of the unbroken run of lags this point belongs to; a gap starts a new run. */
  readonly segment: number;
}

export interface SeriesGap {
  readonly lag_m: number;
  readonly reason: string;
}

export interface NuSeries {
  readonly points: readonly SeriesPoint[];
  readonly gaps: readonly SeriesGap[];
}

/** Both axes of the nu(L) chart are logarithmic (PRD 6.3). */
export const NU_AXES = { x: 'log', y: 'log' } as const;

export function buildNuSeries(variant: TerrainVariant): NuSeries {
  const entries: { lag_m: number; nu: number | null; reason: string | null }[] = [];
  for (const [key, nu] of Object.entries(variant.nu_m)) {
    entries.push({ lag_m: Number(key), nu, reason: nu === null ? NU_NULL_REASON : null });
  }
  for (const lag of variant.unsupported_lags) {
    // An unsupported lag is a gap even if nu_m also carries a number for it.
    const at = entries.findIndex((entry) => entry.lag_m === lag.lag_m);
    const gap = { lag_m: lag.lag_m, nu: null, reason: lag.reason };
    if (at === -1) entries.push(gap);
    else entries[at] = gap;
  }
  entries.sort((a, b) => a.lag_m - b.lag_m);
  const points: SeriesPoint[] = [];
  const gaps: SeriesGap[] = [];
  let segment = 0;
  let broken = false;
  for (const entry of entries) {
    if (entry.nu === null) {
      gaps.push({ lag_m: entry.lag_m, reason: entry.reason ?? NU_NULL_REASON });
      broken = true;
    } else {
      if (broken) segment += 1;
      broken = false;
      points.push({ lag_m: entry.lag_m, nu: entry.nu, segment });
    }
  }
  return { points, gaps };
}

/** A log axis cannot place zero: such points stay in the table and are named, not drawn. */
export function plottableOnLog(points: readonly SeriesPoint[]): SeriesPoint[] {
  return points.filter((point) => point.nu > 0 && point.lag_m > 0);
}

export function offLogAxis(points: readonly SeriesPoint[]): SeriesPoint[] {
  return points.filter((point) => !(point.nu > 0 && point.lag_m > 0));
}

export interface NamedSeries {
  readonly id: string;
  /** Short label, e.g. "Target" or "Earth candidate". */
  readonly role: string;
  readonly series: NuSeries;
  /** Evidence label of the fingerprint the series comes from. */
  readonly evidence: string;
}

/** The terrain variant of a fingerprint, or null when it has no terrain block. */
export function variantOf(record: FingerprintRecord | null, observable: Observable): TerrainVariant | null {
  if (record?.terrain == null) return null;
  return record.terrain[observable];
}
