// The Next mount (PRD 8.5, D24, T15-07): app/api/[[...route]]/route.ts serves the API instance that
// ./index.ts exports through typed (req: Request) => app.fetch(req) wrappers (hono issue 4877), not
// through handle(app) from hono/vercel. These tests import that instance and the route module, and
// call them as Next does, with a Request: no Next server and no network.
//
// The instance reads the bundle with the loader's defaults: apps/web/data/ under process.cwd() and
// process.env. Each bundle test pulls the committed mock bundle into a temporary directory, points
// process.cwd() at it and sets VERCEL_ENV, so no test depends on an earlier bundle:pull.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { afterEach, describe, expect, it, vi } from 'vitest';

import * as route from '../../app/api/[[...route]]/route.ts';
import { MockBundleError, pullBundle } from '../../lib/bundle.ts';
import { REPO_ROOT, tempDir, WEB_DIR } from '../../tests/helpers.ts';
import { app } from './index.ts';
import { expectProblem, mockFile } from './test-support.ts';

const ROUTE_FILE = join(WEB_DIR, 'app', 'api', '[[...route]]', 'route.ts');
const ORIGIN = 'http://localhost';

/** The methods Next routes to a route handler (Next 16 route.md), other than GET and HEAD. */
const OTHER_METHODS = ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'] as const;

type Method = 'GET' | 'HEAD' | (typeof OTHER_METHODS)[number];

