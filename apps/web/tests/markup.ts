// Static markup from renderToStaticMarkup, read without a DOM: each element with its attributes
// and visible text, for the layout, banner and navigation tests (PRD 8.6, 8.10).

export interface MarkupElement {
  readonly tag: string;
  readonly attributes: ReadonlyMap<string, string>;
  /** The text inside the element: tags and comments removed, entities decoded, blanks collapsed. */
  readonly text: string;
  /** Offset of the opening tag in the markup, so tests can compare document order. */
  readonly start: number;
}

const VOID = new Set([
  'area',
  'base',
  'br',
  'col',
  'embed',
  'hr',
  'img',
  'input',
  'link',
  'meta',
  'source',
  'track',
  'wbr',
]);

const ENTITIES: Readonly<Record<string, string>> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  '#x27': "'",
  '#39': "'",
};

function decode(text: string): string {
  return text.replace(/&(amp|lt|gt|quot|#x27|#39);/g, (_, name: string) => ENTITIES[name] ?? '');
}

/** The visible text of a markup fragment. */
export function textOf(html: string): string {
  return decode(html.replace(/<!--[\s\S]*?-->/g, '').replace(/<[^>]*>/g, ' '))
    .replace(/\s+/g, ' ')
    .trim();
}

/** Every element of the markup, in document order of their opening tags. */
export function elements(html: string): MarkupElement[] {
  const found: MarkupElement[] = [];
  const open: { tag: string; attributes: Map<string, string>; start: number; inner: number }[] = [];
  for (const match of html.matchAll(/<(\/?)([a-z][a-z0-9]*)((?:[^>"]|"[^"]*")*)>/g)) {
    const [whole, closing, tag = '', rest = ''] = match;
    if (closing === '/') {
      const top = open.pop();
      if (top?.tag !== tag) throw new Error(`unbalanced </${tag}>`);
      found.push({
        tag,
        attributes: top.attributes,
        text: textOf(html.slice(top.inner, match.index)),
        start: top.start,
      });
      continue;
    }
    const attributes = new Map<string, string>();
    for (const [, name = '', value = ''] of rest.matchAll(/([a-zA-Z][a-zA-Z0-9-]*)(?:="([^"]*)")?/g)) {
      attributes.set(name, decode(value));
    }
    if (VOID.has(tag) || rest.trimEnd().endsWith('/')) {
      found.push({ tag, attributes, text: '', start: match.index });
    } else {
      open.push({ tag, attributes, start: match.index, inner: match.index + whole.length });
    }
  }
  if (open.length > 0) throw new Error(`unclosed <${open.map((element) => element.tag).join('>, <')}>`);
  return found.sort((a, b) => a.start - b.start);
}

export function classesOf(element: MarkupElement): string[] {
  return (element.attributes.get('class') ?? '').split(/\s+/).filter((name) => name !== '');
}

/** The single element that matches; throws when there are none or several. */
export function only(found: readonly MarkupElement[], what: string): MarkupElement {
  if (found.length !== 1) throw new Error(`${String(found.length)} elements match ${what}`);
  return found[0] as MarkupElement;
}

/** True when the source file starts with a "use client" directive (after comments). */
export function isClientModule(source: string): boolean {
  const code = source.replace(/^(?:\s*(?:\/\/[^\n]*|\/\*[\s\S]*?\*\/))*\s*/, '');
  return /^['"]use client['"]/.test(code);
}
