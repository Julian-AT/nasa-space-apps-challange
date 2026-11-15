// Every public route in a real browser (T7-07, T7B-08; PRD 8.6, 8.10): the shipping-mode banner,
// the mock banner exactly when the pulled bundle is a mock, the bundle version in the footer, zero
// serious or critical axe violations (WCAG 2.0 A/AA, 2.1 AA, 2.2 AA), keyboard navigation, image
// alt text and the JavaScript budget of the landing page. The expected texts are read from the
// pulled bundle in apps/web/data/, never typed here.
import { gzipSync } from 'node:zlib';

import AxeBuilder from '@axe-core/playwright';
import { expect, type Page, test } from '@playwright/test';

import { loadBundle } from '../lib/bundle.ts';
import { shellText } from '../lib/shell.ts';
import { modeBannerText } from '../components/mode-banner.tsx';

const bundle = loadBundle();
const shell = shellText(bundle);

const cardIds = [...bundle.records.cards.keys()].sort();
const pairs = [...bundle.records.comparisons.values()]
  .flat()
  .map((record) => `/compare/${record.target_window_id}/${record.earth_window_id}`);

/** Every static route, plus one card and one comparison when the bundle holds them. */
const ROUTES = [
  '/',
  '/explore/moon',
  '/explore/mars',
  '/explore/earth',
  '/site',
  '/model',
  '/methods',
  '/data',
  '/paper',
  ...(cardIds[0] === undefined ? [] : [`/site/${cardIds[0]}`]),
  ...(pairs[0] === undefined ? [] : [pairs[0]]),
];

/** Routes that must never load a map, chart, 3D or ONNX library (PRD 8.10). */
const LIGHT_ROUTES = ['/', '/methods', '/data', '/model', '/paper'];
const HEAVY_MARKERS = [/deck\.gl/i, /luma\.gl/i, /three\.module|THREE\.REVISION/, /onnxruntime/i, /maplibre/i, /@observablehq\/plot|observablehq/i];

async function scriptBodies(page: Page, path: string): Promise<{ url: string; body: Buffer }[]> {
  const scripts: Promise<{ url: string; body: Buffer }>[] = [];
  page.on('response', (response) => {
    if (response.request().resourceType() === 'script') {
      scripts.push(response.body().then((body) => ({ url: response.url(), body })));
    }
  });
  await page.goto(path, { waitUntil: 'networkidle' });
  return Promise.all(scripts);
}

for (const path of ROUTES) {
  test.describe(path, () => {
    test('shows the shipping mode, the mock state and the bundle version', async ({ page }) => {
      const response = await page.goto(path);
      expect(response?.status()).toBe(200);
      await expect(page.getByText(modeBannerText(bundle), { exact: false }).first()).toBeVisible();
      const mock = page.getByRole('status').filter({ hasText: 'Mock bundle' });
      if (shell.mockBanner === null) await expect(mock).toHaveCount(0);
      else await expect(mock.first()).toHaveText(shell.mockBanner);
      await expect(page.getByRole('contentinfo')).toContainText(bundle.manifest.bundle_version);
      await expect(page.locator('h1')).toHaveCount(1);
    });

    test('has no serious or critical axe violation', async ({ page }) => {
      await page.goto(path, { waitUntil: 'networkidle' });
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'])
        // Map canvases are excluded from the WCAG scope (PRD 8.10); their table or text
        // alternative stays in scope.
        .exclude('canvas')
        .analyze();
      const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
      expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([]);
    });

    test('gives every image alt text', async ({ page }) => {
      await page.goto(path);
      const images = page.locator('img');
      const count = await images.count();
      for (let index = 0; index < count; index += 1) {
        const alt = await images.nth(index).getAttribute('alt');
        expect(alt, `img ${String(index)} on ${path}`).not.toBeNull();
      }
    });
  });
}

test('the skip link moves focus to main', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Tab');
  const skip = page.locator(':focus');
  await expect(skip).toHaveAttribute('href', '#main');
  await page.keyboard.press('Enter');
  await expect(page.locator('main')).toBeFocused();
});

test('every primary navigation link is reachable by Tab in DOM order', async ({ page }) => {
  await page.goto('/');
  const hrefs = await page
    .getByRole('navigation', { name: 'Primary' })
    .locator('a')
    .evaluateAll((links) => links.filter((link) => (link as HTMLElement).offsetParent !== null).map((link) => link.getAttribute('href')));
  expect(hrefs.length).toBeGreaterThan(0);
  const reached: string[] = [];
  for (let step = 0; step < 60 && reached.length < hrefs.length; step += 1) {
    await page.keyboard.press('Tab');
    const href = await page.evaluate(() => {
      const element = document.activeElement;
      return element?.closest('nav[aria-label="Primary"]') ? element.getAttribute('href') : null;
    });
    if (href !== null && !reached.includes(href)) reached.push(href);
  }
  expect(reached).toEqual(hrefs);
});

test('/methods shows NOT_RUN literally for every gate validation.json marks NOT_RUN', async ({ page }) => {
  await page.goto('/methods');
  for (const record of bundle.validation.filter((gate) => gate.status === 'NOT_RUN')) {
    await expect(page.locator(`[data-gate="${record.gate_id}"]`)).toContainText('NOT_RUN');
  }
});

test('/model shows no encoder ranking while G5 is not PASS', async ({ page }) => {
  const g5 = bundle.validation.find((gate) => gate.gate_id === 'G5');
  test.skip(g5?.status === 'PASS', 'G5 passed');
  await page.goto('/model');
  await expect(page.getByText(/\brank(ed|ing)?\b.*#\d|top analog/i)).toHaveCount(0);
});

test('the landing page ships at most 250 kB of gzip JavaScript (PRD 8.10)', async ({ page }) => {
  // Measured as the sum of gzip -9 sizes of every script response the page requests until the
  // network is idle; the server may compress differently, so this approximates transfer size.
  const scripts = await scriptBodies(page, '/');
  const total = scripts.reduce((sum, { body }) => sum + gzipSync(body, { level: 9 }).byteLength, 0);
  expect(total).toBeLessThanOrEqual(250 * 1024);
});

for (const path of LIGHT_ROUTES) {
  test(`${path} loads no map, chart, 3D or ONNX library`, async ({ page }) => {
    const scripts = await scriptBodies(page, path);
    for (const { url, body } of scripts) {
      const text = body.toString('utf8');
      for (const marker of HEAVY_MARKERS) expect(`${url}: ${String(marker.test(text) || marker.test(url))}`).toBe(`${url}: false`);
    }
  });
}
