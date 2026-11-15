// /paper (T7B-09): Unavailable until main.pdf exists; then link, abstract and BibTeX.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { readPaper } from '../components/paper-data.ts';
import { PaperView } from '../components/paper-view.tsx';
import { tempDir } from './helpers.ts';
import { elements, textOf } from './markup.ts';

function view(dir: string) {
  return renderToStaticMarkup(<PaperView paper={readPaper(dir)} />);
}

describe('/paper', () => {
  it('shows "Paper not yet released" when the file is absent, with no link or image', () => {
    const out = view(tempDir());
    expect(textOf(out)).toContain('Paper not yet released');
    expect(elements(out).filter((e) => e.tag === 'a' || e.tag === 'img')).toHaveLength(0);
    expect(elements(out).filter((e) => e.tag === 'h1')).toHaveLength(1);
  });

  it('links the PDF and shows the abstract and BibTeX from a temporary public dir', () => {
    const dir = tempDir();
    mkdirSync(join(dir, 'paper'));
    writeFileSync(join(dir, 'paper', 'main.pdf'), '%PDF-fake');
    writeFileSync(join(dir, 'paper', 'abstract.txt'), 'A fake abstract.\n');
    writeFileSync(join(dir, 'paper', 'paper.bib'), '@article{fake2000,\n  title={Fake}\n}\n');
    const out = view(dir);
    const link = elements(out).find((e) => e.tag === 'a');
    expect(link?.attributes.get('href')).toBe('/paper/main.pdf');
    expect(textOf(out)).toContain('A fake abstract.');
    expect(textOf(out)).toContain('@article{fake2000,');
    expect(textOf(out)).not.toContain('Paper not yet released');
    expect(elements(out).filter((e) => e.tag === 'img')).toHaveLength(0);
  });

  it('names a missing abstract or BibTeX instead of inventing one', () => {
    const dir = tempDir();
    mkdirSync(join(dir, 'paper'));
    writeFileSync(join(dir, 'paper', 'main.pdf'), '%PDF-fake');
    const text = textOf(view(dir));
    expect(text).toContain('Abstract not provided');
    expect(text).toContain('BibTeX not provided');
  });
});
