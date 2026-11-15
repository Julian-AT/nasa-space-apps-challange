// GET /api/v1/compare?a=&b= (PRD 8.5): the stored ComparisonRecord of two windows.
//
// When the pair (a target window and an Earth window, in either order) is in a comparisons file,
// that record is returned unchanged. A pair that is not stored is not computed here: on-request
// terrain distances ('Derived (on request)') need @terraluna/core T10-08, which is not in the
// repository yet, so this route imports no @terraluna/core module and never produces a Derived
// value. A pair of stored windows without a stored comparison is a 501 problem; a window the
// bundle does not hold is a 404 problem. Nothing is invented in place of a missing record.
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';

import type { PulledBundle } from '../../lib/bundle.ts';
import {
  bundleCacheHeaders,
  bundleCacheResponseHeaders,
  conditionalGet,
  notModifiedResponse,
} from './cache.ts';
import { findComparison, findWindow } from './lookup.ts';
import { ComparisonRecordSchema } from './records.ts';
import {
  notInBundle,
  notServedYet,
  problemResponses,
  readManifest,
} from './unavailable.ts';
import { WindowIdSchema } from './windows.ts';

export const CompareQuerySchema = z
  .object({
    a: WindowIdSchema.openapi({ description: 'The id of the first window.' }),
    b: WindowIdSchema.openapi({ description: 'The id of the second window.' }),
  })
  .strict();

export const CompareResponseSchema = z
  .object({
    bundle_version: z.string(),
    source: z.literal('stored').openapi({
      description: 'The record is stored in a comparisons file; nothing is computed on request.',
    }),
    comparison: ComparisonRecordSchema,
  })
  .strict()
  .openapi('CompareResponse');

export type CompareResponse = z.infer<typeof CompareResponseSchema>;

export const compareRoute = createRoute({
  method: 'get',
  path: '/compare',
  middleware: [conditionalGet],
  request: { query: CompareQuerySchema },
  summary: 'The stored comparison of two windows',
  description:
    'Returns the ComparisonRecord stored for the pair, unchanged. A pair that is not stored is ' +
    'not computed on request yet (@terraluna/core T10-08): when both windows are in the bundle ' +
    'the answer is a 501 problem, and when a window is not, a 404 problem. Cached per bundle.',
  responses: {
    200: {
      description: 'The stored comparison.',
      headers: bundleCacheResponseHeaders('per-bundle'),
      content: { 'application/json': { schema: CompareResponseSchema } },
    },
    304: notModifiedResponse,
    ...problemResponses({
      invalid: 'a or b is missing or does not match the window id pattern, or a parameter is unknown.',
      404: 'The bundle holds no window with the id a or b.',
      501: 'Both windows are in the bundle, but no comparison is stored and none is computed on request yet.',
    }),
  },
});

/** The compare handler over a bundle reader. */
export function compareHandler(read: () => PulledBundle): RouteHandler<typeof compareRoute> {
  return (c) => {
    const { a, b } = c.req.valid('query');
    const bundle = read();
    const manifest = readManifest(bundle);
    const version = manifest.bundle_version;
    const stored = findComparison(bundle, a, b);
    if (stored !== null) {
      const response: CompareResponse = {
        bundle_version: version,
        source: 'stored',
        comparison: stored,
      };
      return c.json(response, 200, bundleCacheHeaders('per-bundle', version));
    }
    const missing = [a, b].filter((id) => findWindow(bundle, id) === null);
    if (missing.length > 0) {
      throw notInBundle(`Window ${missing.join(' and ')} is not in bundle ${version}.`);
    }
    throw notServedYet(
      `No comparison of ${a} and ${b} is stored in bundle ${version}, and comparisons are not ` +
        'computed on request yet: @terraluna/core has no terrain distance (T10-08).',
    );
  };
}
