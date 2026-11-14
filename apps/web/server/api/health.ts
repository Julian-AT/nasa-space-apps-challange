// GET /api/v1/health (PRD 8.5): the API's status and the bundle_version and schema_version of the
// bundle it serves, read from manifest.json on every request and never cached.
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';
import { BundleManifestSchema } from '@terraluna/schema';

import type { PulledBundle } from '../../lib/bundle.ts';
import { PROBLEM_MEDIA_TYPE, ProblemSchema } from './problem.ts';

/**
 * The health body. bundle_version and schema_version are the BundleManifest schemas themselves,
 * picked from @terraluna/schema, so they cannot drift from the manifest contract (PRD 8.5, D23).
 */
export const HealthSchema = BundleManifestSchema.pick({ bundle_version: true, schema_version: true })
  .extend({
    status: z.literal('ok').openapi({
      description: 'ok when the bundle was read and passed every loader check.',
    }),
  })
  .openapi('Health', {
    description: 'API status and the release bundle it serves.',
  });

export type Health = z.infer<typeof HealthSchema>;

export const healthRoute = createRoute({
  method: 'get',
  path: '/health',
  summary: 'API status and bundle version',
  description:
    'Reads the pulled release bundle and returns its bundle_version and schema_version. ' +
    'A missing or invalid bundle file is a 500 problem; no default value is served. Never cached.',
  responses: {
    200: {
      description: 'The bundle was read and passed every loader check.',
      content: { 'application/json': { schema: HealthSchema } },
    },
    500: {
      description: 'A bundle file is missing or invalid, or an unexpected error occurred.',
      content: { [PROBLEM_MEDIA_TYPE]: { schema: ProblemSchema } },
    },
  },
});

/** The health handler over a bundle reader. The body is parsed, so nothing invalid passes. */
export function healthHandler(read: () => PulledBundle): RouteHandler<typeof healthRoute> {
  return (c) => {
    const { manifest } = read();
    const health = HealthSchema.parse({
      status: 'ok',
      bundle_version: manifest.bundle_version,
      schema_version: manifest.schema_version,
    });
    return c.json(health, 200, { 'Cache-Control': 'no-store' });
  };
}
