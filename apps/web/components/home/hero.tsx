// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
'use client';
// The home hero (PRD 3, 8.6): headline left, the textured Earth globe right with a pin for every
// Earth window in the bundle, and a floating card that cycles through the evidence cards. All
// values arrive as props built from the bundle on the server; nothing scientific is typed here.
import Link from 'next/link';
import { useEffect, useState } from 'react';

import { ArrowRight01Icon } from '@hugeicons/core-free-icons';
import { HugeiconsIcon } from '@hugeicons/react';

import { EvidenceLabel, type EvidenceLabelName } from '@/components/evidence-label';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card';
import { type GlobePin } from '@/components/globe';
import { TwinGlobes } from './twin-globes.tsx';
import { useReducedMotion } from '@/components/globe/use-reduced-motion';

export interface HeroCard {
  readonly cardId: string;
  readonly windowId: string;
  readonly region: string;
  readonly target: string;
  readonly body: 'Moon' | 'Mars';
  readonly relief:
    | { readonly label: Exclude<EvidenceLabelName, 'Unavailable'>; readonly value: string; readonly unit: string }
    | { readonly label: 'Unavailable'; readonly reason: string };
  readonly wording: string;
  readonly reliefValue: number | null;
  readonly targetPin: { readonly lat: number; readonly lon: number } | null;
  readonly earthPin: { readonly lat: number; readonly lon: number } | null;
}

export interface HeroProps {
  readonly eyebrow: string;
  readonly pins: readonly GlobePin[];
  readonly cards: readonly HeroCard[];
  readonly bundleVersion: string;
}

export function Hero({ eyebrow, cards }: HeroProps) {
  const [index, setIndex] = useState(0);
  const reduced = useReducedMotion();
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (reduced || paused || cards.length < 2) return;
    const t = setInterval(() => setIndex((n) => (n + 1) % cards.length), 5000);
    return () => clearInterval(t);
  }, [reduced, paused, cards.length]);
  const card = cards[index];
  const selectedId = card?.windowId ?? null;

  return (
    <section className="pt-10 pb-6 sm:pt-14">
      <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:gap-12">
        <div className="max-w-[760px] lg:pt-6">
          <Badge variant="outline" className="mb-6">
            {eyebrow}
          </Badge>
          <h1 className="text-[clamp(38px,5vw,64px)] leading-[1.05] font-semibold tracking-tight text-balance text-foreground">
            <span className="block">Find the Moon and Mars</span>
            <span className="block text-primary">on Earth.</span>
          </h1>
          <p className="mt-6 max-w-[580px] text-base leading-relaxed text-muted-foreground sm:text-lg">
            <span className="font-medium text-foreground">Our own physics model, measured live:</span> Terraluna computes a relief and texture
            fingerprint of NASA and ESA elevation models, in your browser for any Earth point, to find terrain that resembles lunar
            and Martian targets.
          </p>
          <ul className="mt-8 flex flex-wrap gap-3">
            <li>
              <Button asChild size="lg">
                <Link href="/find" className="no-underline">
                  Measure any point live
                  <HugeiconsIcon icon={ArrowRight01Icon} strokeWidth={2} />
                </Link>
              </Button>
            </li>
            <li>
              <Button asChild size="lg" variant="outline">
                <Link href="/site" className="no-underline">
                  Browse evidence cards
                </Link>
              </Button>
            </li>
            <li>
              <Button asChild size="lg" variant="ghost">
                <Link href="/explore/earth" className="no-underline">
                  Explore the map
                </Link>
              </Button>
            </li>
          </ul>
        </div>
        <div className="flex flex-col gap-4">
        <TwinGlobes cards={cards} />
        {card === undefined ? null : (
          <div className="w-full">
          <aside aria-label="Featured evidence card">
          <Card className="w-full">
            <CardHeader>
            <p className="flex items-center justify-between gap-2 text-xs font-medium text-muted-foreground">
              <span>Candidate window</span>
              <span className="font-mono">{String(index + 1).padStart(2, '0')} / {String(cards.length).padStart(2, '0')}</span>
            </p>
            <p className="text-lg leading-snug font-semibold tracking-tight">
              {card.region} <span className="font-normal text-muted-foreground">practises for</span>{' '}
              <span className="text-primary">{card.target}</span>
              <span className="font-normal text-muted-foreground"> ({card.body})</span>
            </p>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">Terrain relief distance</span>
              {card.relief.label === 'Unavailable' ? (
                <span>{card.relief.reason}</span>
              ) : (
                <span className="font-mono">
                  {card.relief.value} <span className="text-muted-foreground">{card.relief.unit}</span>
                </span>
              )}
              <EvidenceLabel label={card.relief.label} />
            </div>
            <p className="text-xs text-muted-foreground">{card.wording}</p>
            </CardContent>
            <CardFooter>
            <div className="flex w-full items-center justify-between gap-3 text-sm">
              <Link href={`/site/${encodeURIComponent(card.cardId)}`} className="font-medium text-primary no-underline hover:underline">
                Open evidence card {'→'}
              </Link>
              {cards.length > 1 ? (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setPaused(false);
                    setIndex((n) => (n + 1) % cards.length);
                  }}
                >
                  Next card
                </Button>
              ) : null}
            </div>
            </CardFooter>
          </Card>
          </aside>
          </div>
        )}
        </div>
      </div>
    </section>
  );
}
