// /pitch: a two-minute technical pitch as keyboard-driven slides. All numbers come from the release
// bundle and the paper's generated numbers (components/pitch/pitch-data.ts), read at build time.
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import type { Metadata } from 'next';
import localFont from 'next/font/local';
import QRCode from 'qrcode';

import { Deck } from '@/components/pitch/deck';
import { buildBenchData } from '@/components/pitch/fig-benchmarks';
import { buildPitchData } from '@/components/pitch/pitch-data';
import { loadBundle } from '@/lib/bundle';
import { SITE_TITLE } from '@/lib/shell';

const sans = localFont({ src: '../../public/fonts/dm-sans-variable.woff2', variable: '--pitch-sans', weight: '100 1000', display: 'swap' });
const mono = localFont({ src: '../../public/fonts/dm-mono-regular.woff2', variable: '--pitch-mono', weight: '400', display: 'swap' });

export const metadata: Metadata = { title: `Pitch · ${SITE_TITLE}` };

const SITE_URL = 'https://nasa.julianschmidt.cv';

function readNumbersTex(): Map<string, string> {
  const out = new Map<string, string>();
  const candidates = [join(process.cwd(), '../../paper/generated/numbers.tex'), join(process.cwd(), 'paper/generated/numbers.tex')];
  const path = candidates.find((p) => existsSync(p));
  if (path === undefined) return out;
  for (const m of readFileSync(path, 'utf8').matchAll(/\\newcommand\{\\([A-Za-z]+)\}\{([^}]*)\}/g)) {
    const [, name, value] = m;
    if (name !== undefined && value !== undefined) out.set(name, value);
  }
  return out;
}

export default async function PitchPage() {
  const bundle = loadBundle();
  const data = buildPitchData(bundle);
  const bench = buildBenchData(bundle, readNumbersTex());
  const links = [
    { label: 'Live page', url: SITE_URL },
    { label: 'Paper', url: `${SITE_URL}/paper/terraluna-paper.pdf` },
    { label: 'GitHub', url: 'https://github.com/Julian-AT/nasa-space-apps-challange' },
  ];
  const qr = await Promise.all(
    links.map(async (l) => ({
      ...l,
      svg: await QRCode.toString(l.url, { type: 'svg', margin: 0, errorCorrectionLevel: 'M', color: { dark: '#1A1613', light: '#ffffff00' } }).catch(() => ''),
    })),
  );
  return <Deck data={data} bench={bench} curves={bundle.records.curves} qr={qr} fonts={`${sans.variable} ${mono.variable}`} />;
}
