'use client';

// The finder slide: the Earth globe flies to one evidence card's Earth window and the live probe
// (workers/probe.worker.ts, unchanged) measures the fingerprint there against the card's target,
// in the browser. Shown values are the probe's own output, labelled "measured live"; until it
// answers (or if it cannot run) the card's stored values are shown, labelled "from the evidence card".
import { AnimatePresence, motion } from 'motion/react';
import { useEffect, useRef, useState } from 'react';

import { Globe } from '@/components/globe';
import { COP30_MODE } from '@/lib/probe/mode';
import { type ProbeResult, isProbeResult } from '@/lib/probe/result';

import { EASE, INK } from './bits';
import type { PitchData } from './pitch-data';

type Finder = NonNullable<PitchData['finder']>;

const f3 = (v: number | null | undefined) => (v === null || v === undefined ? 'n/a' : v.toFixed(3));

export default function FinderDemo({ finder, step }: { readonly finder: Finder; readonly step: number }) {
  const [result, setResult] = useState<ProbeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ms, setMs] = useState<number | null>(null);
  const started = useRef(0);

  // Start the live probe when the slide mounts, so the answer is usually ready by the → press.
  useEffect(() => {
    let w: Worker;
    try {
      w = new Worker(new URL('../../workers/probe.worker.ts', import.meta.url), { type: 'module' });
    } catch {
      setError('no Web Workers');
      return;
    }
    w.onmessage = (event: MessageEvent<unknown>) => {
      if (!isProbeResult(event.data) || event.data.request_id !== 1) return;
      setResult(event.data);
      setMs(performance.now() - started.current);
    };
    w.onerror = (event: ErrorEvent) => setError(event.message || 'probe worker failed');
    w.postMessage({ type: 'init', mode: COP30_MODE, targets: finder.fingerprints, targetsReason: null, bundleVersion: finder.bundleVersion, encoder: null });
    started.current = performance.now();
    w.postMessage({ type: 'probe', id: 1, latDeg: finder.lat, lonDeg: finder.lon });
    return () => w.terminate();
  }, [finder]);

  const live = result?.kind === 'result' ? result.targets.find((t) => t.target_id === finder.targetId) ?? null : null;
  const relief = live?.terrain_relief.value ?? null;
  const texture = live?.terrain_texture.value ?? null;
  const isLive = live !== null && relief !== null;
  const livePct = live?.terrain_relief.background_percentile;
  const closer = isLive && typeof livePct === 'number' ? Math.floor(100 - livePct) : finder.closerThan;
  const closerFromCard = !(isLive && typeof livePct === 'number');
  const why = error ?? (result !== null && result.kind !== 'result' ? (result.kind === 'stop' ? 'too few valid cells' : result.reason) : null);

  const pin = { id: 'finder', lat: finder.lat, lon: finder.lon, label: finder.earthName, href: '/find', tone: 'earth' as const };

  return (
    <div className="grid h-full grid-cols-[1.05fr_1fr] items-center gap-12">
      <div className="relative mx-auto aspect-square h-full max-h-[54vh]" onClick={(e) => e.stopPropagation()}>
        {/* ripple rings behind the globe (after Magic UI's Ripple, MIT) */}
        <div aria-hidden className="pointer-events-none absolute inset-0">
          {[0, 1, 2].map((k) => (
            <motion.span
              key={k}
              className="absolute left-1/2 top-1/2 rounded-full border border-zinc-200"
              style={{ width: `${100 + k * 11}%`, height: `${100 + k * 11}%`, x: '-50%', y: '-50%', opacity: 0.9 - k * 0.25 }}
              animate={{ scale: [1, 0.96, 1] }}
              transition={{ duration: 3.2, repeat: Infinity, ease: 'easeInOut', delay: k * 0.12 }}
            />
          ))}
        </div>
        {/* clipped to the planet disc: no atmosphere rim on the white slide */}
        <div className="absolute inset-0" style={{ clipPath: 'circle(41% at 50% 50%)' }}>
          <Globe body="earth" pins={[pin]} selectedId={step >= 1 ? 'finder' : null} autoRotate={step === 0} />
        </div>
        <AnimatePresence>
          {step >= 1 ? (
            <motion.span
              aria-hidden
              className="pointer-events-none absolute left-1/2 top-1/2 size-6 -translate-x-1/2 -translate-y-1/2 rounded-full border-2"
              style={{ borderColor: INK }}
              initial={{ scale: 0.4, opacity: 0.9 }}
              animate={{ scale: [0.4, 3.2], opacity: [0.9, 0] }}
              transition={{ duration: 1.6, repeat: Infinity, ease: 'easeOut', delay: 1.1 }}
            />
          ) : null}
        </AnimatePresence>
      </div>
      <div className="flex flex-col gap-6">
        <AnimatePresence mode="wait">
          {step === 0 ? (
            <motion.p key="hint" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="text-3xl leading-snug text-zinc-500">
              Click any spot on Earth.
              <br />
              <span className="text-zinc-900">Real elevation to fingerprint, in seconds.</span>
            </motion.p>
          ) : (
            <motion.div
              key="card"
              initial={{ opacity: 0, x: 40, filter: 'blur(6px)' }}
              animate={{ opacity: 1, x: 0, filter: 'blur(0px)' }}
              transition={{ duration: 0.7, delay: 0.6, ease: EASE }}
              className="flex flex-col gap-6 rounded-2xl border border-zinc-200 bg-zinc-50 p-8"
            >
              <div className="flex items-center justify-between gap-4">
                <span
                  className="rounded-full px-3 py-1 text-sm font-medium"
                  style={isLive ? { background: INK, color: '#ffffff' } : { background: '#e4e4e7', color: '#52525b' }}
                >
                  {isLive ? `Measured live in your browser${ms === null ? '' : `, ${(ms / 1000).toFixed(1)} s`}` : why === null ? 'Measuring live…' : 'From the evidence card'}
                </span>
                <span className="font-mono text-sm text-zinc-400">
                  {finder.lat.toFixed(3)}°, {finder.lon.toFixed(3)}°
                </span>
              </div>
              <div>
                <p className="text-3xl font-semibold tracking-tight">{finder.earthName}</p>
                <p className="mt-1 text-xl text-zinc-500">vs {finder.targetName}</p>
              </div>
              <div className="grid grid-cols-2 gap-6 border-t border-zinc-200 pt-6">
                <div>
                  <p className="text-5xl font-bold tabular-nums text-zinc-950">
                    {f3(isLive ? relief : finder.relief)}
                  </p>
                  <p className="mt-1 text-base text-zinc-500">relief distance, lower is closer</p>
                </div>
                <div>
                  <p className="text-5xl font-semibold tabular-nums text-zinc-950">{f3(isLive ? texture : finder.texture)}</p>
                  <p className="mt-1 text-base text-zinc-500">texture distance</p>
                </div>
              </div>
              {closer !== null ? (
                <p className="text-xl text-zinc-700">
                  Closer than <span className="font-semibold">{closer}%</span> of candidate windows
                  <span className="text-sm text-zinc-400">{closerFromCard ? ', from the evidence card' : ', live background'}</span>
                </p>
              ) : null}
              <p className="text-xs text-zinc-400">
                Derived (live), Copernicus GLO-30, terrain only
                {why !== null && !isLive ? `, live probe unavailable (${why})` : ''}
              </p>
            </motion.div>
          )}
        </AnimatePresence>
        <a
          href="/find"
          onClick={(e) => e.stopPropagation()}
          className="inline-flex w-fit items-center gap-2 rounded-full bg-zinc-950 px-6 py-3 text-lg font-medium text-white transition-colors hover:bg-zinc-800"
        >
          Try it on /find
        </a>
      </div>
    </div>
  );
}
