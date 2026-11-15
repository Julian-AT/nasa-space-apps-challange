// /data (PRD 5, 8.4, 8.6, 8.12): the bundle's own files from manifest.json, and the ProductRecords
// of sources.json when the bundle lists it. Whether sources.json is in the bundle is decided by
// manifest.files, never by a file on disk: a listed file that is missing, changed or malformed
// fails the build, and an unlisted one gives an explicit Unavailable state. Every record here is
// synthetic and made in the test, with fake ids such as 'test-product'.
import { mkdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import * as schemas from '@terraluna/schema';
import { type BundleManifest, BundleManifestSchema, ValidationSchema } from '@terraluna/schema';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import RootLayout from '../app/layout.tsx';
import DataPage, { dynamic } from '../app/data/page.tsx';
import {
  NO_PRODUCT_RECORD_SCHEMA,
  NO_SOURCES,
  productRecordSchema,
  readSources,
  type RecordSchema,
  SOURCES,
} from '../app/data/sources.ts';
import type { Bundle } from '../lib/bundle.ts';
import { ALL_GATES_NOT_RUN, sha256, tempDir, toBytes, WEB_DIR } from '../tests/helpers.ts';
import { elements, textOf } from '../tests/markup.ts';
import { prd812ProductFields } from '../tests/prd.ts';
import { labelledValuesOutsideTables, readTables } from '../tests/tables.ts';
import { LabelledValue } from './labelled-value.tsx';
import {
  BUNDLE_FILE_FIELDS,
  BundleFilesTable,
  omittedReason,
  SOURCE_FIELDS,
  type SourceRecord,
  SourcesTable,
} from './sources-table.tsx';

interface TestState {
  bundle: Bundle | null;
}

const state: TestState = vi.hoisted((): TestState => ({ bundle: null }));

// next/font/google runs only inside next build, which downloads and self-hosts the font files.
vi.mock('next/font/google', () => {
  const font = () => ({ variable: 'font-test-variable', className: 'font-test', style: {} });
  return { Geist: font, Geist_Mono: font, Instrument_Serif: font, Michroma: font };
});

vi.mock('next/navigation', () => ({ usePathname: () => '/data' }));

vi.mock('../lib/bundle.ts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/bundle.ts')>();
  return {
    ...actual,
    loadBundle: () => {
      if (state.bundle === null) throw new Error('the test set no bundle');
      return state.bundle;
    },
  };
});

const VALIDATION_BYTES = toBytes(ALL_GATES_NOT_RUN);

function entry(path: string, bytes: Uint8Array) {
  return { path, sha256: sha256(bytes), bytes: bytes.byteLength };
}

/** A schema-valid manifest listing validation.json and the given extra entries. */
function manifest(extra: readonly { path: string; sha256: string; bytes: number }[] = []): BundleManifest {
  return BundleManifestSchema.parse({
    bundle_version: 'test-bundle',
    created_utc: '2026-11-15T09:00:00Z',
    files: [entry('validation.json', VALIDATION_BYTES), ...extra],
    git_sha: null,
    mock: true,
    model: null,
    run_id: null,
    schema_version: '1.0.0',
    shipping_mode: null,
    targets: [],
  });
}

function bundleWith(manifestValue: BundleManifest): Bundle {
  return { manifest: manifestValue, validation: ValidationSchema.parse(ALL_GATES_NOT_RUN) };
}

/** A data directory holding sources.json with the given bytes. */
function dataDirWith(bytes: Uint8Array | null): string {
  const dir = tempDir();
  mkdirSync(dir, { recursive: true });
  if (bytes !== null) writeFileSync(join(dir, SOURCES), bytes);
  return dir;
}

