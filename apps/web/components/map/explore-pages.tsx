// The server-rendered bodies of the /explore routes (PRD 8.6, 10). They read the bundle through
// the loader the route passes in, project it into serialisable props (explore-data.ts) and render
// the page header, the shipping-mode and bundle-version line, the map and the evidence legend.
import Link from 'next/link';

import { EvidenceLegend } from '@/components/evidence-legend';
import { PageHeader } from '@/components/page-header';
import { Section } from '@/components/section';

import { modeBannerText } from '../mode-banner.tsx';
import type { Bundle } from '../../lib/bundle.ts';
import { buildBodyData, buildEarthData, type ExplorerBundle } from './explore-data.ts';
import { EarthExplorer } from './earth-explorer.tsx';
import { ExplorerMap } from './explorer-map.tsx';

export type LoadedBundle = Bundle & { readonly records: ExplorerBundle['records'] };

function assetBase(): string | null {
  const value = process.env.NEXT_PUBLIC_ASSET_BASE;
  return value === undefined || value === '' ? null : value;
}

function StatusLine({ bundle }: { readonly bundle: LoadedBundle }) {
  return (
    <p className="text-sm text-muted-foreground" data-slot="explore-status">
      {modeBannerText(bundle)}. Bundle version: {bundle.manifest.bundle_version}
      {bundle.manifest.mock ? ' (mock bundle: placeholder data, not results)' : ''}.
    </p>
  );
}

const LEAD: Readonly<Record<'moon' | 'mars', string>> = {
  moon: 'Polar-stereographic view of the Moon target in projected metres, with the window grid coloured by eligibility.',
  mars: 'Local view of the Mars targets in projected metres, terrain only, with the window grid coloured by eligibility.',
};

export function ExploreBodyPage({ body, bundle }: { readonly body: 'moon' | 'mars'; readonly bundle: LoadedBundle }) {
  const data = buildBodyData(bundle, body);
  return (
    <div data-full-bleed className="px-4 pb-8 sm:px-6">
      <h1 className="sr-only">{body === 'moon' ? 'Moon explorer' : 'Mars explorer'}</h1>
      <p className="sr-only">{LEAD[body]}</p>
      <StatusLine bundle={bundle} />
      <div className="mt-3">
        <ExplorerMap data={data} body={body} assetBase={assetBase()} />
      </div>
      <Section title="Evidence labels">
        <EvidenceLegend headingLevel={3} />
      </Section>
    </div>
  );
}

export function ExploreEarthPage({ bundle }: { readonly bundle: LoadedBundle }) {
  const data = buildEarthData(bundle);
  return (
    <div data-full-bleed className="px-4 pb-8 sm:px-6">
      <h1 className="sr-only">Earth candidates</h1>
      <StatusLine bundle={bundle} />
      <div className="mt-3">
        <EarthExplorer
          data={data}
          mapsEnabled={process.env.NEXT_PUBLIC_MAPS_ENABLED !== '0'}
          probeEnabled={process.env.NEXT_PUBLIC_PROBE_ENABLED === '1'}
        />
      </div>
      <Section title="Evidence labels">
        <EvidenceLegend headingLevel={3} />
      </Section>
    </div>
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
