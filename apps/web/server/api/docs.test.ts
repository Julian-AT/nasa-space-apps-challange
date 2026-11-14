// GET /api/v1/docs (PRD 8.5, 8.6, D24, T15-06): the Scalar API reference, rendered by
// @scalar/hono-api-reference and pointing at the relative openapi.json URL, cached per deploy.
// GET /api/docs (the PRD 8.6 path) is a permanent redirect to it.
//
// Scalar's Hono integration renders a page that loads the reference script from a CDN, so the page
// loads that one script from one other host, pinned to an exact version, and turns off every
// option that would fetch from another host (fonts, telemetry, agent, MCP, developer tools).
import { describe, expect, it, vi } from 'vitest';

import type { BundleReader } from './app.ts';
import { PER_DEPLOY_MAX_AGE } from './cache.ts';
import { DOCS_ALIAS_PATH, SCALAR_SCRIPT, SCALAR_SCRIPT_HOST } from './docs.ts';
import { expectProblem, quietApi, releaseBundleReader } from './test-support.ts';

const PATH = '/api/v1/docs';
const PER_DEPLOY = `public, max-age=${String(PER_DEPLOY_MAX_AGE)}, immutable`;

async function page(reader?: BundleReader): Promise<string> {
  const response = await quietApi(reader).api.request(PATH);
  expect(response.status).toBe(200);
  return response.text();
}

/** The configuration object the page passes to Scalar.createApiReference. */
function scalarConfig(html: string): Record<string, unknown> {
  const match = /Scalar\.createApiReference\('#app', (\{[\s\S]*?\n\s*\})\)/.exec(html);
  expect(match, 'createApiReference call').not.toBeNull();
  return JSON.parse(match?.[1] ?? '{}') as Record<string, unknown>;
}

