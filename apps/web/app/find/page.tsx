// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// /find?target=&lat=&lon=: ShadowAstra's "Find new sites" flow on Terraluna's live probe. The
// page is static; targets, their hillshade thumbnails, the example points (centres of the card
// Earth windows) and the card links are all read from the release bundle at build time. The
// probe itself runs in a Web Worker in the visitor's browser (workers/probe.worker.ts).
import type { Metadata } from 'next';
import { Suspense } from 'react';

import { earthRegionName } from '../../components/cards/card-geo.ts';
import { buildCardVisual, shortName } from '../../components/cards/card-visual.ts';
import { FindClient, type FindExample, type FindTargetInfo } from '../../components/find/find-client.tsx';
import type { CardLink } from '../../components/find/result-card.tsx';
import { loadBundle } from '../../lib/bundle.ts';
import { probeEncoder } from '../../lib/probe/encoder-source.ts';
import { probeTargets } from '../../lib/probe/targets.ts';
import { SITE_TITLE } from '../../lib/shell.ts';

export const dynamic = 'error';

export const metadata: Metadata = { title: `Find Earth places · ${SITE_TITLE}` };

function centre(ring: readonly (readonly number[])[]): { lat: number; lon: number } | null {
  // The ring is closed (first point repeated); average the distinct vertices.
  const pts = ring.length > 1 && ring[0]?.[0] === ring[ring.length - 1]?.[0] && ring[0]?.[1] === ring[ring.length - 1]?.[1] ? ring.slice(0, -1) : ring;
  if (pts.length === 0) return null;
  let lon = 0;
  let lat = 0;
  for (const p of pts) {
    lon += p[0] ?? 0;
    lat += p[1] ?? 0;
  }
  return { lat: Number((lat / pts.length).toFixed(3)), lon: Number((lon / pts.length).toFixed(3)) };
}

export default function FindPage() {
  const bundle = loadBundle();
  const probe = probeTargets(bundle);
  const encoder = probeEncoder(bundle);
  const cardRecords = [...bundle.records.cards.values()];
  const visuals = cardRecords.map((card) => ({ card, visual: buildCardVisual(bundle, card) }));

  const targets: FindTargetInfo[] = (bundle.records.targets ?? []).map((t) => ({
    target_id: t.target_id,
    name: shortName(t.name),
    body: t.body,
    thumb: visuals.find((v) => v.card.target.target_id === t.target_id && v.visual.targetTile !== null)?.visual.targetTile ?? null,
  }));
  // Moon first, then Mars, as elsewhere (Connecting Ridge · Jezero · Arcadia).
  targets.sort((a, b) => Number(b.body === 'moon') - Number(a.body === 'moon'));

  const cards: CardLink[] = visuals.map(({ card, visual }) => ({ card_id: card.card_id, target_id: card.target.target_id, earth_name: visual.earthName }));

  const examples: FindExample[] = [];
  for (const { card } of visuals) {
    const name = earthRegionName(card.earth.search_region_id ?? card.earth.earth_window_id);
    if (examples.some((e) => e.name === name)) continue;
    const c = centre(card.earth.polygon.ring_lonlat);
    if (c !== null) examples.push({ name, lat: c.lat, lon: c.lon, card_id: card.card_id });
  }

  return (
    <Suspense fallback={<p className="m-6">Loading the find flow…</p>}>
      <FindClient
        fingerprints={probe.fingerprints}
        targetsReason={probe.reason}
        bundleVersion={bundle.manifest.bundle_version}
        encoder={encoder.encoder}
        encoderReason={encoder.encoder === null ? encoder.reason : null}
        targets={targets}
        examples={examples}
        cards={cards}
      />
    </Suspense>
  );
}
