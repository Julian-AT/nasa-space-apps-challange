// The release bundle as the web reads it (PRD 8.4, 8.5, 8.9). `bundle:pull` copies manifest.json
// and validation.json from the selected bundle into apps/web/data/, and `next build` reads them
// from there. Both run the same checks, so a build never uses a bundle that bundle:pull refuses.
//
// Selection: BUNDLE_VERSION unset or empty selects the mock bundle in fixtures/mock-bundle/;
// otherwise release/<BUNDLE_VERSION>/. A requested bundle that is missing or invalid is an error;
// it never falls back to the mock. A mock bundle is refused when VERCEL_ENV=production.
import { createHash } from 'node:crypto';
import { lstatSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import {
  type BundleManifest,
  BundleManifestSchema,
  type Validation,
  ValidationSchema,
} from '@terraluna/schema';

import { BundleError } from './errors.ts';
import { decodeJson } from './json.ts';

export { BundleError } from './errors.ts';

export const MOCK_BUNDLE_DIR = 'fixtures/mock-bundle';
export const RELEASE_DIR = 'release';
export const MANIFEST = 'manifest.json';
export const VALIDATION = 'validation.json';
const PULL_HINT = 'run `pnpm --filter @terraluna/web bundle:pull` (make web-build does)';

/** The environment variables that select and guard the bundle (PRD 8.9). */
export type BundleEnv = Readonly<Record<string, string | undefined>>;

export type BundleSelection =
  | { readonly kind: 'mock' }
  | { readonly kind: 'release'; readonly version: string };

export interface Bundle {
  readonly manifest: BundleManifest;
  readonly validation: Validation;
}

interface RawBundle {
  readonly manifest: Uint8Array;
  readonly validation: Uint8Array;
}

const VERCEL_ENVS: ReadonlySet<string> = new Set(['production', 'preview', 'development']);

/** True when VERCEL_ENV is production. An unknown value is refused, so a typo cannot pass a mock. */
function isProduction(env: BundleEnv): boolean {
  const value = env.VERCEL_ENV;
  if (value === undefined || value === '') return false;
  if (!VERCEL_ENVS.has(value)) {
    throw new BundleError(
      `VERCEL_ENV must be production, preview, development or unset, not ${JSON.stringify(value)}`,
    );
  }
  return value === 'production';
}

export function selectBundle(env: BundleEnv): BundleSelection {
  isProduction(env);
  const version = env.BUNDLE_VERSION;
  if (version === undefined || version === '') return { kind: 'mock' };
  if (!BundleManifestSchema.shape.bundle_version.safeParse(version).success) {
    throw new BundleError(
      `BUNDLE_VERSION ${JSON.stringify(version)} is not a bundle_version (letters, digits, ".", "_" and "-", not starting with ".")`,
    );
  }
  return { kind: 'release', version };
}

/** The selected bundle's directory, relative to the repository root. */
export function bundleSourcePath(selection: BundleSelection): string {
  return selection.kind === 'mock' ? MOCK_BUNDLE_DIR : `${RELEASE_DIR}/${selection.version}`;
}

/** Throws when a mock bundle would be built for production (PRD 8.4, 12 end-to-end checks). */
export function assertDeployable(manifest: BundleManifest, env: BundleEnv): void {
  if (manifest.mock && isProduction(env)) {
    throw new BundleError(
      `refusing mock bundle ${manifest.bundle_version}: its manifest has mock=true and ` +
        'VERCEL_ENV=production. A production build needs BUNDLE_VERSION set to a release bundle.',
    );
  }
}

function sha256(bytes: Uint8Array): string {
  return createHash('sha256').update(bytes).digest('hex');
}

function issues(error: { issues: readonly { path: readonly PropertyKey[]; message: string }[] }) {
  return error.issues
    .map((issue) => `${issue.path.map(String).join('.') || '(root)'}: ${issue.message}`)
    .join('; ');
}

/** Reads a regular file; symbolic links are never followed (as in the Python validator). */
function readRegularFile(path: string, label: string, missing: string): Buffer {
  const stats = lstatSync(path, { throwIfNoEntry: false });
  if (stats === undefined) throw new BundleError(`${label} is missing: ${missing}`);
  if (!stats.isFile()) throw new BundleError(`${label} is not a regular file`);
  return readFileSync(path);
}

function readBundle(dir: string, where: string, missing: string): RawBundle {
  return {
    manifest: readRegularFile(join(dir, MANIFEST), `${where}/${MANIFEST}`, missing),
    validation: readRegularFile(join(dir, VALIDATION), `${where}/${VALIDATION}`, missing),
  };
}

function parseManifest(raw: RawBundle, label: string): BundleManifest {
  const result = BundleManifestSchema.safeParse(decodeJson(raw.manifest, label));
  if (!result.success) {
    throw new BundleError(`${label} does not match the BundleManifest schema: ${issues(result.error)}`);
  }
  return result.data;
}

/** validation.json must be listed in the manifest with its exact byte count and SHA-256. */
function checkListing(manifest: BundleManifest, raw: RawBundle, where: string): void {
  const listed = manifest.files.filter((file) => file.path === VALIDATION);
  const [entry] = listed;
  if (entry === undefined) {
    throw new BundleError(`${where}/${MANIFEST}: ${VALIDATION} is not listed in files`);
  }
  if (listed.length > 1) {
    throw new BundleError(`${where}/${MANIFEST}: ${VALIDATION} is listed more than once`);
  }
  const label = `${where}/${VALIDATION}`;
  if (raw.validation.byteLength !== entry.bytes) {
    throw new BundleError(
      `${label} has ${String(raw.validation.byteLength)} bytes, but the manifest lists ${String(entry.bytes)} bytes`,
    );
  }
  if (sha256(raw.validation) !== entry.sha256) {
    throw new BundleError(`${label} does not match the sha256 the manifest lists`);
  }
}

function parseValidation(raw: RawBundle, label: string): Validation {
  const result = ValidationSchema.safeParse(decodeJson(raw.validation, label));
  if (!result.success) {
    throw new BundleError(`${label} does not match the Validation schema: ${issues(result.error)}`);
  }
  const seen = new Set<string>();
  for (const record of result.data) {
    if (seen.has(record.gate_id)) {
      throw new BundleError(`${label}: gate ${record.gate_id} is listed twice`);
    }
    seen.add(record.gate_id);
  }
  return result.data;
}

/** The manifest must be the bundle that the environment selects. */
function checkSelection(manifest: BundleManifest, selection: BundleSelection, label: string): void {
  if (selection.kind === 'mock') {
    if (!manifest.mock) {
      throw new BundleError(
        `${label} has mock=false, but BUNDLE_VERSION is unset or empty, which selects the mock bundle (${MOCK_BUNDLE_DIR})`,
      );
    }
    return;
  }
  if (manifest.bundle_version !== selection.version) {
    throw new BundleError(
      `${label} is bundle_version ${manifest.bundle_version}, but BUNDLE_VERSION is ${selection.version}`,
    );
  }
  if (manifest.mock) {
    throw new BundleError(
      `${label} has mock=true: mock records live only in ${MOCK_BUNDLE_DIR}, never in a release`,
    );
  }
}

function parseBundle(raw: RawBundle, selection: BundleSelection, where: string): Bundle {
  const label = `${where}/${MANIFEST}`;
  const manifest = parseManifest(raw, label);
  checkListing(manifest, raw, where);
  const validation = parseValidation(raw, `${where}/${VALIDATION}`);
  checkSelection(manifest, selection, label);
  return { manifest, validation };
}

function writeRegularFile(path: string, bytes: Uint8Array): void {
  const stats = lstatSync(path, { throwIfNoEntry: false });
  if (stats !== undefined && !stats.isFile()) {
    throw new BundleError(`${path} exists and is not a regular file`);
  }
  writeFileSync(path, bytes);
}

export interface PullOptions {
  /** The repository root, which holds fixtures/mock-bundle/ and release/. */
  readonly repoRoot: string;
  /** Where the two files are copied: apps/web/data/. */
  readonly outDir: string;
  readonly env: BundleEnv;
}

/**
 * Validates the selected bundle's manifest.json and validation.json and copies them, byte for
 * byte, into outDir. Nothing is written unless every check passes.
 */
export function pullBundle({ repoRoot, outDir, env }: PullOptions): Bundle {
  const selection = selectBundle(env);
  const where = bundleSourcePath(selection);
  const missing =
    selection.kind === 'mock'
      ? 'the mock bundle is generated by `make mock-bundle`'
      : `BUNDLE_VERSION=${selection.version} requests this bundle, and bundle:pull never falls back to the mock`;
  const raw = readBundle(join(repoRoot, where), where, missing);
  const bundle = parseBundle(raw, selection, where);
  assertDeployable(bundle.manifest, env);
  mkdirSync(outDir, { recursive: true });
  writeRegularFile(join(outDir, VALIDATION), raw.validation);
  writeRegularFile(join(outDir, MANIFEST), raw.manifest);
  return bundle;
}

export interface LoadOptions {
  /** The directory bundle:pull wrote; apps/web/data/ by default. */
  readonly dataDir?: string;
  readonly env?: BundleEnv;
}

/**
 * Reads the pulled bundle for rendering. It repeats bundle:pull's checks against the current
 * environment, so `next build` fails on a mock bundle for production, on a pulled bundle that
 * BUNDLE_VERSION does not select, and on a pulled file changed after the pull.
 */
export function loadBundle({ dataDir, env }: LoadOptions = {}): Bundle {
  const dir = dataDir ?? join(process.cwd(), 'data');
  const environment = env ?? process.env;
  const selection = selectBundle(environment);
  const bundle = parseBundle(readBundle(dir, dir, PULL_HINT), selection, dir);
  assertDeployable(bundle.manifest, environment);
  return bundle;
}
