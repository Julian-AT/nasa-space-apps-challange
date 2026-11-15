// The validation board (PRD 8.4, 8.6, 8.12, 9, 10 "Validation board"): the GateRecords of the
// bundle's validation.json as one table. Columns are the GateRecord fields of @terraluna/schema,
// in schema order, headed by their field names; rows are the schema's gate ids in order. Every
// value is the record's own text: a status is shown as its literal value, in the same style for
// every status, so nothing depends on colour. Every status and result comes from the records.
// A null field is labelled Unavailable with the reason; a gate with no record says so and is
// never shown with a status. A server component.
import { type GateRecord, GateRecordSchema, type Validation } from '@terraluna/schema';

import { LabelledValue } from '@/components/labelled-value';

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
const IDENTIFIER: ReadonlySet<GateField> = new Set(['gate_id', 'status', 'evidence_path', 'run_id']);

function cellClass(field: GateField): string {
  return `border border-border px-2 py-1 text-left align-top wrap-anywhere${IDENTIFIER.has(field) ? ' font-mono' : ''}`;
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

function Value({ field, value }: { readonly field: GateField; readonly value: string | null }) {
  return (
    <td data-field={field} className={cellClass(field)}>
      {value === null ? <LabelledValue label="Unavailable" reason={NULL_REASON} /> : value}
    </td>
  );
}

function RecordRow({ record }: { readonly record: GateRecord }) {
  return (
    <tr>
      <th scope="row" data-field="gate_id" className={cellClass('gate_id')}>
        {record.gate_id}
      </th>
      {GATE_FIELDS.filter((field) => field !== 'gate_id').map((field) => (
        <Value key={field} field={field} value={record[field]} />
      ))}
    </tr>
  );
}

function AbsentRow({ gateId }: { readonly gateId: GateId }) {
  return (
    <tr>
      <th scope="row" data-field="gate_id" className={cellClass('gate_id')}>
        {gateId}
      </th>
      <td colSpan={GATE_FIELDS.length - 1} className={cellClass('consequence')}>
        {NO_RECORD}
      </td>
    </tr>
  );
}

export interface GatesBoardProps {
  /** The bundle's validation.json, parsed with ValidationSchema. */
  readonly validation: Validation;
}

export function GatesBoard({ validation }: GatesBoardProps) {
  const byGate = recordsByGate(validation);
  return (
    <table className="w-full table-fixed border-collapse text-sm">
      <caption className="mb-2 text-left">Release gates from the bundle&apos;s validation.json</caption>
      <thead>
        <tr>
          {GATE_FIELDS.map((field) => (
            <th key={field} scope="col" className={`${cellClass(field)} bg-muted font-semibold`}>
              {field}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {GATE_IDS.map((gateId) => {
          const record = byGate.get(gateId);
          return record === undefined ? (
            <AbsentRow key={gateId} gateId={gateId} />
          ) : (
            <RecordRow key={gateId} record={record} />
          );
        })}
      </tbody>
    </table>
  );
}
