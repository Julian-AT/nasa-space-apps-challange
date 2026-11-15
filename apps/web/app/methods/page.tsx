// `/methods` (PRD 8.6, 9, 10 "Validation board"), statically rendered inside the root layout's
// main landmark. It shows the validation board: the GateRecords of the bundle's validation.json,
// read at build time through loadBundle (lib/bundle.ts). Beyond its headings the page adds no
// text: every status and result on it comes from the records.
import type { Metadata } from 'next';

import { PageHeader } from '../../components/page-header.tsx';
import { Section } from '../../components/section.tsx';
import { GatesBoard } from '../../components/gates-board.tsx';
import { loadBundle } from '../../lib/bundle.ts';
import { SITE_TITLE } from '../../lib/shell.ts';

// Fail the build if anything here would make the route dynamic.
export const dynamic = 'error';

export const metadata: Metadata = { title: `Methods · ${SITE_TITLE}` };

export default function Methods() {
  const { validation } = loadBundle();
  return (
    <>
      <PageHeader title="Methods" />
      <Section title="Validation board" id="validation-board">
        <GatesBoard validation={validation} />
      </Section>
    </>
  );
}
