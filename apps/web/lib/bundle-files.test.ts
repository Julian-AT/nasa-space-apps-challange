// bundle:pull and the loader over every small JSON file the manifest lists for server-side use
// (PRD 8.4, 8.5, T15-01): validation.json, sources.json, targets.json, comparisons/<target_id>.json
// and windows/<body>.json. Each copied file must match the byte count and SHA-256 its manifest
// entry lists; a listed file the bundle lacks fails the pull; an unlisted file is never copied;
// large artifacts are never copied; an optional file the manifest does not list reads as not
// present, never as an empty default. The T0-05 cases stay in tests/bundle.test.ts.
//
// The file contents below are labelled test placeholders, not records: sources, targets,
// comparisons and windows have no @terraluna/schema schema yet, so only their bytes matter here.
import { spawnSync } from 'node:child_process';
import { mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

import { BundleManifestSchema } from '@terraluna/schema';
import { describe, expect, it } from 'vitest';

import {
  ALL_GATES_NOT_RUN,
  type FileSet,
  filesUnder,
  listing,
  MOCK_DIR,
  manifestFor,
  REPO_ROOT,
  repoWithMock,
  tempDir,
  toBytes,
  WEB_DIR,
  writeFiles,
} from '../tests/helpers.ts';
import {
  assertDeployable,
  BundleError,
  BundleFileError,
  comparisonFile,
  isPulledPath,
  loadBundle,
  MockBundleError,
  pullBundle,
  pulledPaths,
  sourcesFile,
  targetsFile,
  windowsTopNFile,
} from './bundle.ts';

const VERSION = '2026-11-15T0900Z-3f9c2a1';
const ENV = { BUNDLE_VERSION: VERSION };
const TARGET = 'test-target-a';
const BODY = 'earth';
const placeholder = (name: string) => toBytes({ test_placeholder: name });

/** The small JSON files bundle:pull copies, besides manifest.json. */
const SERVER_FILES: FileSet = {
  'validation.json': toBytes(ALL_GATES_NOT_RUN),
  'sources.json': placeholder('sources.json'),
  'targets.json': placeholder('targets.json'),
  [`comparisons/${TARGET}.json`]: placeholder('comparison'),
  [`windows/${BODY}.json`]: placeholder('windows top-N'),
};

/** Listed files that bundle:pull never copies: large artifacts and files the server never reads. */
const OTHER_FILES: FileSet = {
  [`windows/${BODY}.parquet`]: Buffer.from('PAR1 test placeholder'),
  'tiles/cpr/0/0/0.webp': Buffer.from('RIFF test placeholder'),
  'model/encoder.onnx': Buffer.from('onnx test placeholder'),
  'model/encoder.fp16.onnx': Buffer.from('onnx fp16 test placeholder'),
  'model/model_card.json': placeholder('model card'),
  'model/embeddings.f16.bin': Buffer.from('f16 test placeholder'),
  'report/metrics.json': placeholder('metrics'),
  [`reference/background_${TARGET}.json`]: placeholder('background'),
  'cards/test-card.json': placeholder('card'),
  'figures/test.svg': Buffer.from('<svg/>'),
  'comparisons/nested/test.json': placeholder('nested comparison'),
};

const SERVER_PATHS = Object.keys(SERVER_FILES).sort();

function bytesOf(path: string): Uint8Array {
  const bytes = SERVER_FILES[path];
  if (bytes === undefined) throw new Error(`no test file ${path}`);
  return bytes;
}
const OPTIONAL_PATHS = SERVER_PATHS.filter((path) => path !== 'validation.json');

/** Writes release/<VERSION>/ holding `present`, with a manifest listing `listed`. */
function addFullRelease(
  root: string,
  {
    listed = { ...SERVER_FILES, ...OTHER_FILES },
    present = listed,
  }: { listed?: FileSet; present?: FileSet } = {},
): string {
  const dir = join(root, 'release', VERSION);
  const manifest = { ...manifestFor(new Uint8Array()), files: listing(listed), targets: [TARGET] };
  writeFiles(dir, { ...present, 'manifest.json': toBytes(manifest) });
  return dir;
}

function caught(run: () => unknown): unknown {
  try {
    run();
  } catch (error) {
    return error;
  }
  throw new Error('expected a throw');
}

/** Pulls into a new directory and expects a BundleFileError for `path`; nothing is written. */
function expectFileError(root: string, path: string, reason: string): BundleFileError {
  const outDir = join(tempDir(), 'data');
  const error = caught(() => pullBundle({ repoRoot: root, outDir, env: ENV }));
  expect(error).toBeInstanceOf(BundleFileError);
  expect(error).toBeInstanceOf(BundleError);
  const fileError = error as BundleFileError;
  expect({ path: fileError.path, reason: fileError.reason }).toStrictEqual({ path, reason });
  expect(fileError.message).toContain(`release/${VERSION}/${path}`);
  expect(filesUnder(outDir)).toStrictEqual([]);
  return fileError;
}

function withOneByteChanged(bytes: Uint8Array): Buffer {
  const changed = Buffer.from(bytes);
  const index = changed.indexOf('"');
  changed[index + 1] = (changed[index + 1] ?? 0) ^ 0x01;
  return changed;
}

describe('isPulledPath', () => {
  it.each(SERVER_PATHS)('copies %s', (path) => {
    expect(isPulledPath(path)).toBe(true);
  });

  it.each([...Object.keys(OTHER_FILES), 'manifest.json', 'comparisons/.json', 'windows/a/b.json'])(
    'never copies %s',
    (path) => {
      expect(isPulledPath(path)).toBe(false);
    },
  );
});

describe('pullBundle copies the small server-side JSON files', () => {
  it('copies exactly the listed server-side files, byte for byte', () => {
    const root = repoWithMock();
    const source = addFullRelease(root);
    const outDir = join(tempDir(), 'data');
    const bundle = pullBundle({ repoRoot: root, outDir, env: ENV });
    const expected = ['manifest.json', ...SERVER_PATHS].sort();
    expect(filesUnder(outDir)).toStrictEqual(expected);
    expect(pulledPaths(bundle)).toStrictEqual(expected);
    for (const path of expected) {
      expect(readFileSync(join(outDir, path))).toStrictEqual(readFileSync(join(source, path)));
    }
  });

  it('never reads large artifacts, so they may be absent from the bundle', () => {
    const root = repoWithMock();
    addFullRelease(root, { listed: { ...SERVER_FILES, ...OTHER_FILES }, present: SERVER_FILES });
    const outDir = join(tempDir(), 'data');
    pullBundle({ repoRoot: root, outDir, env: ENV });
    expect(filesUnder(outDir)).toStrictEqual(['manifest.json', ...SERVER_PATHS].sort());
  });

  it('does not copy a server-side file the manifest does not list', () => {
    const root = repoWithMock();
    const listed = { 'validation.json': bytesOf('validation.json') };
    addFullRelease(root, { listed, present: SERVER_FILES });
    const outDir = join(tempDir(), 'data');
    const bundle = pullBundle({ repoRoot: root, outDir, env: ENV });
    expect(filesUnder(outDir)).toStrictEqual(['manifest.json', 'validation.json']);
    expect(targetsFile(bundle)).toStrictEqual({ status: 'not_present', path: 'targets.json' });
  });

  it.each(OPTIONAL_PATHS)('copies %s alone when it is the only optional file listed', (path) => {
    const root = repoWithMock();
    const listed = {
      'validation.json': bytesOf('validation.json'),
      [path]: bytesOf(path),
    };
    addFullRelease(root, { listed });
    const outDir = join(tempDir(), 'data');
    pullBundle({ repoRoot: root, outDir, env: ENV });
    expect(filesUnder(outDir)).toStrictEqual(['manifest.json', 'validation.json', path].sort());
  });
});

describe('pullBundle refuses a server-side file that does not match its manifest entry', () => {
  it.each(SERVER_PATHS)('names %s when its sha256 differs', (path) => {
    const root = repoWithMock();
    const dir = addFullRelease(root);
    writeFileSync(join(dir, path), withOneByteChanged(bytesOf(path)));
    expect(expectFileError(root, path, 'sha256_mismatch').message).toMatch(/sha256/);
  });

  it.each(SERVER_PATHS)('names %s when its byte count differs', (path) => {
    const root = repoWithMock();
    const dir = addFullRelease(root);
    const bytes = bytesOf(path);
    writeFileSync(join(dir, path), Buffer.concat([bytes, Buffer.from('\n')]));
    expect(expectFileError(root, path, 'bytes_mismatch').message).toMatch(/bytes/);
  });

  it.each(SERVER_PATHS)('names %s when it is listed but missing', (path) => {
    const root = repoWithMock();
    const present = Object.fromEntries(
      Object.entries(SERVER_FILES).filter(([name]) => name !== path),
    );
    addFullRelease(root, { listed: SERVER_FILES, present });
    expect(expectFileError(root, path, 'missing').message).toMatch(/missing/);
  });

  it.each(SERVER_PATHS)('names %s when it is a symbolic link', (path) => {
    const root = repoWithMock();
    const dir = addFullRelease(root, { listed: SERVER_FILES });
    const elsewhere = join(tempDir(), 'elsewhere.json');
    writeFileSync(elsewhere, bytesOf(path));
    rmSync(join(dir, path));
    symlinkSync(elsewhere, join(dir, path));
    expectFileError(root, path, 'not_regular_file');
  });

  it('names a file whose directory is a symbolic link', () => {
    const root = repoWithMock();
    const dir = addFullRelease(root, { listed: SERVER_FILES });
    const elsewhere = tempDir();
    writeFiles(elsewhere, { [`${TARGET}.json`]: bytesOf(`comparisons/${TARGET}.json`) });
    rmSync(join(dir, 'comparisons'), { recursive: true });
    symlinkSync(elsewhere, join(dir, 'comparisons'));
    expectFileError(root, `comparisons/${TARGET}.json`, 'not_regular_file');
  });

  it('names a server-side file that the manifest lists twice', () => {
    const root = repoWithMock();
    const dir = join(root, 'release', VERSION);
    const entries = listing(SERVER_FILES);
    const twice = entries.filter((entry) => entry.path === 'targets.json');
    const manifest = { ...manifestFor(new Uint8Array()), files: [...entries, ...twice] };
    writeFiles(dir, { ...SERVER_FILES, 'manifest.json': toBytes(manifest) });
    expectFileError(root, 'targets.json', 'listed_twice');
  });

  it('refuses a manifest for another bundle_version before reading the files it lists', () => {
    const root = repoWithMock();
    const dir = join(root, 'release', VERSION);
    const manifest = {
      ...manifestFor(new Uint8Array()),
      bundle_version: 'other-version',
      files: listing(SERVER_FILES),
    };
    writeFiles(dir, { 'manifest.json': toBytes(manifest) });
    const error = caught(() =>
      pullBundle({ repoRoot: root, outDir: join(tempDir(), 'data'), env: ENV }),
    );
    expect(error).toBeInstanceOf(BundleError);
    expect(error).not.toBeInstanceOf(BundleFileError);
    expect((error as BundleError).message).toMatch(/other-version/);
  });

  it('writes nothing when an output path is blocked', () => {
    const root = repoWithMock();
    addFullRelease(root, { listed: SERVER_FILES });
    const outDir = join(tempDir(), 'data');
    mkdirSync(outDir);
    writeFileSync(join(outDir, 'comparisons'), 'a file where a directory must go');
    expect(() => pullBundle({ repoRoot: root, outDir, env: ENV })).toThrow(/comparisons/);
    expect(filesUnder(outDir)).toStrictEqual(['comparisons']);
  });
});

describe('typed errors for the manifest and validation.json', () => {
  it.each([
    { name: 'invalid JSON', bytes: Buffer.from('{"mock": false,') },
    { name: 'a schema error', bytes: toBytes({ ...manifestFor(new Uint8Array()), mock: 'no' }) },
  ])('reports a manifest with $name as an invalid manifest.json', ({ bytes }) => {
    const root = repoWithMock();
    writeFiles(join(root, 'release', VERSION), { ...SERVER_FILES, 'manifest.json': bytes });
    expectFileError(root, 'manifest.json', 'invalid');
  });

  it('reports a missing manifest as a missing manifest.json', () => {
    const root = repoWithMock();
    writeFiles(join(root, 'release', VERSION), SERVER_FILES);
    expectFileError(root, 'manifest.json', 'missing');
  });

  it('reports a schema-invalid validation.json as an invalid validation.json', () => {
    const root = repoWithMock();
    const validation = toBytes([{ gate_id: 'G0', status: 'PASSED' }]);
    addFullRelease(root, { listed: { ...SERVER_FILES, 'validation.json': validation } });
    expectFileError(root, 'validation.json', 'invalid');
  });

  it('reports a validation.json that the manifest does not list', () => {
    const root = repoWithMock();
    const listed = Object.fromEntries(OPTIONAL_PATHS.map((path) => [path, bytesOf(path)]));
    addFullRelease(root, { listed, present: SERVER_FILES });
    expect(expectFileError(root, 'validation.json', 'not_listed').message).toMatch(/not listed/);
  });
});

describe('optional files on the mock bundle', () => {
  const absent = [
    { name: 'sources.json', read: sourcesFile, path: 'sources.json' },
    { name: 'targets.json', read: targetsFile, path: 'targets.json' },
    {
      name: 'a comparison',
      read: (bundle: Parameters<typeof comparisonFile>[0]) => comparisonFile(bundle, TARGET),
      path: `comparisons/${TARGET}.json`,
    },
    {
      name: 'a windows top-N file',
      read: (bundle: Parameters<typeof windowsTopNFile>[0]) => windowsTopNFile(bundle, BODY),
      path: `windows/${BODY}.json`,
    },
  ];

  it.each(absent)('reads $name as not present in the pull and the loader', ({ read, path }) => {
    const outDir = join(tempDir(), 'data');
    const pulled = pullBundle({ repoRoot: REPO_ROOT, outDir, env: {} });
    expect(read(pulled)).toStrictEqual({ status: 'not_present', path });
    expect(read(loadBundle({ dataDir: outDir, env: {} }))).toStrictEqual({
      status: 'not_present',
      path,
    });
  });

  it('copies only manifest.json and validation.json from the mock', () => {
    const outDir = join(tempDir(), 'data');
    const bundle = pullBundle({ repoRoot: REPO_ROOT, outDir, env: {} });
    expect(filesUnder(outDir)).toStrictEqual(['manifest.json', 'validation.json']);
    expect(pulledPaths(bundle)).toStrictEqual(['manifest.json', 'validation.json']);
    expect(readFileSync(join(outDir, 'validation.json'))).toStrictEqual(
      readFileSync(join(MOCK_DIR, 'validation.json')),
    );
  });
});

describe('loader accessors', () => {
  function pulledRelease(): { dataDir: string; source: string } {
    const root = repoWithMock();
    const source = addFullRelease(root);
    const dataDir = join(tempDir(), 'data');
    pullBundle({ repoRoot: root, outDir: dataDir, env: ENV });
    return { dataDir, source };
  }

  it('returns each pulled file with the bytes the bundle holds', () => {
    const { dataDir, source } = pulledRelease();
    const bundle = loadBundle({ dataDir, env: ENV });
    const read = (path: string) => readFileSync(join(source, path));
    expect(bundle.validation).toStrictEqual(ALL_GATES_NOT_RUN);
    expect(bundle.manifest.targets).toStrictEqual([TARGET]);
    expect(sourcesFile(bundle)).toStrictEqual({
      status: 'present',
      path: 'sources.json',
      bytes: read('sources.json'),
    });
    expect(targetsFile(bundle)).toStrictEqual({
      status: 'present',
      path: 'targets.json',
      bytes: read('targets.json'),
    });
    expect(comparisonFile(bundle, TARGET)).toStrictEqual({
      status: 'present',
      path: `comparisons/${TARGET}.json`,
      bytes: read(`comparisons/${TARGET}.json`),
    });
    expect(windowsTopNFile(bundle, BODY)).toStrictEqual({
      status: 'present',
      path: `windows/${BODY}.json`,
      bytes: read(`windows/${BODY}.json`),
    });
  });

  it.each(['test-target-b', '../validation', 'nested/test', ''])(
    'reads the comparison for an unlisted id %j as not present',
    (id) => {
      const bundle = loadBundle({ dataDir: pulledRelease().dataDir, env: ENV });
      expect(comparisonFile(bundle, id).status).toBe('not_present');
    },
  );

  it('never reads a file that an earlier pull left and the current manifest does not list', () => {
    const root = repoWithMock();
    addFullRelease(root);
    const dataDir = join(tempDir(), 'data');
    pullBundle({ repoRoot: root, outDir: dataDir, env: ENV });
    pullBundle({ repoRoot: root, outDir: dataDir, env: {} });
    // Nothing is deleted: the release's files stay on disk next to the mock's manifest.
    expect(filesUnder(dataDir)).toStrictEqual(['manifest.json', ...SERVER_PATHS].sort());
    const bundle = loadBundle({ dataDir, env: {} });
    expect(bundle.manifest.mock).toBe(true);
    expect(pulledPaths(bundle)).toStrictEqual(['manifest.json', 'validation.json']);
    expect(readFileSync(join(dataDir, 'validation.json'))).toStrictEqual(
      readFileSync(join(MOCK_DIR, 'validation.json')),
    );
    expect(sourcesFile(bundle).status).toBe('not_present');
    expect(targetsFile(bundle).status).toBe('not_present');
    expect(comparisonFile(bundle, TARGET).status).toBe('not_present');
    expect(windowsTopNFile(bundle, BODY).status).toBe('not_present');
  });

  it.each(OPTIONAL_PATHS)('throws a typed error when pulled %s was changed', (path) => {
    const { dataDir } = pulledRelease();
    writeFileSync(join(dataDir, path), withOneByteChanged(readFileSync(join(dataDir, path))));
    const error = caught(() => loadBundle({ dataDir, env: ENV }));
    expect(error).toBeInstanceOf(BundleFileError);
    expect((error as BundleFileError).path).toBe(path);
    expect((error as BundleFileError).reason).toBe('sha256_mismatch');
  });

  it.each(OPTIONAL_PATHS)('throws a typed error with a pull hint when %s is gone', (path) => {
    const { dataDir } = pulledRelease();
    rmSync(join(dataDir, path));
    const error = caught(() => loadBundle({ dataDir, env: ENV }));
    expect(error).toBeInstanceOf(BundleFileError);
    expect((error as BundleFileError).path).toBe(path);
    expect((error as BundleFileError).reason).toBe('missing');
    expect((error as BundleFileError).message).toMatch(/bundle:pull/);
  });
});

describe('production mock guard', () => {
  const mock = BundleManifestSchema.parse(
    JSON.parse(readFileSync(join(MOCK_DIR, 'manifest.json'), 'utf8')),
  );

  it('throws MockBundleError for a mock manifest when VERCEL_ENV=production', () => {
    const error = caught(() => {
      assertDeployable(mock, { VERCEL_ENV: 'production' });
    });
    expect(error).toBeInstanceOf(MockBundleError);
    expect(error).toBeInstanceOf(BundleError);
  });

  it.each([{}, { VERCEL_ENV: 'preview' }, { VERCEL_ENV: 'development' }])(
    'lets a mock manifest through with %j',
    (env) => {
      expect(() => {
        assertDeployable(mock, env);
      }).not.toThrow();
    },
  );

  it('lets a release manifest through on production', () => {
    expect(() => {
      assertDeployable({ ...mock, mock: false }, { VERCEL_ENV: 'production' });
    }).not.toThrow();
  });

  it('is the guard pullBundle and loadBundle apply', () => {
    const outDir = join(tempDir(), 'data');
    const env = { VERCEL_ENV: 'production' };
    expect(caught(() => pullBundle({ repoRoot: REPO_ROOT, outDir, env }))).toBeInstanceOf(
      MockBundleError,
    );
    const dataDir = join(tempDir(), 'data');
    pullBundle({ repoRoot: REPO_ROOT, outDir: dataDir, env: {} });
    expect(caught(() => loadBundle({ dataDir, env }))).toBeInstanceOf(MockBundleError);
  });
});

describe('bundle:pull command', () => {
  const SCRIPT = join(WEB_DIR, 'scripts', 'bundle-pull.ts');

  function run(root: string, outDir: string) {
    const env: NodeJS.ProcessEnv = { ...process.env, BUNDLE_VERSION: VERSION };
    delete env.VERCEL_ENV;
    return spawnSync(process.execPath, [SCRIPT, '--root', root, '--out', outDir], {
      env,
      encoding: 'utf8',
    });
  }

  it('lists the copied files and the ones copied without a schema check', () => {
    const root = repoWithMock();
    addFullRelease(root);
    const result = run(root, join(tempDir(), 'data'));
    expect(result.stderr).toBe('');
    expect(result.status).toBe(0);
    for (const path of ['manifest.json', ...SERVER_PATHS]) expect(result.stdout).toContain(path);
    expect(result.stdout).toMatch(/no schema/);
    expect(result.stdout).not.toContain('tiles/');
  });

  it('exits 1 naming the file whose sha256 differs', () => {
    const root = repoWithMock();
    const dir = addFullRelease(root);
    const path = `comparisons/${TARGET}.json`;
    writeFileSync(join(dir, path), withOneByteChanged(bytesOf(path)));
    const result = run(root, join(tempDir(), 'data'));
    expect(result.status).toBe(1);
    expect(result.stdout).toBe('');
    expect(result.stderr).toContain(`release/${VERSION}/${path}`);
  });
});
