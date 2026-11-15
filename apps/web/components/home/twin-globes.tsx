'use client';
// Hero scene: the selected Moon or Mars target on the left, its nearest Earth window on the right.
// Both globes are draggable (OrbitControls) and load three.js lazily through Globe's next/dynamic.
// The pair shown is the bundle card with the smallest terrain relief distance for that body.
import Link from 'next/link';
import { useMemo, useState } from 'react';

import { Globe, type GlobePin } from '@/components/globe';
import { useReducedMotion } from '@/components/globe/use-reduced-motion';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';

import type { HeroCard } from './hero.tsx';

type Body = 'Moon' | 'Mars';

function best(cards: readonly HeroCard[], body: Body): HeroCard | undefined {
  let pick: HeroCard | undefined;
  for (const c of cards) {
    if (c.body !== body || c.reliefValue === null) continue;
    if (pick === undefined || (pick.reliefValue ?? Infinity) > c.reliefValue) pick = c;
  }
  return pick;
}

export function TwinGlobes({ cards }: { readonly cards: readonly HeroCard[] }) {
  const reduced = useReducedMotion();
  const bodies = (['Moon', 'Mars'] as const).filter((b) => best(cards, b) !== undefined);
  const [body, setBody] = useState<Body>(bodies[0] ?? 'Moon');
  const card = best(cards, body);
  const targetPins = useMemo<GlobePin[]>(
    () =>
      card?.targetPin == null
        ? []
        : [{ id: card.cardId, lat: card.targetPin.lat, lon: card.targetPin.lon, label: card.target, href: `/site/${encodeURIComponent(card.cardId)}`, tone: body === 'Moon' ? 'moon' : 'mars' }],
    [card, body],
  );
  const earthPins = useMemo<GlobePin[]>(
    () =>
      card?.earthPin == null
        ? []
        : [{ id: card.windowId, lat: card.earthPin.lat, lon: card.earthPin.lon, label: `${card.region} window`, href: `/site/${encodeURIComponent(card.cardId)}`, tone: 'earth' }],
    [card],
  );
  if (card === undefined) return null;
  return (
    <Card className="gap-4 p-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <ToggleGroup
          type="single"
          variant="outline"
          value={body}
          onValueChange={(v) => {
            if (v === 'Moon' || v === 'Mars') setBody(v);
          }}
          aria-label="Target body"
        >
          {bodies.map((b) => (
            <ToggleGroupItem key={b} value={b} aria-label={b}>
              {b}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <span className="text-xs text-muted-foreground">Drag to rotate</span>
      </div>
      <div className="relative grid grid-cols-2 items-center gap-4">
        <div className="aspect-square w-full">
          <Globe key={body} body={body === 'Moon' ? 'moon' : 'mars'} pins={targetPins} selectedId={card.cardId} autoRotate={!reduced} />
        </div>
        <div className="aspect-square w-full">
          <Globe body="earth" pins={earthPins} selectedId={card.windowId} autoRotate={!reduced} />
        </div>
        <div aria-hidden="true" className="pointer-events-none absolute inset-x-[30%] top-1/2 h-px -translate-y-1/2 bg-gradient-to-r from-transparent via-primary to-transparent" />
        {card.relief.label === 'Unavailable' ? null : (
          <Badge className="pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-[calc(50%+18px)] tabular-nums">
            relief {card.relief.value} {card.relief.unit}
          </Badge>
        )}
      </div>
      <p className="text-sm text-balance text-muted-foreground">
        <Link href={`/site/${encodeURIComponent(card.cardId)}`}>{card.target}</Link> and its nearest Earth terrain in {card.region}.
      </p>
    </Card>
  );
}
