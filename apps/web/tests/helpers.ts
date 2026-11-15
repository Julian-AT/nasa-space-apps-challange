// Shared test helpers: temporary repository roots holding a mock bundle and release bundles.
import { createHash } from 'node:crypto';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative, sep } from 'node:path';
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

/**
 * A synthetic GateRecord. NOT_RUN leaves every nullable field null; any other status carries the
 * PRD-01 provenance fields (command, config_version, evidence_path, scope, input_hashes) with
 * labelled synthetic values, and PASS a run_id, as the GateRecord schema requires.
 */
export function gate(gateId: string, status = 'NOT_RUN'): Record<string, unknown> {
  const run = status !== 'NOT_RUN';
  const zero = '0'.repeat(64);
  return {
    command: run ? 'synthetic test command' : null,
    config_version: run ? zero : null,
    consequence: null,
    evidence_path: run ? 'outputs/gates/synthetic-evidence.json' : null,
    gate_id: gateId,
    input_hashes: run ? [{ path: 'config/run.yaml', sha256: zero }] : [],
    result: null,
    run_id: status === 'PASS' ? `sha256:${zero}` : null,
    scope: run ? 'synthetic test scope' : null,
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

/** Bundle files by bundle path (POSIX, relative to the bundle root), as bytes. */
export type FileSet = Readonly<Record<string, Uint8Array>>;

/** The manifest `files` entries listing each of the given files, in path order. */
export function listing(files: FileSet): { bytes: number; path: string; sha256: string }[] {
  return Object.keys(files)
    .sort()
    .map((path) => {
      const bytes = files[path] ?? new Uint8Array();
      return { bytes: bytes.byteLength, path, sha256: sha256(bytes) };
    });
}

/** Writes each file under dir, creating the subdirectories its path names. */
export function writeFiles(dir: string, files: FileSet): void {
  for (const [path, bytes] of Object.entries(files)) {
    const target = join(dir, ...path.split('/'));
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, bytes);
  }
}

/** Every regular file under dir as a sorted POSIX path relative to dir; [] if dir is missing. */
export function filesUnder(dir: string): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => relative(dir, join(entry.parentPath, entry.name)).split(sep).join('/'))
    .sort();
}
