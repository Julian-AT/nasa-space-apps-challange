// T15B-01: every small bundle file the web copies is validated with the @terraluna/schema
// contract its path maps to (lib/bundle-paths.ts), figures pass the SVG allowlist, and an optional
// file the manifest does not list reads as not present (null or absent from its map), never as an
// empty default. Records here are synthetic test input with fake ids.
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import {
  ALL_GATES_NOT_RUN,
  type FileSet,
  listing,
  manifestFor,
  REPO_ROOT,
  tempDir,
  toBytes,
  writeFiles,
} from '../tests/helpers.ts';
import { BUNDLE_PATH_ENTRIES, bundlePathEntry, svgRefusal } from './bundle-paths.ts';
import { BundleFileError, loadBundle, pullBundle } from './bundle.ts';

const VERSION = '2026-11-15T0900Z-3f9c2a1';
const ENV = { BUNDLE_VERSION: VERSION };

function release(files: FileSet): string {
  const root = tempDir();
  const listed: FileSet = { 'validation.json': toBytes(ALL_GATES_NOT_RUN), ...files };
  const manifest = { ...manifestFor(new Uint8Array()), files: listing(listed) };
  writeFiles(join(root, 'release', VERSION), { ...listed, 'manifest.json': toBytes(manifest) });
  return root;
}

function pull(files: FileSet) {
  const root = release(files);
  const outDir = join(tempDir(), 'data');
  return { bundle: pullBundle({ repoRoot: root, outDir, env: ENV }), outDir };
}

function pullError(files: FileSet): BundleFileError {
  try {
    pull(files);
  } catch (error) {
    expect(error).toBeInstanceOf(BundleFileError);
    return error as BundleFileError;
  }
  throw new Error('expected the pull to fail');
}

const SVG_OK = '<svg xmlns="http://www.w3.org/2000/svg"><defs><g id="a"/></defs><use href="#a"/></svg>';

describe('bundle records', () => {
  it('reads every optional file the manifest does not list as not present', () => {
    const { records } = pull({}).bundle;
    expect(records.sources).toBeNull();
    expect(records.targets).toBeNull();
    expect(records.modelCard).toBeNull();
    expect(records.metrics).toBeNull();
    expect(records.windows.size).toBe(0);
    expect(records.comparisons.size).toBe(0);
    expect(records.cards.size).toBe(0);
    expect(records.figures.size).toBe(0);
  });

  it('keeps an empty list as zero records, distinct from not present', () => {
    const { records } = pull({ 'targets.json': toBytes([]), 'windows/moon.json': toBytes([]) }).bundle;
    expect(records.targets).toEqual([]);
    expect(records.windows.get('moon')).toEqual([]);
    expect(records.windows.has('earth')).toBe(false);
  });

  it('fails a schema-invalid card, naming the file', () => {
    const error = pullError({ 'cards/moon-test__earth-test.json': toBytes({ card_id: 'moon-test__earth-test' }) });
    expect(error.path).toBe('cards/moon-test__earth-test.json');
    expect(error.message).toContain('cards/moon-test__earth-test.json');
    expect(error.message).toContain('CardRecord');
  });

  it('fails a schema-invalid targets.json, model card and metrics file, naming each', () => {
    for (const path of ['targets.json', 'model/model_card.json', 'report/metrics.json', 'sources.json']) {
      expect(pullError({ [path]: toBytes({ not: 'a record' }) }).path).toBe(path);
    }
  });

  it('copies a figure that passes the allowlist, and the loader reads it back', () => {
    const { bundle, outDir } = pull({ 'figures/test-card_A.svg': Buffer.from(SVG_OK) });
    expect(bundle.records.figures.get('figures/test-card_A.svg')).toBe(SVG_OK);
    expect(readFileSync(join(outDir, 'figures/test-card_A.svg'), 'utf8')).toBe(SVG_OK);
    const loaded = loadBundle({ dataDir: outDir, env: ENV });
    expect(loaded.records.figures.get('figures/test-card_A.svg')).toBe(SVG_OK);
  });

  it.each([
    ['a script element', '<svg><script>alert(1)</script></svg>'],
    ['an onload attribute', '<svg onload="alert(1)"></svg>'],
    ['a foreignObject', '<svg><foreignObject><p>x</p></foreignObject></svg>'],
    ['an https href', '<svg><image href="https://example.invalid/x.png"/></svg>'],
    ['an xlink:href to a file', '<svg><use xlink:href="other.svg#a"/></svg>'],
  ])('refuses a figure with %s, naming the file, and writes nothing', (_, svg) => {
    const error = pullError({ 'figures/test-card_A.svg': Buffer.from(svg) });
    expect(error.path).toBe('figures/test-card_A.svg');
    expect(error.message).toMatch(/allowlist/);
  });

  it('never copies a path without an entry in the path map', () => {
    const { outDir } = pull({ 'report/curves.json': toBytes({ any: 1 }), 'tiles/a/0/0/0.webp': Buffer.from('x') });
    expect(existsSync(join(outDir, 'report/curves.json'))).toBe(false);
    expect(existsSync(join(outDir, 'tiles'))).toBe(false);
  });
});

describe('the path map', () => {
  it('maps every template it names and nothing else', () => {
    expect(bundlePathEntry('cards/moon-a__earth-b.json')?.key).toBe('moon-a__earth-b');
    expect(bundlePathEntry('windows/moon.json')?.key).toBe('moon');
    expect(bundlePathEntry('windows/venus.json')).toBeNull();
    expect(bundlePathEntry('cards/nested/x.json')).toBeNull();
    expect(bundlePathEntry('model/encoder.onnx')).toBeNull();
  });

  it('agrees with the generated bundle-paths.json when packages/schema ships it', () => {
    const generated = join(REPO_ROOT, 'packages', 'schema', 'json', 'bundle-paths.json');
    if (!existsSync(generated)) return;
    const map = JSON.parse(readFileSync(generated, 'utf8')) as Record<string, string>;
    for (const entry of BUNDLE_PATH_ENTRIES) {
      expect(map[entry.template], entry.template).toBe(entry.contract);
    }
  });

  it('accepts a plain figure and refuses url() references to other files', () => {
    expect(svgRefusal(SVG_OK)).toBeNull();
    expect(svgRefusal('<svg><rect fill="url(#g)"/></svg>')).toBeNull();
    expect(svgRefusal('<svg><rect style="fill:url(http://x/y)"/></svg>')).not.toBeNull();
  });
});
