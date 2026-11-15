// /model (PRD 11.9): Unavailable state, literal G5 status, named failed tests, no ranking claim.
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { ModelReport } from '../components/model/model-report.tsx';
import { gate } from './helpers.ts';
import { metricsRecord, modelRecord } from './explore-compare-records.ts';
import { elements, textOf } from './markup.ts';
import { GateRecordSchema } from '@terraluna/schema';

const g5 = (status: string) => GateRecordSchema.parse(gate('G5', status));

describe('/model', () => {
  it('is an honest Unavailable state naming the files, with the G5 status', () => {
    const out = renderToStaticMarkup(<ModelReport modelCard={null} metrics={null} g5={g5('NOT_RUN')} />);
    const text = textOf(out);
    expect(text).toContain('model/model_card.json');
    expect(text).toContain('report/metrics.json');
    expect(text).toContain('EXPERIMENTAL');
    expect(text).toContain('NOT_RUN');
    expect(text).toContain('outside every ranking');
    expect(elements(out).filter((e) => e.tag === 'table')).toHaveLength(0);
    expect(text).not.toMatch(/\bR²|top-5|ranked/i);
  });

  it('names the failed tests and every condition field as serialized', () => {
    const metrics = metricsRecord({ failed: ['physics_probe', 'seed_stability'], status: 'FAIL' });
    const out = renderToStaticMarkup(<ModelReport modelCard={modelRecord()} metrics={metrics} g5={g5('FAIL')} />);
    const text = textOf(out);
    expect(text).toMatch(/Gate G5 status:\s*FAIL/);
    expect(elements(out).filter((e) => e.tag === 'code').map((e) => e.text)).toEqual(expect.arrayContaining(['physics_probe', 'seed_stability']));
    expect(text).toMatch(/Failed tests:\s*physics_probe\s*,\s*seed_stability/);
    expect(text).toContain('fake below threshold');
    expect(text).toContain('0.5');
    expect(text).toContain('0.25');
    expect(text).toContain('outside every ranking');
  });

  it('shows metrics with intervals as Predicted and null values as Unavailable with the reason', () => {
    const out = renderToStaticMarkup(
      <ModelReport modelCard={modelRecord()} metrics={metricsRecord({ failed: [], status: 'FAIL' })} g5={g5('FAIL')} />,
    );
    const text = textOf(out);
    expect(text).toContain('[0.2, 0.4]');
    expect(text).toContain('Predicted');
    expect(text).toContain('fake_one_seed_only');
  });

  it('renders the model card fields', () => {
    const out = renderToStaticMarkup(<ModelReport modelCard={modelRecord()} metrics={null} g5={g5('FAIL')} />);
    const text = textOf(out);
    for (const s of ['fake-model-id', 'fake-0', 'fake-arch', '12345', 'fake intended use', 'fake limitations', 'fake-licence']) {
      expect(text).toContain(s);
    }
  });

  it('says so when the bundle has no G5 record', () => {
    expect(textOf(renderToStaticMarkup(<ModelReport modelCard={null} metrics={null} g5={null} />))).toContain('no G5 GateRecord');
  });

  it('has one h1', () => {
    const out = renderToStaticMarkup(<ModelReport modelCard={null} metrics={null} g5={null} />);
    expect(elements(out).filter((e) => e.tag === 'h1')).toHaveLength(1);
  });
});
