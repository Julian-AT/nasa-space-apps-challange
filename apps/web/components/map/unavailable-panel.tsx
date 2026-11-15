// An Unavailable panel (PRD 3, 10): shown wherever a record, file or layer the view needs is not
// in the bundle. It names what is missing and why, carries the Unavailable evidence label, and
// stands in place of an empty map, a zero or a placeholder image.
import type { ReactNode } from 'react';

import { EvidenceLabel } from '@/components/evidence-label';
import { cn } from '@/lib/utils';

export interface UnavailablePanelProps {
  readonly title: string;
  /** Why the evidence is absent. */
  readonly reason: ReactNode;
  /** The bundle file or record that was looked for, e.g. "targets.json". */
  readonly missing?: string | undefined;
  readonly className?: string;
  readonly children?: ReactNode;
}

export function UnavailablePanel({ title, reason, missing, className, children }: UnavailablePanelProps) {
  return (
    <div
      data-slot="unavailable-panel"
      className={cn(
        'flex flex-col gap-2 rounded-lg border border-dashed border-border bg-card/60 p-4 text-card-foreground',
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <EvidenceLabel label="Unavailable" />
        <h3 className="text-base font-semibold">{title}</h3>
      </div>
      <p data-slot="reason" className="text-sm text-muted-foreground">
        {reason}
      </p>
      {missing === undefined ? null : (
        <p className="text-sm">
          Missing from this bundle: <code className="rounded bg-muted px-1 py-0.5 text-xs">{missing}</code>
        </p>
      )}
      {children}
    </div>
  );
}
