// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
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
    <div className={cn('flex flex-col gap-3 pt-8 pb-4 sm:pt-12', className)}>
      {eyebrow === undefined ? null : <p className="font-mono text-[11px] tracking-[0.2em] text-primary uppercase">{eyebrow}</p>}
      <h1 className="text-4xl font-medium tracking-[-0.03em] text-balance sm:text-5xl">{title}</h1>
      {lead === undefined ? null : <p className="max-w-3xl text-base leading-relaxed text-muted-foreground sm:text-lg">{lead}</p>}
    </div>
  );
}
