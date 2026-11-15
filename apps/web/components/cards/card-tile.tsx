// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// One tile of the card atlas: the image pair, the headline and the two terrain distances from the
// card with their background position, plus the evidence label they carry. The tile is the link.
import type { CardRecord } from '@terraluna/schema';
import Link from 'next/link';

import { EvidenceLabel, isEvidenceLabel } from '@/components/evidence-label';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

import { Metric } from './card-metric.tsx';
import { CardPair } from './card-pair.tsx';
import type { CardVisual } from './card-visual.ts';

export function CardTile({ card, visual }: { readonly card: CardRecord; readonly visual: CardVisual }) {
  const d = card.distances_by_family;
  const label = d.terrain_relief.evidence_label;
  return (
    <Link
      href={`/site/${card.card_id}`}
      data-slot="card-tile"
      className="group block h-full rounded-xl no-underline outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
    >
      <Card className="h-full gap-5 py-4 transition-[box-shadow,background-color] group-hover:bg-muted/30 group-hover:ring-2 group-hover:ring-primary/40 motion-reduce:transition-none">
        <CardContent className="px-4">
          <CardPair visual={visual} />
        </CardContent>
        <CardHeader className="gap-1.5 px-4">
          <div className="flex items-start justify-between gap-3">
            <CardTitle className="text-lg leading-snug font-semibold text-balance">{visual.headline}</CardTitle>
            {card.mock ? <Badge variant="destructive">Mock</Badge> : null}
          </div>
          <p className="text-sm text-muted-foreground first-letter:uppercase">{card.wording}</p>
        </CardHeader>
        <CardContent className="mt-auto grid grid-cols-2 gap-5 border-t px-4 pt-4">
          <Metric name="Relief" d={d.terrain_relief} />
          <Metric name="Texture" d={d.terrain_texture} />
        </CardContent>
        <CardContent className="flex items-center justify-between gap-3 px-4 text-xs text-muted-foreground">
          {isEvidenceLabel(label) ? <EvidenceLabel label={label} /> : <span />}
          <span className="font-medium text-foreground/80 transition-colors group-hover:text-primary">Open card →</span>
        </CardContent>
      </Card>
    </Link>
  );
}
