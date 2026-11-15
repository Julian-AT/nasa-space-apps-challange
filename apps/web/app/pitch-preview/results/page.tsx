// /pitch-preview/results: the pitch deck's results leaderboard alone on the deck background, for
// review and screenshots. Values are the release bundle's evidence cards; nothing is typed in.
import type { Metadata } from 'next';

import { buildResultsData, FigResults } from '@/components/pitch/fig-results';
import { loadBundle } from '@/lib/bundle';
import { SITE_TITLE } from '@/lib/shell';

export const metadata: Metadata = { title: `Results preview · ${SITE_TITLE}`, robots: { index: false } };

export default function ResultsPreviewPage() {
  const data = buildResultsData(loadBundle());
  return (
    <div style={{ background: '#F9F8F5', padding: '56px 48px', minHeight: '100vh' }}>
      <FigResults data={data} />
    </div>
  );
}
