// make web-smoke fetches / from next start and checks the rendered markup, not the RSC payload
// in <script> tags, against the pulled bundle.
import { BundleManifestSchema, ValidationSchema } from '@terraluna/schema';
import { describe, expect, it } from 'vitest';

import type { Bundle } from '../lib/bundle.ts';
import { HOME_LINE, pageProblems, PRD4_TITLES } from '../scripts/page-check.ts';
import { ALL_GATES_NOT_RUN, manifestFor, toBytes } from './helpers.ts';
import { prd4Titles } from './prd.ts';

function bundle(fields: Record<string, unknown>): Bundle {
  return {
    manifest: BundleManifestSchema.parse(manifestFor(toBytes(ALL_GATES_NOT_RUN), fields)),
    validation: ValidationSchema.parse(ALL_GATES_NOT_RUN),
  };
}

const MOCK = bundle({ mock: true, shipping_mode: null, bundle_version: 'mock-0' });
const REAL = bundle({ mock: false, shipping_mode: 'B', bundle_version: 'v1' });

function page(body: string): string {
  return `<!DOCTYPE html><html lang="en"><head><title>Terraluna</title></head><body>${body}<script>self.__next_f.push([1,"Mock bundle — placeholder data, not results Shipping mode: C"])</script></body></html>`;
}

// The T7-03 layout: skip link, banners and navigation in the header, the page in <main>, the footer.
const CHROME_BEFORE =
  '<a href="#main">Skip to content</a><header><p role="status">Mock bundle — placeholder data, not results</p><p>Shipping mode: not chosen (G0 <!-- -->NOT_RUN<!-- -->)</p><nav aria-label="Primary"><ul><li><a href="/" aria-current="page">Home</a></li><li><a href="/explore/moon">Moon</a></li></ul></nav></header>';
const MAIN = `<main id="main"><h1>Terraluna</h1><p>${HOME_LINE}</p></main>`;
const FOOTER = '<footer>Bundle version: <!-- -->mock-0<!-- --> · Schema 1.0.0</footer>';

const MOCK_PAGE = page(`${CHROME_BEFORE}${MAIN}${FOOTER}`);

const REAL_PAGE = page(
  `<header><p>Shipping mode: B — Terrain analog candidates for Connecting Ridge, with radar context</p></header><main><h1>Terraluna</h1><p>${HOME_LINE}</p></main><footer>Bundle version: v1 · Schema 1.0.0</footer>`,
);

