// GET /api/v1/windows/{id} (PRD 8.5, T15-05). Window records live in targets.json (target
// windows) and windows/<body>.json (the top-N subset), which have no @terraluna/schema schema yet.
// The route answers 404 when the bundle lists neither, and 501 when it lists one: the window could
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

/** A window id for these tests only. It is test input, not a window of any bundle. */
const WINDOW = 'test-window-alpha';

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

  const WINDOW_FILES: readonly (readonly string[])[] = [
    ['targets.json'],
    [windowsTopNPath('earth')],
    ['targets.json', windowsTopNPath('earth'), windowsTopNPath('moon')],
  ];

  for (const paths of WINDOW_FILES) {
    it(`is a 501 problem naming ${paths.join(', ')} when the bundle lists them`, async () => {
      const files = Object.fromEntries(paths.map((path) => [path, testBytes('a window file')]));
      const { api, log } = quietApi(releaseBundleReader({ files }));
      const response = await api.request(windowPath(WINDOW));
      const text = await response.clone().text();
      const problem = await expectProblem(response, 501, 'Not Implemented');
      expect(problem.detail).toContain(
        `Window files are in bundle ${RELEASE_VERSION} (${[...paths].sort().join(', ')})`,
      );
      expect(problem.detail).toContain('not served yet');
      expect(problem.detail).toContain('@terraluna/schema has no WindowRecord schema');
      expect(problem.detail).toContain(WINDOW);
      expect(text).not.toContain(TEST_BYTES_MARKER);
      expect(log).not.toHaveBeenCalled();
    });
  }

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
