// /pitch-preview/training: renders the pitch deck's training figure alone, centred on the deck
// background, for screenshots. Data is report/curves.json from the release bundle.
import type { Metadata } from 'next';

import { FigTraining } from '@/components/pitch/fig-training';
import { LabelledValue } from '@/components/labelled-value';
import { loadBundle } from '@/lib/bundle';
import { SITE_TITLE } from '@/lib/shell';

export const dynamic = 'error';

export const metadata: Metadata = { title: `Training figure preview · ${SITE_TITLE}`, robots: { index: false } };

export default function TrainingFigurePreview() {
  const { curves } = loadBundle().records;
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 60, background: '#F9F8F5', display: 'grid', placeItems: 'center', padding: 48, overflow: 'auto' }}>
      <div style={{ width: '100%', maxWidth: 1200 }}>
        {curves === null ? (
          <LabelledValue label="Unavailable" reason="report/curves.json is not in this bundle" source="report/curves.json" />
        ) : (
          <FigTraining curves={curves} />
        )}
      </div>
    </div>
  );
}
