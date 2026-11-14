// What make web-smoke asserts about the HTML of / (PRD 8.4, 8.6): the red mock banner exactly when
// the bundle is mock, the shipping-mode line once, the bundle version in the footer, wherever the
// layout puts them, and one <main> that shows only the title and HOME_LINE, so no numbers,
// targets or claims. The layout's skip link and navigation sit outside <main>. Only the rendered
// markup counts: <script> elements, which hold the RSC payload, are removed first.
import type { Bundle } from '../lib/bundle.ts';
import { shellText, SITE_TITLE } from '../lib/shell.ts';

/** The one line / shows under its title (app/page.tsx): no numbers, targets or claims. */
export const HOME_LINE = 'These pages show records from the bundle named in the footer.';

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

function occurrences(text: string, part: string): number {
  return text.split(part).length - 1;
}

export function pageProblems(html: string, bundle: Bundle): string[] {
  const expected = shellText(bundle);
  const text = visibleText(html);
  const problems: string[] = [];
  if (expected.mockBanner !== null) {
    if (!text.includes(expected.mockBanner)) {
      problems.push(`the mock banner "${expected.mockBanner}" is missing`);
    }
  } else if (text.includes('Mock bundle')) {
    problems.push('a mock banner is shown, but the bundle is not mock');
  }
  if (!text.includes(expected.shippingMode)) {
    problems.push(`the shipping-mode line "${expected.shippingMode}" is missing`);
  }
  const modeLines = occurrences(text, SHIPPING_MODE);
  if (modeLines > 1) {
    problems.push(`"${SHIPPING_MODE}" appears ${String(modeLines)} times; the page must state its mode once`);
  }
  const footers = [...html.matchAll(/<footer\b[^>]*>([\s\S]*?)<\/footer\s*>/gi)];
  if (footers.length === 0) {
    problems.push('the page has no <footer>');
  } else if (!footers.some((match) => visibleText(match[1] ?? '').includes(expected.footer))) {
    problems.push(`the footer does not show "${expected.footer}"`);
  }
  const markup = renderedMarkup(html);
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
