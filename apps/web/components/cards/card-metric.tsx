// One terrain distance from a card record as a big number with its unit and how it sits in the
// background distribution. background_percentile follows CONTRACTS a.4 (linear_within_bin_v1,
// smaller_is_closer): p is the share of background windows whose distance is smaller. The pair is
// therefore closer than 100 - p percent of them; that share is floored so it never overstates.
import type { CardRecord } from '@terraluna/schema';

import { EvidenceLabel, isEvidenceLabel } from '@/components/evidence-label';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';

export type Distance = CardRecord['distances_by_family']['terrain_relief'];

/** Display rounding only; the bundle value is unchanged. */
export const sig = (v: number, digits = 3): string => String(Number(v.toPrecision(digits)));

/** Whole percent of background windows this pair is closer than, or null without a percentile. */
export function closerThan(d: Distance): number | null {
  return d.background_percentile === null ? null : Math.floor(100 - d.background_percentile);
}

export function hasValue(d: Distance): d is Distance & { value: number } {
  return d.status === 'available' && d.value !== null;
}

export function Metric({ name, d, size = 'md', showLabel = false }: { readonly name: string; readonly d: Distance; readonly size?: 'md' | 'lg'; readonly showLabel?: boolean }) {
  const n = closerThan(d);
  return (
    <div className="flex min-w-0 flex-col gap-2" data-family-stat={name}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium text-muted-foreground">{name}</p>
        {showLabel && isEvidenceLabel(d.evidence_label) ? <EvidenceLabel label={d.evidence_label} /> : null}
      </div>
      {hasValue(d) ? (
        <>
          <p className="flex items-baseline gap-1.5">
            <span data-slot="value" className={cn('font-semibold tracking-tight tabular-nums', size === 'lg' ? 'text-4xl' : 'text-2xl')}>
              {sig(d.value, size === 'lg' ? 4 : 3)}
            </span>
            <span data-slot="unit" className="text-xs text-muted-foreground">{d.unit}</span>
          </p>
          {n === null ? (
            <p className="text-xs text-muted-foreground">No background percentile</p>
          ) : (
            <div className="flex flex-col gap-1.5">
              <Progress value={n} aria-label={`${name}: closer than ${String(n)}% of background windows`} className="h-1.5" />
              <p className="text-xs text-muted-foreground">
                Closer than <span className="font-medium text-foreground tabular-nums">{n}%</span> of background windows
              </p>
            </div>
          )}
        </>
      ) : (
        <p className="text-sm text-muted-foreground">Not measured</p>
      )}
    </div>
  );
}
