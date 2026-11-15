// /find: ShadowAstra Atlas's "Find new sites" page, ported (components/shadowastra, MIT, Team
// ShadowAstra; see THIRD_PARTY_NOTICES.md). Its factor scores come from ShadowAstra's own model and
// 0.5 degree Earth grid (public/shadowastra/data), labelled as such. Under the factor breakdown the
// page adds Terraluna's own live probe, fed by the release bundle read here at build time.
import type { Metadata } from 'next';
import { Suspense } from 'react';

import { shortName } from '../../components/cards/card-visual.ts';
import { earthRegionName } from '../../components/cards/card-geo.ts';
import FindPage from '../../components/shadowastra/find/FindPage.tsx';
import type { TerralunaProbeProps } from '../../components/shadowastra/terraluna-fingerprint.tsx';
import { loadBundle } from '../../lib/bundle.ts';
import { probeEncoder } from '../../lib/probe/encoder-source.ts';
import { probeTargets } from '../../lib/probe/targets.ts';
import { SITE_TITLE } from '../../lib/shell.ts';

export const dynamic = 'error';

export const metadata: Metadata = { title: `Find new sites · ${SITE_TITLE}` };

export default function Page() {
  const bundle = loadBundle();
  const probe = probeTargets(bundle);
  const encoder = probeEncoder(bundle);
  const cards = [...bundle.records.cards.values()].map((card) => ({
    card_id: card.card_id,
    target_id: card.target.target_id,
    earth_name: earthRegionName(card.earth.search_region_id ?? card.earth.earth_window_id),
  }));
  const props: TerralunaProbeProps = {
    fingerprints: probe.fingerprints,
    targetsReason: probe.reason,
    bundleVersion: bundle.manifest.bundle_version,
    encoder: encoder.encoder,
    targets: (bundle.records.targets ?? []).map((t) => ({ target_id: t.target_id, name: shortName(t.name), body: t.body })),
    cards,
  };
  return (
    <div data-full-bleed>
      <Suspense fallback={<p className="m-6">Loading the find flow…</p>}>
      <FindPage probe={props} />
    </Suspense>
    </div>
  );
}
