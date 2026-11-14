// The shadcn/ui primitives under components/ui (PRD 8.2, 8.10): server components that render
// plain elements, keep the global focus outline, and never use the red reserved for the mock
// banner or a dark variant.
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { Badge, badgeVariants } from '../components/ui/badge.tsx';
import { Button, buttonVariants } from '../components/ui/button.tsx';
import { cn } from '../lib/utils.ts';
import { WEB_DIR } from './helpers.ts';

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
    expect(classes).not.toMatch(/destructive|red/);
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
    expect(classes).not.toMatch(/destructive|red/);
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
