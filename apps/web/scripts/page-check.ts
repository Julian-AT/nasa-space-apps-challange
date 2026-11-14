// What make web-smoke asserts about the HTML of / (PRD 8.4, 8.6): the red mock banner exactly when
// the bundle is mock, the shipping-mode banner once, the bundle and schema versions in the footer,
// each as an exact text wherever the layout puts it, and one <main> that shows only the title and
// HOME_LINE, so no numbers, targets or claims. The layout's skip link and navigation sit outside
// <main>. Only the rendered markup counts: <script> elements, which hold the RSC payload, are
// removed first.
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

/** Each run of text between two tags, decoded and trimmed. React renders each banner string as one run. */
function textRuns(markup: string): string[] {
  return markup
    .split(/<[^>]*>/)
    .map((run) => decodeEntities(run).replace(/\s+/g, ' ').trim())
    .filter((run) => run !== '');
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
// the expected text (bundle version v10 for v1, mode BA for B) fails, as it failed the T0-05 check
// of the whole page. Text outside <main> other than these is the layout's chrome and is not checked.
export function pageProblems(html: string, bundle: Bundle): string[] {
  const expected = shellText(bundle);
  const markup = renderedMarkup(html);
  const text = visibleText(html);
  const runs = textRuns(markup);
  const problems: string[] = [];
  if (expected.mockBanner !== null) {
    if (!runs.includes(expected.mockBanner)) {
      problems.push(`the mock banner "${expected.mockBanner}" is missing`);
    }
  } else if (text.includes('Mock bundle')) {
    problems.push('a mock banner is shown, but the bundle is not mock');
  }
  const banner = modeBanner(bundle, expected.shippingMode);
  if (!runs.includes(banner)) {
    problems.push(`the shipping-mode banner "${banner}" is missing`);
  }
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
