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
import { CountUp, EASE, GOLD, HillTile, NuChart } from './bits';
import { mmss, NOTES, TOTAL_S } from './notes';
import type { PitchData } from './pitch-data';

const LabTwin = dynamic(() => import('./lab-twin'), { ssr: false, loading: () => <div className="size-full bg-[#0b0c0f]" /> });
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

/** A word that swaps on a timer with a blur flip (after Aceternity's FlipWords, MIT). */
function FlipWord({ words }: { words: readonly string[] }) {
  const [k, setK] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setK((v) => (v + 1) % words.length), 2400);
    return () => window.clearInterval(id);
  }, [words.length]);
  const w = words[k] ?? '';
  return (
    <span className="relative inline-block min-w-[4.6ch] text-left font-semibold" style={{ color: GOLD }}>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={w}
          className="inline-block"
          initial={{ opacity: 0, y: 14, filter: 'blur(8px)' }}
          animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
          exit={{ opacity: 0, y: -18, filter: 'blur(8px)' }}
          transition={{ duration: 0.5, ease: EASE }}
        >
          {w}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}

const INK = '#0F172A';
const MUTED = '#64748B';

/** Accent words. */
function A({ children }: { children: ReactNode }) {
  return <span style={{ color: GOLD }}>{children}</span>;
}

/** The brand mark and wordmark, large on the title and closing slides. */
function Brand() {
  return (
    <div className="flex items-center gap-5">
      <HugeiconsIcon icon={EarthIcon} strokeWidth={2.2} className="size-20" style={{ color: INK }} aria-hidden />
      <span className="text-[6.5rem] font-semibold leading-none tracking-[-0.045em]" style={{ color: INK }}>
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
      <span className="text-6xl font-semibold tracking-tight" style={{ color: GOLD }}>
        {children}
      </span>
      <span className="mt-1 text-lg">{unit}</span>
      {caption ? <span className="max-w-[22rem] text-sm leading-snug" style={{ color: MUTED }}>{caption}</span> : null}
    </In>
  );
}

