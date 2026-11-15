// GET /api/v1/targets and GET /api/v1/targets/{id} (PRD 8.5), served from the loader's validated
// targets.json (TargetRecord) and windows/<body>.json (WindowEntry) records.
//
// The list gives each TargetRecord's target_id, body, footprint and shipping_mode, and nothing
// else; a footprint that is null (before G0-02) stays null and is never filled from an id or a
// bbox. A bundle without targets.json lists no targets.
//
// The detail returns the TargetRecord, its target window (primary_window_id) and reference
// windows (reference_window_ids) as the WindowEntries windows/<body>.json stores them, and the
// radar context: the stored radar fingerprints of the target window. A window the files lack is
// absent from the response, not a default. A radar fingerprint without data carries its stored
// reason and is Unavailable, never zero.
//
// An unknown id is a 404 problem; an id outside TARGET_ID_PATTERN is a VALIDATION_STATUS problem
// before the bundle is read; a bundle file that fails its schema is a 500 problem.
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';
import type { FingerprintRecord } from '@terraluna/schema';

import type { PulledBundle } from '../../lib/bundle.ts';
import {
  bundleCacheHeaders,
  bundleCacheResponseHeaders,
  conditionalGet,
  notModifiedResponse,
} from './cache.ts';
import { findWindow } from './lookup.ts';
import { FingerprintRecordSchema, TargetRecordSchema, WindowEntrySchema } from './records.ts';
import { notInBundle, problemResponses, readManifest } from './unavailable.ts';

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

/** One row of the target list: the four fields PRD 8.5 names, as the TargetRecord stores them. */
export const TargetListItemSchema = z
  .object({
    target_id: TargetRecordSchema.shape.target_id,
    body: TargetRecordSchema.shape.body,
    footprint: TargetRecordSchema.shape.footprint,
    shipping_mode: TargetRecordSchema.shape.shipping_mode,
  })
  .strict()
  .openapi('TargetListItem');

export const TargetListSchema = z
  .object({
    bundle_version: z.string(),
    shipping_mode: TargetRecordSchema.shape.shipping_mode,
    items: z.array(TargetListItemSchema),
  })
  .strict()
  .openapi('TargetList', {
    description:
      'The targets of targets.json, in file order, with the bundle_version and shipping_mode ' +
      'of the bundle this deploy serves (null when no mode has been chosen). Empty when the ' +
      'bundle has no targets.json. A footprint that is null is not filled in.',
  });

export type TargetList = z.infer<typeof TargetListSchema>;

/**
 * One stored radar fingerprint of the target window. `status` is unavailable when the fingerprint
 * holds no radar data or states a reason; `reason` is then the stored reason (null when the file
 * stores none), and the stored fingerprint is returned as is.
 */
export const RadarContextEntrySchema = z
  .object({
    window_id: z.string(),
    product_id: z.string().nullable(),
    evidence_label: FingerprintRecordSchema.shape.evidence_label,
    status: z.enum(['available', 'unavailable']),
    reason: z.string().nullable(),
    fingerprint: FingerprintRecordSchema,
  })
  .strict()
  .openapi('RadarContextEntry');

export const TargetDetailSchema = z
  .object({
    bundle_version: z.string(),
    target: TargetRecordSchema,
    target_window: WindowEntrySchema.optional(),
    reference_windows: z.array(WindowEntrySchema),
    radar_context: z.array(RadarContextEntrySchema),
  })
  .strict()
  .openapi('TargetDetail', {
    description:
      'The TargetRecord, its target and reference WindowEntries as windows/<body>.json stores ' +
      'them (a window the files lack is absent), and the radar context of the target window.',
  });

export type TargetDetail = z.infer<typeof TargetDetailSchema>;

