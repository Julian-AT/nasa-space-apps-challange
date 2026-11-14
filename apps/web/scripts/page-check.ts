// What make web-smoke asserts about the HTML of / (PRD 8.4, 8.6): the red mock banner exactly when
// the bundle is mock, the shipping-mode banner once, the bundle and schema versions in the footer,
// each as the whole text of its element (and of a banner's own container and every live region
// around it) wherever the layout puts it, and one <main> that shows only the title and HOME_LINE,
// so no numbers, targets or claims.
// The layout's skip link and navigation sit outside <main>. Only the rendered markup counts:
// <script> elements, which hold the RSC payload, are removed first.
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

/** Elements that never have content or an end tag (HTML Living Standard, void elements). */
const VOID_ELEMENTS: ReadonlySet<string> = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr',
]);

/**
 * WAI-ARIA 1.2 live region roles. Assistive technology announces a live region as one message,
 * so a banner inside one reads as the region's whole text.
 */
const LIVE_ROLES: ReadonlySet<string> = new Set(['alert', 'log', 'marquee', 'status', 'timer']);

/** An attribute's value in a start tag, quoted or not, or undefined when the tag lacks it. */
function attribute(tag: string, name: string): string | undefined {
  const match = new RegExp(`\\s${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s"'=<>\`]+))`, 'i').exec(tag);
  return match === null ? undefined : (match[1] ?? match[2] ?? match[3] ?? '');
}

/** Whether a start tag opens a live region: a live role among its roles, or aria-live polite or assertive. */
function isLiveRegion(tag: string): boolean {
  const roles = (attribute(tag, 'role') ?? '').toLowerCase().split(/\s+/);
  const live = (attribute(tag, 'aria-live') ?? '').trim().toLowerCase();
  return roles.some((role) => LIVE_ROLES.has(role)) || live === 'polite' || live === 'assertive';
}

/** The whole text of an element, nested elements included. */
interface Extent {
  full: string;
}

interface Block extends Extent {
  /** The text directly in the block, inline elements included and nested blocks left out. */
  own: string;
  readonly parent: Block | undefined;
  readonly children: Block[];
  /** Every live region the block sits in, on a block or an inline element, itself included. */
  readonly regions: readonly Extent[];
}

/** An open element: a block, a live region, both, or neither (a plain inline element). */
interface OpenElement {
  readonly name: string;
  readonly block: Block | undefined;
  readonly region: Extent | undefined;
}

/**
 * Every block element in the markup, with its text as a reader sees it, whitespace collapsed.
 * All elements are tracked, so an end tag closes its own element, but only blocks split the text.
 */
function blockTree(markup: string): Block[] {
  const open: OpenElement[] = [];
  const blocks: Block[] = [];
  const extents: Extent[] = [];
  const innermostBlock = () => open.findLast((entry) => entry.block !== undefined)?.block;
  const add = (text: string) => {
    const filled = new Set<Extent>();
    for (const { block, region } of open) {
      if (block !== undefined) filled.add(block);
      if (region !== undefined) filled.add(region);
    }
    for (const extent of filled) extent.full += text;
    const innermost = innermostBlock();
    if (innermost !== undefined) innermost.own += text;
  };
  // With one capturing group, split puts the text between tags at even indexes, the tags at odd ones.
  markup.split(/(<[^>]*>)/).forEach((token, index) => {
    if (index % 2 === 0) {
      add(decodeEntities(token));
      return;
    }
    const tag = /^<(\/?)([a-z][a-z0-9-]*)/i.exec(token);
    const name = tag?.[2]?.toLowerCase();
    if (tag === null || name === undefined) return;
    const isBlock = BLOCK_ELEMENTS.has(name);
    if (isBlock) add(' ');
    if (tag[1] === '/') {
      const at = open.map((entry) => entry.name).lastIndexOf(name);
      if (at >= 0) open.splice(at);
      return;
    }
    if (VOID_ELEMENTS.has(name) || token.endsWith('/>')) return;
    const live = isLiveRegion(token);
    const around = open.flatMap((entry) => (entry.region === undefined ? [] : [entry.region]));
    let block: Block | undefined;
    if (isBlock) {
      const parent = innermostBlock();
      const regions: Extent[] = [...around];
      block = { own: '', full: '', parent, children: [], regions };
      if (live) regions.push(block);
      parent?.children.push(block);
      blocks.push(block);
      extents.push(block);
    }
    let region: Extent | undefined;
    if (live) {
      region = block ?? { full: '' };
      if (block === undefined) extents.push(region);
    }
    open.push({ name, block, region });
  });
  const collapse = (text: string) => text.replace(/\s+/g, ' ').trim();
  for (const extent of extents) extent.full = collapse(extent.full);
  for (const block of blocks) block.own = collapse(block.own);
  return blocks;
}

/** The block's only child block that holds text, or undefined when it has none or several. */
function soleTextChild(block: Block): Block | undefined {
  const withText = block.children.filter((child) => child.full !== '');
  return withText.length === 1 ? withText[0] : undefined;
}

/**
 * The elements that are part of the banner held by `holder`. One is its container: walking out
 * from the holder while the parent holds no other block with text, so text the parent adds beside
 * the banner belongs to it. A parent that holds several blocks with text, as the header holds both
 * banners and the navigation, groups them and is part of none. The others are every live region
 * around the holder, nearest to outermost, on a block or an inline element: each is announced as
 * one message, so each must be the banner alone.
 */
function bannerParts(holder: Block): Extent[] {
  let container = holder;
  let parent = holder.parent;
  while (parent !== undefined && soleTextChild(parent) === container) {
    container = parent;
    parent = parent.parent;
  }
  return [container, ...holder.regions.filter((region) => region !== container)];
}

/**
 * A banner is held by the block whose own text holds its first words (the marker). The banner's
 * container and every live region around it (bannerParts) must each read the banner and nothing
 * else, inline and nested elements included.
 */
function bannerProblem(blocks: readonly Block[], marker: string, banner: string, name: string): string | null {
  const holders = blocks.filter((block) => block.own.includes(marker));
  if (holders.length === 0) return `the ${name} "${banner}" is missing`;
  const wrong = holders.flatMap(bannerParts).find((block) => block.full !== banner);
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
// element or in a container of the banner's own, fails, as it failed the T0-05 check of the whole
// page. Text outside <main> other than these is the layout's chrome and is not checked.
export function pageProblems(html: string, bundle: Bundle): string[] {
  const expected = shellText(bundle);
  const markup = renderedMarkup(html);
  const text = visibleText(html);
  const blocks = blockTree(markup);
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
