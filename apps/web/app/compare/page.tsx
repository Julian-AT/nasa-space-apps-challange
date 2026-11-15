// /compare (PRD 8.6, 10): the compare lab opened on the first card of the release bundle, with a
// switcher over every card pair. Pairs come from cards/<card_id>.json; nothing is computed here.
import type { Metadata } from 'next';

import { UnavailablePanel } from '@/components/model/unavailable-panel';
import { LabPage } from '@/components/compare/lab-page';
import { labCards } from '@/components/compare/lab-data';
import { loadBundle } from '@/lib/bundle';
import { SITE_TITLE } from '@/lib/shell';

export const metadata: Metadata = { title: `Compare · ${SITE_TITLE}` };

export default function CompareIndexPage() {
  const bundle = loadBundle();
  const first = labCards(bundle)[0];
  if (first === undefined) {
    return (
      <div className="mx-auto max-w-[1400px] px-4 py-12 sm:px-6">
        <UnavailablePanel title="No card pairs in this bundle">
          <p>The release bundle holds no cards/&lt;card_id&gt;.json, so there is no pair to compare.</p>
        </UnavailablePanel>
      </div>
    );
  }
  return <LabPage bundle={bundle} card={first} />;
}
