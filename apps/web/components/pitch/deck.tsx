'use client';

// The /pitch deck: white full-viewport slides over the site chrome. Keys: → / d / Space next
// (steps through build-ups first), ← / a back, Home / End; N speaker notes + 2:00 timer + pace,
// P overview, F full screen, Esc closes panels. Click and swipe also move. The hash (#5 or #5.2)
// keeps place. Every number on a slide is read from the bundle (pitch-data.ts).
import { AnimatePresence, motion } from 'motion/react';
import dynamic from 'next/dynamic';
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';

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

function Kicker({ children }: { children: ReactNode }) {
  return (
    <p className={`${MONO} text-sm uppercase tracking-[0.22em]`} style={{ color: GOLD }}>
      {children}
    </p>
  );
}

/** The shared grid: kicker + title on top, content filling the rest. */
function Frame({ kicker, title, children, aside }: { kicker: ReactNode; title: ReactNode; children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="flex h-full flex-col gap-10">
      <In className="flex items-end justify-between gap-10">
        <div className="flex flex-col gap-4">
          <Kicker>{kicker}</Kicker>
          <h2 className="text-6xl font-semibold leading-[1.05] tracking-tight text-balance">{title}</h2>
        </div>
        {aside}
      </In>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}

function Keywords({ items, d = 0.35 }: { items: readonly string[]; d?: number }) {
  return (
    <ul className="flex flex-col gap-4">
      {items.map((t, k) => (
        <motion.li
          key={t}
          initial={{ opacity: 0, x: -12 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: d + k * 0.12, duration: 0.5, ease: EASE }}
          className="flex items-center gap-4 text-3xl font-medium tracking-tight"
        >
          <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: GOLD }} />
          {t}
        </motion.li>
      ))}
    </ul>
  );
}

function Metric({ label, children, note, d }: { label: string; children: ReactNode; note?: string | undefined; d: number }) {
  return (
    <In d={d} className="flex flex-col gap-2 border-t border-zinc-200 pt-5">
      <span className="text-6xl font-semibold tracking-tight text-zinc-900">{children}</span>
      <span className="text-xl text-zinc-600">{label}</span>
      {note ? <span className="text-sm text-zinc-400">{note}</span> : null}
    </In>
  );
}

function Dots() {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0"
      style={{
        backgroundImage: 'radial-gradient(#d4d4d8 1px, transparent 1px)',
        backgroundSize: '26px 26px',
        maskImage: 'radial-gradient(ellipse at 75% 50%, black 0%, transparent 60%)',
        WebkitMaskImage: 'radial-gradient(ellipse at 75% 50%, black 0%, transparent 60%)',
      }}
    />
  );
}

interface Slide {
  readonly id: string;
  readonly steps: number;
  readonly body: (step: number) => ReactNode;
}

