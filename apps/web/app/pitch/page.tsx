// /pitch: a two-minute technical pitch as keyboard-driven slides. All numbers come from the release
// bundle and the paper's generated numbers (components/pitch/pitch-data.ts), read at build time.
import type { Metadata } from 'next';
import QRCode from 'qrcode';

import { Deck } from '@/components/pitch/deck';
import { buildPitchData } from '@/components/pitch/pitch-data';
import { loadBundle } from '@/lib/bundle';
import { SITE_TITLE } from '@/lib/shell';

export const metadata: Metadata = { title: `Pitch · ${SITE_TITLE}` };

const SITE_URL = 'https://terraluna-lovat.vercel.app';

export default async function PitchPage() {
  const data = buildPitchData(loadBundle());
  const qr = await QRCode.toString(SITE_URL, { type: 'svg', margin: 0, errorCorrectionLevel: 'M', color: { dark: '#18181b', light: '#ffffff00' } }).catch(() => null);
  return <Deck data={data} qr={qr} />;
}
