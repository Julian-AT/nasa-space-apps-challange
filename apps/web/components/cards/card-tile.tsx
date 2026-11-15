// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// One tile of the card atlas: the image pair, the headline, the two terrain numbers from the card
// and honest Unavailable chips for radar and model. The whole tile is the link.
import type { CardRecord } from '@terraluna/schema';
import Link from 'next/link';

import { EvidenceLabel, isEvidenceLabel } from '@/components/evidence-label';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';

import { CardPair } from './card-pair.tsx';
import type { CardVisual } from './card-visual.ts';

const show = (v: number): string => String(Number(v.toPrecision(4)));

function Stat({ name, d }: { readonly name: string; readonly d: CardRecord['distances_by_family']['terrain_relief'] }) {
  const has = d.status === 'available' && d.value !== null;
  return (
    <div className="min-w-0 rounded-md bg-muted p-3" data-family-stat={name}>
      <p className="text-xs font-medium text-muted-foreground">{name}</p>
      {has ? (
        <>
          <p className="mt-1 font-mono text-2xl leading-none font-semibold tabular-nums">
            {show(d.value ?? 0)} <span className="text-xs text-muted-foreground">{d.unit}</span>
          </p>
          <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            {isEvidenceLabel(d.evidence_label) ? <EvidenceLabel label={d.evidence_label} /> : null}
            {d.background_percentile === null ? null : <span>percentile {show(d.background_percentile)}</span>}
          </p>
        </>
      ) : (
        <p className="mt-1 text-sm text-muted-foreground">Not measured</p>
      )}
    </div>
  );
}

export function CardTile({ card, visual, index }: { readonly card: CardRecord; readonly visual: CardVisual; readonly index: number }) {
  const d = card.distances_by_family;
  return (
    <Link href={`/site/${card.card_id}`} data-slot="card-tile" className="group block h-full rounded-xl no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <Card className="h-full gap-4 text-sm transition-shadow group-hover:ring-primary/60 motion-reduce:transition-none">
        <CardContent>
          <CardPair visual={visual} />
        </CardContent>
        <CardHeader className="gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary" className="font-mono">Card {String(index + 1).padStart(2, '0')}</Badge>
            {card.mock ? <Badge variant="destructive">MOCK</Badge> : null}
          </div>
          <CardTitle className="text-xl leading-tight font-semibold text-balance">{visual.headline}</CardTitle>
          <CardDescription>{card.wording}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <Stat name="Terrain relief" d={d.terrain_relief} />
          <Stat name="Terrain texture" d={d.terrain_texture} />
        </CardContent>
      </Card>
    </Link>
  );
}
