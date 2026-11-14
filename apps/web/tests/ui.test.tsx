// The shadcn/ui primitives under components/ui (PRD 8.2, 8.10): server components that render
// plain elements, keep the global focus outline, meet WCAG 2.2 AA text contrast in every state,
// and never use the red reserved for the mock banner or a dark variant.
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { Badge, badgeVariants } from '../components/ui/badge.tsx';
import { Button, buttonVariants } from '../components/ui/button.tsx';
import { cn } from '../lib/utils.ts';
import { contrastRatio } from './contrast.ts';
import { WEB_DIR } from './helpers.ts';
import { rootTokens, token } from './tokens.ts';
import { describeCase, resolveCase, type TextCase, textCases } from './variant-colours.ts';

const UI_DIR = join(WEB_DIR, 'components', 'ui');

const BUTTON_VARIANTS = ['default', 'outline', 'secondary', 'ghost', 'link'] as const;
const BUTTON_SIZES = ['default', 'xs', 'sm', 'lg'] as const;
const BADGE_VARIANTS = ['default', 'secondary', 'outline', 'ghost', 'link'] as const;

function everyClassList(): string[] {
  const lists: string[] = [];
  for (const variant of BUTTON_VARIANTS) {
    for (const size of BUTTON_SIZES) lists.push(buttonVariants({ variant, size }));
  }
  for (const variant of BADGE_VARIANTS) lists.push(badgeVariants({ variant }));
  return lists;
}

describe('cn', () => {
  it('joins conditional classes and lets the later Tailwind class win', () => {
    expect(cn('px-2', false, undefined, null, 'py-1')).toBe('px-2 py-1');
    expect(cn('px-2', 'px-4')).toBe('px-4');
    expect(cn('bg-primary', 'bg-secondary')).toBe('bg-secondary');
  });
});

describe('Button', () => {
  it('renders a button with its variant and size as data attributes', () => {
    const html = renderToStaticMarkup(<Button>test-action</Button>);
    expect(html).toMatch(/^<button [^>]*data-slot="button"/);
    expect(html).toContain('data-variant="default"');
    expect(html).toContain('data-size="default"');
    expect(html).toContain('>test-action</button>');
  });

  it('renders its child element instead of a button with asChild', () => {
    const html = renderToStaticMarkup(
      <Button asChild variant="outline">
        <a href="/test-path">test-link</a>
      </Button>,
    );
    expect(html).toMatch(/^<a [^>]*href="\/test-path"/);
    expect(html).toContain('data-slot="button"');
    expect(html).not.toContain('<button');
  });

  it('has no destructive variant, because red is reserved for the mock banner', () => {
    // @ts-expect-error -- 'destructive' is not a Button variant.
    const classes = buttonVariants({ variant: 'destructive' });
    expect(classes).not.toMatch(/destructive|-(red|rose)-\d/);
  });
});

describe('Badge', () => {
  it('renders a span with its variant as a data attribute', () => {
    const html = renderToStaticMarkup(<Badge variant="outline">test-badge</Badge>);
    expect(html).toMatch(/^<span [^>]*data-slot="badge"/);
    expect(html).toContain('data-variant="outline"');
    expect(html).toContain('>test-badge</span>');
  });

  it('has no destructive variant', () => {
    // @ts-expect-error -- 'destructive' is not a Badge variant.
    const classes = badgeVariants({ variant: 'destructive' });
    expect(classes).not.toMatch(/destructive|-(red|rose)-\d/);
  });
});

