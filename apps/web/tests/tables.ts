// Tables in static markup, read with parse5 as a browser parses them: caption, header cells and
// body rows of every table, for the /data tests (PRD 8.6, 8.10).
import { type DefaultTreeAdapterTypes, defaultTreeAdapter, parseFragment } from 'parse5';

type Node = DefaultTreeAdapterTypes.ChildNode | DefaultTreeAdapterTypes.DocumentFragment;
type Element = DefaultTreeAdapterTypes.Element;

export interface TableCell {
  readonly tag: string;
  readonly text: string;
  readonly scope: string | null;
  /** The data-field attribute: the record field the cell shows. */
  readonly field: string | null;
  /** The data-label attribute of an evidence-labelled value inside the cell, if any. */
  readonly label: string | null;
  /** The text of the reason inside the cell's labelled value, if any. */
  readonly reason: string | null;
}

export interface MarkupTable {
  readonly caption: string;
  readonly headers: readonly TableCell[];
  readonly rows: readonly (readonly TableCell[])[];
}

function children(node: Node): DefaultTreeAdapterTypes.ChildNode[] {
  return 'childNodes' in node ? node.childNodes : [];
}

function descendants(node: Node, match: (element: Element) => boolean): Element[] {
  const found: Element[] = [];
  for (const child of children(node)) {
    if (defaultTreeAdapter.isElementNode(child)) {
      if (match(child)) found.push(child);
      found.push(...descendants(child, match));
    }
  }
  return found;
}

function byTag(node: Node, tag: string): Element[] {
  return descendants(node, (element) => element.tagName === tag);
}

function attribute(element: Element, name: string): string | null {
  return element.attrs.find((attr) => attr.name === name)?.value ?? null;
}

function rawText(node: Node): string {
  if (defaultTreeAdapter.isTextNode(node)) return node.value;
  return children(node).map(rawText).join(' ');
}

/** The visible text of a node: text nodes joined with blanks, whitespace collapsed. */
export function nodeText(node: Node): string {
  return rawText(node).replace(/\s+/g, ' ').trim();
}

function cell(element: Element): TableCell {
  const [value] = descendants(element, (child) => attribute(child, 'data-slot') === 'labelled-value');
  const [reason] = value === undefined ? [] : descendants(value, (child) => attribute(child, 'data-slot') === 'reason');
  return {
    tag: element.tagName,
    text: nodeText(element),
    scope: attribute(element, 'scope'),
    field: attribute(element, 'data-field'),
    label: value === undefined ? null : attribute(value, 'data-label'),
    reason: reason === undefined ? null : nodeText(reason),
  };
}

function cellsOf(row: Element): TableCell[] {
  return row.childNodes.filter((node) => defaultTreeAdapter.isElementNode(node)).map(cell);
}

/** Every table in the markup, in document order. A table needs a thead and a tbody. */
export function readTables(html: string): MarkupTable[] {
  return byTag(parseFragment(html), 'table').map((table) => {
    const [thead] = byTag(table, 'thead');
    const [tbody] = byTag(table, 'tbody');
    if (thead === undefined || tbody === undefined) throw new Error('a table needs thead and tbody');
    const [caption] = byTag(table, 'caption');
    return {
      caption: caption === undefined ? '' : nodeText(caption),
      headers: byTag(thead, 'tr').flatMap(cellsOf),
      rows: byTag(tbody, 'tr').map(cellsOf),
    };
  });
}

/** The labelled values in the markup that are not inside a table: label and reason. */
export function labelledValuesOutsideTables(html: string): { label: string | null; reason: string | null }[] {
  const fragment = parseFragment(html);
  const inTables = new Set(byTag(fragment, 'table').flatMap((table) => descendants(table, () => true)));
  return descendants(fragment, (element) => attribute(element, 'data-slot') === 'labelled-value')
    .filter((element) => !inTables.has(element))
    .map((element) => {
      const [reason] = descendants(element, (child) => attribute(child, 'data-slot') === 'reason');
      return { label: attribute(element, 'data-label'), reason: reason === undefined ? null : nodeText(reason) };
    });
}
