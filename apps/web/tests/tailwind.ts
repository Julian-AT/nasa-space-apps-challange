// The CSS that Tailwind v4 compiles from app/globals.css (PRD 8.2, 8.10), for tests that check a
// utility exists and what it declares, instead of trusting a class name.
import { readFileSync } from 'node:fs';

import tailwindcss from '@tailwindcss/postcss';
import postcss, { type Root } from 'postcss';

import { WEB_DIR } from './helpers.ts';
import { GLOBALS } from './tokens.ts';

/** Compiles app/globals.css as next build does, plus the given candidate classes. */
export async function compile(candidates: readonly string[]): Promise<{ css: string; root: Root }> {
  const source = `${readFileSync(GLOBALS, 'utf8')}\n@source inline(${JSON.stringify(candidates.join(' '))});\n`;
  const result = await postcss([tailwindcss({ base: WEB_DIR, optimize: false })]).process(source, {
    from: GLOBALS,
  });
  return { css: result.css, root: result.root };
}

/** The declarations of every rule whose selector list contains `selector`, last one winning. */
export function declarations(root: Root, selector: string): Map<string, string> {
  const found = new Map<string, string>();
  root.walkRules((rule) => {
    if (!rule.selectors.includes(selector)) return;
    rule.each((node) => {
      if (node.type === 'decl') found.set(node.prop, node.value);
    });
  });
  return found;
}
