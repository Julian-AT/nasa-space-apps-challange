// /explore/earth (PRD 8.6, 10), statically rendered from the release bundle.
import type { Metadata } from 'next';

import { ExploreEarthPage } from '../../../components/map/explore-pages.tsx';
import { loadBundle } from '../../../lib/bundle.ts';
import { SITE_TITLE } from '../../../lib/shell.ts';

// Fail the build if anything here would make the route dynamic.
export const dynamic = 'error';

export const metadata: Metadata = { title: `Earth candidates · ${SITE_TITLE}` };

export default function Page() {
  return <ExploreEarthPage bundle={loadBundle()} />;
}
