// The legend of the five evidence labels with their PRD 3 meanings (PRD 3, 8.6, 10, D08), shown
// as visible text. A server component. The heading and sentence are PRD 3's own words.
import { useId } from 'react';

import { EVIDENCE_LABELS, EvidenceLabel } from '@/components/evidence-label';

/** PRD 3, "Evidence labels", word for word. */
export const EVIDENCE_LEGEND_SENTENCE = 'Every displayed or exported quantity carries one of these labels.';

export interface EvidenceLegendProps {
  /** The heading level that fits the page outline where the legend is placed. */
  readonly headingLevel?: 2 | 3 | 4;
}

export function EvidenceLegend({ headingLevel = 2 }: EvidenceLegendProps) {
  const headingId = useId();
  const Heading = `h${String(headingLevel)}` as 'h2' | 'h3' | 'h4';
  return (
    <section data-slot="evidence-legend" aria-labelledby={headingId} className="flex flex-col gap-2">
      <Heading id={headingId}>Evidence labels</Heading>
      <p>{EVIDENCE_LEGEND_SENTENCE}</p>
      <ul className="flex flex-col gap-2">
        {EVIDENCE_LABELS.map((label) => (
          <li key={label}>
            <EvidenceLabel label={label} showMeaning />
          </li>
        ))}
      </ul>
    </section>
  );
}
