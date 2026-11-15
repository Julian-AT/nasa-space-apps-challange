// /lab: ShadowAstra Atlas's "Compare in 3D" lab, ported (components/shadowastra/lab, MIT, Team
// ShadowAstra; see THIRD_PARTY_NOTICES.md). Its scores are ShadowAstra's model, labelled as such.
import type { Metadata } from 'next';
import { Suspense } from 'react';

import LabClient from '../../components/shadowastra/lab/LabClient.tsx';
import { SITE_TITLE } from '../../lib/shell.ts';

export const metadata: Metadata = { title: `Compare in 3D · ${SITE_TITLE}` };

export default function Page() {
  return (
    <Suspense fallback={<p className="m-6">Loading the 3D comparison…</p>}>
      <LabClient />
    </Suspense>
  );
}
