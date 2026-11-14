// GET /api/v1/gates (PRD 8.5): the pulled validation.json unchanged, cached per bundle. Each gate
// keeps the status the bundle records (NOT_RUN, PASS, FAIL, PARTIAL or NOT_APPLICABLE), so an
// untested gate is never reported as a pass (PRD 8.12). The manifest supplies the bundle_version
// for the ETag, so both files are checked again with their generated schemas before anything is
// sent.
import { createRoute, type RouteHandler } from '@hono/zod-openapi';

import { MANIFEST, type PulledBundle, VALIDATION } from '../../lib/bundle.ts';
import {
  bundleCacheHeaders,
  bundleCacheResponseHeaders,
  conditionalGet,
  notModifiedResponse,
} from './cache.ts';
import {
  MOCK_REFUSED_STATUS,
  mockRefusedResponse,
  PROBLEM_MEDIA_TYPE,
  ProblemSchema,
} from './problem.ts';
import { BundleManifestRecord, parseBundleFile, ValidationRecord } from './records.ts';

export const gatesRoute = createRoute({
  method: 'get',
  path: '/gates',
  middleware: [conditionalGet],
  summary: 'Release gate states',
  description:
    'Returns validation.json of the bundle this deploy serves, unchanged: one GateRecord per ' +
    'release gate with its status as recorded (NOT_RUN, PASS, FAIL, PARTIAL or NOT_APPLICABLE). ' +
    'A gate that was not run is NOT_RUN, never PASS. Cached per bundle; the ETag holds the ' +
    'bundle_version, and a matching If-None-Match is answered with 304.',
  responses: {
    200: {
      description: 'validation.json, unchanged.',
      headers: bundleCacheResponseHeaders('per-bundle'),
      content: { 'application/json': { schema: ValidationRecord } },
    },
    304: notModifiedResponse,
    500: {
      description: 'A bundle file is missing or invalid, or an unexpected error occurred.',
      content: { [PROBLEM_MEDIA_TYPE]: { schema: ProblemSchema } },
    },
    [MOCK_REFUSED_STATUS]: mockRefusedResponse,
  },
});

/** The gates handler over a bundle reader. Nothing that fails a schema is served. */
export function gatesHandler(read: () => PulledBundle): RouteHandler<typeof gatesRoute> {
  return (c) => {
    const bundle = read();
    const manifest = parseBundleFile(BundleManifestRecord, bundle.manifest, MANIFEST);
    const gates = parseBundleFile(ValidationRecord, bundle.validation, VALIDATION);
    return c.json(gates, 200, bundleCacheHeaders('per-bundle', manifest.bundle_version));
  };
}
