// /methods (PRD 8.6, 9, 10 "Validation board"): a static page inside the root layout with one h1
// and the validation board read from the bundle's validation.json. The bundle and the pathname
// are synthetic here: loadBundle and usePathname are replaced.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { BundleManifestSchema, ValidationSchema } from '@terraluna/schema';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import RootLayout from '../app/layout.tsx';
import Methods, { dynamic } from '../app/methods/page.tsx';
import { GatesBoard } from '../components/gates-board.tsx';
import type { Bundle } from '../lib/bundle.ts';
import { MOCK_BANNER, shellText } from '../lib/shell.ts';
import { modeBannerText } from '../components/mode-banner.tsx';
import { gate, manifestFor, MOCK_DIR, toBytes, WEB_DIR } from './helpers.ts';
import { elements, textOf } from './markup.ts';

interface TestState {
  bundle: Bundle | null;
}

const state: TestState = vi.hoisted((): TestState => ({ bundle: null }));

vi.mock('next/navigation', () => ({ usePathname: () => '/methods' }));

vi.mock('../lib/bundle.ts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/bundle.ts')>();
  return {
    ...actual,
    loadBundle: () => {
      if (state.bundle === null) throw new Error('the test set no bundle');
      return state.bundle;
    },
  };
});

function bundle(fields: Record<string, unknown>, records: unknown): Bundle {
  return {
    manifest: BundleManifestSchema.parse(manifestFor(toBytes(records), fields)),
    validation: ValidationSchema.parse(records),
  };
}

const MOCK_RECORDS: unknown = JSON.parse(readFileSync(join(MOCK_DIR, 'validation.json'), 'utf8'));
const MOCK = bundle({ mock: true, shipping_mode: null, bundle_version: 'mock-0' }, MOCK_RECORDS);

const PAGE_SOURCE = readFileSync(join(WEB_DIR, 'app', 'methods', 'page.tsx'), 'utf8');

// useId gives different ids in separate render trees, so markup is compared without them.
function withoutIds(html: string): string {
  return html.replace(/\b(id|aria-describedby|aria-labelledby)="[^"]*"/g, '$1=""');
}

function page(): string {
  return withoutIds(renderToStaticMarkup(<Methods />));
}

function board(validation: Bundle['validation']): string {
  return withoutIds(renderToStaticMarkup(<GatesBoard validation={validation} />));
}

function inLayout(): string {
  return renderToStaticMarkup(
    <RootLayout>
      <Methods />
    </RootLayout>,
  );
}

beforeEach(() => {
  state.bundle = MOCK;
});

describe('/methods', () => {
  it('is statically rendered: next build fails if anything would make it dynamic', () => {
    expect(dynamic).toBe('error');
  });

  it('has exactly one h1 and renders no main landmark of its own', () => {
    const found = elements(inLayout());
    expect(found.filter((element) => element.tag === 'h1').map((element) => element.text)).toEqual(['Methods']);
    expect(found.filter((element) => element.tag === 'main')).toHaveLength(1);
    expect(page()).not.toMatch(/<main\b/);
  });

  it('shows the board for the bundle validation.json, exactly as GatesBoard renders it', () => {
    expect(page()).toContain(board(MOCK.validation));
    expect(elements(page()).filter((element) => element.tag === 'table')).toHaveLength(1);
  });

  it('follows the selected bundle: other records give another board', () => {
    state.bundle = bundle({ mock: false, shipping_mode: 'B', bundle_version: 'v1' }, [
      { ...gate('G0', 'PASS'), scope: 'Site01 and one Earth radar ROI' },
    ]);
    const html = page();
    expect(html).toContain(board(state.bundle.validation));
    expect(textOf(html)).toContain('Site01 and one Earth radar ROI');
  });

  it('shows the mock banner and the mode banner through the layout', () => {
    const text = elements(inLayout()).map((element) => element.text);
    expect(text).toContain(MOCK_BANNER);
    expect(text).toContain(modeBannerText(MOCK));
    expect(textOf(inLayout())).toContain(shellText(MOCK).footer);
  });

  it('has no encoder, model or embedding content before G5 passes', () => {
    const forbidden = /encoder|model|embedding|onnx|learned|second opinion|predicted/i;
    expect(textOf(page())).not.toMatch(forbidden);
    expect(PAGE_SOURCE).not.toMatch(forbidden);
  });

  it('types no status, result or threshold: the page text beyond its headings comes from the board', () => {
    const rest = textOf(page().replace(board(MOCK.validation), ' '));
    expect(rest).toBe('Methods Validation board');
    expect(PAGE_SOURCE).not.toMatch(/NOT_RUN|PASS|FAIL|PARTIAL|NOT_APPLICABLE/);
  });
});
