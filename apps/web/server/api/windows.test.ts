// GET /api/v1/windows/{id} (PRD 8.5, T15-05). Window records live in targets.json (target
// windows) and windows/<body>.json (the top-N subset), which have no @terraluna/schema schema yet.
// The route answers 404 when no file the bundle lists could hold the window: targets.json holds
// only the target windows manifest.targets lists. It answers 501 when one could: the window could
// be in it, but nothing without a schema is read, so the id is not looked up.
import { describe, expect, it, vi } from 'vitest';

import { comparisonPath, type PulledBundle, windowsTopNPath } from '../../lib/bundle.ts';
import { VALIDATION_STATUS } from './problem.ts';
import { TARGET_ID_MAX_LENGTH, TARGET_ID_PATTERN } from './targets.ts';
import {
  expectProblem,
  mockReader,
  quietApi,
  RELEASE_VERSION,
  releaseBundleReader,
  TEST_BYTES_MARKER,
  testBytes,
} from './test-support.ts';
import { WINDOW_ID_MAX_LENGTH, WINDOW_ID_PATTERN, WindowIdSchema } from './windows.ts';

/** Window ids for these tests only. They are test input, not windows of any bundle. */
const WINDOW = 'test-window-alpha';
const OTHER = 'test-window-gamma';

function windowPath(id: string): string {
  return `/api/v1/windows/${id}`;
}

