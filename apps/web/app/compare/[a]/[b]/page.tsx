// /compare/[a]/[b] (PRD 8.6, 10): two windows side by side from the release bundle. Pairs in the
// bundle are prerendered; any other pair renders an explicit not-found state, never a computed value.
import type { Metadata } from 'next';

import { CompareView } from '@/components/charts/compare-view';
import { pairsInBundle } from '@/components/charts/compare-data';
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
  return <CompareView records={loadBundle().records} a={decodeURIComponent(a)} b={decodeURIComponent(b)} />;
}
