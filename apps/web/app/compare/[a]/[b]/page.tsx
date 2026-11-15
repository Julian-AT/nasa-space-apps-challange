// /compare/[a]/[b] (PRD 8.6, 10): two windows side by side from the release bundle. A pair with an
// evidence card opens the compare lab (maps, nu(L) curves, family tiles); any other bundled pair
// shows the ComparisonRecord view; an unknown pair renders an explicit not-found state.
import type { Metadata } from 'next';

import { CompareView } from '@/components/charts/compare-view';
import { pairsInBundle } from '@/components/charts/compare-data';
import { cardForPair } from '@/components/compare/lab-data';
import { LabPage } from '@/components/compare/lab-page';
import { loadBundle } from '@/lib/bundle';
import { SITE_TITLE } from '@/lib/shell';

export const dynamicParams = true;

export function generateStaticParams(): { a: string; b: string }[] {
  return pairsInBundle(loadBundle().records);
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { a, b } = await params;
  return { title: `Compare ${decodeURIComponent(a)} and ${decodeURIComponent(b)} · ${SITE_TITLE}` };
}

type PageProps = { params: Promise<{ a: string; b: string }> };

export default async function ComparePage({ params }: PageProps) {
  const { a, b } = await params;
  const bundle = loadBundle();
  const ia = decodeURIComponent(a);
  const ib = decodeURIComponent(b);
  const card = cardForPair(bundle, ia, ib);
  if (card !== null) return <LabPage bundle={bundle} card={card} />;
  return (
    <div className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6">
      <CompareView records={bundle.records} a={ia} b={ib} />
    </div>
  );
}
