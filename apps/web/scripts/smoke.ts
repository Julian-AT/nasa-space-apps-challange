// make web-smoke (PRD 8.6, 8.9): starts `next start` on a free port against the production build,
// fetches /, checks the mock banner, the shipping-mode line and the footer's bundle version
// against apps/web/data/, and always stops the server. Run it after make web-build.
import { type ChildProcess, spawn } from 'node:child_process';
import { once } from 'node:events';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

import { BundleError, loadBundle } from '../lib/bundle.ts';
import { shellText } from '../lib/shell.ts';
import { pageProblems } from './page-check.ts';

const WEB_DIR = fileURLToPath(new URL('..', import.meta.url));
const NEXT_BIN = createRequire(import.meta.url).resolve('next/dist/bin/next');
const HOST = '127.0.0.1';
const START_TIMEOUT_MS = 60_000;
const STOP_TIMEOUT_MS = 10_000;

class SmokeError extends Error {
  override name = 'SmokeError';
}

/** A port that was free a moment ago; next start fails loudly if it was taken since. */
async function freePort(): Promise<number> {
  const server = createServer();
  server.listen(0, HOST);
  await once(server, 'listening');
  const address = server.address();
  server.close();
  await once(server, 'close');
  if (address === null || typeof address === 'string') throw new SmokeError('no TCP port found');
  return address.port;
}

function hasExited(child: ChildProcess): boolean {
  return child.exitCode !== null || child.signalCode !== null;
}

/** Signals the server's whole process group; it runs detached, so it has its own. */
function signalGroup(child: ChildProcess, signal: NodeJS.Signals): void {
  if (child.pid === undefined || hasExited(child)) return;
  try {
    process.kill(-child.pid, signal);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ESRCH') throw error;
  }
}

async function stopServer(child: ChildProcess, exited: Promise<void>): Promise<void> {
  if (child.pid === undefined || hasExited(child)) return;
  signalGroup(child, 'SIGTERM');
  const stopped = await Promise.race([exited.then(() => true), sleep(STOP_TIMEOUT_MS, false)]);
  if (!stopped) {
    signalGroup(child, 'SIGKILL');
    await exited;
  }
}

interface Page {
  readonly html: string;
  readonly headers: Headers;
}

/** Fetches url once the server accepts connections. Any non-200 response fails at once. */
async function fetchWhenUp(url: string, child: ChildProcess, log: () => string): Promise<Page> {
  const deadline = Date.now() + START_TIMEOUT_MS;
  for (;;) {
    if (hasExited(child)) {
      throw new SmokeError(`next start exited before serving ${url}:\n${log()}`);
    }
    let response: Response | undefined;
    try {
      response = await fetch(url, { signal: AbortSignal.timeout(5_000), redirect: 'manual' });
    } catch {
      if (Date.now() > deadline) {
        throw new SmokeError(`next start did not answer ${url} within ${String(START_TIMEOUT_MS)} ms:\n${log()}`);
      }
      await sleep(250);
      continue;
    }
    const html = await response.text();
    if (response.status !== 200) {
      throw new SmokeError(`GET ${url} returned ${String(response.status)}:\n${log()}`);
    }
    return { html, headers: response.headers };
  }
}

async function main(): Promise<number> {
  // The same checks as next build, so the smoke test compares the page with the bundle it was
  // built from, under the same BUNDLE_VERSION and VERCEL_ENV.
  const bundle = loadBundle({ dataDir: join(WEB_DIR, 'data') });
  if (!existsSync(join(WEB_DIR, '.next', 'BUILD_ID'))) {
    throw new SmokeError('apps/web has no production build; run make web-build first');
  }
  const port = await freePort();
  const url = `http://${HOST}:${String(port)}/`;
  const child = spawn(
    process.execPath,
    [NEXT_BIN, 'start', '--port', String(port), '--hostname', HOST],
    {
      cwd: WEB_DIR,
      detached: true,
      env: { ...process.env, NEXT_TELEMETRY_DISABLED: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  let output = '';
  const exited = new Promise<void>((resolve) => {
    child.once('exit', () => {
      resolve();
    });
  });
  child.once('error', (error) => (output += `${error.message}\n`));
  child.stdout.on('data', (chunk: Buffer) => (output += chunk.toString('utf8')));
  child.stderr.on('data', (chunk: Buffer) => (output += chunk.toString('utf8')));
  const interrupt = (signal: NodeJS.Signals): void => {
    signalGroup(child, 'SIGKILL');
    process.exit(signal === 'SIGINT' ? 130 : 143);
  };
  process.once('SIGINT', interrupt);
  process.once('SIGTERM', interrupt);
  try {
    const page = await fetchWhenUp(url, child, () => output);
    const problems = pageProblems(page.html, bundle);
    // next start marks a page served from the build's prerendered HTML (PRD 8.6: / is static).
    const prerender = page.headers.get('x-nextjs-prerender') ?? '';
    if (!prerender.split(',').some((value) => value.trim() === '1')) {
      problems.push('/ was not served as a prerendered page (no x-nextjs-prerender: 1)');
    }
    if (problems.length > 0) {
      console.error(`web-smoke: GET ${url}:\n${problems.map((p) => `  - ${p}`).join('\n')}`);
      return 1;
    }
    const banner = bundle.manifest.mock ? 'the mock banner, ' : 'no mock banner, ';
    console.log(
      `web-smoke: GET ${url} (prerendered) shows ${banner}"${shellText(bundle).shippingMode}" and bundle version ${bundle.manifest.bundle_version} in the footer`,
    );
    return 0;
  } finally {
    await stopServer(child, exited);
    process.removeListener('SIGINT', interrupt);
    process.removeListener('SIGTERM', interrupt);
  }
}

try {
  process.exitCode = await main();
} catch (error) {
  if (error instanceof BundleError || error instanceof SmokeError) {
    console.error(`web-smoke: ${error.message}`);
    process.exitCode = 1;
  } else {
    throw error;
  }
}
