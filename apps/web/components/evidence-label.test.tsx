// Evidence labels (PRD 3, 8.6, 10, D08): one component for the five labels, a value that always
// carries its label, and the legend. The labels, meanings and legend sentence are compared with
// PRD.md section 3 itself, so a copy that drifts from the PRD fails. Every value is synthetic.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, expectTypeOf, it } from 'vitest';

import { contrastRatio } from '../tests/contrast.ts';
import { REPO_ROOT, WEB_DIR } from '../tests/helpers.ts';
import { compile, declarations } from '../tests/tailwind.ts';
import { rootTokens, token } from '../tests/tokens.ts';
import { describeCase, resolveCase, textCases } from '../tests/variant-colours.ts';
import {
  EVIDENCE_LABELS,
  EVIDENCE_MEANINGS,
  EvidenceLabel,
  type EvidenceLabelName,
} from './evidence-label.tsx';
import { EVIDENCE_LEGEND_SENTENCE, EvidenceLegend } from './evidence-legend.tsx';
import { LabelledValue } from './labelled-value.tsx';

// PRD.md section 3, "Evidence labels": the sentence and the label table, read from the PRD.

interface Prd3Labels {
  readonly sentence: string;
  readonly rows: readonly (readonly [string, string])[];
}

function prd3Labels(): Prd3Labels {
  const prd = readFileSync(join(REPO_ROOT, 'PRD.md'), 'utf8');
  const chapter = prd.indexOf('\n## 3. Claims, wording and evidence labels\n');
  const start = prd.indexOf('\n### Evidence labels\n', chapter);
  const end = prd.indexOf('\n### ', start + 1);
  if (chapter < 0 || start < 0 || end < 0) throw new Error('PRD.md has no section 3 "Evidence labels"');
  const lines = prd.slice(start, end).split('\n').slice(2);
  const rows = lines
    .filter((line) => line.startsWith('| ') && !line.startsWith('| Label |') && !line.startsWith('| ---'))
    .map((line) => {
      const cells = line.split('|').slice(1, -1).map((cell) => cell.trim());
      if (cells.length !== 2) throw new Error(`not a two-column row: ${line}`);
      return [cells[0] ?? '', cells[1] ?? ''] as const;
    });
  const sentence = lines.find((line) => line.trim() !== '') ?? '';
  return { sentence, rows };
}

// Static markup, read without a DOM.

interface Element {
  readonly tag: string;
  readonly attributes: ReadonlyMap<string, string>;
  /** The text inside the element, tags removed and whitespace collapsed. */
  readonly text: string;
}

const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', '#x27': "'", '#39': "'" };

