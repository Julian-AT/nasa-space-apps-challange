// GET /api/v1/targets/{id}/matches (PRD 8.5): the ranked ComparisonRecords of a target, from
// comparisons/<target_id>.json (PRD 8.4). @terraluna/schema has no ComparisonRecord schema yet, so
// no match is served, and by the rule in ./unavailable.ts the answer is always a problem:
//
//   family=model first goes through modelFamilyAccess (./model.ts): without a model in the bundle,
//     or while G5 is not PASS, it is a 404 problem, whatever else the bundle holds;
//   then a target id manifest.targets does not list is a 404 problem;
//   then a comparison file manifest.json does not list is a 404 problem;
//   and a comparison file it lists is a 501 problem: in the bundle, but not served yet.
//
// Every parameter is validated before the bundle is read, and a request that fails is a
// VALIDATION_STATUS problem. limit and cursor are checked although no page is served yet, so a
// client learns of a bad value now rather than once matches are served.
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';

import { comparisonPath, type PulledBundle } from '../../lib/bundle.ts';
import { modelFamilyAccess } from './model.ts';
import { TargetIdSchema } from './targets.ts';
import {
  isListed,
  notInBundle,
  notServedYet,
  readManifest,
  readValidation,
  unavailableResponses,
} from './unavailable.ts';

/**
 * The body of the matched windows. A ComparisonRecord pairs a target window with an Earth window
 * (earth_window_id, PRD 8.12) and the search universe is Earth (PRD 6.4), so earth is the only one.
 */
export const MATCH_BODIES = ['earth'] as const;

/**
 * The distance families (PRD 8.5). Terrain, radar and model distances stay separate (PRD 6.5), so
 * a request names exactly one and there is no default family.
 */
export const MATCH_FAMILIES = ['terrain', 'radar', 'model'] as const;

export const MATCH_LIMIT_MIN = 1;
/** comparisons/<target_id>.json holds the top 500 ComparisonRecords (PRD 8.4). */
export const MATCH_LIMIT_MAX = 500;
export const MATCH_LIMIT_DEFAULT = 50;

/** A page cursor: opaque URL-safe text (base64url characters), empty for the first page. */
export const CURSOR_PATTERN = '^[A-Za-z0-9_-]*$';
export const CURSOR_MAX_LENGTH = 256;

/** A limit in plain decimal digits becomes a number; anything else stays as it is and fails. */
function decimalLimit(value: unknown): unknown {
  return typeof value === 'string' && /^(0|[1-9][0-9]*)$/.test(value) ? Number(value) : value;
}

const LIMIT_MESSAGE = `expected an integer from ${String(MATCH_LIMIT_MIN)} to ${String(MATCH_LIMIT_MAX)}`;

export const MatchesQuerySchema = z
  .object({
    body: z.enum(MATCH_BODIES).openapi({
      description: 'The body of the matched windows. Earth is the only search universe.',
    }),
    family: z.enum(MATCH_FAMILIES).openapi({
      description:
        'The distance family. Model-family matches exist only when a model is in the bundle and ' +
        'G5 is PASS, and are labelled Predicted.',
    }),
    limit: z
      .preprocess(
        decimalLimit,
        z
          .number({ error: LIMIT_MESSAGE })
          .int({ error: LIMIT_MESSAGE })
          .min(MATCH_LIMIT_MIN, { error: LIMIT_MESSAGE })
          .max(MATCH_LIMIT_MAX, { error: LIMIT_MESSAGE }),
      )
      .default(MATCH_LIMIT_DEFAULT)
      .openapi({
        description:
          `How many matches a page holds, in decimal digits: ${String(MATCH_LIMIT_MIN)} to ` +
          `${String(MATCH_LIMIT_MAX)}, ${String(MATCH_LIMIT_DEFAULT)} by default.`,
      }),
    cursor: z
      .string()
      .max(CURSOR_MAX_LENGTH)
      .regex(new RegExp(CURSOR_PATTERN))
      .optional()
      .openapi({
        description:
          `Where the page starts: opaque, at most ${String(CURSOR_MAX_LENGTH)} URL-safe ` +
          'characters (A-Z, a-z, 0-9, "_", "-"); empty or left out for the first page.',
      }),
  })
  .strict();

export const matchesRoute = createRoute({
  method: 'get',
  path: '/targets/{id}/matches',
  request: { params: z.object({ id: TargetIdSchema }), query: MatchesQuerySchema },
  summary: 'The matches of a target (not served yet)',
  description:
    'Ranked ComparisonRecords are not served yet: @terraluna/schema has no ComparisonRecord ' +
    'schema. family=model is refused with a 404 problem unless a model is in the bundle and G5 is ' +
    'PASS; model-family matches are always labelled Predicted. Otherwise a target id manifest.json ' +
    'does not list, or a target whose comparisons/<id>.json it does not list, is a 404 problem, ' +
    'and a target whose comparison file it lists is a 501 problem. Unknown query parameters are a ' +
    '400 problem. Problems are never cached.',
  responses: unavailableResponses({
    invalid: 'A path or query parameter is missing, unknown or out of range.',
    notInBundle:
      'The bundle holds no such matches: no model or G5 not PASS (family=model), an unlisted ' +
      'target, or an unlisted comparison file.',
    notServed: 'The comparison file is in the bundle, but ComparisonRecords are not served yet.',
  }),
});

/** The matches handler over a bundle reader: 404 or 501, as the module comment says. */
export function matchesHandler(read: () => PulledBundle): RouteHandler<typeof matchesRoute> {
  return (c) => {
    const { id } = c.req.valid('param');
    const { family } = c.req.valid('query');
    const pulled = read();
    const manifest = readManifest(pulled);
    const bundle = `bundle ${manifest.bundle_version}`;
    let label = '';
    if (family === 'model') {
      const access = modelFamilyAccess(manifest, readValidation(pulled), manifest.bundle_version);
      if (!access.allowed) throw notInBundle(access.detail);
      label = ` Model-family matches are ${access.label} values.`;
    }
    if (!manifest.targets.includes(id)) {
      throw notInBundle(`Target ${id} is not in ${bundle}.`);
    }
    const path = comparisonPath(id);
    if (!isListed(manifest, path)) {
      throw notInBundle(
        `The matches of target ${id} are not in ${bundle}: it does not list ${path}.`,
      );
    }
    throw notServedYet(
      `${path} is in ${bundle}, but its ComparisonRecords are not served yet: ` +
        `@terraluna/schema has no ComparisonRecord schema.${label}`,
    );
  };
}
