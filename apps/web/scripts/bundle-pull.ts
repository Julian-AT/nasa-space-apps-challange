// `pnpm --filter @terraluna/web bundle:pull` (PRD 8.5): copies the small files the web reads
// (manifest.json, validation.json and, when listed, every path in lib/bundle-paths.ts, each
// validated with its schema) of the bundle that BUNDLE_VERSION selects into apps/web/data/, after
// checking each against the manifest (lib/bundle.ts). Exits 1 when the bundle is refused and 2 on
// a usage error.
//
//   node scripts/bundle-pull.ts [--root <repository root>] [--out <directory>]
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

import {
  BundleError,
  bundleSourcePath,
  pullBundle,
  pulledPaths,
  selectBundle,
} from '../lib/bundle.ts';

const USAGE = 'usage: bundle-pull.ts [--root <repository root>] [--out <directory>]';

function main(): number {
  let root: string | undefined;
  let out: string | undefined;
  try {
    ({ root, out } = parseArgs({
      options: { root: { type: 'string' }, out: { type: 'string' } },
      allowPositionals: false,
      strict: true,
    }).values);
  } catch (error) {
    console.error(`bundle:pull: ${error instanceof Error ? error.message : String(error)}\n${USAGE}`);
    return 2;
  }
  const repoRoot = root ?? fileURLToPath(new URL('../../..', import.meta.url));
  const outDir = out ?? fileURLToPath(new URL('../data', import.meta.url));
  try {
    const bundle = pullBundle({ repoRoot, outDir, env: process.env });
    const { manifest } = bundle;
    const source = bundleSourcePath(selectBundle(process.env));
    const label = manifest.mock ? ', mock' : '';
    console.log(
      `bundle:pull: copied ${pulledPaths(bundle).join(', ')} from ${source} (bundle_version ${manifest.bundle_version}${label}) to ${outDir}`,
    );
    return 0;
  } catch (error) {
    if (error instanceof BundleError) {
      console.error(`bundle:pull: ${error.message}`);
      return 1;
    }
    throw error;
  }
}

process.exitCode = main();
