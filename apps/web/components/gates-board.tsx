// The validation board (PRD 8.4, 8.6, 8.12, 9, 10 "Validation board"): the GateRecords of the
// bundle's validation.json as one table. Columns are the GateRecord fields of @terraluna/schema,
// in schema order, headed by their field names; rows are the schema's gate ids in order. Every
// value is the record's own text: a status is shown as a badge whose text is the status
// itself, so nothing depends on colour. Every status and result comes from the records.
// A null field is labelled Unavailable with the reason; a gate with no record says so and is
// never shown with a status. A server component.
import { type GateRecord, GateRecordSchema, type Validation } from '@terraluna/schema';

import type { ReactNode } from 'react';

import { LabelledValue } from '@/components/labelled-value';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { cn } from '@/lib/utils';

export type GateField = keyof GateRecord;
export type GateId = GateRecord['gate_id'];

/** The GateRecord fields, in schema order (PRD 8.12). */
export const GATE_FIELDS = Object.keys(GateRecordSchema.shape) as readonly GateField[];

/** The release gates, in schema order (PRD 9). */
export const GATE_IDS: readonly GateId[] = GateRecordSchema.shape.gate_id.options;

/** The row text for a gate that validation.json does not list. */
export const NO_RECORD = 'No record in this bundle';

/** The reason shown with Unavailable for a field that validation.json gives as null. */
export const NULL_REASON = 'validation.json gives null for this field';

// Identifiers and statuses are set in a monospace face; free text is not. Each column has one
// style whatever the value, so a status is told apart only by its text.
const IDENTIFIER: ReadonlySet<GateField> = new Set(['gate_id', 'status', 'evidence_path', 'run_id', 'command', 'config_version', 'input_hashes']);

function cellClass(field: GateField): string {
  return cn(IDENTIFIER.has(field) && 'font-mono text-xs');
}

/**
 * The badge variant of a gate status, derived from the record's own text by lower-casing it and
 * turning underscores into hyphens: no status is typed here. A status without a variant shows as plain text.
 */
function statusVariant(status: string): 'pass' | 'fail' | 'partial' | 'not-run' | 'not-applicable' | null {
  const variant = status.toLowerCase().replaceAll('_', '-');
  const known = ['pass', 'fail', 'partial', 'not-run', 'not-applicable'] as const;
  return known.find((name) => name === variant) ?? null;
}

/**
 * The records keyed by gate id. A gate listed twice or a gate id outside the schema is refused,
 * so no record is ever dropped from the board.
 */
export function recordsByGate(validation: Validation): ReadonlyMap<GateId, GateRecord> {
  const known: ReadonlySet<string> = new Set(GATE_IDS);
  const byGate = new Map<GateId, GateRecord>();
  for (const record of validation) {
    if (!known.has(record.gate_id)) {
      throw new Error(`GatesBoard: ${JSON.stringify(record.gate_id)} is not a gate id of the GateRecord schema`);
    }
    if (byGate.has(record.gate_id)) {
      throw new Error(`GatesBoard: validation.json lists gate ${record.gate_id} more than once`);
    }
    byGate.set(record.gate_id, record);
  }
  return byGate;
}

/** The reason shown with Unavailable for an input_hashes list with no entries. */
export const NO_INPUTS_REASON = 'validation.json lists no input files for this gate';

function Value({ field, value }: { readonly field: GateField; readonly value: GateRecord[GateField] }) {
  let content: ReactNode = typeof value === 'string' ? value : null;
  if (Array.isArray(value)) {
    content =
      value.length === 0 ? (
        <LabelledValue label="Unavailable" reason={NO_INPUTS_REASON} />
      ) : (
        <ul className="flex flex-col gap-1">
          {value.map((input) => (
            <li key={input.path} className="wrap-anywhere">
              {input.path} {input.sha256}
            </li>
          ))}
        </ul>
      );
  } else if (value === null) content = <LabelledValue label="Unavailable" reason={NULL_REASON} />;
  else if (field === 'status') {
    const variant = statusVariant(value);
    if (variant !== null) content = <Badge variant={variant}>{value}</Badge>;
  }
  return (
    <TableCell data-field={field} className={cellClass(field)}>
      {content}
    </TableCell>
  );
}

const ROW_HEADER = 'px-3 py-2.5 text-left align-top font-mono text-sm font-semibold whitespace-nowrap';

function RecordRow({ record }: { readonly record: GateRecord }) {
  return (
    <TableRow>
      <th scope="row" data-field="gate_id" className={ROW_HEADER}>
        {record.gate_id}
      </th>
      {GATE_FIELDS.filter((field) => field !== 'gate_id').map((field) => (
        <Value key={field} field={field} value={record[field]} />
      ))}
    </TableRow>
  );
}

function AbsentRow({ gateId }: { readonly gateId: GateId }) {
  return (
    <TableRow>
      <th scope="row" data-field="gate_id" className={ROW_HEADER}>
        {gateId}
      </th>
      <TableCell colSpan={GATE_FIELDS.length - 1} className="text-muted-foreground">
        {NO_RECORD}
      </TableCell>
    </TableRow>
  );
}

export interface GatesBoardProps {
  /** The bundle's validation.json, parsed with ValidationSchema. */
  readonly validation: Validation;
}

export function GatesBoard({ validation }: GatesBoardProps) {
  const byGate = recordsByGate(validation);
  return (
    <Table className="min-w-[64rem]">
      <TableCaption>Release gates from the bundle&apos;s validation.json</TableCaption>
      <TableHeader>
        <TableRow>
          {GATE_FIELDS.map((field) => (
            <TableHead key={field} scope="col">
              {field}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {GATE_IDS.map((gateId) => {
          const record = byGate.get(gateId);
          return record === undefined ? (
            <AbsentRow key={gateId} gateId={gateId} />
          ) : (
            <RecordRow key={gateId} record={record} />
          );
        })}
      </TableBody>
    </Table>
  );
}
