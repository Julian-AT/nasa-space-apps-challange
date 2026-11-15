'use client';

// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The /probe form and worker host. lat and lon live in the URL query, so a probe link is
// shareable and round-trips. The worker (workers/probe.worker.ts) does all reading and maths;
// this module only renders its plain-data results. A newer request supersedes an older one.
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useMemo, useRef, useState } from 'react';

import type { EncoderSpec, ModelBackground } from '../../lib/probe/embedding.ts';
import { COP30_MODE } from '../../lib/probe/mode.ts';
import { type ProbeResult, type ProbeTargetFingerprint, isProbeResult } from '../../lib/probe/result.ts';
import { ProbeCard } from './probe-card.tsx';

type Target = ProbeTargetFingerprint & { name: string };
export interface ProbeEncoder {
  spec: EncoderSpec;
  backgrounds: Record<string, ModelBackground | null>;
}

// Starting points only: coordinates the visitor may probe, not results.
const EXAMPLES = [
  { name: 'Holuhraun, Iceland', lat: '64.885', lon: '-16.722' },
  { name: 'Rosamond Dry Lake, USA', lat: '34.87', lon: '-117.9' },
] as const;

function parseCoordinate(value: string | null, limit: number): number | null {
  if (value === null || value.trim() === '') return null;
  const n = Number(value);
  return Number.isFinite(n) && Math.abs(n) <= limit ? n : null;
}

export function ProbeClient({
  targets,
  targetsReason,
  bundleVersion,
  encoder,
}: Readonly<{ targets: Target[]; targetsReason: string | null; bundleVersion: string; encoder: ProbeEncoder | null }>) {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const lat = parseCoordinate(params.get('lat'), 90);
  const lon = parseCoordinate(params.get('lon'), 180);
  const [latText, setLatText] = useState(params.get('lat') ?? '');
  const [lonText, setLonText] = useState(params.get('lon') ?? '');
  const [result, setResult] = useState<ProbeResult | null>(null);
  const [running, setRunning] = useState(false);
  const [workerError, setWorkerError] = useState<string | null>(null);
  // Wall-clock evidence measured here: request to result, and page start to result.
  const [shown, setShown] = useState<{ requestToResultMs: number; pageToResultMs: number; probeIndex: number } | null>(null);
  const requestedAt = useRef(0);
  const probeCount = useRef(0);
  const worker = useRef<Worker | null>(null);
  const nextId = useRef(1);
  const latest = useRef(0);
  const names = useMemo(() => Object.fromEntries(targets.map((t) => [t.target_id, t.name])), [targets]);

  useEffect(() => {
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
      const t = performance.now();
      setResult(data);
      setShown({ requestToResultMs: t - requestedAt.current, pageToResultMs: t, probeIndex: probeCount.current });
      setRunning(false);
    };
    w.onerror = (event: ErrorEvent) => {
      setWorkerError(`The probe worker failed: ${event.message || 'unknown error'}`);
      setRunning(false);
    };
    w.postMessage({ type: 'init', mode: COP30_MODE, targets, targetsReason, bundleVersion, encoder });
    worker.current = w;
    return () => {
      w.terminate();
      worker.current = null;
    };
  }, [targets, targetsReason, bundleVersion, encoder]);

  useEffect(() => {
    if (lat === null || lon === null || worker.current === null) return;
    const id = nextId.current++;
    latest.current = id;
    setRunning(true);
    setResult(null);
    setShown(null);
    probeCount.current += 1;
    requestedAt.current = performance.now();
    worker.current.postMessage({ type: 'probe', id, latDeg: lat, lonDeg: lon });
  }, [lat, lon]);

  const invalid = (params.get('lat') !== null && lat === null) || (params.get('lon') !== null && lon === null);

  return (
    <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-[20rem_minmax(0,1fr)]">
      <aside aria-label="Probe input" className="flex flex-col gap-4 self-start rounded-3xl border border-line-2 bg-panel/95 p-4">
        <h2 className="font-mono text-[0.7rem] font-semibold tracking-[0.2em] text-dim uppercase">Point on Earth</h2>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            const q = new URLSearchParams({ lat: latText.trim(), lon: lonText.trim() });
            router.replace(`${pathname}?${q.toString()}`);
          }}
        >
          <label className="flex flex-col gap-1 text-sm text-muted-foreground">
            Latitude (°N)
            <input
              className="rounded-xl border border-line-2 bg-ink px-3 py-2 font-mono text-foreground"
              inputMode="decimal"
              name="lat"
              required
              value={latText}
              onChange={(e) => {
                setLatText(e.target.value);
              }}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-muted-foreground">
            Longitude (°E)
            <input
              className="rounded-xl border border-line-2 bg-ink px-3 py-2 font-mono text-foreground"
              inputMode="decimal"
              name="lon"
              required
              value={lonText}
              onChange={(e) => {
                setLonText(e.target.value);
              }}
            />
          </label>
          <button type="submit" className="rounded-full bg-flare px-5 py-2 text-sm font-semibold text-void hover:opacity-90 focus-visible:outline-2 focus-visible:outline-ring">
            {running ? 'Measuring…' : 'Run probe'}
          </button>
        </form>
        <div className="flex flex-col gap-2 border-t border-line pt-4">
          <h3 className="font-mono text-[0.7rem] font-semibold tracking-[0.2em] text-dim uppercase">Example points</h3>
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((e) => (
              <button
                key={e.name}
                type="button"
                className="rounded-full border border-line-2 px-3 py-1.5 text-[12px] text-chrome hover:border-chrome focus-visible:outline-2 focus-visible:outline-ring"
                onClick={() => {
                  setLatText(e.lat);
                  setLonText(e.lon);
                  router.replace(`${pathname}?${new URLSearchParams({ lat: e.lat, lon: e.lon }).toString()}`);
                }}
              >
                {e.name}
              </button>
            ))}
          </div>
          <p className="text-[12px] text-dim">Coordinates only; results are computed live in your browser.</p>
        </div>
        {invalid ? <p role="alert">Latitude must lie in [-90, 90] and longitude in [-180, 180].</p> : null}
        {workerError === null ? null : <p role="alert">{workerError}</p>}
      </aside>
      <div aria-live="polite" className="min-w-0 rounded-3xl border border-line-2 bg-panel/95 p-4">
        {result === null && !running ? <p className="text-sm text-dim">Run a probe to see relief, texture distances and curves here.</p> : null}
        {running ? <p>Reading the window and measuring it in your browser…</p> : null}
        {result === null ? null : <ProbeCard result={result} names={names} />}
        {shown === null ? null : (
          <p
            className="mt-2 text-sm tabular-nums"
            data-probe-index={shown.probeIndex}
            data-request-to-result-ms={Math.round(shown.requestToResultMs)}
            data-page-to-result-ms={Math.round(shown.pageToResultMs)}
          >
            Probe {shown.probeIndex} on this page: result {Math.round(shown.requestToResultMs)} ms after the request
            {shown.probeIndex === 1 ? `, ${String(Math.round(shown.pageToResultMs))} ms after the page started loading` : ''}.
          </p>
        )}
      </div>
    </div>
  );
}
