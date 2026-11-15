'use client';

// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The /find step flow (ShadowAstra's "Find new sites" console and stage), driven by Terraluna's
// live probe: 1 pick a target, 2 pick a point on Earth, 3 read the probe result. The URL holds
// ?target=&lat=&lon=, so a result link is shareable. All reading and maths run in the probe
// worker (workers/probe.worker.ts); this module only posts requests and renders the plain-data
// results. A newer request supersedes an older one.
import clsx from 'clsx';
import { ArrowLeft, ArrowRight, Crosshair, MapPin, RotateCcw } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import dynamic from 'next/dynamic';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

// Default point for the find and probe forms: Vienna, Austria.
const VIENNA = { lat: 48.2082, lon: 16.3738 } as const;

import type { TileView } from '../cards/card-visual.ts';
import type { EncoderSpec, ModelBackground } from '../../lib/probe/embedding.ts';
import { COP30_MODE } from '../../lib/probe/mode.ts';
import { type ProbeResult, type ProbeTargetFingerprint, isProbeResult } from '../../lib/probe/result.ts';
import { Chip, EASE, Seg, WorldDot } from './kit.tsx';
import { DEFAULT_EARTH_LAYER, EARTH_LAYERS, FALLBACK_EARTH_LAYER } from './layers.ts';
import { StepRail, type StepId, StepTitle } from './pieces.tsx';
import { type CardLink, type FindTarget, ResultCard } from './result-card.tsx';

const EarthMap = dynamic(() => import('./earth-map.tsx'), {
  ssr: false,
  loading: () => <StageNote text="Loading the Earth map…" />,
});

export interface FindTargetInfo extends FindTarget {
  thumb: TileView | null;
}

export interface FindExample {
  name: string;
  lat: number;
  lon: number;
  card_id: string;
}

export interface FindEncoder {
  spec: EncoderSpec;
  backgrounds: Record<string, ModelBackground | null>;
}

interface Props {
  fingerprints: (ProbeTargetFingerprint & { name: string })[];
  targetsReason: string | null;
  bundleVersion: string;
  encoder: FindEncoder | null;
  encoderReason: string | null;
  targets: FindTargetInfo[];
  examples: FindExample[];
  cards: CardLink[];
}

function parseCoordinate(value: string | null, limit: number): number | null {
  if (value === null || value.trim() === '') return null;
  const n = Number(value);
  return Number.isFinite(n) && Math.abs(n) <= limit ? n : null;
}

const coord = (v: number): string => v.toFixed(3);

function StageNote({ text }: Readonly<{ text: string }>) {
  return <div className="grid size-full place-items-center p-6 text-center font-mono text-[11px] tracking-[0.18em] text-dim uppercase">{text}</div>;
}

/** A target's bundle hillshade tile (Derived, 45 deg altitude / 315 deg azimuth). */
export function TargetThumb({ thumb, className, name }: Readonly<{ thumb: TileView | null; className?: string; name: string }>) {
  if (thumb === null) {
    return <div className={clsx('grid place-items-center bg-ink text-center font-mono text-[9px] tracking-wider text-dim uppercase', className)}>Tile unavailable</div>;
  }
  const { box, view, z } = thumb.pick;
  const pct = (n: number): string => `${String(n * 100)}%`;
  return (
    <div className={clsx('relative overflow-hidden bg-ink', className)} role="img" aria-label={`${name} hillshade from the release bundle`}>
      {thumb.tiles.map((t) => (
        <img
          key={`${String(t.x)}/${String(t.y)}`}
          src={t.src}
          alt=""
          aria-hidden="true"
          loading="lazy"
          decoding="async"
          className="absolute max-w-none"
          data-tile={`${String(z)}/${String(t.x)}/${String(t.y)}`}
          style={{ width: pct(1 / view.side), height: pct(1 / view.side), left: pct((t.x - view.x0) / view.side), top: pct((t.y - view.y0) / view.side) }}
        />
      ))}
      <span aria-hidden="true" className="absolute rounded-[2px] border border-flare shadow-[0_0_0_1px_rgba(0,0,0,0.6)]" style={{ left: pct(box.left), top: pct(box.top), width: pct(box.width), height: pct(box.height) }} />
    </div>
  );
}