function decode(text: string): string {
  return text.replace(/&(amp|lt|gt|quot|#x27|#39);/g, (_, name: string) => ENTITIES[name] ?? '');
}

function textOf(html: string): string {
  return decode(html.replace(/<[^>]*>/g, ' '))
    .replace(/\s+/g, ' ')
    .trim();
}

function render(element: ReactElement): string {
  return renderToStaticMarkup(element);
}

/** Every element of the markup, with its text, assuming no void elements (none are rendered). */
function elements(html: string): Element[] {
  const found: Element[] = [];
  const open: { tag: string; attributes: Map<string, string>; start: number }[] = [];
  const tagPattern = /<(\/?)([a-z][a-z0-9]*)([^>]*)>/g;
  for (const match of html.matchAll(tagPattern)) {
    const [whole, closing, tag = '', rest = ''] = match;
    if (closing === '/') {
      const top = open.pop();
      if (top?.tag !== tag) throw new Error(`unbalanced </${tag}>`);
      found.push({ tag, attributes: top.attributes, text: textOf(html.slice(top.start, match.index)) });
    } else {
      const attributes = new Map<string, string>();
      for (const [, name = '', value = ''] of rest.matchAll(/([a-z][a-z0-9-]*)="([^"]*)"/g)) {
        attributes.set(name, decode(value));
      }
      open.push({ tag, attributes, start: match.index + whole.length });
    }
  }
  if (open.length > 0) throw new Error('unclosed element');
  return found;
}

function bySlot(html: string, slot: string): Element[] {
  return elements(html).filter((element) => element.attributes.get('data-slot') === slot);
}

function oneBySlot(html: string, slot: string): Element {
  const found = bySlot(html, slot);
  if (found.length !== 1) throw new Error(`${String(found.length)} elements with data-slot="${slot}"`);
  return found[0] as Element;
}

/** The text of the element that aria-describedby on `element` points to. */
function description(html: string, element: Element): string {
  const id = element.attributes.get('aria-describedby');
  if (id === undefined) throw new Error(`<${element.tag}> has no aria-describedby`);
  const targets = elements(html).filter((candidate) => candidate.attributes.get('id') === id);
  if (targets.length !== 1) throw new Error(`${String(targets.length)} elements with id="${id}"`);
  return (targets[0] as Element).text;
}

function classesOf(element: Element): string[] {
  return (element.attributes.get('class') ?? '').split(/\s+/).filter((name) => name !== '');
}

/** The badge an EvidenceLabel draws, with its merged class list. */
function badge(label: EvidenceLabelName): Element {
  return oneBySlot(render(<EvidenceLabel label={label} />), 'badge');
}

const VALUE_LABELS = ['Observed', 'Derived', 'Predicted', 'Assumed'] as const;

describe('the PRD 3 evidence labels', () => {
  const prd = prd3Labels();

  it('are exactly the five labels of PRD 3, in its order', () => {
    expect(prd.rows.map(([label]) => label)).toEqual(['Observed', 'Derived', 'Predicted', 'Assumed', 'Unavailable']);
    expect(EVIDENCE_LABELS).toEqual(prd.rows.map(([label]) => label));
  });

  it('carry the PRD 3 meanings word for word', () => {
    expect(EVIDENCE_MEANINGS).toEqual(Object.fromEntries(prd.rows));
  });

  it('are a type that admits no other string', () => {
    expectTypeOf<EvidenceLabelName>().toEqualTypeOf<
      'Observed' | 'Derived' | 'Predicted' | 'Assumed' | 'Unavailable'
    >();
    expectTypeOf<'Measured'>().not.toExtend<EvidenceLabelName>();
    expectTypeOf<'observed'>().not.toExtend<EvidenceLabelName>();
    expectTypeOf<string>().not.toExtend<EvidenceLabelName>();
  });
});

describe('EvidenceLabel', () => {
  it.each(EVIDENCE_LABELS)('renders %s as text with its PRD 3 meaning as accessible description', (label) => {
    const html = render(<EvidenceLabel label={label} />);
    const element = oneBySlot(html, 'badge');
    expect(element.text).toBe(label);
    expect(description(html, element)).toBe(EVIDENCE_MEANINGS[label]);
    // Nothing else: the label and its meaning are the whole text.
    expect(textOf(html)).toBe(`${label} ${EVIDENCE_MEANINGS[label]}`);
    expect(oneBySlot(html, 'evidence-label').attributes.get('data-label')).toBe(label);
  });

  it('keeps the meaning for screen readers and shows it on request', () => {
    const hidden = render(<EvidenceLabel label="Derived" />);
    const shown = render(<EvidenceLabel label="Derived" showMeaning />);
    expect(classesOf(oneBySlot(hidden, 'evidence-meaning'))).toContain('sr-only');
    expect(classesOf(oneBySlot(shown, 'evidence-meaning'))).not.toContain('sr-only');
    expect(description(shown, oneBySlot(shown, 'badge'))).toBe(EVIDENCE_MEANINGS.Derived);
  });

  it('gives every instance its own description id', () => {
    const html = render(
      <>
        {EVIDENCE_LABELS.map((label) => (
          <EvidenceLabel key={label} label={label} />
        ))}
        {EVIDENCE_LABELS.map((label) => (
          <EvidenceLabel key={`again-${label}`} label={label} showMeaning />
        ))}
      </>,
    );
    const ids = elements(html).flatMap((element) => element.attributes.get('id') ?? []);
    expect(ids).toHaveLength(EVIDENCE_LABELS.length * 2);
    expect(new Set(ids).size).toBe(ids.length);
    const badges = bySlot(html, 'badge');
    expect(badges.map((element) => description(html, element))).toEqual(
      [...EVIDENCE_LABELS, ...EVIDENCE_LABELS].map((label) => EVIDENCE_MEANINGS[label]),
    );
  });

  it('refuses any other label at compile time and at run time', () => {
    // @ts-expect-error -- 'Measured' is not a PRD 3 evidence label.
    expect(() => render(<EvidenceLabel label="Measured" />)).toThrow(/Measured/);
    // @ts-expect-error -- labels are case-sensitive.
    expect(() => render(<EvidenceLabel label="observed" />)).toThrow(/observed/);
  });
});

/** The CSS border-style a class list sets: Tailwind's border-<style>, solid when none is set. */
function borderStyle(classes: readonly string[]): string {
  const styles = classes.flatMap((name) => /^border-(solid|dashed|dotted|double)$/.exec(name)?.[1] ?? []);
  if (styles.length > 1) throw new Error(`several border styles: ${styles.join(' ')}`);
  return styles[0] ?? 'solid';
}

/** The token of the class list's resting bg-<token>, or null when the surface shows through. */
function fillToken(classes: readonly string[], tokens: ReadonlySet<string>): string | null {
  const fills = classes.flatMap((name) => {
    const colour = /^bg-([a-z-]+)$/.exec(name)?.[1];
    return colour !== undefined && tokens.has(colour) ? [colour] : [];
  });
  if (fills.length > 1) throw new Error(`several fills: ${fills.join(' ')}`);
  return fills[0] ?? null;
}

/** The token of the class list's border-<token>. */
function borderToken(classes: readonly string[], tokens: ReadonlySet<string>): string {
  const colours = classes.flatMap((name) => {
    const colour = /^border-([a-z-]+)$/.exec(name)?.[1];
    return colour !== undefined && tokens.has(colour) ? [colour] : [];
  });
  if (colours.length !== 1) throw new Error(`expected one border colour, got ${colours.join(' ')}`);
  return colours[0] as string;
}

/** WCAG 2.2 SC 1.4.3 for text and SC 1.4.11 for the edge that carries the shape. */
const AA_TEXT = 4.5;
const AA_NON_TEXT = 3;

/** The page surfaces a label may sit on, as in the components/ui contrast test. */
const SURFACES = ['background', 'secondary', 'muted', 'accent'] as const;

describe('EvidenceLabel styling', () => {
  const tokens = new Set(rootTokens().keys());

  it('tells every pair of labels apart in greyscale, by edge style or by fill lightness', () => {
    for (const surface of SURFACES) {
      const looks = EVIDENCE_LABELS.map((label) => {
        const classes = classesOf(badge(label));
        const fill = fillToken(classes, tokens);
        return { label, edge: borderStyle(classes), fill: fill === null ? token(surface) : token(fill) };
      });
      for (const [i, a] of looks.entries()) {
        for (const b of looks.slice(i + 1)) {
          const apart = a.edge !== b.edge || contrastRatio(a.fill, b.fill) >= AA_NON_TEXT;
          expect(apart, `${a.label} and ${b.label} over --${surface}`).toBe(true);
        }
      }
    }
  });

  it('meets 4.5:1 text contrast in every state on every page surface', () => {
    for (const label of EVIDENCE_LABELS) {
      const classes = classesOf(badge(label)).join(' ');
      for (const textCase of textCases(classes, tokens)) {
        for (const surface of SURFACES) {
          const { text, background } = resolveCase(textCase, token(surface), token);
          expect(contrastRatio(text, background), `${label}, ${describeCase(textCase)}, --${surface}`).toBeGreaterThanOrEqual(
            AA_TEXT,
          );
        }
      }
    }
  });

  it('draws its edge at 3:1 or more against every page surface', () => {
    for (const label of EVIDENCE_LABELS) {
      const edge = borderToken(classesOf(badge(label)), tokens);
      for (const surface of SURFACES) {
        expect(contrastRatio(token(edge), token(surface)), `${label} --${edge} on --${surface}`).toBeGreaterThanOrEqual(
          AA_NON_TEXT,
        );
      }
    }
  });

  it('never uses the red reserved for the mock banner', () => {
    for (const label of EVIDENCE_LABELS) {
      // The mock token, a palette red or rose, or an arbitrary colour that could be red.
      expect(classesOf(badge(label)).join(' '), label).not.toMatch(/mock|destructive|-(red|rose)-\d|\[#/);
    }
  });

  it('uses border utilities that Tailwind compiles to the intended style and width', async () => {
    const classes = [...new Set(EVIDENCE_LABELS.flatMap((label) => classesOf(badge(label))))];
    const { root } = await compile(classes);
    for (const name of classes) {
      const style = /^border-(solid|dashed|dotted|double)$/.exec(name)?.[1];
      if (style !== undefined) expect(declarations(root, `.${name}`).get('border-style'), name).toBe(style);
      const width = /^border-(\d+)$/.exec(name)?.[1];
      if (width !== undefined) expect(declarations(root, `.${name}`).get('border-width'), name).toBe(`${width}px`);
    }
    expect(classes.some((name) => /^border-\d+$/.test(name))).toBe(true);
  });
});

describe('LabelledValue', () => {
  it.each(VALUE_LABELS)('renders a %s value with its label, unit and source', (label) => {
    const html = render(<LabelledValue label={label} value="test-value" unit="test-unit" source="test-source" />);
    expect(oneBySlot(html, 'value').text).toBe('test-value');
    expect(oneBySlot(html, 'unit').text).toBe('test-unit');
    expect(oneBySlot(html, 'source').text).toBe('Source: test-source');
    expect(oneBySlot(html, 'badge').text).toBe(label);
    expect(oneBySlot(html, 'labelled-value').attributes.get('data-label')).toBe(label);
    expect(textOf(html)).toBe(`${label} ${EVIDENCE_MEANINGS[label]} test-value test-unit Source: test-source`);
  });

  it('prints a number as String() gives it, and zero as a value rather than as missing', () => {
    const html = render(<LabelledValue label="Derived" value={0} unit="test-unit" source="test-source" />);
    expect(oneBySlot(html, 'value').text).toBe('0');
  });

  it('shows no unit for a quantity whose unit is declared null', () => {
    const html = render(<LabelledValue label="Assumed" value="test-value" unit={null} source="test-source" />);
    expect(bySlot(html, 'unit')).toHaveLength(0);
    expect(textOf(html)).toBe(`Assumed ${EVIDENCE_MEANINGS.Assumed} test-value Source: test-source`);
  });

  it('renders Unavailable with its reason and no value, zero, blank or dash', () => {
    const html = render(<LabelledValue label="Unavailable" reason="test reason" />);
    expect(oneBySlot(html, 'reason').text).toBe('test reason');
    expect(bySlot(html, 'value')).toHaveLength(0);
    expect(bySlot(html, 'unit')).toHaveLength(0);
    expect(textOf(html)).toBe(`Unavailable ${EVIDENCE_MEANINGS.Unavailable} test reason`);
  });

  it('names the source of an Unavailable quantity when one is given', () => {
    const html = render(<LabelledValue label="Unavailable" reason="test reason" source="test-source" />);
    expect(textOf(html)).toBe(`Unavailable ${EVIDENCE_MEANINGS.Unavailable} test reason Source: test-source`);
  });

  it('refuses a value label without a value, at compile time and at run time', () => {
    expect(() =>
      // @ts-expect-error -- a value label needs a value; missing evidence is labelled Unavailable.
      render(<LabelledValue label="Observed" unit="test-unit" source="test-source" />),
    ).toThrow(/Unavailable/);
    expect(() =>
      // @ts-expect-error -- null is not a value; missing evidence is labelled Unavailable.
      render(<LabelledValue label="Predicted" value={null} unit="test-unit" source="test-source" />),
    ).toThrow(/Unavailable/);
  });

  it('refuses Unavailable without a reason, or with a value, at compile time and at run time', () => {
    // @ts-expect-error -- Unavailable needs a reason.
    expect(() => render(<LabelledValue label="Unavailable" />)).toThrow(/reason/);
    expect(() =>
      // @ts-expect-error -- Unavailable carries no value.
      render(<LabelledValue label="Unavailable" reason="test reason" value="test-value" />),
    ).toThrow(/value/);
    expect(() =>
      // @ts-expect-error -- Unavailable carries no unit.
      render(<LabelledValue label="Unavailable" reason="test reason" unit="test-unit" />),
    ).toThrow(/unit/);
  });

  it('refuses a label outside PRD 3 at compile time and at run time', () => {
    expect(() =>
      // @ts-expect-error -- 'Measured' is not a PRD 3 evidence label.
      render(<LabelledValue label="Measured" value="test-value" unit={null} source="test-source" />),
    ).toThrow(/Measured/);
  });

  it.each([
    ['an empty string', ''],
    ['blank text', '  '],
    ['a hyphen', '-'],
    ['an en dash', '\u2013'],
    ['an em dash', '\u2014'],
    ['a minus sign', '\u2212'],
    ['NaN', Number.NaN],
    ['infinity', Number.POSITIVE_INFINITY],
    ['the text N/A', 'N/A'],
    ['the text NA', ' na '],
    ['the text NaN', 'NaN'],
    ['the text null', 'null'],
    ['the text undefined', 'undefined'],
    ['the text none', 'None'],
    ['the text unknown', 'unknown'],
    ['the text missing', 'Missing'],
    ['the text TBD', 'TBD'],
    ['the text TBC', 'tbc'],
    ['the text TBA', ' TBA '],
    ['a question mark', '?'],
  ] as const)('refuses %s as a value, because missing evidence is labelled Unavailable', (_, value) => {
    expect(() =>
      render(<LabelledValue label="Observed" value={value} unit="test-unit" source="test-source" />),
    ).toThrow(/Unavailable/);
  });

  it.each(['test-none-value', 'test-value tbd', 'unknown-test-value'])(
    'shows %s, because only a value that is nothing but a placeholder is refused',
    (value) => {
      const html = render(<LabelledValue label="Observed" value={value} unit="test-unit" source="test-source" />);
      expect(oneBySlot(html, 'value').text).toBe(value);
    },
  );

  it('refuses a blank reason, source or unit', () => {
    expect(() => render(<LabelledValue label="Unavailable" reason=" " />)).toThrow(/reason/);
    expect(() =>
      render(<LabelledValue label="Derived" value="test-value" unit="test-unit" source="" />),
    ).toThrow(/source/);
    expect(() =>
      render(<LabelledValue label="Derived" value="test-value" unit=" " source="test-source" />),
    ).toThrow(/unit/);
    expect(() => render(<LabelledValue label="Unavailable" reason="test reason" source="" />)).toThrow(/source/);
  });
});

describe('EvidenceLegend', () => {
  const prd = prd3Labels();

  it('keeps the PRD 3 sentence word for word', () => {
    expect(prd.sentence).toBe('Every displayed or exported quantity carries one of these labels.');
    expect(EVIDENCE_LEGEND_SENTENCE).toBe(prd.sentence);
    const html = render(<EvidenceLegend />);
    expect(elements(html).filter((element) => element.tag === 'p').map((element) => element.text)).toEqual([
      prd.sentence,
    ]);
  });

  it('lists the five labels with their meanings as visible text, in PRD 3 order', () => {
    const html = render(<EvidenceLegend />);
    const items = elements(html).filter((element) => element.tag === 'li');
    expect(items.map((element) => element.text)).toEqual(prd.rows.map(([label, meaning]) => `${label} ${meaning}`));
    for (const meaning of bySlot(html, 'evidence-meaning')) expect(classesOf(meaning)).not.toContain('sr-only');
  });

  it('is a section named by its heading, at the requested level', () => {
    for (const level of [2, 3, 4] as const) {
      const html = render(<EvidenceLegend headingLevel={level} />);
      const section = elements(html).find((element) => element.tag === 'section');
      const heading = elements(html).find((element) => element.tag === `h${String(level)}`);
      expect(heading?.text).toBe('Evidence labels');
      expect(section?.attributes.get('aria-labelledby')).toBe(heading?.attributes.get('id'));
    }
    expect(elements(render(<EvidenceLegend />)).some((element) => element.tag === 'h2')).toBe(true);
  });

  it('says nothing beyond the PRD 3 heading, sentence, labels and meanings', () => {
    const expected = ['Evidence labels', prd.sentence, ...prd.rows.map(([label, meaning]) => `${label} ${meaning}`)];
    expect(textOf(render(<EvidenceLegend />))).toBe(expected.join(' '));
  });
});

describe('server components with no figures of their own', () => {
  const FILES = ['evidence-label.tsx', 'evidence-legend.tsx', 'labelled-value.tsx'] as const;

  it('use no client directive', () => {
    for (const name of FILES) {
      const source = readFileSync(join(WEB_DIR, 'components', name), 'utf8');
      expect(source, name).not.toMatch(/^\s*['"]use client['"]/m);
    }
  });

  it('add no digit to the page text', () => {
    const pages = [
      ...EVIDENCE_LABELS.map((label) => <EvidenceLabel key={label} label={label} showMeaning />),
      ...VALUE_LABELS.map((label) => (
        <LabelledValue key={label} label={label} value="test-value" unit="test-unit" source="test-source" />
      )),
      <LabelledValue key="unavailable" label="Unavailable" reason="test reason" source="test-source" />,
      <EvidenceLegend key="legend" />,
    ];
    for (const page of pages) expect(textOf(render(page))).not.toMatch(/\d/);
  });
});
