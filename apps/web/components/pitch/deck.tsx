'use client';

// The /pitch deck: white full-viewport slides over the site chrome. Keys: → / d / Space next
// (steps through build-ups first), ← / a back, Home / End; N speaker notes + 2:00 timer + pace,
// P overview, F full screen, Esc closes panels. Click and swipe also move. The hash (#5 or #5.2)
// keeps place. Every number on a slide is read from the bundle (pitch-data.ts).
import { AnimatePresence, motion } from 'motion/react';
import dynamic from 'next/dynamic';
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { EarthIcon } from '@hugeicons/core-free-icons';
import { HugeiconsIcon } from '@hugeicons/react';

import { ArchDiagram, ARCH_STEPS } from './arch';
import { CountUp, EASE, HillTile, INK, LagProfile, LINE, MUTED, NuChart, WORLD } from './bits';
import { mmss, NOTES, TOTAL_S } from './notes';
import type { PitchData } from './pitch-data';

const LabTwin = dynamic(() => import('./lab-twin'), { ssr: false, loading: () => <div className="size-full bg-white" /> });
const FinderDemo = dynamic(() => import('./finder'), { ssr: false, loading: () => null });

const GITHUB = 'github.com/Julian-AT/nasa-space-apps-challange';
const SITE = 'terraluna-lovat.vercel.app';
const MONO = 'font-mono';

const BODY = { moon: 'Moon', mars: 'Mars' } as const;

function Unavailable({ reason }: { reason: string }) {
  return <span className="text-base font-normal text-zinc-400">Unavailable ({reason})</span>;
}

