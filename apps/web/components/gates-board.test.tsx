// The validation board on /methods (PRD 8.4, 8.6, 8.12, 9, 10 "Validation board"): one panel per
// gate G0 to G6 with every GateRecord field in an element named by data-field, every status as its
// literal text, null fields as an explicit unavailable reason and absent gates as "No record in
// this bundle". The provenance fields sit in a per-gate disclosure.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { type GateRecord, GateRecordSchema, ValidationSchema } from '@terraluna/schema';
import { type DefaultTreeAdapterTypes, defaultTreeAdapter, parseFragment } from 'parse5';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { EVIDENCE_MEANINGS } from './evidence-label.tsx';
import { GATE_FIELDS, GATE_IDS, GatesBoard, NO_INPUTS_REASON, NO_RECORD, NULL_REASON } from './gates-board.tsx';
import { MOCK_DIR, WEB_DIR } from '../tests/helpers.ts';
import { prd812GateFields } from '../tests/prd.ts';

type Node = DefaultTreeAdapterTypes.ChildNode | DefaultTreeAdapterTypes.DocumentFragment;
type Element = DefaultTreeAdapterTypes.Element;

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

interface Panel {
  readonly gate: string;
  readonly text: string;
  /** Text of each data-field element, by field name. */
  readonly fields: ReadonlyMap<string, string>;
  /** Fields inside the <details> disclosure. */
  readonly folded: ReadonlySet<string>;
  readonly summary: string | null;
  readonly classes: ReadonlyMap<string, string>;
  readonly titles: readonly string[];
}

function allElements(node: Node): Element[] {
  return children(node).flatMap((child) =>
    defaultTreeAdapter.isElementNode(child) ? [child, ...allElements(child)] : [],
  );
}

function readBoard(html: string): { list: Element; panels: Panel[] } {
  const fragment = parseFragment(html);
  const [list] = descendants(fragment, 'ol');
  if (list === undefined) throw new Error('no ordered list');
  const panels = descendants(list, 'li')
    .filter((li) => attribute(li, 'data-gate') !== null)
    .map((li): Panel => {
      const fields = new Map<string, string>();
      const classes = new Map<string, string>();
      const folded = new Set<string>();
      const [details] = descendants(li, 'details');
      const inDetails = new Set(details === undefined ? [] : allElements(details));
      for (const element of allElements(li)) {
        const field = attribute(element, 'data-field');
        if (field === null) continue;
        fields.set(field, text(element));
        // The wrapper's own classes: the status badge inside it carries the status colour with its text.
        classes.set(field, attribute(element, 'class') ?? '');
        if (inDetails.has(element)) folded.add(field);
      }
      const [summary] = descendants(li, 'summary');
      return {
        gate: attribute(li, 'data-gate') ?? '',
        text: text(li),
        fields,
        folded,
        summary: summary === undefined ? null : text(summary),
        classes,
        titles: allElements(li).flatMap((e) => attribute(e, 'title') ?? []),
      };
    });
  return { list, panels };
}

function render(records: readonly unknown[]): string {
  return renderToStaticMarkup(<GatesBoard validation={ValidationSchema.parse(records)} />);
}

function record(gateId: string, fields: Partial<Record<keyof GateRecord, unknown>> = {}): GateRecord {
  // A record with any status other than NOT_RUN must carry its evidence (GateRecord schema, PRD 8.13).
  const evidence =
    (fields.status ?? 'NOT_RUN') === 'NOT_RUN'
      ? {}
      : {
          evidence_path: `outputs/${gateId}/evidence.json`,
          run_id: `sha256:${'d'.repeat(64)}`,
          scope: `scope text of ${gateId}`,
          command: `uv run gate-${gateId}`,
          config_version: 'e'.repeat(64),
          input_hashes: [{ path: `inputs/${gateId}.json`, sha256: 'f'.repeat(64) }],
        };
  return GateRecordSchema.parse({
    gate_id: gateId,
    status: 'NOT_RUN',
    evidence_path: null,
    run_id: null,
    scope: null,
    result: null,
    consequence: null,
    command: null,
    config_version: null,
    input_hashes: [],
    ...evidence,
    ...fields,
  });
}

