// GET /api/v1/targets and GET /api/v1/targets/{id} (PRD 8.5, T15-04). The list serves the target
// ids manifest.json lists, with its bundle_version and shipping_mode, and nothing else: body and
// footprint wait for a TargetRecord schema in @terraluna/schema, so no target field is derived from
// an id and targets.json is never served. The detail answers 404 for an id the manifest does not
// list and 501 for one it lists, since no target record can be served yet.
import { BundleManifestSchema } from '@terraluna/schema';
import { describe, expect, it, vi } from 'vitest';

import { comparisonPath, isPulledPath, type PulledBundle, targetsFile } from '../../lib/bundle.ts';
import {
  ALL_GATES_NOT_RUN,
  listing,
  manifestFor,
  tempDir,
  toBytes,
  writeFiles,
} from '../../tests/helpers.ts';
import { VALIDATION_STATUS } from './problem.ts';
import {
  TARGET_ID_MAX_LENGTH,
  TARGET_ID_PATTERN,
  TargetIdSchema,
  TargetListSchema,
} from './targets.ts';
import {
  expectProblem,
  mockFile,
  mockReader,
  quietApi,
  RELEASE_VERSION,
  releaseReader,
} from './test-support.ts';

const LIST = '/api/v1/targets';
const MOCK_ETAG = 'W/"mock-0"';
const PER_BUNDLE = 'public, max-age=300, immutable';

/** Two target ids for these tests only. They are test input, not targets of any bundle. */
const TEST_IDS = ['test-target-alpha', 'test-target-beta'];

/** Marks the bytes of a test targets.json, so a test can see whether any response carries them. */
const TARGETS_JSON_MARKER = 'test-only-targets-json-bytes';

interface BundleOptions {
  readonly targets?: readonly string[];
  readonly shippingMode?: string | null;
  /** Lists and writes a targets.json with test bytes, which the loader keeps as checksum-only. */
  readonly withTargetsJson?: boolean;
}

/** A temporary release bundle with the given targets, read through the loader. */
function releaseBundle({
  targets = TEST_IDS,
  shippingMode = 'B',
  withTargetsJson = false,
}: BundleOptions = {}) {
  const validation = toBytes(ALL_GATES_NOT_RUN);
  const files: Record<string, Uint8Array> = { 'validation.json': validation };
  if (withTargetsJson) {
    files['targets.json'] = toBytes({
      note: 'test bytes, not a target record',
      marker: TARGETS_JSON_MARKER,
      targets: targets.map((id) => ({ id, body: 'moon', footprint: [0, 0] })),
    });
  }
  const manifest = manifestFor(validation, {
    targets: [...targets],
    shipping_mode: shippingMode,
    files: listing(files),
  });
  const dir = tempDir();
  writeFiles(dir, { ...files, 'manifest.json': toBytes(manifest) });
  return releaseReader(dir);
}

/** The mock bundle with one part replaced, as no loader would return it. */
function corruptReader(change: (bundle: PulledBundle) => object) {
  const corrupt = change(mockReader());
  return () => corrupt as unknown as PulledBundle;
}

/** A bundle that lists the test ids, with its manifest made invalid (a target id that is a number). */
function corruptTargetsReader() {
  const bundle = releaseBundle()();
  const corrupt = { ...bundle, manifest: { ...bundle.manifest, targets: [...TEST_IDS, 7] } };
  return () => corrupt as unknown as PulledBundle;
}

function detailPath(id: string): string {
  return `${LIST}/${id}`;
}

