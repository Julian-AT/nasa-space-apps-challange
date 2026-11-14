// `/` (PRD 8.6), statically rendered inside the root layout's main landmark. Until real cards
// exist it shows the title and one factual line: no numbers, targets or claims. make web-smoke
// checks that main holds exactly this text (HOME_LINE in scripts/page-check.ts). T7 builds the
// hero once cards exist.
import { SITE_TITLE } from '../lib/shell.ts';

// Fail the build if anything here would make the route dynamic.
export const dynamic = 'error';

export default function Home() {
  return (
    <>
      <h1>{SITE_TITLE}</h1>
      <p className="mt-2">These pages show records from the bundle named in the footer.</p>
    </>
  );
}
