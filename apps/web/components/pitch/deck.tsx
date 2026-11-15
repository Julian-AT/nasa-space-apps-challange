'use client';

// The /pitch deck: white full-viewport slides over the site chrome. Keys: → / d / Space next
// (steps through build-ups first), ← / a back, Home / End; N speaker notes + 2:00 timer + pace,
// P overview, F full screen, Esc closes panels. Click and swipe also move. The hash (#5 or #5.2)
// keeps place. Every number on a slide is read from the bundle (pitch-data.ts).
import { AnimatePresence, motion } from 'motion/react';
import dynamic from 'next/dynamic';
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { ACCENT, EASE, FAINT, INK, LINE, MUTED, PAPER } from './bits';
import { C, FigureCard, LineFigure, type LineSeries } from './figure';
import { FigBenchmarks, type BenchData } from './fig-benchmarks';
import { FigTraining, type CurvesJson } from './fig-training';
import { mmss, NOTES, TOTAL_S } from './notes';
import type { PitchData } from './pitch-data';
import { SslExplainer, SSL_STEPS } from './ssl-explainer';

const FigLab = dynamic(() => import('./fig-lab'), { ssr: false, loading: () => <div className="size-full" /> });
const EarthHero = dynamic(() => import('./earth-hero'), { ssr: false, loading: () => null });
const FinderDemo = dynamic(() => import('./finder'), { ssr: false, loading: () => null });

const GITHUB = 'github.com/Julian-AT/nasa-space-apps-challange';
const SITE = 'terraluna-lovat.vercel.app';
const MONO = 'font-mono';

const BODY = { moon: 'Moon', mars: 'Mars' } as const;

function Unavailable({ reason }: { reason: string }) {
  return <span className="text-base font-normal text-zinc-400">Unavailable ({reason})</span>;
}

/** A slide block. Slide changes are instant: no entrance animation. `d` is kept for call sites. */
function In({ children, className }: { d?: number; children: ReactNode; className?: string }) {
  return <div className={className}>{children}</div>;
}

/** Emphasis by weight, never by colour. */
function A({ children }: { children: ReactNode }) {
  return <span className="font-medium text-zinc-950">{children}</span>;
}

