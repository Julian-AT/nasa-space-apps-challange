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
import { Globe, type GlobePin } from '@/components/globe';
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
}

export interface HeroProps {
  readonly eyebrow: string;
  readonly pins: readonly GlobePin[];
  readonly cards: readonly HeroCard[];
  readonly bundleVersion: string;
}

export function Hero({ eyebrow, pins, cards, bundleVersion }: HeroProps) {
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
    <section data-full-bleed className="relative isolate min-h-[820px] overflow-hidden bg-background md:min-h-[680px] md:h-[calc(100dvh-7.5rem)] md:max-h-[860px]">
      <div className="absolute inset-0 -z-10">
        <Globe
          body="earth"
          variant="hero"
          pins={pins}
          selectedId={selectedId}
          onSelect={(id) => {
            const i = cards.findIndex((c) => c.windowId === id);
            if (i >= 0) {
              setIndex(i);
              setPaused(true);
            }
          }}
        />
      </div>
      <div className="pointer-events-none absolute inset-0 -z-0 bg-[linear-gradient(90deg,var(--background)_0%,color-mix(in_oklab,var(--background)_60%,transparent)_38%,transparent_62%)] max-md:bg-[linear-gradient(180deg,transparent_0%,transparent_34%,var(--background)_52%)]" />
      <div className="pointer-events-none relative z-10 mx-auto flex h-full min-h-[inherit] max-w-[1400px] flex-col justify-end gap-8 px-4 pt-[360px] pb-10 sm:px-6 md:justify-center md:pt-0 md:pb-0">
        <div className="pointer-events-auto max-w-[760px]">
          <Badge variant="outline" className="mb-6 bg-background/60 backdrop-blur">
            {eyebrow}
          </Badge>
          <h1 className="text-[clamp(40px,min(6vw,8.2vh),84px)] leading-[1] font-semibold tracking-tight text-balance text-foreground">
            <span className="block">Find the Moon and Mars</span>
            <span className="block text-primary">on Earth.</span>
          </h1>
          <p className="mt-6 max-w-[580px] text-base leading-relaxed text-muted-foreground sm:text-lg">
            <span className="font-medium text-foreground">Our own physics model, measured live:</span> Terraluna computes a relief and texture
            fingerprint of NASA and ESA elevation models, in your browser for any Earth point, to find terrain that resembles lunar
            and Martian targets. Terrain only (Mode C): the radar comparison was not completed.
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
              <Button asChild size="lg" variant="outline" className="bg-background/60 backdrop-blur">
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
        {card === undefined ? null : (
          <div className="pointer-events-auto w-full max-w-[380px] md:absolute md:right-6 md:bottom-8">
          <aside aria-label="Featured evidence card">
          <Card className="w-full bg-card/90 backdrop-blur">
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
            <CardFooter className="flex flex-col items-stretch gap-3">
            <div className="flex items-center justify-between gap-3 text-sm">
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
            <p className="font-mono text-[10px] tracking-wide text-muted-foreground">BUNDLE {bundleVersion}</p>
            </CardFooter>
          </Card>
          </aside>
          </div>
        )}
      </div>
    </section>
  );
}
