// GET /api/v1/gates (PRD 8.5, T15-03): validation.json unchanged, validated with the generated
// Validation schema, cached per bundle with an ETag derived from bundle_version. Gate statuses are
// served as the bundle records them, so an untested gate is never reported as PASS (PRD 8.12).
import { ValidationSchema } from '@terraluna/schema';
import { describe, expect, it } from 'vitest';

import type { PulledBundle } from '../../lib/bundle.ts';
import { gate, manifestFor, tempDir, toBytes, writeBundle } from '../../tests/helpers.ts';
import { ValidationRecord } from './records.ts';
import {
  dirReader,
  expectProblem,
  jsonSchema,
  mockFile,
  mockReader,
  quietApi,
} from './test-support.ts';

const PATH = '/api/v1/gates';
const MOCK_ETAG = 'W/"mock-0"';
const PER_BUNDLE = 'public, max-age=300, immutable';
const STATUSES = ['NOT_RUN', 'PASS', 'FAIL', 'PARTIAL', 'NOT_APPLICABLE'];

interface GateStatus {
  gate_id: string;
  status: string;
}

/** gate_id to status, for comparing a response with a validation.json. */
function statuses(gates: unknown): Record<string, string> {
  return Object.fromEntries((gates as GateStatus[]).map((g) => [g.gate_id, g.status]));
}

/**
 * A temporary release bundle whose validation.json uses every status once. These statuses are test
 * input for the pass-through check, not gate results.
 */
function mixedStatusReader() {
  const gates = [
    gate('G0', 'PASS'),
    gate('G1', 'FAIL'),
    gate('G2', 'PARTIAL'),
    gate('G3', 'NOT_APPLICABLE'),
    gate('G4', 'NOT_RUN'),
    gate('G5', 'NOT_RUN'),
    gate('G6', 'NOT_RUN'),
  ];
  const validation = toBytes(gates);
  const dir = tempDir();
  writeBundle(dir, { manifest: toBytes(manifestFor(validation)), validation });
  return { reader: dirReader(dir), gates };
}

/** The mock bundle with one part replaced, as no loader would return it. */
function corruptReader(change: (bundle: PulledBundle) => object) {
  const corrupt = change(mockReader());
  return () => corrupt as unknown as PulledBundle;
}

describe('GET /api/v1/gates', () => {
  it('returns fixtures/mock-bundle/validation.json unchanged', async () => {
    const { api } = quietApi();
    const response = await api.request(PATH);
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toBe('application/json');
    expect(await response.json()).toEqual(mockFile('validation.json'));
  });

  it('reports only known statuses, each equal to the mock bundle, so none becomes PASS', async () => {
    const { api } = quietApi();
    const body: unknown = await (await api.request(PATH)).json();
    for (const status of Object.values(statuses(body))) {
      expect(STATUSES).toContain(status);
    }
    expect(statuses(body)).toEqual(statuses(mockFile('validation.json')));
    expect(Object.values(statuses(body))).not.toContain('PASS');
  });

  it('returns a body that is valid against the generated Validation schema', async () => {
    const { api } = quietApi();
    const body: unknown = await (await api.request(PATH)).json();
    expect(ValidationSchema.safeParse(body).success).toBe(true);
  });

  it('passes every status through as the bundle records it', async () => {
    const { reader, gates } = mixedStatusReader();
    const { api } = quietApi(reader);
    const body: unknown = await (await api.request(PATH)).json();
    expect(body).toEqual(gates);
    expect(ValidationSchema.safeParse(body).success).toBe(true);
  });

  it('refuses a corrupted manifest (wrong schema_version type) with a 500 problem', async () => {
    const reader = corruptReader((b) => ({ ...b, manifest: { ...b.manifest, schema_version: 1 } }));
    const { api } = quietApi(reader);
    const response = await api.request(PATH);
    const text = await response.clone().text();
    const problem = await expectProblem(response, 500, 'Internal Server Error');
    expect(problem.detail).toContain('manifest.json');
    expect(text).not.toContain('NOT_RUN');
  });

  it('refuses a gate status outside the five with a 500 problem, serving no gate', async () => {
    const reader = corruptReader((b) => ({
      ...b,
      validation: b.validation.map((g) => (g.gate_id === 'G1' ? { ...g, status: 'PASSED' } : g)),
    }));
    const { api } = quietApi(reader);
    const response = await api.request(PATH);
    const text = await response.clone().text();
    const problem = await expectProblem(response, 500, 'Internal Server Error');
    expect(problem.detail).toContain('validation.json');
    expect(problem.detail).toContain('invalid');
    expect(text).not.toContain('PASSED');
    expect(text).not.toContain('G1');
  });
});

