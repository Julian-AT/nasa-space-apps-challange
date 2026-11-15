// Build-time read of the released paper (PRD 8.6, D35): apps/web/public/paper/main.pdf with its
// abstract.txt and paper.bib. Absent files stay absent; there is no placeholder.
import { existsSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

export interface PaperFiles {
  /** Public URL of the PDF, or null when main.pdf does not exist. */
  readonly pdfHref: string | null;
  readonly pdfBytes: number | null;
  readonly abstract: string | null;
  readonly bibtex: string | null;
}

function textOrNull(path: string): string | null {
  if (!existsSync(path)) return null;
  const text = readFileSync(path, 'utf8').trim();
  return text === '' ? null : text;
}

export function readPaper(publicDir: string): PaperFiles {
  const dir = join(publicDir, 'paper');
  const pdf = join(dir, 'main.pdf');
  const hasPdf = existsSync(pdf) && statSync(pdf).isFile();
  return {
    pdfHref: hasPdf ? '/paper/main.pdf' : null,
    pdfBytes: hasPdf ? statSync(pdf).size : null,
    abstract: textOrNull(join(dir, 'abstract.txt')),
    bibtex: textOrNull(join(dir, 'paper.bib')),
  };
}