describe('GET /api/v1/targets', () => {
  it('returns an empty list for the mock bundle, with its bundle_version and shipping_mode', async () => {
    const { api, log } = quietApi();
    const response = await api.request(LIST);
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toBe('application/json');
    const manifest = mockFile('manifest.json') as Record<string, unknown>;
    expect(manifest.targets).toEqual([]);
    expect(await response.json()).toEqual({
      bundle_version: manifest.bundle_version,
      shipping_mode: manifest.shipping_mode,
      items: [],
    });
    expect(log).not.toHaveBeenCalled();
  });

  it('keeps the mock shipping_mode null, never an empty string or zero', async () => {
    const { api } = quietApi();
    const text = await (await api.request(LIST)).text();
    const body = JSON.parse(text) as Record<string, unknown>;
    expect(body.shipping_mode).toBeNull();
    expect(text).toContain('"shipping_mode":null');
  });

  it('returns exactly the ids the manifest lists, in manifest order', async () => {
    const { api } = quietApi(releaseBundle());
    const response = await api.request(LIST);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      bundle_version: RELEASE_VERSION,
      shipping_mode: 'B',
      items: TEST_IDS,
    });
  });

  it('keeps the manifest order rather than sorting the ids', async () => {
    const reversed = [...TEST_IDS].reverse();
    const { api } = quietApi(releaseBundle({ targets: reversed }));
    const body = (await (await api.request(LIST)).json()) as Record<string, unknown>;
    expect(body.items).toEqual(reversed);
  });

  it('serves the id strings only, with no field synthesized from an id', async () => {
    const { api } = quietApi(releaseBundle());
    const body = (await (await api.request(LIST)).json()) as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(['bundle_version', 'items', 'shipping_mode']);
    for (const item of body.items as unknown[]) expect(typeof item).toBe('string');
  });

  it('keeps a null shipping_mode of a release bundle null', async () => {
    const { api } = quietApi(releaseBundle({ shippingMode: null }));
    const body = (await (await api.request(LIST)).json()) as Record<string, unknown>;
    expect(body.shipping_mode).toBeNull();
    expect(body.items).toEqual(TEST_IDS);
  });

  it('returns a body that is valid against the TargetList schema', async () => {
    for (const reader of [mockReader, releaseBundle()]) {
      const { api } = quietApi(reader);
      const body: unknown = await (await api.request(LIST)).json();
      expect(TargetListSchema.safeParse(body).success).toBe(true);
    }
  });

  it('never serves targets.json, even when the bundle holds it', async () => {
    const reader = releaseBundle({ withTargetsJson: true });
    expect(targetsFile(reader()).status).toBe('present');
    const { api } = quietApi(reader);
    const response = await api.request(LIST);
    expect(response.status).toBe(200);
    const text = await response.text();
    expect(text).not.toContain(TARGETS_JSON_MARKER);
    expect(text).not.toContain('footprint');
    expect(JSON.parse(text)).toEqual({
      bundle_version: RELEASE_VERSION,
      shipping_mode: 'B',
      items: TEST_IDS,
    });
  });

  it('is not an error when targets.json is absent and the manifest lists no targets', async () => {
    const reader = releaseBundle({ targets: [] });
    expect(targetsFile(reader()).status).toBe('not_present');
    const { api, log } = quietApi(reader);
    const response = await api.request(LIST);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ items: [] });
    expect(log).not.toHaveBeenCalled();
  });

  it('refuses a manifest whose targets fail the schema with a 500 problem, listing no id', async () => {
    const { api, log } = quietApi(corruptTargetsReader());
    const response = await api.request(LIST);
    const text = await response.clone().text();
    const problem = await expectProblem(response, 500, 'Internal Server Error');
    expect(problem.detail).toContain('manifest.json');
    expect(problem.detail).toContain('invalid');
    for (const id of TEST_IDS) expect(text).not.toContain(id);
    expect(log).toHaveBeenCalledOnce();
  });

  it('reads the bundle on each request, so a changed bundle is never answered from memory', async () => {
    const reader = vi.fn(mockReader);
    const { api } = quietApi(reader);
    await api.request(LIST);
    await api.request(LIST);
    expect(reader).toHaveBeenCalledTimes(2);
  });
});

describe('GET /api/v1/targets caching (per bundle)', () => {
  it('is public with a 300 s max-age, immutable, and an ETag from bundle_version', async () => {
    const { api } = quietApi();
    const response = await api.request(LIST);
    expect(response.headers.get('cache-control')).toBe(PER_BUNDLE);
    expect(response.headers.get('etag')).toBe(MOCK_ETAG);
  });

  it('gives a release bundle its own ETag', async () => {
    const { api } = quietApi(releaseBundle());
    const response = await api.request(LIST, { headers: { 'If-None-Match': MOCK_ETAG } });
    expect(response.status).toBe(200);
    expect(response.headers.get('etag')).toBe(`W/"${RELEASE_VERSION}"`);
  });

  it('answers a matching If-None-Match with 304, no body and the cache headers', async () => {
    const { api } = quietApi();
    const response = await api.request(LIST, { headers: { 'If-None-Match': MOCK_ETAG } });
    expect(response.status).toBe(304);
    expect(await response.text()).toBe('');
    expect(response.headers.get('etag')).toBe(MOCK_ETAG);
    expect(response.headers.get('cache-control')).toBe(PER_BUNDLE);
  });

  it('answers HEAD with the 200 headers and no body', async () => {
    const { api } = quietApi();
    const head = await api.request(LIST, { method: 'HEAD' });
    expect(head.status).toBe(200);
    expect(head.headers.get('cache-control')).toBe(PER_BUNDLE);
    expect(head.headers.get('etag')).toBe(MOCK_ETAG);
    expect(await head.text()).toBe('');
  });

  it('never answers 304 for a manifest that fails its schema', async () => {
    const reader = corruptReader((b) => ({ ...b, manifest: { ...b.manifest, targets: [''] } }));
    const { api } = quietApi(reader);
    const response = await api.request(LIST, { headers: { 'If-None-Match': '*' } });
    await expectProblem(response, 500, 'Internal Server Error');
  });
});

