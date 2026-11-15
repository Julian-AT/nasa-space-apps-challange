'use client';

// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The top of /methods in the layout of ShadowAstra's "How it works" hero: Terraluna's own title and
// lead on the left, ShadowAstra's dot globe on the right. The globe shows evenly tinted land dots
// with no score layer (one constant value), so it carries no result, Terraluna's or ShadowAstra's.
import { lazy, Suspense, useMemo } from 'react';
import { motion } from 'motion/react';
import { ArrowDown } from 'lucide-react';

import { useGrid } from '@/lib/shadowastra/grid';
import { EASE } from '@/components/shadowastra/ui/kit';
import '@/components/shadowastra/shadowastra.css';

const DotGlobe = lazy(() => import('@/components/shadowastra/learn/DotGlobe'));

export function MethodsHero({ title, lead }: { title: string; lead: string }) {
  const { grid } = useGrid();
  // One constant value for every land cell: an even tint, deliberately carrying no data layer.
  const flat = useMemo(() => (grid ? new Float32Array(grid.n).fill(0.45) : null), [grid]);
  return (
    <section data-theme="dark" className="relative isolate flex min-h-[72dvh] flex-col overflow-hidden bg-void text-platinum">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(60%_60%_at_72%_42%,rgba(143,180,234,0.10),transparent_65%),radial-gradient(40%_50%_at_10%_90%,rgba(242,200,121,0.06),transparent_70%)]" />
      <div aria-hidden="true" className="absolute inset-y-0 right-[-30vw] w-[100vw] opacity-60 sm:right-[-14vw] lg:right-[-4vw] lg:w-[52vw] lg:opacity-100">
        <Suspense fallback={null}>
          <DotGlobe grid={grid} values={flat} tint="#8fb4ea" startLon={10} className="h-full w-full" size={2.1} stillNote={false} />
        </Suspense>
      </div>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-32 bg-gradient-to-t from-void to-transparent" />
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(90deg,rgba(7,8,10,0.92)_0%,rgba(7,8,10,0.6)_70%,rgba(7,8,10,0.2)_100%)] lg:hidden" />
      <div className="relative mx-auto flex w-full max-w-[1400px] flex-1 flex-col justify-center px-4 pt-20 pb-14 sm:px-6">
        <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1 }} className="flex items-center gap-4 font-mono text-[11px] tracking-[0.24em] text-chrome uppercase">
          <span aria-hidden="true" className="h-px w-8 bg-chrome/50" /> Terraluna · how the release is checked
        </motion.p>
        <h1 className="mt-7 max-w-[760px] text-[clamp(44px,7vw,108px)] leading-[0.95] font-medium tracking-[-0.05em]">{title}</h1>
        <motion.p initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.9, ease: EASE, delay: 0.2 }} className="mt-7 max-w-[560px] text-[17px] leading-relaxed text-chrome">
          {lead}
        </motion.p>
        <div className="mt-9 flex flex-wrap gap-3">
          <a href="#validation-board" className="btn-primary inline-flex items-center gap-2 rounded-full px-6 py-3.5 text-[14px] font-medium">
            Validation board <ArrowDown size={16} aria-hidden="true" />
          </a>
          <a href="#shadowastra-model" className="inline-flex items-center gap-2 rounded-full border border-line-2 bg-void/40 px-6 py-3.5 text-[14px] text-platinum backdrop-blur hover:border-chrome">
            ShadowAstra factor model <ArrowDown size={15} aria-hidden="true" />
          </a>
        </div>
        <p className="mt-10 max-w-[520px] text-[12px] text-dim">Globe: ShadowAstra Atlas 0.5° land grid drawn as evenly tinted dots, decoration only (no score layer).</p>
      </div>
    </section>
  );
}

/** A numbered section kicker in ShadowAstra's style, as a real h2. */
export function MethodsKicker({ n, id, children }: { n: string; id: string; children: string }) {
  return (
    <h2 id={id} className="flex items-center gap-4 font-mono text-[12px] font-normal tracking-[0.24em] text-chrome uppercase">
      <span className="text-flare">{n}</span>
      <span aria-hidden="true" className="h-px w-8 bg-chrome/50" />
      {children}
    </h2>
  );
}