function product(id: string, fields: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    product_id: id,
    provider: 'test-provider',
    version: 'test-version',
    body: 'moon',
    source_url: 'https://example.invalid/test-product',
    license_or_attribution: 'test-licence-text',
    checksum: 'f'.repeat(64),
    verification_status: 'NOT_CHECKED',
    // The remaining ProductRecord fields (PRD 8.12), synthetic like the rest.
    local_path: null,
    acquisition_time: null,
    frequency_or_wavelength: null,
    transmit_receive_convention: null,
    units: 'test-unit',
    calibration_type: null,
    native_grid: null,
    effective_resolution: null,
    crs_and_frame: 'test-crs',
    incidence_source: null,
    qa_definition: null,
    ...fields,
  };
}

/** A fake record schema for tests: accepts objects whose product_id starts with "test-". */
const TEST_SCHEMA: RecordSchema = {
  safeParse(value: unknown) {
    if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
      const id: unknown = (value as Record<string, unknown>)['product_id'];
      if (typeof id === 'string' && id.startsWith('test-')) return { success: true, data: value };
    }
    return { success: false, error: { issues: [{ path: ['product_id'], message: 'not a test product' }] } };
  },
};

function render(records: readonly SourceRecord[]): string {
  return renderToStaticMarkup(<SourcesTable records={records} />);
}

/** The text of an Unavailable labelled value, its visually hidden meaning included. */
function unavailableText(reason: string, source?: string): string {
  const value =
    source === undefined ? (
      <LabelledValue label="Unavailable" reason={reason} />
    ) : (
      <LabelledValue label="Unavailable" reason={reason} source={source} />
    );
  return textOf(renderToStaticMarkup(value));
}

// useId gives different ids in separate render trees, so markup is compared without them.
function withoutIds(html: string): string {
  return html.replace(/\b(id|aria-describedby|aria-labelledby)="[^"]*"/g, '$1=""');
}

describe('readSources: presence comes from manifest.files', () => {
  it('is Unavailable with "No sources.json in this bundle" when the manifest does not list it', () => {
    const result = readSources({ manifest: manifest(), dataDir: dataDirWith(null), schema: TEST_SCHEMA });
    expect(result).toEqual({ kind: 'unavailable', reason: NO_SOURCES, source: 'manifest.json' });
    expect(NO_SOURCES).toBe('No sources.json in this bundle');
  });

  it('ignores a sources.json on disk that the manifest does not list', () => {
    const dir = dataDirWith(toBytes([product('test-product')]));
    const result = readSources({ manifest: manifest(), dataDir: dir, schema: TEST_SCHEMA });
    expect(result.kind).toBe('unavailable');
  });

  it('fails the build, naming the file, when it is listed but was not copied', () => {
    const bytes = toBytes([product('test-product')]);
    const read = () => readSources({ manifest: manifest([entry(SOURCES, bytes)]), dataDir: dataDirWith(null) });
    expect(read).toThrow(/sources\.json/);
    expect(read).toThrow(/missing/);
  });

  it('fails the build when the file differs from the manifest sha256', () => {
    const bytes = toBytes([product('test-product')]);
    const listed = { ...entry(SOURCES, bytes), sha256: '0'.repeat(64) };
    const read = () => readSources({ manifest: manifest([listed]), dataDir: dataDirWith(bytes), schema: TEST_SCHEMA });
    expect(read).toThrow(/sources\.json.*sha256/);
  });

  it('fails the build when the file differs from the manifest byte count', () => {
    const bytes = toBytes([product('test-product')]);
    const listed = { ...entry(SOURCES, bytes), bytes: bytes.byteLength + 1 };
    const read = () => readSources({ manifest: manifest([listed]), dataDir: dataDirWith(bytes), schema: TEST_SCHEMA });
    expect(read).toThrow(/sources\.json.*bytes/);
  });

  it('fails the build when the manifest lists sources.json twice', () => {
    const bytes = toBytes([product('test-product')]);
    const listed = entry(SOURCES, bytes);
    const read = () =>
      readSources({ manifest: manifest([listed, listed]), dataDir: dataDirWith(bytes), schema: TEST_SCHEMA });
    expect(read).toThrow(/sources\.json.*more than once/);
  });

  it('never follows a symbolic link in place of sources.json', () => {
    const bytes = toBytes([product('test-product')]);
    const elsewhere = dataDirWith(bytes);
    const dir = dataDirWith(null);
    symlinkSync(join(elsewhere, SOURCES), join(dir, SOURCES));
    const read = () => readSources({ manifest: manifest([entry(SOURCES, bytes)]), dataDir: dir, schema: TEST_SCHEMA });
    expect(read).toThrow(/sources\.json.*not a regular file/);
  });
});

