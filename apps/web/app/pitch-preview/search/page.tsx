// /pitch-preview/search: a standalone preview of the pitch deck's Earth window search figure.
// Values are read at build time from the release bundle's comparisons and cards; nothing is typed in.
import type { Metadata } from 'next';

import { buildSearchData, FigSearch } from '@/components/pitch/fig-search';
import { loadBundle } from '@/lib/bundle';
import { SITE_TITLE } from '@/lib/shell';

export const metadata: Metadata = { title: `Search preview · ${SITE_TITLE}`, robots: { index: false } };

export default function SearchPreviewPage() {
  const data = buildSearchData(loadBundle());
  return (
    <div className="font-sans" style={{ background: '#F9F8F5', padding: '56px 48px', minHeight: '100vh' }}>
      <FigSearch data={data} />
    </div>
  );
}
