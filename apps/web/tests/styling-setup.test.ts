// The styling layer's configuration (PRD 8.2, 8.10, D35): Tailwind CSS v4 through
// @tailwindcss/postcss with no tailwind.config file, shadcn/ui's components.json for Radix and
// React Server Components, and none of the heavy map, 3D, chart or ONNX dependencies, webfonts
// or image assets in the web shell.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join, relative } from 'node:path';
import { pathToFileURL } from 'node:url';

import { describe, expect, it } from 'vitest';

import { WEB_DIR } from './helpers.ts';

function readJson(path: string): unknown {
  return JSON.parse(readFileSync(join(WEB_DIR, path), 'utf8')) as unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function record(value: unknown, what: string): Record<string, unknown> {
  if (!isRecord(value)) throw new Error(`${what} is not an object`);
  return value;
}

const packageJson = record(readJson('package.json'), 'package.json');
const dependencies = {
  ...record(packageJson.dependencies, 'dependencies'),
  ...record(packageJson.devDependencies, 'devDependencies'),
};

/** Files under apps/web that are the app's own, not installed, built or pulled from a bundle. */
function ownFiles(dir = WEB_DIR): string[] {
  const skip = new Set(['node_modules', '.next', '.turbo', 'data']);
  const files: string[] = [];
  for (const name of readdirSync(dir)) {
    if (skip.has(name)) continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) files.push(...ownFiles(path));
    else files.push(relative(WEB_DIR, path));
  }
  return files;
}

