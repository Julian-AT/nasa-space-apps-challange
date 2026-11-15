// What make web-smoke asserts about the HTML of / (PRD 8.4, 8.6): the red mock banner exactly when
// the bundle is mock, the shipping-mode banner once, the bundle and schema versions in the one
// footer, each as the whole text of its element (and of a banner's own container and every live
// region around it) wherever the layout puts it, and one <main> that shows only the title and
// HOME_LINE, so no numbers, targets or claims.
// The only other text outside <main> is the layout's skip link and navigation labels
// (LAYOUT_LABELS). The page is read as a browser reads it: parse5 builds the document tree as the
// HTML Living Standard parser does, so comments, misplaced tags and character references end up
// where a browser puts them. The text of <script> and <style> elements, which hold the RSC payload
// and the CSS, is not page text.
import { defaultTreeAdapter, html as htmlSpec, parse } from 'parse5';
import type { DefaultTreeAdapterTypes } from 'parse5';

import type { Bundle } from '../lib/bundle.ts';
import { shellText, SITE_TITLE } from '../lib/shell.ts';

type Element = DefaultTreeAdapterTypes.Element;
type ParentNode = DefaultTreeAdapterTypes.ParentNode;

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

/**
 * The layout's fixed labels, the only other text allowed outside <main>: the skip link
 * (components/skip-link.tsx), then the primary navigation (components/site-header.tsx). This is
 * the check's own copy; tests/site-layout.test.tsx compares it with the components. Each label
 * must be the whole own text of its block, as each link sits in an li of its own.
 */
export const LAYOUT_LABELS: readonly string[] = [
  'Skip to content',
  'Home',
  'Moon',
  'Mars',
  'Earth',
  'Probe',
  'Model',
  'Methods',
  'Data',
  'Paper',
  'API docs',
  SITE_TITLE,
];

/** Elements whose text the check compares as a whole. */
const COMPARED_ELEMENTS: ReadonlySet<string> = new Set(['main', 'footer']);

/**
 * Whether a block is a title in the document head, the document's title, which a browser shows in
 * its tab, never on the page. The parser keeps one head, the first child of the one html element,
 * and ignores a head tag anywhere else, so a title outside it is page text.
 */
function isDocumentTitle(block: Block): boolean {
  return block.name === 'title' && block.parent !== undefined && isDocumentHead(block.parent);
}

/** Whether a block is the document head: the head that is the child of the one html element. */
function isDocumentHead(block: Block): boolean {
  return block.name === 'head' && block.parent?.name === 'html' && block.parent.parent === undefined;
}

/** Whether a block is the document head or sits in it, such as the document's title. */
function inDocumentHead(block: Block): boolean {
  for (let at: Block | undefined = block; at !== undefined; at = at.parent) {
    if (isDocumentHead(at)) return true;
  }
  return false;
}

const SHIPPING_MODE = 'Shipping mode:';

/** Wording PRD 3 and 4 forbid outside Mode A, and any "best analog" claim in every mode. */
const OVERSTATED = /\bbest\b|\bverified analog|radar[- ]scattering (match|analog)|\bconfirmed\b|\bdiscover/i;
const MOCK_MARKER = 'Mock bundle';

/** Elements whose text is not page text: the RSC payload in <script>, the CSS in <style>. */
const UNSHOWN_ELEMENTS: ReadonlySet<string> = new Set(['script', 'style']);

/**
 * HTML elements a browser lays out as a box of their own by default (HTML Living Standard,
 * Rendering). Every other element, such as a, span, b or strong, and every SVG or MathML element,
 * flows inline with the text around it. An element missing here only joins more text into one
 * block, so the check gets stricter, not weaker.
 */
const BLOCK_ELEMENTS: ReadonlySet<string> = new Set([
  'address', 'article', 'aside', 'blockquote', 'body', 'caption', 'dd', 'details', 'dialog', 'div',
  'dl', 'dt', 'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5',
  'h6', 'head', 'header', 'hgroup', 'hr', 'html', 'legend', 'li', 'main', 'menu', 'nav', 'ol', 'p',
  'pre', 'search', 'section', 'summary', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'title',
  'tr', 'ul',
]);

/**
 * WAI-ARIA 1.2 live region roles. Assistive technology announces a live region as one message,
 * so a banner inside one reads as the region's whole text.
 */
const LIVE_ROLES: ReadonlySet<string> = new Set(['alert', 'log', 'marquee', 'status', 'timer']);

/** An attribute's value, or '' when the element lacks it. The parser lower-cases HTML attribute names. */
function attribute(element: Element, name: string): string {
  return element.attrs.find((attr) => attr.name === name)?.value ?? '';
}

