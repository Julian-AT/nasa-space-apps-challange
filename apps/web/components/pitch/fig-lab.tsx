'use client';

// Figure 8 of the pitch deck: the Lab's terrain twins (real Terrarium and Trek elevation and imagery
// tiles) on an off-white stage, the same rover route driven on both blocks, and a figure-style side
// panel with the route statistics read from the route points the rover drives. Same props as LabTwin.
import dynamic from 'next/dynamic';
import { useEffect, useMemo, useRef, useState } from 'react';

import type { FieldScene } from '@/components/pitch/lab/light-twins';
import { TARGET_BY_ID } from '@/lib/shadowastra/data/sites';
import { useMotionReduced } from '@/lib/shadowastra/motionPref';
import { earthSite } from '@/lib/shadowastra/places';
import { lightTwin } from '@/lib/shadowastra/sun';
import { earthSun, sampleRoute, shadowMask, startHour, startMonth, targetSun } from '@/lib/shadowastra-lab/fieldtest';
import type { RoutePoint } from '@/lib/shadowastra-lab/fieldtest';
import { DEM, earthVisible, IMAGERY, nativeKm, sampleImagery, sampleTerrain, type TerrainGrid } from '@/lib/shadowastra/terrain';

const LightTwins = dynamic(() => import('@/components/pitch/lab/light-twins'), { ssr: false, loading: () => <div className="size-full" /> });

const FOOTPRINT_KM = 40;
const LOOP_SECONDS = 18;
const INK = '#1A1613';
const MUTED = '#6B645C';
const EARTH = '#4A8044';
const MARS = '#CC5800';
const BORDER = 'rgba(26,22,19,0.10)';

interface Live {
  readonly km: number;
  readonly slope: number;
  readonly climb: number;
  readonly descent: number;
  readonly f: number;
}

/** Route statistics at progress p (0..1), interpolated between the route points the rover drives. */
function liveAt(route: readonly RoutePoint[], p: number): Live | null {
  if (route.length < 2) return null;
  const last = route.length - 1;
  const f = Math.max(0, Math.min(last, p * last));
  const k = Math.floor(f);
  let climb = 0;
  let descent = 0;
  for (let q = 1; q <= k; q++) {
    const dh = (route[q]?.height ?? 0) - (route[q - 1]?.height ?? 0);
    if (dh > 0) climb += dh;
    else descent -= dh;
  }
  const a = route[k];
  const b = route[Math.min(last, k + 1)];
  if (a === undefined || b === undefined) return null;
  const t = f - k;
  const dh = (b.height - a.height) * t;
  if (dh > 0) climb += dh;
  else descent -= dh;
  return { km: a.km + (b.km - a.km) * t, slope: a.slope + (b.slope - a.slope) * t, climb, descent, f };
}

function Profile({ route, f, color }: { readonly route: readonly RoutePoint[]; readonly f: number; readonly color: string }) {
  const W = 260;
  const H = 40;
  const hs = route.map((r) => r.height);
  const lo = Math.min(...hs);
  const hi = Math.max(...hs);
  const y = (h: number) => H - 4 - ((h - lo) / Math.max(1e-6, hi - lo)) * (H - 8);
  const x = (q: number) => (q / (route.length - 1)) * W;
  const d = hs.map((h, q) => `${q ? 'L' : 'M'}${x(q).toFixed(1)},${y(h).toFixed(1)}`).join(' ');
  const k = Math.floor(f);
  const h0 = hs[k] ?? lo;
  const h1 = hs[Math.min(hs.length - 1, k + 1)] ?? h0;
  const hd = h0 + (h1 - h0) * (f - k);
  return (
    <div className="flex flex-col gap-1">
      <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-10 w-full overflow-visible" preserveAspectRatio="none" aria-hidden>
        <line x1={0} x2={W} y1={H - 0.5} y2={H - 0.5} stroke={BORDER} strokeWidth={1} vectorEffect="non-scaling-stroke" />
        <path d={d} fill="none" stroke={color} strokeOpacity={0.3} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
        <path d={d} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" pathLength={1} strokeDasharray={`${f / (route.length - 1)} 1`} />
      </svg>
        <span className="absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-[#F9F8F5]" style={{ background: color, left: `${(f / (route.length - 1)) * 100}%`, top: `${(y(hd) / H) * 100}%` }} />
      </div>
      <div className="flex justify-between font-mono text-[10px] text-[#6B645C]">
        <span>0 km</span>
        <span>
          {Math.round(lo)} to {Math.round(hi)} m
        </span>
        <span>{(route[route.length - 1]?.km ?? 0).toFixed(0)} km</span>
      </div>
    </div>
  );
}

