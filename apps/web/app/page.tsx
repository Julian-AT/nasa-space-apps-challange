// Adapted from ShadowAstra_Atlas (MIT, (c) 2026 Team ShadowAstra), https://github.com/ABRUBAB/ShadowAstra_Atlas
// `/` (PRD 3, 8.6), statically rendered inside the root layout's main landmark. A factual
// landing page: what Terraluna compares, the claim limits of the shipping mode, and entry points.
// Every number on it is read from the bundle through loadBundle and shown with an evidence label;
// no scientific result is typed here. make web-smoke checks that main holds the title and HOME_LINE
// (scripts/page-check.ts).
import Link from 'next/link';
import type { ReactNode } from 'react';

import { LabelledValue } from '../components/labelled-value.tsx';
import { Section } from '../components/section.tsx';
import { Badge } from '../components/ui/badge.tsx';
import { Card, CardDescription, CardHeader, CardTitle } from '../components/ui/card.tsx';
import { loadBundle } from '../lib/bundle.ts';
import { SITE_TITLE } from '../lib/shell.ts';

// Fail the build if anything here would make the route dynamic.
export const dynamic = 'error';

/** Shown on the page and required in main by scripts/page-check.ts (a page file may export nothing else). */
const HOME_LINE = 'These pages show records from the bundle named in the footer.';

function Orbit() {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 320 320"
      className="h-auto w-full max-w-xs text-evidence-observed"
    >
      <defs>
        <radialGradient id="orb" cx="35%" cy="30%" r="75%">
          <stop offset="0" stopColor="#2b3a55" />
          <stop offset="1" stopColor="#0b0c0f" />
        </radialGradient>
      </defs>
      <circle cx="160" cy="160" r="96" fill="url(#orb)" stroke="currentColor" strokeOpacity="0.5" />
      <g fill="none" stroke="currentColor" strokeOpacity="0.28">
        <ellipse cx="160" cy="160" rx="96" ry="36" />
        <ellipse cx="160" cy="160" rx="96" ry="68" />
        <ellipse cx="160" cy="160" rx="40" ry="96" />
        <ellipse cx="160" cy="160" rx="150" ry="52" transform="rotate(-20 160 160)" strokeDasharray="3 6" />
      </g>
      <circle cx="283" cy="104" r="9" fill="#c9c5bc" />
      <circle cx="40" cy="214" r="7" fill="#e08a5f" />
    </svg>
  );
}

function EntryCard({ href, title, children }: { href: string; title: string; children: ReactNode }) {
  return (
    <Card className="relative transition-colors hover:border-border motion-reduce:transition-none">
      <CardHeader>
        <CardTitle>
          <Link href={href} className="after:absolute after:inset-0">
            {title}
          </Link>
        </CardTitle>
        <CardDescription>{children}</CardDescription>
      </CardHeader>
    </Card>
  );
}

