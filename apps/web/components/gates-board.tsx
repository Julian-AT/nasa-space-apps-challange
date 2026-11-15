// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The validation board (PRD 8.4, 8.6, 8.12, 9, 10 "Validation board"): the GateRecords of the
// bundle's validation.json as one panel per gate, G0 to G6 in schema order. Each panel shows the
// gate id, the status as a badge whose text is the status itself (so nothing depends on colour),
// and the scope, result and consequence; the provenance fields (evidence_path, run_id, command,
// config_version, input_hashes) sit in a per-gate "Provenance" disclosure. Every GateRecord field
// is rendered, in an element carrying its field name as data-field. A null field is labelled
// Unavailable with the reason; a gate with no record says so and is never shown with a status.
// Every status and result comes from the records. A server component.
import { type GateRecord, GateRecordSchema, type Validation } from '@terraluna/schema';

import { LabelledValue } from '@/components/labelled-value';
import { Badge } from '@/components/ui/badge';
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

/** The fields shown in the panel body, and those folded into the Provenance disclosure. */
const SUMMARY_FIELDS = ['scope', 'result', 'consequence'] as const satisfies readonly GateField[];
const PROVENANCE_FIELDS = [
  'evidence_path',
  'run_id',
  'command',
  'config_version',
  'input_hashes',
] as const satisfies readonly GateField[];

/** How many hex characters of an input hash the compact list shows; the full hash is its title. */
const SHORT_HASH = 12;

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

function FieldValue({ field, value }: { readonly field: GateField; readonly value: GateRecord[GateField] }) {
  if (value === null) return <LabelledValue label="Unavailable" reason={NULL_REASON} />;
  if (Array.isArray(value)) {
    if (value.length === 0) return <LabelledValue label="Unavailable" reason={NO_INPUTS_REASON} />;
    return (
      <ul className="flex flex-col gap-1">
        {value.map((input) => (
          <li key={input.path} className="font-mono text-xs break-all">
            {input.path} <span title={input.sha256}>{input.sha256.slice(0, SHORT_HASH)}</span>
          </li>
        ))}
      </ul>
    );
  }
  if (field === 'status') {
    const variant = statusVariant(value);
    if (variant !== null) return <Badge variant={variant}>{value}</Badge>;
  }
  if (value.trimStart().startsWith('{')) {
    // A machine-readable record (e.g. G1's test summary): collapsed, the full text stays in the page.
    return (
      <details>
        <summary className="cursor-pointer text-muted-foreground">Machine-readable record (JSON), expand</summary>
        <pre className="mt-2 max-h-80 overflow-auto font-mono text-xs break-all whitespace-pre-wrap">{value}</pre>
      </details>
    );
  }
  return <>{value}</>;
}

const LABEL = 'font-mono text-[11px] tracking-[0.18em] text-chrome uppercase';

function Field({ record, field }: { readonly record: GateRecord; readonly field: GateField }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <dt className={LABEL}>{field}</dt>
      <dd data-field={field} className={cn('m-0 min-w-0 text-sm break-words', IDENTIFIER.has(field) && 'font-mono text-xs break-all')}>
        <FieldValue field={field} value={record[field]} />
      </dd>
    </div>
  );
}

// Identifiers are set in a monospace face; free text is not.
const IDENTIFIER: ReadonlySet<GateField> = new Set(['evidence_path', 'run_id', 'command', 'config_version']);

function GatePanel({ gateId, record }: { readonly gateId: GateId; readonly record: GateRecord | undefined }) {
  return (
    <li data-gate={gateId} className="surface rounded-3xl p-5 sm:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <h3 data-field="gate_id" className="font-mono text-lg">
          {gateId}
        </h3>
        {record === undefined ? null : (
          <span data-field="status">
            <FieldValue field="status" value={record.status} />
          </span>
        )}
      </div>
      {record === undefined ? (
        <p className="mt-3 text-sm text-muted-foreground">{NO_RECORD}</p>
      ) : (
        <>
          <dl className="mt-4 grid gap-4 md:grid-cols-3">
            {SUMMARY_FIELDS.map((field) => (
              <Field key={field} record={record} field={field} />
            ))}
          </dl>
          <details className="mt-4 rounded-2xl border border-line bg-ink/60 px-4 py-3">
            <summary className="cursor-pointer text-sm font-medium">Provenance</summary>
            <dl className="mt-3 grid gap-4 md:grid-cols-2">
              {PROVENANCE_FIELDS.map((field) => (
                <Field key={field} record={record} field={field} />
              ))}
            </dl>
          </details>
        </>
      )}
    </li>
  );
}

export interface GatesBoardProps {
  /** The bundle's validation.json, parsed with ValidationSchema. */
  readonly validation: Validation;
}

export function GatesBoard({ validation }: GatesBoardProps) {
  const byGate = recordsByGate(validation);
  return (
    <ol aria-label="Release gates from the bundle's validation.json" className="flex flex-col gap-4">
      {GATE_IDS.map((gateId) => (
        <GatePanel key={gateId} gateId={gateId} record={byGate.get(gateId)} />
      ))}
    </ol>
  );
}
