// The shipping-mode banner and the mock banner that every page shows (PRD 3, 4, 8.6, D08). The
// mode titles are compared with the PRD 4 table read from PRD.md, so a copy that drifts from the
// PRD fails. Every bundle here is synthetic.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { BundleManifestSchema, ValidationSchema } from '@terraluna/schema';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { Bundle } from '../lib/bundle.ts';
import { shellText } from '../lib/shell.ts';
import { contrastRatio } from '../tests/contrast.ts';
import { ALL_GATES_NOT_RUN, gate, manifestFor, toBytes, WEB_DIR } from '../tests/helpers.ts';
import { classesOf, elements, isClientModule, only, textOf } from '../tests/markup.ts';
import { prd4Titles } from '../tests/prd.ts';
import { rootTokens, token } from '../tests/tokens.ts';
import { describeCase, resolveCase, textCases } from '../tests/variant-colours.ts';
import { MockBanner } from './mock-banner.tsx';
import { MODE_TITLES, ModeBanner, modeBannerText } from './mode-banner.tsx';

const AA_TEXT = 4.5;
const MODES = ['A', 'B', 'C'] as const;

function bundle(fields: Record<string, unknown>, records: unknown[] = ALL_GATES_NOT_RUN): Bundle {
  return {
    manifest: BundleManifestSchema.parse(manifestFor(toBytes(records), fields)),
    validation: ValidationSchema.parse(records),
  };
}

function render(element: Parameters<typeof renderToStaticMarkup>[0]): string {
  return renderToStaticMarkup(element);
}

function source(file: string): string {
  return readFileSync(join(WEB_DIR, 'components', file), 'utf8');
}

/** WCAG 2.2 SC 1.4.3 for every state of a class list, on the page background. */
function expectReadable(classes: string, what: string): void {
  const tokens = new Set(rootTokens().keys());
  const cases = textCases(classes, tokens);
  expect(cases.length).toBeGreaterThan(0);
  for (const textCase of cases) {
    const { text, background } = resolveCase(textCase, token('background'), token);
    expect(contrastRatio(text, background), `${what}, ${describeCase(textCase)}`).toBeGreaterThanOrEqual(AA_TEXT);
  }
}

// Wording a banner must never use outside Mode A (PRD 3 wording rules, PRD 4 prohibited claims).
const STRONGER_THAN_MODE = /\bmatch|radar-scattering|\bverified\b|\bbest\b|\bfound\b|\bdiscover|\bconfirmed\b/i;

describe('the PRD 4 mode titles', () => {
  it('are copied word for word for modes A, B and C', () => {
    const prd = prd4Titles();
    expect(Object.keys(prd)).toEqual([...MODES]);
    expect(MODE_TITLES).toStrictEqual(prd);
  });
});

/** The banner sentence: the pill's first paragraph. The bundle version sits in a second one. */
function pillLine(html: string): string {
  const [first] = elements(html).filter((element) => element.tag === 'p');
  if (first === undefined) throw new Error('the pill has no paragraph');
  return first.text;
}