/** Content slide grid: a calm title top-left with a one-line subtitle, content below. */
function Frame({ title, sub, children }: { title: ReactNode; sub?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex h-full flex-col gap-9">
      <In className="flex flex-col gap-2">
        <h2 className="text-[2.75rem] font-medium leading-[1.1] tracking-[-0.025em]">{title}</h2>
        {sub ? <p className="text-xl" style={{ color: MUTED }}>{sub}</p> : null}
      </In>
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}

/** A key number in weight 500 with a small grey label. */
function Key({ value, label }: { value: ReactNode; label: string }) {
  return (
    <div className="flex flex-col gap-1 border-t pt-4" style={{ borderColor: C.hair }}>
      <span className="text-[2rem] font-medium leading-none tracking-[-0.02em] tabular-nums">{value}</span>
      <span className="text-sm" style={{ color: FAINT }}>{label}</span>
    </div>
  );
}

interface Slide {
  readonly id: string;
  readonly steps: number;
  /** Title and closing slides carry no footer. */
  readonly bare?: boolean;
  readonly body: (step: number) => ReactNode;
}

function slides(d: PitchData, qr: string | null, bench: BenchData, curves: CurvesJson | null): Slide[] {
  const m = d.model;
  // the Jezero and Rosamond Dry Lake card: the finder and fingerprint story
  const jr = d.results.find((r) => r.targetName.includes('Jezero')) ?? null;
  const nuSeries: LineSeries[] = d.curves.map((c, k) => ({
    id: c.id,
    label: k === 0 ? c.role.replace(' crater', '') : c.role.replace(' Dry Lake', ''),
    color: k === 0 ? C.orange : C.blue,
    points: c.points.map((p) => ({ x: p.lag_m, y: p.nu })),
  }));
  const nuYs = d.curves.flatMap((c) => c.points.map((p) => p.nu));
  const nuTicks = nuYs.length ? (() => {
    const lo = Math.floor(Math.min(...nuYs) / 5) * 5;
    const hi = Math.ceil(Math.max(...nuYs) / 5) * 5;
    const step = Math.max(5, Math.round((hi - lo) / 4 / 5) * 5);
    return Array.from({ length: Math.floor((hi - lo) / step) + 1 }, (_, k) => lo + k * step).filter((v) => v > 0);
  })() : [];
  return [
    {
      id: 'title',
      steps: 1,
      bare: true,
      body: () => (
        <div className="relative grid h-full grid-cols-[1fr_1fr] items-center gap-8">
          <div className="flex flex-col gap-7">
            <In>
              <h1 className="text-[6.5rem] font-medium leading-none tracking-[-0.045em]">Terraluna</h1>
            </In>
            <In d={0.12}>
              <p className="text-[1.75rem] leading-snug tracking-[-0.01em]" style={{ color: MUTED }}>Find the Moon and Mars on Earth.</p>
            </In>
          </div>
          <In d={0.2} className="relative mx-auto aspect-square h-[78vh] max-w-full">
            <EarthHero className="absolute inset-0" />
          </In>
          <p className="absolute bottom-0 left-0 text-sm" style={{ color: FAINT }}>
            NASA Space Apps 2026, Team Terraluna
          </p>
        </div>
      ),
    },
    {
      id: 'problem',
      steps: 1,
      body: () => (
        <Frame title="Problem">
          <div className="flex h-full flex-col justify-center gap-20 pb-[4vh]">
            <In d={0.1}>
              <p className="max-w-[60rem] text-[2.25rem] font-normal leading-[1.25] tracking-[-0.02em]">
                Astronauts and rovers train on Earth first. But <span style={{ color: ACCENT }}>which place on Earth really matches the target?</span>
              </p>
            </In>
            <div className="grid grid-cols-3 gap-10">
              {[
                { big: String(d.targets.length), unit: 'target sites', caption: d.targets.map((t) => `${t.name} (${BODY[t.body]})`).join(', ') },
                { big: 'By eye', unit: 'how analogs are picked', caption: 'Chosen by experience, not by one shared terrain measure.' },
                { big: 'Any', unit: 'point on Earth', caption: 'Terraluna measures it live in the browser, in seconds.' },
              ].map((c, k) => (
                <In key={c.unit} d={0.2 + k * 0.08} className="flex flex-col gap-2 border-t pt-5">
                  <span className="mb-2 size-2 rounded-full" style={{ background: [C.orange, C.green, C.blue][k] }} />
                  <span className="text-[3.5rem] font-medium leading-none tracking-[-0.03em]">{c.big}</span>
                  <span className="mt-1 text-lg">{c.unit}</span>
                  <span className="max-w-[22rem] text-base leading-snug" style={{ color: FAINT }}>{c.caption}</span>
                </In>
              ))}
            </div>
          </div>
        </Frame>
      ),
    },
    {
      id: 'physics',
      steps: 1,
      body: () => (
        <Frame title="Physics fingerprint" sub="How much the height changes over each distance, the same code in Python and in your browser.">
          <div className="grid h-full grid-cols-[1fr_17rem] gap-10">
            <In d={0.12} className="h-full">
              <FigureCard
                title="Structure function"
                sub="Relief nu(L) per lag, closer curves mean more alike terrain"
                caption={`Figure 1. Relief structure function nu(L) at ${d.lags.length ? `${d.lags[0]} to ${d.lags[d.lags.length - 1]} m` : 'the stored'} lags.${jr ? ` Relief distance ${jr.relief.toFixed(3)}.` : ''}`}
              >
                {nuSeries.length ? (
                  <LineFigure
                    series={nuSeries}
                    xTicks={d.lags}
                    yTicks={nuTicks}
                    xLog
                    yLog
                    dots
                    xLabel="Lag L (m)"
                    yLabel="nu(L) (m)"
                    fmtX={(x) => String(x)}
                    fmtY={(y) => (y >= 10 ? y.toFixed(1) : y.toFixed(2))}
                    ariaLabel="Relief structure function nu(L), log-log, for the Jezero and Rosamond windows"
                    height={340}
                  />
                ) : <Unavailable reason="no fingerprint curve in bundle" />}
              </FigureCard>
            </In>
            <div className="flex flex-col justify-end gap-8 pb-2">
              {jr ? (
                <In d={0.3} className="flex flex-col gap-6">
                  <Key value={<span style={{ color: ACCENT }}>{jr.relief.toFixed(3)}</span>} label="relief distance, lower is closer" />
                  {jr.closerThan !== null ? <Key value={`${jr.closerThan}%`} label="of candidate windows are farther" /> : null}
                  <Key value="5 km" label="square window on both worlds" />
                </In>
              ) : <Unavailable reason="no Jezero and Rosamond card" />}
            </div>
          </div>
        </Frame>
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
      steps: SSL_STEPS.length,
      body: (step) => (
        <Frame title="Our model" sub={<>A ResNet terrain encoder, trained <A>self-supervised</A>, that learns the physics.</>}>
          <In d={0.15} className="flex h-full min-h-0 items-start">
            <SslExplainer step={step} />
          </In>
        </Frame>
      ),
    },
    {
      id: 'training',
      steps: 1,
      body: () => (
        <Frame title="Training" sub="Self-supervised on Moon, Mars and Earth windows, losses read from the release bundle.">
          {/* figure area: components/pitch/fig-training.tsx is wired in here */}
          <div className="h-full w-full" data-figure="training">{curves ? <FigTraining curves={curves} /> : null}</div>
        </Frame>
      ),
    },
    {
      id: 'metrics',
      steps: 1,
      body: () => (
        <Frame title="Benchmarks" sub="Experimental learned similarity, evidence label Predicted, a second opinion beside the physics.">
          {/* figure area: components/pitch/fig-benchmarks.tsx is wired in here */}
          <div className="h-full w-full" data-figure="benchmarks"><FigBenchmarks data={bench} /></div>
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
        <Frame title="Results" sub={<><A>{d.cardCount} evidence cards</A> in the release bundle. Two of them:</>}>
          <div className="grid grid-cols-2 items-start gap-6 pt-[4vh]">
            {d.results.map((r, k) => (
              <In key={r.cardId} d={0.15 + k * 0.1} className="h-full">
                <FigureCard
                  title={<>{r.targetName} <span style={{ color: FAINT }}>and</span> {r.earthName.split(',')[0]}</>}
                  sub={`${BODY[r.body]} vs Earth, ${r.label}`}
                  caption="Candidate windows found by this search."
                >
                  <div className="mt-2 grid grid-cols-2 gap-8">
                    <div className="flex flex-col gap-2">
                      <p className="text-[2.75rem] font-medium leading-none tracking-[-0.03em] tabular-nums" style={{ color: k === 0 ? ACCENT : INK }}>{r.relief.toFixed(3)}</p>
                      <p className="text-lg">relief distance</p>
                      <p className="text-sm" style={{ color: FAINT }}>{r.unit}, lower is closer</p>
                    </div>
                    {r.closerThan !== null ? (
                      <div className="flex flex-col gap-2">
                        <p className="text-[2.75rem] font-medium leading-none tracking-[-0.03em] tabular-nums">{r.closerThan}%</p>
                        <p className="text-lg">closer than</p>
                        <p className="text-sm" style={{ color: FAINT }}>of candidate windows searched</p>
                      </div>
                    ) : null}
                  </div>
                </FigureCard>
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
              <h2 className="text-[5.5rem] font-medium leading-none tracking-[-0.045em]">Terraluna</h2>
            </In>
            <In d={0.12}>
              <p className="text-[1.75rem] tracking-[-0.01em]" style={{ color: MUTED }}>Measure any point on Earth, live.</p>
            </In>
            <In d={0.2} className="mt-4 flex flex-col gap-2 text-xl">
              <a href={`https://${SITE}`} onClick={(e) => e.stopPropagation()} className="underline decoration-zinc-300 underline-offset-8">
                {SITE}
              </a>
              <a href={`https://${GITHUB}`} onClick={(e) => e.stopPropagation()} className="text-base underline decoration-zinc-200 underline-offset-8" style={{ color: FAINT }}>
                {GITHUB}
              </a>
            </In>
          </div>
          {qr ? (
            <In d={0.3} className="flex flex-col items-center gap-3">
              <div className="w-[min(22vw,300px)] rounded-xl border bg-white p-6 [&_svg]:h-auto [&_svg]:w-full" style={{ borderColor: C.hair }} role="img" aria-label={`QR code for https://${SITE}`} dangerouslySetInnerHTML={{ __html: qr }} />
              <span className="text-sm" style={{ color: FAINT }}>scan to open</span>
            </In>
          ) : null}
          <p className="absolute bottom-0 left-0 text-sm" style={{ color: FAINT }}>
            NASA Space Apps 2026, Team Terraluna
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

export function Deck({ data, bench, curves, qr, fonts = '' }: { data: PitchData; bench: BenchData; curves: CurvesJson | null; qr: string | null; fonts?: string }) {
  const list = slides(data, qr, bench, curves);
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

  if (host === null) return <div className="fixed inset-0 z-[1000]" style={{ background: PAPER }} />;
  return createPortal(
    <div
      className={`pitch-root fixed inset-0 z-[1000] overflow-hidden antialiased ${fonts}`}
      style={{ background: PAPER, color: INK }}
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
      <div className="absolute inset-x-0 top-0 z-10 h-px" style={{ background: 'rgba(26,22,19,0.15)' }}>
        <div className="h-full" style={{ background: ACCENT, width: `${((i + 1) / n) * 100}%` }} />
      </div>

      <section
        key={slide?.id}
        aria-roledescription="slide"
        aria-label={`${i + 1} of ${n}: ${note?.title ?? ''}`}
        className="absolute inset-0 px-[6vw] pb-[10vh] pt-[8vh]"
      >
        {slide?.body(step)}
      </section>

      {/* the Lab's 3D stage: mounted one slide early, shown only on the Lab slide */}
      {labMounted && data.lab ? (
        <motion.div
          className="absolute bottom-[8vh] left-[6vw] right-[6vw] top-[20vh]"
          style={{ opacity: i === labIndex ? 1 : 0, pointerEvents: i === labIndex ? 'auto' : 'none', visibility: i === labIndex || i === labIndex - 1 ? 'visible' : 'hidden' }}
          onClick={(e) => e.stopPropagation()}
          aria-hidden={i !== labIndex}
        >
          <FigLab earth={data.lab.earth} target={data.lab.target} earthName={data.lab.earthName} targetName={data.lab.targetName} />
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
            className="absolute inset-x-0 bottom-0 z-30 border-t border-zinc-200 bg-[#F9F8F5]/95 backdrop-blur"
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
            className="absolute inset-0 z-40 grid place-items-center bg-[#F9F8F5]/90 p-[6vw] backdrop-blur"
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
                    className="flex aspect-[16/7] w-full flex-col justify-between rounded-2xl border bg-white/70 p-5 text-left transition-colors hover:border-zinc-950"
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
        .pitch-root, .pitch-root * { font-family: var(--pitch-sans), ui-sans-serif, system-ui, sans-serif; }
        .pitch-root .font-mono, .pitch-root .font-mono * { font-family: var(--pitch-mono), ui-monospace, monospace; }
        .pitch-root .text-zinc-950, .pitch-root .text-zinc-900 { color: #1A1613; }
        .pitch-root .text-zinc-600, .pitch-root .text-zinc-500 { color: #4D4843; }
        .pitch-root .text-zinc-400 { color: #A39E95; }
        .pitch-root .border-zinc-200, .pitch-root .border-zinc-300 { border-color: rgba(26,22,19,0.1); }
        .pitch-root .bg-zinc-50 { background-color: rgba(255,255,255,0.55); }
        .pitch-root .bg-zinc-100, .pitch-root .bg-zinc-200 { background-color: rgba(26,22,19,0.08); }
        .pitch-root .bg-zinc-950 { background-color: #1A1613; }
        .pitch-border-beam { padding: 1.5px; background: conic-gradient(from var(--pitch-a, 0deg), transparent 0deg, #CC5800 40deg, transparent 90deg) border-box;
          -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor; mask-composite: exclude; animation: pitch-spin 6s linear infinite; }
        @property --pitch-a { syntax: '<angle>'; inherits: false; initial-value: 0deg; }
        @keyframes pitch-spin { to { --pitch-a: 360deg; } }
        @media (prefers-reduced-motion: reduce) { .pitch-border-beam { animation: none; } }
      `}</style>
    </div>,
    host,
  );
}
