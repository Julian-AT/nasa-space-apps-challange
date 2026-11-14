// The API's shared behaviour (PRD 8.5, T15-02): base path /api/v1, read-only methods and RFC 9457
// problem+json for every error. The tests call app.request(): no Next import, no network, and no
// bundle:pull. The reader is injected, so each test chooses the bundle the API sees.
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import { createRoute, z } from '@hono/zod-openapi';
import { describe, expect, it, vi } from 'vitest';

import {
  assertDeployable,
  BundleError,
  type BundleEnv,
  BundleFileError,
  loadBundle,
  MockBundleError,
} from '../../lib/bundle.ts';
import { MOCK_DIR, tempDir } from '../../tests/helpers.ts';
import { API_BASE_PATH, type BundleReader, createApi } from './app.ts';
import { PROBLEM_MEDIA_TYPE, ProblemSchema, VALIDATION_STATUS } from './problem.ts';
import { RELEASE_VERSION, releaseBundleReader } from './test-support.ts';

const PROBLEM_KEYS = ['detail', 'instance', 'status', 'title', 'type'];

/** The committed mock bundle, read with the T15-01 loader exactly as the API reads apps/web/data/. */
const mockReader: BundleReader = () => loadBundle({ dataDir: MOCK_DIR, env: {} });

/** An app that never logs to the test output; `log` records what the API would log. */
function quietApi(reader: BundleReader = mockReader) {
  const log = vi.fn<(error: unknown) => void>();
  return { api: createApi({ reader, log }), log };
}

/** Checks the problem+json contract and returns the problem. */
async function expectProblem(response: Response, status: number, title: string) {
  expect(response.status).toBe(status);
  expect(response.headers.get('content-type')).toBe(PROBLEM_MEDIA_TYPE);
  expect(response.headers.get('cache-control')).toBe('no-store');
  const body = (await response.json()) as Record<string, unknown>;
  expect(Object.keys(body).sort()).toEqual(PROBLEM_KEYS);
  const problem = ProblemSchema.parse(body);
  expect(problem).toMatchObject({ type: 'about:blank', title, status });
  return problem;
}

describe('base path', () => {
  it('is /api/v1, as in PRD 8.5', () => {
    expect(API_BASE_PATH).toBe('/api/v1');
  });

  it('serves health under the base path only', async () => {
    const { api } = quietApi();
    expect((await api.request('/api/v1/health')).status).toBe(200);
    await expectProblem(await api.request('/health'), 404, 'Not Found');
  });
});

describe('unknown route', () => {
  it('is a 404 problem naming the method and path', async () => {
    const { api } = quietApi();
    const problem = await expectProblem(await api.request('/api/v1/nope'), 404, 'Not Found');
    expect(problem.instance).toBe('/api/v1/nope');
    expect(problem.detail).toContain('GET /api/v1/nope');
  });

  it('is a 404 problem outside the base path too, since the mount covers all of /api', async () => {
    const { api } = quietApi();
    const problem = await expectProblem(await api.request('/api/other'), 404, 'Not Found');
    expect(problem.instance).toBe('/api/other');
  });

  it('keeps the path percent-encoded in instance, so instance stays a URI reference', async () => {
    const { api } = quietApi();
    const response = await api.request('/api/v1/no%20such%22route');
    const problem = await expectProblem(response, 404, 'Not Found');
    expect(problem.instance).toBe('/api/v1/no%20such%22route');
  });

  it('treats a trailing slash as a different path', async () => {
    const { api } = quietApi();
    await expectProblem(await api.request('/api/v1/health/'), 404, 'Not Found');
  });

  it('answers HEAD with the 404 status and headers and no body', async () => {
    const { api } = quietApi();
    const response = await api.request('/api/v1/nope', { method: 'HEAD' });
    expect(response.status).toBe(404);
    expect(response.headers.get('content-type')).toBe(PROBLEM_MEDIA_TYPE);
    expect(await response.text()).toBe('');
  });
});