function Terrain({ body, name, color, route, live }: { readonly body: string; readonly name: string; readonly color: string; readonly route: readonly RoutePoint[]; readonly live: Live | null }) {
  const stat = (v: string, unit: string, label: string) => (
    <div className="flex flex-col gap-0.5">
      <span className="font-mono text-[22px] leading-none tabular-nums" style={{ color: INK }}>
        {v}
        <span className="ml-1 text-[11px]" style={{ color: MUTED }}>
          {unit}
        </span>
      </span>
      <span className="text-[12px] font-normal" style={{ color: MUTED }}>
        {label}
      </span>
    </div>
  );
  return (
    <section className="flex flex-col gap-3">
      <header className="flex items-center gap-2">
        <span className="size-2 rounded-full" style={{ background: color }} />
        <span className="text-[13px] font-normal" style={{ color: INK }}>
          {body}, {name}
        </span>
      </header>
      {live === null ? (
        <span className="text-[12px]" style={{ color: MUTED }}>
          Route unavailable
        </span>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            {stat(live.km.toFixed(1), 'km', 'distance driven')}
            {stat(live.slope.toFixed(1), 'deg', 'slope')}
            {stat(Math.round(live.climb).toString(), 'm', 'climbed')}
            {stat(Math.round(live.descent).toString(), 'm', 'descended')}
          </div>
          <Profile route={route} f={live.f} color={color} />
        </>
      )}
    </section>
  );
}

interface Loaded {
  readonly e: TerrainGrid;
  readonly t: TerrainGrid;
  readonly de: HTMLCanvasElement | null;
  readonly dt: HTMLCanvasElement | null;
}

export interface FigLabProps {
  readonly earth: string;
  readonly target: string;
  readonly earthName: string;
  readonly targetName: string;
}

