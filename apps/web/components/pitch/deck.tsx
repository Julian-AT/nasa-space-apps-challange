'use client';

// The /pitch deck: white full-viewport slides over the site chrome. Keys: ArrowRight / d / Space
// next, ArrowLeft / a previous, Home / End; click and swipe also move. The URL hash (#3) keeps place.
import { AnimatePresence, motion } from 'motion/react';
import dynamic from 'next/dynamic';
import { type ReactNode, useCallback, useEffect, useRef, useState } from 'react';

import { CountUp, EASE, NuChart } from './bits';
import type { PitchData } from './pitch-data';

const LabTwin = dynamic(() => import('./lab-twin'), { ssr: false, loading: () => <div className="h-full bg-[#0b0c0f]" /> });

const GITHUB = 'https://github.com/Julian-AT/nasa-space-apps-challange';
const SITE = 'terraluna-lovat.vercel.app';

const fmt = (v: number | null, digits = 3) => (v === null ? null : Number(v.toPrecision(digits)).toString());

function Unavailable({ reason }: { reason: string }) {
  return <span className="text-zinc-400">Unavailable ({reason})</span>;
}

function Kicker({ children }: { children: ReactNode }) {
  return <p className="font-mono text-sm uppercase tracking-[0.2em] text-[var(--primary)]">{children}</p>;
}

function Chips({ items }: { items: readonly string[] }) {
  return (
    <ul className="flex flex-wrap gap-2">
      {items.map((t, i) => (
        <motion.li
          key={t}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 + i * 0.08, ease: EASE }}
          className="rounded-full border border-zinc-200 px-4 py-1.5 text-base text-zinc-700"
        >
          {t}
        </motion.li>
      ))}
    </ul>
  );
}

function Metric({ label, children, note }: { label: string; children: ReactNode; note?: string | undefined }) {
  return (
    <div className="flex flex-col gap-1 border-t border-zinc-200 pt-4">
      <span className="text-5xl font-semibold tracking-tight text-zinc-900 xl:text-6xl">{children}</span>
      <span className="text-lg text-zinc-600">{label}</span>
      {note ? <span className="text-sm text-zinc-400">{note}</span> : null}
    </div>
  );
}

