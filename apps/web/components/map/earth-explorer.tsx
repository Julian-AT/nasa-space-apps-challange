'use client';
// The Earth explorer (PRD 8.6, 10): target and distance-family switches, the candidate map and the
// candidate table. Only one family drives colour and order at a time; families are never mixed.
// With maps disabled (NEXT_PUBLIC_MAPS_ENABLED=0) the same route renders the table alone.
import { useCallback, useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';

import { scaleGradient } from './colour-scales.ts';
import { CandidateTable, FAMILY_TITLES, modelIsRanked, visibleFamilies } from './earth-table.tsx';
import { DEFAULT_EARTH_VIEW, type EarthView, parseEarthView, serializeEarthView } from './earth-state.ts';
import { EarthCanvas } from './earth-loader.tsx';
import type { EarthData } from './explore-data.ts';
import { UnavailablePanel } from './unavailable-panel.tsx';

export interface EarthExplorerProps {
  readonly data: EarthData;
  readonly mapsEnabled: boolean;
  readonly probeEnabled: boolean;
}

export function EarthExplorer({ data, mapsEnabled, probeEnabled }: EarthExplorerProps) {
  const [view, setView] = useState<EarthView>(DEFAULT_EARTH_VIEW);
  const [size, setSize] = useState({ w: 900, h: 480 });
  const stage = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setView(parseEarthView(window.location.search));
  }, []);

  useEffect(() => {
    const el = stage.current;
    if (el === null) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry === undefined) return;
      const w = Math.max(1, Math.round(entry.contentRect.width));
      const h = Math.max(1, Math.round(entry.contentRect.height));
      setSize((prev) => (prev.w === w && prev.h === h ? prev : { w, h }));
    });
    observer.observe(el);
    return () => { observer.disconnect(); };
  });

  const commit = useCallback((next: EarthView) => {
    setView(next);
    const query = serializeEarthView(next);
    window.history.replaceState(null, '', `${window.location.pathname}${query === '' ? '' : `?${query}`}`);
  }, []);

  if (data.targetsMissing) {
    return (
      <UnavailablePanel
        title="Earth candidates"
        reason="Candidates are compared against target windows, and this bundle holds no target records."
        missing="targets.json"
      />
    );
  }
  const target = data.targets.find((t) => t.id === view.target) ?? data.targets[0];
  if (target === undefined) {
    return (
      <UnavailablePanel
        title="Earth candidates"
        reason="targets.json lists no Moon or Mars target to compare Earth windows against."
        missing="a target record in targets.json"
      />
    );
  }

  const families = visibleFamilies(data.modelInBundle, target.candidates);
  const family = families.includes(view.family) ? view.family : DEFAULT_EARTH_VIEW.family;
  const ranked = modelIsRanked(data.g5);
  const drawable = target.candidates.some((c) => c.ringLonLat !== null);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end gap-4">
        {data.targets.length > 1 ? (
          <div className="flex items-center gap-2 text-sm">
            <label htmlFor="earth-target" className="font-medium">
              Target
            </label>
            <select
              id="earth-target"
              value={target.id}
              onChange={(e) => { commit({ ...view, target: e.target.value }); }}
              className="h-8 rounded-md border border-border bg-background px-2"
            >
              {data.targets.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        <div role="group" aria-label="Distance family" className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">Colour and order by</span>
          {families.map((f) => (
            <Button
              key={f}
              type="button"
              size="sm"
              variant={f === family ? 'default' : 'outline'}
              aria-pressed={f === family}
              onClick={() => { commit({ ...view, family: f }); }}
            >
              {FAMILY_TITLES[f]}
              {f === 'model_embedding' && !ranked ? ' (Predicted, unranked)' : ''}
            </Button>
          ))}
        </div>
      </div>

      {!target.comparisonsPresent ? (
        <UnavailablePanel
          title={`Candidates for ${target.name}`}
          reason="No comparison records for this target are in the bundle, so no candidate is listed and none is drawn."
          missing={target.comparisonsFile}
        />
      ) : (
        <>
          {mapsEnabled ? (
            <div className="flex flex-col gap-2">
              <div
                ref={stage}
                role="img"
                aria-label={`Map of ${String(target.candidates.length)} candidate windows for ${target.name}, coloured by ${FAMILY_TITLES[family]} background percentile. The table below lists the same windows.`}
                className="relative h-96 overflow-hidden rounded-xl border border-border bg-black/60"
                data-slot="earth-map"
              >
                {drawable ? (
                  <EarthCanvas candidates={target.candidates} family={family} width={size.w} height={size.h} />
                ) : (
                  <div className="flex h-full items-center justify-center p-6">
                    <UnavailablePanel
                      title="Candidate footprints"
                      reason={
                        data.earthWindowsPresent
                          ? 'None of the candidates has a window record in windows/earth.json, so nothing is drawn.'
                          : 'Window footprints are needed to place candidates on the map.'
                      }
                      missing={data.earthWindowsPresent ? 'window records for the candidates' : 'windows/earth.json'}
                    />
                  </div>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                <span>Fill: {FAMILY_TITLES[family]} background percentile, as serialized (0 to 100)</span>
                <span
                  role="img"
                  aria-label="viridis colour scale from percentile 0 to 100"
                  className="inline-block h-3 w-40 rounded-sm border border-border"
                  style={{ backgroundImage: scaleGradient('viridis') }}
                />
                <span>Grey: percentile Unavailable</span>
                <span>No basemap: only the bundle&apos;s own windows are drawn. Footprints are in WGS84.</span>
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Maps are switched off for this build; the candidate table below is the same data.</p>
          )}
          <CandidateTable
            targetName={target.name}
            candidates={target.candidates}
            family={family}
            g5={data.g5}
            modelInBundle={data.modelInBundle}
          />
        </>
      )}

      {probeEnabled ? (
        <form action="/probe" method="get" className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4">
          <h3 className="text-base font-semibold">Probe a location</h3>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col text-sm">
              Latitude
              <input name="lat" type="number" step="any" min="-90" max="90" required className="h-8 rounded-md border border-border bg-background px-2" />
            </label>
            <label className="flex flex-col text-sm">
              Longitude
              <input name="lon" type="number" step="any" min="-180" max="180" required className="h-8 rounded-md border border-border bg-background px-2" />
            </label>
            <Button type="submit">Open live probe</Button>
          </div>
          <p className="text-sm text-muted-foreground">Live estimate from public elevation data; not a site assessment.</p>
        </form>
      ) : null}
    </div>
  );
}
