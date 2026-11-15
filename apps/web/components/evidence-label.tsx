// The five evidence labels of PRD 3 (also 8.6, 10, D08): every displayed or exported quantity
// carries one. The labels and meanings are copied verbatim from PRD.md section 3, and the tests
// compare them with it. A server component; useId works in synchronous server components.
//
// The label is visible text and its meaning is the badge's accessible description, so neither
// depends on colour. The meaning also stays in the reading order (visually hidden unless shown),
// because screen readers rarely announce a description on non-interactive text. Each label also
// has its own edge style, so a greyscale screenshot still tells them apart: Observed is a solid fill and Derived
// a tinted one, both with a solid edge, Predicted a dashed one, Assumed a dotted one, and
// Unavailable a double one. Colours come from the evidence-* tokens in app/globals.css.
import { useId } from 'react';

import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

export const EVIDENCE_LABELS = ['Observed', 'Derived', 'Predicted', 'Assumed', 'Unavailable'] as const;

export type EvidenceLabelName = (typeof EVIDENCE_LABELS)[number];

/** PRD 3, "Evidence labels", word for word. */
export const EVIDENCE_MEANINGS: Readonly<Record<EvidenceLabelName, string>> = {
  Observed: 'Supplied by a qualified instrument product',
  Derived: 'Computed from named observations by a documented transformation',
  Predicted: 'Estimated by a fitted statistical model',
  Assumed: 'Scenario input, tolerance or unverified physical simplification',
  Unavailable: 'Observation or supported inference is absent',
};

/** Whether a value from untyped input, such as bundle JSON, is one of the five labels. */
export function isEvidenceLabel(value: unknown): value is EvidenceLabelName {
  return (EVIDENCE_LABELS as readonly unknown[]).includes(value);
}

const BADGE_VARIANT = {
  Observed: 'observed',
  Derived: 'derived',
  Predicted: 'predicted',
  Assumed: 'assumed',
  Unavailable: 'unavailable',
} as const satisfies Record<EvidenceLabelName, string>;

// A fixed height that fits the thicker edges, merged over the badge's own classes.
const SHAPE = 'h-6 border-2 py-0 font-semibold';

// Each variant sets its own edge style (solid, solid, dashed, dotted, double) and colour; the
// double edge needs a wider line to read as two lines.
const TREATMENT = {
  Observed: '',
  Derived: '',
  Predicted: '',
  Assumed: '',
  Unavailable: 'border-3',
} as const satisfies Record<EvidenceLabelName, string>;

export interface EvidenceLabelProps {
  readonly label: EvidenceLabelName;
  /** Show the meaning as text beside the label, as the legend does; otherwise only screen readers read it. */
  readonly showMeaning?: boolean;
}

export function EvidenceLabel({ label, showMeaning = false }: EvidenceLabelProps) {
  const meaningId = useId();
  if (!isEvidenceLabel(label)) {
    throw new Error(`EvidenceLabel: ${JSON.stringify(label)} is not a PRD 3 evidence label`);
  }
  return (
    <span data-slot="evidence-label" data-label={label} className="inline-flex flex-wrap items-center gap-x-2">
      <Badge
        variant={BADGE_VARIANT[label]}
        aria-describedby={meaningId}
        className={cn(SHAPE, TREATMENT[label])}
      >
        {label}
      </Badge>{' '}
      <span
        data-slot="evidence-meaning"
        id={meaningId}
        className={showMeaning ? 'text-sm text-muted-foreground' : 'sr-only'}
      >
        {EVIDENCE_MEANINGS[label]}
      </span>
    </span>
  );
}
