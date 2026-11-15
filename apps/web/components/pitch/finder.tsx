'use client';

// The finder slide: the Earth globe flies to one evidence card's Earth window and the live probe
// (workers/probe.worker.ts, unchanged) measures the fingerprint there against the card's target,
// in the browser. Shown values are the probe's own output, labelled "measured live"; until it
// answers (or if it cannot run) the card's stored values are shown, labelled "from the evidence card".
import { AnimatePresence, motion } from 'motion/react';
import { useEffect, useRef, useState } from 'react';

import { COP30_MODE } from '@/lib/probe/mode';
import { type ProbeResult, isProbeResult } from '@/lib/probe/result';

import { EASE } from './bits';
import { EarthHero } from './earth-hero';
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

  // the card's Earth window centroid; the globe opens on the Americas and turns to it on →
  const at = step >= 1 ? { lat: finder.lat, lon: finder.lon } : null;

  return (
    <div className="grid h-full grid-cols-[1.05fr_1fr] items-center gap-12">
      <div className="relative mx-auto aspect-square h-full max-h-[54vh]" onClick={(e) => e.stopPropagation()}>
        {/* ripple rings behind the globe (after Magic UI's Ripple, MIT) */}
        <div aria-hidden className="pointer-events-none absolute inset-0">
          {[0, 1, 2].map((k) => (
            <motion.span
              key={k}
              className="absolute left-1/2 top-1/2 rounded-full border border-[rgba(26,22,19,0.1)]"
              style={{ width: `${100 + k * 11}%`, height: `${100 + k * 11}%`, x: '-50%', y: '-50%', opacity: 0.9 - k * 0.25 }}
              animate={{ scale: [1, 0.96, 1] }}
              transition={{ duration: 3.2, repeat: Infinity, ease: 'easeInOut', delay: k * 0.12 }}
            />
          ))}
        </div>
        <EarthHero className="absolute inset-0" focus={at} pin={at} startLon={-65} shadow={false} />
      </div>
      <div className="flex flex-col gap-6">
        <AnimatePresence mode="wait">
          {step === 0 ? (
            <motion.p key="hint" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="text-[1.75rem] leading-snug tracking-[-0.01em] text-[#4D4843]">
              Click any spot on Earth.
              <br />
              <span className="text-[#1A1613]">Real elevation to fingerprint, in seconds.</span>
            </motion.p>
          ) : (
            <motion.div
              key="card"
              initial={{ opacity: 0, x: 40, filter: 'blur(6px)' }}
              animate={{ opacity: 1, x: 0, filter: 'blur(0px)' }}
              transition={{ duration: 0.7, delay: 0.6, ease: EASE }}
              className="flex flex-col gap-6 rounded-xl border border-[rgba(26,22,19,0.1)] bg-white p-7"
            >
              <div className="flex items-center justify-between gap-4">
                <span
                  className="rounded-md px-2.5 py-1 text-sm font-medium"
                  style={isLive ? { background: '#FFE8CC', color: '#CC5800' } : { background: '#F2F0EB', color: '#4D4843' }}
                >
                  {isLive ? `Measured live in your browser${ms === null ? '' : `, ${(ms / 1000).toFixed(1)} s`}` : why === null ? 'Measuring live…' : 'From the evidence card'}
                </span>
                <span className="font-mono text-sm text-[#A39E95]">
                  {finder.lat.toFixed(3)}°, {finder.lon.toFixed(3)}°
                </span>
              </div>
              <div>
                <p className="text-[1.375rem] font-medium tracking-[-0.01em]">{finder.earthName}</p>
                <p className="mt-1 text-sm text-[#A39E95]">vs {finder.targetName}</p>
              </div>
              <div className="grid grid-cols-2 gap-6 border-t border-[rgba(26,22,19,0.1)] pt-6">
                <div>
                  <p className="text-5xl font-medium tracking-[-0.02em] tabular-nums text-[#CC5800]">
                    {f3(isLive ? relief : finder.relief)}
                  </p>
                  <p className="mt-1 text-base text-[#4D4843]">relief distance, lower is closer</p>
                </div>
                <div>
                  <p className="text-5xl font-medium tracking-[-0.02em] tabular-nums text-[#4A8044]">{f3(isLive ? texture : finder.texture)}</p>
                  <p className="mt-1 text-base text-[#4D4843]">texture distance</p>
                </div>
              </div>
              {closer !== null ? (
                <p className="text-lg text-[#4D4843]">
                  Closer than <span className="font-medium text-[#1A1613]">{closer}%</span> of candidate windows
                  <span className="text-sm text-[#A39E95]">{closerFromCard ? ', from the evidence card' : ', live background'}</span>
                </p>
              ) : null}
              <p className="text-sm text-[#A39E95]">
                Figure. Derived (live), Copernicus GLO-30, terrain only
                {why !== null && !isLive ? `, live probe unavailable (${why})` : ''}
              </p>
            </motion.div>
          )}
        </AnimatePresence>
        <a
          href="/find"
          onClick={(e) => e.stopPropagation()}
          className="inline-flex w-fit items-center gap-2 rounded-lg border border-[rgba(26,22,19,0.1)] bg-white px-4 py-2 text-base font-medium text-[#1A1613] transition-colors hover:border-[#CC5800] hover:text-[#CC5800]"
        >
          Try it on /find
        </a>
      </div>
    </div>
  );
}