describe('GET /api/v1/gates caching (per bundle)', () => {
  it('is public with a 300 s max-age, immutable, and an ETag from bundle_version', async () => {
    const { api } = quietApi();
    const response = await api.request(PATH);
    expect(response.headers.get('cache-control')).toBe(PER_BUNDLE);
    expect(response.headers.get('etag')).toBe(MOCK_ETAG);
  });

  it('gives a release bundle its own ETag', async () => {
    const { api } = quietApi(mixedStatusReader().reader);
    const response = await api.request(PATH, { headers: { 'If-None-Match': MOCK_ETAG } });
    expect(response.status).toBe(200);
    expect(response.headers.get('etag')).toBe('W/"2026-11-15T0900Z-3f9c2a1"');
  });

  it('answers a matching If-None-Match with 304, no body and the cache headers', async () => {
    const { api } = quietApi();
    const response = await api.request(PATH, { headers: { 'If-None-Match': MOCK_ETAG } });
    expect(response.status).toBe(304);
    expect(await response.text()).toBe('');
    expect(response.headers.get('etag')).toBe(MOCK_ETAG);
    expect(response.headers.get('cache-control')).toBe(PER_BUNDLE);
  });

  it('answers another bundle ETag with the full 200', async () => {
    const { api } = quietApi();
    const response = await api.request(PATH, { headers: { 'If-None-Match': 'W/"mock-1"' } });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(mockFile('validation.json'));
  });

  it('never answers 304 for gates that fail their schema', async () => {
    const reader = corruptReader((b) => ({ ...b, validation: [{ gate_id: 'G0' }] }));
    const { api } = quietApi(reader);
    const response = await api.request(PATH, { headers: { 'If-None-Match': MOCK_ETAG } });
    await expectProblem(response, 500, 'Internal Server Error');
  });
});

describe('GET /api/v1/gates in the OpenAPI document', () => {
  const doc = () =>
    quietApi().api.getOpenAPI31Document({ openapi: '3.1.0', info: { title: 'test', version: '0' } });

  it('references the Validation component for 200 and documents 304 and 500', () => {
    const operation = doc().paths?.[PATH]?.get;
    expect(operation?.responses?.['200']).toMatchObject({
      content: { 'application/json': { schema: { $ref: '#/components/schemas/Validation' } } },
    });
    expect(operation?.responses).toHaveProperty('304');
    expect(operation?.responses?.['500']).toMatchObject({
      content: { 'application/problem+json': { schema: { $ref: '#/components/schemas/Problem' } } },
    });
  });

  it('builds the component from the generated schema, not a copy', () => {
    expect(ValidationRecord._zod.def).toBe(ValidationSchema._zod.def);
  });

  it('has exactly the GateRecord fields of the JSON Schema generated from the Python record', () => {
    const component = doc().components?.schemas?.Validation as Record<string, unknown>;
    expect(component.type).toBe('array');
    const items = component.items as Record<string, unknown>;
    const generated = jsonSchema('gate-record');
    expect(Object.keys(items.properties as object).sort()).toEqual(
      Object.keys(generated.properties as object).sort(),
    );
    expect([...(items.required as string[])].sort()).toEqual(
      [...(generated.required as string[])].sort(),
    );
    expect(items.additionalProperties).toBe(false);
  });
});
