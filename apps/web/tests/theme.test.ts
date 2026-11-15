// The design tokens in app/globals.css and the CSS that Tailwind v4 compiles from them (PRD 8.2,
// 8.10, D35): one high-contrast light theme that meets WCAG 2.2 AA text contrast, a visible focus
// ring, a red reserved for the mock banner, and no webfont, image or dark theme.
import { readFileSync } from 'node:fs';

import postcss from 'postcss';
import { describe, expect, it } from 'vitest';

import { composite, contrastRatio, isRed, parseHex } from './contrast.ts';
import { compile, declarations } from './tailwind.ts';
import { GLOBALS, token } from './tokens.ts';

/** WCAG 2.2 SC 1.4.3 (AA) for text, SC 1.4.11 for focus indicators and component borders. */
const AA_TEXT = 4.5;
const AA_NON_TEXT = 3;

const COLOUR_TOKENS = [
  'background',
  'foreground',
  'primary',
  'primary-foreground',
  'secondary',
  'secondary-foreground',
  'muted',
  'muted-foreground',
  'accent',
  'accent-foreground',
  'border',
  'input',
  'ring',
  'card',
  'card-foreground',
  'popover',
  'popover-foreground',
  'destructive',
  'destructive-foreground',
  'line',
  'mock',
  'mock-foreground',
  'evidence-observed',
  'evidence-derived',
  'evidence-predicted',
  'evidence-assumed',
  'evidence-unavailable',
  'status-pass',
  'status-fail',
  'status-partial',
  'status-not-run',
  'status-not-applicable',
] as const;

/** Tokens that may be warm red/coral: the mock banner, destructive actions and a FAIL status. */
const REDDISH = new Set(['mock', 'destructive', 'status-fail']);

const SURFACES = ['background', 'card', 'secondary', 'muted'] as const;
const EVIDENCE_AND_STATUS = [
  'evidence-observed',
  'evidence-derived',
  'evidence-predicted',
  'evidence-assumed',
  'evidence-unavailable',
  'status-pass',
  'status-fail',
  'status-partial',
  'status-not-run',
  'status-not-applicable',
] as const;

// Text on its background: each pair the components and the layout use.
const TEXT_PAIRS = [
  ['foreground', 'background'],
  ['foreground', 'secondary'],
  ['foreground', 'muted'],
  ['foreground', 'accent'],
  ['primary', 'background'],
  ['primary-foreground', 'primary'],
  ['secondary-foreground', 'secondary'],
  ['muted-foreground', 'muted'],
  ['muted-foreground', 'background'],
  ['accent-foreground', 'accent'],
  ['card-foreground', 'card'],
  ['popover-foreground', 'popover'],
  ['muted-foreground', 'card'],
  ['primary', 'card'],
  ['destructive-foreground', 'destructive'],
  ['mock-foreground', 'mock'],
] as const;

// The focus outline is drawn 2px outside the element, on whatever surface surrounds it.
const NON_TEXT_PAIRS = [
  ['ring', 'background'],
  ['ring', 'secondary'],
  ['ring', 'muted'],
  ['ring', 'accent'],
  ['ring', 'card'],
  ['border', 'background'],
  ['border', 'card'],
  ['input', 'background'],
] as const;

describe('contrast helper', () => {
  it('reproduces known WCAG contrast ratios', () => {
    expect(contrastRatio('#ffffff', '#000000')).toBeCloseTo(21, 6);
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 6);
    // #767676 is the lightest grey that passes 4.5:1 on white; #777777 just fails.
    expect(contrastRatio('#767676', '#ffffff')).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio('#777777', '#ffffff')).toBeLessThan(4.5);
    expect(contrastRatio('#123456', '#123456')).toBe(1);
  });

  it('accepts only #rrggbb', () => {
    expect(() => parseHex('oklch(0.5 0.1 20)')).toThrow(/#rrggbb/);
    expect(() => parseHex('#fff')).toThrow(/#rrggbb/);
  });

  it('composites a translucent colour over a backdrop in sRGB, as browsers paint it', () => {
    // 127.5 rounds to 128 (0x80).
    expect(composite('#000000', 0.5, '#ffffff')).toBe('#808080');
    expect(composite('#171717', 0.8, '#ffffff')).toBe('#454545');
    expect(composite('#123456', 1, '#abcdef')).toBe('#123456');
    expect(composite('#123456', 0, '#abcdef')).toBe('#abcdef');
    expect(() => composite('#000000', 1.5, '#ffffff')).toThrow(/alpha/);
    expect(() => composite('#000000', -0.1, '#ffffff')).toThrow(/alpha/);
  });

  it('tells red from grey and blue', () => {
    expect(isRed('#b91c1c')).toBe(true);
    expect(isRed('#71717a')).toBe(false);
    expect(isRed('#1d4ed8')).toBe(false);
  });
});