/** Staggered blur-fade entrance for slide elements. */
function In({ d = 0, children, className }: { d?: number; children: ReactNode; className?: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 14, filter: 'blur(8px)' }}
      animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
      transition={{ duration: 0.7, delay: 0.1 + d, ease: EASE }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

/** Emphasis by weight, never by colour. */
function A({ children }: { children: ReactNode }) {
  return <span className="font-semibold text-zinc-950">{children}</span>;
}

/** The brand mark and wordmark, large on the title and closing slides. */
function Brand({ size = 'lg' }: { size?: 'lg' | 'xl' }) {
  const big = size === 'xl';
  return (
    <div className={`flex items-center ${big ? 'gap-7' : 'gap-5'}`}>
      <span className={`grid shrink-0 place-items-center bg-zinc-950 ${big ? 'size-24 rounded-[1.6rem]' : 'size-20 rounded-2xl'}`}>
        <HugeiconsIcon icon={EarthIcon} strokeWidth={1.8} className={big ? 'size-14' : 'size-11'} style={{ color: '#ffffff' }} aria-hidden />
      </span>
      <span className={`${big ? 'text-[7.25rem]' : 'text-[6.5rem]'} font-bold leading-none tracking-[-0.05em]`} style={{ color: INK }}>
        Terraluna
      </span>
    </div>
  );
}

/** Content slide grid: bold title top-left with a one-line subtitle, content below. */
function Frame({ title, sub, children }: { title: ReactNode; sub?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex h-full flex-col gap-10">
      <In className="flex flex-col gap-2">
        <h2 className="text-[2.75rem] font-semibold leading-tight tracking-tight">{title}</h2>
        {sub ? <p className="text-xl" style={{ color: MUTED }}>{sub}</p> : null}
      </In>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}

/** A stats column: bold label, big accent number, small label, grey caption. */
function Stat({ label, children, unit, caption, d }: { label: string; children: ReactNode; unit: string; caption?: string | undefined; d: number }) {
  return (
    <In d={d} className="flex flex-col items-center gap-1 text-center">
      <span className="text-xl font-semibold">{label}</span>
      <span className="text-6xl font-bold tracking-tight text-zinc-950">
        {children}
      </span>
      <span className="mt-1 text-lg">{unit}</span>
      {caption ? <span className="max-w-[22rem] text-sm leading-snug" style={{ color: MUTED }}>{caption}</span> : null}
    </In>
  );
}

/** One numbered step of the fingerprint slide: number, title, visual. */
function Step({ n, title, d, children }: { n: number; title: string; d: number; children: ReactNode }) {
  return (
    <In d={d} className="flex h-full flex-col gap-5 rounded-3xl border border-zinc-200 bg-zinc-50 p-6">
      <div className="flex items-center gap-3">
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-zinc-950 text-sm font-bold text-white">{n}</span>
        <span className="text-xl font-semibold leading-tight tracking-tight">{title}</span>
      </div>
      <div className="flex min-h-0 flex-1 flex-col justify-center gap-4">{children}</div>
    </In>
  );
}

interface Slide {
  readonly id: string;
  readonly steps: number;
  /** Title and closing slides carry no footer. */
  readonly bare?: boolean;
  readonly body: (step: number) => ReactNode;
}

function slides(d: PitchData, qr: string | null): Slide[] {
  const m = d.model;
  // the Jezero and Rosamond Dry Lake card: the finder and fingerprint story
  const jr = d.results.find((r) => r.targetName.includes('Jezero')) ?? null;
  const moonT = d.targets.find((t) => t.body === 'moon');
  const opening = [
    { key: 'moon', body: 'Moon target', name: moonT?.name ?? 'Moon', tile: moonT?.tile ?? null },
    { key: 'mars', body: 'Mars target', name: jr?.targetName ?? 'Jezero crater', tile: jr?.targetTile ?? null },
    { key: 'earth', body: 'Earth match', name: jr?.earthName.split(',')[0] ?? 'Earth', tile: jr?.earthTile ?? null },
  ];
  return [
    {
      id: 'title',
      steps: 1,
      bare: true,
      body: () => (
        <div className="relative grid h-full grid-cols-[1fr_1fr] items-center gap-14">
          <div className="flex flex-col gap-10">
            <In>
              <Brand size="xl" />
            </In>
            <div className="flex flex-col gap-4">
              <In d={0.15}>
                <p className="text-[2.6rem] font-bold leading-tight tracking-tight">Find the Moon and Mars on Earth.</p>
              </In>
              <In d={0.25}>
                <p className="max-w-xl text-2xl leading-snug" style={{ color: MUTED }}>
                  A physics model and a learned encoder that measure terrain, live in the browser.
                </p>
              </In>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-4 pt-10">
            {opening.map((o, k) => (
              <motion.figure
                key={o.key}
                initial={{ opacity: 0, y: 24 }}
                animate={{ opacity: 1, y: k === 1 ? -40 : 0 }}
                transition={{ duration: 0.9, delay: 0.3 + k * 0.12, ease: EASE }}
                className="flex flex-col gap-3"
              >
                <HillTile tile={o.tile} className="w-full shadow-[0_24px_48px_-28px_rgba(9,9,11,0.5)]" />
                <figcaption className="flex flex-col">
                  <span className="text-xs font-semibold uppercase tracking-[0.14em]" style={{ color: MUTED }}>{o.body}</span>
                  <span className="whitespace-nowrap text-base font-semibold tracking-tight">{o.name}</span>
                </figcaption>
              </motion.figure>
            ))}
          </div>
          <p className="absolute bottom-0 left-0 text-sm" style={{ color: MUTED }}>
            <span className="font-semibold text-zinc-950">NASA Space Apps 2026</span>, Gradient Descent, November 2026
          </p>
        </div>
      ),
    },
    {
      id: 'problem',
      steps: 1,
      body: () => (
        <div className="flex h-full flex-col gap-10">
          <In className="flex flex-col gap-4">
            <h2 className="text-[3.25rem] font-bold leading-tight tracking-tight">Which place on Earth matches the Moon or Mars?</h2>
            <p className="flex flex-wrap items-center gap-2 text-base" style={{ color: MUTED }}>
              NASA candidate sites:
              {d.targets.map((t) => (
                <span key={t.targetId} className="rounded-full border border-zinc-200 bg-white px-3 py-0.5 text-sm font-medium text-zinc-950">
                  {t.name}, {BODY[t.body]}
                </span>
              ))}
            </p>
          </In>
          <div className="grid min-h-0 flex-1 grid-cols-2 gap-6">
            {[
              { head: 'Today', items: ['Analog sites are chosen by experience', 'Few known sites, no common measure', 'Hard to compare Moon, Mars and Earth terrain'], dark: false },
              { head: 'Terraluna', items: ['One terrain fingerprint for every body', 'Any point on Earth, measured live', 'An evidence card for every match'], dark: true },
            ].map((col, c) => (
              <In key={col.head} d={0.2 + c * 0.15} className="h-full">
                <div className="flex h-full flex-col gap-10 rounded-3xl border border-zinc-200 bg-zinc-50 p-10">
                  <span className={`w-fit rounded-full px-4 py-1 text-base font-semibold ${col.dark ? 'bg-zinc-950 text-white' : 'border border-zinc-300 bg-white text-zinc-950'}`}>{col.head}</span>
                  <ol className="relative flex flex-1 flex-col justify-around">
                    <span aria-hidden className="absolute bottom-4 left-[11px] top-4 w-px bg-zinc-300" />
                    {col.items.map((it, k) => (
                      <li key={it} className="relative flex items-center gap-6">
                        <span className={`relative z-10 grid size-6 shrink-0 place-items-center rounded-full border-2 ${col.dark ? 'border-zinc-950 bg-zinc-950' : 'border-zinc-400 bg-white'}`}>
                          <span className={`size-2 rounded-full ${col.dark ? 'bg-white' : 'bg-zinc-400'}`} />
                        </span>
                        <span className={`text-[1.6rem] leading-snug tracking-tight ${col.dark ? 'font-semibold text-zinc-950' : 'font-medium text-zinc-600'}`}>
                          <span className="mr-3 font-mono text-base text-zinc-400">{String(k + 1).padStart(2, '0')}</span>
                          {it}
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              </In>
            ))}
          </div>
        </div>
      ),
    },
    {
      id: 'physics',
      steps: 1,
      body: () => (
        <div className="flex h-full flex-col gap-8">
          <In className="flex flex-col gap-2">
            <h2 className="text-[2.75rem] font-bold leading-tight tracking-tight">Physics fingerprint</h2>
            <p className="text-xl" style={{ color: MUTED }}>Three steps, the same code in Python and in your browser.</p>
          </In>
          <div className="grid min-h-0 flex-1 grid-cols-[1fr_1fr_1.3fr] gap-6">
            <Step n={1} title="Take a 5 km window" d={0.15}>
              <div className="grid grid-cols-2 gap-3">
                {jr ? (
                  [
                    { t: jr.targetTile, name: jr.targetName, body: BODY[jr.body] },
                    { t: jr.earthTile, name: jr.earthName.split(',')[0] ?? jr.earthName, body: 'Earth' },
                  ].map((w) => (
                    <figure key={w.body} className="flex flex-col gap-2">
                      <HillTile tile={w.t} className="w-full" />
                      <figcaption className="text-sm leading-tight">
                        <span className="block font-semibold">{w.name}</span>
                        <span style={{ color: MUTED }}>{w.body}</span>
                      </figcaption>
                    </figure>
                  ))
                ) : (
                  <Unavailable reason="no Jezero and Rosamond card" />
                )}
              </div>
            </Step>
            <Step n={2} title="Measure the bumps at three scales" d={0.3}>
              {d.lags.length ? <LagProfile lags={d.lags} /> : <Unavailable reason="no lags in bundle" />}
              <p className="text-lg font-medium leading-snug">How much the height changes over each distance.</p>
              <p className="-mt-2 text-sm" style={{ color: MUTED }}>schematic profile, lags from the bundle</p>
            </Step>
            <Step n={3} title="Compare the two curves" d={0.45}>
              {d.curves.length ? <NuChart curves={d.curves} lags={d.lags} /> : <Unavailable reason="no fingerprint curve in bundle" />}
              <div className="flex items-end justify-between gap-4">
                <div className="flex shrink-0 flex-col gap-1.5 text-sm" style={{ color: MUTED }}>
                  {d.curves.map((c, k) => (
                    <span key={c.id} className="flex items-center gap-2">
                      <span className="h-1 w-5 rounded" style={{ background: k === 0 ? WORLD.mars : WORLD.earth }} />
                      {c.role.replace(' Dry Lake', '')}
                    </span>
                  ))}
                </div>
                {jr ? (
                  <div className="text-right">
                    <p className="text-6xl font-black tracking-tight tabular-nums">{jr.relief.toFixed(3)}</p>
                    <p className="text-sm font-semibold">relief distance</p>
                    <p className="text-sm" style={{ color: MUTED }}>
                      lower = more alike
                      {jr.closerThan !== null ? <><br />closer than {jr.closerThan}% of candidate windows</> : null}
                    </p>
                  </div>
                ) : null}
              </div>
            </Step>
          </div>
        </div>
      ),
    },
    {
      id: 'finder',
      steps: 2,
      body: (step) => (
        <Frame title="Live finder" sub={<>Click any spot: real elevation becomes a fingerprint <A>in your browser</A>.</>}>
          {d.finder ? <FinderDemo finder={d.finder} step={step} /> : <Unavailable reason="no evidence card for the demo" />}
        </Frame>
      ),
    },
    {
      id: 'model',
      steps: ARCH_STEPS.length,
      body: (step) => {
        const cur = ARCH_STEPS[Math.min(step, ARCH_STEPS.length - 1)];
        return (
          <Frame title="Our model" sub={<>A ResNet terrain encoder, trained <A>self-supervised</A>, that learns the physics.</>}>
            <div className="flex h-full flex-col justify-between gap-6">
              <In d={0.15} className="flex min-h-0 flex-1 items-center">
                <ArchDiagram step={step} />
              </In>
              {cur ? (
                <div className="grid grid-cols-[auto_1fr_auto] items-center gap-10 rounded-3xl border border-zinc-200 bg-zinc-50 px-10 py-6">
                  <span className="font-mono text-lg tabular-nums text-zinc-400">
                    {String(step + 1).padStart(2, '0')} / {String(ARCH_STEPS.length).padStart(2, '0')}
                  </span>
                  <AnimatePresence mode="wait">
                    <motion.div key={cur.key} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.3, ease: EASE }} className="flex flex-col gap-1">
                      <span className="text-3xl font-bold tracking-tight">{cur.title}</span>
                      <span className="text-xl" style={{ color: MUTED }}>{cur.line}</span>
                    </motion.div>
                  </AnimatePresence>
                  <span className="text-4xl font-black tracking-tight tabular-nums">{cur.num}</span>
                </div>
              ) : null}
            </div>
          </Frame>
        );
      },
    },
    {
      id: 'metrics',
      steps: 1,
      body: () => (
        <Frame title="Model metrics" sub="Experimental learned similarity, evidence label Predicted, a second opinion beside the physics.">
          <div className="flex h-full flex-col justify-center gap-16">
            <div className="grid grid-cols-3 gap-12">
              <Stat d={0.15} label="Small" unit="parameters" caption="ResNet backbone with three training heads">
                {m.parameters === null ? <Unavailable reason="model card" /> : <CountUp value={m.parameters} />}
              </Stat>
              <Stat d={0.25} label="Trained" unit="training windows" caption={m.totalWindows === null ? undefined : `of ${m.totalWindows.toLocaleString('en-US')} windows from the Moon, Mars and Earth`}>
                {m.trainWindows === null ? <Unavailable reason="model card" /> : <CountUp value={m.trainWindows} />}
              </Stat>
              <Stat d={0.35} label="Fast" unit="faster training on Apple MPS" caption={m.mpsMedian !== null && m.cpuMedian !== null ? `${m.mpsMedian} vs ${m.cpuMedian} windows per second, median, laptop GPU vs CPU server` : undefined}>
                {m.mpsMedian !== null && m.cpuMedian ? (
                  <>
                    ~<CountUp value={Math.round(m.mpsMedian / m.cpuMedian)} />x
                  </>
                ) : (
                  <Unavailable reason="run records" />
                )}
              </Stat>
            </div>
            <div className="mx-auto grid w-2/3 grid-cols-2 gap-12">
              <Stat d={0.45} label="In the browser" unit="parity, min cosine" caption="ONNX in WebAssembly vs the training model">
                {m.wasmMinCosine === null ? <Unavailable reason="numbers.tex" /> : <CountUp value={m.wasmMinCosine} digits={6} />}
              </Stat>
              <Stat d={0.55} label="Knows physics" unit="probe R², lower bound" caption="pooled relief ν at every lag, 95% CI">
                {m.probeR2Low === null ? <Unavailable reason="model card" /> : <CountUp value={Number(m.probeR2Low.toFixed(3))} digits={3} />}
              </Stat>
            </div>
          </div>
        </Frame>
      ),
    },
    {
      id: 'lab',
      steps: 1,
      body: () => (
        <Frame
          title="The Lab"
          sub={d.lab ? <><A>Real elevation</A>, same scale, same rover route: {d.lab.earthName.split(',')[0]} vs {d.lab.targetName}.</> : 'Side-by-side terrain twin'}
        >
          {/* the 3D stage is the persistent layer in Deck, laid over this space */}
          <div className="h-full" />
        </Frame>
      ),
    },
    {
      id: 'results',
      steps: 1,
      body: () => (
        <Frame title="Results" sub={<><A><CountUp value={d.cardCount} duration={0.8} /> evidence cards</A> in the release bundle. Two of them:</>}>
          <div className="grid h-full grid-cols-2 gap-6">
            {d.results.map((r, k) => (
              <In key={r.cardId} d={0.2 + k * 0.15} className="h-full">
                <div className="h-full">
                  <div className="flex h-full flex-col gap-5 rounded-2xl border border-zinc-200 bg-zinc-50 p-7">
                    <p className="text-2xl font-semibold tracking-tight">
                      {r.targetName} and {r.earthName.split(',')[0]}
                    </p>
                    <div className="grid grid-cols-2 gap-3">
                      <HillTile tile={r.targetTile} className="max-h-[28vh]" />
                      <HillTile tile={r.earthTile} className="max-h-[28vh]" />
                    </div>
                    <div className="grid grid-cols-2 gap-6">
                      <div>
                        <p className="text-5xl font-bold tracking-tight text-zinc-950">
                          <CountUp value={Number(r.relief.toPrecision(3))} digits={Math.max(0, 2 - Math.floor(Math.log10(r.relief)))} delay={0.5 + k * 0.15} />
                        </p>
                        <p className="text-base">relief distance</p>
                        <p className="text-sm" style={{ color: MUTED }}>{r.unit}, lower is closer</p>
                      </div>
                      {r.closerThan !== null ? (
                        <div>
                          <p className="text-5xl font-bold tracking-tight text-zinc-950">{r.closerThan}%</p>
                          <p className="text-base">closer than</p>
                          <p className="text-sm" style={{ color: MUTED }}>of candidate windows</p>
                        </div>
                      ) : null}
                    </div>
                    <p className="mt-auto text-xs" style={{ color: MUTED }}>{r.label}, {BODY[r.body]} vs Earth, candidate windows found by this search, not proven analogs</p>
                  </div>
                </div>
              </In>
            ))}
          </div>
        </Frame>
      ),
    },
    {
      id: 'close',
      steps: 1,
      bare: true,
      body: () => (
        <div className="relative grid h-full grid-cols-[1.4fr_1fr] items-center gap-16">
          <div className="flex flex-col gap-6">
            <In>
              <Brand />
            </In>
            <In d={0.15}>
              <p className="text-3xl font-semibold tracking-tight">
                Measure any point on Earth, <A>live.</A>
              </p>
            </In>
            <In d={0.25} className="flex flex-col gap-2 text-xl" >
              <a href={`https://${SITE}`} onClick={(e) => e.stopPropagation()} className="font-medium underline decoration-zinc-300 underline-offset-8">
                {SITE}
              </a>
              <a href={`https://${GITHUB}`} onClick={(e) => e.stopPropagation()} className="text-base underline decoration-zinc-200 underline-offset-8" style={{ color: MUTED }}>
                {GITHUB}
              </a>
            </In>
          </div>
          {qr ? (
            <In d={0.3} className="flex flex-col items-center gap-3">
              <div className="w-[min(24vw,340px)] rounded-2xl bg-white p-6 [&_svg]:h-auto [&_svg]:w-full" role="img" aria-label={`QR code for https://${SITE}`} dangerouslySetInnerHTML={{ __html: qr }} />
              <span className="text-sm" style={{ color: MUTED }}>scan to open</span>
            </In>
          ) : null}
          <p className="absolute bottom-0 left-0 text-sm">
            NASA Space Apps 2026, Gradient Descent, bundle {d.bundleVersion}
            {d.shippingMode ? `, mode ${d.shippingMode}` : ''}
          </p>
        </div>
      ),
    },
  ];
}

/** Renders **bold** and *italic* markers of the script. */
function Script({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g);
  return (
    <>
      {parts.map((p, k) =>
        p.startsWith('**') ? (
          <strong key={k} className="font-semibold text-zinc-900">
            {p.slice(2, -2)}
          </strong>
        ) : p.startsWith('*') ? (
          <em key={k} className="text-zinc-400">
            {p.slice(1, -1)}
          </em>
        ) : (
          <span key={k}>{p}</span>
        ),
      )}
    </>
  );
}

export function Deck({ data, qr }: { data: PitchData; qr: string | null }) {
  const list = slides(data, qr);
  const n = list.length;
  const [pos, setPos] = useState({ i: 0, step: 0, dir: 1 });
  const [notes, setNotes] = useState(false);
  const [overview, setOverview] = useState(false);
  const [t0, setT0] = useState<number | null>(null);
  const [now, setNow] = useState(0);
  const [labMounted, setLabMounted] = useState(false);
  const touch = useRef<number | null>(null);
  const { i, step } = pos;
  const labIndex = list.findIndex((s) => s.id === 'lab');
  // portal to <body>: the site layout has transformed ancestors that would clip a fixed overlay
  const [host, setHost] = useState<HTMLElement | null>(null);
  useEffect(() => setHost(document.body), []);

  useEffect(() => {
    const [a, b] = window.location.hash.slice(1).split('.');
    const k = Number(a);
    const s = Number(b ?? 0);
    if (Number.isInteger(k) && k >= 1 && k <= n) setPos({ i: k - 1, step: Number.isInteger(s) ? Math.max(0, Math.min((list[k - 1]?.steps ?? 1) - 1, s)) : 0, dir: 1 });
    // list is rebuilt each render; n is the stable part
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [n]);

  // mount the 3D Lab one slide early so its tiles stream in before it is shown
  useEffect(() => {
    if (labIndex >= 0 && i >= labIndex - 1) setLabMounted(true);
  }, [i, labIndex]);

  useEffect(() => {
    window.history.replaceState(null, '', `#${i + 1}${step > 0 ? `.${step}` : ''}`);
  }, [i, step]);

  useEffect(() => {
    if (t0 === null) return;
    const id = window.setInterval(() => setNow(performance.now()), 250);
    return () => window.clearInterval(id);
  }, [t0]);

  const next = useCallback(() => {
    setPos((p) => {
      const steps = list[p.i]?.steps ?? 1;
      if (p.step < steps - 1) return { ...p, step: p.step + 1, dir: 1 };
      if (p.i >= n - 1) return p;
      return { i: p.i + 1, step: 0, dir: 1 };
    });
  }, [list, n]);

  const prev = useCallback(() => {
    setPos((p) => {
      if (p.step > 0) return { ...p, step: p.step - 1, dir: -1 };
      if (p.i <= 0) return p;
      const steps = list[p.i - 1]?.steps ?? 1;
      return { i: p.i - 1, step: steps - 1, dir: -1 };
    });
  }, [list]);

  const jump = useCallback((k: number) => setPos((p) => ({ i: Math.max(0, Math.min(n - 1, k)), step: 0, dir: k >= p.i ? 1 : -1 })), [n]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key;
      if (t0 === null && k !== 'Shift') {
        setT0(performance.now());
        setNow(performance.now());
      }
      if (k === 'ArrowRight' || k === 'd' || k === ' ' || k === 'PageDown') {
        e.preventDefault();
        next();
      } else if (k === 'ArrowLeft' || k === 'a' || k === 'PageUp') {
        e.preventDefault();
        prev();
      } else if (k === 'Home') jump(0);
      else if (k === 'End') jump(n - 1);
      else if (k === 'n' || k === 'N') setNotes((v) => !v);
      else if (k === 'p' || k === 'P') setOverview((v) => !v);
      else if (k === 'f' || k === 'F') {
        if (document.fullscreenElement) void document.exitFullscreen();
        else void document.documentElement.requestFullscreen().catch(() => {});
      } else if (k === 'r' || k === 'R') {
        setT0(performance.now());
        setNow(performance.now());
      } else if (k === 'Escape') {
        setOverview(false);
        setNotes(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [next, prev, jump, n, t0]);

  const slide = list[i];
  const note = NOTES[i];
  const elapsed = t0 === null ? 0 : Math.max(0, (now - t0) / 1000);
  const left = TOTAL_S - elapsed;
  const pace = note === undefined || t0 === null ? null : elapsed < note.from ? -(note.from - elapsed) : elapsed > note.to ? elapsed - note.to : 0;

  if (host === null) return <div className="fixed inset-0 z-[1000] bg-white" />;
  return createPortal(
    <div
      className="fixed inset-0 z-[1000] overflow-hidden bg-white font-sans text-zinc-950 antialiased"
      role="region"
      aria-roledescription="slide deck"
      aria-label="Terraluna pitch"
      onClick={(e) => (e.clientX < window.innerWidth * 0.25 ? prev() : next())}
      onTouchStart={(e) => (touch.current = e.touches[0]?.clientX ?? null)}
      onTouchEnd={(e) => {
        const x0 = touch.current;
        const x1 = e.changedTouches[0]?.clientX;
        if (x0 !== null && x1 !== undefined && Math.abs(x1 - x0) > 50) {
          if (x1 < x0) next();
          else prev();
        }
        touch.current = null;
      }}
    >
      {data.mock ? (
        <div role="alert" className="absolute inset-x-0 top-0 z-20 bg-amber-100 px-6 py-2 text-center text-sm font-medium text-amber-900">
          Mock preview: fixture bundle, not production data
        </div>
      ) : null}

      {/* slim progress line */}
      <div className="absolute inset-x-0 top-0 z-10 h-[3px] bg-zinc-100">
        <motion.div className="h-full" style={{ background: INK }} animate={{ width: `${((i + 1) / n) * 100}%` }} transition={{ duration: 0.5, ease: EASE }} />
      </div>

      <AnimatePresence mode="wait" custom={pos.dir}>
        <motion.section
          key={slide?.id}
          aria-roledescription="slide"
          aria-label={`${i + 1} of ${n}: ${note?.title ?? ''}`}
          initial={{ opacity: 0, x: pos.dir * 30 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: pos.dir * -30 }}
          transition={{ duration: 0.4, ease: EASE }}
          className="absolute inset-0 px-[6vw] pb-[10vh] pt-[8vh]"
        >
          {slide?.body(step)}
        </motion.section>
      </AnimatePresence>

      {/* the Lab's 3D stage: mounted one slide early, shown only on the Lab slide */}
      {labMounted && data.lab ? (
        <motion.div
          className="absolute bottom-[9vh] left-[6vw] right-[6vw] top-[23vh] overflow-hidden rounded-2xl border border-zinc-200 bg-white"
          initial={false}
          animate={{ opacity: i === labIndex ? 1 : 0, scale: i === labIndex ? 1 : 0.985 }}
          transition={{ duration: 0.6, delay: i === labIndex ? 0.25 : 0, ease: EASE }}
          style={{ pointerEvents: i === labIndex ? 'auto' : 'none', visibility: i === labIndex || i === labIndex - 1 ? 'visible' : 'hidden' }}
          onClick={(e) => e.stopPropagation()}
          aria-hidden={i !== labIndex}
        >
          <LabTwin earth={data.lab.earth} target={data.lab.target} earthName={data.lab.earthName} targetName={data.lab.targetName} />
          <div className="pointer-events-none absolute inset-x-0 top-0 flex justify-between p-6 text-sm text-zinc-500">
            <span>40 km blocks, real elevation tiles</span>
            <span>drag to orbit</span>
          </div>
          <span aria-hidden className="pitch-border-beam pointer-events-none absolute inset-0 rounded-2xl" />
        </motion.div>
      ) : null}

      {/* footer: hairline, label left, site right */}
      {slide?.bare ? null : (
        <div className="pointer-events-none absolute inset-x-[6vw] bottom-0 flex h-[6vh] items-center justify-between border-t border-zinc-200 text-sm" aria-live="polite">
          <span>
            Pitch Deck <span className="ml-3 tabular-nums text-zinc-400">{String(i + 1).padStart(2, '0')} / {String(n).padStart(2, '0')}</span>
          </span>
          <span>{SITE}</span>
        </div>
      )}

      {/* presenter drawer (N) */}
      <AnimatePresence>
        {notes && note ? (
          <motion.aside
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={{ duration: 0.35, ease: EASE }}
            onClick={(e) => e.stopPropagation()}
            className="absolute inset-x-0 bottom-0 z-30 border-t border-zinc-200 bg-zinc-50/95 backdrop-blur"
            aria-label="Speaker notes"
          >
            <div className="h-1 w-full bg-zinc-200">
              <div className="relative h-full">
                <div className="absolute inset-y-0 bg-zinc-300" style={{ left: `${(note.from / TOTAL_S) * 100}%`, width: `${((note.to - note.from) / TOTAL_S) * 100}%` }} />
                <div
                  className="absolute inset-y-0 left-0"
                  style={{ width: `${Math.min(100, (elapsed / TOTAL_S) * 100)}%`, background: pace === null || pace === 0 ? INK : pace < 0 ? '#16a34a' : '#dc2626' }}
                />
              </div>
            </div>
            <div className="grid grid-cols-[13rem_1fr] gap-8 px-8 py-5">
              <div className="flex flex-col gap-1">
                <span className={`${MONO} text-4xl font-semibold tabular-nums ${left < 0 ? 'text-red-600' : 'text-zinc-900'}`}>{t0 === null ? '2:00' : mmss(left)}</span>
                <span className="text-xs text-zinc-500">
                  {i + 1}. {note.title}, {mmss(note.from)} to {mmss(note.to)}
                  {(list[i]?.steps ?? 1) > 1 ? `, step ${step + 1}/${list[i]?.steps ?? 1}` : ''}
                </span>
                <span className={`text-xs font-medium ${pace === null ? 'text-zinc-400' : pace === 0 ? 'text-zinc-600' : pace < 0 ? 'text-green-700' : 'text-red-600'}`}>
                  {pace === null ? 'timer starts on first key, R resets' : pace === 0 ? 'on time' : pace < 0 ? `ahead ${Math.round(-pace)} s` : `behind ${Math.round(pace)} s`}
                </span>
              </div>
              <p className="max-w-4xl text-[15px] leading-relaxed text-zinc-600">
                <Script text={note.text} />
              </p>
            </div>
          </motion.aside>
        ) : null}
      </AnimatePresence>

      {/* overview (P) */}
      <AnimatePresence>
        {overview ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={(e) => e.stopPropagation()}
            className="absolute inset-0 z-40 grid place-items-center bg-white/90 p-[6vw] backdrop-blur"
            aria-label="Slide overview"
          >
            <ol className="grid w-full grid-cols-3 gap-5">
              {NOTES.map((s, k) => (
                <li key={s.title}>
                  <button
                    type="button"
                    onClick={() => {
                      jump(k);
                      setOverview(false);
                    }}
                    className="flex aspect-[16/7] w-full flex-col justify-between rounded-2xl border bg-white p-5 text-left transition-colors hover:border-zinc-950"
                    style={{ borderColor: k === i ? INK : LINE }}
                  >
                    <span className={`${MONO} text-sm text-zinc-400`}>
                      {String(k + 1).padStart(2, '0')}<span className="ml-4">{mmss(s.from)}</span>
                    </span>
                    <span className="text-2xl font-semibold tracking-tight">{s.title}</span>
                  </button>
                </li>
              ))}
            </ol>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <style>{`
        .pitch-border-beam { padding: 1.5px; background: conic-gradient(from var(--pitch-a, 0deg), transparent 0deg, #a1a1aa 40deg, transparent 90deg) border-box;
          -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor; mask-composite: exclude; animation: pitch-spin 6s linear infinite; }
        @property --pitch-a { syntax: '<angle>'; inherits: false; initial-value: 0deg; }
        @keyframes pitch-spin { to { --pitch-a: 360deg; } }
        @media (prefers-reduced-motion: reduce) { .pitch-border-beam { animation: none; } }
      `}</style>
    </div>,
    host,
  );
}
