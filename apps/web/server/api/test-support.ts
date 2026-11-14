// Shared helpers for the API's route tests (never imported by the app). Each test chooses the
// bundle the API sees through the injected reader, and calls app.request(): no Next, no network.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect, vi } from 'vitest';

import { loadBundle } from '../../lib/bundle.ts';
import {
  ALL_GATES_NOT_RUN,
  type FileSet,
  listing,
  manifestFor,
  MOCK_DIR,
  REPO_ROOT,
  tempDir,
  toBytes,
  writeFiles,
} from '../../tests/helpers.ts';
import { type BundleReader, createApi } from './app.ts';
import { PROBLEM_MEDIA_TYPE, ProblemSchema } from './problem.ts';

/** The committed mock bundle, read with the T15-01 loader exactly as the API reads apps/web/data/. */
export const mockReader: BundleReader = () => loadBundle({ dataDir: MOCK_DIR, env: {} });

/** The bundle_version of the release fixtures that tests/helpers.ts writes (manifestFor). */
export const RELEASE_VERSION = '2026-11-15T0900Z-3f9c2a1';

/**
 * A pulled release bundle in dataDir, read with the T15-01 loader as the API reads apps/web/data/.
 * The loader only reads a release bundle that BUNDLE_VERSION selects.
 */
export function releaseReader(dataDir: string, version = RELEASE_VERSION): BundleReader {
  return () => loadBundle({ dataDir, env: { BUNDLE_VERSION: version } });
}

/** Marks test bytes, so a test can see whether any response carries them. */
export const TEST_BYTES_MARKER = 'test-only-bundle-file-bytes';

/**
 * Bytes standing in for a bundle file that has no schema (a comparison or window file). They are
 * test input, not a record and not an observation: the numbers only let a test see whether a
 * response ever passes such a file through. The loader keeps them as checksum-only bytes.
 */
export function testBytes(what: string): Uint8Array {
  return toBytes({
    note: `test bytes standing in for ${what}, not a record`,
    marker: TEST_BYTES_MARKER,
    distances_by_family: { terrain: 0.125, radar: 0.25, model: 0.375 },
    rank: 1,
    fingerprint: [0.5, 0.625],
  });
}

export interface ReleaseBundleOptions {
  /** manifest.targets; none by default. */
  readonly targets?: readonly string[];
  /** manifest.model; null by default. */
  readonly model?: Readonly<Record<string, unknown>> | null;
  /** The GateRecords of validation.json; every gate NOT_RUN by default. */
  readonly gates?: readonly unknown[];
  /** Further bundle files by bundle path, written and listed in the manifest. */
  readonly files?: FileSet;
}

/** A temporary release bundle (RELEASE_VERSION), read through the loader as the API reads it. */
export function releaseBundleReader({
  targets = [],
  model = null,
  gates = ALL_GATES_NOT_RUN,
  files = {},
}: ReleaseBundleOptions = {}): BundleReader {
  const validation = toBytes(gates);
  const listed: FileSet = { ...files, 'validation.json': validation };
  const manifest = manifestFor(validation, {
    targets: [...targets],
    model,
    files: listing(listed),
  });
  const dir = tempDir();
  writeFiles(dir, { ...listed, 'manifest.json': toBytes(manifest) });
  return releaseReader(dir);
}

/** An app that never logs to the test output; `log` records what the API would log. */
export function quietApi(reader: BundleReader = mockReader) {
  const log = vi.fn<(error: unknown) => void>();
  return { api: createApi({ reader, log }), log };
}

/** A file of the committed mock bundle as plain JSON, without the loader or any schema. */
export function mockFile(name: string): unknown {
  return JSON.parse(readFileSync(join(MOCK_DIR, name), 'utf8'));
}

/** A JSON Schema generated from the Python records (packages/schema/json/<name>.schema.json). */
export function jsonSchema(name: string): Record<string, unknown> {
  const path = join(REPO_ROOT, 'packages', 'schema', 'json', `${name}.schema.json`);
  return JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>;
}

const PROBLEM_KEYS = ['detail', 'instance', 'status', 'title', 'type'];

/** Checks the problem+json contract and returns the problem. */
export async function expectProblem(response: Response, status: number, title: string) {
  expect(response.status).toBe(status);
  expect(response.headers.get('content-type')).toBe(PROBLEM_MEDIA_TYPE);
  expect(response.headers.get('cache-control')).toBe('no-store');
  expect(response.headers.get('etag')).toBeNull();
  const body = (await response.json()) as Record<string, unknown>;
  expect(Object.keys(body).sort()).toEqual(PROBLEM_KEYS);
  const problem = ProblemSchema.parse(body);
  expect(problem).toMatchObject({ type: 'about:blank', title, status });
  return problem;
}