/** Every URL the page names: src and href attributes, url() in styles and absolute URLs. */
function namedUrls(html: string): string[] {
  const attributes = [...html.matchAll(/\b(?:src|href|action)\s*=\s*["']([^"']*)["']/gi)];
  const css = [...html.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/gi)];
  const absolute = [...html.matchAll(/(?:https?:)?\/\/[A-Za-z0-9.-]+\.[A-Za-z]{2,}[^\s"'`<>)]*/g)];
  return [...attributes, ...css].map((m) => m[1] ?? '').concat(absolute.map((m) => m[0]));
}

describe('GET /api/v1/docs', () => {
  it('returns the Scalar reference as HTML', async () => {
    const response = await quietApi().api.request(PATH);
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toMatch(/^text\/html; charset=utf-8$/i);
    const html = await response.text();
    expect(html).toMatch(/^<!doctype html>/i);
    expect(html).toContain('<title>Terraluna API reference</title>');
    expect(html).toContain('<div id="app"></div>');
    expect(html).toContain("Scalar.createApiReference('#app', ");
  });

  it('points Scalar at the relative openapi.json URL, which resolves to the served document', async () => {
    const { api } = quietApi();
    const config = scalarConfig(await (await api.request(PATH)).text());
    expect(config.url).toBe('openapi.json');
    expect(config).not.toHaveProperty('content');
    const resolved = new URL(String(config.url), `http://localhost${PATH}`);
    expect(resolved.pathname).toBe('/api/v1/openapi.json');
    const document = await api.request(resolved.pathname);
    expect(document.status).toBe(200);
    expect(((await document.json()) as { openapi: string }).openapi).toMatch(/^3\.1\./);
  });

  it('loads one script, from the pinned CDN URL the package requires, and no other resource', async () => {
    const html = await page();
    const scripts = [...html.matchAll(/<script\b[^>]*>/gi)].map((m) => m[0]);
    expect(scripts.filter((tag) => /\bsrc=/.test(tag))).toEqual([`<script src="${SCALAR_SCRIPT}">`]);
    expect(html).not.toMatch(/<script[^>]*type=["']module["']/i);
    expect(html).not.toMatch(/<link\b/i);
    expect(html).not.toMatch(/@import/i);
    expect(html).not.toMatch(/<(?:img|iframe|object|embed)\b/i);
  });

  it('names no host but the CDN host of the script', async () => {
    const urls = namedUrls(await page());
    expect(urls).toContain(SCALAR_SCRIPT);
    for (const url of urls) {
      const parsed = new URL(url, 'http://localhost/api/v1/docs');
      if (parsed.host !== 'localhost') expect(parsed.host, url).toBe(SCALAR_SCRIPT_HOST);
    }
  });

  it('pins the script to an exact version over https', () => {
    const url = new URL(SCALAR_SCRIPT);
    expect(url.protocol).toBe('https:');
    expect(url.host).toBe(SCALAR_SCRIPT_HOST);
    expect(url.pathname).toMatch(/^\/npm\/@scalar\/api-reference@\d+\.\d+\.\d+\/dist\/browser\//);
  });

  it('turns off the options that would fetch from other hosts', async () => {
    const config = scalarConfig(await page());
    expect(config).toMatchObject({
      withDefaultFonts: false,
      telemetry: false,
      agent: { disabled: true },
      mcp: { disabled: true },
      showDeveloperTools: 'never',
    });
    for (const key of ['proxyUrl', 'proxy', 'externalUrls', 'pluginUrls', 'baseServerURL']) {
      expect(config, key).not.toHaveProperty(key);
    }
  });

  it('never reads the bundle, so the page is the same for every bundle', async () => {
    const reader = vi.fn<BundleReader>(() => {
      throw new Error('this reader must not be called');
    });
    const { api, log } = quietApi(reader);
    const response = await api.request(PATH);
    expect(response.status).toBe(200);
    expect(await response.text()).toBe(await page(releaseBundleReader()));
    expect(reader).not.toHaveBeenCalled();
    expect(log).not.toHaveBeenCalled();
  });

  it('is cached per deploy with a strong ETag of its bytes, and answers 304 to it', async () => {
    const { api } = quietApi();
    const first = await api.request(PATH);
    expect(first.headers.get('cache-control')).toBe(PER_DEPLOY);
    const etag = first.headers.get('etag') ?? '';
    expect(etag).toMatch(/^"[A-Za-z0-9_-]{43}"$/);
    expect((await api.request(PATH)).headers.get('etag')).toBe(etag);
    const cached = await api.request(PATH, { headers: { 'If-None-Match': etag } });
    expect(cached.status).toBe(304);
    expect(cached.headers.get('cache-control')).toBe(PER_DEPLOY);
    expect(await cached.text()).toBe('');
  });

  it('has an ETag that differs from the document one, since the bytes differ', async () => {
    const { api } = quietApi();
    const docs = (await api.request(PATH)).headers.get('etag');
    const document = (await api.request('/api/v1/openapi.json')).headers.get('etag');
    expect(docs).not.toBe(document);
  });

  it('answers HEAD with the same headers and no body', async () => {
    const { api } = quietApi();
    const head = await api.request(PATH, { method: 'HEAD' });
    expect(head.status).toBe(200);
    expect(head.headers.get('content-type')).toMatch(/^text\/html/);
    expect(head.headers.get('cache-control')).toBe(PER_DEPLOY);
    expect(head.headers.get('etag')).toBe((await api.request(PATH)).headers.get('etag'));
    expect(await head.text()).toBe('');
  });

  it('is not served with a trailing slash, where the relative URL would resolve elsewhere', async () => {
    await expectProblem(await quietApi().api.request(`${PATH}/`), 404, 'Not Found');
  });
});

describe('GET /api/docs', () => {
  it('is the PRD 8.6 path', () => {
    expect(DOCS_ALIAS_PATH).toBe('/api/docs');
  });

  it('redirects permanently to /api/v1/docs', async () => {
    const { api } = quietApi();
    const response = await api.request(DOCS_ALIAS_PATH);
    expect(response.status).toBe(308);
    expect(response.headers.get('location')).toBe('/api/v1/docs');
    expect(response.headers.get('cache-control')).toBe(PER_DEPLOY);
    expect(await response.text()).toBe('');
  });

  it('lands where the relative openapi.json URL resolves to the served document', async () => {
    const { api } = quietApi();
    const location = (await api.request(DOCS_ALIAS_PATH)).headers.get('location') ?? '';
    const landed = new URL(location, 'http://localhost/api/docs');
    expect((await api.request(landed.pathname)).status).toBe(200);
    expect(new URL('openapi.json', landed).pathname).toBe('/api/v1/openapi.json');
  });

  it('redirects HEAD the same way, without a body', async () => {
    const response = await quietApi().api.request(DOCS_ALIAS_PATH, { method: 'HEAD' });
    expect(response.status).toBe(308);
    expect(response.headers.get('location')).toBe('/api/v1/docs');
    expect(await response.text()).toBe('');
  });

  it('never reads the bundle', async () => {
    const reader = vi.fn<BundleReader>(() => {
      throw new Error('this reader must not be called');
    });
    const { api } = quietApi(reader);
    expect((await api.request(DOCS_ALIAS_PATH)).status).toBe(308);
    expect(reader).not.toHaveBeenCalled();
  });

  it('refuses other methods with 405, as every route does', async () => {
    const response = await quietApi().api.request(DOCS_ALIAS_PATH, { method: 'DELETE' });
    await expectProblem(response, 405, 'Method Not Allowed');
  });

  it('redirects only that path: a trailing slash or a sub path stays a 404 problem', async () => {
    const { api } = quietApi();
    await expectProblem(await api.request('/api/docs/'), 404, 'Not Found');
    await expectProblem(await api.request('/api/docs/openapi.json'), 404, 'Not Found');
  });
});