describe('components/ui', () => {
  it('never hides the focus outline that app/globals.css draws', () => {
    for (const classes of everyClassList()) {
      expect(classes).not.toMatch(/(^|\s|:)outline-(none|hidden)(\s|$)/);
    }
  });

  it('uses no mock, destructive or dark-theme class', () => {
    for (const classes of everyClassList()) {
      expect(classes).not.toMatch(/mock|destructive|(^|\s)dark:/);
    }
  });

  it('holds server components only: no client directive', () => {
    for (const name of readdirSync(UI_DIR)) {
      const source = readFileSync(join(UI_DIR, name), 'utf8');
      expect(source, name).not.toMatch(/^\s*['"]use client['"]/m);
    }
  });
});

/** WCAG 2.2 SC 1.4.3 (AA) for text. */
const AA_TEXT = 4.5;

/** The page surfaces a button or badge may sit on: the ones the focus ring is checked against. */
const SURFACES = ['background', 'secondary', 'muted', 'accent'] as const;

function variantClassLists(): (readonly [string, string])[] {
  return [
    ...BUTTON_VARIANTS.map((variant) => [`Button ${variant}`, buttonVariants({ variant })] as const),
    ...BADGE_VARIANTS.map((variant) => [`Badge ${variant}`, badgeVariants({ variant })] as const),
  ];
}

describe('text contrast of every variant and state (WCAG 2.2 SC 1.4.3)', () => {
  const tokens = new Set(rootTokens().keys());

  it('meets 4.5:1 at rest, on hover and when expanded, on every page surface', () => {
    for (const [label, classes] of variantClassLists()) {
      for (const textCase of textCases(classes, tokens)) {
        for (const surface of SURFACES) {
          const { text, background } = resolveCase(textCase, token(surface), token);
          expect(
            contrastRatio(text, background),
            `${label}, ${describeCase(textCase)}, over --${surface}`,
          ).toBeGreaterThanOrEqual(AA_TEXT);
        }
      }
    }
  });

  it('covers the hover and expanded states, not only the resting one', () => {
    const states = new Set(
      variantClassLists().flatMap(([, classes]) => textCases(classes, tokens).map((c) => c.state)),
    );
    for (const state of ['rest', 'hover', '[a]:hover', 'aria-expanded', 'hover+aria-expanded']) {
      expect(states, state).toContain(state);
    }
  });
});

describe('textCases, the state model behind the contrast test', () => {
  const tokens = new Set(['foreground', 'primary', 'primary-foreground', 'muted-foreground', 'accent']);
  const HEX: Record<string, string> = { primary: '#171717', 'primary-foreground': '#fafafa' };
  const hex = (name: string): string => {
    const value = HEX[name];
    if (value === undefined) throw new Error(`no test colour for --${name}`);
    return value;
  };
  const only = (cases: readonly TextCase[], state: string): TextCase => {
    const found = cases.filter((c) => c.state === state);
    if (found.length !== 1) throw new Error(`${String(found.length)} cases for ${state}`);
    return found[0] as TextCase;
  };

  it('composites a translucent hover background over the surface', () => {
    const hover = only(textCases('bg-primary text-primary-foreground hover:bg-primary/20', tokens), 'hover');
    expect(hover.background).toEqual({ token: 'primary', alpha: 0.2 });
    expect(hover.text).toEqual({ token: 'primary-foreground', alpha: 1 });
    // 20% of #171717 over white is #d1d1d1, far below 4.5:1 for #fafafa, so the test would fail.
    const { text, background } = resolveCase(hover, '#ffffff', hex);
    expect(background).toBe('#d1d1d1');
    expect(contrastRatio(text, background)).toBeLessThan(AA_TEXT);
  });

  it('inherits the body text colour and the surface where no colour is set', () => {
    expect(textCases('text-sm bg-clip-padding', tokens)).toEqual([
      { state: 'rest', text: { token: 'foreground', alpha: 1 }, background: null },
    ]);
  });

  it('checks every combination of states that can apply at once', () => {
    const cases = textCases('hover:bg-accent aria-expanded:text-muted-foreground', tokens);
    expect(cases.map((c) => c.state)).toEqual(['rest', 'hover', 'aria-expanded', 'hover+aria-expanded']);
    expect(only(cases, 'hover+aria-expanded')).toEqual({
      state: 'hover+aria-expanded',
      text: { token: 'muted-foreground', alpha: 1 },
      background: { token: 'accent', alpha: 1 },
    });
  });

  it('skips the disabled state, which SC 1.4.3 exempts as an inactive component', () => {
    expect(textCases('disabled:opacity-50 disabled:hover:text-accent', tokens)).toEqual([
      { state: 'rest', text: { token: 'foreground', alpha: 1 }, background: null },
    ]);
  });

  it('refuses a class it cannot judge instead of skipping it', () => {
    expect(() => textCases('hover:opacity-80', tokens)).toThrow(/hover:opacity-80/);
    expect(() => textCases('bg-red-700', tokens)).toThrow(/bg-red-700/);
    expect(() => textCases('bg-linear-to-r', tokens)).toThrow(/bg-linear-to-r/);
    expect(() => textCases('text-primary/[0.5]', tokens)).toThrow(/text-primary\/\[0.5\]/);
    expect(() => textCases('hover:text-[#777777]', tokens)).toThrow(/text-\[#777777\]/);
  });
});
