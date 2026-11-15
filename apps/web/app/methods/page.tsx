// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// `/methods` (PRD 8.6, 9, 10 "Validation board"), statically rendered inside the root layout's
// main landmark. Part one is Terraluna's own: the GateRecords of the bundle's validation.json, read
// at build time through loadBundle (lib/bundle.ts), in ShadowAstra's "How it works" layout. Part two,
// clearly separated and labelled, is ShadowAstra Atlas's own methods page explaining the factor
// model behind the scores on /find and /explore; its numbers are ShadowAstra's, not Terraluna's.
import type { Metadata } from 'next';

import { EvidenceLegend } from '../../components/evidence-legend.tsx';
import { GatesBoard } from '../../components/gates-board.tsx';
import { MethodsHero, MethodsKicker } from '../../components/shadowastra/learn/MethodsHero.tsx';
import ShadowAstraMethods from '../../components/shadowastra/learn/ShadowAstraMethods.tsx';
import { loadBundle } from '../../lib/bundle.ts';
import { SITE_TITLE } from '../../lib/shell.ts';

// Fail the build if anything here would make the route dynamic.
export const dynamic = 'error';

export const metadata: Metadata = { title: `Methods · ${SITE_TITLE}` };

const LEAD =
  "Each release gate with its status, scope, result and consequence, as recorded in the bundle's validation.json. A status is always shown as its literal text, and a missing field is labelled Unavailable.";

export default function Methods() {
  const { validation } = loadBundle();
  return (
    <div className="bg-void">
      <MethodsHero title="Methods" lead={LEAD} />
      <section aria-labelledby="validation-board" className="border-t border-line px-4 py-20 sm:px-6">
        <div className="mx-auto flex max-w-[1200px] flex-col gap-8">
          <MethodsKicker n="01" id="validation-board">Validation board</MethodsKicker>
          <GatesBoard validation={validation} />
        </div>
      </section>
      <div className="border-t border-line px-4 py-16 sm:px-6">
        <div className="mx-auto max-w-[1200px]">
          <EvidenceLegend />
        </div>
      </div>
      <section aria-labelledby="shadowastra-model" className="border-t-4 border-line-2">
        <div className="px-4 pt-20 pb-6 sm:px-6">
          <div className="mx-auto max-w-[1200px] rounded-2xl border border-line-2 bg-panel/60 p-6 sm:p-8">
            <p className="inline-flex items-center rounded-full border border-flare/40 bg-flare/10 px-3 py-1 font-mono text-[11px] tracking-[0.14em] text-flare uppercase">
              From ShadowAstra Atlas (MIT, Team ShadowAstra)
            </p>
            <h2 id="shadowastra-model" className="mt-5 max-w-[900px] text-[clamp(28px,3.6vw,48px)] leading-[1.05] font-medium tracking-[-0.03em]">
              How ShadowAstra Atlas scores sites (the factor model used on /find and /explore)
            </h2>
            <p className="mt-4 max-w-[820px] text-[15px] leading-relaxed text-chrome">
              Everything below is ShadowAstra Atlas&apos;s own methods page, copied with permission. It explains the factor scores shown on /find and /explore, which come from their model and their 0.5° Earth grid. Its tests, ranks and counts are ShadowAstra&apos;s validation, not Terraluna results; Terraluna&apos;s own gates are the validation board above.
            </p>
          </div>
        </div>
        <ShadowAstraMethods />
      </section>
    </div>
  );
}
