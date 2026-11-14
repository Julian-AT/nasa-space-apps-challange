// GET /api/v1/docs (PRD 8.5, D24): the Scalar API reference, rendered by @scalar/hono-api-reference
// from the relative URL openapi.json, which a browser resolves against /api/v1/docs to
// /api/v1/openapi.json. GET /api/docs (PRD 8.6) is a permanent redirect to it, so both PRD paths
// work; it is the API's only route outside the base path. The redirect is not a second copy of the
// page, because there the relative URL would resolve to /api/openapi.json.
//
// Other hosts (PRD 8.10, strict CSP): the Scalar integration renders a page that loads the
// reference script from a CDN; the package ships no script to serve from here. So the page loads
// exactly one script from one other host, SCALAR_SCRIPT on cdn.jsdelivr.net, pinned to an exact
// version, and turns off every option that would fetch from another host: Scalar's fonts,
// telemetry, its agent and MCP integrations and the developer tools. No request proxy is set, so
// test requests go straight to this API. Whether the CSP allows cdn.jsdelivr.net is a human
// decision (story notes); nothing here allows it.
//
// The page depends on the code of a deploy only, never on the bundle. It is rendered on the first
// request, kept, and cached per deploy with a strong ETag of its bytes (./cache.ts).
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';
import { Scalar } from '@scalar/hono-api-reference';
import { type Handler, Hono } from 'hono';

import {
  cacheControl,
  conditionalGet,
  deployCacheHeaders,
  deployCacheResponseHeaders,
  notModifiedResponse,
} from './cache.ts';
import { OPENAPI_PATH } from './openapi.ts';
import { PROBLEM_MEDIA_TYPE, ProblemSchema } from './problem.ts';

export const DOCS_PATH = '/docs';

/** The PRD 8.6 path of the reference, outside the base path; it redirects to DOCS_PATH. */
export const DOCS_ALIAS_PATH = '/api/docs';

/** The one other host the page loads from. */
export const SCALAR_SCRIPT_HOST = 'cdn.jsdelivr.net';

/**
 * The Scalar reference script (the single-file standalone build). @scalar/api-reference 1.78.1 is
 * the release published with @scalar/hono-api-reference 0.12.20 and built on the same
 * @scalar/types 0.28.1. Pinned, so a deploy always loads the same script; jsDelivr serves an exact
 * version as immutable. Update it together with @scalar/hono-api-reference.
 */
export const SCALAR_SCRIPT = `https://${SCALAR_SCRIPT_HOST}/npm/@scalar/api-reference@1.78.1/dist/browser/standalone.js`;

const PAGE_TITLE = 'Terraluna API reference';

/** The relative document URL: OPENAPI_PATH next to DOCS_PATH, under the same base path. */
const DOCUMENT_URL = OPENAPI_PATH.replace(/^\//, '');

/**
 * A private app that only renders the page with the Scalar middleware, so the page never depends
 * on the request that first asks for it. It is never mounted.
 */
const renderer = new Hono().get(
  '/',
  Scalar({
    url: DOCUMENT_URL,
    pageTitle: PAGE_TITLE,
    // Given a cdn, the integration loads its single-file build with one <script src> (no module
    // chunks from the CDN at run time).
    cdn: SCALAR_SCRIPT,
    withDefaultFonts: false,
    telemetry: false,
    agent: { disabled: true },
    mcp: { disabled: true },
    showDeveloperTools: 'never',
  }),
);

export const docsRoute = createRoute({
  method: 'get',
  path: DOCS_PATH,
  middleware: [conditionalGet],
  summary: 'API reference',
  description:
    'The interactive Scalar reference for this API, rendered from openapi.json. The page loads ' +
    `the Scalar script from ${SCALAR_SCRIPT_HOST}, pinned to an exact version, and nothing else ` +
    'from another host. It depends on the deploy only, never on the bundle. Cached per deploy; ' +
    'the ETag is a hash of the bytes, and a matching If-None-Match is answered with 304. ' +
    `${DOCS_ALIAS_PATH} redirects here.`,
  responses: {
    200: {
      description: 'The reference page.',
      headers: deployCacheResponseHeaders(),
      content: { 'text/html': { schema: z.string().openapi({ description: 'An HTML page.' }) } },
    },
    304: notModifiedResponse,
    500: {
      description: 'The page could not be rendered, or an unexpected error occurred.',
      content: { [PROBLEM_MEDIA_TYPE]: { schema: ProblemSchema } },
    },
  },
});

/** The page as the Scalar integration renders it. */
async function renderPage(): Promise<string> {
  const response = await renderer.request('/');
  if (!response.ok) {
    throw new Error(`the Scalar integration rendered no page (status ${String(response.status)})`);
  }
  return response.text();
}

/**
 * The reference handler. The page is rendered once, on the first request; a render that fails is a
 * 500 problem and is tried again on the next request.
 */
export function docsHandler(): RouteHandler<typeof docsRoute> {
  let served: { body: string; headers: Record<string, string> } | undefined;
  return async (c) => {
    if (served === undefined) {
      const body = await renderPage();
      served = {
        body,
        headers: { 'Content-Type': 'text/html; charset=UTF-8', ...deployCacheHeaders(body) },
      };
    }
    return c.body(served.body, 200, served.headers);
  };
}

/**
 * The permanent redirect from DOCS_ALIAS_PATH to the reference at `location`. 308 (RFC 9110
 * 15.4.9) keeps the method, and the per-deploy Cache-Control bounds how long a client keeps it.
 */
export function docsRedirect(location: string): Handler {
  return () =>
    new Response(null, {
      status: 308,
      headers: { Location: location, 'Cache-Control': cacheControl('per-deploy') },
    });
}
