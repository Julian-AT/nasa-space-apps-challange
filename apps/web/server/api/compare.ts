// GET /api/v1/compare?a=&b= (PRD 8.5): the ComparisonRecord of two windows, precomputed in
// comparisons/<target_id>.json or, if not, computed on request with @terraluna/core from stored
// fingerprints and labelled Derived (on request). Neither happens yet: comparison files have no
// @terraluna/schema schema (no ComparisonRecord), and the fingerprints would come from window files
// (targets.json, windows/<body>.json) that have none either (no WindowRecord). On-request
// computation waits for @terraluna/core (T10) and real fingerprints, so this route imports no
// @terraluna/core module and never produces a Derived value. Which files could hold the comparison
// follows from manifest.json alone (comparisonSources):
//
//   - precomputed: comparisons/<a>.json or comparisons/<b>.json. A ComparisonRecord there has that
//     target as its target window (PRD 8.4, 8.12; a target id is the id of its target window, see
//     ./windows.ts), so the comparison files of other targets cannot hold it;
//   - on request: the window files that could hold a and b (windowSources in ./windows.ts), and
//     only when both could, since a comparison needs the fingerprints of both windows.
//
// By the rule in ./unavailable.ts:
//
//   - 404 when none could: a comparison of the two windows is not in the bundle, and there is
//     nothing to compute one from;
//   - 501 when one could: a comparison might be in it or be computed from it, but neither is
//     served yet.
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';
import type { BundleManifest } from '@terraluna/schema';

import { comparisonPath, type PulledBundle } from '../../lib/bundle.ts';
import {
  listedPaths,
  notInBundle,
  notServedYet,
  readManifest,
  unavailableResponses,
} from './unavailable.ts';
import { WindowIdSchema, windowSources } from './windows.ts';

/**
 * The files manifest.json lists that could hold a comparison of windows `a` and `b`, sorted: the
 * comparison file of a or b, and the window files of both when both could be in one.
 */
export function comparisonSources(manifest: BundleManifest, a: string, b: string): string[] {
  const precomputed = new Set([comparisonPath(a), comparisonPath(b)]);
  const fromA = windowSources(manifest, a);
  const fromB = windowSources(manifest, b);
  const computable = fromA.length > 0 && fromB.length > 0 ? [...fromA, ...fromB] : [];
  const paths = new Set([...listedPaths(manifest, (path) => precomputed.has(path)), ...computable]);
  return [...paths].sort();
}

export const CompareQuerySchema = z
  .object({
    a: WindowIdSchema.openapi({ description: 'The id of the first window.' }),
    b: WindowIdSchema.openapi({ description: 'The id of the second window.' }),
  })
  .strict();

export const compareRoute = createRoute({
  method: 'get',
  path: '/compare',
  request: { query: CompareQuerySchema },
  summary: 'The comparison of two windows (not served yet)',
  description:
    'ComparisonRecords are not served yet: @terraluna/schema has no ComparisonRecord schema, and ' +
    'comparisons are not computed on request yet, so no Derived (on request) value is produced. ' +
    'When manifest.json lists neither comparisons/<a>.json nor comparisons/<b>.json, and no ' +
    'window files that could hold both windows to compute from, as on the mock bundle, the answer ' +
    'is a 404 problem; when it lists one, a 501 problem. Unknown query parameters are a 400 ' +
    'problem. Problems are never cached.',
  responses: unavailableResponses({
    invalid:
      'a or b is missing or does not match the window id pattern, or a parameter is unknown.',
    notInBundle:
      'No file manifest.json lists could hold the comparison or both windows: the bundle does ' +
      'not hold it.',
    notServed:
      'A comparison or window file that could hold the comparison is in the bundle, but ' +
      'comparisons are not served yet.',
  }),
});

/** The compare handler over a bundle reader: 404 or 501, as the module comment says. */
export function compareHandler(read: () => PulledBundle): RouteHandler<typeof compareRoute> {
  return (c) => {
    const { a, b } = c.req.valid('query');
    const manifest = readManifest(read());
    const bundle = `bundle ${manifest.bundle_version}`;
    const sources = comparisonSources(manifest, a, b);
    if (sources.length > 0) {
      throw notServedYet(
        `Comparison or window files that could hold a comparison of ${a} and ${b} are in ` +
          `${bundle} (${sources.join(', ')}), but comparisons are not served yet: ` +
          '@terraluna/schema has no ComparisonRecord schema, and no comparison is computed on ' +
          'request.',
      );
    }
    const ids = a === b ? [a] : [a, b];
    const lists =
      a === b ? `no ${comparisonPath(a)}` : `neither ${comparisonPath(a)} nor ${comparisonPath(b)}`;
    const missing = ids.filter((id) => windowSources(manifest, id).length === 0).join(' or ');
    throw notInBundle(
      `A comparison of ${a} and ${b} is not in ${bundle}: it lists ${lists}, and no window file ` +
        `it lists could hold ${missing} to compute one from.`,
    );
  };
}
