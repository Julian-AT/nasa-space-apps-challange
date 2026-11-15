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
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
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
  return cn(MONOSPACE.has(field) && 'font-mono text-xs');
}

const ROW_HEADER = 'px-3 py-2.5 text-left align-top font-mono text-xs font-semibold wrap-anywhere';

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
    <th scope="row" data-field={field} className={ROW_HEADER}>
      {content}
    </th>
  ) : (
    <TableCell data-field={field} className={cellClass(field)}>
      {content}
    </TableCell>
  );
}

export interface SourcesTableProps {
  /** ProductRecords from sources.json, validated as a whole list. */
  readonly records: readonly SourceRecord[];
}

export function SourcesTable({ records }: SourcesTableProps) {
  return (
    <Table className="min-w-[56rem]">
      <TableCaption>ProductRecords from the bundle&apos;s sources.json</TableCaption>
      <TableHeader>
        <TableRow>
          {SOURCE_FIELDS.map((field) => (
            <TableHead key={field} scope="col">
              {field}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {records.map((record, index) => (
          <TableRow key={index}>
            {SOURCE_FIELDS.map((field) => (
              <Value key={field} record={record} field={field} />
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export interface BundleFilesTableProps {
  /** manifest.files of the bundle. */
  readonly files: readonly BundleFile[];
}

export function BundleFilesTable({ files }: BundleFilesTableProps) {
  if (files.length === 0) return <p>manifest.json lists no files</p>;
  return (
    <Table className="min-w-[40rem]">
      <TableCaption>Files listed in the bundle&apos;s manifest.json</TableCaption>
      <TableHeader>
        <TableRow>
          {BUNDLE_FILE_FIELDS.map((field) => (
            <TableHead key={field} scope="col">
              {field}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {files.map((file) => (
          <TableRow key={file.path}>
            {BUNDLE_FILE_FIELDS.map((field) =>
              field === 'path' ? (
                <th key={field} scope="row" data-field={field} className={ROW_HEADER}>
                  {file.path}
                </th>
              ) : (
                <TableCell key={field} data-field={field} className={cellClass(field)}>
                  {String(file[field])}
                </TableCell>
              ),
            )}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