export function FindClient({ fingerprints, targetsReason, bundleVersion, encoder, encoderReason, targets, examples, cards }: Readonly<Props>) {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const urlTarget = params.get('target');
  // Local state is the source of truth; the URL mirrors it (router.replace is asynchronous).
  const [targetId, setTargetId] = useState(() => (targets.some((t) => t.target_id === urlTarget) ? (urlTarget as string) : (targets[0]?.target_id ?? '')));
  const target = targets.find((t) => t.target_id === targetId);
  const lat = parseCoordinate(params.get('lat'), 90);
  const lon = parseCoordinate(params.get('lon'), 180);
  const invalid = (params.get('lat') !== null && lat === null) || (params.get('lon') !== null && lon === null);
  const hasPoint = lat !== null && lon !== null;

  const [step, setStep] = useState<StepId>(() => (hasPoint ? 'result' : urlTarget === null ? 'target' : 'place'));
  const [pending, setPending] = useState<{ lat: number; lon: number } | null>(hasPoint ? { lat, lon } : VIENNA);
  const [latText, setLatText] = useState(params.get('lat') ?? String(VIENNA.lat));
  const [lonText, setLonText] = useState(params.get('lon') ?? String(VIENNA.lon));
  const [layerId, setLayerId] = useState(DEFAULT_EARTH_LAYER);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [result, setResult] = useState<ProbeResult | null>(null);
  const [running, setRunning] = useState(false);
  const [workerError, setWorkerError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const requestedAt = useRef(0);
  const worker = useRef<Worker | null>(null);
  const [workerReady, setWorkerReady] = useState(false);
  const nextId = useRef(1);
  const latest = useRef(0);

  const writeUrl = useCallback(
    (next: { target?: string; lat?: number | null; lon?: number | null }) => {
      const q = new URLSearchParams();
      if (next.target !== undefined) setTargetId(next.target);
      q.set('target', next.target ?? targetId);
      const la = next.lat === undefined ? lat : next.lat;
      const lo = next.lon === undefined ? lon : next.lon;
      if (la !== null && lo !== null) {
        q.set('lat', coord(la));
        q.set('lon', coord(lo));
      }
      router.replace(`${pathname}?${q.toString()}`, { scroll: false });
    },
    [router, pathname, targetId, lat, lon],
  );

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
      setResult(data);
      setElapsed(performance.now() - requestedAt.current);
      setRunning(false);
    };
    w.onerror = (event: ErrorEvent) => {
      setWorkerError(`The probe worker failed: ${event.message || 'unknown error'}`);
      setRunning(false);
    };
    w.postMessage({ type: 'init', mode: COP30_MODE, targets: fingerprints, targetsReason, bundleVersion, encoder });
    worker.current = w;
    setWorkerReady(true);
    return () => {
      w.terminate();
      worker.current = null;
      setWorkerReady(false);
    };
  }, [fingerprints, targetsReason, bundleVersion, encoder]);

  // The probe runs for the point in the URL; the target only changes which distances lead.
  useEffect(() => {
    if (lat === null || lon === null || worker.current === null || !workerReady) return;
    const id = nextId.current++;
    latest.current = id;
    setRunning(true);
    setResult(null);
    setElapsed(null);
    requestedAt.current = performance.now();
    worker.current.postMessage({ type: 'probe', id, latDeg: lat, lonDeg: lon });
  }, [lat, lon, workerReady]);

  const pick = useCallback((la: number, lo: number) => {
    const p = { lat: Number(la.toFixed(4)), lon: Number((((lo + 540) % 360) - 180).toFixed(4)) };
    setPending(p);
    setLatText(coord(p.lat));
    setLonText(coord(p.lon));
  }, []);

  const runProbe = (p: { lat: number; lon: number }) => {
    setPending(p);
    setStep('result');
    writeUrl({ lat: p.lat, lon: p.lon });
  };

  const markers = useMemo(() => examples.map((e) => ({ id: e.card_id, lat: e.lat, lon: e.lon, label: e.name })), [examples]);
  const mapPin = pending ?? (hasPoint ? { lat, lon } : null);
  const layer = EARTH_LAYERS.find((l) => l.id === layerId);

  if (target === undefined) {
    return <p className="m-6 rounded-xl border border-dashed border-border p-6 text-muted-foreground">Unavailable: the release bundle lists no targets ({targetsReason ?? 'no reason recorded'}).</p>;
  }

  return (
    <section className="relative xl:h-[calc(100dvh-112px)] xl:min-h-[720px]" aria-label="Find Earth places for a target">
      {/* stage: the target hillshade in step 1, the Earth map after */}
      <div className="relative h-[48vh] min-h-[340px] overflow-hidden bg-void xl:absolute xl:inset-y-0 xl:right-0 xl:left-[492px] xl:h-auto">
        {step === 'target' ? (
          <div className="relative size-full">
            <TargetThumb thumb={target.thumb} name={target.name} className="absolute inset-0 opacity-90" />
            <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-void via-void/20 to-transparent" />
            <div className="absolute inset-x-0 bottom-0 p-5">
              <div className="flex items-center gap-2 text-[12px] text-chrome">
                <WorldDot world={target.body} /> {target.body === 'moon' ? 'Moon' : 'Mars'} · target window
              </div>
              <div className="mt-1 text-[22px] font-medium tracking-tight text-platinum">{target.name}</div>
              <div className="mt-1 text-[11px] text-dim">Derived · bundle hillshade (45° altitude, 315° azimuth); the outline marks the 5 km analysis window.</div>
            </div>
          </div>
        ) : (
          <>
            <EarthMap
              layerId={layerId}
              pin={mapPin}
              markers={markers}
              onPick={(la, lo) => {
                pick(la, lo);
                if (step === 'result') setStep('place');
              }}
              onLayerBlocked={(id) => {
                setBlocked(id);
                setLayerId(FALLBACK_EARTH_LAYER);
              }}
            />
            <div className="absolute top-3 left-3 z-[650] max-w-[calc(100%-4.5rem)]">
              <Seg
                label="Earth map layer"
                value={layerId}
                onChange={(id) => {
                  setBlocked(null);
                  setLayerId(id);
                }}
                options={EARTH_LAYERS.map((l) => ({ id: l.id, label: l.name, title: `${l.provider} · ${l.instrument}` }))}
                className="bg-void/80 backdrop-blur-xl"
              />
              {blocked === null ? null : (
                <p role="status" className="mt-2 max-w-[340px] rounded-xl border border-line-2 bg-void/85 px-3 py-2 text-[11.5px] text-chrome backdrop-blur">
                  {EARTH_LAYERS.find((l) => l.id === blocked)?.name ?? blocked}: Unavailable: its tiles did not load (host unreachable or refused). Showing the self-hosted Blue Marble.
                </p>
              )}
            </div>
            {layer === undefined ? null : (
              <div className="pointer-events-none absolute bottom-6 left-3 z-[650] rounded-full border border-line bg-void/75 px-3 py-1 font-mono text-[10px] tracking-wider text-chrome uppercase backdrop-blur">
                {layer.provider} · {layer.instrument}
              </div>
            )}
          </>
        )}
      </div>

      {/* the console */}
      <aside className="relative z-20 flex flex-col border-line bg-panel xl:absolute xl:top-3 xl:bottom-3 xl:left-3 xl:w-[476px] xl:rounded-3xl xl:border xl:shadow-[0_40px_120px_-40px_rgba(0,0,0,0.8)]" aria-label="Find console">
        <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
          <StepRail step={step} onStep={setStep} can={{ target: true, place: true, result: hasPoint }} />
          <button
            type="button"
            onClick={() => {
              setPending(null);
              setLatText('');
              setLonText('');
              setResult(null);
              setStep('target');
              router.replace(pathname, { scroll: false });
            }}
            title="Start over"
            aria-label="Start over"
            className="grid size-8 shrink-0 place-items-center rounded-full border border-line-2 text-chrome hover:text-platinum focus-visible:outline-2 focus-visible:outline-ring"
          >
            <RotateCcw size={13} aria-hidden="true" />
          </button>
        </div>
        <div className="scrollbar-thin flex-1 overflow-y-auto">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={step} initial={{ opacity: 0, x: 18 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -18 }} transition={{ duration: 0.3, ease: EASE }} className="p-5">
              {step === 'target' ? (
                <div>
                  <StepTitle kicker="Step 1 of 3" title="Which world are you matching?" text="Pick a target window. Its terrain fingerprint comes from the release bundle." />
                  <ul className="mt-5 flex flex-col gap-1.5">
                    {targets.map((t) => (
                      <li key={t.target_id}>
                        <button
                          type="button"
                          aria-pressed={t.target_id === targetId}
                          onClick={() => {
                            writeUrl({ target: t.target_id });
                          }}
                          className={clsx(
                            'flex w-full items-center gap-3 rounded-xl border p-2 text-left transition-colors focus-visible:outline-2 focus-visible:outline-ring',
                            t.target_id === targetId ? 'border-flare/50 bg-flare/[0.07]' : 'border-transparent hover:border-line-2 hover:bg-platinum/[0.03]',
                          )}
                        >
                          <TargetThumb thumb={t.thumb} name={t.name} className="size-12 shrink-0 rounded-lg" />
                          <span className="min-w-0 flex-1">
                            <span className={clsx('flex items-center gap-2 truncate text-[13.5px]', t.target_id === targetId ? 'text-platinum' : 'text-chrome')}>
                              <WorldDot world={t.body} /> {t.name}
                            </span>
                            <span className="block truncate font-mono text-[10.5px] text-dim">{t.body === 'moon' ? 'Moon' : 'Mars'} · {t.target_id}</span>
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                  {targetsReason === null ? null : <p className="mt-3 text-[12px] text-dim">Some targets are Unavailable: {targetsReason}.</p>}
                  <button
                    type="button"
                    onClick={() => {
                      writeUrl({ target: targetId });
                      setStep('place');
                    }}
                    className="btn-primary mt-6 inline-flex items-center gap-2 rounded-full px-6 py-3 text-[14px] font-medium"
                  >
                    Pick a place on Earth <ArrowRight size={15} aria-hidden="true" />
                  </button>
                </div>
              ) : null}

              {step === 'place' ? (
                <div>
                  <StepTitle kicker="Step 2 of 3" title="Where on Earth?" text={`Click the map to drop a pin, or start from an evidence-card window. The probe reads a 5 km window around the pin and compares it with ${target.name}.`} />
                  <div className="mt-5 flex flex-col gap-2">
                    <h3 className="font-mono text-[10px] tracking-[0.22em] text-dim uppercase">Use an example (card windows)</h3>
                    <div className="flex flex-wrap gap-1.5">
                      {examples.map((e) => (
                        <button
                          key={e.card_id}
                          type="button"
                          onClick={() => {
                            pick(e.lat, e.lon);
                          }}
                          className="rounded-full border border-line-2 px-3 py-1.5 text-left text-[12px] text-chrome hover:border-chrome hover:text-platinum focus-visible:outline-2 focus-visible:outline-ring"
                        >
                          {e.name} <span className="font-mono text-dim">{coord(e.lat)}, {coord(e.lon)}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                  <form
                    className="mt-5 grid grid-cols-2 gap-3"
                    onSubmit={(event) => {
                      event.preventDefault();
                      const la = parseCoordinate(latText, 90);
                      const lo = parseCoordinate(lonText, 180);
                      if (la === null || lo === null) return;
                      runProbe({ lat: la, lon: lo });
                    }}
                  >
                    <label className="flex min-w-0 flex-col gap-1 text-[12px] text-chrome">
                      Latitude (°N)
                      <input
                        className="min-w-0 rounded-xl border border-line-2 bg-ink px-3 py-2 font-mono text-[13px] text-platinum"
                        inputMode="decimal"
                        name="lat"
                        required
                        value={latText}
                        onChange={(e) => {
                          setLatText(e.target.value);
                        }}
                      />
                    </label>
                    <label className="flex min-w-0 flex-col gap-1 text-[12px] text-chrome">
                      Longitude (°E)
                      <input
                        className="min-w-0 rounded-xl border border-line-2 bg-ink px-3 py-2 font-mono text-[13px] text-platinum"
                        inputMode="decimal"
                        name="lon"
                        required
                        value={lonText}
                        onChange={(e) => {
                          setLonText(e.target.value);
                        }}
                      />
                    </label>
                    {parseCoordinate(latText, 90) === null && latText !== '' ? <p role="alert" className="col-span-2 text-[12px] text-status-fail">Latitude must lie in [-90, 90].</p> : null}
                    {parseCoordinate(lonText, 180) === null && lonText !== '' ? <p role="alert" className="col-span-2 text-[12px] text-status-fail">Longitude must lie in [-180, 180].</p> : null}
                    <div className="col-span-2 flex flex-wrap items-center gap-3">
                      <button type="button" onClick={() => { setStep('target'); }} className="inline-flex items-center gap-1.5 rounded-full border border-line-2 px-4 py-2.5 text-[13px] text-chrome hover:text-platinum focus-visible:outline-2 focus-visible:outline-ring">
                        <ArrowLeft size={14} aria-hidden="true" /> Target
                      </button>
                      <button type="submit" className="btn-primary inline-flex items-center gap-2 rounded-full px-6 py-2.5 text-[14px] font-medium">
                        <Crosshair size={15} aria-hidden="true" /> Run the probe
                      </button>
                    </div>
                  </form>
                  <p className="mt-4 flex items-start gap-2 text-[11.5px] text-dim">
                    <MapPin size={13} className="mt-0.5 shrink-0" aria-hidden="true" /> Coordinates only. Copernicus GLO-30 is read through the DEM proxy and measured in a worker in your browser; nothing is stored or ranked.
                  </p>
                </div>
              ) : null}

              {step === 'result' ? (
                <div>
                  <StepTitle kicker="Step 3 of 3" title={`${target.name} on Earth?`} text={hasPoint ? `Live probe at ${coord(lat)}°, ${coord(lon)}°.` : 'Pick a place first.'} />
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    <Chip>Mode: live probe · GLO-30 {COP30_MODE}</Chip>
                    <Chip>Bundle {bundleVersion}</Chip>
                    <Chip tone="predicted">{encoder === null ? `Encoder Unavailable: ${encoderReason ?? 'no reason recorded'}` : `Encoder EXPERIMENTAL · G5 ${encoder.spec.g5_status}`}</Chip>
                  </div>
                  <div aria-live="polite" className="mt-5">
                    {running ? (
                      <div role="status" className="rounded-2xl border border-line p-4">
                        <div className="shimmer h-2 rounded-full" />
                        <p className="mt-3 text-[13px] text-chrome">Reading the 5 km window and measuring it in your browser…</p>
                      </div>
                    ) : null}
                    {result === null ? null : <ResultCard result={result} target={target} targets={targets} cards={cards} />}
                    {elapsed === null ? null : <p className="mt-2 font-mono text-[10.5px] text-dim" data-request-to-result-ms={Math.round(elapsed)}>Result {Math.round(elapsed)} ms after the request.</p>}
                  </div>
                  <button type="button" onClick={() => { setStep('place'); }} className="mt-5 inline-flex items-center gap-1.5 rounded-full border border-line-2 px-4 py-2.5 text-[13px] text-chrome hover:text-platinum focus-visible:outline-2 focus-visible:outline-ring">
                    <ArrowLeft size={14} aria-hidden="true" /> Pick another place
                  </button>
                </div>
              ) : null}
              {invalid ? <p role="alert" className="mt-4 text-[12px] text-status-fail">The link&apos;s coordinates are out of range: latitude must lie in [-90, 90] and longitude in [-180, 180].</p> : null}
              {workerError === null ? null : <p role="alert" className="mt-4 text-[12px] text-status-fail">{workerError}</p>}
            </motion.div>
          </AnimatePresence>
        </div>
      </aside>
    </section>
  );
}
