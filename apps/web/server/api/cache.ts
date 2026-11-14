// HTTP caching for the API's responses (PRD 8.5, Cache column). There are two kinds of ETag: a
// weak bundle ETag for responses read from the release bundle (bundleETag), and a strong content
// ETag for responses that only the deploy fixes (contentETag, last paragraph).
//
// A published bundle is immutable and any change gets a new bundle_version (PRD 8.4), so for one
// bundle_version these responses never change: that is their immutable semantics. The bundle
// ETag holds the bundle_version, and a request whose If-None-Match names it is answered 304 by
// Hono's ETag middleware (conditionalGet below) after the handler has read and validated the
// bundle, so nothing invalid is ever confirmed by a 304.
//
// The bundle ETag is weak (RFC 9110 section 8.8.1): bundle_version identifies the content, not
// the bytes of one serialization, which another deploy of the same bundle may produce differently.
// If-None-Match always uses the weak comparison, so 304 works as with a strong tag.
//
// The URLs carry no bundle_version, and a deploy may pin a new bundle (PRD 8.9), so a client must
// look again after a while. `public, max-age=N, immutable` lets any cache reuse a response for N
// seconds without asking (immutable: not even on reload, RFC 8246), and then revalidate it, which
// costs a 304 while the bundle is unchanged. CDN-specific directives (s-maxage, CDN-Cache-Control)
// are not set here; they belong with the Vercel mount.
//
// The mock bundle keeps bundle_version mock-0 while its fixture changes between commits, so a
// client may hold an older mock across preview deploys. Mock bundles are placeholders and are
// never served on production.
//
// /openapi.json and /docs (T15-06) are per deploy too, but they never read the bundle: the code of
// a deploy fixes their bytes. Their ETag is therefore a strong hash of those bytes (contentETag),
// which changes only when a deploy changes the document or the page.
import { createHash } from 'node:crypto';

import { BundleManifestSchema } from '@terraluna/schema';
import { etag } from 'hono/etag';

/**
 * per deploy: /manifest, /openapi.json and /docs. The manifest names the bundle a deploy serves,
 * and clients read it to find a new bundle_version; the document and the reference change with the
 * code of a deploy. So they stay fresh for one minute only.
 */
export const PER_DEPLOY_MAX_AGE = 60;

/**
 * per bundle: /gates and the other records of one bundle. They change only with the bundle, which
 * a client sees in /manifest first, so they stay fresh for five minutes.
 */
export const PER_BUNDLE_MAX_AGE = 300;

export type CachePolicy = 'per-deploy' | 'per-bundle';

const MAX_AGE: Readonly<Record<CachePolicy, number>> = {
  'per-deploy': PER_DEPLOY_MAX_AGE,
  'per-bundle': PER_BUNDLE_MAX_AGE,
};

/** The weak ETag of everything read from one bundle: W/"<bundle_version>". */
export function bundleETag(bundleVersion: string): string {
  // The schema allows only [A-Za-z0-9._-], all valid inside an entity tag (RFC 9110 8.8.3).
  if (!BundleManifestSchema.shape.bundle_version.safeParse(bundleVersion).success) {
    throw new Error(
      `not a bundle_version, so it cannot be an ETag: ${JSON.stringify(bundleVersion)}`,
    );
  }
  return `W/"${bundleVersion}"`;
}

/** The Cache-Control value of a policy: `public, max-age=N, immutable`. */
export function cacheControl(policy: CachePolicy): string {
  return `public, max-age=${String(MAX_AGE[policy])}, immutable`;
}

/** Cache-Control and ETag for a response read from the bundle with this bundle_version. */
export function bundleCacheHeaders(
  policy: CachePolicy,
  bundleVersion: string,
): { 'Cache-Control': string; ETag: string } {
  return { 'Cache-Control': cacheControl(policy), ETag: bundleETag(bundleVersion) };
}

/**
 * The strong ETag of a body: the base64url SHA-256 of its UTF-8 bytes, quoted. Strong (RFC 9110
 * 8.8.1), because it names these exact bytes; base64url uses only characters an entity tag allows.
 */
export function contentETag(body: string): string {
  return `"${createHash('sha256').update(body, 'utf8').digest('base64url')}"`;
}

/** Cache-Control and ETag for a body that a deploy fixes and the bundle never changes. */
export function deployCacheHeaders(body: string): { 'Cache-Control': string; ETag: string } {
  return { 'Cache-Control': cacheControl('per-deploy'), ETag: contentETag(body) };
}

/** The Cache-Control and ETag headers of a 200, for the OpenAPI document. */
export function bundleCacheResponseHeaders(policy: CachePolicy) {
  return {
    'Cache-Control': {
      description: `${cacheControl(policy)} (${policy.replace('-', ' ')}).`,
      schema: { type: 'string' as const },
    },
    ETag: {
      description: 'W/"<bundle_version>": the same for everything read from one bundle.',
      schema: { type: 'string' as const },
    },
  };
}

/** The Cache-Control and ETag headers of a 200 fixed by the deploy, for the OpenAPI document. */
export function deployCacheResponseHeaders() {
  return {
    'Cache-Control': {
      description: `${cacheControl('per-deploy')} (per deploy).`,
      schema: { type: 'string' as const },
    },
    ETag: {
      description: 'A strong hash of the body, which only a new deploy changes.',
      schema: { type: 'string' as const },
    },
  };
}

/**
 * Answers a GET or HEAD whose If-None-Match matches the response's ETag (or is *) with 304 Not
 * Modified, keeping Cache-Control and ETag (RFC 9110 15.4.5). Error responses are never touched.
 * Every route that uses it sets its own ETag, so the middleware never hashes a body.
 */
export const conditionalGet = etag();

/** The 304 response, for the OpenAPI document. */
export const notModifiedResponse = {
  description: 'If-None-Match names the current ETag: the cached response is still valid.',
} as const;
