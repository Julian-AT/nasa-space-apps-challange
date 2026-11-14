// make web-smoke fetches / from next start and checks the rendered markup, not the RSC payload
// in <script> tags, against the pulled bundle.
import { BundleManifestSchema, ValidationSchema } from '@terraluna/schema';
import { describe, expect, it } from 'vitest';

import type { Bundle } from '../lib/bundle.ts';
import { pageProblems } from '../scripts/page-check.ts';
import { ALL_GATES_NOT_RUN, manifestFor, toBytes } from './helpers.ts';

function bundle(fields: Record<string, unknown>): Bundle {
  return {
    manifest: BundleManifestSchema.parse(manifestFor(toBytes(ALL_GATES_NOT_RUN), fields)),
    validation: ValidationSchema.parse(ALL_GATES_NOT_RUN),
  };
}

const MOCK = bundle({ mock: true, shipping_mode: null, bundle_version: 'mock-0' });
const REAL = bundle({ mock: false, shipping_mode: 'B', bundle_version: 'v1' });

function page(body: string): string {
  return `<!DOCTYPE html><html lang="en"><head><title>Terraluna</title></head><body>${body}<script>self.__next_f.push([1,"Mock bundle — placeholder data, not results Shipping mode: C"])</script></body></html>`;
}

const MOCK_PAGE = page(
  '<div>Mock bundle — placeholder data, not results</div><p>Shipping mode: not chosen (G0 <!-- -->NOT_RUN<!-- -->)</p><main><h1>Terraluna</h1></main><footer>Bundle version: <!-- -->mock-0</footer>',
);

describe('pageProblems', () => {
  it('accepts a page with the banner, the shipping-mode line and the footer version', () => {
    expect(pageProblems(MOCK_PAGE, MOCK)).toStrictEqual([]);
    expect(
      pageProblems(
        page('<p>Shipping mode: B</p><main></main><footer>Bundle version: v1</footer>'),
        REAL,
      ),
    ).toStrictEqual([]);
  });

  it('reports a missing mock banner, even when the RSC payload holds its text', () => {
    const html = MOCK_PAGE.replace('<div>Mock bundle — placeholder data, not results</div>', '');
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/banner/);
  });

  it('reports a mock banner on a bundle that is not mock', () => {
    const html = page(
      '<div>Mock bundle — placeholder data, not results</div><p>Shipping mode: B</p><footer>Bundle version: v1</footer>',
    );
    expect(pageProblems(html, REAL).join('\n')).toMatch(/banner/);
  });

  it('reports a wrong shipping-mode line', () => {
    const html = MOCK_PAGE.replace('NOT_RUN', 'PASS');
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/Shipping mode: not chosen \(G0 NOT_RUN\)/);
  });

  it('reports a bundle version that is not in the footer', () => {
    const html = MOCK_PAGE.replace('<footer>Bundle version: <!-- -->mock-0</footer>', '<p>Bundle version: mock-0</p>');
    expect(pageProblems(html, MOCK).join('\n')).toMatch(/footer/);
  });
});
