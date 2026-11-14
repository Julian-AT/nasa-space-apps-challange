// The cache policies of PRD 8.5's Cache column for responses read from the release bundle: public,
// a documented max-age, immutable while fresh, and an ETag derived from bundle_version (T15-03).
import { describe, expect, it } from 'vitest';

import { bundleCacheHeaders, bundleETag, PER_BUNDLE_MAX_AGE, PER_DEPLOY_MAX_AGE } from './cache.ts';

describe('cache policies', () => {
  it('keeps per-deploy responses fresh for 60 s, so a new deploy shows within a minute', () => {
    expect(PER_DEPLOY_MAX_AGE).toBe(60);
    expect(bundleCacheHeaders('per-deploy', 'mock-0')).toEqual({
      'Cache-Control': 'public, max-age=60, immutable',
      ETag: 'W/"mock-0"',
    });
  });

  it('keeps per-bundle responses fresh for 300 s', () => {
    expect(PER_BUNDLE_MAX_AGE).toBe(300);
    expect(bundleCacheHeaders('per-bundle', 'mock-0')).toEqual({
      'Cache-Control': 'public, max-age=300, immutable',
      ETag: 'W/"mock-0"',
    });
  });
});

describe('bundleETag', () => {
  it('is a weak entity tag holding the bundle_version', () => {
    expect(bundleETag('2026-11-15T0900Z-3f9c2a1')).toBe('W/"2026-11-15T0900Z-3f9c2a1"');
  });

  it('differs between bundles, so a new bundle is never answered with 304', () => {
    expect(bundleETag('mock-0')).not.toBe(bundleETag('mock-1'));
  });

  // BundleManifest.bundle_version already allows only [A-Za-z0-9._-]; this guards the header.
  for (const version of ['', 'a"b', 'a b', 'a,b', 'é']) {
    it(`refuses ${JSON.stringify(version)}, which is not a bundle_version`, () => {
      expect(() => bundleETag(version)).toThrow(/bundle_version/);
    });
  }
});
