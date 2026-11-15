// `/data` (PRD 8.6), statically rendered inside the root layout's main landmark. It shows the
// bundle's sources from sources.json, read through readSources (./sources.ts) when manifest.json
// lists that file, and the bundle's own files from manifest.json. Beyond its headings the page adds
// no text: every source, file and checksum comes from the records, and a missing sources.json is
// labelled Unavailable with the reason.
import type { Metadata } from 'next';

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
      <h1>Data sources</h1>
      <section aria-labelledby="sources" className="mt-6">
        <h2 id="sources">Sources</h2>
        <div className="mt-2">
          {sources.kind === 'records' ? (
            <SourcesTable records={sources.records} />
          ) : (
            <LabelledValue label="Unavailable" reason={sources.reason} source={sources.source} />
          )}
        </div>
      </section>
      <section aria-labelledby="bundle-files" className="mt-6">
        <h2 id="bundle-files">Bundle files</h2>
        <div className="mt-2">
          <BundleFilesTable files={manifest.files} />
        </div>
      </section>
    </>
  );
}
