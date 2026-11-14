// `pnpm --filter @terraluna/web bundle:pull` runs scripts/bundle-pull.ts. It must exit non-zero
// with a clear message when the bundle is refused, so make web-build stops before next build.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { MOCK_DIR, REPO_ROOT, tempDir, WEB_DIR } from './helpers.ts';

const SCRIPT = join(WEB_DIR, 'scripts', 'bundle-pull.ts');

function run(env: Record<string, string>, outDir: string, root = REPO_ROOT) {
  const childEnv: NodeJS.ProcessEnv = { ...process.env };
  delete childEnv.BUNDLE_VERSION;
  delete childEnv.VERCEL_ENV;
  return spawnSync(process.execPath, [SCRIPT, '--root', root, '--out', outDir], {
    env: { ...childEnv, ...env },
    encoding: 'utf8',
  });
}

describe('bundle:pull command', () => {
  it('exits 1 with a clear message for the mock bundle on production', () => {
    const result = run({ VERCEL_ENV: 'production' }, join(tempDir(), 'data'));
    expect(result.status).toBe(1);
    expect(result.stdout).toBe('');
    expect(result.stderr).toMatch(/^bundle:pull: .*mock.*VERCEL_ENV=production/s);
  });

  it('exits 1 for a missing requested bundle', () => {
    const result = run({ BUNDLE_VERSION: 'not-there' }, join(tempDir(), 'data'));
    expect(result.status).toBe(1);
    expect(result.stderr).toMatch(/release\/not-there/);
  });

  it('copies the mock bundle on preview and names the source and version', () => {
    const outDir = join(tempDir(), 'data');
    const result = run({ VERCEL_ENV: 'preview' }, outDir);
    expect(result.stderr).toBe('');
    expect(result.status).toBe(0);
    expect(result.stdout).toMatch(/fixtures\/mock-bundle/);
    expect(result.stdout).toMatch(/mock-0/);
    expect(readFileSync(join(outDir, 'manifest.json'))).toStrictEqual(
      readFileSync(join(MOCK_DIR, 'manifest.json')),
    );
  });

  it('exits 2 on an unknown argument', () => {
    const result = spawnSync(process.execPath, [SCRIPT, '--bogus'], { encoding: 'utf8' });
    expect(result.status).toBe(2);
    expect(result.stderr).toMatch(/^bundle:pull: /);
  });
});
