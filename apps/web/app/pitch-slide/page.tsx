// /pitch-slide: one 1920x1080 summary slide shown after the pitch and exported as a single-page PDF.
// Every number is read at build time from the release bundle and the paper's generated numbers.tex
// through the pitch data builders; a value that cannot be read renders as Unavailable.
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import type { Metadata } from 'next';
import localFont from 'next/font/local';
import QRCode from 'qrcode';

import { OneSlide, type OneSlideData } from '@/components/pitch/one-slide';
import { buildBenchData } from '@/components/pitch/fig-benchmarks';
import { buildResultsData } from '@/components/pitch/fig-results';
import { buildPitchData } from '@/components/pitch/pitch-data';
import { loadBundle } from '@/lib/bundle';
import { SITE_TITLE } from '@/lib/shell';

const sans = localFont({ src: '../../public/fonts/dm-sans-variable.woff2', variable: '--pitch-sans', weight: '100 1000', display: 'swap' });
const mono = localFont({ src: '../../public/fonts/dm-mono-regular.woff2', variable: '--pitch-mono', weight: '400', display: 'swap' });

export const metadata: Metadata = { title: `Summary · ${SITE_TITLE}` };

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

export default async function PitchSlidePage() {
  const bundle = loadBundle();
  const pitch = buildPitchData(bundle);
  const bench = buildBenchData(bundle, readNumbersTex());
  const results = buildResultsData(bundle);

  // best Jezero match: the lowest relief distance over Jezero's cards (buildBenchData)
  const jez = bench.relief.targets.find((t) => t.targetId === 'mars-jezero-w000') ?? null;
  const jezRow = results.groups.find((g) => g.targetId === 'mars-jezero-w000')?.rows.find((r) => r.best) ?? null;
  const params = bench.parameters ?? pitch.model.parameters;

  const data: OneSlideData = {
    bundleVersion: results.bundleVersion,
    mock: results.mock,
    shippingModes: results.shippingModes,
    jezeroBest: jez?.best ?? null,
    jezeroEarth: jezRow?.earthPlace ?? jez?.earthName ?? null,
    jezeroLabel: jezRow?.evidenceLabel ?? null,
    cardCount: results.cardCount,
    parameters: params,
    parity: bench.parity.minCosineText,
    parityTolerance: bench.parity.tolerance,
  };

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
  return <OneSlide data={data} qr={qr} fonts={`${sans.variable} ${mono.variable}`} />;
}
