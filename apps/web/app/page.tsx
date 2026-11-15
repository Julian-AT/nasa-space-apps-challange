// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// `/` (PRD 3, 8.6), statically rendered inside the root layout's main landmark. A factual
// landing page: what Terraluna compares, the claim limits of the shipping mode, and entry points.
// Every number on it is read from the bundle through loadBundle and shown with an evidence label;
// no scientific result is typed here. make web-smoke checks that main holds the title and HOME_LINE
// (scripts/page-check.ts).
import Link from 'next/link';
import type { ReactNode } from 'react';

import { earthRegionName } from '../components/cards/card-geo.ts';
import { LabelledValue } from '../components/labelled-value.tsx';
import { Hero, type HeroCard } from '../components/home/hero.tsx';
import { readModelFacts } from '../components/home/model-facts.ts';
import { ModelSection } from '../components/home/model-section.tsx';
import { ringCentroid, type GlobePin } from '../components/globe/index.ts';
import { Badge } from '../components/ui/badge.tsx';
import { Card, CardContent, CardHeader } from '../components/ui/card.tsx';
import { loadBundle } from '../lib/bundle.ts';
import HomeSections from '../components/shadowastra/home/HomeSections.tsx';

// Fail the build if anything here would make the route dynamic.
export const dynamic = 'error';

/** Shown on the page and required in main by scripts/page-check.ts (a page file may export nothing else). */
const HOME_LINE = 'These pages show records from the bundle named in the footer.';

/** SA-style numbered section: a hairline, a mono index and the heading. */
function Numbered({ n, title, description, children }: { n: string; title: string; description?: string; children: ReactNode }) {
  const id = `home-${n}`;
  return (
    <section aria-labelledby={id} className="mt-20 border-t pt-10">
      <div className="mb-8 flex flex-col gap-3">
        <Badge variant="secondary" className="w-fit">{n} / {title}</Badge>
        <div className="flex flex-col gap-2">
          <h2 id={id} className="text-[clamp(24px,3vw,36px)] leading-tight font-semibold tracking-tight">
            {title}
          </h2>
          {description === undefined ? null : <p className="max-w-3xl text-muted-foreground">{description}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
      </CardHeader>
      <CardContent className="text-base">{children}</CardContent>
    </Card>
  );
}

function EntryCard({ href, title, kicker, children }: { href: string; title: string; kicker: string; children: ReactNode }) {
  return (
    <Card className="relative gap-2 transition-colors hover:bg-muted/40">
      <CardContent className="flex flex-col gap-2">
      <p className="text-xs font-medium text-muted-foreground">{kicker}</p>
      <p className="text-lg font-semibold tracking-tight">
        <Link href={href} className="no-underline after:absolute after:inset-0 after:rounded-xl">
          {title}
          <span aria-hidden="true" className="ml-1 text-primary">
            {'\u2192'}
          </span>
        </Link>
      </p>
      <p className="text-sm text-muted-foreground">{children}</p>
      </CardContent>
    </Card>
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
  const modelFacts = readModelFacts(bundle);

  // Pins: the centroid of each Earth window's polygon ring (one pin per window), read from the cards.
  const pinById = new Map<string, GlobePin>();
  const heroCards: HeroCard[] = [];
  for (const card of bundle.records.cards.values()) {
    const windowId = card.earth.earth_window_id;
    const c = ringCentroid(card.earth.polygon.ring_lonlat);
    const region = earthRegionName(card.earth.search_region_id ?? windowId);
    if (c !== null && !pinById.has(windowId)) {
      pinById.set(windowId, {
        id: windowId,
        lat: c.lat,
        lon: c.lon,
        label: `${region} window ${windowId}`,
        href: `/site/${encodeURIComponent(card.card_id)}`,
        tone: 'earth',
      });
    }
    const relief = card.distances_by_family.terrain_relief;
    heroCards.push({
      cardId: card.card_id,
      windowId,
      region,
      target: /for (.+?);/.exec(card.title)?.[1] ?? card.target.target_id,
      body: card.target.body === 'moon' ? 'Moon' : 'Mars',
      relief:
        relief.status === 'available' && relief.value !== null && relief.evidence_label !== 'Unavailable'
          ? { label: relief.evidence_label, value: relief.value.toFixed(3), unit: relief.unit }
          : { label: 'Unavailable', reason: relief.reason ?? 'not in this bundle' },
      wording: card.wording,
    });
  }

  return (
    <>
      <Hero
        eyebrow="NASA Space Apps 2026 · Team Terraluna"
        pins={[...pinById.values()]}
        cards={heroCards}
        bundleVersion={bundle.manifest.bundle_version}
      />
      <ModelSection facts={modelFacts} />
      <p className="mt-8 max-w-2xl text-sm text-muted-foreground">{HOME_LINE}</p>
      <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
        Earth candidates come only from the declared regions{regions.length === 0 ? '' : `: ${regions.join(' and ')}`}. The Moon target is
        Connecting Ridge (an Artemis candidate region) and the Mars targets are Jezero and Arcadia, compared by terrain roughness
        fingerprints ν(L) from public elevation data.
      </p>

      <Numbered n="01" title="In this bundle" description="Counts are read from the bundle records, not typed into the page.">
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
      </Numbered>

      <Numbered n="02" title="What this does and does not claim">
        <Card>
          <CardContent>
          <p className="max-w-3xl text-base leading-relaxed sm:text-lg">
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
          </CardContent>
        </Card>
      </Numbered>

      <Numbered n="03" title="Where to go">
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
            <Card>
              <CardContent className="flex flex-col gap-2">
              <p className="text-xs font-medium text-muted-foreground">Evidence</p>
              <p className="text-lg font-semibold tracking-tight">Evidence cards</p>
              <LabelledValue label="Unavailable" reason="the bundle holds no evidence cards" />
              </CardContent>
            </Card>
          ) : (
            <EntryCard href={`/site/${encodeURIComponent(firstCard)}`} kicker="Evidence" title="Evidence cards">
              One card per target, with evidence labels and claim limits.
            </EntryCard>
          )}
          <EntryCard href="/model" kicker="Model" title="Model">
            Our encoder: architecture, training curves and the G5 verdict.
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
      </Numbered>

      {/* ShadowAstra Atlas's home sections (MIT, Team ShadowAstra), each labelled as theirs. */}
      <div data-full-bleed>
        <HomeSections />
      </div>
    </>
  );
}