describe('Tailwind CSS v4 through PostCSS', () => {
  it('pins tailwindcss and @tailwindcss/postcss to the same exact 4.x version', () => {
    const tailwind = dependencies.tailwindcss;
    expect(tailwind).toMatch(/^4\.\d+\.\d+$/);
    expect(dependencies['@tailwindcss/postcss']).toBe(tailwind);
    expect(dependencies.postcss).toMatch(/^8\.\d+\.\d+$/);
  });

  it('registers only the @tailwindcss/postcss plugin in postcss.config.mjs', async () => {
    const url = pathToFileURL(join(WEB_DIR, 'postcss.config.mjs')).href;
    const config = record(((await import(url)) as { default: unknown }).default, 'postcss config');
    expect(Object.keys(record(config.plugins, 'plugins'))).toEqual(['@tailwindcss/postcss']);
  });

  it('has no tailwind.config file, which Tailwind v4 does not need', () => {
    const configs = readdirSync(WEB_DIR).filter((name) => name.startsWith('tailwind.config'));
    expect(configs).toEqual([]);
  });

  it('imports Tailwind first in app/globals.css', () => {
    const css = readFileSync(join(WEB_DIR, 'app', 'globals.css'), 'utf8');
    const first = css.replace(/\/\*[\s\S]*?\*\//g, '').trim().split(';')[0];
    expect(first).toMatch(/^@import "tailwindcss"( source\(none\))?$/);
  });

  it('loads app/globals.css from the root layout', () => {
    const layout = readFileSync(join(WEB_DIR, 'app', 'layout.tsx'), 'utf8');
    expect(layout).toMatch(/^import '\.\/globals\.css';$/m);
  });
});

describe('shadcn/ui components.json', () => {
  const config = record(readJson('components.json'), 'components.json');
  const tailwind = record(config.tailwind, 'tailwind');
  const aliases = record(config.aliases, 'aliases');
  const tsconfig = record(readJson('tsconfig.json'), 'tsconfig.json');
  const paths = record(record(tsconfig.compilerOptions, 'compilerOptions').paths, 'paths');

  /** Resolves an alias through tsconfig.json paths, as tsc, Next.js and Vitest do. */
  function resolveAlias(alias: string): string | null {
    for (const [pattern, targets] of Object.entries(paths)) {
      if (!pattern.endsWith('/*') || !Array.isArray(targets)) continue;
      const prefix = pattern.slice(0, -1);
      const [target] = targets as unknown[];
      if (alias.startsWith(prefix) && typeof target === 'string' && target.endsWith('/*')) {
        return join(WEB_DIR, target.slice(0, -1), alias.slice(prefix.length));
      }
    }
    return null;
  }

  it('uses a Radix style, React Server Components and TSX', () => {
    expect(config.$schema).toBe('https://ui.shadcn.com/schema.json');
    expect(config.style).toMatch(/^radix-/);
    expect(config.rsc).toBe(true);
    expect(config.tsx).toBe(true);
  });

  it('is set up for Tailwind v4 with CSS variables in app/globals.css', () => {
    expect(tailwind.config).toBe('');
    expect(tailwind.css).toBe('app/globals.css');
    expect(existsSync(join(WEB_DIR, 'app', 'globals.css'))).toBe(true);
    expect(tailwind.cssVariables).toBe(true);
  });

  it('declares only aliases that tsconfig.json paths resolve', () => {
    expect(Object.keys(aliases).sort()).toEqual(['components', 'hooks', 'lib', 'ui', 'utils']);
    for (const [name, alias] of Object.entries(aliases)) {
      expect(typeof alias, name).toBe('string');
      expect(resolveAlias(String(alias)), `${name}: ${String(alias)}`).not.toBeNull();
    }
  });

  it('points the components, ui, lib and utils aliases at files that exist', () => {
    const at = (name: string): string => resolveAlias(String(aliases[name])) ?? '';
    expect(statSync(at('components')).isDirectory()).toBe(true);
    expect(statSync(at('ui')).isDirectory()).toBe(true);
    expect(statSync(at('lib')).isDirectory()).toBe(true);
    expect(existsSync(`${at('utils')}.ts`)).toBe(true);
  });
});

describe('the web shell stays light (PRD 8.10, D35)', () => {
  // Map, 3D, chart and ONNX libraries load only on the pages that use them (T7B). The explorer
  // adds deck.gl (maps, /explore/*) and Observable Plot (/compare, cards), loaded by dynamic import
  // only; the e2e budget test checks they never reach /, /methods, /data or /model. three.js,
  // MapLibre (LIC-02: no third-party basemap) and onnxruntime stay out of the web package.
  const HEAVY = [
    /^deck\.gl$/,
    /^@deck\.gl\//,
    /^three$/,
    /^@react-three\//,
    /^onnxruntime-/,
    /^@observablehq\/plot$/,
    /^maplibre-gl$/,
    /^react-map-gl$/,
  ];

  it('adds only the explorer map and chart dependencies, and no 3D, basemap or ONNX one', () => {
    const heavy = Object.keys(dependencies).filter((name) => HEAVY.some((re) => re.test(name)));
    expect(heavy.sort()).toEqual([
      '@deck.gl/core',
      '@deck.gl/geo-layers',
      '@deck.gl/layers',
      '@deck.gl/react',
      '@observablehq/plot',
      // The live probe's encoder (T13-03): imported only by lib/probe/embedding.ts, dynamically,
      // inside the probe Web Worker, so it never enters a page's initial JavaScript.
      'onnxruntime-web',
    ]);
  });

  it('pins every dependency exactly (PRD 8.2)', () => {
    for (const [name, version] of Object.entries(dependencies)) {
      expect(String(version), name).toMatch(/^(\d+\.\d+\.\d+|workspace:\*)$/);
    }
  });

  it('self-hosts its fonts: next/font/google only, no external font host or @font-face in the page code', () => {
    // The page code only: the tests themselves name what they look for. next/font/google downloads
    // the files at build time and serves them from this origin, so the CSP stays 'self'.
    const pageCode = ownFiles().filter(
      (path) => /\.(tsx?|css)$/.test(path) && !path.startsWith('tests/') && !/\.test\.tsx?$/.test(path),
    );
    expect(pageCode).toContain('app/globals.css');
    for (const file of pageCode) {
      const text = readFileSync(join(WEB_DIR, file), 'utf8');
      expect(text, file).not.toMatch(/fonts\.googleapis|fonts\.gstatic|@font-face|<link[^>]+font/);
      if (file !== 'app/layout.tsx' && !file.endsWith('.css')) expect(text, file).not.toMatch(/next\/font/);
    }
    const layout = readFileSync(join(WEB_DIR, 'app', 'layout.tsx'), 'utf8');
    expect(layout).toMatch(/from 'next\/font\/google'/);
    for (const family of ['Geist', 'Geist_Mono', 'Instrument_Serif', 'Michroma']) {
      expect(layout, family).toContain(family);
    }
  });

  it('ships no image or illustration asset', () => {
    const images = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.svg', '.ico']);
    // public/assets/<v>/tiles holds the bundle's generated hillshade tiles (bundle:pull, gitignored), not artwork.
    const own = ownFiles().filter((path) => !path.replaceAll('\\', '/').includes('public/assets/'));
    expect(own.filter((path) => images.has(extname(path).toLowerCase()))).toEqual([]);
  });
});
