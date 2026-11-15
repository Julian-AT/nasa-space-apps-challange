// /pitch: a two-minute technical pitch as keyboard-driven slides. All numbers come from the release
// bundle and the paper's generated numbers (components/pitch/pitch-data.ts), read at build time.
import type { Metadata } from 'next';

import { Deck } from '@/components/pitch/deck';
import { buildPitchData } from '@/components/pitch/pitch-data';
import { loadBundle } from '@/lib/bundle';
import { SITE_TITLE } from '@/lib/shell';

export const metadata: Metadata = { title: `Pitch · ${SITE_TITLE}` };

export default function PitchPage() {
  const data = buildPitchData(loadBundle());
  return <Deck data={data} />;
}
