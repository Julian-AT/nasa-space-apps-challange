// /site (PRD 10): the evidence cards the bundle holds, or an honest empty state.
import type { Metadata } from 'next';
import Link from 'next/link';

import { PageHeader } from '@/components/page-header';
import { Badge } from '@/components/ui/badge';
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

import { loadBundle } from '../../lib/bundle.ts';
import { SITE_TITLE } from '../../lib/shell.ts';

export const dynamic = 'error';

export const metadata: Metadata = { title: `Evidence cards · ${SITE_TITLE}` };

export default function SiteIndex() {
  const { records, manifest } = loadBundle();
  const cards = [...records.cards.values()];
  return (
    <>
      <PageHeader
        eyebrow={`Bundle ${manifest.bundle_version}`}
        title="Evidence cards"
        lead="Each card compares one lunar or Mars window with an Earth window, with evidence labels and its limits."
      />
      {cards.length === 0 ? (
        <p className="mt-8 rounded-xl border border-dashed border-border p-6 text-muted-foreground">
          This bundle contains no evidence cards.
        </p>
      ) : (
        <ul className="mt-8 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {cards.map((card, i) => (
            <li key={card.card_id}>
              <Link href={`/site/${card.card_id}`} className="block rounded-xl focus-visible:ring-2 focus-visible:ring-ring">
                <Card className="h-full transition-colors hover:border-primary">
                  <CardHeader>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm text-primary">{String(i + 1).padStart(2, '0')}</span>
                      <Badge>Mode {card.shipping_mode}</Badge>
                      {card.mock ? <Badge variant="outline">MOCK</Badge> : null}
                    </div>
                    <CardTitle className="font-serif text-2xl font-normal">{card.title}</CardTitle>
                    <CardDescription>
                      {card.target.target_id} · {card.earth.earth_window_id}
                    </CardDescription>
                  </CardHeader>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
