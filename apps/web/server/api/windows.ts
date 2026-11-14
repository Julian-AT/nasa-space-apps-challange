// GET /api/v1/windows/{id} (PRD 8.5): a WindowRecord with its fingerprint and card link. The API
// reads window records only from the pulled files that hold them: targets.json (target windows)
// and windows/<body>.json (the top-N subset of windows/<body>.parquet, which is never pulled)
// (PRD 8.4, 8.5). Neither has a @terraluna/schema schema yet (no WindowRecord), so nothing is read
// from them, and by the rule in ./unavailable.ts:
//
//   - 404 when manifest.json lists neither: no window is in the bundle for the API to serve;
//   - 501 when it lists one: the window could be in it, but the file is not read, so the id is not
//     looked up. Telling an unknown window id from a known one would mean parsing a file that has
//     no schema, which the API never does.
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';

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

/** True for a pulled bundle file that holds window records: targets.json or windows/<body>.json. */
export function isWindowFile(path: string): boolean {
  return path === TARGETS || (path.startsWith('windows/') && isPulledPath(path));
}

export const windowRoute = createRoute({
  method: 'get',
  path: '/windows/{id}',
  request: {
    params: z.object({ id: WindowIdSchema.openapi({ param: { name: 'id', in: 'path' } }) }),
  },
  summary: 'One window of the bundle (not served yet)',
  description:
    'Window records are not served yet: @terraluna/schema has no WindowRecord schema. When ' +
    'manifest.json lists no window file the API reads (targets.json or windows/<body>.json), as ' +
    'the mock bundle does, the answer is a 404 problem; when it lists one, a 501 problem, since the ' +
    'id is not looked up in a file without a schema. Problems are never cached.',
  responses: unavailableResponses({
    invalid: 'The id does not match the window id pattern or is too long.',
    notInBundle: 'manifest.json lists no window file: the bundle holds no window to serve.',
    notServed: 'Window files are in the bundle, but window records are not served yet.',
  }),
});

/** The window handler over a bundle reader: 404 or 501, as the module comment says. */
export function windowHandler(read: () => PulledBundle): RouteHandler<typeof windowRoute> {
  return (c) => {
    const { id } = c.req.valid('param');
    const manifest = readManifest(read());
    const bundle = `bundle ${manifest.bundle_version}`;
    const files = listedPaths(manifest, isWindowFile).sort();
    if (files.length === 0) {
      throw notInBundle(
        `Window ${id} is not in ${bundle}: it lists no window file the API reads ` +
          '(targets.json or windows/<body>.json).',
      );
    }
    throw notServedYet(
      `Window files are in ${bundle} (${files.join(', ')}), but window records are not served ` +
        `yet: @terraluna/schema has no WindowRecord schema, so window ${id} is not looked up.`,
    );
  };
}