describe('readSources: validation', () => {
  function read(value: unknown, schema: RecordSchema | null = TEST_SCHEMA) {
    const bytes = typeof value === 'string' ? Buffer.from(value, 'utf8') : toBytes(value);
    return () => readSources({ manifest: manifest([entry(SOURCES, bytes)]), dataDir: dataDirWith(bytes), schema });
  }

  it('returns every record when the whole list is valid', () => {
    const records = [product('test-product'), product('test-product-2')];
    expect(read(records)()).toEqual({ kind: 'records', records });
  });

  it('accepts an empty list as zero records, not as missing', () => {
    expect(read([])()).toEqual({ kind: 'records', records: [] });
  });

  it.each([
    ['text that is not JSON', 'not json'],
    ['a JSON object instead of a list', '{"product_id": "test-product"}'],
    ['a duplicate key', '[{"product_id": "test-product", "product_id": "test-other"}]'],
  ])('refuses %s with an invalid-sources message', (_, text) => {
    expect(read(text)).toThrow(/invalid sources\.json/i);
  });

  it.each([
    ['a null entry', [product('test-product'), null]],
    ['a list entry', [product('test-product'), [product('test-product-2')]]],
    ['a string entry', ['test-product']],
  ])('refuses a list with %s, without schema or with one', (_, records) => {
    expect(read(records)).toThrow(/invalid sources\.json/i);
    expect(read(records, null)).toThrow(/invalid sources\.json/i);
  });

  it('refuses the whole list when one record fails the schema, so no partial rows render', () => {
    const records = [product('test-product'), product('real-looking-product')];
    expect(read(records)).toThrow(/invalid sources\.json.*\[1\].*product_id.*not a test product/i);
  });

  it('refuses two records for the same product_id', () => {
    expect(read([product('test-product'), product('test-product')])).toThrow(
      /invalid sources\.json.*test-product.*more than once/i,
    );
  });

  it('without a ProductRecord schema, is Unavailable with that reason and returns no records', () => {
    expect(read([product('test-product')], null)()).toEqual({
      kind: 'unavailable',
      reason: NO_PRODUCT_RECORD_SCHEMA,
      source: SOURCES,
    });
    expect(NO_PRODUCT_RECORD_SCHEMA).toBe('ProductRecord schema not in @terraluna/schema');
  });

  it('uses the ProductRecord schema of @terraluna/schema exactly when the package exports one', () => {
    const exported = 'ProductRecordSchema' in schemas;
    expect(productRecordSchema() !== null).toBe(exported);
    const bytes = toBytes([product('test-product')]);
    const result = readSources({ manifest: manifest([entry(SOURCES, bytes)]), dataDir: dataDirWith(bytes) });
    if (!exported) expect(result).toEqual({ kind: 'unavailable', reason: NO_PRODUCT_RECORD_SCHEMA, source: SOURCES });
    else expect(result).toMatchObject({ kind: 'records', records: [{ product_id: 'test-product' }] });
  });

  it('names the missing schema in a code comment', () => {
    const source = readFileSync(join(WEB_DIR, 'app', 'data', 'sources.ts'), 'utf8');
    expect(source).toMatch(/\/\/[^\n]*ProductRecordSchema/);
  });
});