export default function FigLab({ earth, target, earthName, targetName }: FigLabProps) {
  const [p, setP] = useState(0);
  const site = earthSite(earth);
  const tgt = TARGET_BY_ID[target];
  const reduce = useMotionReduced();
  const [data, setData] = useState<Loaded | null>(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState(false);
  const lat = site?.lat ?? 0;
  const lon = site?.lon ?? 0;

  useEffect(() => {
    if (!tgt || !site) return;
    let alive = true;
    const spacing = Math.max(nativeKm(DEM[tgt.body], DEM[tgt.body].maxZoom, 0), FOOTPRINT_KM / 128);
    const prog = { a: 0, b: 0 };
    const tick = () => alive && setProgress((prog.a + prog.b) / 2);
    Promise.all([
      sampleTerrain({ body: 'earth', lat, lon, footprintKm: FOOTPRINT_KM, spacingKm: spacing, onProgress: (d: number, n: number) => ((prog.a = d / n), tick()) }),
      sampleTerrain({ body: tgt.body, lat: tgt.lat, lon: tgt.lon, footprintKm: FOOTPRINT_KM, spacingKm: spacing, onProgress: (d: number, n: number) => ((prog.b = d / n), tick()) }),
      sampleImagery(earthVisible(lat), lat, lon, FOOTPRINT_KM, 2048).catch(() => null),
      sampleImagery(IMAGERY[tgt.body].visible, tgt.lat, tgt.lon, FOOTPRINT_KM, 2048).catch(() => null),
    ])
      .then(([e, t, de, dt]) => alive && setData({ e, t, de, dt }))
      .catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
    // site is derived from `earth`; lat/lon carry it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lat, lon, tgt]);

  const run = useRef(0);
  const field = useMemo<FieldScene | null>(() => {
    if (!data || !tgt) return null;
    const month = startMonth(lat, lightTwin(lat, tgt.body, tgt.lat).months.map((m: { score: number }) => m.score));
    const hour = startHour(lat, tgt.body, tgt.lat, month);
    const shadeE = shadowMask(data.e, earthSun(lat, month, hour));
    const shadeT = shadowMask(data.t, targetSun(tgt.body, tgt.lat, month, hour));
    return {
      progress: run,
      mode: 'rover',
      earth: { route: sampleRoute(data.e, shadeE, 15), shade: shadeE },
      target: { route: sampleRoute(data.t, shadeT, 15), shade: shadeT },
    };
  }, [data, lat, tgt]);

  useEffect(() => {
    if (!field) return;
    if (reduce) {
      run.current = 0.5;
      return;
    }
    let raf = 0;
    let last = performance.now();
    const loop = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      run.current = (run.current + dt / LOOP_SECONDS) % 1;
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [field, reduce]);

  // panel: read the rover's progress a few times a second (the scene reads it every frame)
  useEffect(() => {
    if (!field) return;
    const id = window.setInterval(() => setP(run.current), 100);
    return () => window.clearInterval(id);
  }, [field]);

  const liveE = field ? liveAt(field.earth.route, p) : null;
  const liveT = field ? liveAt(field.target.route, p) : null;

  return (
    <figure className="flex size-full min-h-0 flex-col gap-4 bg-[#F9F8F5] font-sans" style={{ color: INK }}>
      <div className="flex min-h-0 flex-1 gap-5">
        <div className="relative min-h-0 min-w-0 flex-1" role="img" aria-label={`3D terrain: ${earthName} beside ${targetName}, ${FOOTPRINT_KM} km blocks, the same rover route driven on both, drag to orbit`}>
          {data ? (
            <>
              <LightTwins
                earth={data.e}
                target={data.t}
                drapeEarth={data.de}
                drapeTarget={data.dt}
                litEarth={null}
                litTarget={null}
                footprintKm={FOOTPRINT_KM}
                sunElev={50}
                sunAz={135}
                playing={false}
                exaggeration={3}
                overlay="imagery"
                field={field}
              />
              <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-around text-[12px]" style={{ color: MUTED }}>
                <span>Earth, {earthName}</span>
                <span>Mars, {targetName}</span>
              </div>
            </>
          ) : (
            <div className="grid size-full place-items-center text-[13px]" style={{ color: MUTED }}>
              <div className="flex w-64 flex-col items-center gap-3">
                <span>{error ? 'Elevation tiles could not be loaded.' : `Streaming real elevation tiles, ${Math.round(progress * 100)}%`}</span>
                <span className="h-px w-full" style={{ background: BORDER }}>
                  <span className="block h-px transition-[width]" style={{ background: INK, width: `${Math.max(4, progress * 100)}%` }} />
                </span>
              </div>
            </div>
          )}
        </div>
        <aside className="flex w-[280px] shrink-0 flex-col gap-5 self-center py-2">
          <span className="font-mono text-[11px] uppercase tracking-[0.08em]" style={{ color: MUTED }}>
            Rover route, {FOOTPRINT_KM} km W to E
          </span>
          <Terrain body="Earth" name={earthName} color={EARTH} route={field?.earth.route ?? []} live={liveE} />
          <span className="h-px w-full" style={{ background: BORDER }} />
          <Terrain body="Mars" name={targetName} color={MARS} route={field?.target.route ?? []} live={liveT} />
          <span className="text-[11px] leading-snug" style={{ color: MUTED }}>
            Relief exaggerated 3x. Statistics read from the elevation samples along the drawn route.
          </span>
        </aside>
      </div>
      <figcaption className="text-[13px] leading-snug" style={{ color: MUTED }}>
        <span style={{ color: INK }}>Figure 8.</span> Same rover route on real elevation: Holuhraun (Earth) and Jezero crater (Mars), 40 km blocks.
      </figcaption>
    </figure>
  );
}

export { FigLab };