/** A stacked item: heading with an accent phrase and a grey line. */
function Item({ head, accent, line, d }: { head: string; accent: string; line: string; d: number }) {
  return (
    <In d={d} className="flex flex-col gap-1">
      <span className="text-2xl font-medium tracking-tight">
        {head} <A>{accent}</A>
      </span>
      <span className="text-base" style={{ color: MUTED }}>{line}</span>
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
  const jezT = d.targets.find((t) => t.targetId.includes('jezero'));
  const arcT = d.targets.find((t) => t.targetId.includes('arcadia'));
  const last = d.lags[d.lags.length - 1] ?? 0;
  const lagText = d.lags.length ? `${d.lags[0]} m to ${last >= 1000 ? `${(last / 1000).toFixed(1)} km` : `${last} m`},` : 'Multi-scale,';
  const caption: Record<string, string> = { 'moon-cr-site01-w000': 'lunar south pole', 'mars-jezero-w000': 'Perseverance landing site', 'mars-arcadia-w000': 'mid-latitude plains' };
  return [
    {
      id: 'title',
      steps: 1,
      bare: true,
      body: () => (
        <div className="relative grid h-full grid-cols-[1.3fr_1fr] items-center gap-12">
          <div className="flex flex-col gap-6">
            <In>
              <Brand />
            </In>
            <In d={0.15}>
              <p className="text-3xl font-semibold tracking-tight">
                Find <FlipWord words={['the Moon', 'Mars']} /> on Earth.
              </p>
            </In>
            <In d={0.25}>
              <p className="max-w-2xl text-xl" style={{ color: MUTED }}>
                Measured Earth analogs for NASA&apos;s Moon and Mars sites, computed from real elevation, live in your browser.
              </p>
            </In>
          </div>
          <div className="relative flex items-center justify-center">
            {[jezT, arcT, d.targets[0]].map((t, k) =>
              t ? (
                <motion.div
                  key={t.targetId}
                  initial={{ opacity: 0, scale: 0.9, rotate: 0 }}
                  animate={{ opacity: 1, scale: 1, rotate: [-8, 4, -2][k] ?? 0 }}
                  transition={{ duration: 0.9, delay: 0.3 + k * 0.15, ease: EASE }}
                  className="absolute w-[46%] shadow-[0_30px_60px_-30px_rgba(15,23,42,0.45)]"
                  style={{ left: `${[2, 30, 54][k]}%`, top: `${[-28, -2, -36][k]}%` }}
                >
                  <HillTile tile={t.tile} />
                </motion.div>
              ) : null,
            )}
          </div>
          <p className="absolute bottom-0 left-0 text-sm">NASA Space Apps 2026, Gradient Descent</p>
        </div>
      ),
    },
    {
      id: 'problem',
      steps: 1,
      body: () => (
        <Frame title="The problem" sub={<>Earth analog sites are picked from <A>experience</A>. We <A>measure</A> them.</>}>
          <div className="grid h-full grid-cols-3 gap-12">
            {d.targets.map((t, k) => (
              <In key={t.targetId} d={0.2 + k * 0.12} className="flex flex-col items-center gap-4 text-center">
                <span className="text-xl font-semibold">{BODY[t.body]}</span>
                <HillTile tile={t.tile} className="w-full max-w-[22rem]" />
                <span className="text-2xl font-semibold tracking-tight" style={{ color: GOLD }}>{t.name}</span>
                <span className="-mt-3 text-sm" style={{ color: MUTED }}>{caption[t.targetId] ?? ''}</span>
              </In>
            ))}
          </div>
        </Frame>
      ),
    },
    {
      id: 'physics',
      steps: 1,
      body: () => (
        <div className="grid h-full grid-cols-[0.85fr_1.15fr] items-center gap-16">
          <div className="flex flex-col gap-10">
            <In className="flex flex-col gap-2">
              <h2 className="text-[2.75rem] font-semibold leading-tight tracking-tight">Physics fingerprint</h2>
              <p className="text-xl" style={{ color: MUTED }}>
                The structure function <span className="font-serif italic">ν(L)</span> of every 5 km window.
              </p>
            </In>
            <div className="flex flex-col gap-7">
              <Item d={0.25} head={lagText} accent="multi-scale" line="how much the ground rises and falls at each lag" />
              <Item d={0.35} head="Relief and texture," accent="two fingerprints" line="each distance kept separate, lower is closer" />
              <Item d={0.45} head="Same code," accent="in your browser" line="Python and TypeScript with tested parity" />
            </div>
          </div>
          <In d={0.2} className="flex flex-col gap-3">
            {d.curves.length ? <NuChart curves={d.curves} lags={d.lags} /> : <Unavailable reason="no fingerprint curve in bundle" />}
            <div className="flex items-center justify-between gap-6 text-base" style={{ color: MUTED }}>
              <div className="flex gap-6">
                {d.curves.map((c, k) => (
                  <span key={c.id} className="flex items-center gap-2">
                    <span className="h-1 w-6 rounded" style={{ background: k === 0 ? GOLD : INK }} />
                    {c.role}
                  </span>
                ))}
              </div>
              <span className="text-sm">hover to read values, Derived from the bundle</span>
            </div>
          </In>
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
      steps: 5,
      body: (step) => (
        <Frame title="Our model" sub={<>A ResNet terrain encoder, trained <A>self-supervised</A>, that learns the physics.</>}>
          <div className="relative flex h-full flex-col justify-between gap-6">
            <ArchDiagram step={Math.min(step, 3)} />
            <ol className="grid grid-cols-4 gap-6 border-t pt-5" style={{ borderColor: '#E2E8F0' }}>
              {ARCH_STEPS.map((s, k) => (
                <li key={s.key} className="flex items-start gap-3 transition-opacity duration-500" style={{ opacity: k <= step ? 1 : 0.35 }}>
                  <span className="mt-1.5 size-3.5 shrink-0 rounded-full border-2" style={{ borderColor: k <= step ? GOLD : '#94A3B8', background: k === Math.min(step, 3) ? GOLD : 'transparent' }} />
                  <span className="flex flex-col">
                    <span className="text-lg font-semibold">{s.label}</span>
                    <span className="text-sm" style={{ color: MUTED }}>{s.hint}</span>
                  </span>
                </li>
              ))}
            </ol>
            <AnimatePresence>
              {step >= 4 ? (
                <motion.figure
                  initial={{ opacity: 0, scale: 0.97 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.5, ease: EASE }}
                  className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-[#F8FAFC]"
                >
                  <img src="/figures/encoder-architecture.svg" alt="Paper figure: encoder architecture with ResNet backbone, VICReg projector, physics head and gradient-reversal body adversary" className="max-h-full w-full rounded-xl bg-white object-contain p-4" />
                  <figcaption className="text-sm" style={{ color: MUTED }}>Full figure from the paper</figcaption>
                </motion.figure>
              ) : null}
            </AnimatePresence>
          </div>
        </Frame>
      ),
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
                  <div className="flex h-full flex-col gap-5 rounded-2xl bg-[#F1F5F9] p-7">
                    <p className="text-2xl font-semibold tracking-tight">
                      <A>{r.targetName}</A> and {r.earthName.split(',')[0]}
                    </p>
                    <div className="grid grid-cols-2 gap-3">
                      <HillTile tile={r.targetTile} className="max-h-[28vh]" />
                      <HillTile tile={r.earthTile} className="max-h-[28vh]" />
                    </div>
                    <div className="grid grid-cols-2 gap-6">
                      <div>
                        <p className="text-5xl font-semibold tracking-tight" style={{ color: GOLD }}>
                          <CountUp value={Number(r.relief.toPrecision(3))} digits={Math.max(0, 2 - Math.floor(Math.log10(r.relief)))} delay={0.5 + k * 0.15} />
                        </p>
                        <p className="text-base">relief distance</p>
                        <p className="text-sm" style={{ color: MUTED }}>{r.unit}, lower is closer</p>
                      </div>
                      {r.closerThan !== null ? (
                        <div>
                          <p className="text-5xl font-semibold tracking-tight" style={{ color: GOLD }}>{r.closerThan}%</p>
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
              <a href={`https://${SITE}`} onClick={(e) => e.stopPropagation()} className="font-medium underline decoration-slate-300 underline-offset-8">
                {SITE}
              </a>
              <a href={`https://${GITHUB}`} onClick={(e) => e.stopPropagation()} className="text-base underline decoration-slate-200 underline-offset-8" style={{ color: MUTED }}>
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
      return { i: p.i - 1, step: Math.min(steps - 1, 3), dir: -1 };
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
      className="fixed inset-0 z-[1000] overflow-hidden bg-[#F8FAFC] font-sans text-[#0F172A] antialiased"
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
        <motion.div className="h-full" style={{ background: GOLD }} animate={{ width: `${((i + 1) / n) * 100}%` }} transition={{ duration: 0.5, ease: EASE }} />
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
          className="absolute bottom-[9vh] left-[6vw] right-[6vw] top-[23vh] overflow-hidden rounded-2xl border border-[#E2E8F0] bg-white"
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
        <div className="pointer-events-none absolute inset-x-[6vw] bottom-0 flex h-[6vh] items-center justify-between border-t border-[#E2E8F0] text-sm" aria-live="polite">
          <span>
            Pitch Deck <span className="ml-3 tabular-nums text-[#94A3B8]">{String(i + 1).padStart(2, '0')} / {String(n).padStart(2, '0')}</span>
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
                  style={{ width: `${Math.min(100, (elapsed / TOTAL_S) * 100)}%`, background: pace === null || pace === 0 ? GOLD : pace < 0 ? '#16a34a' : '#dc2626' }}
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
                    className="flex aspect-[16/7] w-full flex-col justify-between rounded-2xl border bg-white p-5 text-left transition-colors hover:border-[#C8962E]"
                    style={{ borderColor: k === i ? GOLD : '#e4e4e7' }}
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
        .pitch-border-beam { padding: 1.5px; background: conic-gradient(from var(--pitch-a, 0deg), transparent 0deg, ${GOLD} 40deg, transparent 90deg) border-box;
          -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor; mask-composite: exclude; animation: pitch-spin 6s linear infinite; }
        @property --pitch-a { syntax: '<angle>'; inherits: false; initial-value: 0deg; }
        @keyframes pitch-spin { to { --pitch-a: 360deg; } }
        @media (prefers-reduced-motion: reduce) { .pitch-border-beam { animation: none; } }
      `}</style>
    </div>,
    host,
  );
}
