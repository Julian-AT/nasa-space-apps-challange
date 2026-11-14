// GET /api/v1/compare?a=&b= (PRD 8.5, T15-05). A comparison is either precomputed in a
// comparison file or computed on request with @terraluna/core from stored fingerprints, labelled
// Derived (on request). Neither happens in this batch: comparison and window files have no
// @terraluna/schema schema, and on-request computation waits for T10 and real fingerprints. The
// route answers 404 when the bundle lists no file a comparison could come from, and 501 otherwise.
import { describe, expect, it, vi } from 'vitest';

import { comparisonPath, type PulledBundle, windowsTopNPath } from '../../lib/bundle.ts';
import { CompareQuerySchema } from './compare.ts';
import { VALIDATION_STATUS } from './problem.ts';
import {
  expectProblem,
  mockReader,
  quietApi,
  RELEASE_VERSION,
  releaseBundleReader,
  TEST_BYTES_MARKER,
  testBytes,
} from './test-support.ts';
import { WINDOW_ID_MAX_LENGTH } from './windows.ts';

/** Window ids for these tests only. They are test input, not windows of any bundle. */
const A = 'test-window-alpha';
const B = 'test-window-beta';

function comparePath(query = `a=${A}&b=${B}`): string {
  return `/api/v1/compare?${query}`;
}

describe('GET /api/v1/compare', () => {
  it('is a 404 problem on the mock bundle, naming both windows and the bundle', async () => {
    const { api, log } = quietApi();
    const problem = await expectProblem(await api.request(comparePath()), 404, 'Not Found');
    expect(problem.detail).toBe(
      `A comparison of ${A} and ${B} is not in bundle mock-0: it lists no comparison file and ` +
        'no window file to compare from.',
    );
    expect(problem.instance).toBe('/api/v1/compare');
    expect(log).not.toHaveBeenCalled();
  });

  it('is a 404 problem on a release bundle that lists no comparison or window file', async () => {
    const { api } = quietApi(releaseBundleReader({ targets: [A] }));
    const problem = await expectProblem(await api.request(comparePath()), 404, 'Not Found');
    expect(problem.detail).toContain(`is not in bundle ${RELEASE_VERSION}`);
  });

  const SOURCES: readonly (readonly string[])[] = [
    [comparisonPath(A)],
    ['targets.json'],
    [windowsTopNPath('earth')],
    [comparisonPath(B), 'targets.json', windowsTopNPath('earth')],
  ];

  for (const paths of SOURCES) {
    it(`is a 501 problem, computing nothing, when the bundle lists ${paths.join(', ')}`, async () => {
      const files = Object.fromEntries(paths.map((path) => [path, testBytes('a bundle file')]));
      const { api, log } = quietApi(releaseBundleReader({ targets: [A], files }));
      const response = await api.request(comparePath());
      const text = await response.clone().text();
      const problem = await expectProblem(response, 501, 'Not Implemented');
      expect(problem.detail).toContain(
        `Comparison or window files are in bundle ${RELEASE_VERSION} (${[...paths].sort().join(', ')})`,
      );
      expect(problem.detail).toContain('not served yet');
      expect(problem.detail).toContain('@terraluna/schema has no ComparisonRecord schema');
      expect(problem.detail).toContain('no comparison is computed on request');
      expect(text).not.toContain(TEST_BYTES_MARKER);
      expect(text).not.toContain('Derived');
      expect(log).not.toHaveBeenCalled();
    });
  }

  it('answers HEAD with the problem status and no body', async () => {
    const { api } = quietApi();
    const head = await api.request(comparePath(), { method: 'HEAD' });
    expect(head.status).toBe(404);
    expect(await head.text()).toBe('');
  });

  it('refuses a manifest that fails its schema with a 500 problem', async () => {
    const bundle = mockReader();
    const corrupt = { ...bundle, manifest: { ...bundle.manifest, bundle_version: '' } };
    const { api, log } = quietApi(() => corrupt as unknown as PulledBundle);
    const problem = await expectProblem(
      await api.request(comparePath()),
      500,
      'Internal Server Error',
    );
    expect(problem.detail).toContain('manifest.json');
    expect(log).toHaveBeenCalledOnce();
  });
});

describe('the compare query parameters', () => {
  it('are two window ids, a and b', () => {
    expect(CompareQuerySchema.parse({ a: A, b: B })).toEqual({ a: A, b: B });
  });

  const REFUSED: readonly [string, string, string][] = [
    ['no a', `b=${B}`, 'a'],
    ['no b', `a=${A}`, 'b'],
    ['no parameters', '', 'a'],
    ['an empty a', `a=&b=${B}`, 'a'],
    ['an a with a slash', `a=x%2Fy&b=${B}`, 'a'],
    ['a b with a leading dot', `a=${A}&b=.x`, 'b'],
    ['two values of a', `a=${A}&a=${B}&b=${B}`, 'a'],
    ['a too long', `a=${'w'.repeat(WINDOW_ID_MAX_LENGTH + 1)}&b=${B}`, 'a'],
    ['an unknown parameter', `a=${A}&b=${B}&c=x`, ''],
  ];

  for (const [what, query, name] of REFUSED) {
    it(`refuse ${what} with a ${String(VALIDATION_STATUS)} problem, without reading the bundle`, async () => {
      const reader = vi.fn(mockReader);
      const { api } = quietApi(reader);
      const problem = await expectProblem(
        await api.request(comparePath(query)),
        VALIDATION_STATUS,
        'Bad Request',
      );
      expect(problem.detail).toContain(`query parameter ${name}`);
      expect(reader).not.toHaveBeenCalled();
    });
  }
});