/** Calls the route module's export for the method, as Next does. */
function viaRoute(method: Method, path: string): Promise<Response> {
  return Promise.resolve(route[method](new Request(`${ORIGIN}${path}`, { method })));
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

/**
 * Pulls the committed mock bundle into <tmp>/data/, makes <tmp> the working directory the loader
 * sees and sets VERCEL_ENV ('' is unset). BUNDLE_VERSION is empty, which selects the mock.
 */
function serveMockBundle(vercelEnv: string): void {
  const work = tempDir();
  pullBundle({ repoRoot: REPO_ROOT, outDir: join(work, 'data'), env: {} });
  vi.spyOn(process, 'cwd').mockReturnValue(work);
  vi.stubEnv('BUNDLE_VERSION', '');
  vi.stubEnv('VERCEL_ENV', vercelEnv);
}

/** The errors the API instance logs (console.error by default), kept out of the test output. */
function capturedLog() {
  return vi.spyOn(console, 'error').mockImplementation(() => undefined);
}

describe('the route module', () => {
  it('exports only the method handlers and the runtime and dynamic segment config', () => {
    expect(Object.keys(route).sort()).toEqual(
      ['DELETE', 'GET', 'HEAD', 'OPTIONS', 'PATCH', 'POST', 'PUT', 'dynamic', 'runtime'].sort(),
    );
  });

  it('runs on Node.js, which the loader needs to read apps/web/data/, and is never prerendered', () => {
    expect(route.runtime).toBe('nodejs');
    expect(route.dynamic).toBe('force-dynamic');
  });

  it('hands GET and HEAD to the API instance with app.fetch(req) and nothing else', async () => {
    const fetch = vi.spyOn(app, 'fetch');
    for (const method of ['GET', 'HEAD'] as const) {
      const request = new Request(`${ORIGIN}/api/v1/openapi.json`, { method });
      const response = await route[method](request);
      expect(fetch).toHaveBeenLastCalledWith(request);
      expect(fetch.mock.lastCall).toHaveLength(1);
      expect(response.status).toBe(200);
      if (method === 'HEAD') expect(await response.text()).toBe('');
    }
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('wraps app.fetch itself instead of handle(app) from hono/vercel, citing hono issue 4877', () => {
    const source = readFileSync(ROUTE_FILE, 'utf8');
    expect(source).not.toMatch(/hono\/vercel/);
    expect(source).not.toMatch(/\bhandle\(/);
    expect(source).toContain('https://github.com/honojs/hono/issues/4877');
    expect(source).toContain('app.fetch(req)');
  });

  for (const method of OTHER_METHODS) {
    it(`answers ${method} with the API's 405 problem, never reading the bundle`, async () => {
      const response = await viaRoute(method, '/api/v1/health');
      await expectProblem(response, 405, 'Method Not Allowed');
      expect(response.headers.get('allow')).toBe('GET, HEAD');
    });
  }

  it('serves the API under /api/v1 (PRD 8.5) and a 404 problem anywhere else under /api', async () => {
    const document = await viaRoute('GET', '/api/v1/openapi.json');
    expect(document.status).toBe(200);
    expect(document.headers.get('content-type')).toMatch(/^application\/json\b/);
    for (const path of ['/api', '/api/v2/health', '/api/health']) {
      await expectProblem(await viaRoute('GET', path), 404, 'Not Found');
    }
  });

  it('routes /api/docs (PRD 8.6) to the redirect to /api/v1/docs', async () => {
    const response = await viaRoute('GET', '/api/docs');
    expect(response.status).toBe(308);
    expect(response.headers.get('location')).toBe('/api/v1/docs');
    const docs = await viaRoute('GET', '/api/v1/docs');
    expect(docs.status).toBe(200);
    expect(docs.headers.get('content-type')).toMatch(/^text\/html\b/);
  });
});

describe('the mounted API over apps/web/data/', () => {
  for (const vercelEnv of ['', 'development', 'preview']) {
    it(`serves the pulled mock bundle when VERCEL_ENV is ${vercelEnv || 'unset'}`, async () => {
      serveMockBundle(vercelEnv);
      const health = await viaRoute('GET', '/api/v1/health');
      expect(health.status).toBe(200);
      const manifest = mockFile('manifest.json') as Record<string, unknown>;
      expect(await health.json()).toEqual({
        status: 'ok',
        bundle_version: manifest.bundle_version,
        schema_version: manifest.schema_version,
      });
      const served = await viaRoute('GET', '/api/v1/manifest');
      expect(served.status).toBe(200);
      expect(await served.json()).toEqual(manifest);
    });
  }
});

describe('a mock bundle with VERCEL_ENV=production (PRD 8.4, 12 end-to-end checks)', () => {
  for (const path of ['/api/v1/health', '/api/v1/manifest']) {
    it(`makes the mounted GET ${path} a 503 problem instead of the mock data`, async () => {
      serveMockBundle('production');
      const log = capturedLog();
      const response = await viaRoute('GET', path);
      const text = await response.clone().text();
      const problem = await expectProblem(response, 503, 'Service Unavailable');
      expect(problem.instance).toBe(path);
      expect(problem.detail).toMatch(/mock/);
      expect(problem.detail).toMatch(/production/);
      expect(text).not.toContain('mock-0');
      expect(text).not.toContain('bundle_version');
      expect(log).toHaveBeenCalledTimes(1);
      expect(log.mock.calls[0]?.[0]).toBeInstanceOf(MockBundleError);
    });

    it(`makes app.request(${path}) on the same instance a 503 problem`, async () => {
      serveMockBundle('production');
      capturedLog();
      await expectProblem(await app.request(path), 503, 'Service Unavailable');
    });

    it(`makes HEAD ${path} a 503 without a body`, async () => {
      serveMockBundle('production');
      capturedLog();
      const response = await viaRoute('HEAD', path);
      expect(response.status).toBe(503);
      expect(response.headers.get('content-type')).toBe('application/problem+json');
      expect(await response.text()).toBe('');
    });
  }

  it('leaves the bundle-independent document and reference pages served', async () => {
    serveMockBundle('production');
    const log = capturedLog();
    expect((await viaRoute('GET', '/api/v1/openapi.json')).status).toBe(200);
    expect((await viaRoute('GET', '/api/v1/docs')).status).toBe(200);
    expect(log).not.toHaveBeenCalled();
  });
});
