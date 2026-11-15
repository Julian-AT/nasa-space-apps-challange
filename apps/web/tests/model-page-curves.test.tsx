// /model curves and examples (PRD 11.9): SVG paths follow the record arrays, Unavailable lines name
// the files, encoder neighbours stay Predicted and beside the deterministic list. Synthetic records.
import { CurvesSchema, ExamplesSchema } from '@terraluna/schema';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { curveGeometry } from '../components/model/loss-curve.tsx';
import { ModelReport } from '../components/model/model-report.tsx';
import { elements, textOf } from './markup.ts';

const epochs = [
  { epoch: 0, mean: 4, min: 3, max: 5, n_seeds: 2 },
  { epoch: 1, mean: 2, min: 1, max: 3, n_seeds: 2 },
  { epoch: 2, mean: 1, min: 0.5, max: 1.5, n_seeds: 2 },
];

const curves = CurvesSchema.parse({
  mock: false,
  run_id: null,
  loss_terms: [{ term: 'fake_term', epochs }],
  gpu_hours: { runs: [{ run_name: 'fake-run', hours: null, reason: 'fake_not_logged', source: null }], total_hours: null, partial: true },
  tradeoff: [
    {
      lambda_max: 0.5,
      status: 'unavailable',
      reason: 'fake_not_trained',
      body_probe_balanced_accuracy: { value: null, ci_low: null, ci_high: null },
      physics_r2: { value: null, ci_low: null, ci_high: null },
    },
  ],
  chance_level_body_probe: 0.33,
  validation_probe_metrics: { status: 'unavailable', reason: 'fake_no_probe', metrics: [] },
});

const examples = ExamplesSchema.parse({
  mock: false,
  run_id: null,
  retrieval_gallery: [
    {
      target_id: 'fake-target',
      encoder_top5: [{ window_id: 'earth-fake-enc', body: 'earth', rank: 1, distance: 0.1, distance_family: 'model_embedding', evidence_label: 'Predicted' }],
      deterministic_top5: [{ window_id: 'earth-fake-det', body: 'earth', rank: 1, distance: 0.2, distance_family: 'terrain_relief', evidence_label: 'Derived' }],
      shortfall_reason: null,
    },
  ],
  failure_cases: { sort_rule: 'fake_rule', entries: [], shortfall_reason: 'fake_none_found' },
  ood_examples: [],
  hillshades: [],
});

describe('/model curves and examples', () => {
  it('draws the mean path from the record arrays', () => {
    const g = curveGeometry(epochs);
    expect(g.mean).toHaveLength(epochs.length);
    // Strictly decreasing mean means strictly increasing screen y.
    const ys = g.mean.map((p) => p[1]);
    expect(ys).toEqual([...ys].sort((a, b) => a - b));
    expect(new Set(ys).size).toBe(3);
    expect(g.mean.map((p) => p[0])).toEqual([...g.mean.map((p) => p[0])].sort((a, b) => a - b));
  });

  it('names the missing files when absent', () => {
    const text = textOf(renderToStaticMarkup(<ModelReport modelCard={null} metrics={null} g5={null} />));
    expect(text).toContain('report/curves.json');
    expect(text).toContain('report/examples.json');
  });

  it('renders accessible SVG with a table, GPU hours reason and trade-off reason', () => {
    const out = renderToStaticMarkup(<ModelReport modelCard={null} metrics={null} g5={null} curves={curves} examples={examples} />);
    const svg = elements(out).find((e) => e.tag === 'svg');
    expect(svg?.attributes.get('aria-label')).toContain('fake_term');
    expect(elements(out).some((e) => e.tag === 'table' && e.text.includes('Epoch'))).toBe(true);
    const text = textOf(out);
    expect(text).toContain('fake_not_logged');
    expect(text).toContain('partial');
    expect(text).toContain('fake_not_trained');
    expect(text).toContain('fake_no_probe');
  });

  it('shows encoder neighbours as Predicted and EXPERIMENTAL beside the deterministic list', () => {
    const out = renderToStaticMarkup(<ModelReport modelCard={null} metrics={null} g5={null} curves={null} examples={examples} />);
    const text = textOf(out);
    expect(text).toContain('Encoder top 5 (EXPERIMENTAL)');
    expect(text).toContain('earth-fake-enc');
    expect(text).toContain('earth-fake-det');
    expect(text).toContain('fake_none_found');
  });
});
