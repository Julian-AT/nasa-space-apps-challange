// What make web-smoke asserts about the HTML of / (PRD 8.4, 8.6): the red mock banner exactly when
// the bundle is mock, the shipping-mode line, the bundle version in the footer, and no other text
// than these and the title, so no numbers, targets or claims. Only the rendered markup counts:
// <script> elements, which hold the RSC payload, are removed first.
import type { Bundle } from '../lib/bundle.ts';
import { shellText, SITE_TITLE } from '../lib/shell.ts';

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
  const markup = html
    .replace(/<script\b[\s\S]*?<\/script\s*>/gi, ' ')
    .replace(/<style\b[\s\S]*?<\/style\s*>/gi, ' ')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<[^>]*>/g, ' ');
  return decodeEntities(markup).replace(/\s+/g, ' ').trim();
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
  const footers = [...html.matchAll(/<footer\b[^>]*>([\s\S]*?)<\/footer\s*>/gi)];
  if (footers.length === 0) {
    problems.push('the page has no <footer>');
  } else if (!footers.some((match) => visibleText(match[1] ?? '').includes(expected.footer))) {
    problems.push(`the footer does not show "${expected.footer}"`);
  }
  const body = /<body\b[^>]*>([\s\S]*)<\/body\s*>/i.exec(html)?.[1] ?? html;
  const allowed = [expected.mockBanner, expected.shippingMode, SITE_TITLE, expected.footer]
    .filter((line) => line !== null)
    .join(' ');
  if (visibleText(body) !== allowed) {
    problems.push(`the page must show only "${allowed}", but shows "${visibleText(body)}"`);
  }
  return problems;
}
