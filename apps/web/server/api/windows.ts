// GET /api/v1/windows/{id} (PRD 8.5): a WindowRecord with its fingerprint and card link. The API
// reads window records only from the pulled files that hold them: targets.json (target windows)
// and windows/<body>.json (the top-N subset of windows/<body>.parquet, which is never pulled)
// (PRD 8.4, 8.5). Neither has a @terraluna/schema schema yet (no WindowRecord), so nothing is read
// from them. Which of them could hold a window follows from manifest.json alone (windowSources):
//
//   - targets.json holds the target windows, whose ids manifest.targets lists: a target id is the
//     id of its target window (moon-cr-site01-w000 in PRD 8.4, which also keys
//     comparisons/<target_id>.json), so it could hold the window only if manifest.targets lists it;
//   - windows/<body>.json could hold any window: its ids are known only by reading it.
//
// By the rule in ./unavailable.ts:
//
//   - 404 when no file manifest.json lists could hold the window: the bundle does not hold it;
//   - 501 when one could: the window could be in it, but the file is not read, so the id is not
//     looked up in it. Telling an unknown window id from a known one there would mean parsing a
//     file that has no schema, which the API never does.
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';
import type { BundleManifest } from '@terraluna/schema';

import { isPulledPath, type PulledBundle, TARGETS } from '../../lib/bundle.ts';
import { TARGET_ID_MAX_LENGTH, TARGET_ID_PATTERN } from './targets.ts';
import {
  listedPaths,
  notInBundle,
  notServedYet,
  readManifest,
  unavailableResponses,
} from './unavailable.ts';

/**
 * A window id the API accepts. WindowRecord has no schema yet, so window ids follow the target id
 * rule (./targets.ts): target ids are window ids in the PRD 8.4 example (moon-cr-site01-w000), and
 * a ComparisonRecord pairs a target window id with an Earth window id.
 */
export const WINDOW_ID_PATTERN = TARGET_ID_PATTERN;
export const WINDOW_ID_MAX_LENGTH = TARGET_ID_MAX_LENGTH;

export const WindowIdSchema = z
  .string()
  .max(WINDOW_ID_MAX_LENGTH)
  .regex(new RegExp(WINDOW_ID_PATTERN))
  .openapi({
    description:
      `A window id: at most ${String(WINDOW_ID_MAX_LENGTH)} ASCII letters, digits, ".", "_" or ` +
      '"-", not starting with ".". Any other id is a 400 problem.',
  });

/** True for a pulled top-N window file, windows/<body>.json. */
function isTopNWindowFile(path: string): boolean {
  return path.startsWith('windows/') && isPulledPath(path);
}

/** True for a pulled bundle file that holds window records: targets.json or windows/<body>.json. */
export function isWindowFile(path: string): boolean {
  return path === TARGETS || isTopNWindowFile(path);
}

/**
 * The files manifest.json lists that could hold the WindowRecord of window `id`, sorted:
 * targets.json when manifest.targets lists the id, and every windows/<body>.json.
 */
export function windowSources(manifest: BundleManifest, id: string): string[] {
  const target = manifest.targets.includes(id);
  return listedPaths(manifest, (path) =>
    path === TARGETS ? target : isTopNWindowFile(path),
  ).sort();
}

export const windowRoute = createRoute({
  method: 'get',
  path: '/windows/{id}',
  request: {
    params: z.object({ id: WindowIdSchema.openapi({ param: { name: 'id', in: 'path' } }) }),
  },
  summary: 'One window of the bundle (not served yet)',
  description:
    'Window records are not served yet: @terraluna/schema has no WindowRecord schema. When no ' +
    'file manifest.json lists could hold the window, as on the mock bundle, the answer is a 404 ' +
    'problem: targets.json holds only the target windows manifest.json lists, and ' +
    'windows/<body>.json could hold any window. When one could, the answer is a 501 problem, since ' +
    'the id is not looked up in a file without a schema. Problems are never cached.',
  responses: unavailableResponses({
    invalid: 'The id does not match the window id pattern or is too long.',
    notInBundle: 'No file manifest.json lists could hold the window: the bundle does not hold it.',
    notServed:
      'A window file that could hold the window is in the bundle, but window records are not ' +
      'served yet.',
  }),
});

/** The window handler over a bundle reader: 404 or 501, as the module comment says. */
export function windowHandler(read: () => PulledBundle): RouteHandler<typeof windowRoute> {
  return (c) => {
    const { id } = c.req.valid('param');
    const manifest = readManifest(read());
    const bundle = `bundle ${manifest.bundle_version}`;
    const sources = windowSources(manifest, id);
    if (sources.length > 0) {
      throw notServedYet(
        `Window files that could hold window ${id} are in ${bundle} (${sources.join(', ')}), ` +
          'but window records are not served yet: @terraluna/schema has no WindowRecord schema, ' +
          'so the id is not looked up in them.',
      );
    }
    if (listedPaths(manifest, isWindowFile).length === 0) {
      throw notInBundle(
        `Window ${id} is not in ${bundle}: it lists no window file the API reads ` +
          '(targets.json or windows/<body>.json).',
      );
    }
    // Only targets.json is listed, and manifest.targets does not list the id.
    throw notInBundle(
      `Window ${id} is not in ${bundle}: targets.json holds only the target windows ` +
        'manifest.json lists, which do not include it, and the bundle lists no windows/<body>.json.',
    );
  };
}