describe('SourcesTable', () => {
  it('has the PRD 8.12 ProductRecord fields as columns, headed by their names', () => {
    const prd = prd812ProductFields();
    for (const field of SOURCE_FIELDS) expect(prd).toContain(field);
    expect(SOURCE_FIELDS).toEqual(expect.arrayContaining(['product_id', 'license_or_attribution', 'checksum']));
    const [table] = readTables(render([product('test-product')]));
    expect(table?.headers.map((cell) => cell.text)).toEqual([...SOURCE_FIELDS]);
    expect(table?.headers.every((cell) => cell.tag === 'th' && cell.scope === 'col')).toBe(true);
  });

  it('shows one row per record with every value verbatim, led by its product_id', () => {
    const records = [product('test-product'), product('test-product-2', { provider: 'test-other-provider' })];
    const [table] = readTables(render(records));
    expect(table?.rows).toHaveLength(2);
    records.forEach((record, index) => {
      const row = table?.rows[index] ?? [];
      expect(row[0]).toMatchObject({ tag: 'th', scope: 'row', text: record['product_id'] });
      expect(row.map((cell) => cell.field)).toEqual([...SOURCE_FIELDS]);
      expect(row.map((cell) => cell.text)).toEqual(SOURCE_FIELDS.map((field) => record[field]));
    });
  });

  it.each(['license_or_attribution', 'checksum', 'source_url', 'provider'] as const)(
    'labels an omitted or null %s Unavailable with a reason',
    (field) => {
      const omitted = Object.fromEntries(Object.entries(product('test-product')).filter(([key]) => key !== field));
      const nulled = product('test-product-2', { [field]: null });
      const tables = readTables(render([omitted, nulled]));
      expect(tables).toHaveLength(1);
      const rows = tables[0]?.rows ?? [];
      expect(rows.map((row) => row[0]?.text)).toEqual(['test-product', 'test-product-2']);
      for (const row of rows) {
        expect(row.map((cell) => cell.field)).toEqual([...SOURCE_FIELDS]);
        const cell = row.find((each) => each.field === field);
        expect(cell).toMatchObject({ label: 'Unavailable', reason: omittedReason(field) });
      }
      expect(omittedReason(field)).toBe(`sources.json gives no ${field} for this product`);
    },
  );

  it.each([
    ['a number', 42],
    ['a boolean', true],
    ['an object', { name: 'test' }],
    ['a blank string', '  '],
  ])('refuses %s as a value rather than guess how to show it', (_, value) => {
    expect(() => render([product('test-product', { license_or_attribution: value })])).toThrow(
      /license_or_attribution/,
    );
  });

  it('adds no text of its own beyond the caption, headers, values and Unavailable reasons', () => {
    const omitted = product('test-product');
    delete omitted['license_or_attribution'];
    const html = render([omitted]);
    const [table] = readTables(html);
    const known = new Set([
      ...(table?.headers.map((cell) => cell.text) ?? []),
      ...Object.values(omitted).map(String),
      unavailableText(omittedReason('license_or_attribution')),
    ]);
    for (const cell of table?.rows.flat() ?? []) expect(known).toContain(cell.text);
  });
});

describe('BundleFilesTable', () => {
  it('lists every manifest file with its path, sha256 and bytes, headed by the field names', () => {
    const sources = toBytes([product('test-product')]);
    const files = manifest([entry(SOURCES, sources)]).files;
    expect(BUNDLE_FILE_FIELDS).toEqual(['path', 'sha256', 'bytes']);
    const [table] = readTables(renderToStaticMarkup(<BundleFilesTable files={files} />));
    expect(table?.caption).toMatch(/manifest\.json/);
    expect(table?.headers.map((cell) => cell.text)).toEqual(['path', 'sha256', 'bytes']);
    expect(table?.rows.map((row) => row.map((cell) => cell.text))).toEqual(
      files.map((file) => [file.path, file.sha256, String(file.bytes)]),
    );
    expect(table?.rows.every((row) => row[0]?.tag === 'th' && row[0].scope === 'row')).toBe(true);
  });

  it('says so when the manifest lists no files', () => {
    const html = renderToStaticMarkup(<BundleFilesTable files={[]} />);
    expect(readTables(html)).toHaveLength(0);
    expect(textOf(html)).toBe('manifest.json lists no files');
  });
});

