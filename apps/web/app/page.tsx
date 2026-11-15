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
import { loadBundle } from '../lib/bundle.ts';
import { SITE_TITLE } from '../lib/shell.ts';

// Fail the build if anything here would make the route dynamic.
export const dynamic = 'error';

/** Shown on the page and required in main by scripts/page-check.ts (a page file may export nothing else). */
const HOME_LINE = 'These pages show records from the bundle named in the footer.';

// Star positions for the decorative sky: fixed numbers, drawn as SVG circles (no image, D35).
const STARS: readonly (readonly [number, number, number])[] = [
  [18, 30, 1], [52, 120, 1.4], [96, 60, 0.9], [140, 200, 1.2], [30, 260, 0.8], [210, 40, 1.1],
  [260, 150, 0.9], [300, 280, 1.3], [180, 300, 0.8], [330, 70, 1], [70, 190, 0.7], [240, 230, 1],
  [120, 20, 0.8], [350, 190, 1.1], [10, 150, 0.9], [285, 20, 0.7], [160, 120, 0.6], [320, 330, 0.9],
];

function Orbit() {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 380 380"
      className="h-auto w-full max-w-md text-earth"
    >
      <defs>
        <radialGradient id="orb" cx="34%" cy="30%" r="78%">
          <stop offset="0" stopColor="#33466b" />
          <stop offset="0.6" stopColor="#10141d" />
          <stop offset="1" stopColor="#07080a" />
        </radialGradient>
        <radialGradient id="halo" cx="50%" cy="50%" r="50%">
          <stop offset="0.7" stopColor="#f2c879" stopOpacity="0" />
          <stop offset="0.9" stopColor="#f2c879" stopOpacity="0.1" />
          <stop offset="1" stopColor="#f2c879" stopOpacity="0" />
        </radialGradient>
      </defs>
      {STARS.map(([x, y, r]) => (
        <circle key={`${String(x)}-${String(y)}`} cx={x} cy={y} r={r} fill="#edeae4" opacity="0.6" />
      ))}
      <circle cx="190" cy="190" r="150" fill="url(#halo)" />
      <circle cx="190" cy="190" r="104" fill="url(#orb)" stroke="currentColor" strokeOpacity="0.4" />
      <g fill="none" stroke="currentColor" strokeOpacity="0.25">
        <ellipse cx="190" cy="190" rx="104" ry="38" />
        <ellipse cx="190" cy="190" rx="104" ry="74" />
        <ellipse cx="190" cy="190" rx="44" ry="104" />
        <ellipse cx="190" cy="190" rx="168" ry="56" transform="rotate(-22 190 190)" strokeDasharray="2 7" />
        <ellipse cx="190" cy="190" rx="140" ry="130" strokeDasharray="1 9" strokeOpacity="0.3" />
      </g>
      <circle cx="318" cy="118" r="9" fill="#c9c5bc" />
      <circle cx="52" cy="244" r="6.5" fill="#e08a5f" />
    </svg>
  );
}

function Kicker({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-center gap-3 font-mono text-[11px] tracking-[0.22em] text-chrome uppercase">
      <span aria-hidden="true" className="h-px w-8 bg-chrome/50" />
      {children}
    </p>
  );
}

function Em({ children }: { children: ReactNode }) {
  return <span className="block font-serif font-normal tracking-[-0.02em] text-flare italic">{children}</span>;
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="surface flex flex-col gap-3 rounded-3xl p-6">
      <p className="font-mono text-[11px] tracking-[0.18em] text-chrome uppercase">{label}</p>
      <div className="text-base">{children}</div>
    </div>
  );
}

function EntryCard({ href, title, kicker, children }: { href: string; title: string; kicker: string; children: ReactNode }) {
  return (
    <div className="surface lift flex flex-col gap-2 rounded-3xl p-6">
      <p className="font-mono text-[11px] tracking-[0.18em] text-chrome uppercase">{kicker}</p>
      <p className="text-xl font-medium tracking-tight">
        <Link href={href} className="no-underline after:absolute after:inset-0 after:rounded-3xl">
          {title}
          <span aria-hidden="true" className="ml-1 text-flare">
            {'\u2192'}
          </span>
        </Link>
      </p>
      <p className="text-sm text-muted-foreground">{children}</p>
    </div>
  );
}