describe('design tokens in app/globals.css', () => {
  it('defines every colour token as an exact #rrggbb value', () => {
    for (const name of COLOUR_TOKENS) {
      expect(() => parseHex(token(name)), `--${name}`).not.toThrow();
    }
  });

  it('meets WCAG 2.2 AA contrast for text (4.5:1)', () => {
    for (const [text, surface] of TEXT_PAIRS) {
      const ratio = contrastRatio(token(text), token(surface));
      expect(ratio, `--${text} on --${surface}`).toBeGreaterThanOrEqual(AA_TEXT);
    }
  });

  it('meets 3:1 for the focus ring and component borders', () => {
    for (const [fill, surface] of NON_TEXT_PAIRS) {
      const ratio = contrastRatio(token(fill), token(surface));
      expect(ratio, `--${fill} against --${surface}`).toBeGreaterThanOrEqual(AA_NON_TEXT);
    }
  });

  it('reads evidence-label and gate-status text at 4.5:1 on its own 15% tint over every surface', () => {
    for (const name of EVIDENCE_AND_STATUS) {
      for (const surface of SURFACES) {
        const fill = composite(token(name), 0.15, token(surface));
        expect(contrastRatio(token(name), fill), `--${name} on its tint over --${surface}`).toBeGreaterThanOrEqual(AA_TEXT);
      }
    }
  });

  it('keeps the mock banner red and limits other reds to destructive and FAIL', () => {
    expect(isRed(token('mock'))).toBe(true);
    for (const name of COLOUR_TOKENS) {
      if (REDDISH.has(name)) continue;
      expect(isRed(token(name)), `--${name} must not be red`).toBe(false);
    }
  });

  it('is a dark theme only: no light scheme, a near-black page', () => {
    const root = postcss.parse(readFileSync(GLOBALS, 'utf8'));
    expect(declarations(root, ':root').get('color-scheme')).toBe('dark');
    expect(contrastRatio(token('background'), '#000000')).toBeLessThan(1.3);
    expect(contrastRatio(token('foreground'), token('background'))).toBeGreaterThanOrEqual(7);
  });
});

describe('compiled Tailwind CSS', () => {
  it('maps each token to its utility through a CSS variable', async () => {
    const { root } = await compile(COLOUR_TOKENS.map((name) => `bg-${name}`));
    for (const name of COLOUR_TOKENS) {
      expect(declarations(root, `.bg-${name}`).get('background-color'), `.bg-${name}`).toBe(
        `var(--${name})`,
      );
    }
  });

  it('offers no red or rose from the default palette, so only the mock banner is red', async () => {
    const { root } = await compile(['bg-mock', 'bg-red-700', 'text-red-600', 'bg-rose-700']);
    expect(declarations(root, '.bg-mock').size).toBeGreaterThan(0);
    for (const selector of ['.bg-red-700', '.text-red-600', '.bg-rose-700']) {
      expect(declarations(root, selector).size, selector).toBe(0);
    }
  });

  it('draws a visible focus outline in the ring colour on every focusable element', async () => {
    const { root } = await compile([]);
    const focus = declarations(root, ':focus-visible');
    const outline = /^(\d+(?:\.\d+)?)px solid var\(--ring\)$/.exec(focus.get('outline') ?? '');
    expect(outline, `outline: ${focus.get('outline') ?? '(none)'}`).not.toBeNull();
    expect(Number(outline?.[1])).toBeGreaterThanOrEqual(2);
    expect(focus.get('outline-offset')).toBe('2px');
  });

  it('keeps links underlined, which Preflight would otherwise remove (WCAG 1.4.1)', async () => {
    const { root } = await compile([]);
    let line: string | undefined;
    root.walkRules((rule) => {
      if (!rule.selectors.includes('a')) return;
      rule.walkDecls((decl) => {
        if (['text-decoration', '-webkit-text-decoration', 'text-decoration-line'].includes(decl.prop)) {
          line = decl.value.split(/\s+/)[0];
        }
      });
    });
    expect(line).toBe('underline');
  });

  it('applies the theme to the page body', async () => {
    const { root } = await compile([]);
    const body = declarations(root, 'body');
    expect(body.get('background-color')).toBe('var(--background)');
    expect(body.get('color')).toBe('var(--foreground)');
  });

  it('downloads no webfont or image and has no dark theme', async () => {
    const { css, root } = await compile(COLOUR_TOKENS.map((name) => `text-${name}`));
    let fontFaces = 0;
    root.walkAtRules('font-face', () => {
      fontFaces += 1;
    });
    expect(fontFaces).toBe(0);
    expect(css).not.toMatch(/url\(/i);
    expect(css).not.toMatch(/prefers-color-scheme/);
    expect(css).not.toMatch(/\.dark\b/);
  });
});
