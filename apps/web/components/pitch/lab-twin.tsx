'use client';

// The Lab slide's 3D twin: the Lab's TerrainTwins (ShadowAstra Atlas, MIT; real elevation and
// imagery tiles) as in LabShowcase, with the field-test rover driving the centre slice on a loop
// and drag-to-orbit left on. Loaded client-only from the deck, mounted one slide early.
import { _roots } from '@react-three/fiber';
import { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';

import TerrainTwins, { type FieldScene } from '@/components/shadowastra/lab/TerrainTwins';
import { TARGET_BY_ID } from '@/lib/shadowastra/data/sites';
import { useMotionReduced } from '@/lib/shadowastra/motionPref';
import { earthSite } from '@/lib/shadowastra/places';
import { lightTwin } from '@/lib/shadowastra/sun';
import { earthSun, sampleRoute, shadowMask, startHour, startMonth, targetSun } from '@/lib/shadowastra-lab/fieldtest';
import type { RoutePoint } from '@/lib/shadowastra-lab/fieldtest';
import { DEM, earthVisible, IMAGERY, nativeKm, sampleImagery, sampleTerrain, type TerrainGrid } from '@/lib/shadowastra/terrain';

const FOOTPRINT_KM = 40;
const LOOP_SECONDS = 16;
const GOLD = '#C8962E';

interface Live {
  readonly km: number;
  readonly slope: number;
  readonly climb: number;
  readonly descent: number;
  readonly k: number;
}

/** Route statistics at progress p (0..1), read from the route points the rover drives. */
function liveAt(route: readonly RoutePoint[], p: number): Live | null {
  if (route.length < 2) return null;
  const k = Math.max(0, Math.min(route.length - 1, Math.round(p * (route.length - 1))));
  let climb = 0;
  let descent = 0;
  for (let q = 1; q <= k; q++) {
    const dh = (route[q]?.height ?? 0) - (route[q - 1]?.height ?? 0);
    if (dh > 0) climb += dh;
    else descent -= dh;
  }
  const pt = route[k];
  return pt === undefined ? null : { km: pt.km, slope: pt.slope, climb, descent, k };
}

function Spark({ route, k }: { readonly route: readonly RoutePoint[]; readonly k: number }) {
  const W = 220;
  const H = 44;
  const hs = route.map((r) => r.height);
  const lo = Math.min(...hs);
  const hi = Math.max(...hs);
  const y = (h: number) => H - 3 - ((h - lo) / Math.max(1e-6, hi - lo)) * (H - 6);
  const x = (q: number) => (q / (route.length - 1)) * W;
  const d = hs.map((h, q) => `${q ? 'L' : 'M'}${x(q).toFixed(1)},${y(h).toFixed(1)}`).join(' ');
  const done = hs.slice(0, k + 1).map((h, q) => `${q ? 'L' : 'M'}${x(q).toFixed(1)},${y(h).toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-11 w-full" aria-hidden>
      <path d={d} fill="none" stroke="#d4d4d8" strokeWidth={1.5} />
      <path d={done} fill="none" stroke={GOLD} strokeWidth={2.2} />
      <circle cx={x(k)} cy={y(hs[k] ?? lo)} r={3.5} fill={GOLD} />
    </svg>
  );
}

function Hud({ title, route, live }: { readonly title: string; readonly route: readonly RoutePoint[]; readonly live: Live | null }) {
  if (live === null) return null;
  const stat = (v: string, unit: string, label: string) => (
    <div className="flex flex-col">
      <span className="text-2xl font-semibold tabular-nums text-[#0F172A]">
        {v}
        <span className="ml-1 text-sm font-normal text-zinc-400">{unit}</span>
      </span>
      <span className="text-xs text-zinc-500">{label}</span>
    </div>
  );
  return (
    <div className="pointer-events-none flex w-[23rem] flex-col gap-3 rounded-xl border border-[#E2E8F0] bg-white/85 p-4 backdrop-blur">
      <span className="text-sm font-semibold text-zinc-900">{title}</span>
      <div className="grid grid-cols-4 gap-3">
        {stat(live.km.toFixed(1), 'km', 'driven')}
        {stat(live.slope.toFixed(1), 'deg', 'slope')}
        {stat(Math.round(live.climb).toString(), 'm', 'climbed')}
        {stat(Math.round(live.descent).toString(), 'm', 'descended')}
      </div>
      <Spark route={route} k={live.k} />
    </div>
  );
}

interface Loaded {
  readonly e: TerrainGrid;
  readonly t: TerrainGrid;
  readonly de: HTMLCanvasElement | null;
  readonly dt: HTMLCanvasElement | null;
}

export default function LabTwin({ earth, target, earthName, targetName }: { readonly earth: string; readonly target: string; readonly earthName: string; readonly targetName: string }) {
  const host = useRef<HTMLDivElement>(null);
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
      sampleImagery(earthVisible(lat), lat, lon, FOOTPRINT_KM, 1024).catch(() => null),
      sampleImagery(IMAGERY[tgt.body].visible, tgt.lat, tgt.lon, FOOTPRINT_KM, 1024).catch(() => null),
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

  // HUD: read the rover's progress a few times a second (the scene reads it every frame)
  useEffect(() => {
    if (!field) return;
    const id = window.setInterval(() => setP(run.current), 120);
    return () => window.clearInterval(id);
  }, [field]);

  // White stage: recolour the Lab scene's background, fog and floor through its R3F root.
  useEffect(() => {
    if (!data) return;
    const white = new THREE.Color('#ffffff');
    const id = window.setInterval(() => {
      const canvas = host.current?.querySelector('canvas');
      const root = canvas ? _roots.get(canvas) : undefined;
      if (!root) return;
      const { scene, gl } = root.store.getState();
      // a clear colour is not tone-mapped, so the stage stays paper white
      scene.background = null;
      gl.setClearColor(white, 1);
      if (scene.fog) (scene.fog as THREE.Fog).color.copy(white);
      scene.traverse((o) => {
        const m = o as THREE.Mesh;
        if (m.isMesh && m.geometry instanceof THREE.PlaneGeometry && m.geometry.parameters.width === 80) {
          const mat = m.material as THREE.MeshStandardMaterial;
          mat.color.set('#ffffff');
          mat.emissive.set('#ffffff');
          mat.emissiveIntensity = 0.92;
        }
      });
    }, 400);
    return () => window.clearInterval(id);
  }, [data]);

  const liveE = field ? liveAt(field.earth.route, p) : null;
  const liveT = field ? liveAt(field.target.route, p) : null;

  if (!data) {
    return (
      <div className="grid size-full place-items-center bg-white text-sm text-zinc-500">
        <div className="flex w-64 flex-col items-center gap-3">
          <span>{error ? 'Elevation tiles could not be loaded.' : `Streaming real elevation tiles · ${Math.round(progress * 100)}%`}</span>
          <span className="h-px w-full bg-zinc-200">
            <span className="block h-px bg-[#C8962E] transition-[width]" style={{ width: `${Math.max(4, progress * 100)}%` }} />
          </span>
        </div>
      </div>
    );
  }
  return (
    <div ref={host} className="relative size-full" role="img" aria-label={`3D terrain: ${earthName} beside ${targetName}, ${FOOTPRINT_KM} km blocks, drag to orbit`}>
      <TerrainTwins
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
      {field ? (
        <div className="pointer-events-none absolute inset-x-6 top-12 flex justify-between gap-6">
          <Hud title={`Earth, ${earthName}`} route={field.earth.route} live={liveE} />
          <Hud title={`Mars, ${targetName}`} route={field.target.route} live={liveT} />
        </div>
      ) : null}
    </div>
  );
}