function slides(d: PitchData): { id: string; body: ReactNode }[] {
  const m = d.model;
  const jez = d.ranges.find((r) => r.targetId === 'mars-jezero-w000');
  const arc = d.ranges.find((r) => r.targetId === 'mars-arcadia-w000');
  const cr = d.ranges.find((r) => r.targetId.startsWith('moon-'));
  const body = d.best?.targetId.startsWith('moon') ? 'moon' : 'mars';
  return [
    {
      id: 'title',
      body: (
        <div className="flex h-full flex-col justify-center gap-8">
          <Kicker>NASA Space Apps 2026 · Gradient Descent</Kicker>
          <h1 className="text-[10rem] font-semibold leading-none tracking-tighter">
            Terra<span className="text-[var(--primary)]">luna</span>
          </h1>
          <p className="text-4xl text-zinc-600">Find the Moon and Mars on Earth.</p>
        </div>
      ),
    },
    {
      id: 'problem',
      body: (
        <div className="flex h-full flex-col justify-center gap-10">
          <Kicker>The problem</Kicker>
          <h2 className="max-w-5xl text-6xl font-semibold leading-tight tracking-tight">
            Astronauts and rovers train on Earth first. Which Earth terrain really matches?
          </h2>
          <div className="grid grid-cols-3 gap-6">
            {['Connecting Ridge', 'Jezero crater', 'Arcadia Planitia'].map((t, i) => (
              <motion.div key={t} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 + i * 0.12, ease: EASE }} className="rounded-2xl border border-zinc-200 p-8">
                <p className="font-mono text-sm text-zinc-400">{i === 0 ? 'Moon · south pole' : 'Mars'}</p>
                <p className="mt-2 text-3xl font-semibold">{t}</p>
              </motion.div>
            ))}
          </div>
        </div>
      ),
    },
    {
      id: 'physics',
      body: (
        <div className="grid h-full grid-cols-[1fr_1.2fr] items-center gap-16">
          <div className="flex flex-col gap-8">
            <Kicker>Our physics model</Kicker>
            <h2 className="text-6xl font-semibold leading-tight tracking-tight">
              Structure function <span className="font-mono">ν(L)</span>
            </h2>
            <p className="text-2xl text-zinc-600">
              {d.lags.length ? `${d.lags[0]}–${d.lags[d.lags.length - 1]} m lags` : 'multi-scale lags'} → relief &amp; texture fingerprint
            </p>
            <Chips items={['same code in Python and in your browser', 'tested parity', 'live on any Earth point']} />
          </div>
          <div className="flex flex-col gap-3">
            {d.curves.length ? <NuChart curves={d.curves} lags={d.lags} /> : <Unavailable reason="no fingerprint curve in bundle" />}
            <div className="flex gap-6 text-sm text-zinc-500">
              {d.curves.map((c, k) => (
                <span key={c.id} className="flex items-center gap-2">
                  <span className="h-1 w-6 rounded" style={{ background: k === 0 ? 'var(--primary)' : '#18181b' }} />
                  {c.role} <span className="font-mono text-zinc-400">{c.id}</span>
                </span>
              ))}
            </div>
            <p className="text-xs text-zinc-400">Derived · relief ν(L) as stored in {d.curvesFrom ?? 'the bundle'}</p>
          </div>
        </div>
      ),
    },
    {
      id: 'model',
      body: (
        <div className="grid h-full grid-cols-[1fr_1fr] items-center gap-16">
          <div className="flex flex-col gap-8">
            <Kicker>The learned model · experimental</Kicker>
            <h2 className="text-6xl font-semibold leading-tight tracking-tight">A terrain encoder that knows physics</h2>
            <Chips items={['ResNet-18 layout', 'VICReg self-supervision', 'physics head', 'gradient-reversal body adversary', `${m.embeddingDim ?? 256}-d embedding`]} />
          </div>
          <motion.img
            initial={{ opacity: 0, scale: 0.97 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.8, ease: EASE }}
            src="/figures/encoder-architecture.svg"
            alt="Encoder architecture: ResNet-18 backbone, VICReg projector, physics head and gradient-reversal body adversary"
            className="max-h-[72vh] w-full object-contain"
          />
        </div>
      ),
    },
    {
      id: 'metrics',
      body: (
        <div className="flex h-full flex-col justify-center gap-12">
          <div className="flex flex-col gap-4">
            <Kicker>Experimental learned similarity · {m.version ?? 'model card'}</Kicker>
            <h2 className="text-6xl font-semibold tracking-tight">Small, fast, and identical in the browser</h2>
          </div>
          <div className="grid grid-cols-3 gap-x-12 gap-y-10">
            <Metric label="parameters">{m.parameters === null ? <Unavailable reason="model card" /> : <CountUp value={m.parameters} />}</Metric>
            <Metric label="training windows" note={m.totalWindows === null ? undefined : `${m.totalWindows.toLocaleString('en-US')} windows total, Moon · Mars · Earth`}>
              {m.trainWindows === null ? <Unavailable reason="model card" /> : <CountUp value={m.trainWindows} />}
            </Metric>
            <Metric label="browser parity, min cosine" note="ONNX → WebAssembly">
              {m.wasmMinCosine === null ? <Unavailable reason="numbers.tex" /> : <CountUp value={m.wasmMinCosine} digits={6} />}
            </Metric>
            <Metric label="faster training on Apple MPS" note={m.mpsMedian !== null && m.cpuMedian !== null ? `${m.mpsMedian} vs ${m.cpuMedian} windows/s, median` : undefined}>
              {m.mpsMedian !== null && m.cpuMedian ? (
                <>
                  ~<CountUp value={Math.round(m.mpsMedian / m.cpuMedian)} />×
                </>
              ) : (
                <Unavailable reason="run records" />
              )}
            </Metric>
            <Metric label="physics probe R², lower bound" note="pooled, relief ν at every lag, 95% CI">
              {m.probeR2Low === null ? <Unavailable reason="model card" /> : <CountUp value={Number(m.probeR2Low.toFixed(3))} digits={3} />}
            </Metric>
            <Metric label="evidence label" note="model outputs, outside the rankings">
              <span className="text-[var(--primary)]">Predicted</span>
            </Metric>
          </div>
        </div>
      ),
    },
    {
      id: 'lab',
      body: (
        <div className="flex h-full flex-col gap-6 py-4">
          <div className="flex items-end justify-between gap-8">
            <div className="flex flex-col gap-3">
              <Kicker>The Lab</Kicker>
              <h2 className="text-5xl font-semibold tracking-tight">
                {d.twin ? `${d.twin.earthName} ↔ ${d.twin.targetName}` : 'Side-by-side terrain twin'}
              </h2>
            </div>
            <Chips items={['real elevation tiles', 'rover simulation', 'side-by-side twin']} />
          </div>
          <div className="relative min-h-0 flex-1 overflow-hidden rounded-3xl bg-[#0b0c0f]" onClick={(e) => e.stopPropagation()}>
            {d.twin ? <LabTwin earth={d.twin.earth} target={d.twin.target} body={body} /> : <Unavailable reason="no card polygons" />}
            {d.twin ? (
              <div className="pointer-events-none absolute inset-x-0 top-0 flex justify-between p-5 text-sm text-zinc-300">
                <span>Earth · {d.twin.earthName}</span>
                <span>{d.twin.targetName}</span>
              </div>
            ) : null}
          </div>
          <p className="text-xs text-zinc-400">80 km around the best card&apos;s two windows · Terrarium SRTM and NASA Trek MOLA tiles · drag to orbit</p>
        </div>
      ),
    },
    {
      id: 'results',
      body: (
        <div className="flex h-full flex-col justify-center gap-12">
          <div className="flex flex-col gap-4">
            <Kicker>Results · bundle {d.bundleVersion}</Kicker>
            <h2 className="text-6xl font-semibold tracking-tight">
              <CountUp value={d.cardCount} duration={0.8} /> evidence cards
            </h2>
          </div>
          <div className="grid grid-cols-3 gap-6">
            {[jez, arc, cr].map((r) =>
              r ? (
                <div key={r.targetId} className="flex flex-col gap-3 rounded-2xl border border-zinc-200 p-8">
                  <p className="text-2xl font-semibold">{r.targetName}</p>
                  <p className="font-mono text-4xl text-[var(--primary)]">
                    {r.min === null ? '—' : r.n > 1 ? `${fmt(r.min, 2)}–${fmt(r.max, 3)}` : fmt(r.min, 3)}
                  </p>
                  <p className="text-sm text-zinc-500">terrain relief distance, {r.unit ?? 'unit unavailable'} · lower is closer</p>
                  <p className="text-base text-zinc-700">↔ {r.earthRegions.join(', ')}</p>
                </div>
              ) : null,
            )}
          </div>
          <p className="text-sm text-zinc-400">Derived · candidate windows identified by this search, not proven analogs · radar and encoder distances Unavailable on these cards</p>
        </div>
      ),
    },
    {
      id: 'close',
      body: (
        <div className="flex h-full flex-col justify-center gap-10">
          <h2 className="text-8xl font-semibold leading-none tracking-tighter">
            Measure any point on Earth — <span className="text-[var(--primary)]">live.</span>
          </h2>
          <div className="flex flex-col gap-3 text-2xl" onClick={(e) => e.stopPropagation()}>
            <a href={`https://${SITE}`} className="font-mono underline decoration-zinc-300 underline-offset-8 hover:decoration-[var(--primary)]">
              {SITE}
            </a>
            <a href={GITHUB} className="font-mono text-zinc-600 underline decoration-zinc-300 underline-offset-8 hover:decoration-[var(--primary)]">
              github.com/Julian-AT/nasa-space-apps-challange
            </a>
            {d.paperHref ? (
              <a href={d.paperHref} className="text-zinc-600 underline decoration-zinc-300 underline-offset-8 hover:decoration-[var(--primary)]">
                Paper (PDF)
              </a>
            ) : null}
          </div>
          <p className="text-lg text-zinc-400">Terraluna · Gradient Descent · NASA Space Apps 2026</p>
        </div>
      ),
    },
  ];
}

