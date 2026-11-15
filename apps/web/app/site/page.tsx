// /site (PRD 10): the evidence cards the bundle holds, as an atlas of target/Earth pairs, or an
// honest empty state. Every number on a tile is read from the card record.
import type { Metadata } from 'next';

import { PageHeader } from '@/components/page-header';

import { BODY_COLOURS } from '../../components/cards/card-geo.ts';
import { CardAtlas, type AtlasItem, type AtlasSegment } from '../../components/cards/card-atlas.tsx';
import { CardTile } from '../../components/cards/card-tile.tsx';
import { buildCardVisual } from '../../components/cards/card-visual.ts';
import { loadBundle } from '../../lib/bundle.ts';
import { SITE_TITLE } from '../../lib/shell.ts';

export const dynamic = 'error';

export const metadata: Metadata = { title: `Evidence cards · ${SITE_TITLE}` };

export default function SiteIndex() {
  const bundle = loadBundle();
  const { records, manifest } = bundle;
  const cards = [...records.cards.values()];
  const segments: AtlasSegment[] = [];
  const items: AtlasItem[] = cards.map((card, i) => {
    const visual = buildCardVisual(bundle, card);
    if (!segments.some((s) => s.id === card.target.target_id)) {
      segments.push({ id: card.target.target_id, label: visual.targetName, colour: BODY_COLOURS[visual.body] });
    }
    return { cardId: card.card_id, segment: card.target.target_id, node: <CardTile card={card} visual={visual} index={i} /> };
  });
  // Moon first, then Mars targets, as the filter reads Moon · Jezero · Arcadia.
  segments.sort((a, b) => Number(b.colour === BODY_COLOURS.moon) - Number(a.colour === BODY_COLOURS.moon));
  return (
    <>
      <PageHeader
        eyebrow={`Bundle ${manifest.bundle_version}`}
        title="Evidence cards"
        lead="Each card pairs one lunar or Mars window with an Earth window, with evidence labels and its limits. The outline marks the 5 km window on the bundle hillshade."
      />
      {cards.length === 0 ? (
        <p className="mt-8 rounded-xl border border-dashed border-border p-6 text-muted-foreground">
          This bundle contains no evidence cards.
        </p>
      ) : (
        <div className="mt-8">
          <CardAtlas segments={segments} items={items} />
        </div>
      )}
    </>
  );
}
