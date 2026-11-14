// The release bundle as the web reads it (PRD 8.4, 8.5, 8.9). `bundle:pull` copies the small JSON
// files the server reads from the selected bundle into apps/web/data/: manifest.json,
// validation.json and, when the manifest lists them, sources.json, targets.json,
// comparisons/<target_id>.json and windows/<body>.json (the top-N subset). `next build` and the
// API read apps/web/data/ through loadBundle, which repeats every check, so nothing reads a
// bundle that bundle:pull refuses.
//
// Every copied file must be listed in the manifest, and its byte count and SHA-256 must match the
// entry. A listed file the bundle lacks is an error; a file the manifest does not list is never
// copied or read. Large artifacts (tiles/, windows/*.parquet, model/, report/, reference/) and
// files the server never reads (cards/, figures/) stay in the bundle and are not read here, so
// they may live in object storage only. Validating the whole bundle (every checksum, unlisted
// files, budgets) is `export.py validate`'s job.
//
// manifest.json and validation.json are validated with their @terraluna/schema schemas. sources,
// targets, comparisons and windows have no schema there yet (schema gaps, left to a human
// decision), so they are checked against the manifest only and kept as bytes, never parsed.
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

import { BundleError, BundleFileError, MockBundleError } from './errors.ts';
import { decodeJson } from './json.ts';

export { BundleError, BundleFileError, type BundleFileProblem, MockBundleError } from './errors.ts';

export const MOCK_BUNDLE_DIR = 'fixtures/mock-bundle';
export const RELEASE_DIR = 'release';
export const MANIFEST = 'manifest.json';
export const VALIDATION = 'validation.json';
/** ProductRecords (PRD 8.4, 8.12). */
export const SOURCES = 'sources.json';
/** Target windows (PRD 8.4). */
export const TARGETS = 'targets.json';
const PULL_HINT = 'run `pnpm --filter @terraluna/web bundle:pull` (make web-build does)';

/** comparisons/<target_id>.json: the target's top 500 ComparisonRecords (PRD 8.4). */
export function comparisonPath(targetId: string): string {
  return `comparisons/${targetId}.json`;
}

/** windows/<body>.json: the JSON top-N subset of windows/<body>.parquet (PRD 8.4). */
export function windowsTopNPath(body: string): string {
  return `windows/${body}.json`;
}

// One segment of a bundle path, as BundlePath in @terraluna/schema allows it.
const SEGMENT = '[A-Za-z0-9_-][A-Za-z0-9._-]*';
const PULLED_PATH = new RegExp(
  `^(?:validation\\.json|sources\\.json|targets\\.json|(?:comparisons|windows)/${SEGMENT}\\.json)$`,
);

/** True for a listed bundle path that bundle:pull copies. manifest.json itself is never listed. */
export function isPulledPath(path: string): boolean {
  return PULLED_PATH.test(path);
}

/** The environment variables that select and guard the bundle (PRD 8.9). */
export type BundleEnv = Readonly<Record<string, string | undefined>>;

export type BundleSelection =
  | { readonly kind: 'mock' }
  | { readonly kind: 'release'; readonly version: string };

/** The bundle files that have a @terraluna/schema schema, parsed and validated. */
export interface Bundle {
  readonly manifest: BundleManifest;
  readonly validation: Validation;
}

/** Everything bundle:pull copied. Read the optional files through the accessors below. */
export interface PulledBundle extends Bundle {
  /**
   * The pulled files that have no @terraluna/schema schema yet, by bundle path. Their bytes match
   * the manifest's byte count and SHA-256; nothing else about them is known, so they are never
   * parsed here and must not be served as records.
   */
  readonly checksumOnly: ReadonlyMap<string, Uint8Array>;
}

/**
 * An optional pulled file without a schema. `not_present` means the manifest does not list it,
 * which is not the same as an empty file: no empty default is ever substituted.
 */
export type SchemaGapFile =
  | { readonly status: 'not_present'; readonly path: string }
  | { readonly status: 'present'; readonly path: string; readonly bytes: Uint8Array };

function schemaGapFile(bundle: PulledBundle, path: string): SchemaGapFile {
  const bytes = bundle.checksumOnly.get(path);
  return bytes === undefined ? { status: 'not_present', path } : { status: 'present', path, bytes };
}

/** sources.json (ProductRecords), if the manifest lists it. */
export function sourcesFile(bundle: PulledBundle): SchemaGapFile {
  return schemaGapFile(bundle, SOURCES);
}

/** targets.json (target windows), if the manifest lists it. Absent on the mock bundle. */
export function targetsFile(bundle: PulledBundle): SchemaGapFile {
  return schemaGapFile(bundle, TARGETS);
}

/** comparisons/<targetId>.json, if the manifest lists it. */
export function comparisonFile(bundle: PulledBundle, targetId: string): SchemaGapFile {
  return schemaGapFile(bundle, comparisonPath(targetId));
}

