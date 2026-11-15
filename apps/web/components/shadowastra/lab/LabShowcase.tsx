'use client';

// A chrome-free 3D terrain twin for slides and hero panels: the Lab's Earth and Moon/Mars blocks
// (ShadowAstra Atlas, MIT, Team ShadowAstra; see THIRD_PARTY_NOTICES.md) with the field-test rover
// driving the centre slice on a loop. Real elevation and imagery tiles, no scores, no controls.
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';

import { TARGET_BY_ID } from '@/lib/shadowastra/data/sites';
import { earthSite } from '@/lib/shadowastra/places';
import { DEM, earthVisible, IMAGERY, nativeKm, sampleImagery, sampleTerrain, type TerrainGrid } from '@/lib/shadowastra/terrain';
import { lightTwin } from '@/lib/shadowastra/sun';
import { earthSun, sampleRoute, shadowMask, startHour, startMonth, targetSun } from '@/lib/shadowastra-lab/fieldtest';
import { useMotionReduced } from '@/lib/shadowastra/motionPref';

import type { FieldScene } from './TerrainTwins.tsx';

const TerrainTwins = lazy(() => import('./TerrainTwins.tsx'));

const FOOTPRINT_KM = 40;
/** Seconds for one crossing of the 40 km slice. */
const LOOP_SECONDS = 16;

interface Loaded {
  earth: TerrainGrid;
  target: TerrainGrid;
  drapeEarth: HTMLCanvasElement | null;
  drapeTarget: HTMLCanvasElement | null;
}

export interface LabShowcaseProps {
  /** Earth analogue site id (see the Lab's site list); defaults to the Dry Valleys. */
  earth?: string;
  /** Moon or Mars target id; defaults to Shackleton crater. */
  target?: string;
  /** Drive the rover along the slice on a loop (default true; off under reduced motion). */
  autoDrive?: boolean;
  className?: string;
}

export function LabShowcase({ earth = 'dry-valleys', target = 'shackleton', autoDrive = true, className }: LabShowcaseProps) {
  const site = earthSite(earth) ?? earthSite('dry-valleys');
  const tgt = TARGET_BY_ID[target] ?? TARGET_BY_ID['shackleton'];
  const reduce = useMotionReduced();
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState(false);

  const lat = site?.lat ?? 0;
  const lon = site?.lon ?? 0;
  useEffect(() => {
    if (!tgt) return;
    let alive = true;
    setData(null);
    setError(false);
    const spacing = Math.max(nativeKm(DEM[tgt.body], DEM[tgt.body].maxZoom, 0), FOOTPRINT_KM / 128);
    Promise.all([
      sampleTerrain({ body: 'earth', lat, lon, footprintKm: FOOTPRINT_KM, spacingKm: spacing }),
      sampleTerrain({ body: tgt.body, lat: tgt.lat, lon: tgt.lon, footprintKm: FOOTPRINT_KM, spacingKm: spacing }),
      sampleImagery(earthVisible(lat), lat, lon, FOOTPRINT_KM, 1024).catch(() => null),
      sampleImagery(IMAGERY[tgt.body].visible, tgt.lat, tgt.lon, FOOTPRINT_KM, 1024).catch(() => null),
    ])
      .then(([e, t, de, dt]) => {
        if (alive) setData({ earth: e, target: t, drapeEarth: de, drapeTarget: dt });
      })
      .catch(() => {
        if (alive) setError(true);
      });
    return () => {
      alive = false;
    };
  }, [lat, lon, tgt]);

  // Each block's own Sun at the best-matching month, as the Lab's field test starts.
  const progress = useRef(0);
  const field = useMemo<FieldScene | null>(() => {
    if (!data || !tgt) return null;
    const month = startMonth(lat, lightTwin(lat, tgt.body, tgt.lat).months.map((m: { score: number }) => m.score));
    const hour = startHour(lat, tgt.body, tgt.lat, month);
    const shadeE = shadowMask(data.earth, earthSun(lat, month, hour));
    const shadeT = shadowMask(data.target, targetSun(tgt.body, tgt.lat, month, hour));
    return {
      progress,
      mode: 'rover',
      earth: { route: sampleRoute(data.earth, shadeE, 15), shade: shadeE },
      target: { route: sampleRoute(data.target, shadeT, 15), shade: shadeT },
    };
  }, [data, lat, tgt]);

  const drive = autoDrive && !reduce;
  useEffect(() => {
    if (!field) return;
    if (!drive) {
      progress.current = 0.5;
      return;
    }
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      progress.current = (progress.current + dt / LOOP_SECONDS) % 1;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [field, drive]);

  const label = `3D terrain: ${site?.name ?? 'Earth site'} beside ${tgt?.name ?? 'target'}, ${FOOTPRINT_KM} km blocks`;
  return (
    <div className={`relative size-full min-h-[240px] overflow-hidden bg-[#0b0c0f] ${className ?? ''}`} role="img" aria-label={label}>
      {data ? (
        <Suspense fallback={null}>
          <TerrainTwins
            earth={data.earth}
            target={data.target}
            drapeEarth={data.drapeEarth}
            drapeTarget={data.drapeTarget}
            litEarth={null}
            litTarget={null}
            footprintKm={FOOTPRINT_KM}
            sunElev={50}
            sunAz={135}
            playing={false}
            exaggeration={3}
            overlay="imagery"
            field={field}
            showcase
          />
        </Suspense>
      ) : (
        <div className="absolute inset-0 grid place-items-center text-xs text-white/60">{error ? 'Elevation tiles could not be loaded.' : 'Loading terrain…'}</div>
      )}
    </div>
  );
}

export default LabShowcase;