describe('read-only methods', () => {
  const WRITE_METHODS = ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'];
  const PATHS = ['/api/v1/health', '/api/v1/nope', '/api/other'];

  for (const method of WRITE_METHODS) {
    for (const path of PATHS) {
      it(`refuses ${method} ${path} with 405 and Allow: GET, HEAD`, async () => {
        const reader = vi.fn(mockReader);
        const { api } = quietApi(reader);
        const init: RequestInit = { method };
        if (method !== 'OPTIONS') {
          init.body = JSON.stringify({ status: 'ok' });
          init.headers = { 'Content-Type': 'application/json' };
        }
        const response = await api.request(path, init);
        expect(response.headers.get('allow')).toBe('GET, HEAD');
        const problem = await expectProblem(response, 405, 'Method Not Allowed');
        expect(problem.instance).toBe(path);
        expect(problem.detail).toContain(method);
        expect(reader).not.toHaveBeenCalled();
      });
    }
  }

  // Methods outside Hono's list (get, post, put, delete, options, patch) are refused the same way.
  for (const method of ['PROPFIND', 'PURGE']) {
    it(`refuses the method ${method} too`, async () => {
      const { api } = quietApi();
      const response = await api.request('/api/v1/health', { method });
      expect(response.headers.get('allow')).toBe('GET, HEAD');
      const problem = await expectProblem(response, 405, 'Method Not Allowed');
      expect(problem.detail).toContain(method);
    });
  }
});

describe('validation failures', () => {
  // A route that exists only in this test, so the shared validation hook can be exercised before
  // any public endpoint takes parameters. It is registered on an app built by createApi.
  function probeApi() {
    const { api } = quietApi();
    const route = createRoute({
      method: 'get',
      path: '/test-probe/{id}',
      request: {
        params: z.object({ id: z.string().regex(/^[a-z]+$/) }),
        query: z.object({ limit: z.coerce.number().int().min(1).max(500).default(50) }),
      },
      responses: {
        200: {
          content: {
            'application/json': {
              schema: z.object({ id: z.string(), limit: z.number() }),
            },
          },
          description: 'The validated parameters',
        },
      },
    });
    api.openapi(route, (c) => c.json({ ...c.req.valid('param'), ...c.req.valid('query') }, 200));
    return api;
  }

  it('uses 400 Bad Request: a GET has no content for 422 to describe', () => {
    expect(VALIDATION_STATUS).toBe(400);
  });

  it('passes valid parameters to the handler', async () => {
    const response = await probeApi().request('/api/v1/test-probe/abc?limit=7');
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ id: 'abc', limit: 7 });
  });

  it('is a 400 problem naming the path parameter', async () => {
    const response = await probeApi().request('/api/v1/test-probe/ABC');
    const problem = await expectProblem(response, VALIDATION_STATUS, 'Bad Request');
    expect(problem.detail).toContain('path parameter id');
    expect(problem.instance).toBe('/api/v1/test-probe/ABC');
  });

  it('is a 400 problem naming the query parameter', async () => {
    for (const limit of ['0', '501', '2.5', 'many']) {
      const response = await probeApi().request(`/api/v1/test-probe/abc?limit=${limit}`);
      const problem = await expectProblem(response, VALIDATION_STATUS, 'Bad Request');
      expect(problem.detail).toContain('query parameter limit');
      expect(problem.instance).toBe('/api/v1/test-probe/abc');
    }
  });
});

