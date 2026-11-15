// The left results panel of the explorer layout (PRD 8.6): a rounded panel with a mono-caps
// eyebrow, a title carrying the real count from the bundle, chips, and the content. The layout
// follows the Discover view of ShadowAstra Atlas (MIT); no code is copied from it.
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

export function Chip({ children, className }: { readonly children: ReactNode; readonly className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border border-border bg-muted/40 px-2.5 py-0.5 text-xs text-foreground',
        className,
      )}
    >
      {children}
    </span>
  );
}

export interface ResultsPanelProps {
  readonly title: ReactNode;
  readonly chips?: readonly ReactNode[];
  readonly children: ReactNode;
  readonly label: string;
}

export function ResultsPanel({ title, chips = [], children, label }: ResultsPanelProps) {
  return (
    <aside
      aria-label={label}
      className="flex min-w-0 flex-col gap-4 overflow-y-auto rounded-2xl border border-border bg-card/95 p-4 text-card-foreground shadow-lg lg:max-h-[calc(100dvh-9rem)]"
    >
      <header className="flex flex-col gap-2">
        <p className="font-mono text-[0.7rem] font-semibold tracking-[0.22em] text-primary uppercase">Results</p>
        <h2 className="text-2xl font-semibold tracking-tight">{title}</h2>
        {chips.length === 0 ? null : <div className="flex flex-wrap gap-2">{chips}</div>}
      </header>
      {children}
    </aside>
  );
}
