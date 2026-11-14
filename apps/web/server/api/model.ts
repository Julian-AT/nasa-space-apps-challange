// GET /api/v1/model (PRD 8.5, 11.9) and the guard for model-family data (PRD 3, 11.7, 11.10).
//
// /model would return the model card, summary metrics and the G5 status. They come from
// model/model_card.json and report/*.json, which bundle:pull never copies and which have no
// @terraluna/schema schema (no ModelRecord). By the rule in ./unavailable.ts the route answers
// 404 when manifest.json names no model, as the mock bundle does, and 501 when it names one.
//
// modelFamilyAccess is the one gate for model-family data, such as encoder distances in matches.
// The encoder becomes a distance family only when G5 passes (PRD 11.7), so access is refused unless
// manifest.json names a model and G5 is PASS in both manifest.json (model.g5) and validation.json,
// which must hold exactly one G5 record: a missing record is an untested gate, never a pass
// (PRD 8.12), and two records leave the verdict unclear (the loader already refuses them).
// When access is allowed it carries the Predicted label, and any response that returns
// model-family data must take its label from here (PRD 3: encoder output is always Predicted).
import { createRoute, type RouteHandler } from '@hono/zod-openapi';
import type { BundleManifest, Validation } from '@terraluna/schema';

import type { PulledBundle } from '../../lib/bundle.ts';
import { notInBundle, notServedYet, readManifest, unavailableResponses } from './unavailable.ts';

/** The evidence label of every model-family value (PRD 3). */
export const PREDICTED_LABEL = 'Predicted';

export type ModelFamilyAccess =
  | { readonly allowed: false; readonly detail: string }
  | { readonly allowed: true; readonly label: typeof PREDICTED_LABEL; readonly modelId: string };

/** What validation.json records for G5, for the detail. */
function validationG5(statuses: readonly string[]): string {
  if (statuses.length === 0) return 'validation.json has no G5 record';
  if (statuses.length === 1) return `validation.json gives G5 as ${statuses.join('')}`;
  return `validation.json records G5 ${String(statuses.length)} times (${statuses.join(', ')})`;
}

/** Whether model-family data may be served from this bundle, as the module comment says. */
export function modelFamilyAccess(
  manifest: BundleManifest,
  validation: Validation,
  bundleVersion: string,
): ModelFamilyAccess {
  const notInThisBundle = `Model-family matches are not in bundle ${bundleVersion}`;
  const { model } = manifest;
  if (model === null) {
    return {
      allowed: false,
      detail: `${notInThisBundle}: they exist only when a model is in the bundle, and manifest.json names none.`,
    };
  }
  const g5 = validation.filter((r) => r.gate_id === 'G5').map((r) => r.status);
  const passed = model.g5 === 'PASS' && g5.length === 1 && g5[0] === 'PASS';
  if (!passed) {
    return {
      allowed: false,
      detail:
        `${notInThisBundle}: model ${model.id} is ${model.status}, and its distances are ` +
        `${PREDICTED_LABEL} values that enter rankings only when G5 is PASS ` +
        `(manifest.json gives G5 as ${model.g5}; ${validationG5(g5)}).`,
    };
  }
  return { allowed: true, label: PREDICTED_LABEL, modelId: model.id };
}

export const modelRoute = createRoute({
  method: 'get',
  path: '/model',
  summary: 'The model report (not served yet)',
  description:
    'The model card, summary metrics and G5 status are not served yet: @terraluna/schema has no ' +
    'ModelRecord schema, and model/model_card.json and report/*.json are not pulled. When ' +
    'manifest.json names no model, as the mock bundle does, the answer is a 404 problem; when it ' +
    'names one, a 501 problem. Problems are never cached.',
  responses: unavailableResponses({
    notInBundle: 'manifest.json names no model: the model report is not in the bundle.',
    notServed: 'manifest.json names a model, but the model report is not served yet.',
  }),
});

/** The model handler over a bundle reader: 404 or 501, as the module comment says. */
export function modelHandler(read: () => PulledBundle): RouteHandler<typeof modelRoute> {
  return () => {
    const manifest = readManifest(read());
    const bundle = `bundle ${manifest.bundle_version}`;
    if (manifest.model === null) {
      throw notInBundle(`The model report is not in ${bundle}: manifest.json names no model.`);
    }
    throw notServedYet(
      `Model ${manifest.model.id} is in ${bundle}, but the model report is not served yet: ` +
        '@terraluna/schema has no ModelRecord schema.',
    );
  };
}
