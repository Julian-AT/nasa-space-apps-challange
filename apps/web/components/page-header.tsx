// The top of a page (PRD 8.6): an eyebrow, the page's one h1 and a lead paragraph.
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

export interface PageHeaderProps {
  readonly title: string;
  readonly eyebrow?: string;
  readonly lead?: ReactNode;
  readonly className?: string;
}

export function PageHeader({ title, eyebrow, lead, className }: PageHeaderProps) {
  return (
    <header className={cn('flex flex-col gap-3 pt-2 pb-2', className)}>
      {eyebrow === undefined ? null : (
        <p className="text-xs font-semibold tracking-[0.18em] text-primary uppercase">{eyebrow}</p>
      )}
      <h1 className="text-balance sm:text-4xl">{title}</h1>
      {lead === undefined ? null : <p className="max-w-3xl text-lg text-muted-foreground">{lead}</p>}
    </header>
  );
}
