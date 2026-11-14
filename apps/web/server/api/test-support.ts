// Shared helpers for the API's route tests (never imported by the app). Each test chooses the
// bundle the API sees through the injected reader, and calls app.request(): no Next, no network.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect, vi } from 'vitest';

import { loadBundle } from '../../lib/bundle.ts';
import { MOCK_DIR, REPO_ROOT } from '../../tests/helpers.ts';
import { type BundleReader, createApi } from './app.ts';
import { PROBLEM_MEDIA_TYPE, ProblemSchema } from './problem.ts';

/** The committed mock bundle, read with the T15-01 loader exactly as the API reads apps/web/data/. */
export const mockReader: BundleReader = () => loadBundle({ dataDir: MOCK_DIR, env: {} });

/** A bundle directory read with the T15-01 loader, as the API reads apps/web/data/. */
export function dirReader(dataDir: string): BundleReader {
  return () => loadBundle({ dataDir, env: {} });
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
