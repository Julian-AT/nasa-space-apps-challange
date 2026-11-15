'use client';

// The Lab slide's 3D twin: the Lab's TerrainTwins (real elevation tiles, imagery drapes) with a
// rover driving the centre route on both slabs in sync. Loaded client-only from the deck.
import { useEffect, useMemo, useRef, useState } from 'react';

import TerrainTwins, { type FieldScene } from '@/components/shadowastra/lab/TerrainTwins';
import { earthSun, sampleRoute, shadowMask, targetSun } from '@/lib/shadowastra-lab/fieldtest';
import { DEM, earthVisible, IMAGERY, nativeKm, sampleImagery, sampleTerrain, type TerrainGrid } from '@/lib/shadowastra/terrain';

import type { LonLat } from './pitch-data';

const FOOTPRINT_KM = 80;
const LAP_S = 16;

interface Props {
  readonly earth: LonLat;
  readonly target: LonLat;
  readonly body: 'moon' | 'mars';
}

export default function LabTwin({ earth, target, body }: Props) {
  const [grids, setGrids] = useState<{ e: TerrainGrid; t: TerrainGrid } | null>(null);
  const [drapes, setDrapes] = useState<{ e: HTMLCanvasElement | null; t: HTMLCanvasElement | null }>({ e: null, t: null });
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const run = useRef({ current: 0 });

  useEffect(() => {
    let alive = true;
    const spacing = Math.max(nativeKm(DEM[body], DEM[body].maxZoom, 0), FOOTPRINT_KM / 128);
    const prog = { a: 0, b: 0 };
    const tick = () => alive && setProgress((prog.a + prog.b) / 2);
    Promise.all([
      sampleTerrain({ body: 'earth', lat: earth.lat, lon: earth.lon, footprintKm: FOOTPRINT_KM, spacingKm: spacing, onProgress: (d: number, n: number) => ((prog.a = d / n), tick()) }),
      sampleTerrain({ body, lat: target.lat, lon: target.lon, footprintKm: FOOTPRINT_KM, spacingKm: spacing, onProgress: (d: number, n: number) => ((prog.b = d / n), tick()) }),
    ])
      .then(([e, t]) => alive && setGrids({ e, t }))
      .catch(() => alive && setError('Elevation tiles could not be loaded.'));
    sampleImagery(earthVisible(earth.lat), earth.lat, earth.lon, FOOTPRINT_KM, 512)
      .then((c: HTMLCanvasElement) => alive && setDrapes((d) => ({ ...d, e: c })))
      .catch(() => {});
    sampleImagery(IMAGERY[body].visible, target.lat, target.lon, FOOTPRINT_KM, 512)
      .then((c: HTMLCanvasElement) => alive && setDrapes((d) => ({ ...d, t: c })))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [earth.lat, earth.lon, target.lat, target.lon, body]);

  const field = useMemo<FieldScene | null>(() => {
    if (grids === null) return null;
    const month = 6;
    const hour = 15;
    const shadeE = shadowMask(grids.e, earthSun(earth.lat, month, hour));
    const shadeT = shadowMask(grids.t, targetSun(body, target.lat, month, hour));
    return {
      progress: run.current,
      mode: 'rover',
      earth: { route: sampleRoute(grids.e, shadeE, 15), shade: shadeE },
      target: { route: sampleRoute(grids.t, shadeT, 15), shade: shadeT },
    };
  }, [grids, earth.lat, target.lat, body]);

  // the rover drives the route back and forth, read every frame by the scene
  useEffect(() => {
    if (field === null) return;
    let raf = 0;
    const start = performance.now();
    const loop = (now: number) => {
      const t = ((now - start) / 1000 / LAP_S) % 2;
      const p = t < 1 ? t : 2 - t;
      run.current.current = p * p * (3 - 2 * p);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [field]);

  if (grids === null) {
    return (
      <div className="grid h-full place-items-center bg-[#0b0c0f] text-sm text-zinc-400">
        <div className="flex w-56 flex-col items-center gap-3">
          <span>{error ?? `Streaming real elevation tiles · ${Math.round(progress * 100)}%`}</span>
          <span className="h-px w-full bg-zinc-800">
            <span className="block h-px bg-[var(--primary)] transition-[width]" style={{ width: `${Math.max(4, progress * 100)}%` }} />
          </span>
        </div>
      </div>
    );
  }
  return (
    <TerrainTwins
      earth={grids.e}
      target={grids.t}
      drapeEarth={drapes.e}
      drapeTarget={drapes.t}
      litEarth={null}
      litTarget={null}
      footprintKm={FOOTPRINT_KM}
      sunElev={50}
      sunAz={225}
      playing={false}
      exaggeration={4}
      overlay="imagery"
      field={field}
    />
  );
}
