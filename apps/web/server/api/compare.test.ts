// GET /api/v1/compare?a=&b= (PRD 8.5, T15-05). A comparison is either precomputed in a
// comparison file or computed on request with @terraluna/core from stored fingerprints, labelled
// Derived (on request). Neither happens in this batch: comparison and window files have no
// @terraluna/schema schema, and on-request computation waits for T10 and real fingerprints. The
// route answers 404 when no file the bundle lists could hold the comparison of a and b or the
// windows it would be computed from, and 501 when one could.
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
const OTHER = 'test-window-gamma';

function comparePath(query = `a=${A}&b=${B}`): string {
  return `/api/v1/compare?${query}`;
}

describe('GET /api/v1/compare', () => {
  it('is a 404 problem on the mock bundle, naming both windows and the bundle', async () => {
    const { api, log } = quietApi();
    const problem = await expectProblem(await api.request(comparePath()), 404, 'Not Found');
    expect(problem.detail).toBe(
      `A comparison of ${A} and ${B} is not in bundle mock-0: it lists neither ` +
        `comparisons/${A}.json nor comparisons/${B}.json, and no window file it lists could ` +
        `hold ${A} or ${B} to compute one from.`,
    );
    expect(problem.instance).toBe('/api/v1/compare');
    expect(log).not.toHaveBeenCalled();
  });

  // A comparison of a and b could be precomputed in comparisons/<a>.json or comparisons/<b>.json
  // (a ComparisonRecord there has that target as its target window, PRD 8.4 and 8.12), or computed
  // on request from the fingerprints of both windows. A bundle with neither does not hold it.
  const NOT_HELD: readonly [string, readonly string[], readonly string[], string][] = [
    ['no comparison or window file', [A], [], `${A} or ${B}`],
    [
      'only the comparison file of another target',
      [OTHER],
      [comparisonPath(OTHER)],
      `${A} or ${B}`,
    ],
    ['targets.json, which could hold only a', [A], ['targets.json'], B],
    ['targets.json, which could hold only b', [B, OTHER], ['targets.json'], A],
    [
      'targets.json and another comparison file, with neither a target',
      [OTHER],
      ['targets.json', comparisonPath(OTHER)],
      `${A} or ${B}`,
    ],
  ];

  for (const [what, targets, paths, missing] of NOT_HELD) {
    it(`is a 404 problem on a release bundle that lists ${what}`, async () => {
      const files = Object.fromEntries(paths.map((path) => [path, testBytes('a bundle file')]));
      const { api, log } = quietApi(releaseBundleReader({ targets, files }));
      const problem = await expectProblem(await api.request(comparePath()), 404, 'Not Found');
      expect(problem.detail).toBe(
        `A comparison of ${A} and ${B} is not in bundle ${RELEASE_VERSION}: it lists neither ` +
          `comparisons/${A}.json nor comparisons/${B}.json, and no window file it lists could ` +
          `hold ${missing} to compute one from.`,
      );
      expect(log).not.toHaveBeenCalled();
    });
  }

  it('names the one comparison file when a and b are the same window', async () => {
    const { api } = quietApi(releaseBundleReader({ targets: [A], files: {} }));
    const problem = await expectProblem(
      await api.request(comparePath(`a=${A}&b=${A}`)),
      404,
      'Not Found',
    );
    expect(problem.detail).toBe(
      `A comparison of ${A} and ${A} is not in bundle ${RELEASE_VERSION}: it lists no ` +
        `comparisons/${A}.json, and no window file it lists could hold ${A} to compute one from.`,
    );
  });

  // [what, targets, listed paths, the paths the 501 names]
  const HELD: readonly [string, readonly string[], readonly string[], readonly string[]][] = [
    ['the comparison file of a', [A], [comparisonPath(A)], [comparisonPath(A)]],
    ['the comparison file of b', [B], [comparisonPath(B)], [comparisonPath(B)]],
    ['targets.json, holding both targets', [A, B], ['targets.json'], ['targets.json']],
    ['a top-N file', [A], [windowsTopNPath('earth')], [windowsTopNPath('earth')]],
    [
      'targets.json for a and a top-N file for both',
      [A],
      [windowsTopNPath('earth'), 'targets.json'],
      ['targets.json', windowsTopNPath('earth')],
    ],
    [
      'the comparison file of b, targets.json and a top-N file',
      [A],
      [windowsTopNPath('earth'), comparisonPath(B), 'targets.json'],
      [comparisonPath(B), 'targets.json', windowsTopNPath('earth')],
    ],
    [
      'a top-N file, naming no comparison file of another target',
      [OTHER],
      [comparisonPath(OTHER), 'targets.json', windowsTopNPath('earth')],
      [windowsTopNPath('earth')],
    ],
  ];

  for (const [what, targets, paths, named] of HELD) {
    it(`is a 501 problem, computing nothing, when the bundle lists ${what}`, async () => {
      const files = Object.fromEntries(paths.map((path) => [path, testBytes('a bundle file')]));
      const { api, log } = quietApi(releaseBundleReader({ targets, files }));
      const response = await api.request(comparePath());
      const text = await response.clone().text();
      const problem = await expectProblem(response, 501, 'Not Implemented');
      expect(problem.detail).toBe(
        `Comparison or window files that could hold a comparison of ${A} and ${B} are in bundle ` +
          `${RELEASE_VERSION} (${named.join(', ')}), but comparisons are not served yet: ` +
          '@terraluna/schema has no ComparisonRecord schema, and no comparison is computed on ' +
          'request.',
      );
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
    // An empty bundle_version still type-checks as a string, but fails the manifest schema.
    const corrupt: PulledBundle = {
      ...bundle,
      manifest: { ...bundle.manifest, bundle_version: '' },
    };
    const { api, log } = quietApi(() => corrupt);
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