// 'rosamond-dry-lake' -> 'Rosamond dry lake'.
const regionName = (id: string): string => {
  const text = id.replaceAll('-', ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
};

export default function Home() {
  const bundle = loadBundle();
  const cards = [...bundle.records.cards.keys()];
  const targets = bundle.records.targets;
  const statuses = new Map<string, number>();
  for (const record of bundle.validation) statuses.set(record.status, (statuses.get(record.status) ?? 0) + 1);
  const firstCard = cards[0];
  // Declared Earth search regions, read from the Earth window records (search_region_id).
  const regions = [
    ...new Set(
      (bundle.records.windows.get('earth') ?? []).flatMap((w) => {
        const id = w.window.environment_flags.search_region_id;
        return id === null ? [] : [regionName(id)];
      }),
    ),
  ];
  const g5 = bundle.validation.find((g) => g.gate_id === 'G5') ?? null;

  return (
    <>
      <div className="grid items-center gap-10 py-8 md:grid-cols-[1.35fr_1fr]">
        <div className="flex flex-col gap-7">
          <Kicker>Terrain-only Earth candidate search · Mode C</Kicker>
          <h1 className="text-[clamp(44px,7vw,104px)] text-balance">
            <span className="block">{`${SITE_TITLE} `}</span>
            <Em>a terrain-only Earth candidate search for Connecting Ridge, Jezero and Arcadia, over declared Earth regions only.</Em>
          </h1>
          <p className="max-w-2xl text-[17px] leading-relaxed text-muted-foreground">
            Earth windows are compared with the Moon target (Connecting Ridge, an Artemis candidate region) and
            the Mars targets (Jezero and Arcadia, terrain only) by terrain roughness fingerprints ν(L) computed
            from public elevation data. Earth candidates come only from the declared regions
            {regions.length === 0 ? '' : `: ${regions.join(' and ')}`}.
          </p>
          <p className="max-w-2xl text-sm text-muted-foreground">{HOME_LINE}</p>
          <ul className="flex flex-wrap gap-3">
            <li>
              <Link
                href="/explore/moon"
                className="btn-primary inline-flex h-11 items-center rounded-full px-6 text-sm font-semibold no-underline"
              >
                <span className="relative z-10">Explore the Moon</span>
              </Link>
            </li>
            <li>
              <Link
                href="/methods"
                className="inline-flex h-11 items-center rounded-full border border-border px-6 text-sm font-medium no-underline transition-colors hover:bg-platinum/5"
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
          <Stat label="Evidence cards">
            <LabelledValue label="Derived" value={cards.length} unit="cards" source="cards/ files of the bundle" />
          </Stat>
          <Stat label="Targets">
            {targets === null ? (
              <LabelledValue label="Unavailable" reason="the bundle lists no targets.json" />
            ) : (
              <LabelledValue label="Derived" value={targets.length} unit="targets" source="targets.json" />
            )}
          </Stat>
          <Stat label="Release gates by status">
            <ul className="mb-3 flex flex-wrap gap-2">
              {[...statuses].map(([status, count]) => (
                <li key={status}>
                  <Badge variant="outline">{`${status}: ${String(count)}`}</Badge>
                </li>
              ))}
            </ul>
            <LabelledValue label="Derived" value={bundle.validation.length} unit="gate records" source="validation.json" />
          </Stat>
        </div>
      </Section>

      <Section title="What this does and does not claim">
        <div className="surface rounded-3xl p-6 sm:p-8">
          <p className="max-w-3xl text-lg leading-relaxed">
            The shipping mode in the pill above limits every claim. In Mode C this is a terrain analog pilot:
            radar comparison was not completed, so nothing here is a radar claim and no page ranks a site as
            the analog. The nearest Earth window to a target is the nearest in the metric, not a close match or a
            morphological analog. The learned encoder is experimental
            {g5 === null
              ? ' and outside every ranking; G5 is not recorded in this bundle.'
              : g5.status === 'PASS'
                ? ' and stays a Predicted second opinion; G5 passed.'
                : ` and outside every ranking: its promotion gate G5 is ${g5.status}${g5.status === 'FAIL' && g5.result !== null ? ` (${g5.result})` : ''}.`}
          </p>
        </div>
      </Section>

      <Section title="Where to go">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <EntryCard href="/explore/moon" kicker="Explore" title="Moon">
            Projected polar map of the Connecting Ridge region.
          </EntryCard>
          <EntryCard href="/explore/mars" kicker="Explore" title="Mars">
            Projected map, terrain only.
          </EntryCard>
          <EntryCard href="/explore/earth" kicker="Explore" title="Earth">
            Candidate Earth windows from the declared regions.
          </EntryCard>
          {firstCard === undefined ? (
            <div className="surface flex flex-col gap-2 rounded-3xl p-6">
              <p className="font-mono text-[11px] tracking-[0.18em] text-chrome uppercase">Evidence</p>
              <p className="text-xl font-medium tracking-tight">Evidence cards</p>
              <LabelledValue label="Unavailable" reason="the bundle holds no evidence cards" />
            </div>
          ) : (
            <EntryCard href={`/site/${encodeURIComponent(firstCard)}`} kicker="Evidence" title="Evidence cards">
              One card per target, with evidence labels and claim limits.
            </EntryCard>
          )}
          <EntryCard href="/model" kicker="Model" title="Model">
            The experimental encoder and its evaluation status.
          </EntryCard>
          <EntryCard href="/methods" kicker="Validation" title="Methods">
            The validation board: every release gate with its status.
          </EntryCard>
          <EntryCard href="/data" kicker="Provenance" title="Data">
            Source products, licences and bundle checksums.
          </EntryCard>
          <EntryCard href="/paper" kicker="Write-up" title="Paper">
            The write-up generated from the same bundle.
          </EntryCard>
        </div>
      </Section>
    </>
  );
}
