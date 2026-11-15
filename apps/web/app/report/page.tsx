// /report: ShadowAstra Atlas's printable site report, ported (components/shadowastra/lab, MIT, Team
// ShadowAstra; see THIRD_PARTY_NOTICES.md). Its scores are ShadowAstra's model, labelled as such.
import type { Metadata } from 'next';
import { Suspense } from 'react';

import ReportPage from '../../components/shadowastra/lab/ReportPage.tsx';
import { SITE_TITLE } from '../../lib/shell.ts';

export const metadata: Metadata = { title: `Site report · ${SITE_TITLE}` };

export default function Page() {
  return (
    <div data-full-bleed>
      <Suspense fallback={<p className="m-6">Loading the report…</p>}>
      <ReportPage />
    </Suspense>
    </div>
  );
}
