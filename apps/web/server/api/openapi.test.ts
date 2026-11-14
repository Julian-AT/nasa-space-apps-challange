// GET /api/v1/openapi.json (PRD 8.5, 8.2, D24, T15-06): the OpenAPI 3.1 document generated from the
// same zod-openapi route definitions that validate the requests, so the documented contract is the
// served one. It lists every routed path, only as get operations, and is checked against the
// official OpenAPI 3.1 JSON Schema with an offline validator. It is fixed per deploy and never reads
// the bundle, so it is the same for every bundle and names no target and no number in its info.
import { Validator } from '@seriousme/openapi-schema-validator';
import { describe, expect, it, vi } from 'vitest';

import { API_BASE_PATH, type BundleReader, createApi } from './app.ts';
import { PER_DEPLOY_MAX_AGE } from './cache.ts';
import { DOCS_ALIAS_PATH } from './docs.ts';
import { API_VERSION } from './openapi.ts';
import { PROBLEM_MEDIA_TYPE } from './problem.ts';
import { expectProblem, quietApi, releaseBundleReader } from './test-support.ts';

const PATH = '/api/v1/openapi.json';

/** Test ids for a release bundle that lists targets; never under fixtures/ or in the app source. */
const TEST_TARGETS = ['test-only-openapi-target-a', 'test-only-openapi-target-b'];

/** Every path the API routes under /api/v1, in the order PRD 8.5 lists them. */
const ROUTED_PATHS = [
  '/api/v1/health',
  '/api/v1/manifest',
  '/api/v1/targets',
  '/api/v1/targets/{id}',
  '/api/v1/targets/{id}/matches',
  '/api/v1/windows/{id}',
  '/api/v1/compare',
  '/api/v1/model',
  '/api/v1/gates',
  '/api/v1/openapi.json',
  '/api/v1/docs',
];

const PER_DEPLOY = `public, max-age=${String(PER_DEPLOY_MAX_AGE)}, immutable`;

type Json = Record<string, unknown>;

async function documentOf(reader?: BundleReader): Promise<Json> {
  const { api } = quietApi(reader);
  const response = await api.request(PATH);
  expect(response.status).toBe(200);
  return (await response.json()) as Json;
}

/** The (method, path) pairs the app routes, in OpenAPI path syntax, without middleware. */
function routedPairs(api: ReturnType<typeof createApi>): string[] {
  const pairs = api.routes
    .filter((route) => route.method !== 'ALL')
    .map((route) => `${route.method} ${route.path.replaceAll(/:([^/]+)/g, '{$1}')}`);
  return [...new Set(pairs)].sort();
}

function operations(document: Json): [string, string, Json][] {
  const paths = document.paths as Record<string, Record<string, Json>>;
  return Object.entries(paths).flatMap(([path, item]) =>
    Object.entries(item).map(([method, operation]): [string, string, Json] => [
      path,
      method,
      operation,
    ]),
  );
}

