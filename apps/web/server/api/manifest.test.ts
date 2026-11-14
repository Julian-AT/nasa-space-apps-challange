// GET /api/v1/manifest (PRD 8.5, T15-03): manifest.json unchanged, validated with the generated
// BundleManifest schema, cached per deploy with an ETag derived from bundle_version.
import { BundleManifestSchema } from '@terraluna/schema';
import { describe, expect, it, vi } from 'vitest';

import type { PulledBundle } from '../../lib/bundle.ts';
import { releaseFiles, tempDir, writeBundle } from '../../tests/helpers.ts';
import { BundleManifestRecord } from './records.ts';
import {
  expectProblem,
  jsonSchema,
  mockFile,
  mockReader,
  quietApi,
  RELEASE_VERSION,
  releaseReader,
} from './test-support.ts';

const PATH = '/api/v1/manifest';
const MOCK_ETAG = 'W/"mock-0"';
const PER_DEPLOY = 'public, max-age=60, immutable';

/** A temporary release bundle (not the mock), read through the loader. */
function releaseBundle() {
  const dir = tempDir();
  const files = releaseFiles();
  writeBundle(dir, files);
  return {
    reader: releaseReader(dir),
    manifest: JSON.parse(new TextDecoder().decode(files.manifest)) as object,
  };
}

/** The mock bundle with schema_version replaced by a number, as no loader would return it. */
function corruptReader() {
  const bundle = mockReader();
  const corrupt = { ...bundle, manifest: { ...bundle.manifest, schema_version: 1 } };
  return () => corrupt as unknown as PulledBundle;
}

describe('GET /api/v1/manifest', () => {
  it('returns fixtures/mock-bundle/manifest.json unchanged, mock=true included', async () => {
    const { api } = quietApi();
    const response = await api.request(PATH);
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toBe('application/json');
    const body = (await response.json()) as Record<string, unknown>;
    expect(body).toEqual(mockFile('manifest.json'));
    expect(body.mock).toBe(true);
  });

  it('returns a body that is valid against the generated BundleManifest schema', async () => {
    const { api } = quietApi();
    const body: unknown = await (await api.request(PATH)).json();
    expect(BundleManifestSchema.safeParse(body).success).toBe(true);
  });

  it('returns a release manifest unchanged too, nulls kept as null', async () => {
    const { reader, manifest } = releaseBundle();
    const { api } = quietApi(reader);
    const response = await api.request(PATH);
    expect(response.status).toBe(200);
    const body = (await response.json()) as Record<string, unknown>;
    expect(body).toEqual(manifest);
    expect(body.mock).toBe(false);
    expect(body.model).toBeNull();
    expect(BundleManifestSchema.safeParse(body).success).toBe(true);
  });

  it('reads the bundle on each request, so a changed bundle is never answered from memory', async () => {
    const reader = vi.fn(mockReader);
    const { api } = quietApi(reader);
    await api.request(PATH);
    await api.request(PATH);
    expect(reader).toHaveBeenCalledTimes(2);
  });

  it('refuses a corrupted reader fixture with a 500 problem instead of passing it through', async () => {
    const { api, log } = quietApi(corruptReader());
    const response = await api.request(PATH);
    const problem = await expectProblem(response, 500, 'Internal Server Error');
    expect(problem.detail).toContain('manifest.json');
    expect(problem.detail).toContain('invalid');
    expect(problem).not.toHaveProperty('schema_version');
    expect(problem).not.toHaveProperty('bundle_version');
    expect(log).toHaveBeenCalledOnce();
  });
});

