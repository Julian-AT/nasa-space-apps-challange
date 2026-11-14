// GET /api/v1/targets and GET /api/v1/targets/{id} (PRD 8.5), served from manifest.json only.
//
// PRD 8.5 lists each target with its body, footprint and shipping mode, and the detail with its
// windows, fingerprints and radar context. Those live in targets.json and other files that have no
// schema in @terraluna/schema yet: there is no TargetRecord schema (a decision left to a human).
// Until one exists the API serves only what the BundleManifest schema validates: the target ids
// manifest.json lists, with its bundle_version and shipping_mode. No target field is derived from
// an id, and targets.json is never read here, so its records are never passed through unvalidated
// and its absence is never an error.
//
// The detail answers with a problem in every case, by one rule:
//   - 404 when the manifest does not list the id: the bundle holds no such target;
//   - 501 when it does: the target exists in the bundle, but no record of it can be served yet.
// Both are expected answers (ProblemError), never logged. An id outside TARGET_ID_PATTERN is a
// VALIDATION_STATUS problem before the bundle is read, and a manifest that fails its schema is a
// 500 problem, never a 404 or 501.
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';
import { BundleManifestSchema } from '@terraluna/schema';

import { MANIFEST, type PulledBundle } from '../../lib/bundle.ts';
import {
  bundleCacheHeaders,
  bundleCacheResponseHeaders,
  conditionalGet,
  notModifiedResponse,
} from './cache.ts';
import { PROBLEM_MEDIA_TYPE, ProblemError, ProblemSchema, VALIDATION_STATUS } from './problem.ts';
import { BundleManifestRecord, parseBundleFile } from './records.ts';

/**
 * A target id the API accepts: one segment of a bundle path, as BundlePath in @terraluna/schema
 * allows it (ASCII letters, digits, ".", "_" and "-", not starting with "."). The bundle names a
 * target's files after its id (comparisons/<target_id>.json, PRD 8.4), so any other id could not
 * name a bundle file. BundleManifest.targets itself constrains ids only to be non-empty.
 */
export const TARGET_ID_PATTERN = '^[A-Za-z0-9_-][A-Za-z0-9._-]*$';

/**
 * The longest target id the API accepts. The longest bundle file name built from an id,
 * reference/background_<target_id>.json (PRD 8.4), adds 16 characters, which keeps every such
 * name within the 255 bytes common file systems allow; the PRD's example ids have under 20.
 */
export const TARGET_ID_MAX_LENGTH = 128;

export const TargetIdSchema = z
  .string()
  .max(TARGET_ID_MAX_LENGTH)
  .regex(new RegExp(TARGET_ID_PATTERN))
  .openapi({
    param: { name: 'id', in: 'path' },
    description:
      `A target id as manifest.json lists it: at most ${String(TARGET_ID_MAX_LENGTH)} ASCII ` +
      'letters, digits, ".", "_" or "-", not starting with ".". Any other id is a 400 problem.',
  });

/**
 * The target list: bundle_version, shipping_mode and targets (as items) are the BundleManifest
 * schemas themselves, picked from @terraluna/schema, so no field is redeclared (PRD 8.5, D23).
 */
export const TargetListSchema = BundleManifestSchema.pick({
  bundle_version: true,
  shipping_mode: true,
})
  .extend({ items: BundleManifestSchema.shape.targets })
  .strict()
  .openapi('TargetList', {
    description:
      'The targets of the release bundle: the target ids manifest.json lists, in its order, ' +
      'with its bundle_version and shipping_mode (null when no mode has been chosen). ' +
      'The body and footprint of each target are not served until @terraluna/schema has a ' +
      'TargetRecord schema.',
  });

export type TargetList = z.infer<typeof TargetListSchema>;

const problemContent = { [PROBLEM_MEDIA_TYPE]: { schema: ProblemSchema } };

const serverError = {
  description: 'A bundle file is missing or invalid, or an unexpected error occurred.',
  content: problemContent,
};

export const targetListRoute = createRoute({
  method: 'get',
  path: '/targets',
  middleware: [conditionalGet],
  summary: 'The targets of the bundle',
  description:
    'Returns the target ids manifest.json lists, in its order, with the bundle_version and ' +
    'shipping_mode of the bundle this deploy serves. The list is empty when the bundle lists no ' +
    'targets, as the mock bundle does. The body and footprint of each target are not served ' +
    'until @terraluna/schema has a TargetRecord schema, and no target field is derived from an ' +
    'id. Cached per bundle; the ETag holds the bundle_version, and a matching If-None-Match is ' +
    'answered with 304.',
  responses: {
    200: {
      description: 'The target ids of the bundle.',
      headers: bundleCacheResponseHeaders('per-bundle'),
      content: { 'application/json': { schema: TargetListSchema } },
    },
    304: notModifiedResponse,
    500: serverError,
  },
});

export const targetRoute = createRoute({
  method: 'get',
  path: '/targets/{id}',
  request: { params: z.object({ id: TargetIdSchema }) },
  summary: 'One target of the bundle (not served yet)',
  description:
    'Target records (windows, fingerprints, radar context) are not served yet: ' +
    '@terraluna/schema has no TargetRecord schema. An id manifest.json does not list is a 404 ' +
    'problem; an id it lists is a 501 problem. Problems are never cached.',
  responses: {
    [VALIDATION_STATUS]: {
      description: 'The id does not match the target id pattern or is too long.',
      content: problemContent,
    },
    404: {
      description: 'manifest.json does not list the id: the bundle holds no such target.',
      content: problemContent,
    },
    500: serverError,
    501: {
      description: 'manifest.json lists the id, but target records are not served yet.',
      content: problemContent,
    },
  },
});

function readManifest(read: () => PulledBundle) {
  return parseBundleFile(BundleManifestRecord, read().manifest, MANIFEST);
}

/** The target list handler over a bundle reader. Only the validated manifest is read. */
export function targetListHandler(read: () => PulledBundle): RouteHandler<typeof targetListRoute> {
  return (c) => {
    const manifest = readManifest(read);
    const list: TargetList = {
      bundle_version: manifest.bundle_version,
      shipping_mode: manifest.shipping_mode,
      items: manifest.targets,
    };
    return c.json(list, 200, bundleCacheHeaders('per-bundle', manifest.bundle_version));
  };
}

/** The target detail handler over a bundle reader: 404 or 501, as the module comment says. */
export function targetHandler(read: () => PulledBundle): RouteHandler<typeof targetRoute> {
  return (c) => {
    const { id } = c.req.valid('param');
    const manifest = readManifest(read);
    const bundle = `bundle ${manifest.bundle_version}`;
    if (!manifest.targets.includes(id)) {
      throw new ProblemError(404, `Target ${id} is not in ${bundle}.`);
    }
    throw new ProblemError(
      501,
      `Target ${id} is in ${bundle}, but target records are not served yet: ` +
        '@terraluna/schema has no TargetRecord schema.',
    );
  };
}
