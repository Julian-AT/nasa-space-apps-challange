// The public read-only API (PRD 8.5, D24): a Hono app with base path /api/v1, typed and validated
// routes from @hono/zod-openapi, and RFC 9457 problem+json for every error (./problem.ts).
//
// The API reads the release bundle only through the T15-01 loader (lib/bundle.ts). The reader is
// injectable, so route tests pass fixtures through app.request() without bundle:pull, Next or the
// network; by default it is loadBundle() over apps/web/data/. A bundle file that is missing or
// invalid makes the loader throw, and the error becomes a 500 problem: no default is ever served.
//
// Only GET and HEAD are served (HEAD is GET without a body). Every other method, on any path the
// app receives, is a 405 problem with Allow: GET, HEAD.
import { OpenAPIHono } from '@hono/zod-openapi';
import type { Context, MiddlewareHandler, ValidationTargets } from 'hono';

import {
  BundleError,
  BundleFileError,
  type BundleFileProblem,
  loadBundle,
  type PulledBundle,
} from '../../lib/bundle.ts';
import { gatesHandler, gatesRoute } from './gates.ts';
import { healthHandler, healthRoute } from './health.ts';
import { manifestHandler, manifestRoute } from './manifest.ts';
import { ProblemError, problemResponse, VALIDATION_STATUS } from './problem.ts';
import { targetHandler, targetListHandler, targetListRoute, targetRoute } from './targets.ts';

export const API_BASE_PATH = '/api/v1';

/** Returns the pulled bundle, or throws (BundleFileError, MockBundleError, ...) as loadBundle does. */
export type BundleReader = () => PulledBundle;

export interface ApiOptions {
  /** How the API reads the bundle; loadBundle() over apps/web/data/ by default. */
  readonly reader?: BundleReader;
  /** Where errors that become a 500 are logged; console.error by default. */
  readonly log?: (error: unknown) => void;
}

const ALLOW = 'GET, HEAD';

/** 405 for any method other than GET and HEAD. Nothing else runs, so no bundle file is read. */
const readOnly: MiddlewareHandler = async (c, next) => {
  const { method } = c.req.raw;
  if (method === 'GET' || method === 'HEAD') {
    await next();
    return;
  }
  return problemResponse(
    c.req.raw,
    405,
    `${method} is not allowed: the Terraluna API is read-only and serves only GET and HEAD.`,
    { Allow: ALLOW },
  );
};

function notFound(c: Context): Response {
  const path = new URL(c.req.url).pathname;
  return problemResponse(c.req.raw, 404, `No route matches ${c.req.method} ${path}.`);
}

const TARGETS: Readonly<Record<keyof ValidationTargets, string>> = {
  param: 'path parameter',
  query: 'query parameter',
  header: 'header',
  cookie: 'cookie',
  json: 'body',
  form: 'form field',
};

/** Why a bundle file is refused, for the problem detail. */
const FILE_PROBLEMS: Readonly<Record<BundleFileProblem, string>> = {
  missing: 'missing',
  not_regular_file: 'not a regular file',
  not_listed: 'not listed in manifest.json',
  listed_twice: 'listed twice in manifest.json',
  bytes_mismatch: 'not the size manifest.json lists',
  sha256_mismatch: 'not the checksum manifest.json lists',
  invalid: 'invalid',
};

/**
 * The detail for a 500. A bundle file is named by its bundle path, which the manifest makes public,
 * with the reason; the loader's own message (with server paths) only goes to the log.
 */
function serverErrorDetail(error: Error): string {
  if (error instanceof BundleFileError) {
    return `The bundle file ${error.path} is ${FILE_PROBLEMS[error.reason]}, so the API serves no value in its place.`;
  }
  if (error instanceof BundleError) {
    return 'The release bundle could not be loaded, so the API serves no value in its place.';
  }
  return 'An unexpected error occurred.';
}

/** Hono hands only Error values to onError, so a reader that throws anything else is wrapped. */
function guarded(reader: BundleReader): BundleReader {
  return () => {
    try {
      return reader();
    } catch (error) {
      if (error instanceof Error) throw error;
      throw new Error('the bundle reader threw a value that is not an Error', { cause: error });
    }
  };
}

/** A new API app. Each call builds an independent app with its own routes and error handling. */
export function createApi({
  reader = () => loadBundle(),
  log = (error: unknown) => {
    console.error(error);
  },
}: ApiOptions = {}) {
  const root = new OpenAPIHono({
    // The shared validation hook for path and query parameters (VALIDATION_STATUS, ./problem.ts).
    defaultHook: (result, c) => {
      if (result.success) return;
      const where = TARGETS[result.target];
      const issues = result.error.issues
        .map((issue) => `${where} ${issue.path.map(String).join('.')}: ${issue.message}`)
        .join('; ');
      return problemResponse(c.req.raw, VALIDATION_STATUS, `Invalid request. ${issues}.`);
    },
  });
  // On the root, so the guard covers every path, not only those under the base path.
  root.use('*', readOnly);
  // basePath shares the root's router; notFound and onError must be set on the instance that
  // serves requests, because OpenAPIHono.basePath does not copy the not-found handler.
  const api = root.basePath(API_BASE_PATH);
  api.notFound(notFound);
  api.onError((error, c) => {
    // An expected problem (an id the bundle does not hold): answered as is, not logged.
    if (error instanceof ProblemError) return problemResponse(c.req.raw, error.status, error.detail);
    log(error);
    return problemResponse(c.req.raw, 500, serverErrorDetail(error));
  });
  const read = guarded(reader);
  api.openapi(healthRoute, healthHandler(read));
  api.openapi(manifestRoute, manifestHandler(read));
  api.openapi(gatesRoute, gatesHandler(read));
  api.openapi(targetListRoute, targetListHandler(read));
  api.openapi(targetRoute, targetHandler(read));
  return api;
}
