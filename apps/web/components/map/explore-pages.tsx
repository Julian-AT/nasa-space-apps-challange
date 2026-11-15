// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The server-rendered bodies of the /explore routes (PRD 8.6, 10). They read the bundle through
// the loader the route passes in, project it into serialisable props (explore-data.ts) and render
// the page header, the shipping-mode and bundle-version line, the map and the evidence legend.
import Link from 'next/link';

import { EvidenceLegend } from '@/components/evidence-legend';
import { PageHeader } from '@/components/page-header';
import { Section } from '@/components/section';

import type { Bundle } from '../../lib/bundle.ts';
import type { ExplorerBundle } from './explore-data.ts';
import { Atlas } from '../atlas/atlas.tsx';
import { buildAtlasBody, buildAtlasEarth } from '../atlas/atlas-data.ts';

export type LoadedBundle = Bundle & { readonly records: ExplorerBundle['records'] };

const LEAD: Readonly<Record<'moon' | 'mars', string>> = {
  moon: 'Moon target on a globe and, in 2D, in its polar-stereographic frame in projected metres, with the window grid coloured by eligibility.',
  mars: 'Mars targets on a globe and, in 2D, in their local projected frames in metres, terrain only, with the window grid coloured by eligibility.',
};

export function ExploreBodyPage({ body, bundle }: { readonly body: 'moon' | 'mars'; readonly bundle: LoadedBundle }) {
  return (
    <>
      <p className="sr-only">{LEAD[body]}</p>
      <Atlas data={buildAtlasBody(bundle, body)} />
      <div className="px-4 pt-6 pb-8 sm:px-6">
        <Section title="Evidence labels">
          <EvidenceLegend headingLevel={3} />
        </Section>
      </div>
    </>
  );
}

export function ExploreEarthPage({ bundle }: { readonly bundle: LoadedBundle }) {
  return (
    <>
      <p className="sr-only">Earth analog windows from the release bundle on a globe and a NASA basemap, with the bundle hillshade tiles on top.</p>
      <Atlas data={buildAtlasEarth(bundle)} />
      <div data-full-bleed className="px-4 pt-6 pb-8 sm:px-6">
        <Section title="Evidence labels">
          <EvidenceLegend headingLevel={3} />
        </Section>
      </div>
    </>
  );
}

export function ExploreIndexPage() {
  return (
    <>
      <PageHeader eyebrow="Explore" title="Explore" lead="Choose a body." />
      <ul className="mt-6 flex flex-col gap-2">
        {(['moon', 'mars', 'earth'] as const).map((body) => (
          <li key={body}>
            <Link className="underline" href={`/explore/${body}`}>
              {body[0]?.toUpperCase()}
              {body.slice(1)}
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
