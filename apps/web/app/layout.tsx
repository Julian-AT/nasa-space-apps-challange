// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Root layout (PRD 3, 8.6, 8.10, D08). Every route shows, in this order: the skip link; in the
// page header, the mock banner for a mock bundle, the shipping-mode banner and the primary
// navigation; the page inside the one main landmark; and the footer with the bundle and schema
// versions. The bundle is read from apps/web/data/ at build time through loadBundle
// (lib/bundle.ts), which throws, and so fails next build, on a mock bundle for production. All of
// this is server-rendered; only the navigation (components/site-header.tsx) is a client module,
// because it reads the pathname for aria-current.
import './globals.css';

import type { Metadata } from 'next';
import { Geist, Geist_Mono, Instrument_Serif, Michroma } from 'next/font/google';
import type { ReactNode } from 'react';

import { MockBanner } from '../components/mock-banner.tsx';
import { ModeBanner } from '../components/mode-banner.tsx';
import { SiteFooter } from '../components/site-footer.tsx';
import { SiteHeader } from '../components/site-header.tsx';
import { MAIN_ID, SkipLink } from '../components/skip-link.tsx';
import { loadBundle } from '../lib/bundle.ts';
import { SITE_TITLE } from '../lib/shell.ts';

// next/font/google downloads the font files at build time and serves them from this origin
// (/_next/static/media), so the CSP stays 'self' and the browser makes no request to Google.
const sans = Geist({ subsets: ['latin'], variable: '--font-geist', display: 'swap' });
const mono = Geist_Mono({ subsets: ['latin'], variable: '--font-geist-mono', display: 'swap' });
const serif = Instrument_Serif({
  subsets: ['latin'],
  weight: '400',
  style: ['normal', 'italic'],
  variable: '--font-instrument',
  display: 'swap',
});
const wide = Michroma({ subsets: ['latin'], weight: '400', variable: '--font-michroma', display: 'swap' });

export const metadata: Metadata = { title: SITE_TITLE };

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  const bundle = loadBundle();
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} ${serif.variable} ${wide.variable}`}>
      <body className="bg-observatory min-h-dvh antialiased">
        <SkipLink />
        <header className="sticky top-0 z-40 border-b border-line bg-void">
          <MockBanner bundle={bundle} />
          {/* Slim hairline strip first in the DOM (read first), drawn above the navigation row. */}
          <ModeBanner bundle={bundle} />
          <div className="relative">
            <SiteHeader />
          </div>
        </header>
        {/* tabIndex -1 lets the skip link move focus here in every browser. */}
        <main id={MAIN_ID} tabIndex={-1} className="w-full">
          {children}
        </main>
        <SiteFooter bundle={bundle} />
      </body>
    </html>
  );
}
