// What make web-smoke asserts about the HTML of / (PRD 8.4, 8.6): the red mock banner exactly when
// the bundle is mock, the shipping-mode banner once, the bundle and schema versions in the footer,
// each as the whole text of its element wherever the layout puts it, and one <main> that shows only
// the title and HOME_LINE, so no numbers, targets or claims. The layout's skip link and navigation
// sit outside <main>. Only the rendered markup counts: <script> elements, which hold the RSC
// payload, are removed first.
import type { Bundle } from '../lib/bundle.ts';
import { shellText, SITE_TITLE } from '../lib/shell.ts';

/** The one line / shows under its title (app/page.tsx): no numbers, targets or claims. */
export const HOME_LINE = 'These pages show records from the bundle named in the footer.';

/**
 * PRD 4 "Title wording" for each mode, word for word. The mode banner (components/mode-banner.tsx)
 * adds it after the shipping-mode line once G0 has chosen a mode. This is the check's own copy;
 * tests/page-check.test.ts compares it with PRD.md.
 */
export const PRD4_TITLES: Readonly<Record<NonNullable<Bundle['manifest']['shipping_mode']>, string>> = {
  A: 'Radar-scattering analog candidates for Connecting Ridge',
  B: 'Terrain analog candidates for Connecting Ridge, with radar context',
  C: 'Terrain analog pilot for Connecting Ridge; radar comparison not completed',
};

const SHIPPING_MODE = 'Shipping mode:';
const MOCK_MARKER = 'Mock bundle';

const ENTITIES: Readonly<Record<string, string>> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
};

function decodeEntities(text: string): string {
  return text.replace(/&(#x[0-9a-f]+|#[0-9]+|[a-z]+);/gi, (entity: string, name: string) => {
    if (name.startsWith('#x') || name.startsWith('#X')) {
      return String.fromCodePoint(Number.parseInt(name.slice(2), 16));
    }
    if (name.startsWith('#')) return String.fromCodePoint(Number.parseInt(name.slice(1), 10));
    return ENTITIES[name.toLowerCase()] ?? entity;
  });
}

/** The text a reader sees: no scripts, styles or comments; tags become spaces. */
export function visibleText(html: string): string {
  const markup = renderedMarkup(html).replace(/<[^>]*>/g, ' ');
  return decodeEntities(markup).replace(/\s+/g, ' ').trim();
}

/** The markup without scripts, styles and comments, which a reader never sees. */
function renderedMarkup(html: string): string {
  return html
    .replace(/<script\b[\s\S]*?<\/script\s*>/gi, ' ')
    .replace(/<style\b[\s\S]*?<\/style\s*>/gi, ' ')
    .replace(/<!--[\s\S]*?-->/g, '');
}

/**
 * Elements a browser lays out as a box of their own by default (HTML Living Standard, Rendering).
 * Every other element, such as a, span, b or strong, flows inline with the text around it. An
 * element missing here only joins more text into one block, so the check gets stricter, not weaker.
 */
const BLOCK_ELEMENTS: ReadonlySet<string> = new Set([
  'address', 'article', 'aside', 'blockquote', 'body', 'caption', 'dd', 'details', 'dialog', 'div',
  'dl', 'dt', 'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5',
  'h6', 'head', 'header', 'hgroup', 'hr', 'html', 'legend', 'li', 'main', 'menu', 'nav', 'ol', 'p',
  'pre', 'search', 'section', 'summary', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'title',
  'tr', 'ul',
]);

interface BlockText {
  /** The text directly in the block, inline elements included and nested blocks left out. */
  readonly own: string;
  /** All of the block's text, nested blocks included. */
  readonly full: string;
}