export default function Home() {
  const bundle = loadBundle();
  const cards = [...bundle.records.cards.keys()];
  const targets = bundle.records.targets;
  const statuses = new Map<string, number>();
  for (const record of bundle.validation) statuses.set(record.status, (statuses.get(record.status) ?? 0) + 1);
  const firstCard = cards[0];

  return (
    <>
      <div className="grid items-center gap-10 py-6 md:grid-cols-[1.4fr_1fr]">
        <div className="flex flex-col gap-5">
          <p className="text-xs font-semibold tracking-[0.18em] text-primary uppercase">
            Earth analog search · terrain pilot
          </p>
          <h1 className="text-5xl text-balance sm:text-6xl">{SITE_TITLE}</h1>
          <p className="max-w-2xl text-lg text-muted-foreground">
            An Earth analog search for the Moon (Connecting Ridge, an Artemis candidate region) and for Mars
            (terrain only). It compares terrain roughness fingerprints ν(L) computed from public elevation
            data.
          </p>
          <p className="max-w-2xl text-sm text-muted-foreground">{HOME_LINE}</p>
          <ul className="flex flex-wrap gap-3">
            <li>
              <Link
                href="/explore/moon"
                className="inline-flex items-center rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground no-underline hover:opacity-90"
              >
                Explore the Moon
              </Link>
            </li>
            <li>
              <Link
                href="/methods"
                className="inline-flex items-center rounded-md border border-border px-4 py-2 text-sm font-semibold no-underline hover:bg-accent"
              >
                Read the methods
              </Link>
            </li>
          </ul>
        </div>
        <div className="flex justify-center md:justify-end">
          <Orbit />
        </div>
      </div>

      <Section title="In this bundle" description="Counts are read from the bundle records, not typed into the page.">
        <div className="grid gap-4 sm:grid-cols-3">
          <Card>
            <CardHeader>
              <CardDescription>Evidence cards</CardDescription>
              <CardTitle className="text-base font-normal">
                <LabelledValue label="Derived" value={cards.length} unit="cards" source="cards/ files of the bundle" />
              </CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader>
              <CardDescription>Targets</CardDescription>
              <CardTitle className="text-base font-normal">
                {targets === null ? (
                  <LabelledValue label="Unavailable" reason="the bundle lists no targets.json" />
                ) : (
                  <LabelledValue label="Derived" value={targets.length} unit="targets" source="targets.json" />
                )}
              </CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader>
              <CardDescription>Release gates by status</CardDescription>
              <CardTitle className="text-base font-normal">
                <ul className="flex flex-wrap gap-2">
                  {[...statuses].map(([status, count]) => (
                    <li key={status}>
                      <Badge variant="outline">{`${status}: ${String(count)}`}</Badge>
                    </li>
                  ))}
                </ul>
              </CardTitle>
              <CardDescription>
                <LabelledValue
                  label="Derived"
                  value={bundle.validation.length}
                  unit="gate records"
                  source="validation.json"
                />
              </CardDescription>
            </CardHeader>
          </Card>
        </div>
      </Section>

      <Section title="What this does and does not claim">
        <Card>
          <CardHeader>
            <CardDescription className="max-w-3xl text-base text-foreground">
              The shipping mode in the banner above limits every claim. In Mode C this is a terrain analog
              pilot: radar comparison was not completed, so nothing here is a radar claim and no page ranks a
              site as the analog. The learned encoder is experimental and outside every
              ranking until its release gate passes.
            </CardDescription>
          </CardHeader>
        </Card>
      </Section>

      <Section title="Where to go">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <EntryCard href="/explore/moon" title="Moon">
            Projected polar map of the Connecting Ridge region.
          </EntryCard>
          <EntryCard href="/explore/mars" title="Mars">
            Projected map, terrain only.
          </EntryCard>
          <EntryCard href="/explore/earth" title="Earth">
            The Earth sites searched for analogs.
          </EntryCard>
          {firstCard === undefined ? (
            <Card>
              <CardHeader>
                <CardTitle>Evidence cards</CardTitle>
                <CardDescription>
                  <LabelledValue label="Unavailable" reason="the bundle holds no evidence cards" />
                </CardDescription>
              </CardHeader>
            </Card>
          ) : (
            <EntryCard href={`/site/${encodeURIComponent(firstCard)}`} title="Evidence cards">
              One card per target, with evidence labels and claim limits.
            </EntryCard>
          )}
          <EntryCard href="/model" title="Model">
            The experimental encoder and its evaluation status.
          </EntryCard>
          <EntryCard href="/methods" title="Methods">
            The validation board: every release gate with its status.
          </EntryCard>
          <EntryCard href="/data" title="Data">
            Source products, licences and bundle checksums.
          </EntryCard>
          <EntryCard href="/paper" title="Paper">
            The write-up generated from the same bundle.
          </EntryCard>
        </div>
      </Section>
    </>
  );
}
