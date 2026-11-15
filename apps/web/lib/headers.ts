// Security headers of the web app (PRD 8.10, 8.5, 8.6): a strict Content-Security-Policy on every
// route, and cross-origin isolation (COOP same-origin, COEP require-corp) on the two routes that
// need SharedArrayBuffer-grade isolation for the browser worker: /probe and /explore/earth.
// next.config.ts builds headers() from these constants, and the live-probe code (T13-06) imports
// the same values, so a header and the code that depends on it cannot drift apart.
//
// Hosts. The policy allows 'self', the origin of NEXT_PUBLIC_ASSET_BASE when it is set, and the
// Copernicus DEM bucket the probe reads. Nothing else: the WorldCover bucket joins only when the
// G0 record shows it CORS-readable, and a third-party basemap host only after LIC-02; both are
// absent until then (ASSET_HOSTS_PENDING documents them, and no code path adds them).
//
// Inline script and style. Next 16 emits inline bootstrap and RSC payload scripts and Tailwind
// and Next emit inline style attributes, and a static site has no per-request nonce. Next's
// "Content Security Policy" guide for applications without nonces (guides/content-security-policy,
// "Without Nonces") therefore allows 'unsafe-inline' for script-src and style-src. This is a
// known weakening that it accepts; the alternative, nonces, makes every page dynamic, and the
// experimental hash-based SRI mode is not used. 'unsafe-eval' is never allowed in production: it
// is added only for next dev, whose React build evaluates code for error stacks.
// Every other directive is as strict as the guide's: no object, frame, base or form-action
// beyond 'self', and no framing.

/** The Copernicus DEM 30 m bucket the probe worker reads (PRD 5, 8.10). */
export const COPERNICUS_DEM_ORIGIN = 'https://copernicus-dem-30m.s3.amazonaws.com';

/**
 * Hosts that are deliberately not allowed yet: the ESA WorldCover bucket (until the G0 record
 * shows it CORS-readable) and any third-party basemap (until LIC-02). Listed for the tests and
 * the reader; never added to the policy.
 */
export const ASSET_HOSTS_PENDING = ['worldcover', 'basemap'] as const;

/** Routes that are cross-origin isolated (Next source patterns). */
export const ISOLATED_ROUTES = ['/probe', '/explore/earth'] as const;

export const COOP_HEADER = { key: 'Cross-Origin-Opener-Policy', value: 'same-origin' } as const;
export const COEP_HEADER = { key: 'Cross-Origin-Embedder-Policy', value: 'require-corp' } as const;

export const CSP_HEADER_NAME = 'Content-Security-Policy';

export interface HeaderEnv {
  readonly NEXT_PUBLIC_ASSET_BASE?: string | undefined;
  readonly NODE_ENV?: string | undefined;
}

export interface NextHeader {
  readonly key: string;
  readonly value: string;
}

export interface NextHeaderRule {
  readonly source: string;
  readonly headers: NextHeader[];
}

/**
 * The origin of NEXT_PUBLIC_ASSET_BASE, or null when it is unset or empty. A value that is not an
 * absolute http(s) URL throws: a typo must not silently drop (or widen) the allow-list. A
 * relative base such as "/data" is the same origin and adds no host.
 */
export function assetOrigin(base: string | undefined): string | null {
  if (base === undefined || base === '' || base.startsWith('/')) return null;
  let url: URL;
  try {
    url = new URL(base);
  } catch {
    throw new Error(`NEXT_PUBLIC_ASSET_BASE is not an absolute URL or a /path: ${base}`);
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') {
    throw new Error(`NEXT_PUBLIC_ASSET_BASE must be http(s): ${base}`);
  }
  return url.origin;
}

/** The Content-Security-Policy value for the given environment. */
export function contentSecurityPolicy(env: HeaderEnv = {}): string {
  const dev = env.NODE_ENV === 'development';
  const asset = assetOrigin(env.NEXT_PUBLIC_ASSET_BASE);
  const hosts = [...(asset === null ? [] : [asset]), COPERNICUS_DEM_ORIGIN];
  const directives: [string, string[]][] = [
    ['default-src', ["'self'"]],
    // 'unsafe-inline': see the module comment. 'unsafe-eval' only for next dev.
    ['script-src', ["'self'", "'unsafe-inline'", ...(dev ? ["'unsafe-eval'"] : [])]],
    ['style-src', ["'self'", "'unsafe-inline'"]],
    ['img-src', ["'self'", 'blob:', 'data:', ...hosts]],
    ['font-src', ["'self'"]],
    ['connect-src', ["'self'", ...hosts, ...(dev ? ['ws:', 'wss:'] : [])]],
    ['worker-src', ["'self'", 'blob:']],
    ['object-src', ["'none'"]],
    ['base-uri', ["'self'"]],
    ['form-action', ["'self'"]],
    ['frame-ancestors', ["'none'"]],
  ];
  const lines = directives.map(([name, values]) => `${name} ${values.join(' ')}`);
  if (!dev) lines.push('upgrade-insecure-requests');
  return lines.join('; ');
}

/** The headers() rules: CSP everywhere, COOP and COEP on the isolated routes only. */
export function securityHeaders(env: HeaderEnv = {}): NextHeaderRule[] {
  return [
    {
      source: '/:path*',
      headers: [{ key: CSP_HEADER_NAME, value: contentSecurityPolicy(env) }],
    },
    ...ISOLATED_ROUTES.map((source) => ({ source, headers: [COOP_HEADER, COEP_HEADER] })),
  ];
}
