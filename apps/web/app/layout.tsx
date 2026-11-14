// Root layout (PRD 8.6): every page shows the mock banner for a mock bundle, the shipping-mode
// line and the bundle version in the footer. The bundle is read from apps/web/data/ at build time;
// loadBundle throws, and so fails next build, on a mock bundle for production.
import type { Metadata } from 'next';
import type { CSSProperties, ReactNode } from 'react';

import { loadBundle } from '../lib/bundle.ts';
import { shellText, SITE_TITLE } from '../lib/shell.ts';

export const metadata: Metadata = { title: SITE_TITLE };

// White on #b91c1c has a contrast ratio of 6.5:1 (WCAG 2.2 AA, PRD 8.10).
const MOCK_BANNER_STYLE: CSSProperties = {
  margin: 0,
  padding: '0.75rem 1rem',
  background: '#b91c1c',
  color: '#ffffff',
  fontWeight: 700,
};

const LINE_STYLE: CSSProperties = { margin: 0, padding: '0.5rem 1rem' };

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  const text = shellText(loadBundle());
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: 'system-ui, sans-serif' }}>
        <header>
          {text.mockBanner === null ? null : <p style={MOCK_BANNER_STYLE}>{text.mockBanner}</p>}
          <p style={LINE_STYLE}>{text.shippingMode}</p>
        </header>
        {children}
        <footer style={LINE_STYLE}>{text.footer}</footer>
      </body>
    </html>
  );
}
