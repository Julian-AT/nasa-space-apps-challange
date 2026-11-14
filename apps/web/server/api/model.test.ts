// GET /api/v1/model (PRD 8.5, 11.9, T15-05) and the model-family guard. The model report needs
// model/model_card.json and report/*.json, which have no @terraluna/schema schema and are never
// pulled, so /model answers 404 when manifest.json names no model and 501 when it names one.
// modelFamilyAccess is the one gate for model-family data: refused unless a model is in the bundle
// and G5 is PASS in both manifest.json and validation.json, and labelled Predicted when allowed.
import type { BundleManifest, Validation } from '@terraluna/schema';
import { describe, expect, it, vi } from 'vitest';

import type { PulledBundle } from '../../lib/bundle.ts';
import { ALL_GATES_NOT_RUN, gate } from '../../tests/helpers.ts';
import { modelFamilyAccess, PREDICTED_LABEL } from './model.ts';
import {
  expectProblem,
  mockFile,
  mockReader,
  quietApi,
  RELEASE_VERSION,
  releaseBundleReader,
} from './test-support.ts';

const MODEL = '/api/v1/model';

/** A model entry for these tests only, not a model of any bundle. */
const TEST_MODEL_ID = 'test-model-alpha';

const GATE_STATUSES = ['NOT_RUN', 'PASS', 'FAIL', 'PARTIAL', 'NOT_APPLICABLE'] as const;

function testModel(g5: string) {
  return { id: TEST_MODEL_ID, status: 'EXPERIMENTAL', g5 };
}

function gatesWithG5(...statuses: string[]) {
  return [
    ...ALL_GATES_NOT_RUN.filter((record) => record.gate_id !== 'G5'),
    ...statuses.map((status) => gate('G5', status)),
  ];
}

describe('GET /api/v1/model', () => {
  it('is a 404 problem on the mock bundle, which names no model', async () => {
    const manifest = mockFile('manifest.json') as Record<string, unknown>;
    expect(manifest.model).toBeNull();
    const { api, log } = quietApi();
    const problem = await expectProblem(await api.request(MODEL), 404, 'Not Found');
    expect(problem.detail).toBe('The model report is not in bundle mock-0: manifest.json names no model.');
    expect(problem.instance).toBe(MODEL);
    expect(log).not.toHaveBeenCalled();
  });

  it('is a 404 problem on a release bundle that names no model', async () => {
    const { api } = quietApi(releaseBundleReader({ gates: gatesWithG5('PASS') }));
    const problem = await expectProblem(await api.request(MODEL), 404, 'Not Found');
    expect(problem.detail).toContain(`not in bundle ${RELEASE_VERSION}`);
  });

  for (const g5 of GATE_STATUSES) {
    it(`is a 501 problem when manifest.json names a model (G5 ${g5}): no ModelRecord schema`, async () => {
      const reader = releaseBundleReader({ model: testModel(g5), gates: gatesWithG5(g5) });
      const { api, log } = quietApi(reader);
      const problem = await expectProblem(await api.request(MODEL), 501, 'Not Implemented');
      expect(problem.detail).toContain(`Model ${TEST_MODEL_ID} is in bundle ${RELEASE_VERSION}`);
      expect(problem.detail).toContain('not served yet');
      expect(problem.detail).toContain('@terraluna/schema has no ModelRecord schema');
      expect(log).not.toHaveBeenCalled();
    });
  }

  it('answers HEAD with the problem status and no body', async () => {
    const { api } = quietApi();
    const head = await api.request(MODEL, { method: 'HEAD' });
    expect(head.status).toBe(404);
    expect(await head.text()).toBe('');
  });

  it('refuses a manifest that fails its schema with a 500 problem', async () => {
    const bundle = mockReader();
    const corrupt = { ...bundle, manifest: { ...bundle.manifest, model: { id: TEST_MODEL_ID } } };
    const { api, log } = quietApi(() => corrupt as unknown as PulledBundle);
    const problem = await expectProblem(await api.request(MODEL), 500, 'Internal Server Error');
    expect(problem.detail).toContain('manifest.json');
    expect(log).toHaveBeenCalledOnce();
  });

  it('reads the bundle on each request', async () => {
    const reader = vi.fn(mockReader);
    const { api } = quietApi(reader);
    await api.request(MODEL);
    await api.request(MODEL);
    expect(reader).toHaveBeenCalledTimes(2);
  });
});

describe('modelFamilyAccess', () => {
  function manifestWith(model: BundleManifest['model']): BundleManifest {
    return { ...mockReader().manifest, model };
  }

  function validationWith(...statuses: string[]): Validation {
    return gatesWithG5(...statuses) as Validation;
  }

  it('labels model-family data Predicted, as PRD 3 and 11.7 require', () => {
    expect(PREDICTED_LABEL).toBe('Predicted');
  });

  it('refuses when manifest.json names no model, whatever validation.json says', () => {
    for (const status of GATE_STATUSES) {
      const access = modelFamilyAccess(manifestWith(null), validationWith(status), 'b-1');
      expect(access).toEqual({
        allowed: false,
        detail:
          'Model-family matches are not in bundle b-1: they exist only when a model is in the ' +
          'bundle, and manifest.json names none.',
      });
    }
  });

  it('allows only G5 PASS in both files, and then gives the Predicted label', () => {
    for (const inManifest of GATE_STATUSES) {
      for (const inValidation of GATE_STATUSES) {
        const access = modelFamilyAccess(
          manifestWith(testModel(inManifest) as BundleManifest['model']),
          validationWith(inValidation),
          'b-1',
        );
        const pass = inManifest === 'PASS' && inValidation === 'PASS';
        expect(access.allowed, `${inManifest}/${inValidation}`).toBe(pass);
        if (access.allowed) {
          expect(access).toEqual({ allowed: true, label: 'Predicted', modelId: TEST_MODEL_ID });
        } else {
          expect(access.detail).toContain('only when G5 is PASS');
          expect(access.detail).toContain(`manifest.json gives G5 as ${inManifest}`);
          expect(access.detail).toContain(`validation.json gives G5 as ${inValidation}`);
          expect(access).not.toHaveProperty('label');
        }
      }
    }
  });

  it('refuses when validation.json has no G5 record, since an untested gate is never a pass', () => {
    const access = modelFamilyAccess(
      manifestWith(testModel('PASS') as BundleManifest['model']),
      validationWith(),
      'b-1',
    );
    expect(access.allowed).toBe(false);
  });

  it('refuses when validation.json records G5 more than once with different statuses', () => {
    const access = modelFamilyAccess(
      manifestWith(testModel('PASS') as BundleManifest['model']),
      validationWith('PASS', 'FAIL'),
      'b-1',
    );
    expect(access.allowed).toBe(false);
  });

  it('allows G5 recorded twice when both records are PASS', () => {
    const access = modelFamilyAccess(
      manifestWith(testModel('PASS') as BundleManifest['model']),
      validationWith('PASS', 'PASS'),
      'b-1',
    );
    expect(access.allowed).toBe(true);
  });
});
