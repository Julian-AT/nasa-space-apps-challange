// An honest "not available" panel: names what is missing and where it would come from. No value,
// no placeholder content.
import type { ReactNode } from 'react';

import { EvidenceLabel } from '@/components/evidence-label';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export function UnavailablePanel({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  return (
    <Card data-slot="unavailable-panel" role="status" className="border-dashed">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-3">
          <EvidenceLabel label="Unavailable" />
          <span>{title}</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-muted-foreground">{children}</CardContent>
    </Card>
  );
}
