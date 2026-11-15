// GET /api/v1/targets/{id}/matches (PRD 8.5): the ComparisonRecords of a target from the loader's
// validated comparisons/<target_id>.json (PRD 8.4), in file order (the trim order of the pipeline).
//
// family=terrain returns terrain_relief and terrain_texture together; measure=relief|texture
// reorders inside the terrain family only (stable, ascending, an unavailable distance last) and is
// a 400 problem with radar or model. family=radar returns radar_cpr and family=model returns
// model_embedding (CONTRACTS a.2). Families are never combined (PRD 6.5). A distance a record
// stores as unavailable or not_comparable is returned as stored with its reason; no value is
// ever filled in. A Mode B record never carries a radar value: a stored one is a 500 problem.
//
// family=model is a 409 problem while the G5 status of validation.json is not PASS (and while
// no model is in the bundle); the encoder stays EXPERIMENTAL and outside rankings. Otherwise its
// entries are labelled Predicted.
//
// Paging: limit 1 to 500 (default 50) and an opaque cursor; at most the first 500 stored records
// are paged (the file holds the top 500, PRD 8.4). A malformed cursor is a 400 problem.
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';

import type { ComparisonRecord } from '@terraluna/schema';

import { BundleFileError, comparisonPath, type PulledBundle } from '../../lib/bundle.ts';
import {
  bundleCacheHeaders,
  bundleCacheResponseHeaders,
  conditionalGet,
  notModifiedResponse,
} from './cache.ts';
import { modelFamilyAccess } from './model.ts';
import { ProblemError, VALIDATION_STATUS } from './problem.ts';
import { ComparisonRecordSchema, DistanceEntrySchema, ModelDistanceEntrySchema } from './records.ts';
import { TargetIdSchema } from './targets.ts';
import { notInBundle, problemResponses, readManifest, readValidation } from './unavailable.ts';

