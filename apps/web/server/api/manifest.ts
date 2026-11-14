// GET /api/v1/manifest (PRD 8.5): the pulled manifest.json unchanged, mock flag included, cached per
// deploy. The body is checked again with the generated BundleManifest schema before it is sent.
import { createRoute, type RouteHandler } from '@hono/zod-openapi';

import { MANIFEST, type PulledBundle } from '../../lib/bundle.ts';
import {
  bundleCacheHeaders,
  bundleCacheResponseHeaders,
  conditionalGet,
  notModifiedResponse,
} from './cache.ts';
import { PROBLEM_MEDIA_TYPE, ProblemSchema } from './problem.ts';
import { BundleManifestRecord, parseBundleFile } from './records.ts';

export const manifestRoute = createRoute({
  method: 'get',
  path: '/manifest',
  middleware: [conditionalGet],
  summary: 'The release bundle manifest',
  description:
    'Returns manifest.json of the bundle this deploy serves, unchanged: its bundle_version, ' +
    'schema_version, mock flag, shipping mode, targets, model and file list with checksums. ' +
    'A mock bundle is a placeholder (mock is true). Cached per deploy; the ETag holds the ' +
    'bundle_version, and a matching If-None-Match is answered with 304.',
  responses: {
    200: {
      description: 'manifest.json, unchanged.',
      headers: bundleCacheResponseHeaders('per-deploy'),
      content: { 'application/json': { schema: BundleManifestRecord } },
    },
    304: notModifiedResponse,
    500: {
      description: 'A bundle file is missing or invalid, or an unexpected error occurred.',
      content: { [PROBLEM_MEDIA_TYPE]: { schema: ProblemSchema } },
    },
  },
});

/** The manifest handler over a bundle reader. Nothing that fails the schema is served. */
export function manifestHandler(read: () => PulledBundle): RouteHandler<typeof manifestRoute> {
  return (c) => {
    const manifest = parseBundleFile(BundleManifestRecord, read().manifest, MANIFEST);
    return c.json(manifest, 200, bundleCacheHeaders('per-deploy', manifest.bundle_version));
  };
}
