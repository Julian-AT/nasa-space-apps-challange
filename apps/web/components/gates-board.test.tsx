// The validation board on /methods (PRD 8.4, 8.6, 8.12, 9, 10 "Validation board"): one table row
// per gate G0 to G6, each GateRecord field in its own column, every status as its literal text,
// null fields as an explicit unavailable reason and absent gates as "No record in this bundle".
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { type GateRecord, GateRecordSchema, ValidationSchema } from '@terraluna/schema';
import { type DefaultTreeAdapterTypes, defaultTreeAdapter, parseFragment } from 'parse5';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { EVIDENCE_MEANINGS } from './evidence-label.tsx';
import { GATE_FIELDS, GATE_IDS, GatesBoard, NO_RECORD, NULL_REASON } from './gates-board.tsx';
import { MOCK_DIR, WEB_DIR } from '../tests/helpers.ts';
import { prd812GateFields } from '../tests/prd.ts';

type Node = DefaultTreeAdapterTypes.ChildNode | DefaultTreeAdapterTypes.DocumentFragment;
type Element = DefaultTreeAdapterTypes.Element;

interface Cell {
  readonly tag: string;
  readonly text: string;
  readonly scope: string | null;
  readonly colSpan: number;
  readonly classes: string;
}

interface Table {
  readonly count: number;
  readonly caption: string;
  readonly headers: readonly Cell[];
  readonly rows: readonly (readonly Cell[])[];
}

const STATUSES = GateRecordSchema.shape.status.options;

function children(node: Node): DefaultTreeAdapterTypes.ChildNode[] {
  return 'childNodes' in node ? node.childNodes : [];
}

function descendants(node: Node, tag: string): Element[] {
  const found: Element[] = [];
  for (const child of children(node)) {
    if (defaultTreeAdapter.isElementNode(child)) {
      if (child.tagName === tag) found.push(child);
      found.push(...descendants(child, tag));
    }
  }
  return found;
}

function rawText(node: Node): string {
  if (defaultTreeAdapter.isTextNode(node)) return node.value;
  return children(node).map(rawText).join(' ');
}

function text(node: Node): string {
  return rawText(node).replace(/\s+/g, ' ').trim();
}

function attribute(element: Element, name: string): string | null {
  return element.attrs.find((attr) => attr.name === name)?.value ?? null;
}

function cell(element: Element): Cell {
  return {
    tag: element.tagName,
    text: text(element),
    scope: attribute(element, 'scope'),
    colSpan: Number(attribute(element, 'colspan') ?? '1'),
    classes: attribute(element, 'class') ?? '',
  };
}

function cellsOf(row: Element): Cell[] {
  return row.childNodes.filter((node) => defaultTreeAdapter.isElementNode(node)).map(cell);
}

function readTable(html: string): Table {
  const fragment = parseFragment(html);
  const tables = descendants(fragment, 'table');
  const [table] = tables;
  if (table === undefined) throw new Error('no table');
  const [thead] = descendants(table, 'thead');
  const [tbody] = descendants(table, 'tbody');
  if (thead === undefined || tbody === undefined) throw new Error('the table needs thead and tbody');
  const [caption] = descendants(table, 'caption');
  return {
    count: tables.length,
    caption: caption === undefined ? '' : text(caption),
    headers: descendants(thead, 'tr').flatMap(cellsOf),
    rows: descendants(tbody, 'tr').map(cellsOf),
  };
}

function render(records: readonly unknown[]): string {
  return renderToStaticMarkup(<GatesBoard validation={ValidationSchema.parse(records)} />);
}

function record(gateId: string, fields: Partial<Record<keyof GateRecord, string | null>> = {}): GateRecord {
  return GateRecordSchema.parse({
    gate_id: gateId,
    status: 'NOT_RUN',
    evidence_path: null,
    run_id: null,
    scope: null,
    result: null,
    consequence: null,
    ...fields,
  });
}

/** A record with a distinct non-null value in every field. */
function fullRecord(gateId: string, status: string): GateRecord {
  return record(gateId, {
    status,
    evidence_path: `outputs/${gateId}/evidence.json`,
    run_id: `sha256:run-${gateId}`,
    scope: `scope text of ${gateId}`,
    result: `result text of ${gateId}`,
    consequence: `consequence text of ${gateId}`,
  });
}

