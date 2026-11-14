// GET /api/v1/compare?a=&b= (PRD 8.5): the ComparisonRecord of two windows, precomputed in
// comparisons/<target_id>.json or, if not, computed on request with @terraluna/core from stored
// fingerprints and labelled Derived (on request). Neither happens yet: comparison files have no
// @terraluna/schema schema (no ComparisonRecord), and the fingerprints would come from window files
// (targets.json, windows/<body>.json) that have none either (no WindowRecord). On-request
// computation waits for @terraluna/core (T10) and real fingerprints, so this route imports no
// @terraluna/core module and never produces a Derived value. By the rule in ./unavailable.ts:
//
//   - 404 when manifest.json lists no comparison file and no window file: a comparison of the two
//     windows is not in the bundle, and there is nothing to compute one from;
//   - 501 when it lists one: a comparison might be in it or be computed from it, but neither is
//     served yet.
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';

import { isPulledPath, type PulledBundle } from '../../lib/bundle.ts';
import {
  listedPaths,
  notInBundle,
  notServedYet,
  readManifest,
  unavailableResponses,
} from './unavailable.ts';
import { isWindowFile, WindowIdSchema } from './windows.ts';

/** True for a pulled comparison file, comparisons/<target_id>.json, or a window file. */
function isComparisonSource(path: string): boolean {
  return (path.startsWith('comparisons/') && isPulledPath(path)) || isWindowFile(path);
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
    'When manifest.json lists no comparison file and no window file, as the mock bundle does, the ' +
    'answer is a 404 problem; when it lists one, a 501 problem. Unknown query parameters are a ' +
    '400 problem. Problems are never cached.',
  responses: unavailableResponses({
    invalid:
      'a or b is missing or does not match the window id pattern, or a parameter is unknown.',
    notInBundle:
      'manifest.json lists no comparison or window file: no comparison is in the bundle.',
    notServed: 'Comparison or window files are in the bundle, but comparisons are not served yet.',
  }),
});

/** The compare handler over a bundle reader: 404 or 501, as the module comment says. */
export function compareHandler(read: () => PulledBundle): RouteHandler<typeof compareRoute> {
  return (c) => {
    const { a, b } = c.req.valid('query');
    const manifest = readManifest(read());
    const bundle = `bundle ${manifest.bundle_version}`;
    const files = listedPaths(manifest, isComparisonSource).sort();
    if (files.length === 0) {
      throw notInBundle(
        `A comparison of ${a} and ${b} is not in ${bundle}: it lists no comparison file and no ` +
          'window file to compare from.',
      );
    }
    throw notServedYet(
      `Comparison or window files are in ${bundle} (${files.join(', ')}), but comparisons are ` +
        'not served yet: @terraluna/schema has no ComparisonRecord schema, and no comparison is ' +
        'computed on request.',
    );
  };
}
