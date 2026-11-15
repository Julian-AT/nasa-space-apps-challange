// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// /paper (PRD 8.6, D35): static page. The PDF is read from apps/web/public/paper/ at build time;
// the title and abstract excerpt are read from the paper's own LaTeX sources (paper/main.tex,
// paper/abstract.tex) with numbers resolved from paper/generated/numbers.tex, so nothing is typed.
import type { Metadata } from 'next';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { PaperView, paperBibtex, type PaperInfo } from '../../components/paper-view.tsx';
import { SITE_TITLE } from '../../lib/shell.ts';

export const dynamic = 'error';

export const metadata: Metadata = { title: `Paper · ${SITE_TITLE}` };

const PDF_NAMES = ['terraluna-paper.pdf', 'main.pdf'] as const;

function read(path: string): string | null {
  return existsSync(path) ? readFileSync(path, 'utf8') : null;
}

/** LaTeX to plain text for the few constructs the abstract uses; numbers come from numbers.tex. */
function plain(tex: string, numbers: ReadonlyMap<string, string>): string {
  return tex
    .replace(/\\([A-Za-z]+)\{\}/g, (m, name: string) => numbers.get(name) ?? m)
    .replace(/\$\\nu\(L\)\$/g, 'ν(L)')
    .replace(/\$([^$]*)\$/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
}

function readInfo(): PaperInfo {
  const publicPaper = join(process.cwd(), 'public', 'paper');
  const pdfName = PDF_NAMES.find((n) => existsSync(join(publicPaper, n))) ?? null;
  const pdfPath = pdfName === null ? null : join(publicPaper, pdfName);
  const src = join(process.cwd(), '..', '..', 'paper');
  const numbers = new Map<string, string>();
  for (const m of (read(join(src, 'generated', 'numbers.tex')) ?? '').matchAll(/\\newcommand\{\\([A-Za-z]+)\}\{([^}]*)\}/g)) {
    if (m[1] !== undefined && m[2] !== undefined) numbers.set(m[1], m[2]);
  }
  const title = /\\title\{([^}]*)\}/.exec(read(join(src, 'main.tex')) ?? '')?.[1] ?? null;
  const abstractTex = read(join(src, 'abstract.tex'));
  const abstract = abstractTex === null ? null : plain(abstractTex, numbers);
  // The excerpt is the abstract's first two sentences; skip it if any macro stayed unresolved.
  const excerpt = abstract === null || abstract.includes('\\') ? null : (abstract.match(/[^.]+\./g) ?? []).slice(0, 2).join('').trim();
  return {
    title,
    pdfHref: pdfName === null ? null : `/paper/${pdfName}`,
    pdfBytes: pdfPath === null || !statSync(pdfPath).isFile() ? null : statSync(pdfPath).size,
    abstract: excerpt === null || excerpt === '' ? null : excerpt,
    excerpt: true,
    bibtex: title === null ? null : paperBibtex(title),
  };
}

export default function PaperPage() {
  return <PaperView paper={readInfo()} />;
}
