// Adapted from ShadowAstra_Atlas (MIT, (c) 2026 Team ShadowAstra), https://github.com/ABRUBAB/ShadowAstra_Atlas
// The footer on every page (PRD 8.4, 8.6): T0's "Bundle version: <bundle_version>", which
// web-smoke asserts, followed by the bundle's schema version, as one text node in its own
// paragraph, then the team and links to the data sources and methods.
import Link from 'next/link';

import type { Bundle } from '../lib/bundle.ts';
import { shellText } from '../lib/shell.ts';

export function SiteFooter({ bundle }: Readonly<{ bundle: Bundle }>) {
  return (
    <footer className="mt-20 border-t border-line bg-card/40">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 px-4 py-8 text-sm text-muted-foreground sm:px-6 md:flex-row md:items-center md:justify-between">
        <div className="flex flex-col gap-1">
          <p className="font-mono text-xs">{`${shellText(bundle).footer} · Schema ${bundle.manifest.schema_version}`}</p>
          <p>Gradient Descent</p>
        </div>
        <ul className="flex gap-4">
          <li>
            <Link href="/data" className="text-foreground">
              Data sources
            </Link>
          </li>
          <li>
            <Link href="/methods" className="text-foreground">
              Methods
            </Link>
          </li>
        </ul>
      </div>
    </footer>
  );
}