describe('GET /api/v1/windows/{id}', () => {
  it('is a 404 problem on the mock bundle, naming the window and the bundle', async () => {
    const { api, log } = quietApi();
    const problem = await expectProblem(await api.request(windowPath(WINDOW)), 404, 'Not Found');
    expect(problem.detail).toBe(
      `Window ${WINDOW} is not in bundle mock-0: it lists no window file the API reads ` +
        '(targets.json or windows/<body>.json).',
    );
    expect(problem.instance).toBe(windowPath(WINDOW));
    expect(log).not.toHaveBeenCalled();
  });

  it('is a 404 problem on a release bundle that lists no window file', async () => {
    const reader = releaseBundleReader({
      targets: [WINDOW],
      files: { [comparisonPath(WINDOW)]: testBytes('a comparison file') },
    });
    const { api } = quietApi(reader);
    const problem = await expectProblem(await api.request(windowPath(WINDOW)), 404, 'Not Found');
    expect(problem.detail).toContain(`Window ${WINDOW} is not in bundle ${RELEASE_VERSION}`);
  });

  // targets.json holds the target windows, whose ids manifest.targets lists (PRD 8.4: a target id
  // is its window id, moon-cr-site01-w000), so it can hold only those. windows/<body>.json, the
  // top-N subset, can hold any window and is not read, so an id there cannot be told apart.
  const HOLDING: readonly [string, readonly string[], readonly string[], readonly string[]][] = [
    ['a target window: targets.json', [WINDOW], ['targets.json'], ['targets.json']],
    ['any window: a top-N file', [], [windowsTopNPath('earth')], [windowsTopNPath('earth')]],
    [
      'a target window: every window file',
      [WINDOW],
      ['targets.json', windowsTopNPath('moon'), windowsTopNPath('earth')],
      ['targets.json', windowsTopNPath('earth'), windowsTopNPath('moon')],
    ],
    [
      'another window: only the top-N files are named',
      [OTHER],
      ['targets.json', windowsTopNPath('moon'), windowsTopNPath('earth')],
      [windowsTopNPath('earth'), windowsTopNPath('moon')],
    ],
    [
      'a target window: only a top-N file',
      [WINDOW],
      [windowsTopNPath('moon'), comparisonPath(WINDOW)],
      [windowsTopNPath('moon')],
    ],
  ];

  for (const [what, targets, paths, named] of HOLDING) {
    it(`is a 501 problem when a listed file could hold ${what}`, async () => {
      const files = Object.fromEntries(paths.map((path) => [path, testBytes('a window file')]));
      const { api, log } = quietApi(releaseBundleReader({ targets, files }));
      const response = await api.request(windowPath(WINDOW));
      const text = await response.clone().text();
      const problem = await expectProblem(response, 501, 'Not Implemented');
      expect(problem.detail).toBe(
        `Window files that could hold window ${WINDOW} are in bundle ${RELEASE_VERSION} ` +
          `(${named.join(', ')}), but window records are not served yet: @terraluna/schema has ` +
          'no WindowRecord schema, so the id is not looked up in them.',
      );
      expect(text).not.toContain(TEST_BYTES_MARKER);
      expect(log).not.toHaveBeenCalled();
    });
  }

  // An id that no listed file could hold is not in the bundle, whatever else it lists.
  const NOT_HELD: readonly [string, readonly string[], readonly string[]][] = [
    ['an id that is not a target, with only targets.json', [OTHER], ['targets.json']],
    ['an id with no target at all, with only targets.json', [], ['targets.json']],
    [
      'an id whose comparison file is listed, but not as a target',
      [OTHER],
      ['targets.json', comparisonPath(WINDOW), comparisonPath(OTHER)],
    ],
    [
      'an id that only shares a prefix with targets',
      [WINDOW.slice(0, -1), `${WINDOW}-x`],
      ['targets.json'],
    ],
    ['an id a target matches only without case', [WINDOW.toUpperCase()], ['targets.json']],
  ];

  for (const [what, targets, paths] of NOT_HELD) {
    it(`is a 404 problem for ${what}`, async () => {
      const files = Object.fromEntries(paths.map((path) => [path, testBytes('a bundle file')]));
      const { api, log } = quietApi(releaseBundleReader({ targets, files }));
      const problem = await expectProblem(await api.request(windowPath(WINDOW)), 404, 'Not Found');
      expect(problem.detail).toBe(
        `Window ${WINDOW} is not in bundle ${RELEASE_VERSION}: targets.json holds only the ` +
          'target windows manifest.json lists, which do not include it, and the bundle lists no ' +
          'windows/<body>.json.',
      );
      expect(log).not.toHaveBeenCalled();
    });
  }

  it('is a 404 problem for a target window when the bundle lists no window file', async () => {
    const reader = releaseBundleReader({
      targets: [WINDOW],
      files: { [comparisonPath(WINDOW)]: testBytes('a comparison file') },
    });
    const problem = await expectProblem(
      await quietApi(reader).api.request(windowPath(WINDOW)),
      404,
      'Not Found',
    );
    expect(problem.detail).toBe(
      `Window ${WINDOW} is not in bundle ${RELEASE_VERSION}: it lists no window file the API ` +
        'reads (targets.json or windows/<body>.json).',
    );
  });

  it('answers HEAD with the problem status and no body', async () => {
    const { api } = quietApi();
    const head = await api.request(windowPath(WINDOW), { method: 'HEAD' });
    expect(head.status).toBe(404);
    expect(await head.text()).toBe('');
  });

  it('refuses a manifest that fails its schema with a 500 problem', async () => {
    const bundle = mockReader();
    const corrupt = { ...bundle, manifest: { ...bundle.manifest, files: [{ path: '/x' }] } };
    const { api, log } = quietApi(() => corrupt as unknown as PulledBundle);
    const problem = await expectProblem(
      await api.request(windowPath(WINDOW)),
      500,
      'Internal Server Error',
    );
    expect(problem.detail).toContain('manifest.json');
    expect(log).toHaveBeenCalledOnce();
  });

  for (const [what, id] of [
    ['a leading dot', '.test-window'],
    ['an encoded slash', 'test%2Fwindow'],
    ['a colon', 'test:window'],
    ['too many characters', 'w'.repeat(WINDOW_ID_MAX_LENGTH + 1)],
  ] as const) {
    it(`is a ${String(VALIDATION_STATUS)} problem for an id with ${what}, without reading the bundle`, async () => {
      const reader = vi.fn(mockReader);
      const { api } = quietApi(reader);
      const problem = await expectProblem(
        await api.request(windowPath(id)),
        VALIDATION_STATUS,
        'Bad Request',
      );
      expect(problem.detail).toContain('path parameter id');
      expect(reader).not.toHaveBeenCalled();
    });
  }

  it(`accepts an id of exactly ${String(WINDOW_ID_MAX_LENGTH)} characters`, async () => {
    const { api } = quietApi();
    const id = 'w'.repeat(WINDOW_ID_MAX_LENGTH);
    await expectProblem(await api.request(windowPath(id)), 404, 'Not Found');
  });
});

describe('the window id parameter', () => {
  it('uses the target id pattern and length limit', () => {
    expect(WINDOW_ID_PATTERN).toBe(TARGET_ID_PATTERN);
    expect(WINDOW_ID_MAX_LENGTH).toBe(TARGET_ID_MAX_LENGTH);
  });

  it('accepts one bundle path segment and refuses anything else', () => {
    for (const id of ['test-window-alpha', 'a', 'A-Z_0.9', 'w'.repeat(128)]) {
      expect(WindowIdSchema.safeParse(id).success, id).toBe(true);
    }
    for (const id of ['', '.a', 'a/b', 'a b', 'é', 'w'.repeat(129)]) {
      expect(WindowIdSchema.safeParse(id).success, JSON.stringify(id)).toBe(false);
    }
  });
});