describe('GET /api/v1/targets/{id}', () => {
  it('is a 404 problem for any id on the mock bundle, naming the id and the bundle', async () => {
    const { api, log } = quietApi();
    const response = await api.request(detailPath('test-target-alpha'));
    const problem = await expectProblem(response, 404, 'Not Found');
    expect(problem.detail).toContain('test-target-alpha');
    expect(problem.detail).toContain('is not in bundle mock-0');
    expect(problem.instance).toBe(detailPath('test-target-alpha'));
    expect(log).not.toHaveBeenCalled();
  });

  it('is a 404 problem for an id the manifest does not list', async () => {
    const { api } = quietApi(releaseBundle());
    const problem = await expectProblem(
      await api.request(detailPath('test-target-gamma')),
      404,
      'Not Found',
    );
    expect(problem.detail).toContain('test-target-gamma');
    expect(problem.detail).toContain(`is not in bundle ${RELEASE_VERSION}`);
  });

  for (const id of ['test-target', 'test-target-alpha-', 'TEST-TARGET-ALPHA', 'test-target-alph']) {
    it(`matches ids exactly: ${id} is a 404`, async () => {
      const { api } = quietApi(releaseBundle());
      await expectProblem(await api.request(detailPath(id)), 404, 'Not Found');
    });
  }

  for (const id of TEST_IDS) {
    it(`is a 501 problem for the listed id ${id}: target records are not served yet`, async () => {
      const { api, log } = quietApi(releaseBundle());
      const problem = await expectProblem(
        await api.request(detailPath(id)),
        501,
        'Not Implemented',
      );
      expect(problem.detail).toContain(id);
      expect(problem.detail).toContain(`is in bundle ${RELEASE_VERSION}`);
      expect(problem.detail).toContain('TargetRecord');
      expect(log).not.toHaveBeenCalled();
    });
  }

  it('serves nothing from targets.json for a listed id', async () => {
    const { api } = quietApi(releaseBundle({ withTargetsJson: true }));
    const response = await api.request(detailPath('test-target-alpha'));
    const text = await response.clone().text();
    await expectProblem(response, 501, 'Not Implemented');
    expect(text).not.toContain(TARGETS_JSON_MARKER);
    expect(text).not.toContain('footprint');
  });

  it('answers HEAD with the problem status and no body', async () => {
    const { api } = quietApi(releaseBundle());
    const head = await api.request(detailPath('test-target-alpha'), { method: 'HEAD' });
    expect(head.status).toBe(501);
    expect(await head.text()).toBe('');
  });

  it('refuses a manifest that fails its schema with a 500 problem, not 404 or 501', async () => {
    const { api, log } = quietApi(corruptTargetsReader());
    const response = await api.request(detailPath('test-target-alpha'));
    const problem = await expectProblem(response, 500, 'Internal Server Error');
    expect(problem.detail).toContain('manifest.json');
    expect(log).toHaveBeenCalledOnce();
  });

  const MALFORMED: readonly [string, string][] = [
    ['a leading dot', '.test-target-alpha'],
    ['a space', 'test%20target'],
    ['an encoded slash', 'test-target%2Falpha'],
    ['a non-ASCII letter', 'test-target-%C3%A9'],
    ['a colon', 'test:target'],
    ['too many characters', 'a'.repeat(TARGET_ID_MAX_LENGTH + 1)],
  ];

  for (const [what, id] of MALFORMED) {
    it(`is a ${String(VALIDATION_STATUS)} problem for an id with ${what}, without reading the bundle`, async () => {
      const reader = vi.fn(mockReader);
      const { api } = quietApi(reader);
      const response = await api.request(detailPath(id));
      const problem = await expectProblem(response, VALIDATION_STATUS, 'Bad Request');
      expect(problem.detail).toContain('path parameter id');
      expect(reader).not.toHaveBeenCalled();
    });
  }

  it(`accepts an id of exactly ${String(TARGET_ID_MAX_LENGTH)} characters`, async () => {
    const { api } = quietApi();
    const id = 'a'.repeat(TARGET_ID_MAX_LENGTH);
    await expectProblem(await api.request(detailPath(id)), 404, 'Not Found');
  });
});