/** The text of each block element in the markup, as a reader sees it, whitespace collapsed. */
function blockTexts(markup: string): BlockText[] {
  const open: { name: string; own: string; full: string }[] = [];
  const blocks: BlockText[] = [];
  const add = (text: string) => {
    for (const block of open) block.full += text;
    const innermost = open.at(-1);
    if (innermost !== undefined) innermost.own += text;
  };
  const collapse = (text: string) => text.replace(/\s+/g, ' ').trim();
  const close = (block: { own: string; full: string }) => {
    blocks.push({ own: collapse(block.own), full: collapse(block.full) });
  };
  // With one capturing group, split puts the text between tags at even indexes, the tags at odd ones.
  markup.split(/(<[^>]*>)/).forEach((token, index) => {
    if (index % 2 === 0) {
      add(decodeEntities(token));
      return;
    }
    const tag = /^<(\/?)([a-z][a-z0-9-]*)/i.exec(token);
    const name = tag?.[2]?.toLowerCase();
    if (tag === null || name === undefined || !BLOCK_ELEMENTS.has(name)) return;
    add(' ');
    if (tag[1] === '/') {
      const at = open.map((block) => block.name).lastIndexOf(name);
      if (at >= 0) open.splice(at).reverse().forEach(close);
    } else if (name !== 'hr' && !token.endsWith('/>')) {
      open.push({ name, own: '', full: '' });
    }
  });
  open.reverse().forEach(close);
  return blocks;
}

/**
 * A banner is the block whose own text holds its first words (the marker). That block's whole
 * text, inline and nested elements included, must be the banner and nothing else.
 */
function bannerProblem(blocks: readonly BlockText[], marker: string, banner: string, name: string): string | null {
  const holders = blocks.filter((block) => block.own.includes(marker));
  if (holders.length === 0) return `the ${name} "${banner}" is missing`;
  const wrong = holders.find((block) => block.full !== banner);
  return wrong === undefined ? null : `the ${name} must read "${banner}", but reads "${wrong.full}"`;
}

function occurrences(text: string, part: string): number {
  return text.split(part).length - 1;
}

/** The mode banner in full: the shipping-mode line, then for a chosen mode " — " and its PRD 4 title. */
function modeBanner(bundle: Bundle, shippingMode: string): string {
  const mode = bundle.manifest.shipping_mode;
  return mode === null ? shippingMode : `${shippingMode} — ${PRD4_TITLES[mode]}`;
}

// Each protected text must be whole: a mock banner, mode banner or footer that only starts with
// the expected text (bundle version v10 for v1, mode BA for B), or that has words added inside its
// element, fails, as it failed the T0-05 check of the whole page. Text outside <main> other than
// these is the layout's chrome and is not checked.
export function pageProblems(html: string, bundle: Bundle): string[] {
  const expected = shellText(bundle);
  const markup = renderedMarkup(html);
  const text = visibleText(html);
  const blocks = blockTexts(markup);
  const problems: string[] = [];
  if (expected.mockBanner !== null) {
    const problem = bannerProblem(blocks, MOCK_MARKER, expected.mockBanner, 'mock banner');
    if (problem !== null) problems.push(problem);
  } else if (text.includes(MOCK_MARKER)) {
    problems.push('a mock banner is shown, but the bundle is not mock');
  }
  const banner = modeBanner(bundle, expected.shippingMode);
  const modeProblem = bannerProblem(blocks, SHIPPING_MODE, banner, 'shipping-mode banner');
  if (modeProblem !== null) problems.push(modeProblem);
  const modeLines = occurrences(text, SHIPPING_MODE);
  if (modeLines > 1) {
    problems.push(`"${SHIPPING_MODE}" appears ${String(modeLines)} times; the page must state its mode once`);
  }
  const footer = `${expected.footer} · Schema ${bundle.manifest.schema_version}`;
  const footers = [...markup.matchAll(/<footer\b[^>]*>([\s\S]*?)<\/footer\s*>/gi)];
  if (footers.length === 0) {
    problems.push('the page has no <footer>');
  } else if (!footers.some((match) => visibleText(match[1] ?? '') === footer)) {
    problems.push(`the footer must read "${footer}"`);
  }
  const opened = markup.match(/<main(?=[\s/>])/gi)?.length ?? 0;
  const mains = [...markup.matchAll(/<main(?=[\s/>])[^>]*>([\s\S]*?)<\/main\s*>/gi)];
  const [main] = mains;
  if (opened !== 1 || mains.length !== 1 || main === undefined) {
    problems.push(`the page must have exactly one <main>, but has ${String(opened)}`);
  } else {
    const allowed = `${SITE_TITLE} ${HOME_LINE}`;
    const shown = visibleText(main[1] ?? '');
    if (shown !== allowed) {
      problems.push(`<main> must show only "${allowed}", but shows "${shown}"`);
    }
  }
  return problems;
}