/** A record with a distinct non-null value in every field. */
function fullRecord(gateId: string, status: string): GateRecord {
  return record(gateId, {
    status,
    evidence_path: `outputs/${gateId}/evidence.json`,
    run_id: `sha256:${'d'.repeat(64)}`,
    command: `uv run gate-${gateId}`,
    config_version: 'e'.repeat(64),
    input_hashes: [{ path: `inputs/${gateId}.json`, sha256: 'f'.repeat(64) }],
    scope: `scope text of ${gateId}`,
    result: `result text of ${gateId}`,
    consequence: `consequence text of ${gateId}`,
  });
}

/** The text a field shows: an input_hashes list is one "path <first 12 hex>" per entry. */
function cellText(value: GateRecord[keyof GateRecord]): string | null {
  return Array.isArray(value) ? value.map((input) => `${input.path} ${input.sha256.slice(0, 12)}`).join(' ') : value;
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

const SUMMARY = ['scope', 'result', 'consequence'] as const;
const PROVENANCE = ['evidence_path', 'run_id', 'command', 'config_version', 'input_hashes'] as const;

describe('GatesBoard on the mock bundle', () => {
  const records = ValidationSchema.parse(MOCK_VALIDATION);
  const { list, panels } = readBoard(render(records));

  it('renders one labelled list with one panel per gate, G0 to G6', () => {
    expect(attribute(list, 'aria-label')).toMatch(/validation\.json/);
    expect(panels.map((panel) => panel.gate)).toEqual([...GATE_IDS]);
  });

  it('renders every GateRecord field once per recorded gate, the provenance fields folded', () => {
    for (const panel of panels.filter((p) => p.fields.has('status'))) {
      expect([...panel.fields.keys()].sort()).toEqual([...GATE_FIELDS].sort());
      expect([...panel.folded].sort()).toEqual([...PROVENANCE].sort());
      expect(panel.summary).toBe('Provenance');
    }
  });

  it('shows NOT_RUN as the literal text NOT_RUN, never as a pass, zero, blank or green', () => {
    for (const panel of panels) {
      expect(panel.fields.get('status')).toBe('NOT_RUN');
      expect(panel.classes.get('status')).not.toMatch(GREEN);
    }
    expect(panels.map((panel) => panel.text).join(' ')).not.toMatch(/\bPASS\b|\b0\b/);
  });

  it('shows every null field as Unavailable with the reason, never as 0 or a blank', () => {
    for (const panel of panels) {
      for (const field of ['evidence_path', 'run_id', 'scope', 'result', 'consequence', 'command', 'config_version']) {
        expect(panel.fields.get(field), field).toBe(UNAVAILABLE_TEXT);
      }
      // An empty input list is absent evidence too, with its own reason; it is never a blank or 0.
      expect(panel.fields.get('input_hashes')).toBe(`Unavailable ${EVIDENCE_MEANINGS.Unavailable} ${NO_INPUTS_REASON}`);
    }
  });
});

describe('GatesBoard on small GateRecord arrays', () => {
  it('renders every field of a full record in its own element, input hashes shortened with the full hash as title', () => {
    const full = fullRecord('G3', 'PARTIAL');
    const panel = readBoard(render([full])).panels.find((p) => p.gate === 'G3');
    for (const field of GATE_FIELDS) {
      expect(panel?.fields.get(field), field).toBe(field === 'gate_id' ? 'G3' : cellText(full[field]));
    }
    expect(panel?.titles).toContain('f'.repeat(64));
  });

  it('renders each status as its own literal text, with the same wrapper styling for every status', () => {
    const records = STATUSES.map((status, index) => fullRecord(`G${String(index)}`, status));
    const panels = readBoard(render(records)).panels.slice(0, STATUSES.length);
    expect(panels.map((panel) => panel.fields.get('status'))).toEqual([...STATUSES]);
    for (const panel of panels) expect(panel.classes.get('status')).not.toMatch(GREEN);
  });

  it('renders a null result as an explicit unavailable reason, not 0', () => {
    const panels = readBoard(render([fullRecord('G1', 'PASS'), record('G2', { status: 'FAIL', result: null })])).panels;
    const result = panels[2]?.fields.get('result');
    expect(panels[2]?.gate).toBe('G2');
    expect(result).toBe(UNAVAILABLE_TEXT);
    expect(result).not.toMatch(/^0$|^$|^[-–—]$/);
  });

  it('sorts panels G0 to G6 without dropping or inventing records', () => {
    const shuffled = ['G6', 'G2', 'G0', 'G5', 'G1', 'G4', 'G3'].map((id, index) =>
      fullRecord(id, STATUSES[index % STATUSES.length] ?? 'NOT_RUN'),
    );
    const { panels } = readBoard(render(shuffled));
    expect(panels.map((panel) => panel.gate)).toEqual(GATE_IDS);
    for (const panel of panels) {
      const source = shuffled.find((entry) => entry.gate_id === panel.gate);
      for (const field of [...SUMMARY, ...PROVENANCE, 'status'] as const) {
        expect(panel.fields.get(field)).toBe(source === undefined ? undefined : cellText(source[field]));
      }
    }
  });

  it('shows gates absent from validation.json as "No record in this bundle", not as NOT_RUN', () => {
    const { panels } = readBoard(render([fullRecord('G4', 'FAIL'), fullRecord('G1', 'PASS')]));
    expect(panels.map((panel) => panel.gate)).toEqual(GATE_IDS);
    for (const panel of panels) {
      if (panel.gate === 'G1' || panel.gate === 'G4') {
        expect(panel.fields.has('status')).toBe(true);
        continue;
      }
      expect(panel.text).toBe(`${panel.gate} ${NO_RECORD}`);
      expect(panel.fields.has('status')).toBe(false);
      expect(panel.text).not.toMatch(/NOT_RUN|Unavailable/);
    }
  });

  it('shows "No record in this bundle" for every gate of an empty validation.json', () => {
    const { panels } = readBoard(render([]));
    expect(panels.map((panel) => panel.text)).toEqual(GATE_IDS.map((id) => `${id} ${NO_RECORD}`));
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
    const panel = readBoard(render([g5])).panels.find((p) => p.gate === 'G5');
    for (const field of GATE_FIELDS.filter((f) => f !== 'gate_id')) {
      expect(panel?.fields.get(field), field).toBe(cellText(g5[field]));
    }
  });

  it('adds no text of its own beyond the gate ids, field names, Provenance and the fixed reasons', () => {
    const records = ['G0', 'G2'].map((id) => fullRecord(id, 'PASS'));
    const html = render(records);
    const own = new Set<string>([
      ...GATE_IDS,
      NO_RECORD,
      'Provenance',
      ...GATE_FIELDS,
      ...records.flatMap((entry) => GATE_FIELDS.map((f) => cellText(entry[f]) ?? '')),
    ]);
    const fragment = parseFragment(html);
    for (const element of allElements(fragment)) {
      if (!['dt', 'dd', 'h3', 'summary', 'p'].includes(element.tagName)) continue;
      const own_ = text(element);
      // Whole-element text is a field value, a field name, a gate id, the summary or a fixed reason.
      const parts = own_ === '' ? [] : [own_];
      for (const part of parts) {
        if (own.has(part)) continue;
        // A dd holding a list or badge reads as the joined field text; accept values listed above.
        expect([...own].some((value) => part.includes(value)), part).toBe(true);
      }
    }
  });
});

describe('gates-board.tsx source', () => {
  const source = readFileSync(join(WEB_DIR, 'components', 'gates-board.tsx'), 'utf8');
  const badgeSource = readFileSync(join(WEB_DIR, 'components', 'ui', 'badge.tsx'), 'utf8');

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
      // The board's classes live in its own source and in the badge primitive it uses.
      expect(`${source}${badgeSource}`, name).toMatch(new RegExp(`['"\\s]${escaped}['"\\s]`));
    }
  });

  it('is a server component', () => {
    expect(source).not.toMatch(/['"]use client['"]/);
  });
});