/** Whether an element is a live region: a live role among its roles, or aria-live polite or assertive. */
function isLiveRegion(element: Element): boolean {
  const roles = attribute(element, 'role').toLowerCase().split(/\s+/);
  const live = attribute(element, 'aria-live').trim().toLowerCase();
  return roles.some((role) => LIVE_ROLES.has(role)) || live === 'polite' || live === 'assertive';
}

/** The whole text of an element, nested elements included. */
interface Extent {
  full: string;
}

interface Block extends Extent {
  /** The element name, in lower case. */
  readonly name: string;
  /** The text directly in the block, inline elements included and nested blocks left out. */
  own: string;
  readonly parent: Block | undefined;
  readonly children: Block[];
  /** Every live region the block sits in, on a block or an inline element, itself included. */
  readonly regions: readonly Extent[];
}

/**
 * Every block element of the parsed page, with its text as a reader sees it, whitespace collapsed.
 * Only blocks split the text; inline elements join theirs into the block around them. The content
 * of a <template> counts as text too, as a declarative shadow root shows it.
 */
function blockTree(html: string): Block[] {
  const blocks: Block[] = [];
  const extents: Extent[] = [];
  /** The blocks and live regions around the node the walk is at. */
  const open: Extent[] = [];
  /** The live regions among them, outermost first. */
  const regions: Extent[] = [];
  let innermost: Block | undefined;
  const add = (text: string) => {
    for (const extent of open) extent.full += text;
    if (innermost !== undefined) innermost.own += text;
  };
  const visit = (node: ParentNode) => {
    const children = 'content' in node ? [...node.content.childNodes, ...node.childNodes] : node.childNodes;
    for (const child of children) {
      if (defaultTreeAdapter.isTextNode(child)) add(child.value);
      else if (defaultTreeAdapter.isElementNode(child)) visitElement(child);
    }
  };
  const visitElement = (element: Element) => {
    const name = element.tagName.toLowerCase();
    if (UNSHOWN_ELEMENTS.has(name)) return;
    const live = isLiveRegion(element);
    const isBlock = element.namespaceURI === htmlSpec.NS.HTML && BLOCK_ELEMENTS.has(name);
    const outer = innermost;
    let extent: Extent | undefined;
    if (isBlock) {
      add(' ');
      const blockRegions: Extent[] = [...regions];
      const block: Block = { name, own: '', full: '', parent: outer, children: [], regions: blockRegions };
      if (live) blockRegions.push(block);
      outer?.children.push(block);
      blocks.push(block);
      innermost = block;
      extent = block;
    } else if (live) {
      extent = { full: '' };
    }
    if (extent !== undefined) {
      extents.push(extent);
      open.push(extent);
      if (live) regions.push(extent);
    }
    visit(element);
    if (extent !== undefined) {
      open.pop();
      if (live) regions.pop();
    }
    innermost = outer;
    if (isBlock) add(' ');
  };
  visit(parse(html));
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

interface BannerCheck {
  readonly problem: string | null;
  /** The banner's parts, whose text this check accounts for. */
  readonly parts: readonly Extent[];
}

/**
 * A banner is held by the block whose own text holds its first words (the marker). The document
 * head, its title included, holds none, as a browser shows the title in the tab, not on the page. The banner's container and
 * every live region around it (bannerParts) must each read the banner and nothing else, inline and
 * nested elements included.
 */
function checkBanner(blocks: readonly Block[], marker: string, banner: string, name: string): BannerCheck {
  const holders = blocks.filter((block) => block.own.includes(marker) && !inDocumentHead(block));
  if (holders.length === 0) return { problem: `the ${name} "${banner}" is missing`, parts: [] };
  const parts = holders.flatMap(bannerParts);
  const wrong = parts.find((block) => block.full !== banner);
  const problem = wrong === undefined ? null : `the ${name} must read "${banner}", but reads "${wrong.full}"`;
  return { problem, parts };
}

/**
 * The text outside the elements whose text the check compares as a whole: every block's own text,
 * unless the block is the document's title, in <main>, in a footer or in a banner part, or sits in
 * a live region that is a banner part. Each of these texts must be a layout label.
 */
function strayTexts(blocks: readonly Block[], banners: readonly Extent[], extra: readonly string[]): string[] {
  const compared = new Set<Extent>(banners);
  for (const block of blocks) {
    if (COMPARED_ELEMENTS.has(block.name) || isDocumentTitle(block)) compared.add(block);
  }
  const isCompared = (block: Block): boolean => {
    for (let at: Block | undefined = block; at !== undefined; at = at.parent) {
      if (compared.has(at)) return true;
    }
    return block.regions.some((region) => compared.has(region));
  };
  return blocks
    .filter((block) => block.own !== '' && !isCompared(block) && !LAYOUT_LABELS.includes(block.own) && !extra.includes(block.own))
    .map((block) => block.own);
}

/** Every block nested in a block, at any depth. */
function descendants(block: Block): Block[] {
  return block.children.flatMap((child) => [child, ...descendants(child)]);
}

function occurrences(text: string, part: string): number {
  return text.split(part).length - 1;
}

/** The mode banner in full: the shipping-mode line, then for a chosen mode " — " and its PRD 4 title. */
function modeBanner(bundle: Bundle, shippingMode: string): string {
  const mode = bundle.manifest.shipping_mode;
  return mode === null ? shippingMode : `${shippingMode} — ${PRD4_TITLES[mode]}`;
}

/** Exactly one block with this name, or a problem that says how many the page has. */
function onlyBlock(blocks: readonly Block[], name: string): Block | string {
  const found = blocks.filter((block) => block.name === name);
  const [only] = found;
  if (only === undefined) return `the page has no <${name}>`;
  if (found.length !== 1) return `the page must have exactly one <${name}>, but has ${String(found.length)}`;
  return only;
}

// Each protected text must be whole: a mock banner, mode banner or footer that only starts with
// the expected text (bundle version v10 for v1, mode BA for B), or that has words added inside its
// element or in a container of the banner's own, fails, as it failed the T0-05 check of the whole
// page. Any other text outside <main> must be one of the layout's labels (LAYOUT_LABELS), so a
// claim beside a banner, in the header or between <main> and the footer fails too.
export function pageProblems(html: string, bundle: Bundle): string[] {
  const expected = shellText(bundle);
  const blocks = blockTree(html);
  // The whole page as a reader sees it: the text of the html element, the one top-level block.
  const text = blocks
    .filter((block) => block.parent === undefined)
    .map((block) => block.full)
    .join(' ');
  const problems: string[] = [];
  const banners: Extent[] = [];
  if (expected.mockBanner !== null) {
    const mock = checkBanner(blocks, MOCK_MARKER, expected.mockBanner, 'mock banner');
    if (mock.problem !== null) problems.push(mock.problem);
    banners.push(...mock.parts);
  } else if (text.includes(MOCK_MARKER)) {
    problems.push('a mock banner is shown, but the bundle is not mock');
  }
  const banner = modeBanner(bundle, expected.shippingMode);
  const mode = checkBanner(blocks, SHIPPING_MODE, banner, 'shipping-mode banner');
  if (mode.problem !== null) problems.push(mode.problem);
  banners.push(...mode.parts);
  const modeLines = occurrences(text, SHIPPING_MODE);
  if (modeLines > 1) {
    problems.push(`"${SHIPPING_MODE}" appears ${String(modeLines)} times; the page must state its mode once`);
  }
  // One footer, so a second one, even nested in the first, cannot state other versions.
  const footerText = `${expected.footer} · Schema ${bundle.manifest.schema_version}`;
  const footer = onlyBlock(blocks, 'footer');
  if (typeof footer === 'string') {
    problems.push(footer);
  } else if (footer.full !== footerText && !descendants(footer).some((child) => child.full === footerText)) {
    // The versions are one block of their own; the team name and links may sit beside it.
    problems.push(`the footer must read "${footerText}"`);
  }
  const main = onlyBlock(blocks, 'main');
  if (typeof main === 'string') {
    problems.push(main);
  } else if (!main.full.startsWith(`${SITE_TITLE} `) && !main.full.includes(` ${SITE_TITLE} `)) {
    problems.push(`<main> must show the title "${SITE_TITLE}"`);
  } else if (!main.full.includes(HOME_LINE)) {
    problems.push(`<main> must show "${HOME_LINE}", but shows "${main.full}"`);
  } else if (main.full.includes(MOCK_MARKER) || main.full.includes(SHIPPING_MODE)) {
    problems.push('<main> must not repeat a banner: the mock and shipping-mode banners belong to the header');
  } else if (OVERSTATED.test(main.full)) {
    problems.push(`<main> makes a claim stronger than any shipping mode allows: "${OVERSTATED.exec(main.full)?.[0] ?? ''}"`);
  }
  for (const stray of strayTexts(blocks, banners, [`Bundle ${bundle.manifest.bundle_version}`])) {
    problems.push(`text outside <main> must be a banner, the footer or a layout label, but "${stray}" is none of these`);
  }
  return problems;
}
