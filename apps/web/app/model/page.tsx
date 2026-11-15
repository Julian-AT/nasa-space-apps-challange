// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// /model (PRD 8.6, 11.9): the model card, metrics and G5 verdict from the release bundle,
// statically rendered. Without model/model_card.json and report/metrics.json it is an honest
// Unavailable state.
import type { Metadata } from 'next';

import { ModelReport } from '../../components/model/model-report.tsx';
import { readModelFacts } from '../../components/home/model-facts.ts';
import { loadBundle } from '../../lib/bundle.ts';
import { SITE_TITLE } from '../../lib/shell.ts';

export const dynamic = 'error';

export const metadata: Metadata = { title: `Model · ${SITE_TITLE}` };

export default function ModelPage() {
  const bundle = loadBundle();
  const { validation, records } = bundle;
  const g5 = validation.find((gate) => gate.gate_id === 'G5') ?? null;
  return <ModelReport modelCard={records.modelCard} metrics={records.metrics} g5={g5} curves={records.curves} examples={records.examples} facts={readModelFacts(bundle)} />;
}
