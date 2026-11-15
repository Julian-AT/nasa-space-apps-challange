// /site (PRD 10): the evidence cards the bundle holds, as an atlas of target/Earth pairs, or an
// honest empty state. Every number on a tile is read from the card record.
import type { Metadata } from 'next';

import { PageHeader } from '@/components/page-header';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';

import { CardAtlas, type AtlasItem, type AtlasSegment } from '../../components/cards/card-atlas.tsx';
import { CardTile } from '../../components/cards/card-tile.tsx';
import { buildCardVisual } from '../../components/cards/card-visual.ts';
import { MODE_TITLES } from '../../components/mode-banner.tsx';
import { loadBundle } from '../../lib/bundle.ts';
import { SITE_TITLE } from '../../lib/shell.ts';

export const dynamic = 'error';

export const metadata: Metadata = { title: `Evidence cards · ${SITE_TITLE}` };

export default function SiteIndex() {
  const bundle = loadBundle();
  const { records, manifest } = bundle;
  const cards = [...records.cards.values()];
  const segments: AtlasSegment[] = [];
  const items: AtlasItem[] = cards.map((card) => {
    const visual = buildCardVisual(bundle, card);
    if (!segments.some((s) => s.id === card.target.target_id)) {
      segments.push({ id: card.target.target_id, label: visual.targetName, body: visual.body });
    }
    return { cardId: card.card_id, segment: card.target.target_id, body: visual.body, node: <CardTile card={card} visual={visual} /> };
  });
  // Moon targets first, then Mars.
  segments.sort((a, b) => Number(b.body === 'moon') - Number(a.body === 'moon'));
  const modes = [...new Set(cards.map((c) => c.shipping_mode))];
  return (
    <>
      <PageHeader
        title="Evidence cards"
        lead="Each card pairs a lunar or Martian terrain window with its closest Earth analogue. Lower distances mean more similar terrain."
      />
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {modes.map((m) => (
          <Badge key={m} variant="outline" data-slot="mode-pill">Mode {m} · {m === 'C' ? 'Terrain fingerprint' : MODE_TITLES[m]}</Badge>
        ))}
        <span>Bundle {manifest.bundle_version}</span>
      </div>
      {cards.length === 0 ? (
        <Card className="mt-8 border-dashed">
          <CardContent className="text-muted-foreground">This bundle contains no evidence cards.</CardContent>
        </Card>
      ) : (
        <div className="mt-8">
          <CardAtlas segments={segments} items={items} />
        </div>
      )}
    </>
  );
}