/** windows/<body>.json (top-N windows), if the manifest lists it. */
export function windowsTopNFile(bundle: PulledBundle, body: string): SchemaGapFile {
  return schemaGapFile(bundle, windowsTopNPath(body));
}

/** Every file bundle:pull copied for this bundle, manifest.json included, in path order. */
export function pulledPaths(bundle: PulledBundle): string[] {
  return [MANIFEST, VALIDATION, ...bundle.checksumOnly.keys()].sort();
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

/**
 * The production mock guard (PRD 8.4, 12 end-to-end checks): throws MockBundleError when a mock
 * bundle would be served on production. bundle:pull and loadBundle apply it, and the API imports
 * it rather than repeating the rule.
 */
export function assertDeployable(manifest: BundleManifest, env: BundleEnv): void {
  if (manifest.mock && isProduction(env)) {
    throw new MockBundleError(
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

type FileEntry = BundleManifest['files'][number];

/** A directory holding bundle files: a bundle itself, or apps/web/data/ after a pull. */
interface Source {
  readonly dir: string;
  /** How messages name the directory. */
  readonly where: string;
  /** What to do about a missing file. */
  readonly hint: string;
}

/**
 * Reads the regular file at a bundle path. No symbolic link is followed, in any segment of the
 * path (as in the Python validator), so a link cannot pull in a file from outside the bundle.
 */
function readBundleFile(source: Source, path: string, missing: string): Buffer {
  const label = `${source.where}/${path}`;
  const segments = path.split('/');
  let current = source.dir;
  for (const [index, segment] of segments.entries()) {
    current = join(current, segment);
    const stats = lstatSync(current, { throwIfNoEntry: false });
    if (stats === undefined) {
      throw new BundleFileError(path, 'missing', `${label} is missing${missing}: ${source.hint}`);
    }
    const last = index === segments.length - 1;
    if (last ? !stats.isFile() : !stats.isDirectory()) {
      const detail = last ? '' : ` (${segments.slice(0, index + 1).join('/')} is not a directory)`;
      throw new BundleFileError(
        path,
        'not_regular_file',
        `${label} is not a regular file${detail}; symbolic links are not followed`,
      );
    }
  }
  return readFileSync(current);
}

/** Reads a listed file and checks it against its manifest entry. */
function readListedFile(source: Source, entry: FileEntry): Buffer {
  const { path } = entry;
  const label = `${source.where}/${path}`;
  const bytes = readBundleFile(source, path, `, but ${MANIFEST} lists it`);
  if (bytes.byteLength !== entry.bytes) {
    throw new BundleFileError(
      path,
      'bytes_mismatch',
      `${label} has ${String(bytes.byteLength)} bytes, but the manifest lists ${String(entry.bytes)} bytes`,
    );
  }
  if (sha256(bytes) !== entry.sha256) {
    throw new BundleFileError(
      path,
      'sha256_mismatch',
      `${label} does not match the sha256 the manifest lists`,
    );
  }
  return bytes;
}

/** Strict JSON (lib/json.ts); a refusal names the file. */
function decodeFile(bytes: Uint8Array, path: string, label: string): unknown {
  try {
    return decodeJson(bytes, label);
  } catch (error) {
    if (error instanceof BundleError) throw new BundleFileError(path, 'invalid', error.message);
    throw error;
  }
}

function parseManifest(bytes: Uint8Array, label: string): BundleManifest {
  const result = BundleManifestSchema.safeParse(decodeFile(bytes, MANIFEST, label));
  if (!result.success) {
    throw new BundleFileError(
      MANIFEST,
      'invalid',
      `${label} does not match the BundleManifest schema: ${issues(result.error)}`,
    );
  }
  return result.data;
}

function parseValidation(bytes: Uint8Array, label: string): Validation {
  const result = ValidationSchema.safeParse(decodeFile(bytes, VALIDATION, label));
  if (!result.success) {
    throw new BundleFileError(
      VALIDATION,
      'invalid',
      `${label} does not match the Validation schema: ${issues(result.error)}`,
    );
  }
  const seen = new Set<string>();
  for (const record of result.data) {
    if (seen.has(record.gate_id)) {
      const message = `${label}: gate ${record.gate_id} is listed twice`;
      throw new BundleFileError(VALIDATION, 'invalid', message);
    }
    seen.add(record.gate_id);
  }
  return result.data;
}

/** The manifest entries of the files to copy: validation.json's, and the others in path order. */
function pulledEntries(
  manifest: BundleManifest,
  where: string,
): { readonly validation: FileEntry; readonly others: readonly FileEntry[] } {
  const entries = manifest.files
    .filter((entry) => isPulledPath(entry.path))
    .sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  for (const [index, entry] of entries.entries()) {
    if (entries[index + 1]?.path === entry.path) {
      throw new BundleFileError(
        entry.path,
        'listed_twice',
        `${where}/${entry.path} is listed more than once in ${where}/${MANIFEST}`,
      );
    }
  }
  const validation = entries.find((entry) => entry.path === VALIDATION);
  if (validation === undefined) {
    throw new BundleFileError(
      VALIDATION,
      'not_listed',
      `${where}/${VALIDATION} is not listed in the files of ${where}/${MANIFEST}`,
    );
  }
  return { validation, others: entries.filter((entry) => entry !== validation) };
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

interface ReadBundle {
  readonly bundle: PulledBundle;
  readonly manifestBytes: Uint8Array;
  /** The listed files to copy, by bundle path, as read. */
  readonly files: ReadonlyMap<string, Uint8Array>;
}

function readBundle(source: Source, selection: BundleSelection): ReadBundle {
  const label = `${source.where}/${MANIFEST}`;
  const manifestBytes = readBundleFile(source, MANIFEST, '');
  const manifest = parseManifest(manifestBytes, label);
  const entries = pulledEntries(manifest, source.where);
  const validationBytes = readListedFile(source, entries.validation);
  const checksumOnly = new Map(
    entries.others.map((entry) => [entry.path, readListedFile(source, entry)] as const),
  );
  const validation = parseValidation(validationBytes, `${source.where}/${VALIDATION}`);
  checkSelection(manifest, selection, label);
  const files = new Map([[VALIDATION, validationBytes] as const, ...checksumOnly]);
  return { bundle: { manifest, validation, checksumOnly }, manifestBytes, files };
}

/**
 * The output path of a bundle path under outDir. Every existing segment must be a directory, and
 * an existing file a regular file, so a pull never writes through a symbolic link. With `create`,
 * missing directories are made.
 */
function outputPath(outDir: string, path: string, create: boolean): string {
  const segments = path.split('/');
  let current = outDir;
  for (const [index, segment] of segments.entries()) {
    current = join(current, segment);
    const last = index === segments.length - 1;
    const stats = lstatSync(current, { throwIfNoEntry: false });
    if (stats === undefined) {
      if (create && !last) mkdirSync(current);
    } else if (last ? !stats.isFile() : !stats.isDirectory()) {
      const kind = last ? 'regular file' : 'directory';
      throw new BundleError(`${current} exists and is not a ${kind}`);
    }
  }
  return current;
}

export interface PullOptions {
  /** The repository root, which holds fixtures/mock-bundle/ and release/. */
  readonly repoRoot: string;
  /** Where the files are copied: apps/web/data/. */
  readonly outDir: string;
  readonly env: BundleEnv;
}

/**
 * Checks the selected bundle's manifest and every file it lists that the server reads, then copies
 * them, byte for byte, into outDir. Nothing is written unless every check passes. A file an
 * earlier pull wrote and this manifest does not list is left in place, and never read.
 */
export function pullBundle({ repoRoot, outDir, env }: PullOptions): PulledBundle {
  const selection = selectBundle(env);
  const where = bundleSourcePath(selection);
  const hint =
    selection.kind === 'mock'
      ? 'the mock bundle is generated by `make mock-bundle`'
      : `BUNDLE_VERSION=${selection.version} requests this bundle, and bundle:pull never falls back to the mock`;
  const source = { dir: join(repoRoot, where), where, hint };
  const { bundle, manifestBytes, files } = readBundle(source, selection);
  assertDeployable(bundle.manifest, env);
  mkdirSync(outDir, { recursive: true });
  for (const path of [...files.keys(), MANIFEST]) outputPath(outDir, path, false);
  for (const [path, bytes] of files) writeFileSync(outputPath(outDir, path, true), bytes);
  // Last, so a pull that stops part way leaves the earlier manifest, whose sizes and checksums
  // loadBundle then checks against whatever was written.
  writeFileSync(outputPath(outDir, MANIFEST, true), manifestBytes);
  return bundle;
}

export interface LoadOptions {
  /** The directory bundle:pull wrote; apps/web/data/ by default. */
  readonly dataDir?: string;
  readonly env?: BundleEnv;
}

/**
 * Reads the pulled bundle for rendering and for the API. It repeats bundle:pull's checks against
 * the current environment, so it throws on a mock bundle for production (MockBundleError), on a
 * pulled bundle that BUNDLE_VERSION does not select, and on a pulled file that is missing or was
 * changed after the pull (BundleFileError).
 */
export function loadBundle({ dataDir, env }: LoadOptions = {}): PulledBundle {
  const dir = dataDir ?? join(process.cwd(), 'data');
  const environment = env ?? process.env;
  const selection = selectBundle(environment);
  const { bundle } = readBundle({ dir, where: dir, hint: PULL_HINT }, selection);
  assertDeployable(bundle.manifest, environment);
  return bundle;
}
