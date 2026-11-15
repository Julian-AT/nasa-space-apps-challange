// /explore (PRD 8.6): the entry to the three explorer views.
import type { Metadata } from 'next';

import { ExploreIndexPage } from '../../components/map/explore-pages.tsx';
import { SITE_TITLE } from '../../lib/shell.ts';

export const dynamic = 'error';

export const metadata: Metadata = { title: `Explore · ${SITE_TITLE}` };

export default function Page() {
  return <ExploreIndexPage />;
}
