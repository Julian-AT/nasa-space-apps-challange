// GET /api/v1/model (PRD 8.5, 11.9) and the guard for model-family data (PRD 3, 11.7, 11.10).
//
// /model returns the ModelRecord summary (model/model_card.json), MetricsFile.g5 from
// report/metrics.json when the bundle holds it (null otherwise: a missing metric is never
// invented), and the G5 status of validation.json. Every model value is Predicted. While G5 is
// not PASS in_rankings is false: the encoder stays outside rankings. A bundle without a
// model card is a 404 problem.
//
// modelFamilyAccess is the one gate for model-family data, such as encoder distances in matches.
// The encoder becomes a distance family only when G5 passes (PRD 11.7), so access is refused unless
// manifest.json names a model and G5 is PASS in both manifest.json (model.g5) and validation.json,
// which must hold exactly one G5 record: a missing record is an untested gate, never a pass
// (PRD 8.12). When access is allowed it carries the Predicted label.
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';
import type { BundleManifest, Validation } from '@terraluna/schema';

import type { PulledBundle } from '../../lib/bundle.ts';
import {
  bundleCacheHeaders,
  bundleCacheResponseHeaders,
  conditionalGet,
  notModifiedResponse,
} from './cache.ts';
import { G5SummarySchema, ModelRecordResponseSchema } from './records.ts';
import {
  g5Status,
  notInBundle,
  problemResponses,
  readManifest,
  readValidation,
} from './unavailable.ts';

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
  const notInThisBundle = `Model-family matches are not available in bundle ${bundleVersion}`;
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

export const ModelResponseSchema = z
  .object({
    bundle_version: z.string(),
    evidence_label: z.literal(PREDICTED_LABEL),
    status: z.literal('EXPERIMENTAL').openapi({
      description: 'The encoder is EXPERIMENTAL, as manifest.json labels it.',
    }),
    in_rankings: z.boolean().openapi({
      description: 'True only when the G5 status of validation.json is PASS; otherwise false.',
    }),
    g5_status: z.string().nullable().openapi({
      description: 'The status of the single G5 record in validation.json; null if it has none.',
    }),
    model: ModelRecordResponseSchema,
    g5: G5SummarySchema.nullable().openapi({
      description: 'MetricsFile.g5 of report/metrics.json; null when the bundle has no metrics.',
    }),
  })
  .strict()
  .openapi('ModelResponse');

export type ModelResponse = z.infer<typeof ModelResponseSchema>;

export const modelRoute = createRoute({
  method: 'get',
  path: '/model',
  middleware: [conditionalGet],
  summary: 'The model report (EXPERIMENTAL)',
  description:
    'Returns the model card, MetricsFile.g5 (null without report/metrics.json) and the G5 status ' +
    'of validation.json. Every value is Predicted and the status is EXPERIMENTAL; in_rankings is false while G5 is not ' +
    'PASS. A bundle without a model card is a 404 problem. Cached per bundle.',
  responses: {
    200: {
      description: 'The model report.',
      headers: bundleCacheResponseHeaders('per-bundle'),
      content: { 'application/json': { schema: ModelResponseSchema } },
    },
    304: notModifiedResponse,
    ...problemResponses({ 404: 'The bundle holds no model card.' }),
  },
});

/** The model handler over a bundle reader. */
export function modelHandler(read: () => PulledBundle): RouteHandler<typeof modelRoute> {
  return (c) => {
    const bundle = read();
    const manifest = readManifest(bundle);
    const model = bundle.records.modelCard;
    if (model === null) {
      throw notInBundle(
        `The model report is not in bundle ${manifest.bundle_version}: it has no model card.`,
      );
    }
    const g5 = g5Status(readValidation(bundle));
    const response: ModelResponse = {
      bundle_version: manifest.bundle_version,
      evidence_label: PREDICTED_LABEL,
      status: 'EXPERIMENTAL',
      in_rankings: g5 === 'PASS',
      g5_status: g5,
      model,
      g5: bundle.records.metrics?.g5 ?? null,
    };
    return c.json(response, 200, bundleCacheHeaders('per-bundle', manifest.bundle_version));
  };
}
