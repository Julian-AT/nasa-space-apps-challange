'use client';

// The Lab slide's 3D twin: the Lab's TerrainTwins (ShadowAstra Atlas, MIT; real elevation and
// imagery tiles) as in LabShowcase, with the field-test rover driving the centre slice on a loop
// and drag-to-orbit left on. Loaded client-only from the deck, mounted one slide early.
import { useEffect, useMemo, useRef, useState } from 'react';

import TerrainTwins, { type FieldScene } from '@/components/shadowastra/lab/TerrainTwins';
import { TARGET_BY_ID } from '@/lib/shadowastra/data/sites';
import { useMotionReduced } from '@/lib/shadowastra/motionPref';
import { earthSite } from '@/lib/shadowastra/places';
import { lightTwin } from '@/lib/shadowastra/sun';
import { earthSun, sampleRoute, shadowMask, startHour, startMonth, targetSun } from '@/lib/shadowastra-lab/fieldtest';
import { DEM, earthVisible, IMAGERY, nativeKm, sampleImagery, sampleTerrain, type TerrainGrid } from '@/lib/shadowastra/terrain';

const FOOTPRINT_KM = 40;
const LOOP_SECONDS = 16;

interface Loaded {
  readonly e: TerrainGrid;
  readonly t: TerrainGrid;
  readonly de: HTMLCanvasElement | null;
  readonly dt: HTMLCanvasElement | null;
}

export default function LabTwin({ earth, target }: { readonly earth: string; readonly target: string }) {
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

  if (!data) {
    return (
      <div className="grid size-full place-items-center bg-[#0b0c0f] text-sm text-zinc-400">
        <div className="flex w-64 flex-col items-center gap-3">
          <span>{error ? 'Elevation tiles could not be loaded.' : `Streaming real elevation tiles · ${Math.round(progress * 100)}%`}</span>
          <span className="h-px w-full bg-zinc-800">
            <span className="block h-px bg-[#C8962E] transition-[width]" style={{ width: `${Math.max(4, progress * 100)}%` }} />
          </span>
        </div>
      </div>
    );
  }
  return (
    <div className="size-full" role="img" aria-label={`3D terrain: ${site?.name ?? 'Earth site'} beside ${tgt?.name ?? 'target'}, ${FOOTPRINT_KM} km blocks, drag to orbit`}>
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
    </div>
  );
}
