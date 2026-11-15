// sources.json for /data (PRD 5, 8.4, 8.6, 8.12): the bundle's ProductRecords, read at build time
// from apps/web/data/. Whether the bundle has sources.json is decided by manifest.files only, never
// by a file merely being on disk. A listed file must be in the data directory with the listed
// byte count and SHA-256, or next build fails: a file that was not copied never turns into the
// empty state. The list is validated as a whole, so a malformed list renders no partial rows.
//
// This reader belongs to /data. bundle:pull and loadBundle (lib/bundle.ts, scripts/) do not copy
// sources.json yet; until they do, a bundle that lists it fails the build here.
import { createHash } from 'node:crypto';
import { lstatSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import * as schemas from '@terraluna/schema';
import type { BundleManifest } from '@terraluna/schema';

import { BundleError } from '../../lib/errors.ts';
import { decodeJson } from '../../lib/json.ts';

export const SOURCES = 'sources.json';

/** The reason /data shows when manifest.files does not list sources.json. */
export const NO_SOURCES = 'No sources.json in this bundle';

/** The reason /data shows when @terraluna/schema has no ProductRecord schema to validate with. */
export const NO_PRODUCT_RECORD_SCHEMA = 'ProductRecord schema not in @terraluna/schema';

interface SchemaIssue {
  readonly path: readonly PropertyKey[];
  readonly message: string;
}

/** The part of a zod schema the reader uses. */
export interface RecordSchema {
  safeParse(
    value: unknown,
  ): { readonly success: true; readonly data: unknown } | { readonly success: false; readonly error: { readonly issues: readonly SchemaIssue[] } };
}

/** One ProductRecord as the schema returned it. */
export type SourceRecord = Readonly<Record<string, unknown>>;

export type SourcesState =
  | { readonly kind: 'records'; readonly records: readonly SourceRecord[] }
  | {
      readonly kind: 'unavailable';
      readonly reason: string;
      /** The file that was checked. */
      readonly source: 'manifest.json' | typeof SOURCES;
    };

function isRecordSchema(value: unknown): value is RecordSchema {
  return typeof value === 'object' && value !== null && typeof Reflect.get(value, 'safeParse') === 'function';
}

/**
 * The ProductRecord zod schema of @terraluna/schema, or null when the package has none.
 *
 * On this branch @terraluna/schema exports no ProductRecordSchema (the ProductRecord contract of
 * PRD 8.12 is added by another task), so this returns null and /data shows Unavailable with
 * NO_PRODUCT_RECORD_SCHEMA. The schema is looked up by its export name, so the page starts
 * validating and rendering rows once the package exports ProductRecordSchema.
 */
export function productRecordSchema(): RecordSchema | null {
  // ProductRecordSchema: the missing export this lookup waits for.
  const candidate: unknown = Reflect.get(schemas, 'ProductRecordSchema');
  return isRecordSchema(candidate) ? candidate : null;
}

function isPlainObject(value: unknown): value is SourceRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function invalid(label: string, why: string): BundleError {
  return new BundleError(`invalid sources.json (${label}): ${why}; /data renders no source rows from it`);
}

function issues(list: readonly SchemaIssue[]): string {
  return list.map((issue) => `${issue.path.map(String).join('.') || '(root)'}: ${issue.message}`).join('; ');
}

/** The manifest entry for sources.json, or null when the bundle does not list it. */
function listing(manifest: BundleManifest): BundleManifest['files'][number] | null {
  const listed = manifest.files.filter((file) => file.path === SOURCES);
  if (listed.length > 1) throw new BundleError(`manifest.json lists ${SOURCES} more than once`);
  return listed[0] ?? null;
}

function readListed(path: string, entry: BundleManifest['files'][number]): Buffer {
  const stats = lstatSync(path, { throwIfNoEntry: false });
  if (stats === undefined) {
    throw new BundleError(
      `manifest.json lists ${SOURCES}, but ${path} is missing: the bundle has sources, so the page ` +
        'refuses to show the empty state. Copy sources.json from the bundle into the data directory.',
    );
  }
  if (!stats.isFile()) throw new BundleError(`${path} (${SOURCES}) is not a regular file`);
  const bytes = readFileSync(path);
  if (bytes.byteLength !== entry.bytes) {
    throw new BundleError(
      `${path} (${SOURCES}) has ${String(bytes.byteLength)} bytes, but the manifest lists ${String(entry.bytes)} bytes`,
    );
  }
  if (createHash('sha256').update(bytes).digest('hex') !== entry.sha256) {
    throw new BundleError(`${path} (${SOURCES}) does not match the sha256 the manifest lists`);
  }
  return bytes;
}

/** Every entry must be a JSON object, and each product_id may appear once. */
function checkList(value: unknown, label: string): readonly SourceRecord[] {
  if (!Array.isArray(value)) throw invalid(label, 'it must be a JSON list of ProductRecords');
  const records: SourceRecord[] = [];
  value.forEach((item: unknown, index) => {
    if (!isPlainObject(item)) throw invalid(label, `[${String(index)}] is not a JSON object`);
    records.push(item);
  });
  return records;
}

function validate(records: readonly SourceRecord[], schema: RecordSchema, label: string): SourceRecord[] {
  const seen = new Set<unknown>();
  return records.map((record, index) => {
    const result = schema.safeParse(record);
    if (!result.success) throw invalid(label, `[${String(index)}] ${issues(result.error.issues)}`);
    if (!isPlainObject(result.data)) throw invalid(label, `[${String(index)}] is not a JSON object`);
    const id = result.data['product_id'];
    if (seen.has(id)) throw invalid(label, `product_id ${JSON.stringify(id)} is listed more than once`);
    seen.add(id);
    return result.data;
  });
}

export interface ReadSourcesOptions {
  readonly manifest: BundleManifest;
  /** The directory bundle:pull writes; apps/web/data/ by default. */
  readonly dataDir?: string;
  /** The ProductRecord schema; productRecordSchema() by default. */
  readonly schema?: RecordSchema | null;
}

/** The sources of the bundle for /data. Throws, so next build fails, on any listed-file problem. */
export function readSources({ manifest, dataDir, schema }: ReadSourcesOptions): SourcesState {
  const entry = listing(manifest);
  if (entry === null) return { kind: 'unavailable', reason: NO_SOURCES, source: 'manifest.json' };
  const path = join(dataDir ?? join(process.cwd(), 'data'), SOURCES);
  const bytes = readListed(path, entry);
  let value: unknown;
  try {
    value = decodeJson(bytes, path);
  } catch (error) {
    throw invalid(path, error instanceof Error ? error.message : String(error));
  }
  const records = checkList(value, path);
  const recordSchema = schema === undefined ? productRecordSchema() : schema;
  if (recordSchema === null) {
    return { kind: 'unavailable', reason: NO_PRODUCT_RECORD_SCHEMA, source: SOURCES };
  }
  return { kind: 'records', records: validate(records, recordSchema, path) };
}
