// The root layout every route shares (PRD 3, 8.6, 8.10, D08): skip link, mock banner, mode
// banner, primary navigation, one main landmark and the footer with the bundle and schema
// versions. The bundle and the pathname are synthetic: loadBundle and usePathname are replaced.
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';

import { BundleManifestSchema, ValidationSchema } from '@terraluna/schema';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import RootLayout from '../app/layout.tsx';
import Home, { dynamic } from '../app/page.tsx';
import { SiteFooter } from '../components/site-footer.tsx';
import { NAV_ITEMS, SiteHeader, SiteNav } from '../components/site-header.tsx';
import { SKIP_LINK_TEXT, SkipLink } from '../components/skip-link.tsx';
import type { Bundle } from '../lib/bundle.ts';
import { MOCK_BANNER, shellText, SITE_TITLE } from '../lib/shell.ts';
import { HOME_LINE } from '../scripts/page-check.ts';
import { ALL_GATES_NOT_RUN, gate, manifestFor, toBytes, WEB_DIR } from './helpers.ts';
import { classesOf, elements, isClientModule, type MarkupElement, only, textOf } from './markup.ts';
import { compile } from './tailwind.ts';

const state = vi.hoisted(() => ({ pathname: '/' as string | null, bundle: null as unknown }));

vi.mock('next/navigation', () => ({ usePathname: () => state.pathname }));

vi.mock('../lib/bundle.ts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/bundle.ts')>();
  return {
    ...actual,
    loadBundle: () => {
      if (state.bundle === null) throw new Error('the test set no bundle');
      return state.bundle;
    },
  };
});

function bundle(fields: Record<string, unknown>, records: unknown[] = ALL_GATES_NOT_RUN): Bundle {
  return {
    manifest: BundleManifestSchema.parse(manifestFor(toBytes(records), fields)),
    validation: ValidationSchema.parse(records),
  };
}

const MOCK = bundle({ mock: true, shipping_mode: null, bundle_version: 'mock-0', schema_version: '1.0.0' });
const MODE_B = bundle({ mock: false, shipping_mode: 'B', bundle_version: 'v1', schema_version: '1.2.3' }, [
  gate('G0', 'PASS'),
]);

const EXPECTED_NAV = [
  ['Home', '/'],
  ['Moon', '/explore/moon'],
  ['Mars', '/explore/mars'],
  ['Earth', '/explore/earth'],
  ['Model', '/model'],
  ['Methods', '/methods'],
  ['Data', '/data'],
] as const;

beforeEach(() => {
  state.pathname = '/';
  state.bundle = null;
});

function links(html: string): MarkupElement[] {
  return elements(html).filter((element) => element.tag === 'a');
}

function current(html: string): string[] {
  return links(html)
    .filter((link) => link.attributes.get('aria-current') === 'page')
    .map((link) => link.attributes.get('href') ?? '');
}

function layout(shown: Bundle, pathname = '/'): string {
  state.bundle = shown;
  state.pathname = pathname;
  return renderToStaticMarkup(
    <RootLayout>
      <h1>test-page-heading</h1>
    </RootLayout>,
  );
}

/** Every .ts and .tsx file of app/ and components/, as paths relative to apps/web. */
function appSources(): string[] {
  const found: string[] = [];
  for (const top of ['app', 'components']) {
    const entries = readdirSync(join(WEB_DIR, top), { recursive: true, withFileTypes: true });
    for (const entry of entries) {
      if (entry.isFile() && /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
        found.push(relative(WEB_DIR, join(entry.parentPath, entry.name)));
      }
    }
  }
  return found.sort();
}