type MatchFamily = (typeof MATCH_FAMILIES)[number];

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
    body: z.enum(MATCH_BODIES).optional().openapi({
      description: 'The body of the matched windows; earth, the only search universe, if left out.',
    }),
    family: z.enum(MATCH_FAMILIES).openapi({
      description:
        'The distance family. Model-family matches exist only when a model is in the bundle and ' +
        'G5 is PASS, and are labelled Predicted.',
    }),
    measure: z.enum(['relief', 'texture']).optional().openapi({
      description:
        'Orders the terrain family by terrain_relief or terrain_texture (ascending, ' +
        'unavailable last). Only with family=terrain; any other family is a 400 problem.',
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

export const MATCH_MEASURES = ['relief', 'texture'] as const;

/** The most records ever paged: the first 500 stored records of a comparison file. */
export const MATCH_TOTAL_MAX = MATCH_LIMIT_MAX;

export const MatchItemSchema = z
  .object({
    target_window_id: z.string(),
    earth_window_id: z.string(),
    shipping_mode: ComparisonRecordSchema.shape.shipping_mode,
    comparable_observables: ComparisonRecordSchema.shape.comparable_observables,
    excluded_observables_and_reasons: ComparisonRecordSchema.shape.excluded_observables_and_reasons,
    claim_limits: ComparisonRecordSchema.shape.claim_limits,
    sort_rule: ComparisonRecordSchema.shape.sort_rule,
    terrain_relief: DistanceEntrySchema.optional(),
    terrain_texture: DistanceEntrySchema.optional(),
    radar_cpr: DistanceEntrySchema.optional(),
    model_embedding: ModelDistanceEntrySchema.optional(),
    run_id: z.string(),
  })
  .strict()
  .openapi('MatchItem');

export type MatchItem = z.infer<typeof MatchItemSchema>;

export const MatchesResponseSchema = z
  .object({
    bundle_version: z.string(),
    target_id: z.string(),
    family: z.enum(MATCH_FAMILIES),
    measure: z.enum(MATCH_MEASURES).nullable(),
    evidence_label: z.literal('Predicted').optional().openapi({
      description: 'Set on family=model only: encoder distances are Predicted.',
    }),
    total: z.number().int().openapi({
      description: 'The records the cursor pages over: at most the first 500 stored records.',
    }),
    limit: z.number().int(),
    next_cursor: z.string().nullable(),
    items: z.array(MatchItemSchema),
  })
  .strict()
  .openapi('Matches');

export type MatchesResponse = z.infer<typeof MatchesResponseSchema>;

export const matchesRoute = createRoute({
  method: 'get',
  path: '/targets/{id}/matches',
  middleware: [conditionalGet],
  request: { params: z.object({ id: TargetIdSchema }), query: MatchesQuerySchema },
  summary: 'The matches of a target',
  description:
    'Returns the stored ComparisonRecords of the target in file order, one family per request: ' +
    'terrain (terrain_relief and terrain_texture; measure=relief|texture reorders within it), ' +
    'radar (radar_cpr) or model (model_embedding, Predicted). Distances are never combined, and ' +
    'a distance stored as unavailable keeps its reason. family=model is a 409 problem while the ' +
    'G5 status is not PASS. measure with radar or model, an unknown parameter and a malformed ' +
    'cursor are 400 problems. Pages at most the first 500 stored records. Cached per bundle.',
  responses: {
    200: {
      description: 'One page of matches.',
      headers: bundleCacheResponseHeaders('per-bundle'),
      content: { 'application/json': { schema: MatchesResponseSchema } },
    },
    304: notModifiedResponse,
    ...problemResponses({
      invalid:
        'A path or query parameter is missing, unknown or out of range, measure is given with a ' +
        'family other than terrain, or the cursor is malformed.',
      404: 'The bundle holds no comparisons for this target.',
      409: 'family=model while the G5 status is not PASS, or the bundle has no model.',
    }),
  },
});

/** The stored distances of one family, as the response carries them. */
function familyFields(record: ComparisonRecord, family: MatchFamily): Partial<MatchItem> {
  const d = record.distances_by_family;
  if (family === 'terrain') {
    return { terrain_relief: d.terrain_relief, terrain_texture: d.terrain_texture };
  }
  if (family === 'radar') return { radar_cpr: d.radar_cpr };
  return { model_embedding: d.model_embedding };
}

/** A distance's sort key: its value, with every distance that has none after all that do. */
function sortKey(record: ComparisonRecord, measure: (typeof MATCH_MEASURES)[number]): number {
  const entry = measure === 'relief' ? record.distances_by_family.terrain_relief : record.distances_by_family.terrain_texture;
  return entry.status === 'available' && entry.value !== null ? entry.value : Number.POSITIVE_INFINITY;
}

/** A cursor holds the offset of the next record as base64url text. */
export function encodeCursor(offset: number): string {
  return Buffer.from(`o${String(offset)}`, 'utf8').toString('base64url');
}

/** The offset a cursor names, or a 400 problem when it is not one this API issued. */
export function decodeCursor(cursor: string | undefined, total: number): number {
  if (cursor === undefined || cursor === '') return 0;
  const text = Buffer.from(cursor, 'base64url').toString('utf8');
  const match = /^o(0|[1-9][0-9]*)$/.exec(text);
  const offset = match?.[1] === undefined ? Number.NaN : Number(match[1]);
  if (!Number.isSafeInteger(offset) || offset > total || encodeCursor(offset) !== cursor) {
    throw new ProblemError(VALIDATION_STATUS, 'Invalid request. query parameter cursor: malformed.');
  }
  return offset;
}

/** The matches handler over a bundle reader. */
export function matchesHandler(read: () => PulledBundle): RouteHandler<typeof matchesRoute> {
  return (c) => {
    const { id } = c.req.valid('param');
    const { family, measure, limit, cursor } = c.req.valid('query');
    if (measure !== undefined && family !== 'terrain') {
      throw new ProblemError(
        VALIDATION_STATUS,
        `Invalid request. query parameter measure: allowed only with family=terrain, not ${family}.`,
      );
    }
    const bundle = read();
    const manifest = readManifest(bundle);
    let label: 'Predicted' | undefined;
    if (family === 'model') {
      const access = modelFamilyAccess(manifest, readValidation(bundle), manifest.bundle_version);
      if (!access.allowed) throw new ProblemError(409, access.detail);
      label = access.label;
    }
    const stored = bundle.records.comparisons.get(id);
    if (stored === undefined) {
      throw notInBundle(
        `The matches of target ${id} are not in bundle ${manifest.bundle_version}: ` +
          `it holds no ${comparisonPath(id)}.`,
      );
    }
    if (family === 'radar') {
      for (const record of stored) {
        if (record.shipping_mode === 'B' && record.distances_by_family.radar_cpr.value !== null) {
          throw new BundleFileError(
            comparisonPath(id),
            'invalid',
            `${comparisonPath(id)} gives a Mode B record a radar value`,
          );
        }
      }
    }
    const limited = stored.slice(0, MATCH_TOTAL_MAX);
    const ordered =
      measure === undefined
        ? limited
        : limited
            .map((record, index) => ({ record, index, key: sortKey(record, measure) }))
            .sort((x, y) => (x.key === y.key ? x.index - y.index : x.key < y.key ? -1 : 1))
            .map((x) => x.record);
    const start = decodeCursor(cursor, ordered.length);
    const page = ordered.slice(start, start + limit);
    const end = start + page.length;
    const response: MatchesResponse = {
      bundle_version: manifest.bundle_version,
      target_id: id,
      family,
      measure: measure ?? null,
      ...(label === undefined ? {} : { evidence_label: label }),
      total: ordered.length,
      limit,
      next_cursor: end < ordered.length ? encodeCursor(end) : null,
      items: page.map((record) => ({
        target_window_id: record.target_window_id,
        earth_window_id: record.earth_window_id,
        shipping_mode: record.shipping_mode,
        comparable_observables: record.comparable_observables,
        excluded_observables_and_reasons: record.excluded_observables_and_reasons,
        claim_limits: record.claim_limits,
        sort_rule: record.sort_rule,
        ...familyFields(record, family),
        run_id: record.run_id,
      })),
    };
    return c.json(response, 200, bundleCacheHeaders('per-bundle', manifest.bundle_version));
  };
}