describe('the target id parameter', () => {
  it('has a documented pattern and length limit', () => {
    expect(TARGET_ID_PATTERN).toBe('^[A-Za-z0-9_-][A-Za-z0-9._-]*$');
    expect(TARGET_ID_MAX_LENGTH).toBe(128);
  });

  const ACCEPTED = ['test-target-alpha', 'a', '0', '_x', 'a.b', 'A-Z_0.9', 'a'.repeat(128)];
  const REFUSED = ['', '.a', 'a/b', 'a b', 'a:b', 'é', 'a\n', '..', 'a'.repeat(129)];

  it('accepts only ids that can name the comparison file of the target in the bundle', () => {
    for (const id of ACCEPTED) {
      expect(TargetIdSchema.safeParse(id).success, id).toBe(true);
      expect(isPulledPath(comparisonPath(id)), id).toBe(true);
    }
    for (const id of REFUSED) {
      expect(TargetIdSchema.safeParse(id).success, JSON.stringify(id)).toBe(false);
    }
  });
});

describe('GET /api/v1/targets in the OpenAPI document', () => {
  const doc = () =>
    quietApi().api.getOpenAPI31Document({
      openapi: '3.1.0',
      info: { title: 'test', version: '0' },
    });

  it('references the TargetList component for 200 and documents 304 and 500', () => {
    const operation = doc().paths?.[LIST]?.get;
    expect(operation?.responses?.['200']).toMatchObject({
      content: { 'application/json': { schema: { $ref: '#/components/schemas/TargetList' } } },
    });
    expect(operation?.responses).toHaveProperty('304');
    expect(operation?.responses?.['500']).toMatchObject({
      content: { 'application/problem+json': { schema: { $ref: '#/components/schemas/Problem' } } },
    });
  });

  it('says that body and footprint wait for a TargetRecord schema', () => {
    const description = doc().paths?.[LIST]?.get?.description ?? '';
    expect(description).toContain('body');
    expect(description).toContain('footprint');
    expect(description).toContain('TargetRecord');
  });

  it('builds TargetList from the BundleManifest schema, not a copy', () => {
    const { shape } = TargetListSchema;
    expect(Object.keys(shape).sort()).toEqual(['bundle_version', 'items', 'shipping_mode']);
    expect(shape.bundle_version).toBe(BundleManifestSchema.shape.bundle_version);
    expect(shape.shipping_mode).toBe(BundleManifestSchema.shape.shipping_mode);
    expect(shape.items).toBe(BundleManifestSchema.shape.targets);
  });

  it('documents the TargetList fields exactly as the BundleManifest component', () => {
    const schemas = doc().components?.schemas ?? {};
    const list = schemas.TargetList as Record<string, Record<string, unknown>>;
    const manifest = schemas.BundleManifest as Record<string, Record<string, unknown>>;
    expect(Object.keys(list.properties ?? {}).sort()).toEqual([
      'bundle_version',
      'items',
      'shipping_mode',
    ]);
    expect(list.properties?.bundle_version).toEqual(manifest.properties?.bundle_version);
    expect(list.properties?.shipping_mode).toEqual(manifest.properties?.shipping_mode);
    expect(list.properties?.items).toEqual(manifest.properties?.targets);
    expect([...(list.required as unknown as string[])].sort()).toEqual([
      'bundle_version',
      'items',
      'shipping_mode',
    ]);
    expect(list.additionalProperties).toBe(false);
  });

  it('defines no target record component', () => {
    const names = Object.keys(doc().components?.schemas ?? {});
    expect(names.filter((name) => /target/i.test(name))).toEqual(['TargetList']);
  });
});

describe('GET /api/v1/targets/{id} in the OpenAPI document', () => {
  const operation = () =>
    quietApi().api.getOpenAPI31Document({
      openapi: '3.1.0',
      info: { title: 'test', version: '0' },
    }).paths?.['/api/v1/targets/{id}']?.get;

  it('documents the id path parameter with its pattern and length limit', () => {
    const parameters = operation()?.parameters ?? [];
    expect(parameters).toHaveLength(1);
    expect(parameters[0]).toMatchObject({
      name: 'id',
      in: 'path',
      required: true,
      schema: { type: 'string', pattern: TARGET_ID_PATTERN, maxLength: TARGET_ID_MAX_LENGTH },
    });
  });

  it('documents 400, 404, 501 and 500 as problems and no 200', () => {
    const responses = operation()?.responses ?? {};
    expect(Object.keys(responses).sort()).toEqual(['400', '404', '500', '501']);
    for (const status of ['400', '404', '500', '501']) {
      expect(responses[status]).toMatchObject({
        content: {
          'application/problem+json': { schema: { $ref: '#/components/schemas/Problem' } },
        },
      });
    }
  });

  it('says that target records wait for a TargetRecord schema', () => {
    expect(operation()?.description ?? '').toContain('TargetRecord');
  });
});
