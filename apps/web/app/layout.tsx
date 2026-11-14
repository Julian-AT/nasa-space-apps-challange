// Root layout (PRD 8.6): every page shows the mock banner for a mock bundle, the shipping-mode
// line and the bundle version in the footer. The bundle is read from apps/web/data/ at build time;
// loadBundle throws, and so fails next build, on a mock bundle for production.
import './globals.css';

import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { loadBundle } from '../lib/bundle.ts';
import { shellText, SITE_TITLE } from '../lib/shell.ts';

export const metadata: Metadata = { title: SITE_TITLE };

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  const text = shellText(loadBundle());
  return (
    <html lang="en">
      <body className="antialiased">
        <header>
          {text.mockBanner === null ? null : (
            // The red token is reserved for this banner; white on it is 6.5:1 (PRD 8.10).
            <p className="bg-mock px-4 py-3 font-bold text-mock-foreground">{text.mockBanner}</p>
          )}
          <p className="px-4 py-2">{text.shippingMode}</p>
        </header>
        {children}
        <footer className="px-4 py-2">{text.footer}</footer>
      </body>
    </html>
  );
}
