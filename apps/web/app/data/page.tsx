// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// `/data` (PRD 8.6), statically rendered inside the root layout's main landmark. It shows the
// bundle's sources from sources.json, read through readSources (./sources.ts) when manifest.json
// lists that file, and the bundle's own files from manifest.json. Beyond its headings the page adds
// no text: every source, file and checksum comes from the records, and a missing sources.json is
// labelled Unavailable with the reason.
import type { Metadata } from 'next';

import { PageHeader } from '../../components/page-header.tsx';
import { Section } from '../../components/section.tsx';
import { LabelledValue } from '../../components/labelled-value.tsx';
import { BundleFilesTable, SourcesTable } from '../../components/sources-table.tsx';
import { loadBundle } from '../../lib/bundle.ts';
import { SITE_TITLE } from '../../lib/shell.ts';
import { readSources } from './sources.ts';

// Fail the build if anything here would make the route dynamic.
export const dynamic = 'error';

export const metadata: Metadata = { title: `Data sources · ${SITE_TITLE}` };

export default function DataPage() {
  const { manifest } = loadBundle();
  const sources = readSources({ manifest });
  return (
    <>
      <PageHeader title="Data sources" />
      <Section title="Sources" id="sources" className="mt-8 border-t-0 pt-0">
        {sources.kind === 'records' ? (
          <SourcesTable records={sources.records} />
        ) : (
          <LabelledValue label="Unavailable" reason={sources.reason} source={sources.source} />
        )}
      </Section>
      <Section title="Bundle files" id="bundle-files">
        <BundleFilesTable files={manifest.files} />
      </Section>
    </>
  );
}
