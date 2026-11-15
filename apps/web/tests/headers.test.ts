// Security headers (PRD 8.10, T15B-04): the CSP allow-list, cross-origin isolation on the two
// isolated routes only, and next.config.ts using the exported constants.
import { describe, expect, it } from 'vitest';

import {
  assetOrigin,
  COEP_HEADER,
  COOP_HEADER,
  COPERNICUS_DEM_ORIGIN,
  contentSecurityPolicy,
  ISOLATED_ROUTES,
  securityHeaders,
} from '../lib/headers.ts';
import nextConfig from '../next.config.ts';

function directives(csp: string): Map<string, string[]> {
  return new Map(
    csp.split('; ').map((d) => {
      const [name = '', ...values] = d.split(' ');
      return [name, values];
    }),
  );
}

/** Every host (scheme://host) a policy names. */
function hosts(csp: string): string[] {
  return [...csp.matchAll(/https?:\/\/[^\s;]+/g)].map((m) => m[0]);
}

const PROD = { NODE_ENV: 'production' } as const;

describe('contentSecurityPolicy', () => {
  it('allows only self and the Copernicus bucket when no asset base is set', () => {
    expect(hosts(contentSecurityPolicy(PROD))).toEqual([
      COPERNICUS_DEM_ORIGIN,
      COPERNICUS_DEM_ORIGIN,
    ]);
    expect(COPERNICUS_DEM_ORIGIN).toBe('https://copernicus-dem-30m.s3.amazonaws.com');
  });

  it('adds exactly the origin of NEXT_PUBLIC_ASSET_BASE and no other host', () => {
    const csp = contentSecurityPolicy({
      ...PROD,
      NEXT_PUBLIC_ASSET_BASE: 'https://assets.terraluna.test/bundles/2026/x?y=1',
    });
    const found = new Set(hosts(csp));
    expect(found).toEqual(new Set(['https://assets.terraluna.test', COPERNICUS_DEM_ORIGIN]));
    expect(csp).not.toContain('/bundles');
  });

  it('adds no host for a relative or empty asset base and rejects a malformed one', () => {
    expect(assetOrigin('/data')).toBeNull();
    expect(assetOrigin('')).toBeNull();
    expect(assetOrigin(undefined)).toBeNull();
    expect(() => assetOrigin('not a url')).toThrow(/NEXT_PUBLIC_ASSET_BASE/);
    expect(() => assetOrigin('ftp://example.test')).toThrow(/http/);
  });

  it('never allows the WorldCover bucket, a basemap host, a wildcard or unsafe-eval in production', () => {
    const csp = contentSecurityPolicy(PROD);
    expect(csp).not.toMatch(/worldcover/i);
    // 'wasm-unsafe-eval' (WebAssembly compilation for the probe's encoder) is not 'unsafe-eval'.
    expect(csp).not.toMatch(/(^|\s)'unsafe-eval'/);
    expect(csp).not.toMatch(/\*/);
    expect(csp).not.toMatch(/\bhttps?:(?![/])/);
    expect(csp).toContain('upgrade-insecure-requests');
  });

  it('is strict outside scripts and styles', () => {
    const d = directives(contentSecurityPolicy(PROD));
    expect(d.get('default-src')).toEqual(["'self'"]);
    expect(d.get('object-src')).toEqual(["'none'"]);
    expect(d.get('frame-ancestors')).toEqual(["'none'"]);
    expect(d.get('base-uri')).toEqual(["'self'"]);
    expect(d.get('form-action')).toEqual(["'self'"]);
    expect(d.get('script-src')).toEqual(["'self'", "'unsafe-inline'", "'wasm-unsafe-eval'"]);
    expect(d.get('connect-src')).toContain(COPERNICUS_DEM_ORIGIN);
  });

  it('permits unsafe-eval and websockets only in development', () => {
    const d = directives(contentSecurityPolicy({ NODE_ENV: 'development' }));
    expect(d.get('script-src')).toContain("'unsafe-eval'");
    expect(d.get('connect-src')).toContain('ws:');
    expect(contentSecurityPolicy({ NODE_ENV: 'development' })).not.toContain(
      'upgrade-insecure-requests',
    );
  });
});

describe('next.config.ts headers()', () => {
  it('uses the exported constants: the same rules lib/headers.ts builds', async () => {
    expect(await nextConfig.headers?.()).toEqual(securityHeaders(process.env));
  });

  it('sets COOP same-origin and COEP require-corp on /probe and /explore/earth only', async () => {
    const rules = (await nextConfig.headers?.()) ?? [];
    const isolated = rules.filter((r) => r.headers.some((h) => h.key === COOP_HEADER.key));
    // The probe worker's scripts carry COEP too, or the isolated page could not start the worker.
    expect(rules.find((r) => r.source === '/_next/static/:path*')?.headers).toEqual([COEP_HEADER]);
    expect(isolated.map((r) => r.source)).toEqual([...ISOLATED_ROUTES]);
    expect(ISOLATED_ROUTES).toEqual(['/probe', '/explore/earth']);
    for (const rule of isolated) {
      expect(rule.headers).toEqual([COOP_HEADER, COEP_HEADER]);
    }
    expect(COOP_HEADER.value).toBe('same-origin');
    expect(COEP_HEADER.value).toBe('require-corp');
  });

  it('carries the CSP on every route and nothing else', async () => {
    const rules = (await nextConfig.headers?.()) ?? [];
    const csp = rules.find((r) => r.source === '/:path*');
    expect(csp?.headers.map((h) => h.key)).toEqual(['Content-Security-Policy']);
    expect(rules.flatMap((r) => r.headers.map((h) => h.key)).sort()).toEqual([
      'Content-Security-Policy',
      'Cross-Origin-Embedder-Policy',
      'Cross-Origin-Embedder-Policy',
      'Cross-Origin-Embedder-Policy',
      'Cross-Origin-Opener-Policy',
      'Cross-Origin-Opener-Policy',
    ]);
  });
});