describe('GET /api/v1/openapi.json', () => {
  it('returns an OpenAPI 3.1 document as JSON', async () => {
    const { api } = quietApi();
    const response = await api.request(PATH);
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toMatch(/^application\/json\b/);
    const document = (await response.json()) as Json;
    expect(document.openapi).toMatch(/^3\.1\./);
  });

  it('is valid against the official OpenAPI 3.1 schema (offline validator)', async () => {
    const validator = new Validator({ allErrors: true });
    const result = await validator.validate(await documentOf());
    expect(result.errors).toBeUndefined();
    expect(result.valid).toBe(true);
    expect(validator.version).toBe('3.1');
  });

  it('is refused by the validator when it is broken, so the check is not vacuous', async () => {
    const document = await documentOf();
    const broken = { ...document, info: { title: 'missing version' } };
    const result = await new Validator().validate(broken);
    expect(result.valid).toBe(false);
  });

  it('lists exactly the routed paths under /api/v1', async () => {
    const { api } = quietApi();
    const document = (await (await api.request(PATH)).json()) as Json;
    const documented = Object.keys(document.paths as object).sort();
    expect(documented).toEqual([...ROUTED_PATHS].sort());
    const routed = routedPairs(api).filter((pair) => pair.startsWith(`GET ${API_BASE_PATH}/`));
    expect(routed).toEqual(documented.map((path) => `GET ${path}`));
  });

  it('has only get operations, and the app routes only GET (HEAD is GET without a body)', async () => {
    const { api } = quietApi();
    const document = (await (await api.request(PATH)).json()) as Json;
    for (const [path, method] of operations(document)) expect(method, path).toBe('get');
    expect(new Set(routedPairs(api).map((pair) => pair.split(' ')[0]))).toEqual(new Set(['GET']));
  });

  it('routes nothing outside /api/v1 but the /api/docs redirect, which it does not document', async () => {
    const { api } = quietApi();
    const outside = routedPairs(api).filter((pair) => !pair.startsWith(`GET ${API_BASE_PATH}/`));
    expect(outside).toEqual([`GET ${DOCS_ALIAS_PATH}`]);
    const document = (await (await api.request(PATH)).json()) as Json;
    expect(Object.keys(document.paths as object)).not.toContain(DOCS_ALIAS_PATH);
  });

  it('gives every operation a summary, a description and a documented 500 problem', async () => {
    for (const [path, , operation] of operations(await documentOf())) {
      expect(operation.summary, path).toEqual(expect.any(String));
      expect(operation.description, path).toEqual(expect.any(String));
      const responses = operation.responses as Record<string, Json>;
      expect(responses['500'], path).toMatchObject({
        content: { [PROBLEM_MEDIA_TYPE]: { schema: { $ref: '#/components/schemas/Problem' } } },
      });
    }
  });

  it('describes every error response as problem+json and gives every body a schema', async () => {
    for (const [path, , operation] of operations(await documentOf())) {
      for (const [status, response] of Object.entries(
        operation.responses as Record<string, Json>,
      )) {
        const where = `${path} ${status}`;
        expect(response.description, where).toEqual(expect.any(String));
        const content = (response.content ?? {}) as Record<string, Json>;
        if (Number(status) >= 400) {
          expect(Object.keys(content), where).toEqual([PROBLEM_MEDIA_TYPE]);
          expect(content[PROBLEM_MEDIA_TYPE]?.schema, where).toEqual({
            $ref: '#/components/schemas/Problem',
          });
        }
        if (status === '304') expect(content, where).toEqual({});
        for (const media of Object.values(content)) expect(media.schema, where).toBeDefined();
      }
    }
  });

  it('documents a 400 problem on every operation that takes parameters, each with a schema', async () => {
    for (const [path, , operation] of operations(await documentOf())) {
      const parameters = (operation.parameters ?? []) as Json[];
      for (const parameter of parameters) expect(parameter.schema, path).toBeDefined();
      const responses = operation.responses as Record<string, Json>;
      if (parameters.length > 0) expect(responses, path).toHaveProperty('400');
      else expect(responses, path).not.toHaveProperty('400');
    }
  });

  it('references the record and problem components and defines each one', async () => {
    const document = await documentOf();
    const components = (document.components as { schemas: Json }).schemas;
    expect(Object.keys(components)).toEqual(
      expect.arrayContaining(['BundleManifest', 'Health', 'Problem', 'TargetList', 'Validation']),
    );
    const refs = [
      ...JSON.stringify(document).matchAll(/"\$ref":"#\/components\/schemas\/([^"]+)"/g),
    ];
    expect(refs.length).toBeGreaterThan(0);
    for (const [, name] of refs) expect(components, name).toHaveProperty(name ?? '');
  });

  it('documents itself and the reference page with their content and per-deploy caching', async () => {
    const paths = (await documentOf()).paths as Record<string, { get: Json }>;
    const self = paths['/api/v1/openapi.json']?.get.responses as Record<string, Json>;
    expect(Object.keys(self).sort()).toEqual(['200', '304', '500']);
    expect(self['200']?.content).toHaveProperty(['application/json', 'schema']);
    expect(self['200']?.headers).toHaveProperty(['Cache-Control', 'description']);
    expect(self['200']?.headers).toHaveProperty(['ETag', 'description']);
    const docs = paths['/api/v1/docs']?.get.responses as Record<string, Json>;
    expect(Object.keys(docs).sort()).toEqual(['200', '304', '500']);
    expect(docs['200']?.content).toHaveProperty(['text/html', 'schema']);
    expect(docs['200']?.headers).toHaveProperty(['Cache-Control', 'description']);
  });
});