const MOCK_VALIDATION: unknown = JSON.parse(readFileSync(join(MOCK_DIR, 'validation.json'), 'utf8'));
// A null field: the PRD 3 Unavailable label, its meaning (read by screen readers) and the reason.
const UNAVAILABLE_TEXT = `Unavailable ${EVIDENCE_MEANINGS.Unavailable} ${NULL_REASON}`;
const GREEN = /green|emerald|lime|teal|success|pass|ok\b/i;

describe('GATE_FIELDS and GATE_IDS', () => {
  it('are the GateRecord fields of the schema, in the order PRD 8.12 lists them', () => {
    expect(GATE_FIELDS).toEqual(Object.keys(GateRecordSchema.shape));
    expect(GATE_FIELDS).toEqual(prd812GateFields());
  });

  it('are the gate ids of the schema, G0 to G6', () => {
    expect(GATE_IDS).toEqual(GateRecordSchema.shape.gate_id.options);
    expect(GATE_IDS).toEqual(['G0', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6']);
  });
});

describe('GatesBoard on the mock bundle', () => {
  const records = ValidationSchema.parse(MOCK_VALIDATION);
  const table = readTable(render(records));

  it('renders one table with a caption and a column header for every GateRecord field', () => {
    expect(table.count).toBe(1);
    expect(table.caption).not.toBe('');
    expect(table.headers.map((header) => [header.tag, header.text, header.scope])).toEqual(
      GATE_FIELDS.map((field) => ['th', field, 'col']),
    );
  });

  it('renders one row per record, led by its gate_id as a row header', () => {
    expect(table.rows).toHaveLength(records.length);
    expect(table.rows.map((row) => [row[0]?.tag, row[0]?.scope, row[0]?.text])).toEqual(
      records.map((entry) => ['th', 'row', entry.gate_id]),
    );
  });

  it('shows NOT_RUN as the literal text NOT_RUN, never as a pass, zero, blank or green', () => {
    for (const row of table.rows) {
      const status = row[GATE_FIELDS.indexOf('status')];
      expect(status?.text).toBe('NOT_RUN');
      expect(status?.classes).not.toMatch(GREEN);
    }
    expect(textOfTable(table)).not.toMatch(/\bPASS\b|\b0\b/);
  });

  it('shows every null field as Unavailable with the reason, never as 0 or a blank', () => {
    for (const row of table.rows) {
      expect(row).toHaveLength(GATE_FIELDS.length);
      for (const field of ['evidence_path', 'run_id', 'scope', 'result', 'consequence'] as const) {
        expect(row[GATE_FIELDS.indexOf(field)]?.text).toBe(UNAVAILABLE_TEXT);
      }
    }
  });
});

function textOfTable(table: Table): string {
  return [table.caption, ...table.headers.map((c) => c.text), ...table.rows.flat().map((c) => c.text)].join(' ');
}

describe('GatesBoard on small GateRecord arrays', () => {
  it('renders every field of a full record verbatim in its own column', () => {
    const full = fullRecord('G3', 'PARTIAL');
    const [row] = readTable(render([full])).rows.filter((cells) => cells[0]?.text === 'G3');
    expect(row?.map((c) => c.text)).toEqual(GATE_FIELDS.map((field) => full[field]));
  });

  it('renders each status as its own literal text, with the same styling for every status', () => {
    const records = STATUSES.map((status, index) => fullRecord(`G${String(index)}`, status));
    const rows = readTable(render(records)).rows.slice(0, STATUSES.length);
    const cells = rows.map((row) => row[GATE_FIELDS.indexOf('status')]);
    expect(cells.map((c) => c?.text)).toEqual([...STATUSES]);
    expect(new Set(cells.map((c) => c?.classes)).size).toBe(1);
    for (const c of cells) expect(c?.classes).not.toMatch(GREEN);
  });

  it('renders a null result as an explicit unavailable reason, not 0', () => {
    const row = readTable(render([fullRecord('G1', 'PASS'), record('G2', { status: 'FAIL', result: null })]))
      .rows[2];
    expect(row?.[0]?.text).toBe('G2');
    const result = row?.[GATE_FIELDS.indexOf('result')];
    expect(result?.text).toBe(UNAVAILABLE_TEXT);
    expect(result?.text).not.toMatch(/^0$|^$|^[-–—]$/);
  });

  it('sorts rows G0 to G6 without dropping or inventing records', () => {
    const shuffled = ['G6', 'G2', 'G0', 'G5', 'G1', 'G4', 'G3'].map((id, index) =>
      fullRecord(id, STATUSES[index % STATUSES.length] ?? 'NOT_RUN'),
    );
    const rows = readTable(render(shuffled)).rows;
    expect(rows.map((row) => row[0]?.text)).toEqual(GATE_IDS);
    for (const row of rows) {
      const source = shuffled.find((entry) => entry.gate_id === row[0]?.text);
      expect(row.map((c) => c.text)).toEqual(GATE_FIELDS.map((field) => source?.[field]));
    }
  });

  it('shows gates absent from validation.json as "No record in this bundle", not as NOT_RUN', () => {
    const rows = readTable(render([fullRecord('G4', 'FAIL'), fullRecord('G1', 'PASS')])).rows;
    expect(rows.map((row) => row[0]?.text)).toEqual(GATE_IDS);
    for (const row of rows) {
      const id = row[0]?.text;
      if (id === 'G1' || id === 'G4') {
        expect(row).toHaveLength(GATE_FIELDS.length);
        continue;
      }
      expect(row.map((c) => c.text)).toEqual([id, NO_RECORD]);
      expect(row[1]?.colSpan).toBe(GATE_FIELDS.length - 1);
      expect(row[1]?.text).not.toMatch(/NOT_RUN|Unavailable/);
    }
  });

  it('shows "No record in this bundle" for every gate of an empty validation.json', () => {
    const rows = readTable(render([])).rows;
    expect(rows.map((row) => row.map((c) => c.text))).toEqual(GATE_IDS.map((id) => [id, NO_RECORD]));
  });

  it('refuses a validation.json that lists a gate twice, rather than drop a record', () => {
    const twice = [record('G1'), record('G1', { status: 'PASS' })];
    expect(() => renderToStaticMarkup(<GatesBoard validation={twice} />)).toThrow(/G1/);
  });

  it('refuses a record whose gate_id is not a schema gate id, rather than drop it', () => {
    const unknown = { ...record('G1'), gate_id: 'G7' } as unknown as GateRecord;
    expect(() => renderToStaticMarkup(<GatesBoard validation={[unknown]} />)).toThrow(/G7/);
  });

  it('renders a G5 record like any other gate, from its record alone', () => {
    const g5 = fullRecord('G5', 'FAIL');
    const rows = readTable(render([g5])).rows;
    expect(rows.find((row) => row[0]?.text === 'G5')?.map((c) => c.text)).toEqual(
      GATE_FIELDS.map((field) => g5[field]),
    );
  });

  it('adds no text of its own beyond the caption, the field names and the two fixed reasons', () => {
    const records = ['G0', 'G2'].map((id) => fullRecord(id, 'PASS'));
    const table = readTable(render(records));
    const own = new Set([...GATE_IDS, NO_RECORD, ...records.flatMap((entry) => GATE_FIELDS.map((f) => entry[f]))]);
    for (const c of table.rows.flat()) expect(own).toContain(c.text);
  });
});

describe('gates-board.tsx source', () => {
  const source = readFileSync(join(WEB_DIR, 'components', 'gates-board.tsx'), 'utf8');

  it('types no status, result or threshold: every status literal comes from the records', () => {
    for (const status of STATUSES) expect(source).not.toContain(status);
    expect(source).not.toMatch(/\b(threshold|metric|score|passed|failed)\b/i);
  });

  it('mentions no encoder, model or embedding', () => {
    expect(source).not.toMatch(/encoder|model|embedding|onnx/i);
  });

  it('writes each class it renders as a whole word, so the Tailwind scanner finds it', () => {
    // Tailwind reads candidates from the source text; a class glued to `${` in a template is missed.
    const html = render([fullRecord('G0', 'PASS')]);
    const classes = new Set([...html.matchAll(/class="([^"]*)"/g)].flatMap((match) => (match[1] ?? '').split(' ')));
    expect(classes.size).toBeGreaterThan(0);
    for (const name of classes) {
      const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      expect(source, name).toMatch(new RegExp(`['"\\s]${escaped}['"\\s]`));
    }
  });

  it('is a server component', () => {
    expect(source).not.toMatch(/['"]use client['"]/);
  });
});