describe('bundle errors', () => {
  it('turns a missing manifest into a 500 problem naming the file, with no default served', async () => {
    const dataDir = tempDir();
    const { api, log } = quietApi(() => loadBundle({ dataDir, env: {} }));
    const response = await api.request('/api/v1/health');
    const problem = await expectProblem(response, 500, 'Internal Server Error');
    expect(problem.detail).toContain('manifest.json');
    expect(problem.detail).toContain('missing');
    // The bundle-relative path is public (the manifest lists it); the server's directory is not.
    expect(problem.detail).not.toContain(dataDir);
    expect(log).toHaveBeenCalledOnce();
    expect(log.mock.calls[0]?.[0]).toBeInstanceOf(BundleFileError);
  });

  it('turns a missing validation.json into a 500 problem naming that file', async () => {
    const dataDir = tempDir();
    writeFileSync(join(dataDir, 'manifest.json'), readFileSync(join(MOCK_DIR, 'manifest.json')));
    const { api } = quietApi(() => loadBundle({ dataDir, env: {} }));
    const response = await api.request('/api/v1/health');
    const problem = await expectProblem(response, 500, 'Internal Server Error');
    expect(problem.detail).toContain('validation.json');
    expect(problem.detail).toContain('missing');
  });

  it('turns a schema-invalid manifest into a 500 problem naming the file', async () => {
    const dataDir = tempDir();
    const manifest = JSON.parse(readFileSync(join(MOCK_DIR, 'manifest.json'), 'utf8')) as object;
    writeFileSync(join(dataDir, 'manifest.json'), JSON.stringify({ ...manifest, schema_version: 1 }));
    writeFileSync(join(dataDir, 'validation.json'), readFileSync(join(MOCK_DIR, 'validation.json')));
    const { api } = quietApi(() => loadBundle({ dataDir, env: {} }));
    const response = await api.request('/api/v1/health');
    const problem = await expectProblem(response, 500, 'Internal Server Error');
    expect(problem.detail).toContain('manifest.json');
    expect(problem.detail).toContain('invalid');
  });

  it('turns a changed validation.json into a 500 problem naming the file', async () => {
    const dataDir = tempDir();
    writeFileSync(join(dataDir, 'manifest.json'), readFileSync(join(MOCK_DIR, 'manifest.json')));
    writeFileSync(join(dataDir, 'validation.json'), '[]');
    const { api } = quietApi(() => loadBundle({ dataDir, env: {} }));
    const response = await api.request('/api/v1/health');
    const problem = await expectProblem(response, 500, 'Internal Server Error');
    expect(problem.detail).toContain('validation.json');
  });

  it('refuses the mock bundle on production through the loader guard with a 503, serving nothing', async () => {
    const { api, log } = quietApi(() =>
      loadBundle({ dataDir: MOCK_DIR, env: { VERCEL_ENV: 'production' } }),
    );
    const response = await api.request('/api/v1/health');
    const problem = await expectProblem(response, 503, 'Service Unavailable');
    expect(problem.detail).not.toContain('mock-0');
    expect(log.mock.calls[0]?.[0]).toBeInstanceOf(MockBundleError);
  });

  it('turns any other bundle refusal into a 500 problem without its message', async () => {
    const { api } = quietApi(() => {
      throw new BundleError('release/secret-dir/manifest.json is bundle_version x');
    });
    const response = await api.request('/api/v1/health');
    const problem = await expectProblem(response, 500, 'Internal Server Error');
    expect(problem.detail).not.toContain('secret-dir');
    expect(problem.detail).toContain('bundle');
  });
});

describe('unexpected errors', () => {
  it('are a 500 problem with a fixed detail: no message and no stack trace', async () => {
    const error = new Error('internal detail 7f3a at /srv/app/server.ts:12');
    const { api, log } = quietApi(() => {
      throw error;
    });
    const response = await api.request('/api/v1/health');
    const text = await response.clone().text();
    const problem = await expectProblem(response, 500, 'Internal Server Error');
    expect(problem.instance).toBe('/api/v1/health');
    expect(text).not.toContain('7f3a');
    expect(text).not.toContain('server.ts');
    expect(text).not.toContain('stack');
    expect(text).not.toMatch(/\bat \S+ \(/);
    expect(log).toHaveBeenCalledWith(error);
  });

  it('are a 500 problem even when the thrown value is not an Error', async () => {
    const { api, log } = quietApi(() => {
      // eslint-disable-next-line @typescript-eslint/only-throw-error -- the case under test
      throw 'a string, not an Error';
    });
    const response = await api.request('/api/v1/health');
    const text = await response.clone().text();
    await expectProblem(response, 500, 'Internal Server Error');
    expect(text).not.toContain('a string');
    expect(log).toHaveBeenCalledOnce();
  });

  it('log through console.error by default', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    try {
      const error = new Error('boom');
      const api = createApi({
        reader: () => {
          throw error;
        },
      });
      await expectProblem(await api.request('/api/v1/health'), 500, 'Internal Server Error');
      expect(spy).toHaveBeenCalledWith(error);
    } finally {
      spy.mockRestore();
    }
  });
});