export const targetListRoute = createRoute({
  method: 'get',
  path: '/targets',
  middleware: [conditionalGet],
  summary: 'The targets of the bundle',
  description:
    'Returns each TargetRecord of targets.json as target_id, body, footprint and shipping_mode, ' +
    'in file order, with the bundle_version and shipping_mode of the bundle this deploy serves. ' +
    'A null footprint stays null. Cached per bundle; the ETag holds the bundle_version, and a ' +
    'matching If-None-Match is answered with 304.',
  responses: {
    200: {
      description: 'The targets of the bundle.',
      headers: bundleCacheResponseHeaders('per-bundle'),
      content: { 'application/json': { schema: TargetListSchema } },
    },
    304: notModifiedResponse,
    ...problemResponses({}),
  },
});

export const targetRoute = createRoute({
  method: 'get',
  path: '/targets/{id}',
  middleware: [conditionalGet],
  request: { params: z.object({ id: TargetIdSchema }) },
  summary: 'One target of the bundle',
  description:
    'Returns the TargetRecord, its target window and reference windows as stored in ' +
    'windows/<body>.json, and the radar context from the stored radar fingerprints of the target ' +
    'window. A window the bundle lacks is absent; a radar fingerprint without data is ' +
    'Unavailable with its stored reason, never zero. An id the bundle does not hold is a 404 ' +
    'problem. Cached per bundle.',
  responses: {
    200: {
      description: 'The target and its windows.',
      headers: bundleCacheResponseHeaders('per-bundle'),
      content: { 'application/json': { schema: TargetDetailSchema } },
    },
    304: notModifiedResponse,
    ...problemResponses({
      invalid: 'The id does not match the target id pattern or is too long.',
      404: 'The bundle holds no target with this id.',
    }),
  },
});

/** The radar context entries of one window row: its stored radar fingerprints, none invented. */
function radarContext(radar: readonly FingerprintRecord[]): TargetDetail['radar_context'] {
  return radar.map((fingerprint) => {
    const data = fingerprint.radar;
    const reason = data?.reason ?? null;
    return {
      window_id: fingerprint.window_id,
      product_id: data?.product_id ?? fingerprint.product_ids[0] ?? null,
      evidence_label: fingerprint.evidence_label,
      status: data === null || reason !== null ? ('unavailable' as const) : ('available' as const),
      reason,
      fingerprint,
    };
  });
}

/** The target list handler over a bundle reader. */
export function targetListHandler(read: () => PulledBundle): RouteHandler<typeof targetListRoute> {
  return (c) => {
    const bundle = read();
    const manifest = readManifest(bundle);
    const list: TargetList = {
      bundle_version: manifest.bundle_version,
      shipping_mode: manifest.shipping_mode,
      items: (bundle.records.targets ?? []).map((t) => ({
        target_id: t.target_id,
        body: t.body,
        footprint: t.footprint,
        shipping_mode: t.shipping_mode,
      })),
    };
    return c.json(list, 200, bundleCacheHeaders('per-bundle', manifest.bundle_version));
  };
}

/** The target detail handler over a bundle reader. */
export function targetHandler(read: () => PulledBundle): RouteHandler<typeof targetRoute> {
  return (c) => {
    const { id } = c.req.valid('param');
    const bundle = read();
    const manifest = readManifest(bundle);
    const target = bundle.records.targets?.find((t) => t.target_id === id);
    if (target === undefined) {
      throw notInBundle(`Target ${id} is not in bundle ${manifest.bundle_version}.`);
    }
    const targetWindow =
      target.primary_window_id === null ? null : findWindow(bundle, target.primary_window_id);
    const references = target.reference_window_ids.flatMap((windowId) => {
      const found = findWindow(bundle, windowId);
      return found === null ? [] : [found.entry];
    });
    const detail: TargetDetail = {
      bundle_version: manifest.bundle_version,
      target,
      ...(targetWindow === null ? {} : { target_window: targetWindow.entry }),
      reference_windows: references,
      radar_context: targetWindow === null ? [] : radarContext(targetWindow.entry.radar),
    };
    return c.json(detail, 200, bundleCacheHeaders('per-bundle', manifest.bundle_version));
  };
}
