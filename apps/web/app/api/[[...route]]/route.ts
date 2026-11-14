// The public read-only API (PRD 8.5, D24): every request under /api goes to the Hono app in
// server/api/, which serves /api/v1/* and the /api/docs redirect and answers anything else with a
// problem. Routes are tested without Next (server/api/*.test.ts).
//
// The handlers are typed (req: Request) => app.fetch(req) wrappers, not handle(app) from
// hono/vercel (PRD 8.5): hono issue 4877 reports that Next's generated route types reject the
// exported handle(app) signature at tsc time, although it works at run time
// (https://github.com/honojs/hono/issues/4877). With next 16.4.6 and hono 4.13.18 handle(app)
// does type-check (the issue was closed as not reproducible), but the wrapper keeps the handler
// type explicit, and Next's route validator (.next/types/validator.ts) checks it.
//
// GET and HEAD are served. POST, PUT, PATCH, DELETE and OPTIONS are exported too, so the app, not
// Next, answers them: a 405 problem with Allow: GET, HEAD. Without an OPTIONS export, Next would
// answer OPTIONS itself.
import { app } from '../../../server/api/index.ts';

// The loader reads apps/web/data/ with node:fs, so the route needs the Node.js runtime.
export const runtime = 'nodejs';

// Every response depends on the bundle and the environment of the request, so nothing is
// prerendered or cached by Next; the app sets each response's Cache-Control.
export const dynamic = 'force-dynamic';

type ApiHandler = (req: Request) => Response | Promise<Response>;

const serve: ApiHandler = (req) => app.fetch(req);

export const GET = serve;
export const HEAD = serve;
export const POST = serve;
export const PUT = serve;
export const PATCH = serve;
export const DELETE = serve;
export const OPTIONS = serve;
