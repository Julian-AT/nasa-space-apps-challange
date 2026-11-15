// /site/[cardId] (PRD 10, T7B-01): one evidence card from the bundle's cards/<card_id>.json.
// Statically generated for exactly the cards the bundle holds; any other id is a 404. With no
// cards in the bundle the list is empty and every id is a 404 (dynamicParams = false).
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { buildCardVisual } from '../../../components/cards/card-visual.ts';
import { EvidenceCardView } from '../../../components/card/evidence-card.tsx';
import { loadBundle } from '../../../lib/bundle.ts';
import { SITE_TITLE } from '../../../lib/shell.ts';

export const dynamicParams = false;

interface Props {
  readonly params: Promise<{ cardId: string }>;
}

export function generateStaticParams(): { cardId: string }[] {
  return [...loadBundle().records.cards.keys()].map((cardId) => ({ cardId }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { cardId } = await params;
  const card = loadBundle().records.cards.get(cardId);
  if (card === undefined) return {};
  return { title: `${card.target.target_id} · Mode ${card.shipping_mode} · ${SITE_TITLE}` };
}

export default async function CardPage({ params }: Props) {
  const { cardId } = await params;
  const bundle = loadBundle();
  const card = bundle.records.cards.get(cardId);
  if (card === undefined) notFound();
  const g5 = bundle.validation.find((g) => g.gate_id === 'G5');
  return (
    <EvidenceCardView
      card={card}
      bundleVersion={bundle.manifest.bundle_version}
      g5Gate={g5?.status ?? null}
      figures={bundle.records.figures}
      visual={buildCardVisual(bundle, card)}
    />
  );
}
