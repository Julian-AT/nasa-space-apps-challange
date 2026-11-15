// Adapted from ShadowAstra_Atlas (MIT, (c) 2026 Team ShadowAstra), https://github.com/ABRUBAB/ShadowAstra_Atlas
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
    <div className={cn('flex flex-col gap-3 pt-2 pb-2', className)}>
      {eyebrow === undefined ? null : (
        <p className="flex items-center gap-3 font-mono text-[11px] tracking-[0.22em] text-chrome uppercase">
          <span aria-hidden="true" className="h-px w-8 bg-chrome/50" />{eyebrow}</p>
      )}
      <h1 className="text-[clamp(36px,5vw,64px)] text-balance">{title}</h1>
      {lead === undefined ? null : <p className="max-w-3xl text-[17px] leading-relaxed text-muted-foreground">{lead}</p>}
    </div>
  );
}