describe('pageProblems', () => {
  it('accepts a page with the banner, the shipping-mode line, the footer version and <main>', () => {
    expect(pageProblems(MOCK_PAGE, MOCK)).toStrictEqual([]);
    expect(pageProblems(REAL_PAGE, REAL)).toStrictEqual([]);
  });

  it('accepts layout chrome such as a skip link and navigation outside <main>', () => {
    const html = MOCK_PAGE.replace('</ul></nav>', '<li><a href="/data">Data</a></li></ul></nav>');
    expect(pageProblems(html, MOCK)).toStrictEqual([]);
  });

  it('reports a missing mock banner, even when the RSC payload holds its text', () => {
    const html = MOCK_PAGE.replace('<p role="status">Mock bundle — placeholder data, not results</p>', '');
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/banner/);
  });

  it('reports a mock banner on a bundle that is not mock', () => {
    const html = REAL_PAGE.replace('<header>', '<header><p>Mock bundle — placeholder data, not results</p>');
    expect(pageProblems(html, REAL).join('\n')).toMatch(/banner/);
  });

  it('reports a wrong shipping-mode line', () => {
    const html = MOCK_PAGE.replace('NOT_RUN', 'PASS');
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/Shipping mode: not chosen \(G0 NOT_RUN\)/);
  });

  it('reports a second shipping-mode line, which could contradict the first', () => {
    const html = MOCK_PAGE.replace('</header>', '<p>Shipping mode: C</p></header>');
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/once/);
  });

  it('reports a bundle version that is not in the footer', () => {
    const html = MOCK_PAGE.replace(FOOTER, '<p>Bundle version: mock-0</p>');
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/footer/);
  });

  // The T0-05 check compared the whole page, so a value that only starts with the expected one
  // failed it. Checking each protected text on its own must keep that.
  it.each([
    { name: 'v10 for v1', html: REAL_PAGE.replace('Bundle version: v1 ·', 'Bundle version: v10 ·'), shown: REAL },
    { name: 'mock-01 for mock-0', html: MOCK_PAGE.replace('mock-0<!-- -->', 'mock-01<!-- -->'), shown: MOCK },
    { name: 'no schema version', html: MOCK_PAGE.replace(' · Schema 1.0.0', ''), shown: MOCK },
    { name: 'schema 1.0.01 for 1.0.0', html: MOCK_PAGE.replace('Schema 1.0.0', 'Schema 1.0.01'), shown: MOCK },
    { name: 'words after the versions', html: MOCK_PAGE.replace('1.0.0</footer>', '1.0.0 (latest)</footer>'), shown: MOCK },
  ])('reports a footer that is not exactly the bundle and schema versions ($name)', ({ html, shown }) => {
    expect(pageProblems(html, shown).join('\n')).toMatch(/footer/);
  });

  it.each([
    { name: 'mode BA for mode B', html: REAL_PAGE.replace('Shipping mode: B —', 'Shipping mode: BA —'), shown: REAL },
    {
      name: 'mode B without its PRD 4 title',
      html: REAL_PAGE.replace(' — Terrain analog candidates for Connecting Ridge, with radar context', ''),
      shown: REAL,
    },
    {
      name: 'the mode A title on a mode B bundle',
      html: REAL_PAGE.replace(
        'Terrain analog candidates for Connecting Ridge, with radar context',
        'Radar-scattering analog candidates for Connecting Ridge',
      ),
      shown: REAL,
    },
    {
      name: 'a mode title while no mode is chosen',
      html: MOCK_PAGE.replace(
        'NOT_RUN<!-- -->)',
        'NOT_RUN<!-- -->) — Terrain analog pilot for Connecting Ridge; radar comparison not completed',
      ),
      shown: MOCK,
    },
    { name: 'words after the G0 status', html: MOCK_PAGE.replace('NOT_RUN<!-- -->)', 'NOT_RUN<!-- -->) so far'), shown: MOCK },
  ])('reports a shipping-mode banner that is not exactly the bundle mode ($name)', ({ html, shown }) => {
    expect(pageProblems(html, shown).join('\n')).toMatch(/shipping-mode banner/);
  });

  it('reports a mock banner with words added', () => {
    const html = MOCK_PAGE.replace('not results</p>', 'not results yet</p>');
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/mock banner/);
  });

  // A banner is its whole element, so words added inside it fail in a child element as they do
  // in plain text (codex-r2-1). The T0-05 check of the whole page rejected all of these.
  const MOCK_P = '<p role="status">Mock bundle — placeholder data, not results</p>';
  const MODE_B = 'Shipping mode: B — Terrain analog candidates for Connecting Ridge, with radar context';

  it.each([
    { name: 'words in a span after its text', html: MOCK_PAGE.replace('not results</p>', 'not results<span> yet</span></p>') },
    {
      name: 'its text in one span and words in the next',
      html: MOCK_PAGE.replace(
        MOCK_P,
        '<p role="status"><span>Mock bundle — placeholder data, not results</span><span> yet</span></p>',
      ),
    },
    {
      name: 'its text in bold, then plain words',
      html: MOCK_PAGE.replace(
        MOCK_P,
        '<p role="status"><b>Mock bundle — placeholder data, not results</b>, except the radar match</p>',
      ),
    },
    {
      name: 'a block nested inside it',
      html: MOCK_PAGE.replace('not results</p>', 'not results<div>Radar match passed</div></p>'),
    },
  ])('reports a mock banner with words added inside its element ($name)', ({ html }) => {
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/mock banner/);
  });

  it.each([
    {
      name: 'words in a span after its text',
      html: REAL_PAGE.replace('radar context</p>', 'radar context<span>; radar match passed</span></p>'),
      shown: REAL,
    },
    {
      name: 'its text in bold, then plain words',
      html: REAL_PAGE.replace(`<p>${MODE_B}</p>`, `<p><strong>${MODE_B}</strong> (radar match passed)</p>`),
      shown: REAL,
    },
    {
      name: 'a block nested inside it',
      html: REAL_PAGE.replace('radar context</p>', 'radar context<div>Radar match passed</div></p>'),
      shown: REAL,
    },
    {
      name: 'a mode title in a span while no mode is chosen',
      html: MOCK_PAGE.replace(
        'NOT_RUN<!-- -->)</p>',
        'NOT_RUN<!-- -->)<span> — Radar-scattering analog candidates for Connecting Ridge</span></p>',
      ),
      shown: MOCK,
    },
  ])('reports a shipping-mode banner with words added inside its element ($name)', ({ html, shown }) => {
    expect(pageProblems(html, shown).join('\n')).toMatch(/shipping-mode banner/);
  });

  it('reports a footer with words added in a child element', () => {
    const html = MOCK_PAGE.replace('1.0.0</footer>', '1.0.0<span> (latest)</span></footer>');
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/footer/);
  });

  it('accepts a banner split by inline markup when it reads exactly the same', () => {
    const html = REAL_PAGE.replace('Shipping mode: B —', 'Shipping mode: <strong>B</strong> —');
    expect(pageProblems(html, REAL)).toStrictEqual([]);
  });

  it('expects the PRD 4 title wording after the shipping-mode line, word for word', () => {
    expect(PRD4_TITLES).toStrictEqual(prd4Titles());
  });

  it('reports a page without a <main> landmark', () => {
    const html = MOCK_PAGE.replace(MAIN, `<div><h1>Terraluna</h1><p>${HOME_LINE}</p></div>`);
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/<main>/);
  });

  it('reports more than one <main>', () => {
    const html = MOCK_PAGE.replace(MAIN, `${MAIN}<main><p>${HOME_LINE}</p></main>`);
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/<main>/);
  });

  it('reports a <main> without the factual line', () => {
    const html = MOCK_PAGE.replace(`<p>${HOME_LINE}</p>`, '');
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/only/);
  });

  it.each([
    { name: 'a number', extra: '<p>CPR 3.2</p>' },
    { name: 'a target', extra: '<p>moon-cr-site01-w000</p>' },
    { name: 'a claim', extra: '<p>Best Earth analog found</p>' },
    { name: 'a banner moved into it', extra: '<p>Mock bundle — placeholder data, not results</p>' },
  ])('reports $name in <main> beyond the title and the factual line', ({ extra }) => {
    const html = MOCK_PAGE.replace('<h1>Terraluna</h1>', `<h1>Terraluna</h1>${extra}`);
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/<main> must show only/);
  });

  it('states no numbers, targets or claims in the factual line', () => {
    expect(HOME_LINE).toBe('These pages show records from the bundle named in the footer.');
    expect(HOME_LINE).not.toMatch(/\d/);
  });
});
