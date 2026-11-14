// A quantity shown with its evidence label, unit and source (PRD 3, 8.6, 10, D08). The value is
// rendered only for Observed, Derived, Predicted and Assumed. Missing evidence is labelled
// Unavailable with the reason it is absent, and is never shown as zero, a blank or a dash. The
// types make a value label without a value a compile error; the run-time checks below catch the
// same mistakes in data that arrives untyped, and throw so that next build fails.
import { EvidenceLabel, type EvidenceLabelName, isEvidenceLabel } from '@/components/evidence-label';

type ValueLabel = Exclude<EvidenceLabelName, 'Unavailable'>;

export interface PresentValueProps {
  readonly label: ValueLabel;
  /** The value as the bundle gives it; a number is printed exactly as String() writes it. */
  readonly value: string | number;
  /** The unit, or null for a quantity that has none. */
  readonly unit: string | null;
  /** The product, observations, model or scenario the value comes from. */
  readonly source: string;
  readonly reason?: never;
}

export interface UnavailableValueProps {
  readonly label: 'Unavailable';
  /** Why the observation or supported inference is absent. */
  readonly reason: string;
  /** The product or record that was checked, when there is one. */
  readonly source?: string;
  readonly value?: never;
  readonly unit?: never;
}

export type LabelledValueProps = PresentValueProps | UnavailableValueProps;

// Text that only stands in for a missing value: blank, dashes or a minus sign, a question mark,
// or a word for absence, including what String() makes of a missing or non-finite number.
const PLACEHOLDER = /^\s*([-\u2010-\u2015\u2212]*|\?|n\/?a|nan|null|undefined|none|unknown)\s*$/iu;

function isText(value: unknown): value is string {
  return typeof value === 'string' && value.trim() !== '';
}

function refuse(message: string): never {
  throw new Error(`LabelledValue: ${message}`);
}

function valueText(label: string, value: unknown): string {
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      refuse(`${label} value ${String(value)} is not a number to show; use label Unavailable with a reason`);
    }
    return String(value);
  }
  if (typeof value !== 'string' || PLACEHOLDER.test(value)) {
    refuse(`${label} needs a value; missing evidence takes label Unavailable with a reason`);
  }
  return value;
}

function Source({ source }: { readonly source: string }) {
  return <span data-slot="source">Source: {source}</span>;
}

type UntypedProps = { readonly [K in 'label' | 'value' | 'unit' | 'source' | 'reason']?: unknown };

export function LabelledValue(props: LabelledValueProps) {
  // Read as untyped input: the same component renders records parsed from bundle JSON.
  const { label, value, unit, source, reason }: UntypedProps = props;
  if (!isEvidenceLabel(label)) refuse(`${JSON.stringify(label)} is not a PRD 3 evidence label`);

  if (label === 'Unavailable') {
    if (!isText(reason)) refuse('Unavailable needs a reason that says why the evidence is absent');
    if (value !== undefined && value !== null) refuse('Unavailable carries no value');
    if (unit !== undefined && unit !== null) refuse('Unavailable carries no unit');
    const named = source === undefined ? null : isText(source) ? source : refuse('a source must not be blank');
    return (
      <span data-slot="labelled-value" data-label={label} className="inline-flex flex-wrap items-baseline gap-x-2">
        <EvidenceLabel label={label} /> <span data-slot="reason">{reason}</span>
        {named === null ? null : (
          <>
            {' '}
            <Source source={named} />
          </>
        )}
      </span>
    );
  }

  const text = valueText(label, value);
  const unitText =
    unit === null ? null : isText(unit) ? unit : refuse(`${label} needs a unit, or null for a quantity without one`);
  if (!isText(source)) refuse(`${label} needs a source`);
  return (
    <span data-slot="labelled-value" data-label={label} className="inline-flex flex-wrap items-baseline gap-x-2">
      <EvidenceLabel label={label} /> <span data-slot="value">{text}</span>
      {unitText === null ? null : (
        <>
          {' '}
          <span data-slot="unit">{unitText}</span>
        </>
      )}{' '}
      <Source source={source} />
    </span>
  );
}
