// Shared test helpers: temporary repository roots holding a mock bundle and release bundles.
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach } from 'vitest';

export const REPO_ROOT = fileURLToPath(new URL('../../..', import.meta.url));
export const WEB_DIR = fileURLToPath(new URL('..', import.meta.url));
export const MOCK_DIR = join(REPO_ROOT, 'fixtures', 'mock-bundle');

const made: string[] = [];

afterEach(() => {
  for (const dir of made.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

/** A new empty directory that is removed after the test. */
export function tempDir(): string {
  const dir = mkdtempSync(join(tmpdir(), 'terraluna-web-'));
  made.push(dir);
  return dir;
}

export function sha256(bytes: Uint8Array): string {
  return createHash('sha256').update(bytes).digest('hex');
}

export function toBytes(value: unknown): Buffer {
  return Buffer.from(`${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

export function gate(gateId: string, status = 'NOT_RUN'): Record<string, unknown> {
  return {
    consequence: null,
    evidence_path: null,
    gate_id: gateId,
    result: null,
    run_id: null,
    scope: null,
    status,
  };
}

export const ALL_GATES_NOT_RUN = ['G0', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6'].map((id) => gate(id));

/** A schema-valid manifest that lists the given validation.json bytes. */
export function manifestFor(
  validation: Uint8Array,
  fields: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    bundle_version: '2026-11-15T0900Z-3f9c2a1',
    created_utc: '2026-11-15T09:00:00Z',
    files: [{ bytes: validation.byteLength, path: 'validation.json', sha256: sha256(validation) }],
    git_sha: '3f9c2a1',
    mock: false,
    model: null,
    run_id: 'sha256:0123',
    schema_version: '1.0.0',
    shipping_mode: 'B',
    targets: [],
    ...fields,
  };
}

export interface BundleFiles {
  manifest?: Uint8Array;
  validation?: Uint8Array;
}

/** Writes manifest.json and validation.json (each only if given) into dir. */
export function writeBundle(dir: string, files: BundleFiles): void {
  mkdirSync(dir, { recursive: true });
  if (files.manifest !== undefined) writeFileSync(join(dir, 'manifest.json'), files.manifest);
  if (files.validation !== undefined) writeFileSync(join(dir, 'validation.json'), files.validation);
}

/** A valid release bundle with the given manifest fields, as file bytes. */
export function releaseFiles(fields: Record<string, unknown> = {}): Required<BundleFiles> {
  const validation = toBytes(ALL_GATES_NOT_RUN);
  return { manifest: toBytes(manifestFor(validation, fields)), validation };
}

/** A temporary repository root holding a copy of the committed mock bundle. */
export function repoWithMock(): string {
  const root = tempDir();
  writeBundle(join(root, 'fixtures', 'mock-bundle'), {
    manifest: readFileSync(join(MOCK_DIR, 'manifest.json')),
    validation: readFileSync(join(MOCK_DIR, 'validation.json')),
  });
  return root;
}

/** Adds release/<version>/ with the given files to a repository root. */
export function addRelease(root: string, version: string, files: BundleFiles): string {
  const dir = join(root, 'release', version);
  writeBundle(dir, files);
  return dir;
}
