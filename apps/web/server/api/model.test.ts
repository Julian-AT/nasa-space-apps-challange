// GET /api/v1/model (PRD 8.5, 11.9, T15B-05) and modelFamilyAccess.
import { describe, expect, it } from 'vitest';

import { metricsFile, memoryReader, modelRecord } from './test-records.ts';
import { expectProblem, quietApi } from './test-support.ts';
import { type ModelResponse, modelFamilyAccess, PREDICTED_LABEL } from './model.ts';
import { mockReader } from './test-support.ts';

const get = (r: ReturnType<typeof memoryReader>) => quietApi(r).api.request('/api/v1/model');

describe('GET /api/v1/model', () => {
  it('is a 404 problem when the bundle has no model card', async () => {
    await expectProblem(await get(mockReader), 404, 'Not Found');
  });

  it('returns the card, g5 and the G5 status as EXPERIMENTAL and outside rankings', async () => {
    const r = memoryReader({ modelCard: modelRecord(), metrics: metricsFile('NOT_RUN'), g5: 'NOT_RUN' });
    const response = await get(r);
    expect(response.status).toBe(200);
    const body = (await response.json()) as ModelResponse;
    expect(body).toMatchObject({
      evidence_label: 'Predicted',
      status: 'EXPERIMENTAL',
      in_rankings: false,
      g5_status: 'NOT_RUN',
    });
    expect(body.model.model_id).toBe('test-model-alpha');
    expect(body.g5?.status).toBe('NOT_RUN');
  });

  it('invents no metric: g5 is null without report/metrics.json, and the G5 status null without a record', async () => {
    const body = (await (await get(memoryReader({ modelCard: modelRecord(), g5: null }))).json()) as ModelResponse;
    expect(body.g5).toBeNull();
    expect(body.g5_status).toBeNull();
    expect(body.in_rankings).toBe(false);
  });

  it('is in rankings only when the validation.json G5 status is PASS', async () => {
    const body = (await (await get(memoryReader({ modelCard: modelRecord('PASS'), g5: 'PASS' }))).json()) as ModelResponse;
    expect(body.in_rankings).toBe(true);
    expect(body.status).toBe('EXPERIMENTAL');
  });
});

describe('modelFamilyAccess', () => {
  const bundle = (g5: string | null, manifestG5: string, model = true) => {
    const b = memoryReader({
      g5,
      manifest: { model: model ? { id: 'test-model-alpha', status: 'EXPERIMENTAL', g5: manifestG5 } : null },
    })();
    return modelFamilyAccess(b.manifest, b.validation, b.manifest.bundle_version);
  };

  it('is allowed and Predicted only with a model and G5 PASS in both files', () => {
    expect(bundle('PASS', 'PASS')).toMatchObject({ allowed: true, label: PREDICTED_LABEL });
    expect(bundle('PASS', 'FAIL').allowed).toBe(false);
    expect(bundle('FAIL', 'PASS').allowed).toBe(false);
    expect(bundle(null, 'PASS').allowed).toBe(false);
    expect(bundle('PASS', 'PASS', false).allowed).toBe(false);
  });
});
