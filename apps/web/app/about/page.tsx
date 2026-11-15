// /about: the layout of ShadowAstra Atlas's About page (components/shadowastra/about, MIT, Team
// ShadowAstra; see THIRD_PARTY_NOTICES.md) with Terraluna's own team, credits and AI disclosure.
import type { Metadata } from 'next';

import AboutPage from '../../components/shadowastra/about/AboutPage.tsx';
import { SITE_TITLE } from '../../lib/shell.ts';

export const dynamic = 'error';

export const metadata: Metadata = { title: `About · ${SITE_TITLE}` };

export default function Page() {
  return (
    <div data-full-bleed>
      <AboutPage />
    </div>
  );
}
