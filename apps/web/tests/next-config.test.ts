// outputFileTracingIncludes in next.config.ts (PRD 8.5, 8.9, T15-07). The API route reads the
// pulled bundle from apps/web/data/ at a path built at run time, so the config names those files
// for the route's server trace, which Vercel uses to decide what the function ships with.
//
// Vercel cannot be run here. This test applies the config with the matcher Next 16's JavaScript
// tracer uses (next/dist/build/collect-build-traces.js, the webpack build): each key is matched
// with Next's picomatch ({ dot, contains }) against normalizeAppPath(<entry name>), and each value
// is expanded with Next's glob ({ cwd: project dir, nodir, dot }). Turbopack, the default builder,
// applies the option in native code; a make web-build with this key put the data files in
// .next/server/app/api/[[...route]]/route.js.nft.json (T15-07 notes).
import { mkdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';

import { describe, expect, it } from 'vitest';

import nextConfig from '../next.config.ts';
import { tempDir } from './helpers.ts';

const require = createRequire(import.meta.url);

type Matcher = (path: string) => boolean;
const picomatch = require('next/dist/compiled/picomatch') as (
  glob: string,
  options: { dot: boolean; contains: boolean },
) => Matcher;
const nextGlob = require('next/dist/compiled/glob') as (
  pattern: string,
  options: { cwd: string; nodir: boolean; dot: boolean },
  callback: (error: Error | null, files: string[]) => void,
) => void;
const { normalizeAppPath } = require('next/dist/shared/lib/router/utils/app-paths') as {
  normalizeAppPath: (route: string) => string;
};

/** The build entry names of app/api/[[...route]]/route.ts and of the / page. */
const API_ENTRY = 'app/api/[[...route]]/route';
const PAGE_ENTRY = 'app/page';

function glob(pattern: string, cwd: string): Promise<string[]> {
  return new Promise((resolve, reject) => {
    nextGlob(pattern, { cwd, nodir: true, dot: true }, (error, files) => {
      if (error) reject(error);
      else resolve(files);
    });
  });
}

/** The include globs the tracer applies to an entry. */
function includesFor(entry: string): string[] {
  const route = normalizeAppPath(entry);
  const includes = nextConfig.outputFileTracingIncludes ?? {};
  return Object.entries(includes)
    .filter(([key]) => picomatch(key, { dot: true, contains: true })(route))
    .flatMap(([, globs]) => globs);
}

/** Every file bundle:pull may write (lib/bundle.ts isPulledPath), and files it never writes. */
const PULLED = [
  'data/manifest.json',
  'data/validation.json',
  'data/sources.json',
  'data/targets.json',
  'data/comparisons/test-only-target.json',
  'data/windows/earth.json',
];
const NOT_PULLED = ['app/page.tsx', 'lib/bundle.ts', 'next.config.ts', 'package.json'];

describe('outputFileTracingIncludes', () => {
  it('has a key that matches the API route', () => {
    expect(includesFor(API_ENTRY).length).toBeGreaterThan(0);
  });

  it('adds nothing to the / page, which is static', () => {
    expect(includesFor(PAGE_ENTRY)).toEqual([]);
  });

  it('names the brackets and dots of [[...route]] literally, so no other route matches', () => {
    for (const entry of ['app/api/route', 'app/api/x/route', 'app/api/[[...rest]]/route']) {
      expect(includesFor(entry), entry).toEqual([]);
    }
  });

  it('includes every file bundle:pull writes into data/ and nothing outside it', async () => {
    const project = tempDir();
    for (const path of [...PULLED, ...NOT_PULLED]) {
      const file = join(project, ...path.split('/'));
      mkdirSync(dirname(file), { recursive: true });
      writeFileSync(file, '{}\n');
    }
    const found = new Set<string>();
    for (const pattern of includesFor(API_ENTRY)) {
      for (const file of await glob(pattern, project)) found.add(file.replace(/^\.\//, ''));
    }
    expect([...found].sort()).toEqual([...PULLED].sort());
  });
});
