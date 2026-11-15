// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
'use client';
// The home hero (PRD 3, 8.6): headline left, the textured Earth globe right with a pin for every
// Earth window in the bundle, and a floating card that cycles through the evidence cards. All
// values arrive as props built from the bundle on the server; nothing scientific is typed here.
import Link from 'next/link';
import { useEffect, useState } from 'react';

import { EvidenceLabel, type EvidenceLabelName } from '@/components/evidence-label';
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
    <section data-full-bleed className="relative isolate min-h-[820px] overflow-hidden bg-void md:min-h-[680px] md:h-[calc(100dvh-7.5rem)] md:max-h-[860px]">
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
      <div className="pointer-events-none absolute inset-0 -z-0 bg-[linear-gradient(90deg,rgba(7,8,10,0.92)_0%,rgba(7,8,10,0.55)_38%,transparent_62%)] max-md:bg-[linear-gradient(180deg,transparent_0%,transparent_34%,rgba(7,8,10,0.92)_52%)]" />
      <div className="pointer-events-none relative z-10 mx-auto flex h-full min-h-[inherit] max-w-[1400px] flex-col justify-end gap-8 px-4 pt-[360px] pb-10 sm:px-6 md:justify-center md:pt-0 md:pb-0">
        <div className="pointer-events-auto max-w-[760px]">
          <p className="mb-6 flex items-center gap-3 font-mono text-[11px] tracking-[0.22em] text-chrome uppercase">
            <span aria-hidden="true" className="h-px w-8 shrink-0 bg-chrome/50" />
            {eyebrow}
          </p>
          <h1 className="text-[clamp(40px,min(6.4vw,8.6vh),96px)] leading-[0.95] font-medium tracking-[-0.045em] text-balance text-platinum">
            <span className="block">Find the Moon and Mars</span>
            <span className="block font-serif font-normal tracking-[-0.02em] text-flare italic">on Earth.</span>
          </h1>
          <p className="mt-6 max-w-[560px] text-[17px] leading-relaxed text-chrome">
            Terraluna compares the relief and texture fingerprints of NASA and ESA elevation models to find Earth terrain that
            resembles lunar and Martian targets. It is a terrain-only pilot (Mode C): the radar comparison was not completed.
          </p>
          <ul className="mt-8 flex flex-wrap gap-3">
            <li>
              <Link href="/site" className="btn-primary inline-flex h-11 items-center rounded-full px-6 text-sm font-semibold no-underline">
                <span className="relative z-10">Browse evidence cards</span>
              </Link>
            </li>
            <li>
              <Link
                href="/probe"
                className="inline-flex h-11 items-center rounded-full border border-border px-6 text-sm font-medium no-underline transition-colors hover:bg-platinum/5"
              >
                Probe any point
              </Link>
            </li>
            <li>
              <Link
                href="/explore/earth"
                className="inline-flex h-11 items-center rounded-full border border-border px-6 text-sm font-medium no-underline transition-colors hover:bg-platinum/5"
              >
                Explore the map
              </Link>
            </li>
          </ul>
        </div>
        {card === undefined ? null : (
          <div className="pointer-events-auto w-full max-w-[380px] md:absolute md:right-6 md:bottom-8">
          <aside aria-label="Featured evidence card" className="surface w-full rounded-3xl p-5">
            <p className="flex items-center justify-between gap-2 font-mono text-[11px] tracking-[0.18em] text-chrome uppercase">
              <span>Candidate window</span>
              <span>{String(index + 1).padStart(2, '0')} / {String(cards.length).padStart(2, '0')}</span>
            </p>
            <p className="mt-3 text-lg leading-snug font-medium tracking-tight">
              {card.region} <span className="text-chrome">practises for</span>{' '}
              <span className="font-serif font-normal text-flare italic">{card.target}</span>
              <span className="text-chrome"> ({card.body})</span>
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
              <span className="text-chrome">Terrain relief distance</span>
              {card.relief.label === 'Unavailable' ? (
                <span>{card.relief.reason}</span>
              ) : (
                <span className="font-mono">
                  {card.relief.value} <span className="text-chrome">{card.relief.unit}</span>
                </span>
              )}
              <EvidenceLabel label={card.relief.label} />
            </div>
            <p className="mt-3 text-xs text-chrome">{card.wording}</p>
            <div className="mt-4 flex items-center justify-between gap-3 text-sm">
              <Link href={`/site/${encodeURIComponent(card.cardId)}`} className="font-medium text-flare no-underline hover:underline">
                Open evidence card {'→'}
              </Link>
              {cards.length > 1 ? (
                <button
                  type="button"
                  className="rounded-full border border-border px-3 py-1 text-xs text-chrome hover:bg-platinum/5"
                  onClick={() => {
                    setPaused(false);
                    setIndex((n) => (n + 1) % cards.length);
                  }}
                >
                  Next card
                </button>
              ) : null}
            </div>
            <p className="mt-3 font-mono text-[10px] tracking-[0.12em] text-dim">BUNDLE {bundleVersion}</p>
          </aside>
          </div>
        )}
      </div>
    </section>
  );
}
