// The design tokens of app/globals.css (PRD 8.2, 8.10), read with PostCSS so the tests check the
// shipped values, not a copy of them.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import postcss from 'postcss';

import { WEB_DIR } from './helpers.ts';

export const GLOBALS = join(WEB_DIR, 'app', 'globals.css');

/** The custom properties of the top-level :root rule, in source order. */
export function rootTokens(): Map<string, string> {
  const tokens = new Map<string, string>();
  for (const node of postcss.parse(readFileSync(GLOBALS, 'utf8')).nodes) {
    if (node.type !== 'rule' || node.selector !== ':root') continue;
    node.walkDecls((decl) => {
      if (decl.prop.startsWith('--')) tokens.set(decl.prop.slice(2), decl.value);
    });
  }
  return tokens;
}

export function token(name: string): string {
  const value = rootTokens().get(name);
  if (value === undefined) throw new Error(`app/globals.css :root defines no --${name}`);
  return value;
}
