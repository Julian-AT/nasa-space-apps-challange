// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// A titled page section (PRD 8.6): a landmark region labelled by its h2, with consistent spacing.
import { useId, type ReactNode } from 'react';

import { cn } from '@/lib/utils';

export interface SectionProps {
  readonly title: string;
  readonly description?: ReactNode;
  readonly id?: string;
  readonly className?: string;
  readonly children?: ReactNode;
}

export function Section({ title, description, id, className, children }: SectionProps) {
  const generated = useId();
  const headingId = id ?? generated;
  return (
    <section aria-labelledby={headingId} className={cn('mt-16 flex flex-col gap-6 border-t pt-10 sm:mt-20', className)}>
      <div className="flex flex-col gap-2">
        <h2 id={headingId} className="text-2xl font-semibold tracking-tight">{title}</h2>
        {description === undefined ? null : <p className="max-w-3xl text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}
