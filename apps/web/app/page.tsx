// `/` (PRD 8.6), statically rendered. Until real cards exist it shows only the layout's banner,
// shipping-mode line and footer: no numbers, targets or claims. T7 builds the hero.

import { SITE_TITLE } from '../lib/shell.ts';

// Fail the build if anything here would make the route dynamic.
export const dynamic = 'error';

const MAIN_STYLE = { padding: '0 1rem' } as const;

export default function Home() {
  return (
    <main style={MAIN_STYLE}>
      <h1>{SITE_TITLE}</h1>
    </main>
  );
}
