'use client';

// Terraluna's own measurement under the ported ShadowAstra factor breakdown: the live COP30 probe
// (workers/probe.worker.ts, lib/probe) for the selected Earth point, shown with the existing
// /find ResultCard. ShadowAstra targets map to the nearest Terraluna target window; targets with
// no Terraluna counterpart say so instead of showing a number. Distances come only from the
// worker's ProbeResult; nothing here is typed in.
import clsx from 'clsx';
import { useEffect, useRef, useState } from 'react';

import { type CardLink, type FindTarget, ResultCard } from '../find/result-card.tsx';
import type { FindEncoder } from '../find/find-client.tsx';
import { COP30_MODE } from '../../lib/probe/mode.ts';
import { type ProbeResult, type ProbeTargetFingerprint, isProbeResult } from '../../lib/probe/result.ts';

/** ShadowAstra target id to the Terraluna target window it is compared against. */
export const TERRALUNA_TARGET_FOR: Readonly<Record<string, string>> = {
  jezero: 'mars-jezero-w000',
  arcadia: 'mars-arcadia-w000',
  // Connecting Ridge lies between Shackleton and de Gerlache.
  shackleton: 'moon-cr-site01-w000',
  'de-gerlache': 'moon-cr-site01-w000',
};

export interface TerralunaProbeProps {
  fingerprints: (ProbeTargetFingerprint & { name: string })[];
  targetsReason: string | null;
  bundleVersion: string;
  encoder: FindEncoder | null;
  targets: FindTarget[];
  cards: CardLink[];
}

export function TerralunaFingerprint({
  lat,
  lon,
  shadowAstraTarget,
  probe,
  className,
}: Readonly<{ lat: number; lon: number; shadowAstraTarget: string; probe: TerralunaProbeProps; className?: string }>) {
  const { fingerprints, targetsReason, bundleVersion, encoder, targets, cards } = probe;
  const tlId = TERRALUNA_TARGET_FOR[shadowAstraTarget];
  const target = tlId === undefined ? undefined : targets.find((t) => t.target_id === tlId);
  const worker = useRef<Worker | null>(null);
  const [ready, setReady] = useState(false);
  const [result, setResult] = useState<ProbeResult | null>(null);
  const [running, setRunning] = useState(false);
  const [workerError, setWorkerError] = useState<string | null>(null);
  const latest = useRef(0);
  const nextId = useRef(1);
  const active = target !== undefined;

  useEffect(() => {
    if (!active) return;
    let w: Worker;
    try {
      w = new Worker(new URL('../../workers/probe.worker.ts', import.meta.url), { type: 'module' });
    } catch {
      setWorkerError('The live probe needs Web Workers, which this browser does not provide.');
      return;
    }
    w.onmessage = (event: MessageEvent<unknown>) => {
      const data = event.data;
      if (!isProbeResult(data) || data.request_id !== latest.current) return;
      setResult(data);
      setRunning(false);
    };
    w.onerror = (event: ErrorEvent) => {
      setWorkerError(`The probe worker failed: ${event.message || 'unknown error'}`);
      setRunning(false);
    };
    w.postMessage({ type: 'init', mode: COP30_MODE, targets: fingerprints, targetsReason, bundleVersion, encoder });
    worker.current = w;
    setReady(true);
    return () => {
      w.terminate();
      worker.current = null;
      setReady(false);
    };
  }, [active, fingerprints, targetsReason, bundleVersion, encoder]);

  useEffect(() => {
    if (!ready || worker.current === null) return;
    const t = setTimeout(() => {
      const id = nextId.current++;
      latest.current = id;
      setRunning(true);
      setResult(null);
      worker.current?.postMessage({ type: 'probe', id, latDeg: Number(lat.toFixed(4)), lonDeg: Number(lon.toFixed(4)) });
    }, 300);
    return () => clearTimeout(t);
  }, [lat, lon, ready]);

  return (
    <section className={clsx('rounded-2xl border border-line-2 bg-ink p-4', className)} aria-label="Terraluna terrain fingerprint" data-terraluna-fingerprint>
      <h3 className="font-mono text-[10px] tracking-[0.22em] text-dim uppercase">Terraluna terrain fingerprint (live, in browser)</h3>
      <p className="mt-1.5 text-[11.5px] leading-snug text-dim">
        Terraluna&apos;s own measurement, separate from the ShadowAstra factor scores above: the 5 km Copernicus GLO-30 window at {lat.toFixed(3)}°, {lon.toFixed(3)}°, computed in a Web Worker against the
        bundle&apos;s target fingerprints (bundle {bundleVersion}).
      </p>
      {target === undefined ? (
        <p className="mt-3 rounded-xl border border-dashed border-line-2 p-3 text-[12.5px] text-chrome" data-terraluna-target="none">
          No Terraluna target for this site.
        </p>
      ) : (
        <div className="mt-3" aria-live="polite" aria-busy={running} data-terraluna-target={target.target_id}>
          <p className="mb-2 text-[12px] text-chrome">
            Compared against Terraluna target <span className="text-platinum">{target.name}</span>
            {shadowAstraTarget === 'shackleton' || shadowAstraTarget === 'de-gerlache' ? ' (Connecting Ridge lies between Shackleton and de Gerlache)' : ''}.
          </p>
          {running && result === null ? (
            <p role="status" className="font-mono text-[11px] tracking-widest text-dim uppercase">
              Measuring in your browser…
            </p>
          ) : null}
          {result === null ? null : <ResultCard result={result} target={target} targets={targets} cards={cards} />}
          {workerError === null ? null : (
            <p role="alert" className="mt-3 text-[12px] text-status-fail">
              {workerError}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