describe('primary navigation', () => {
  it('links Home, Moon, Mars, Earth, Model, Methods and Data, in that DOM order', () => {
    expect(NAV_ITEMS.map(({ label, href }) => [label, href])).toEqual(EXPECTED_NAV);
    const html = renderToStaticMarkup(<SiteNav pathname="/" />);
    const nav = only(
      elements(html).filter((element) => element.tag === 'nav'),
      '<nav>',
    );
    expect(nav.attributes.get('aria-label')).toBe('Primary');
    expect(links(html).map((link) => [link.text, link.attributes.get('href')])).toEqual(EXPECTED_NAV);
  });

  it('links to no route this batch does not build', () => {
    const hrefs = links(renderToStaticMarkup(<SiteNav pathname="/" />)).map((link) => link.attributes.get('href'));
    for (const href of hrefs) expect(href).not.toMatch(/^\/(api\/docs|paper|probe|compare)(\/|$|\?)/);
  });

  it.each(EXPECTED_NAV)('marks only %s with aria-current="page" on its own route', (_, href) => {
    expect(current(renderToStaticMarkup(<SiteNav pathname={href} />))).toEqual([href]);
  });

  it.each([['/site/test-card'], ['/explore'], ['/explore/moon/extra'], ['/models'], ['/dat'], [null]])(
    'marks no link as current on %s',
    (pathname) => {
      expect(current(renderToStaticMarkup(<SiteNav pathname={pathname} />))).toEqual([]);
    },
  );

  it('shows the current link without relying on colour alone', () => {
    const html = renderToStaticMarkup(<SiteNav pathname="/model" />);
    for (const link of links(html)) {
      const bold = classesOf(link).includes('font-bold');
      expect(bold, link.text).toBe(link.attributes.get('aria-current') === 'page');
    }
  });

  it('is keyboard-operable in DOM order with the global focus ring', () => {
    const html = renderToStaticMarkup(<SiteNav pathname="/" />);
    for (const element of elements(html)) {
      expect(element.attributes.has('tabindex'), `<${element.tag}>`).toBe(false);
      expect(classesOf(element).filter((name) => /(^|:)(outline-|ring-0)/.test(name))).toEqual([]);
    }
    for (const link of links(html)) expect(link.attributes.get('href')).toMatch(/^\//);
    expect(elements(html).filter((element) => /^(button|input|select|textarea)$/.test(element.tag))).toEqual([]);
  });

  it('reads the current route with usePathname in SiteHeader', () => {
    state.pathname = '/data';
    expect(current(renderToStaticMarkup(<SiteHeader />))).toEqual(['/data']);
    state.pathname = null;
    expect(current(renderToStaticMarkup(<SiteHeader />))).toEqual([]);
  });

  it('is the only client module: next/link and usePathname, no bundle data and no provider', () => {
    const clients = appSources().filter((file) => isClientModule(readFileSync(join(WEB_DIR, file), 'utf8')));
    expect(clients).toEqual(['components/site-header.tsx']);
    const header = readFileSync(join(WEB_DIR, 'components', 'site-header.tsx'), 'utf8');
    const imports = [...header.matchAll(/^import\b[^'"]*['"]([^'"]+)['"]/gm)].map((match) => match[1]);
    expect(imports.sort()).toEqual(['next/link', 'next/navigation', 'react']);
    expect(header).not.toMatch(/Provider|createContext|useContext|Adapter/);
  });
});

describe('skip link', () => {
  it('jumps to the main landmark', () => {
    const link = only(links(renderToStaticMarkup(<SkipLink />)), 'the skip link');
    expect(SKIP_LINK_TEXT).toBe('Skip to content');
    expect(link.text).toBe('Skip to content');
    expect(link.attributes.get('href')).toBe('#main');
  });

  it('is hidden until focused, then shown on the page background', async () => {
    const classes = classesOf(only(links(renderToStaticMarkup(<SkipLink />)), 'the skip link'));
    const { root } = await compile(classes);
    const rest = new Map<string, string>();
    const focused = new Map<string, string>();
    root.walkRules((rule) => {
      const names = classes.filter((name) => rule.selector.startsWith(`.${name.replace(/:/g, '\\:')}`));
      if (names.length === 0) return;
      const target = rule.selector.endsWith(':focus') ? focused : rest;
      rule.walkDecls((decl) => target.set(decl.prop, decl.value));
    });
    expect(rest.get('clip-path')).toBe('inset(50%)');
    expect(rest.get('position')).toBe('absolute');
    expect(focused.get('clip-path')).toBe('none');
    expect(focused.get('position')).toBe('fixed');
    expect(focused.get('width')).toBe('auto');
    expect(focused.get('background-color')).toBe('var(--background)');
    expect(classes.filter((name) => /(^|:)(outline-|ring-0)/.test(name))).toEqual([]);
  });
});

describe('site footer', () => {
  it('shows the T0 bundle-version text and the schema version', () => {
    const html = renderToStaticMarkup(<SiteFooter bundle={MOCK} />);
    const footer = only(
      elements(html).filter((element) => element.tag === 'footer'),
      '<footer>',
    );
    expect(footer.text).toBe('Bundle version: mock-0 · Schema 1.0.0');
    expect(footer.text).toContain(shellText(MOCK).footer);
    expect(textOf(renderToStaticMarkup(<SiteFooter bundle={MODE_B} />))).toBe('Bundle version: v1 · Schema 1.2.3');
  });
});

describe('root layout', () => {
  it('renders skip link, mock banner, mode banner, navigation, main and footer in that order', () => {
    const html = layout(MOCK);
    const all = elements(html);
    const skip = only(
      links(html).filter((link) => link.text === SKIP_LINK_TEXT),
      'the skip link',
    );
    const mock = only(
      all.filter((element) => element.attributes.get('role') === 'status'),
      'the mock banner',
    );
    const mode = only(
      all.filter((element) => element.tag === 'p' && element.text.startsWith('Shipping mode: ')),
      'the mode banner',
    );
    const nav = only(
      all.filter((element) => element.tag === 'nav'),
      '<nav>',
    );
    const main = only(
      all.filter((element) => element.tag === 'main'),
      '<main>',
    );
    const footer = only(
      all.filter((element) => element.tag === 'footer'),
      '<footer>',
    );
    const body = only(
      all.filter((element) => element.tag === 'body'),
      '<body>',
    );
    const firstInBody = all.find((element) => element.start > body.start);
    expect(firstInBody?.start).toBe(skip.start);
    expect([skip, mock, mode, nav, main, footer].map((element) => element.start)).toEqual(
      [skip.start, mock.start, mode.start, nav.start, main.start, footer.start].sort((a, b) => a - b),
    );
    expect(mock.text).toBe(MOCK_BANNER);
    expect(mode.text).toBe('Shipping mode: not chosen (G0 NOT_RUN)');
    expect(footer.text).toBe('Bundle version: mock-0 · Schema 1.0.0');
    expect(main.text).toBe('test-page-heading');
    expect(main.attributes.get('id')).toBe(skip.attributes.get('href')?.slice(1));
  });

  it('puts the banners and navigation in the page header, outside main', () => {
    const all = elements(layout(MOCK));
    const header = only(
      all.filter((element) => element.tag === 'header'),
      '<header>',
    );
    for (const text of [MOCK_BANNER, 'Shipping mode: not chosen (G0 NOT_RUN)', 'Home Moon Mars Earth Model Methods Data']) {
      expect(header.text).toContain(text);
    }
    const main = only(
      all.filter((element) => element.tag === 'main'),
      '<main>',
    );
    expect(main.text).not.toContain('Shipping mode');
    expect(main.text).not.toContain('Mock bundle');
  });

  it('shows no mock banner, and the mode title, for a real bundle', () => {
    const html = layout(MODE_B);
    expect(textOf(html)).not.toContain('Mock bundle');
    expect(elements(html).filter((element) => element.attributes.get('role') === 'status')).toEqual([]);
    expect(textOf(html)).toContain(
      'Shipping mode: B — Terrain analog candidates for Connecting Ridge, with radar context',
    );
    expect(textOf(html)).toContain('Bundle version: v1 · Schema 1.2.3');
  });

  it('marks the active route in the shared navigation', () => {
    expect(current(layout(MOCK, '/methods'))).toEqual(['/methods']);
  });

  it('sets the document language', () => {
    const html = only(
      elements(layout(MOCK)).filter((element) => element.tag === 'html'),
      '<html>',
    );
    expect(html.attributes.get('lang')).toBe('en');
  });

  it('stays a static server component that adds no provider', () => {
    const source = readFileSync(join(WEB_DIR, 'app', 'layout.tsx'), 'utf8');
    expect(source).not.toMatch(/NuqsAdapter|nuqs|Provider/);
    expect(source).not.toMatch(/next\/headers|cookies\(|headers\(|connection\(|force-dynamic/);
  });
});

describe('home page', () => {
  it('shows the title and one factual line, with no numbers, targets or claims', () => {
    const html = renderToStaticMarkup(<Home />);
    expect(textOf(html)).toBe(`${SITE_TITLE} ${HOME_LINE}`);
    expect(HOME_LINE).toBe('These pages show records from the bundle named in the footer.');
    expect(HOME_LINE).not.toMatch(/\d/);
    expect(elements(html).filter((element) => element.tag === 'main')).toEqual([]);
  });

  it('fails the build if it would become dynamic', () => {
    expect(dynamic).toBe('error');
  });
});