export function Deck({ data }: { data: PitchData }) {
  const list = slides(data);
  const n = list.length;
  const [i, setI] = useState(0);
  const [dir, setDir] = useState(1);
  const touch = useRef<number | null>(null);

  useEffect(() => {
    const k = Number(window.location.hash.slice(1));
    if (Number.isInteger(k) && k >= 1 && k <= n) setI(k - 1);
  }, [n]);

  const go = useCallback(
    (to: number) => {
      const next = Math.max(0, Math.min(n - 1, to));
      setI((cur) => {
        setDir(next >= cur ? 1 : -1);
        return next;
      });
      window.history.replaceState(null, '', `#${next + 1}`);
    },
    [n],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'ArrowRight' || e.key === 'd' || e.key === ' ' || e.key === 'PageDown') {
        e.preventDefault();
        go(i + 1);
      } else if (e.key === 'ArrowLeft' || e.key === 'a' || e.key === 'PageUp') {
        e.preventDefault();
        go(i - 1);
      } else if (e.key === 'Home') go(0);
      else if (e.key === 'End') go(n - 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [go, i, n]);

  const slide = list[i];
  return (
    <div
      className="fixed inset-0 z-50 overflow-hidden bg-white font-sans text-zinc-900"
      role="region"
      aria-roledescription="slide deck"
      aria-label="Terraluna pitch"
      onClick={(e) => go(e.clientX < window.innerWidth * 0.3 ? i - 1 : i + 1)}
      onTouchStart={(e) => (touch.current = e.touches[0]?.clientX ?? null)}
      onTouchEnd={(e) => {
        const x0 = touch.current;
        const x1 = e.changedTouches[0]?.clientX;
        if (x0 !== null && x1 !== undefined && Math.abs(x1 - x0) > 50) go(x1 < x0 ? i + 1 : i - 1);
        touch.current = null;
      }}
    >
      {data.mock ? (
        <div role="alert" className="absolute inset-x-0 top-0 z-10 bg-amber-100 px-6 py-2 text-center text-sm font-medium text-amber-900">
          Mock preview: fixture bundle, not production data
        </div>
      ) : null}
      <AnimatePresence mode="wait" custom={dir}>
        <motion.section
          key={slide?.id}
          aria-roledescription="slide"
          aria-label={`${i + 1} of ${n}`}
          custom={dir}
          initial={{ opacity: 0, x: dir * 40 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: dir * -40 }}
          transition={{ duration: 0.45, ease: EASE }}
          className="absolute inset-0 px-[7vw] pb-20 pt-14"
        >
          {slide?.body}
        </motion.section>
      </AnimatePresence>
      <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-6 px-[7vw] pb-6 text-sm text-zinc-400">
        <span className="font-mono">
          Terraluna · bundle {data.bundleVersion} · mode {data.shippingMode ?? 'unset'}
        </span>
        <span className="font-mono tabular-nums" aria-live="polite">
          {String(i + 1).padStart(2, '0')} / {String(n).padStart(2, '0')}
        </span>
      </div>
      <div className="absolute inset-x-0 bottom-0 h-1 bg-zinc-100">
        <motion.div className="h-1 bg-[var(--primary)]" animate={{ width: `${((i + 1) / n) * 100}%` }} transition={{ duration: 0.5, ease: EASE }} />
      </div>
    </div>
  );
}
