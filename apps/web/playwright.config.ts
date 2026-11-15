// Browser tests (T7-07, T7B-08; PRD 8.10): Chromium only, against `next start` of the production
// build of the bundle that BUNDLE_VERSION selects, on a fixed local port. Run with
// `pnpm --filter @terraluna/web e2e`, which pulls the bundle and builds first. Specs are
// e2e/*.e2e.ts, so Vitest never collects them.
import { defineConfig, devices } from '@playwright/test';

const PORT = 3123;

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  forbidOnly: true,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${String(PORT)}`,
    viewport: { width: 1280, height: 800 },
    reducedMotion: 'reduce',
    colorScheme: 'dark',
    trace: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } } }],
  webServer: {
    command: `node node_modules/next/dist/bin/next start -H 127.0.0.1 -p ${String(PORT)}`,
    url: `http://127.0.0.1:${String(PORT)}/`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { NEXT_TELEMETRY_DISABLED: '1' },
  },
});
