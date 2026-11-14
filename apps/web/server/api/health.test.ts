// GET /api/v1/health (PRD 8.5): status, bundle_version and schema_version from the manifest the
// loader read, with Cache-Control: no-store. Its response schema is built from @terraluna/schema.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { BundleManifestSchema } from '@terraluna/schema';
import { describe, expect, it, vi } from 'vitest';

import { loadBundle, type PulledBundle, pullBundle } from '../../lib/bundle.ts';
import { MOCK_DIR, REPO_ROOT, tempDir } from '../../tests/helpers.ts';
import { type BundleReader, createApi } from './app.ts';
import { HealthSchema } from './health.ts';
import { PROBLEM_MEDIA_TYPE } from './problem.ts';

/** fixtures/mock-bundle/manifest.json as plain JSON, without the loader or any schema. */
const MOCK_MANIFEST = JSON.parse(readFileSync(join(MOCK_DIR, 'manifest.json'), 'utf8')) as Record<
  string,
  unknown
>;

const mockReader: BundleReader = () => loadBundle({ dataDir: MOCK_DIR, env: {} });

function quietApi(reader: BundleReader) {
  return createApi({ reader, log: () => undefined });
}

describe('GET /api/v1/health', () => {
  it('returns the mock manifest bundle_version and schema_version with status ok', async () => {
    const response = await quietApi(mockReader).request('/api/v1/health');
    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toBe('application/json');
    expect(await response.json()).toEqual({
      status: 'ok',
      bundle_version: MOCK_MANIFEST.bundle_version,
      schema_version: MOCK_MANIFEST.schema_version,
    });
    expect(MOCK_MANIFEST.bundle_version).toBe('mock-0');
  });

  it('is never cached', async () => {
    const response = await quietApi(mockReader).request('/api/v1/health');
    expect(response.headers.get('cache-control')).toBe('no-store');
  });

  it('serves what bundle:pull wrote, read back through the loader', async () => {
    const outDir = tempDir();
    pullBundle({ repoRoot: REPO_ROOT, outDir, env: {} });
    const api = quietApi(() => loadBundle({ dataDir: outDir, env: {} }));
    const body = (await (await api.request('/api/v1/health')).json()) as Record<string, unknown>;
    expect(body.bundle_version).toBe(MOCK_MANIFEST.bundle_version);
    expect(body.schema_version).toBe(MOCK_MANIFEST.schema_version);
  });

  it('reads the bundle on each request, so a changed bundle is never answered from memory', async () => {
    const reader = vi.fn(mockReader);
    const api = quietApi(reader);
    await api.request('/api/v1/health');
    await api.request('/api/v1/health');
    expect(reader).toHaveBeenCalledTimes(2);
  });

  it('answers HEAD with the same status and headers and no body', async () => {
    const response = await quietApi(mockReader).request('/api/v1/health', { method: 'HEAD' });
    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(response.headers.get('content-type')).toBe('application/json');
    expect(await response.text()).toBe('');
  });

  it('does not pass through a manifest value that does not match its schema', async () => {
    const bundle = mockReader();
    const corrupt = {
      ...bundle,
      manifest: { ...bundle.manifest, schema_version: 1 },
    } as unknown as PulledBundle;
    const response = await quietApi(() => corrupt).request('/api/v1/health');
    expect(response.status).toBe(500);
    expect(response.headers.get('content-type')).toBe(PROBLEM_MEDIA_TYPE);
    const body = (await response.json()) as Record<string, unknown>;
    expect(body).not.toHaveProperty('schema_version');
    expect(body).not.toHaveProperty('bundle_version');
  });
});

describe('HealthSchema', () => {
  it('takes bundle_version and schema_version from BundleManifestSchema, not a copy', () => {
    expect(HealthSchema.shape.bundle_version).toBe(BundleManifestSchema.shape.bundle_version);
    expect(HealthSchema.shape.schema_version).toBe(BundleManifestSchema.shape.schema_version);
  });

  it('has exactly status, bundle_version and schema_version, and refuses other keys', () => {
    expect(Object.keys(HealthSchema.shape).sort()).toEqual([
      'bundle_version',
      'schema_version',
      'status',
    ]);
    const health = { status: 'ok', bundle_version: 'mock-0', schema_version: '1.0.0' };
    expect(HealthSchema.safeParse(health).success).toBe(true);
    expect(HealthSchema.safeParse({ ...health, mock: true }).success).toBe(false);
    expect(HealthSchema.safeParse({ ...health, status: 'degraded' }).success).toBe(false);
  });
});
