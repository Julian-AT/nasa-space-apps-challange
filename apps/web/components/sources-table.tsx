// The tables of /data (PRD 5, 8.4, 8.6, 8.12). SourcesTable shows ProductRecords from the bundle's
// sources.json, already validated by the reader in app/data/sources.ts. Its columns are PRD 8.12
// ProductRecord fields headed by their names, and every cell is the record's own text: a field the
// record omits or gives as null is labelled Unavailable with the reason, and a value that is not
// text is refused rather than formatted by guesswork. BundleFilesTable lists the files of
// manifest.json with their path, sha256 and bytes. Neither adds licence or attribution text of its
// own. Server components.
import { type BundleManifest, BundleManifestSchema } from '@terraluna/schema';

import type { SourceRecord } from '@/app/data/sources';
import { LabelledValue } from '@/components/labelled-value';
import { cn } from '@/lib/utils';

export type { SourceRecord } from '@/app/data/sources';

/**
 * The ProductRecord fields /data shows (PRD 8.12), led by the product id: who provides the product
 * and where, its licence or attribution, its checksum and whether it was verified.
 */
export const SOURCE_FIELDS = [
  'product_id',
  'provider',
  'version',
  'body',
  'source_url',
  'license_or_attribution',
  'checksum',
  'verification_status',
] as const;

export type SourceField = (typeof SOURCE_FIELDS)[number];

type BundleFile = BundleManifest['files'][number];

/** The BundleManifest file fields, in schema order (PRD 8.4). */
export const BUNDLE_FILE_FIELDS = Object.keys(BundleManifestSchema.shape.files.element.shape) as readonly (keyof BundleFile)[];

/** The reason shown with Unavailable for a field the record omits or gives as null. */
export function omittedReason(field: string): string {
  return `sources.json gives no ${field} for this product`;
}

// Identifiers, checksums and links are set in a monospace face; free text is not.
const MONOSPACE: ReadonlySet<string> = new Set(['product_id', 'checksum', 'source_url', 'path', 'sha256', 'bytes']);

function cellClass(field: string): string {
  return cn('border border-border px-2 py-1 text-left align-top wrap-anywhere', MONOSPACE.has(field) && 'font-mono');
}

function headerClass(field: string): string {
  return cn(cellClass(field), 'bg-muted font-semibold');
}

function valueText(record: SourceRecord, field: SourceField): string | null {
  const value = record[field];
  if (value === undefined || value === null) return null;
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(
      `SourcesTable: ${field} of ${JSON.stringify(record['product_id'])} is ${JSON.stringify(value)}, not text to show`,
    );
  }
  return value;
}

function Value({ record, field }: { readonly record: SourceRecord; readonly field: SourceField }) {
  const text = valueText(record, field);
  const content = text ?? <LabelledValue label="Unavailable" reason={omittedReason(field)} />;
  return field === 'product_id' ? (
    <th scope="row" data-field={field} className={cellClass(field)}>
      {content}
    </th>
  ) : (
    <td data-field={field} className={cellClass(field)}>
      {content}
    </td>
  );
}

export interface SourcesTableProps {
  /** ProductRecords from sources.json, validated as a whole list. */
  readonly records: readonly SourceRecord[];
}

export function SourcesTable({ records }: SourcesTableProps) {
  return (
    <table className="w-full table-fixed border-collapse text-sm">
      <caption className="mb-2 text-left">ProductRecords from the bundle&apos;s sources.json</caption>
      <thead>
        <tr>
          {SOURCE_FIELDS.map((field) => (
            <th key={field} scope="col" className={headerClass(field)}>
              {field}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {records.map((record, index) => (
          <tr key={index}>
            {SOURCE_FIELDS.map((field) => (
              <Value key={field} record={record} field={field} />
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export interface BundleFilesTableProps {
  /** manifest.files of the bundle. */
  readonly files: readonly BundleFile[];
}

export function BundleFilesTable({ files }: BundleFilesTableProps) {
  if (files.length === 0) return <p>manifest.json lists no files</p>;
  return (
    <table className="w-full table-fixed border-collapse text-sm">
      <caption className="mb-2 text-left">Files listed in the bundle&apos;s manifest.json</caption>
      <thead>
        <tr>
          {BUNDLE_FILE_FIELDS.map((field) => (
            <th key={field} scope="col" className={headerClass(field)}>
              {field}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {files.map((file) => (
          <tr key={file.path}>
            {BUNDLE_FILE_FIELDS.map((field) =>
              field === 'path' ? (
                <th key={field} scope="row" data-field={field} className={cellClass(field)}>
                  {file.path}
                </th>
              ) : (
                <td key={field} data-field={field} className={cellClass(field)}>
                  {String(file[field])}
                </td>
              ),
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
