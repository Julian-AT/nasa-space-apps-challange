// /explore/moon (PRD 8.6, 10), statically rendered from the release bundle.
import type { Metadata } from 'next';

import { ExploreBodyPage } from '../../../components/map/explore-pages.tsx';
import { loadBundle } from '../../../lib/bundle.ts';
import { SITE_TITLE } from '../../../lib/shell.ts';

// Fail the build if anything here would make the route dynamic.
export const dynamic = 'error';

export const metadata: Metadata = { title: `Moon explorer · ${SITE_TITLE}` };

export default function Page() {
  return <ExploreBodyPage body="moon" bundle={loadBundle()} />;
}
