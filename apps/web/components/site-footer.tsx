// The footer on every page (PRD 8.4, 8.6): T0's "Bundle version: <bundle_version>", which
// web-smoke asserts, followed by the bundle's schema version, as one text node.
import type { Bundle } from '../lib/bundle.ts';
import { shellText } from '../lib/shell.ts';

export function SiteFooter({ bundle }: Readonly<{ bundle: Bundle }>) {
  return (
    <footer className="border-t px-4 py-2">
      {`${shellText(bundle).footer} · Schema ${bundle.manifest.schema_version}`}
    </footer>
  );
}
