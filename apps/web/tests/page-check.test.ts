// make web-smoke fetches / from next start and checks the rendered markup, not the RSC payload
// in <script> tags, against the pulled bundle.
import { BundleManifestSchema, ValidationSchema } from '@terraluna/schema';
import { describe, expect, it } from 'vitest';

import type { Bundle } from '../lib/bundle.ts';
import { HOME_LINE, LAYOUT_LABELS, pageProblems, PRD4_TITLES } from '../scripts/page-check.ts';
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

  // The document head is not shown on the page; its title shows in the browser tab. Neither can
  // stand in for a banner (codex-r7-1).
  it.each([
    {
      name: 'the mock banner',
      html: MOCK_PAGE.replace('<title>Terraluna</title>', '<title>Mock bundle — placeholder data, not results</title>').replace(
        '<p role="status">Mock bundle — placeholder data, not results</p>',
        '',
      ),
      shown: MOCK,
      problem: /mock banner .* is missing/,
    },
    {
      name: 'the shipping-mode banner',
      html: REAL_PAGE.replace(
        '<title>Terraluna</title>',
        '<title>Shipping mode: B — Terrain analog candidates for Connecting Ridge, with radar context</title>',
      ).replace('<header><p>Shipping mode: B — Terrain analog candidates for Connecting Ridge, with radar context</p></header>', ''),
      shown: REAL,
      problem: /shipping-mode banner .* is missing/,
    },
    {
      name: 'the mock banner (in a head noscript)',
      html: MOCK_PAGE.replace('</title>', '</title><noscript>Mock bundle — placeholder data, not results</noscript>').replace(
        '<p role="status">Mock bundle — placeholder data, not results</p>',
        '',
      ),
      shown: MOCK,
      problem: /mock banner .* is missing/,
    },
  ])('reports $name missing from the page when only the document head holds its text', ({ html, shown, problem }) => {
    expect(pageProblems(html, shown).join('\n')).toMatch(problem);
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

  // The check counts the lines in the text as a browser shows it, so inline markup inside the
  // words, which a browser joins up, cannot hide a second, complete banner (codex-r6-2).
  it.each([
    { name: 'inline markup inside its first word', line: '<p>Ship<span>ping</span> mode: B — Terrain analog candidates for Connecting Ridge, with radar context</p>' },
    { name: 'its words split across two blocks', line: '<p>Shipping</p><p>mode: C</p>' },
  ])('reports a second shipping-mode line ($name)', ({ line }) => {
    const html = REAL_PAGE.replace('</header>', `${line}</header>`);
    expect(pageProblems(html, REAL).join('\n')).toMatch(/"Shipping mode:" appears 2 times; the page must state its mode once/);
  });

  it('reports a mock banner split by inline markup on a bundle that is not mock', () => {
    const html = REAL_PAGE.replace('<header>', '<header><p>Mo<b>ck</b> bundle — placeholder data, not results</p>');
    expect(pageProblems(html, REAL).join('\n')).toMatch(/a mock banner is shown, but the bundle is not mock/);
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
      html: MOCK_PAGE.replace(MOCK_P, '<div role="status">Mock bundle — placeholder data, not results<div>Radar match passed</div></div>'),
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
      html: REAL_PAGE.replace(`<p>${MODE_B}</p>`, `<div>${MODE_B}<div>Radar match passed</div></div>`),
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

  // A div start tag closes an open p (HTML parsing, "in body" mode), so a browser shows the div's
  // words beside the banner, not in it. The check reads the page the same way and reports them.
  it.each([
    { name: 'the mock banner', html: MOCK_PAGE.replace('not results</p>', 'not results<div>Radar match passed</div></p>'), shown: MOCK },
    { name: 'the mode banner', html: REAL_PAGE.replace('radar context</p>', 'radar context<div>Radar match passed</div></p>'), shown: REAL },
  ])('reports a block written inside the paragraph of $name, which a browser moves out', ({ html, shown }) => {
    expect(pageProblems(html, shown)).toStrictEqual([
      'text outside <main> must be a banner, the footer or a layout label, but "Radar match passed" is none of these',
    ]);
  });

  // A banner may sit in a container of its own, such as a status region around its paragraph. The
  // container is part of the banner, so words added inside it fail too (codex-r3-1).
  const MOCK_TEXT = 'Mock bundle — placeholder data, not results';

  it.each([
    { name: 'a status region around its paragraph', banner: `<div role="status"><p>${MOCK_TEXT}</p></div>` },
    { name: 'a wrapper around the status paragraph', banner: `<div class="bg-mock"><p role="status">${MOCK_TEXT}</p></div>` },
    { name: 'its text split by a nested block', banner: `<div role="status"><p>Mock bundle</p><span> — placeholder data, not results</span></div>` },
  ])('accepts a mock banner nested in a container of its own ($name)', ({ banner }) => {
    expect(pageProblems(MOCK_PAGE.replace(MOCK_P, banner), MOCK)).toStrictEqual([]);
  });

  it.each([
    { name: 'words in a span after its paragraph', banner: `<div role="status"><p>${MOCK_TEXT}</p><span> yet</span></div>` },
    { name: 'words before its paragraph', banner: `<div role="status">Radar match passed. <p>${MOCK_TEXT}</p></div>` },
    { name: 'a second paragraph in its status region', banner: `<div role="status"><p>${MOCK_TEXT}</p><p>Radar match passed</p></div>` },
    {
      name: 'a second paragraph in an aria-live region',
      banner: `<div aria-live="polite"><p>${MOCK_TEXT}</p><p>Radar match passed</p></div>`,
    },
    {
      name: 'words in a wrapper around the status paragraph',
      banner: `<div class="bg-mock"><p role="status">${MOCK_TEXT}</p><span> yet</span></div>`,
    },
    {
      name: 'words two wrappers out',
      banner: `<section><div role="status"><p>${MOCK_TEXT}</p></div> except the radar match</section>`,
    },
  ])('reports a mock banner with words added inside its container ($name)', ({ banner }) => {
    expect(pageProblems(MOCK_PAGE.replace(MOCK_P, banner), MOCK).join('\n')).toMatch(/mock banner/);
  });

  // Every live region around a banner is announced as one message, not only the nearest one, and
  // a live region counts whatever element carries it, inline ones included (codex-r4-1).
  it.each([
    {
      name: 'a status region around the status paragraph and a second paragraph',
      banner: `<div role="status"><p role="status">${MOCK_TEXT}</p><p>Radar match passed</p></div>`,
    },
    {
      name: 'an aria-live region around the status paragraph and a second paragraph',
      banner: `<div aria-live="polite"><p role="status">${MOCK_TEXT}</p><p>Radar match passed</p></div>`,
    },
    {
      name: 'an alert region two blocks out',
      banner: `<section role="alert"><div><p role="status">${MOCK_TEXT}</p></div><p>Radar match passed</p></section>`,
    },
    {
      name: 'an inline status region around its paragraph',
      banner: `<span role="status"><p>${MOCK_TEXT}</p> except the radar match</span>`,
    },
    {
      name: 'an inline aria-live region around the status paragraph',
      banner: `<span aria-live="assertive"><p role="status">${MOCK_TEXT}</p><b> Radar match passed</b></span>`,
    },
    {
      name: 'words after an empty span in an inline status region',
      banner: `<span role="status"><p>${MOCK_TEXT}</p><span class="sep"></span> except the radar match</span>`,
    },
    { name: 'words after an inline status region', banner: `<p><span role="status">${MOCK_TEXT}</span> yet</p>` },
    { name: 'an inline status region alone in the header', banner: `<span role="status">${MOCK_TEXT}</span>` },
  ])('reports a mock banner inside a live region with words added ($name)', ({ banner }) => {
    expect(pageProblems(MOCK_PAGE.replace(MOCK_P, banner), MOCK).join('\n')).toMatch(/mock banner/);
  });

  it.each([
    { name: 'nested status regions', banner: `<div role="status"><p role="status">${MOCK_TEXT}</p></div>` },
    { name: 'an inline status region around its paragraph', banner: `<span role="status"><p>${MOCK_TEXT}</p></span>` },
    { name: 'an inline status region in its paragraph', banner: `<p><span role="status">${MOCK_TEXT}</span></p>` },
    {
      name: 'an inline status region split by inline markup',
      banner: `<p><span role="status"><span>Mock bundle</span> — <b>placeholder data</b>, not results</span></p>`,
    },
  ])('accepts a mock banner in live regions that read exactly the banner ($name)', ({ banner }) => {
    expect(pageProblems(MOCK_PAGE.replace(MOCK_P, banner), MOCK)).toStrictEqual([]);
  });

  it('reports a shipping-mode banner in a status region around a second paragraph', () => {
    const banner = `<div role="status"><p role="status">${MODE_B}</p><p>Radar match passed</p></div>`;
    const html = REAL_PAGE.replace(`<p>${MODE_B}</p>`, banner);
    expect(pageProblems(html, REAL).join('\n')).toMatch(/shipping-mode banner/);
  });

  it.each([
    { name: 'words in a span after its paragraph', banner: `<div><p>${MODE_B}</p><span>; radar match passed</span></div>` },
    {
      name: 'a second paragraph in its status region',
      banner: `<div role="status"><p>${MODE_B}</p><p>Radar match passed</p></div>`,
    },
  ])('reports a shipping-mode banner with words added inside its container ($name)', ({ banner }) => {
    const html = REAL_PAGE.replace(`<p>${MODE_B}</p>`, banner);
    expect(pageProblems(html, REAL).join('\n')).toMatch(/shipping-mode banner/);
  });

  it('accepts a shipping-mode banner nested in a container of its own', () => {
    const html = REAL_PAGE.replace(`<p>${MODE_B}</p>`, `<div><div><p>${MODE_B}</p></div></div>`);
    expect(pageProblems(html, REAL)).toStrictEqual([]);
  });

  // A block that holds several blocks with text, as the header holds both banners and the
  // navigation, groups them and is part of neither banner. Its own text may only be a layout label.
  it('accepts a container that groups the banners with a layout label', () => {
    const html = MOCK_PAGE.replace('<nav aria-label="Primary">', '<a href="/">Home</a><nav aria-label="Primary">');
    expect(pageProblems(html, MOCK)).toStrictEqual([]);
  });

  it('accepts every layout label outside <main>: the skip link, the wordmark and the nine navigation links', () => {
    expect(LAYOUT_LABELS).toStrictEqual([
      'Skip to content',
      'Home',
      'Moon',
      'Mars',
      'Earth',
      'Model',
      'Methods',
      'Data',
      'Paper',
      'API docs',
      'Terraluna',
    ]);
    const items = LAYOUT_LABELS.slice(1).map((label) => `<li><a href="/">${label}</a></li>`).join('');
    const html = MOCK_PAGE.replace(/<ul>[\s\S]*<\/ul>/, `<ul>${items}</ul>`);
    expect(pageProblems(html, MOCK)).toStrictEqual([]);
  });

  // Outside <main>, the T0-05 check of the whole page allowed no text but the banners and the
  // footer. The layout adds only its fixed labels, so any other text there is reported, wherever
  // it sits and whatever wraps it (codex-r5-1).
  it.each([
    {
      name: 'a second paragraph in a wrapper around the status paragraph',
      html: MOCK_PAGE.replace(MOCK_P, `<div class="bg-mock">${MOCK_P}<p>Radar match passed</p></div>`),
      shown: MOCK,
    },
    {
      name: 'a second paragraph in a wrapper around the mode banner',
      html: REAL_PAGE.replace(`<p>${MODE_B}</p>`, `<div class="bg-muted"><p>${MODE_B}</p><p>Radar match passed</p></div>`),
      shown: REAL,
    },
    { name: 'a paragraph in the header', html: MOCK_PAGE.replace('</header>', '<p>Best Earth analog found</p></header>'), shown: MOCK },
    { name: 'a heading in the header', html: REAL_PAGE.replace('<header>', '<header><h2>Radar analog found</h2>'), shown: REAL },
    { name: 'words directly in the body', html: MOCK_PAGE.replace(MAIN, `Radar match passed${MAIN}`), shown: MOCK },
    { name: 'words in a span in the header', html: MOCK_PAGE.replace('<nav', '<span>CPR 3.2</span><nav'), shown: MOCK },
    { name: 'words after the skip link', html: MOCK_PAGE.replace('Skip to content</a>', 'Skip to content</a> (radar match)'), shown: MOCK },
    {
      name: 'words added to a navigation label',
      html: MOCK_PAGE.replace('>Moon</a>', '>Moon: radar match passed</a>'),
      shown: MOCK,
    },
    { name: 'a paragraph between <main> and the footer', html: REAL_PAGE.replace('</main>', '</main><p>moon-cr-site01-w000</p>'), shown: REAL },
    { name: 'words in a hidden element', html: MOCK_PAGE.replace('<header>', '<div hidden>Radar match passed</div><header>'), shown: MOCK },
    { name: 'words after the body', html: REAL_PAGE.replace('</body>', '</body>Radar match passed'), shown: REAL },
    // A browser ignores a head tag inside the body (HTML parsing, "in body" mode) and shows its text.
    { name: 'words in a head tag inside the body', html: MOCK_PAGE.replace('<header>', '<head>Radar match passed</head><header>'), shown: MOCK },
    { name: 'words in a second head after the body', html: REAL_PAGE.replace('</html>', '<head>Radar match passed</head></html>'), shown: REAL },
    // It also ignores html and head start tags nested in the body (codex-r6-1). Only the
    // document's own title is left out, and a title outside the document head is reported.
    {
      name: 'words in a head inside a nested html element',
      html: MOCK_PAGE.replace('<header>', '<html><head>Radar match passed</head></html><header>'),
      shown: MOCK,
    },
    {
      name: 'words in a title inside a nested html element',
      html: MOCK_PAGE.replace('<header>', '<html><head><title>Radar match passed</title></head></html><header>'),
      shown: MOCK,
    },
    // Text after the title or after </html> ends up in the body.
    {
      name: 'words in the document head after its title',
      html: MOCK_PAGE.replace('<title>Terraluna</title>', '<title>Terraluna</title>Radar match passed'),
      shown: MOCK,
    },
    { name: 'words after the html end tag', html: REAL_PAGE.replace('</html>', '</html>Radar match passed'), shown: REAL },
    // Comments end where the HTML tokenizer ends them, and a lone "<" is text.
    { name: 'words after an empty comment', html: MOCK_PAGE.replace('</header>', '<!-->Radar match passed<!-- --></header>'), shown: MOCK },
    { name: 'words after the empty comment <!--->', html: MOCK_PAGE.replace('</header>', '<!--->Radar match passed<!-- --></header>'), shown: MOCK },
    { name: 'words after a comment closed by --!>', html: MOCK_PAGE.replace('</header>', '<!-- a --!>Radar match passed<!-- b --></header>'), shown: MOCK },
    {
      name: 'words between comments that hold script tags',
      html: MOCK_PAGE.replace('</header>', '<!-- <script> -->Radar match passed<!-- </script> --></header>'),
      shown: MOCK,
    },
    { name: 'words after a lone less-than sign', html: MOCK_PAGE.replace('</header>', '<p>< Radar match passed <b></b></p></header>'), shown: MOCK },
  ])('reports text outside <main> that is no banner, footer or layout label ($name)', ({ html, shown }) => {
    expect(pageProblems(html, shown).join('\n')).toMatch(/layout label/);
  });

  it('names the stray text outside <main> it reports', () => {
    const html = MOCK_PAGE.replace(MOCK_P, `<div class="bg-mock">${MOCK_P}<p>Radar match passed</p></div>`);
    expect(pageProblems(html, MOCK)).toStrictEqual([
      'text outside <main> must be a banner, the footer or a layout label, but "Radar match passed" is none of these',
    ]);
  });

  it('does not count the document title in <head> as page text', () => {
    expect(REAL_PAGE).toContain('<head><title>Terraluna</title></head>');
    expect(pageProblems(REAL_PAGE, REAL)).toStrictEqual([]);
  });

  it('reports a footer with words added in a child element', () => {
    const html = MOCK_PAGE.replace('1.0.0</footer>', '1.0.0<span> (latest)</span></footer>');
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/footer/);
  });

  // One correct footer must not hide a second one that states other versions (codex-r5-2).
  it.each([
    { name: 'a second footer with another bundle version', footer: '<footer>Bundle version: v10 · Schema 1.0.0</footer>' },
    { name: 'a second footer with another schema version', footer: '<footer>Bundle version: v1 · Schema 2.0.0</footer>' },
    { name: 'the same footer twice', footer: '<footer>Bundle version: v1 · Schema 1.0.0</footer>' },
    { name: 'an empty second footer', footer: '<footer></footer>' },
  ])('reports more than one <footer> ($name)', ({ footer }) => {
    const html = REAL_PAGE.replace('</footer>', `</footer>${footer}`);
    expect(pageProblems(html, REAL).join('\n')).toMatch(/exactly one <footer>, but has 2/);
  });

  it('reports a footer nested in the footer', () => {
    const html = REAL_PAGE.replace('Schema 1.0.0</footer>', 'Schema 1.0.0<footer>Bundle version: v10</footer></footer>');
    expect(pageProblems(html, REAL).join('\n')).toMatch(/exactly one <footer>, but has 2/);
  });

  it('reports the versions in another element beside the footer', () => {
    const html = REAL_PAGE.replace('<header>', '<header><p>Bundle version: v10 · Schema 1.0.0</p>');
    expect(pageProblems(html, REAL).join('\n')).toMatch(/Bundle version: v10 · Schema 1\.0\.0" is none of these/);
  });

  it('reads character references as a browser does', () => {
    const html = MOCK_PAGE.replace('Mock bundle — placeholder', 'Mock bundle &mdash; placeholder');
    expect(html).toContain('Mock bundle &mdash; placeholder');
    expect(pageProblems(html, MOCK)).toStrictEqual([]);
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

  // Inside <svg>, main and footer tags make SVG elements, not landmarks (HTML parsing, foreign
  // content), so they neither stand in for the page's <main> or footer nor exempt their text.
  it('reports a page whose only main is an SVG element', () => {
    const html = MOCK_PAGE.replace(MAIN, `<svg><main>Terraluna ${HOME_LINE}</main></svg>`);
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/the page has no <main>/);
  });

  it('reports the text of an SVG footer element outside <main>', () => {
    const html = REAL_PAGE.replace('</main>', '</main><svg><footer>Bundle version: v10 · Schema 1.0.0</footer></svg>');
    expect(pageProblems(html, REAL)).toStrictEqual([
      'text outside <main> must be a banner, the footer or a layout label, but "Bundle version: v10 · Schema 1.0.0" is none of these',
    ]);
  });

  it('reports more than one <main>', () => {
    const html = MOCK_PAGE.replace(MAIN, `${MAIN}<main><p>${HOME_LINE}</p></main>`);
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/<main>/);
  });

  it('reports a <main> without the factual line', () => {
    const html = MOCK_PAGE.replace(`<p>${HOME_LINE}</p>`, '');
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/must show/);
  });

  it('accepts a richer <main> that keeps the title and the factual line', () => {
    const html = MOCK_PAGE.replace('<h1>Terraluna</h1>', '<h1>Terraluna</h1><p>Evidence cards: 3</p>');
    expect(pageProblems(html, MOCK)).toStrictEqual([]);
  });

  it.each([
    { name: 'a banner moved into it', extra: '<p>Mock bundle — placeholder data, not results</p>', message: /must not repeat a banner/ },
    { name: 'the shipping-mode line', extra: '<p>Shipping mode: C</p>', message: /must not repeat a banner/ },
    { name: 'a best-analog claim', extra: '<p>Best Earth analog found</p>', message: /stronger than any shipping mode/ },
    { name: 'a verified claim', extra: '<p>A verified analog</p>', message: /stronger than any shipping mode/ },
  ])('reports $name in <main>', ({ extra, message }) => {
    const html = MOCK_PAGE.replace('<h1>Terraluna</h1>', `<h1>Terraluna</h1>${extra}`);
    expect(pageProblems(html, MOCK).join('\n')).toMatch(message);
  });

  it('states no numbers, targets or claims in the factual line', () => {
    expect(HOME_LINE).toBe('These pages show records from the bundle named in the footer.');
    expect(HOME_LINE).not.toMatch(/\d/);
  });
});