describe('ModeBanner', () => {
  it('shows the bundle version beside the sentence, not inside it', () => {
    const shown = bundle({ shipping_mode: 'C', bundle_version: 'v-test' }, [gate('G0', 'PASS')]);
    const html = render(<ModeBanner bundle={shown} />);
    expect(pillLine(html)).toBe(modeBannerText(shown));
    expect(textOf(html)).toBe(`${modeBannerText(shown)} Bundle v-test`);
  });

  it('reads "Shipping mode: not chosen (G0 <status>)" with the G0 status from validation.json', () => {
    for (const status of ['NOT_RUN', 'PARTIAL', 'FAIL', 'PASS']) {
      const records = [gate('G1', 'PASS'), gate('G0', status)];
      const shown = bundle({ shipping_mode: null }, records);
      expect(modeBannerText(shown)).toBe(`Shipping mode: not chosen (G0 ${status})`);
      expect(pillLine(render(<ModeBanner bundle={shown} />))).toBe(`Shipping mode: not chosen (G0 ${status})`);
    }
  });

  it('says that G0 has no record when validation.json has none, rather than inventing a status', () => {
    const shown = bundle({ shipping_mode: null }, [gate('G1'), gate('G2')]);
    expect(pillLine(render(<ModeBanner bundle={shown} />))).toBe('Shipping mode: not chosen (G0 not recorded)');
  });

  it.each(MODES)('reads "Shipping mode: %s" and the PRD 4 title for that mode', (mode) => {
    const shown = bundle({ shipping_mode: mode }, [gate('G0', 'PASS')]);
    const expected = `Shipping mode: ${mode} — ${prd4Titles()[mode] ?? ''}`;
    expect(modeBannerText(shown)).toBe(expected);
    expect(pillLine(render(<ModeBanner bundle={shown} />))).toBe(expected);
  });

  it('starts with the shipping-mode line that make web-smoke asserts', () => {
    for (const mode of [null, ...MODES]) {
      const shown = bundle({ shipping_mode: mode }, [gate('G0', mode === null ? 'NOT_RUN' : 'PASS')]);
      expect(modeBannerText(shown).startsWith(shellText(shown).shippingMode)).toBe(true);
    }
  });

  it('implies no radar match or analog result outside Mode A (PRD 3, 4)', () => {
    for (const mode of [null, 'B', 'C'] as const) {
      for (const status of ['NOT_RUN', 'PASS', 'FAIL']) {
        const text = pillLine(render(<ModeBanner bundle={bundle({ shipping_mode: mode }, [gate('G0', status)])} />));
        expect(text, text).not.toMatch(STRONGER_THAN_MODE);
        if (mode === null) expect(text).not.toMatch(/analog|candidate|radar/i);
      }
    }
    const modeC = modeBannerText(bundle({ shipping_mode: 'C' }, [gate('G0', 'PASS')]));
    expect(modeC).toContain('radar comparison not completed');
  });

  it('is one prominent line in the page colours, never the red of the mock banner', () => {
    const html = render(<ModeBanner bundle={bundle({ shipping_mode: 'B' }, [gate('G0', 'PASS')])} />);
    const [banner] = elements(html).filter((element) => element.tag === 'p');
    if (banner === undefined) throw new Error('the pill has no paragraph');
    const classes = classesOf(banner);
    expect(classes).toContain('font-semibold');
    expect(classes.join(' ')).not.toMatch(/(^|[\s:])(bg|text|border)-mock\b|-(red|rose)-\d/);
    expectReadable(classes.join(' '), 'mode banner');
  });

  it('is a server component', () => {
    expect(isClientModule(source('mode-banner.tsx'))).toBe(false);
  });
});

describe('MockBanner', () => {
  it('renders nothing for a bundle that is not mock', () => {
    expect(render(<MockBanner bundle={bundle({ mock: false })} />)).toBe('');
  });

  it('reads exactly "Mock bundle — placeholder data, not results" for a mock bundle', () => {
    const html = render(<MockBanner bundle={bundle({ mock: true, bundle_version: 'mock-0' })} />);
    expect(textOf(html)).toBe('Mock bundle — placeholder data, not results');
  });

  it('is a status line on the mock colour whose text, not its colour alone, says it is a mock', () => {
    const html = render(<MockBanner bundle={bundle({ mock: true })} />);
    const all = elements(html);
    const banner = only(
      all.filter((element) => element.attributes.get('role') === 'status'),
      'the status paragraph',
    );
    expect(banner.text.startsWith('Mock bundle')).toBe(true);
    // The strip holds the background; the status paragraph inside it holds the text colour.
    const strip = only(
      all.filter((element) => element.start === 0),
      'the banner strip',
    );
    expect(classesOf(strip)).toContain('bg-mock');
    const classes = [...classesOf(strip), ...classesOf(banner)];
    expect(classes).toContain('text-mock-foreground');
    expectReadable(classes.join(' '), 'mock banner');
  });

  it('is a server component', () => {
    expect(isClientModule(source('mock-banner.tsx'))).toBe(false);
  });
});
