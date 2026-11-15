// GET /api/v1/windows/{id} (PRD 8.5): the WindowEntry of a window, from the loader's validated
// windows/<body>.json records (PRD 8.4), with the link to its card. The card link names the
// card_id and bundle path the WindowEntry stores, and says whether cards/<card_id>.json is in the
// bundle; a window without a card_id has no link (null), never an invented one. A window id the
// bundle does not hold is a 404 problem; an id outside WINDOW_ID_PATTERN is a 400 problem.
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';

import type { PulledBundle } from '../../lib/bundle.ts';
import {
  bundleCacheHeaders,
  bundleCacheResponseHeaders,
  conditionalGet,
  notModifiedResponse,
} from './cache.ts';
import { findWindow } from './lookup.ts';
import { WindowEntrySchema } from './records.ts';
import { notInBundle, problemResponses, readManifest } from './unavailable.ts';

/** WindowId (@terraluna/schema, CONTRACTS a.2): body prefix, then lowercase letters, digits, "-". */
export const WINDOW_ID_PATTERN = '^(moon|mars|earth)-[a-z0-9][a-z0-9-]*$';
export const WINDOW_ID_MAX_LENGTH = 128;

export const WindowIdSchema = z
  .string()
  .max(WINDOW_ID_MAX_LENGTH)
  .regex(new RegExp(WINDOW_ID_PATTERN))
  .openapi({
    description:
      `A window id: moon-, mars- or earth- and lowercase letters, digits or "-", at most ` +
      `${String(WINDOW_ID_MAX_LENGTH)} characters. Any other id is a 400 problem.`,
  });

export const CardLinkSchema = z
  .object({
    card_id: z.string(),
    path: z.string().openapi({ description: 'The bundle path of the card, cards/<card_id>.json.' }),
    in_bundle: z.boolean().openapi({
      description: 'True when the loaded bundle holds the card file.',
    }),
  })
  .strict()
  .openapi('CardLink');

export const WindowResponseSchema = z
  .object({
    bundle_version: z.string(),
    body: z.string().openapi({ description: 'The body of the windows/<body>.json that holds it.' }),
    window: WindowEntrySchema,
    card: CardLinkSchema.nullable(),
  })
  .strict()
  .openapi('WindowResponse');

export type WindowResponse = z.infer<typeof WindowResponseSchema>;

export const windowRoute = createRoute({
  method: 'get',
  path: '/windows/{id}',
  middleware: [conditionalGet],
  request: { params: z.object({ id: WindowIdSchema.openapi({ param: { name: 'id', in: 'path' } }) }) },
  summary: 'One window of the bundle',
  description:
    'Returns the WindowEntry (window record, fingerprints, eligibility) and the link to its ' +
    'card. card is null when the entry stores no card_id. An id the bundle does not hold is a ' +
    '404 problem. Cached per bundle.',
  responses: {
    200: {
      description: 'The window.',
      headers: bundleCacheResponseHeaders('per-bundle'),
      content: { 'application/json': { schema: WindowResponseSchema } },
    },
    304: notModifiedResponse,
    ...problemResponses({
      invalid: 'The id does not match the window id pattern or is too long.',
      404: 'The bundle holds no window with this id.',
    }),
  },
});

/** The window handler over a bundle reader. */
export function windowHandler(read: () => PulledBundle): RouteHandler<typeof windowRoute> {
  return (c) => {
    const { id } = c.req.valid('param');
    const bundle = read();
    const manifest = readManifest(bundle);
    const found = findWindow(bundle, id);
    if (found === null) {
      throw notInBundle(`Window ${id} is not in bundle ${manifest.bundle_version}.`);
    }
    const cardId = found.entry.card_id;
    const response: WindowResponse = {
      bundle_version: manifest.bundle_version,
      body: found.body,
      window: found.entry,
      card:
        cardId === null
          ? null
          : {
              card_id: cardId,
              path: `cards/${cardId}.json`,
              in_bundle: bundle.records.cards.has(cardId),
            },
    };
    return c.json(response, 200, bundleCacheHeaders('per-bundle', manifest.bundle_version));
  };
}
