// bundle:pull and the build-time loader (PRD 8.4, 8.5, 8.9): the source is the mock bundle when
// BUNDLE_VERSION is unset or empty and release/<BUNDLE_VERSION>/ otherwise; both files are
// validated; a missing or invalid requested bundle never falls back to the mock; a mock bundle is
// refused when VERCEL_ENV=production.
import { existsSync, readdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { BundleError, loadBundle, pullBundle, selectBundle } from '../lib/bundle.ts';
import {
  ALL_GATES_NOT_RUN,
  addRelease,
  gate,
  MOCK_DIR,
  manifestFor,
  releaseFiles,
  REPO_ROOT,
  repoWithMock,
  tempDir,
  toBytes,
  writeBundle,
} from './helpers.ts';

const VERSION = '2026-11-15T0900Z-3f9c2a1';

function pulledNames(outDir: string): string[] {
  return existsSync(outDir) ? readdirSync(outDir).sort() : [];
}

function expectPullError(
  root: string,
  env: Record<string, string | undefined>,
  message: RegExp,
): string {
  const outDir = join(tempDir(), 'data');
  expect(() => pullBundle({ repoRoot: root, outDir, env })).toThrow(BundleError);
  expect(() => pullBundle({ repoRoot: root, outDir, env })).toThrow(message);
  expect(pulledNames(outDir)).toStrictEqual([]);
  return outDir;
}

describe('selectBundle', () => {
  it('selects the mock bundle when BUNDLE_VERSION is unset or empty', () => {
    expect(selectBundle({})).toStrictEqual({ kind: 'mock' });
    expect(selectBundle({ BUNDLE_VERSION: undefined })).toStrictEqual({ kind: 'mock' });
    expect(selectBundle({ BUNDLE_VERSION: '' })).toStrictEqual({ kind: 'mock' });
  });

  it('selects release/<BUNDLE_VERSION>/ otherwise', () => {
    expect(selectBundle({ BUNDLE_VERSION: VERSION })).toStrictEqual({
      kind: 'release',
      version: VERSION,
    });
  });

  it.each(['../mock', 'a/b', '.hidden', ' ', 'v1 ', 'v1\n', '..'])(
    'refuses BUNDLE_VERSION %j, which is not a bundle_version',
    (version) => {
      expect(() => selectBundle({ BUNDLE_VERSION: version })).toThrow(BundleError);
    },
  );

  it.each(['prod', 'Production', ' production'])('refuses an unknown VERCEL_ENV %j', (vercelEnv) => {
    expect(() => selectBundle({ VERCEL_ENV: vercelEnv })).toThrow(/VERCEL_ENV/);
  });
});

describe('pullBundle from the mock bundle', () => {
  it('throws on a mock bundle when VERCEL_ENV=production and copies nothing', () => {
    expectPullError(REPO_ROOT, { VERCEL_ENV: 'production' }, /mock.*VERCEL_ENV=production/s);
  });

  it('throws on a mock bundle when VERCEL_ENV=production even with an empty BUNDLE_VERSION', () => {
    expectPullError(REPO_ROOT, { VERCEL_ENV: 'production', BUNDLE_VERSION: '' }, /mock/);
  });

  it.each([
    { name: 'preview', env: { VERCEL_ENV: 'preview' } },
    { name: 'development', env: { VERCEL_ENV: 'development' } },
    { name: 'unset', env: {} },
    { name: 'empty', env: { VERCEL_ENV: '' } },
  ])('copies both files byte for byte when VERCEL_ENV is $name', ({ env }) => {
    const outDir = join(tempDir(), 'data');
    const bundle = pullBundle({ repoRoot: REPO_ROOT, outDir, env });
    expect(bundle.manifest.mock).toBe(true);
    expect(pulledNames(outDir)).toStrictEqual(['manifest.json', 'validation.json']);
    for (const name of ['manifest.json', 'validation.json']) {
      expect(readFileSync(join(outDir, name))).toStrictEqual(readFileSync(join(MOCK_DIR, name)));
    }
  });

  it('overwrites files from an earlier pull', () => {
    const root = repoWithMock();
    const outDir = join(tempDir(), 'data');
    addRelease(root, VERSION, releaseFiles());
    pullBundle({ repoRoot: root, outDir, env: { BUNDLE_VERSION: VERSION } });
    pullBundle({ repoRoot: root, outDir, env: {} });
    expect(readFileSync(join(outDir, 'manifest.json'))).toStrictEqual(
      readFileSync(join(MOCK_DIR, 'manifest.json')),
    );
  });

  it('refuses a mock bundle directory whose manifest is not labelled mock', () => {
    const root = tempDir();
    writeBundle(join(root, 'fixtures', 'mock-bundle'), releaseFiles());
    expectPullError(root, {}, /mock=false/);
  });

  it('throws when the mock bundle is missing', () => {
    expectPullError(tempDir(), {}, /fixtures\/mock-bundle\/manifest\.json/);
  });
});

describe('pullBundle from a release bundle', () => {
  it('copies a valid release bundle, also on production', () => {
    const root = repoWithMock();
    const files = releaseFiles();
    addRelease(root, VERSION, files);
    const outDir = join(tempDir(), 'data');
    const env = { BUNDLE_VERSION: VERSION, VERCEL_ENV: 'production' };
    const bundle = pullBundle({ repoRoot: root, outDir, env });
    expect(bundle.manifest.bundle_version).toBe(VERSION);
    expect(bundle.manifest.mock).toBe(false);
    expect(readFileSync(join(outDir, 'manifest.json'))).toStrictEqual(files.manifest);
    expect(readFileSync(join(outDir, 'validation.json'))).toStrictEqual(files.validation);
  });

  it('throws on a missing requested BUNDLE_VERSION instead of using the mock', () => {
    const root = repoWithMock();
    expectPullError(root, { BUNDLE_VERSION: VERSION }, /release\/2026-11-15T0900Z-3f9c2a1/);
    expectPullError(root, { BUNDLE_VERSION: VERSION, VERCEL_ENV: 'preview' }, /never falls back/);
  });

  it('throws on a requested bundle without validation.json', () => {
    const root = repoWithMock();
    addRelease(root, VERSION, { manifest: releaseFiles().manifest });
    expectPullError(root, { BUNDLE_VERSION: VERSION }, /validation\.json/);
  });

  it.each([
    { name: 'a wrong field type', fields: { mock: 'no' } },
    { name: 'an unknown field', fields: { extra: 1 } },
    { name: 'a missing field', fields: { shipping_mode: undefined } },
    { name: 'an invalid shipping mode', fields: { shipping_mode: 'D' } },
    { name: 'an uppercase sha256', fields: { files: [{ bytes: 1, path: 'validation.json', sha256: 'A'.repeat(64) }] } },
  ])('throws on an invalid manifest: $name', ({ fields }) => {
    const root = repoWithMock();
    const files = releaseFiles();
    const manifest = { ...manifestFor(files.validation), ...fields };
    addRelease(root, VERSION, { manifest: toBytes(manifest), validation: files.validation });
    expectPullError(root, { BUNDLE_VERSION: VERSION }, /manifest\.json/);
  });

  it.each([
    { name: 'invalid JSON', text: '{"mock": false,' },
    { name: 'a NaN', text: '{"mock": NaN}' },
    { name: 'a duplicate key', text: '{"mock": true, "mock": false}' },
    { name: 'an array', text: '[]' },
  ])('throws on a manifest with $name', ({ text }) => {
    const root = repoWithMock();
    addRelease(root, VERSION, {
      manifest: Buffer.from(text, 'utf8'),
      validation: releaseFiles().validation,
    });
    expectPullError(root, { BUNDLE_VERSION: VERSION }, /manifest\.json/);
  });

  it('throws when the manifest names another bundle_version', () => {
    const root = repoWithMock();
    addRelease(root, VERSION, releaseFiles({ bundle_version: 'other-version' }));
    expectPullError(root, { BUNDLE_VERSION: VERSION }, /other-version/);
  });

  it('throws on a release bundle labelled mock, whatever VERCEL_ENV is', () => {
    const root = repoWithMock();
    addRelease(root, VERSION, releaseFiles({ mock: true }));
    expectPullError(root, { BUNDLE_VERSION: VERSION }, /mock=true/);
    expectPullError(root, { BUNDLE_VERSION: VERSION, VERCEL_ENV: 'production' }, /mock/);
  });

  it('throws when validation.json is not listed in the manifest', () => {
    const root = repoWithMock();
    const files = releaseFiles();
    addRelease(root, VERSION, {
      manifest: toBytes(manifestFor(files.validation, { files: [] })),
      validation: files.validation,
    });
    expectPullError(root, { BUNDLE_VERSION: VERSION }, /not listed/);
  });

  it('throws when validation.json does not match its sha256 or byte count', () => {
    const root = repoWithMock();
    const files = releaseFiles();
    const changed = Buffer.from(files.validation);
    changed[changed.indexOf('NOT_RUN')] = 'M'.charCodeAt(0);
    addRelease(root, VERSION, { manifest: files.manifest, validation: changed });
    expectPullError(root, { BUNDLE_VERSION: VERSION }, /sha256/);

    const longer = Buffer.concat([files.validation, Buffer.from('\n')]);
    writeFileSync(join(root, 'release', VERSION, 'validation.json'), longer);
    expectPullError(root, { BUNDLE_VERSION: VERSION }, /bytes/);
  });

  it.each([
    { name: 'a record with an unknown status', records: [gate('G0', 'PASSED')] },
    { name: 'a record with a missing field', records: [{ gate_id: 'G0', status: 'NOT_RUN' }] },
    { name: 'an object instead of an array', records: { G0: gate('G0') } },
    { name: 'a gate listed twice', records: [gate('G0'), gate('G0', 'PASS')] },
  ])('throws on validation.json with $name', ({ records }) => {
    const root = repoWithMock();
    const validation = toBytes(records);
    addRelease(root, VERSION, { manifest: toBytes(manifestFor(validation)), validation });
    expectPullError(root, { BUNDLE_VERSION: VERSION }, /validation\.json/);
  });

  it('throws on validation.json that is not UTF-8', () => {
    const root = repoWithMock();
    const validation = Buffer.from([0x5b, 0xff, 0x5d]);
    addRelease(root, VERSION, { manifest: toBytes(manifestFor(validation)), validation });
    expectPullError(root, { BUNDLE_VERSION: VERSION }, /UTF-8/);
  });

  it('throws on a manifest that is a symbolic link', () => {
    const root = repoWithMock();
    const dir = addRelease(root, VERSION, { validation: releaseFiles().validation });
    const elsewhere = join(tempDir(), 'manifest.json');
    writeFileSync(elsewhere, releaseFiles().manifest);
    symlinkSync(elsewhere, join(dir, 'manifest.json'));
    expectPullError(root, { BUNDLE_VERSION: VERSION }, /regular file/);
  });
});

describe('loadBundle (next build)', () => {
  function pulledMock(): string {
    const outDir = join(tempDir(), 'data');
    pullBundle({ repoRoot: REPO_ROOT, outDir, env: { VERCEL_ENV: 'preview' } });
    return outDir;
  }

  it('returns the pulled mock bundle on preview or without VERCEL_ENV', () => {
    const dataDir = pulledMock();
    expect(loadBundle({ dataDir, env: { VERCEL_ENV: 'preview' } }).manifest.mock).toBe(true);
    expect(loadBundle({ dataDir, env: {} }).validation).toStrictEqual(ALL_GATES_NOT_RUN);
  });

  it('throws on a pulled mock bundle when VERCEL_ENV=production', () => {
    const dataDir = pulledMock();
    expect(() => loadBundle({ dataDir, env: { VERCEL_ENV: 'production' } })).toThrow(
      /mock.*VERCEL_ENV=production/s,
    );
  });

  it('throws when the pulled bundle is not the one BUNDLE_VERSION requests', () => {
    const dataDir = pulledMock();
    expect(() => loadBundle({ dataDir, env: { BUNDLE_VERSION: VERSION } })).toThrow(BundleError);

    const root = repoWithMock();
    addRelease(root, VERSION, releaseFiles());
    const releaseData = join(tempDir(), 'data');
    pullBundle({ repoRoot: root, outDir: releaseData, env: { BUNDLE_VERSION: VERSION } });
    expect(() => loadBundle({ dataDir: releaseData, env: {} })).toThrow(/BUNDLE_VERSION/);
    expect(() =>
      loadBundle({ dataDir: releaseData, env: { BUNDLE_VERSION: 'other-version' } }),
    ).toThrow(/other-version/);
  });

  it('throws with a bundle:pull hint when nothing was pulled', () => {
    expect(() => loadBundle({ dataDir: join(tempDir(), 'data'), env: {} })).toThrow(/bundle:pull/);
  });

  it('throws when a pulled file was changed afterwards', () => {
    const dataDir = pulledMock();
    const path = join(dataDir, 'validation.json');
    writeFileSync(path, readFileSync(path, 'utf8').replace('"G6"', '"G5"'));
    expect(() => loadBundle({ dataDir, env: {} })).toThrow(/sha256|listed twice/);
  });
});
