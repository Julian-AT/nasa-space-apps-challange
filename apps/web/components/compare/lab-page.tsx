// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Builds the compare lab for one card of the loaded bundle, with the pair switcher over all cards.
import type { CardRecord } from '@terraluna/schema';

import type { PulledBundle } from '../../lib/bundle.ts';
import { CompareLab } from './compare-lab.tsx';
import type { PairOption } from './kit.tsx';
import { buildLabPair, labCards } from './lab-data.ts';

export const compareHref = (card: CardRecord): string =>
  `/compare/${encodeURIComponent(card.comparison_ref.target_window_id)}/${encodeURIComponent(card.comparison_ref.earth_window_id)}`;

export function LabPage({ bundle, card }: { readonly bundle: PulledBundle; readonly card: CardRecord }) {
  const cards = labCards(bundle);
  const options: PairOption[] = cards.map((c, i) => {
    const p = buildLabPair(bundle, c);
    return { href: compareHref(c), label: p.headline, body: p.body };
  });
  return (
    <CompareLab
      card={card}
      pair={buildLabPair(bundle, card)}
      index={cards.indexOf(card)}
      total={cards.length}
      options={options}
      current={compareHref(card)}
      records={bundle.records}
      bundleVersion={bundle.manifest.bundle_version}
    />
  );
}
