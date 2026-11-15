// /learn: ShadowAstra Atlas's "Learn" page, ported (components/shadowastra/learn, MIT, Team
// ShadowAstra; see THIRD_PARTY_NOTICES.md): a short lesson on Earth analog sites and Moon/Mars
// conditions with a quiz. Its scores come from ShadowAstra's own grid, not from Terraluna's bundle.
import type { Metadata } from 'next';
import { Suspense } from 'react';

import LearnPage from '../../components/shadowastra/learn/LearnPage.tsx';
import { SITE_TITLE } from '../../lib/shell.ts';

export const dynamic = 'error';

export const metadata: Metadata = { title: `Learn · ${SITE_TITLE}` };

export default function Page() {
  return (
    <Suspense fallback={<p className="m-6">Loading the lesson…</p>}>
      <LearnPage />
    </Suspense>
  );
}