describe('the production mock guard (T15-07)', () => {
  /** What assertDeployable, the loader's guard, makes of the manifest under env. */
  function guardOutcome(reader: BundleReader, env: BundleEnv): number {
    try {
      assertDeployable(reader().manifest, env);
      return 200;
    } catch (error) {
      if (error instanceof MockBundleError) return 503;
      if (error instanceof BundleError) return 500;
      throw error;
    }
  }

  const ENVS: readonly BundleEnv[] = [
    {},
    { VERCEL_ENV: '' },
    { VERCEL_ENV: 'development' },
    { VERCEL_ENV: 'preview' },
    { VERCEL_ENV: 'production' },
    // Not a Vercel environment: the loader refuses it for a mock rather than guessing.
    { VERCEL_ENV: 'prod' },
  ];

  it('answers exactly as the imported assertDeployable decides, for mock and release bundles', async () => {
    const readers: readonly [string, BundleReader][] = [
      ['mock', mockReader],
      ['release', releaseBundleReader()],
    ];
    const seen = new Set<number>();
    for (const [name, reader] of readers) {
      for (const env of ENVS) {
        const expected = guardOutcome(reader, env);
        seen.add(expected);
        const api = createApi({ reader, env, log: () => undefined });
        const response = await api.request('/api/v1/health');
        expect(response.status, `${name} ${JSON.stringify(env)}`).toBe(expected);
      }
    }
    // The table covers serving, the refusal and an unknown VERCEL_ENV.
    expect([...seen].sort()).toEqual([200, 500, 503]);
  });

  it('refuses a mock that an injected reader returns, on every route that reads the bundle', async () => {
    const log = vi.fn<(error: unknown) => void>();
    const api = createApi({ reader: mockReader, env: { VERCEL_ENV: 'production' }, log });
    const paths = ['/api/v1/health', '/api/v1/manifest', '/api/v1/gates', '/api/v1/targets'];
    for (const path of paths) {
      const response = await api.request(path);
      const text = await response.clone().text();
      const problem = await expectProblem(response, 503, 'Service Unavailable');
      expect(problem.instance).toBe(path);
      expect(response.headers.get('etag')).toBeNull();
      expect(text).not.toContain('mock-0');
    }
    expect(log).toHaveBeenCalledTimes(paths.length);
    for (const [error] of log.mock.calls) expect(error).toBeInstanceOf(MockBundleError);
  });

  it('serves a release bundle on production', async () => {
    const api = createApi({ reader: releaseBundleReader(), env: { VERCEL_ENV: 'production' } });
    const response = await api.request('/api/v1/health');
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ bundle_version: RELEASE_VERSION });
  });

  it('reads VERCEL_ENV from process.env on each request by default', async () => {
    const log = vi.fn<(error: unknown) => void>();
    const api = createApi({ reader: mockReader, log });
    try {
      vi.stubEnv('VERCEL_ENV', 'production');
      await expectProblem(await api.request('/api/v1/health'), 503, 'Service Unavailable');
      vi.stubEnv('VERCEL_ENV', 'preview');
      expect((await api.request('/api/v1/health')).status).toBe(200);
    } finally {
      vi.unstubAllEnvs();
    }
    expect(log).toHaveBeenCalledOnce();
  });

  it('does not read the bundle for an expected problem before the guard, so 405 stays 405', async () => {
    const reader = vi.fn<BundleReader>(mockReader);
    const api = createApi({ reader, env: { VERCEL_ENV: 'production' }, log: () => undefined });
    const response = await api.request('/api/v1/health', { method: 'DELETE' });
    await expectProblem(response, 405, 'Method Not Allowed');
    expect(reader).not.toHaveBeenCalled();
  });
});