describe('/data', () => {
  let dataDir = '';

  beforeEach(() => {
    // The page reads <cwd>/data, as next build does from apps/web.
    const root = tempDir();
    vi.spyOn(process, 'cwd').mockReturnValue(root);
    dataDir = join(root, 'data');
    mkdirSync(dataDir);
    state.bundle = bundleWith(manifest());
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function page(): string {
    return renderToStaticMarkup(<DataPage />);
  }

  function list(bytes: Uint8Array, write = true): void {
    state.bundle = bundleWith(manifest([entry(SOURCES, bytes)]));
    if (write) writeFileSync(join(dataDir, SOURCES), bytes);
  }

  it('is statically rendered: next build fails if anything would make it dynamic', () => {
    expect(dynamic).toBe('error');
  });

  it('has exactly one h1 and no main landmark of its own', () => {
    const found = elements(renderToStaticMarkup(<RootLayout><DataPage /></RootLayout>));
    expect(found.filter((element) => element.tag === 'h1')).toHaveLength(1);
    expect(found.filter((element) => element.tag === 'main')).toHaveLength(1);
    expect(page()).not.toMatch(/<main\b/);
  });

  it('without sources.json in manifest.files: Unavailable "No sources.json in this bundle", no source table', () => {
    const html = page();
    expect(textOf(html)).toContain('No sources.json in this bundle');
    expect(labelledValuesOutsideTables(html)).toEqual([{ label: 'Unavailable', reason: NO_SOURCES }]);
    const tables = readTables(html);
    expect(tables).toHaveLength(1);
    expect(tables[0]?.headers.map((cell) => cell.text)).toEqual([...BUNDLE_FILE_FIELDS]);
  });

  it('lists the bundle files exactly as BundleFilesTable renders them', () => {
    const files = state.bundle?.manifest.files ?? [];
    expect(withoutIds(page())).toContain(withoutIds(renderToStaticMarkup(<BundleFilesTable files={files} />)));
  });

  it('fails the build when sources.json is listed but was not copied, never showing the empty state', () => {
    list(toBytes([product('test-product')]), false);
    expect(page).toThrow(/sources\.json/);
  });

  it('fails the build when the copied sources.json differs from the manifest', () => {
    const bytes = toBytes([product('test-product')]);
    list(bytes, false);
    writeFileSync(join(dataDir, SOURCES), toBytes([product('test-product-2')]));
    expect(page).toThrow(/sources\.json/);
  });

  it('fails the build on a malformed list rather than render part of it', () => {
    list(Buffer.from('{"product_id": "test-product"}', 'utf8'));
    expect(page).toThrow(/invalid sources\.json/i);
  });

  it('renders rows only from the ProductRecord schema of @terraluna/schema', () => {
    list(toBytes([product('test-product')]));
    const html = page();
    if (productRecordSchema() === null) {
      expect(labelledValuesOutsideTables(html)).toEqual([{ label: 'Unavailable', reason: NO_PRODUCT_RECORD_SCHEMA }]);
      expect(readTables(html)).toHaveLength(1);
      expect(textOf(html)).not.toContain('test-provider');
    } else {
      expect(labelledValuesOutsideTables(html)).toEqual([]);
      expect(readTables(html)).toHaveLength(2);
      expect(textOf(html)).toContain('test-provider');
    }
  });

  it('types no licence or attribution: the text beyond headings and tables comes from records', () => {
    const html = page();
    const tables = readTables(html);
    expect(tables).toHaveLength(1);
    const outside = textOf(html.replace(/<table[\s\S]*<\/table>/g, ' '));
    expect(outside).toBe(`Data sources Sources ${unavailableText(NO_SOURCES, 'manifest.json')} Bundle files`);
    const source = readFileSync(join(WEB_DIR, 'app', 'data', 'page.tsx'), 'utf8');
    expect(source).not.toMatch(/licen[cs]e|attribution|CC[- ]BY|public domain|copyright/i);
  });
});