describe('GET /api/v1/manifest caching (per deploy)', () => {
  it('is public with a 60 s max-age, immutable, and an ETag from bundle_version', async () => {
    const { api } = quietApi();
    const response = await api.request(PATH);
    expect(response.headers.get('cache-control')).toBe(PER_DEPLOY);
    expect(response.headers.get('etag')).toBe(MOCK_ETAG);
  });

  it('gives a release bundle its own ETag', async () => {
    const { api } = quietApi(releaseBundle().reader);
    const response = await api.request(PATH);
    expect(response.headers.get('etag')).toBe(`W/"${RELEASE_VERSION}"`);
  });

  for (const ifNoneMatch of [MOCK_ETAG, '"mock-0"', '"other", W/"mock-0"', '*']) {
    it(`answers If-None-Match: ${ifNoneMatch} with 304, no body and the cache headers`, async () => {
      const { api } = quietApi();
      const response = await api.request(PATH, { headers: { 'If-None-Match': ifNoneMatch } });
      expect(response.status).toBe(304);
      expect(await response.text()).toBe('');
      expect(response.headers.get('etag')).toBe(MOCK_ETAG);
      expect(response.headers.get('cache-control')).toBe(PER_DEPLOY);
    });
  }

  for (const ifNoneMatch of ['W/"mock-1"', '"mock-0-x"', `W/"${RELEASE_VERSION}"`]) {
    it(`answers If-None-Match: ${ifNoneMatch} with the full 200`, async () => {
      const { api } = quietApi();
      const response = await api.request(PATH, { headers: { 'If-None-Match': ifNoneMatch } });
      expect(response.status).toBe(200);
      expect(await response.json()).toEqual(mockFile('manifest.json'));
    });
  }

  it('answers a release bundle with 200 to a client holding the mock ETag', async () => {
    const { api } = quietApi(releaseBundle().reader);
    const response = await api.request(PATH, { headers: { 'If-None-Match': MOCK_ETAG } });
    expect(response.status).toBe(200);
  });

  it('answers HEAD with the 200 headers and no body, and conditional HEAD with 304', async () => {
    const { api } = quietApi();
    const head = await api.request(PATH, { method: 'HEAD' });
    expect(head.status).toBe(200);
    expect(head.headers.get('cache-control')).toBe(PER_DEPLOY);
    expect(head.headers.get('etag')).toBe(MOCK_ETAG);
    expect(await head.text()).toBe('');
    const conditional = await api.request(PATH, {
      method: 'HEAD',
      headers: { 'If-None-Match': MOCK_ETAG },
    });
    expect(conditional.status).toBe(304);
  });

  it('never answers 304 for a manifest that fails its schema', async () => {
    const { api } = quietApi(corruptReader());
    const response = await api.request(PATH, { headers: { 'If-None-Match': '*' } });
    await expectProblem(response, 500, 'Internal Server Error');
  });
});

describe('GET /api/v1/manifest in the OpenAPI document', () => {
  const doc = () =>
    quietApi().api.getOpenAPI31Document({
      openapi: '3.1.0',
      info: { title: 'test', version: '0' },
    });

  it('references the BundleManifest component for 200 and documents 304 and 500', () => {
    const operation = doc().paths?.[PATH]?.get;
    expect(operation?.responses?.['200']).toMatchObject({
      content: { 'application/json': { schema: { $ref: '#/components/schemas/BundleManifest' } } },
    });
    expect(operation?.responses).toHaveProperty('304');
    expect(operation?.responses?.['500']).toMatchObject({
      content: { 'application/problem+json': { schema: { $ref: '#/components/schemas/Problem' } } },
    });
  });

  it('builds the component from the generated schema, not a copy', () => {
    expect(BundleManifestRecord._zod.def).toBe(BundleManifestSchema._zod.def);
  });

  it('has exactly the fields of the JSON Schema generated from the Python record', () => {
    const component = doc().components?.schemas?.BundleManifest as Record<string, unknown>;
    const generated = jsonSchema('bundle-manifest');
    expect(Object.keys(component.properties as object).sort()).toEqual(
      Object.keys(generated.properties as object).sort(),
    );
    expect([...(component.required as string[])].sort()).toEqual(
      [...(generated.required as string[])].sort(),
    );
    expect(component.additionalProperties).toBe(false);
  });
});
