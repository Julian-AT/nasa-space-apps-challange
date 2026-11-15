// /pitch-preview/benchmarks: a standalone preview of the pitch deck's benchmark figures. Values are
// read at build time from the release bundle and paper/generated/numbers.tex; nothing is typed in.
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import type { Metadata } from 'next';

import { buildBenchData, FigBenchmarks } from '@/components/pitch/fig-benchmarks';
import { loadBundle } from '@/lib/bundle';
import { SITE_TITLE } from '@/lib/shell';

export const metadata: Metadata = { title: `Benchmarks preview · ${SITE_TITLE}`, robots: { index: false } };

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

export default function BenchmarksPreviewPage() {
  const data = buildBenchData(loadBundle(), readNumbersTex());
  return (
    <div style={{ background: '#F9F8F5', padding: '56px 48px', minHeight: '100vh' }}>
      <FigBenchmarks data={data} />
    </div>
  );
}