function slides(d: PitchData, qr: string | null): Slide[] {
  const m = d.model;
  const [jezT, , arcT] = [d.targets.find((t) => t.targetId.includes('jezero')), null, d.targets.find((t) => t.targetId.includes('arcadia'))];
  const lagText = d.lags.length ? `${d.lags[0]} m → ${(d.lags[d.lags.length - 1] ?? 0) >= 1000 ? `${((d.lags[d.lags.length - 1] ?? 0) / 1000).toFixed(1)} km` : `${d.lags[d.lags.length - 1]} m`}` : 'multi-scale lags';
  return [
    {
      id: 'title',
      steps: 1,
      body: () => (
        <div className="relative grid h-full grid-cols-[1.25fr_1fr] items-center gap-12">
          <div className="flex flex-col gap-10">
            <In>
              <Kicker>NASA Space Apps 2026 · Gradient Descent</Kicker>
            </In>
            <In d={0.1}>
              <h1 className="text-[11rem] font-semibold leading-[0.9] tracking-[-0.06em]">
                Terra<span style={{ color: GOLD }}>luna</span>
              </h1>
            </In>
            <In d={0.25}>
              <p className="text-4xl tracking-tight text-zinc-500">Find the Moon and Mars on Earth.</p>
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
                  className="absolute w-[46%] shadow-[0_30px_60px_-30px_rgba(0,0,0,0.45)]"
                  style={{ left: `${[2, 30, 54][k]}%`, top: `${[-28, -2, -36][k]}%` }}
                >
                  <HillTile tile={t.tile} />
                </motion.div>
              ) : null,
            )}
          </div>
        </div>
      ),
    },
    {
      id: 'problem',
      steps: 1,
      body: () => (
        <Frame kicker="The problem" title={<>Which Earth terrain <span style={{ color: GOLD }}>really</span> matches?</>}>
          <div className="flex h-full flex-col justify-between gap-8">
            <div className="grid grid-cols-3 gap-8">
              {d.targets.map((t, k) => (
                <In key={t.targetId} d={0.2 + k * 0.12} className="flex flex-col gap-4">
                  <HillTile tile={t.tile} className="max-h-[44vh]" />
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="text-2xl font-semibold tracking-tight">{t.name}</span>
                    <span className={`${MONO} text-sm uppercase tracking-widest text-zinc-400`}>{BODY[t.body]}</span>
                  </div>
                </In>
              ))}
            </div>
            <In d={0.6} className="flex items-center gap-6 text-2xl">
              <span className="text-zinc-400 line-through decoration-zinc-300">picked by experience</span>
              <span className="text-zinc-300">→</span>
              <span className="font-semibold">measured</span>
            </In>
          </div>
        </Frame>
      ),
    },
    {
      id: 'physics',
      steps: 1,
      body: () => (
        <Frame kicker="Physics fingerprint" title={<>Structure function <span className="font-mono font-medium">ν(L)</span></>}>
          <div className="grid h-full grid-cols-[0.8fr_1.2fr] items-center gap-16">
            <Keywords items={[lagText, 'relief + texture', 'Python = browser']} />
            <In d={0.2} className="flex flex-col gap-3">
              {d.curves.length ? <NuChart curves={d.curves} lags={d.lags} /> : <Unavailable reason="no fingerprint curve in bundle" />}
              <div className="flex items-center justify-between gap-6 text-base text-zinc-500">
                <div className="flex gap-6">
                  {d.curves.map((c, k) => (
                    <span key={c.id} className="flex items-center gap-2">
                      <span className="h-1 w-6 rounded" style={{ background: k === 0 ? GOLD : '#18181b' }} />
                      {c.role}
                    </span>
                  ))}
                </div>
                <span className="text-sm text-zinc-400">hover to read · Derived, relief ν(L) from the bundle</span>
              </div>
            </In>
          </div>
        </Frame>
      ),
    },
    {
      id: 'finder',
      steps: 2,
      body: (step) => (
        <Frame kicker="Live finder" title={<>Any point on Earth, <span style={{ color: GOLD }}>measured</span></>}>
          {d.finder ? <FinderDemo finder={d.finder} step={step} /> : <Unavailable reason="no evidence card for the demo" />}
        </Frame>
      ),
    },
    {
      id: 'model',
      steps: 5,
      body: (step) => (
        <Frame
          kicker="Our model · experimental learned similarity"
          title="ResNet terrain encoder"
          aside={
            <ol className="flex gap-2">
              {ARCH_STEPS.map((s, k) => (
                <li
                  key={s.key}
                  className="flex flex-col rounded-xl border px-4 py-2 transition-all duration-500"
                  style={{ borderColor: k <= step ? GOLD : '#e4e4e7', opacity: k <= step ? 1 : 0.45, background: k === Math.min(step, 3) ? '#FBF3E2' : 'white' }}
                >
                  <span className="text-base font-semibold">{s.label}</span>
                  <span className={`${MONO} text-xs text-zinc-500`}>{s.hint}</span>
                </li>
              ))}
            </ol>
          }
        >
          <div className="relative flex h-full items-center">
            <ArchDiagram step={Math.min(step, 3)} />
            <AnimatePresence>
              {step >= 4 ? (
                <motion.figure
                  initial={{ opacity: 0, scale: 0.97 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.5, ease: EASE }}
                  className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-white"
                >
                  <img src="/figures/encoder-architecture.svg" alt="Paper figure: encoder architecture with ResNet backbone, VICReg projector, physics head and gradient-reversal body adversary" className="max-h-full w-full object-contain" />
                  <figcaption className="text-sm text-zinc-400">Full figure from the paper</figcaption>
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
        <Frame
          kicker={`Experimental learned similarity · ${m.version ?? 'model card'}`}
          title="Small, fast, runs in your browser"
        >
          <div className="grid grid-cols-3 gap-x-14 gap-y-12 pt-4">
            <Metric d={0.15} label="parameters">
              {m.parameters === null ? <Unavailable reason="model card" /> : <CountUp value={m.parameters} />}
            </Metric>
            <Metric d={0.25} label="training windows" note={m.totalWindows === null ? undefined : `of ${m.totalWindows.toLocaleString('en-US')} · Moon · Mars · Earth`}>
              {m.trainWindows === null ? <Unavailable reason="model card" /> : <CountUp value={m.trainWindows} />}
            </Metric>
            <Metric d={0.35} label="faster training on Apple MPS" note={m.mpsMedian !== null && m.cpuMedian !== null ? `${m.mpsMedian} vs ${m.cpuMedian} windows/s, median` : undefined}>
              {m.mpsMedian !== null && m.cpuMedian ? (
                <>
                  ~<CountUp value={Math.round(m.mpsMedian / m.cpuMedian)} />×
                </>
              ) : (
                <Unavailable reason="run records" />
              )}
            </Metric>
            <Metric d={0.45} label="browser parity · min cosine" note="ONNX → WebAssembly vs PyTorch">
              {m.wasmMinCosine === null ? <Unavailable reason="numbers.tex" /> : <CountUp value={m.wasmMinCosine} digits={6} />}
            </Metric>
            <Metric d={0.55} label="physics probe R² · lower bound" note="pooled relief ν, 95% CI">
              {m.probeR2Low === null ? <Unavailable reason="model card" /> : <CountUp value={Number(m.probeR2Low.toFixed(3))} digits={3} />}
            </Metric>
            <Metric d={0.65} label="evidence label" note="a second opinion beside the physics fingerprint">
              <span style={{ color: GOLD }}>Predicted</span>
            </Metric>
          </div>
        </Frame>
      ),
    },
    {
      id: 'lab',
      steps: 1,
      body: () => (
        <Frame
          kicker="The Lab"
          title={d.lab ? <>{d.lab.earthName.split(',')[0]} <span className="text-zinc-300">↔</span> {d.lab.targetName}</> : 'Side-by-side terrain twin'}
          aside={<Keywords items={['real elevation', 'same scale', 'same rover route']} d={0.3} />}
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
        <Frame kicker="Results" title={<><CountUp value={d.cardCount} duration={0.8} /> evidence cards</>}>
          <div className="grid h-full grid-cols-2 gap-10">
            {d.results.map((r, k) => (
              <In key={r.cardId} d={0.2 + k * 0.15} className="flex flex-col gap-5 rounded-3xl border border-zinc-200 p-7">
                <div className="grid grid-cols-2 gap-3">
                  <div className="flex flex-col gap-2">
                    <HillTile tile={r.targetTile} className="max-h-[30vh]" />
                    <span className={`${MONO} text-xs uppercase tracking-widest text-zinc-400`}>{BODY[r.body]} · {r.targetName}</span>
                  </div>
                  <div className="flex flex-col gap-2">
                    <HillTile tile={r.earthTile} className="max-h-[30vh]" />
                    <span className={`${MONO} text-xs uppercase tracking-widest text-zinc-400`}>Earth · {r.earthName.split(',')[0]}</span>
                  </div>
                </div>
                <p className="text-2xl font-semibold tracking-tight">
                  {r.targetName} <span className="text-zinc-300">↔</span> {r.earthName}
                </p>
                <div className="flex items-end justify-between gap-6">
                  <div>
                    <p className="font-mono text-5xl font-semibold" style={{ color: GOLD }}>
                      <CountUp value={Number(r.relief.toPrecision(3))} digits={Math.max(0, 2 - Math.floor(Math.log10(r.relief)))} delay={0.5 + k * 0.15} />
                    </p>
                    <p className="text-sm text-zinc-500">relief distance{r.unit ? ` (${r.unit})` : ''} · lower is closer</p>
                  </div>
                  {r.closerThan !== null ? (
                    <p className="text-right text-xl text-zinc-700">
                      closer than <span className="text-4xl font-semibold text-zinc-900">{r.closerThan}%</span>
                      <br />
                      <span className="text-sm text-zinc-400">of candidate windows</span>
                    </p>
                  ) : null}
                </div>
                <p className="text-xs text-zinc-400">{r.label} · candidate windows found by this search, not proven analogs</p>
              </In>
            ))}
          </div>
        </Frame>
      ),
    },
    {
      id: 'close',
      steps: 1,
      body: () => (
        <div className="relative grid h-full grid-cols-[1.4fr_1fr] items-center gap-16">
          <div className="flex flex-col gap-12">
            <In>
              <h2 className="text-[7.5rem] font-semibold leading-[0.95] tracking-[-0.05em]">
                Measure any point on Earth <span style={{ color: GOLD }}>— live.</span>
              </h2>
            </In>
            <In d={0.25} className="flex flex-col gap-3 text-2xl">
              <a href={`https://${SITE}`} onClick={(e) => e.stopPropagation()} className="font-mono font-medium underline decoration-zinc-300 underline-offset-8 hover:decoration-[#C8962E]">
                {SITE}
              </a>
              <a href={`https://${GITHUB}`} onClick={(e) => e.stopPropagation()} className="font-mono text-lg text-zinc-500 underline decoration-zinc-200 underline-offset-8">
                {GITHUB}
              </a>
            </In>
            <In d={0.4}>
              <p className="text-base text-zinc-400">
                Terraluna · Gradient Descent · bundle {d.bundleVersion}
                {d.shippingMode ? ` · ${d.shippingMode}` : ''}
              </p>
            </In>
          </div>
          {qr ? (
            <In d={0.3} className="flex flex-col items-center gap-4">
              <div className="w-[min(26vw,380px)] rounded-3xl border border-zinc-200 p-6 [&_svg]:h-auto [&_svg]:w-full" role="img" aria-label={`QR code for https://${SITE}`} dangerouslySetInnerHTML={{ __html: qr }} />
              <span className="text-sm text-zinc-400">scan to open</span>
            </In>
          ) : null}
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

  return (
    <div
      className="fixed inset-0 z-[1000] overflow-hidden bg-white font-sans text-zinc-900 antialiased"
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
      {slide?.id === 'title' || slide?.id === 'close' ? <Dots /> : null}
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
          className="absolute inset-0 px-[6vw] pb-[8vh] pt-[8vh]"
        >
          {slide?.body(step)}
        </motion.section>
      </AnimatePresence>

      {/* the Lab's 3D stage: mounted one slide early, shown only on the Lab slide */}
      {labMounted && data.lab ? (
        <motion.div
          className="absolute bottom-[8vh] left-[6vw] right-[6vw] top-[27vh] overflow-hidden rounded-3xl bg-[#0b0c0f]"
          initial={false}
          animate={{ opacity: i === labIndex ? 1 : 0, scale: i === labIndex ? 1 : 0.985 }}
          transition={{ duration: 0.6, delay: i === labIndex ? 0.25 : 0, ease: EASE }}
          style={{ pointerEvents: i === labIndex ? 'auto' : 'none', visibility: i === labIndex || i === labIndex - 1 ? 'visible' : 'hidden' }}
          onClick={(e) => e.stopPropagation()}
          aria-hidden={i !== labIndex}
        >
          <LabTwin earth={data.lab.earth} target={data.lab.target} />
          <div className="pointer-events-none absolute inset-x-0 top-0 flex justify-between bg-gradient-to-b from-black/50 to-transparent p-6 text-base text-white/90">
            <span>
              Earth · {data.lab.earthName}
            </span>
            <span>Mars · {data.lab.targetName}</span>
          </div>
          <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-between bg-gradient-to-t from-black/50 to-transparent p-5 text-sm text-white/70">
            <span>40 km blocks · real elevation tiles (Terrarium SRTM · NASA Trek MOLA) · rover on the same route</span>
            <span>drag to orbit</span>
          </div>
          <span aria-hidden className="pitch-border-beam pointer-events-none absolute inset-0 rounded-3xl" />
        </motion.div>
      ) : null}

      {/* slide number */}
      <div className={`${MONO} pointer-events-none absolute bottom-6 right-[6vw] text-sm tabular-nums text-zinc-400`} aria-live="polite">
        {String(i + 1).padStart(2, '0')} / {String(n).padStart(2, '0')}
      </div>

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
                  {i + 1}. {note.title} · {mmss(note.from)}–{mmss(note.to)}
                  {(list[i]?.steps ?? 1) > 1 ? ` · step ${step + 1}/${list[i]?.steps ?? 1}` : ''}
                </span>
                <span className={`text-xs font-medium ${pace === null ? 'text-zinc-400' : pace === 0 ? 'text-zinc-600' : pace < 0 ? 'text-green-700' : 'text-red-600'}`}>
                  {pace === null ? 'timer starts on first key · R resets' : pace === 0 ? 'on time' : pace < 0 ? `ahead ${Math.round(-pace)} s` : `behind ${Math.round(pace)} s`}
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
                      {String(k + 1).padStart(2, '0')} · {mmss(s.from)}
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
    </div>
  );
}
