// Root layout (PRD 3, 8.6, 8.10, D08). Every route shows, in this order: the skip link; in the
// page header, the mock banner for a mock bundle, the shipping-mode banner and the primary
// navigation; the page inside the one main landmark; and the footer with the bundle and schema
// versions. The bundle is read from apps/web/data/ at build time through loadBundle
// (lib/bundle.ts), which throws, and so fails next build, on a mock bundle for production. All of
// this is server-rendered; only the navigation (components/site-header.tsx) is a client module,
// because it reads the pathname for aria-current.
import './globals.css';

import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { MockBanner } from '../components/mock-banner.tsx';
import { ModeBanner } from '../components/mode-banner.tsx';
import { SiteFooter } from '../components/site-footer.tsx';
import { SiteHeader } from '../components/site-header.tsx';
import { MAIN_ID, SkipLink } from '../components/skip-link.tsx';
import { loadBundle } from '../lib/bundle.ts';
import { SITE_TITLE } from '../lib/shell.ts';

export const metadata: Metadata = { title: SITE_TITLE };

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  const bundle = loadBundle();
  return (
    <html lang="en">
      <body className="bg-observatory min-h-dvh antialiased">
        <SkipLink />
        <header className="border-b border-line bg-background/90">
          <MockBanner bundle={bundle} />
          <ModeBanner bundle={bundle} />
          <SiteHeader />
        </header>
        {/* tabIndex -1 lets the skip link move focus here in every browser. */}
        <main id={MAIN_ID} tabIndex={-1} className="mx-auto w-full max-w-6xl px-4 py-10 sm:px-6">
          {children}
        </main>
        <SiteFooter bundle={bundle} />
      </body>
    </html>
  );
}