describe('the document info', () => {
  it('carries the API version, not the bundle_version', async () => {
    const info = (await documentOf()).info as Json;
    expect(info.version).toBe(API_VERSION);
    expect(API_VERSION).toMatch(/^\d+\.\d+\.\d+$/);
    expect(info.version).not.toBe('mock-0');
  });

  it('has the major version of the base path, so the contract and the URL agree', () => {
    expect(API_BASE_PATH).toBe(`/api/v${API_VERSION.split('.')[0] ?? ''}`);
  });

  it('says the API is read-only and that mock bundles are placeholders', async () => {
    const info = (await documentOf()).info as Json;
    expect(info.title).toBe('Terraluna API');
    const text = String(info.description);
    expect(text).toMatch(/read-only/i);
    expect(text).toMatch(/GET and HEAD/);
    expect(text).toMatch(/mock bundle/i);
    expect(text).toMatch(/placeholder/i);
  });

  it('has no numbers in its text', async () => {
    const info = (await documentOf()).info as Json;
    for (const key of ['title', 'summary', 'description']) {
      expect(String(info[key] ?? ''), key).not.toMatch(/\d/);
    }
  });
});

describe('the document is fixed per deploy, not per bundle', () => {
  it('is the same for the mock bundle and for a release bundle that lists targets', async () => {
    const mock = await documentOf();
    const release = await documentOf(releaseBundleReader({ targets: TEST_TARGETS }));
    expect(release).toEqual(mock);
    const text = JSON.stringify(release);
    for (const target of TEST_TARGETS) expect(text).not.toContain(target);
  });

  it('never reads the bundle, so a broken bundle does not change it', async () => {
    const reader = vi.fn<BundleReader>(() => {
      throw new Error('this reader must not be called');
    });
    const { api, log } = quietApi(reader);
    const response = await api.request(PATH);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(await documentOf());
    expect(reader).not.toHaveBeenCalled();
    expect(log).not.toHaveBeenCalled();
  });

  it('is cached per deploy with a strong ETag of its bytes', async () => {
    const { api } = quietApi();
    const first = await api.request(PATH);
    const second = await api.request(PATH);
    expect(first.headers.get('cache-control')).toBe(PER_DEPLOY);
    const etag = first.headers.get('etag');
    expect(etag).toMatch(/^"[A-Za-z0-9_-]{43}"$/);
    expect(second.headers.get('etag')).toBe(etag);
    expect(await second.text()).toBe(await first.text());
  });

  it('gives the same ETag in another app instance of the same deploy', async () => {
    const one = await quietApi().api.request(PATH);
    const other = await quietApi(releaseBundleReader()).api.request(PATH);
    expect(other.headers.get('etag')).toBe(one.headers.get('etag'));
  });

  it('answers 304 to a matching If-None-Match, keeping the cache headers', async () => {
    const { api } = quietApi();
    const etag = (await api.request(PATH)).headers.get('etag') ?? '';
    const response = await api.request(PATH, { headers: { 'If-None-Match': etag } });
    expect(response.status).toBe(304);
    expect(response.headers.get('etag')).toBe(etag);
    expect(response.headers.get('cache-control')).toBe(PER_DEPLOY);
    expect(await response.text()).toBe('');
  });

  it('answers 200 to an If-None-Match that names another document', async () => {
    const { api } = quietApi();
    const response = await api.request(PATH, { headers: { 'If-None-Match': '"other"' } });
    expect(response.status).toBe(200);
  });

  it('answers HEAD with the same headers and no body', async () => {
    const { api } = quietApi();
    const get = await api.request(PATH);
    const head = await api.request(PATH, { method: 'HEAD' });
    expect(head.status).toBe(200);
    expect(head.headers.get('etag')).toBe(get.headers.get('etag'));
    expect(head.headers.get('cache-control')).toBe(PER_DEPLOY);
    expect(await head.text()).toBe('');
  });

  it('refuses other methods with 405, as every route does', async () => {
    const { api } = quietApi();
    const response = await api.request(PATH, { method: 'POST' });
    await expectProblem(response, 405, 'Method Not Allowed');
    expect(response.headers.get('allow')).toBe('GET, HEAD');
  });
});
