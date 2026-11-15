// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The footer on every page (PRD 8.4, 8.6): T0's "Bundle version: <bundle_version>", which
// web-smoke asserts, followed by the bundle's schema version, as one text node in its own
// paragraph, then the team and links to the data sources and methods.
import Link from 'next/link';

import { LogoMark } from '@/components/site-header';
import { Separator } from '@/components/ui/separator';

import type { Bundle } from '../lib/bundle.ts';
import { shellText } from '../lib/shell.ts';

export function SiteFooter({ bundle }: Readonly<{ bundle: Bundle }>) {
  return (
    <footer className="mt-24 border-t bg-muted/30">
      <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-4 px-4 py-8 text-sm text-muted-foreground sm:px-6 md:flex-row md:items-center md:justify-between">
        <div className="flex flex-col gap-1">
          <p className="flex items-center gap-2 font-sans text-sm font-semibold text-foreground">
            <LogoMark size="xs" />
            Terraluna
          </p>
          <p className="font-mono text-[11px]">{`${shellText(bundle).footer} · Schema ${bundle.manifest.schema_version}`}</p>
        </div>
        <ul className="flex items-center gap-4 text-sm">
          <li>
            <Link href="/data" className="text-foreground no-underline hover:text-primary">
              Data sources
            </Link>
          </li>
          <li aria-hidden="true">
            <Separator orientation="vertical" className="h-4" />
          </li>
          <li>
            <Link href="/methods" className="text-foreground no-underline hover:text-primary">
              Methods
            </Link>
          </li>
        </ul>
      </div>
    </footer>
  );
}
