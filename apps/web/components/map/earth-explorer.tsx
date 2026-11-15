'use client';
// The Earth explorer (PRD 8.6, 10): target and distance-family switches, the candidate map and the
// candidate table. Only one family drives colour and order at a time; families are never mixed.
// With maps disabled (NEXT_PUBLIC_MAPS_ENABLED=0) the same route renders the table alone.
import { useCallback, useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';

import { scaleGradient } from './colour-scales.ts';
import { EarthCards } from './earth-cards.tsx';
import { CandidateTable, FAMILY_TITLES, modelIsRanked, visibleFamilies } from './earth-table.tsx';
import { Chip, ResultsPanel } from './results-panel.tsx';
import { DEFAULT_EARTH_VIEW, type EarthView, focusOn, parseEarthView, serializeEarthView } from './earth-state.ts';
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
  const [selected, setSelected] = useState<number | null>(null);
  const [region, setRegion] = useState<string | null>(null);

  useEffect(() => {
    setView(parseEarthView(window.location.search));
  }, []);

  const commit = useCallback((next: EarthView) => {
    setView(next);
    const query = serializeEarthView(next);
    window.history.replaceState(null, '', `${window.location.pathname}${query === '' ? '' : `?${query}`}`);
  }, []);

  const noTargets = data.targetsMissing
    ? { reason: 'Candidates are compared against target windows, and this bundle holds no target records.', missing: 'targets.json' }
    : data.targets.length === 0
      ? { reason: 'targets.json lists no Moon or Mars target to compare Earth windows against.', missing: 'a target record in targets.json' }
      : null;
  const target = data.targets.find((t) => t.id === view.target) ?? data.targets[0];
  if (noTargets !== null || target === undefined) {
    return (
      <div className="grid gap-4 lg:grid-cols-[26rem_minmax(0,1fr)]">
        <ResultsPanel label="Candidate results" title="No candidates">
          <UnavailablePanel title="Earth candidates" reason={noTargets?.reason ?? ''} missing={noTargets?.missing} />
        </ResultsPanel>
        <div className="flex min-h-96 items-center justify-center rounded-2xl border border-border bg-black/60 p-6">
          <UnavailablePanel title="Candidate globe" reason="Nothing to draw: no candidate records." missing={noTargets?.missing} />
        </div>
      </div>
    );
  }

  const families = visibleFamilies(data.modelInBundle, target.candidates);
  const family = families.includes(view.family) ? view.family : DEFAULT_EARTH_VIEW.family;
  const ranked = modelIsRanked(data.g5);
  const drawable = target.candidates.some((c) => c.centroidLonLat !== null);
  const regions = [...new Set(target.candidates.flatMap((c) => (c.regionId === null ? [] : [c.regionId])))];
  const placed = target.candidates.flatMap((c, i) => (c.centroidLonLat === null ? [] : [{ i, c }]));
  const selectedPoint = selected === null ? undefined : placed.find((p) => p.i === selected)?.c.centroidLonLat;
  const focus =
    selectedPoint !== undefined && selectedPoint !== null
      ? focusOn([selectedPoint])
      : focusOn(
          placed
            .filter((p) => region === null || p.c.regionId === region)
            .flatMap((p) => (p.c.centroidLonLat === null ? [] : [p.c.centroidLonLat])),
        );
  const sortRule = target.candidates.find((c) => c.sortRule !== null)?.sortRule ?? null;
  const mode = target.candidates.find((c) => c.shippingMode !== null)?.shippingMode ?? null;

  return (
    <div className="flex flex-col gap-8">
      <div className="grid gap-4 lg:grid-cols-[26rem_minmax(0,1fr)]">
        <ResultsPanel
          label="Candidate results"
          title={`${String(target.candidates.length)} candidate ${target.candidates.length === 1 ? 'window' : 'windows'}`}
          chips={[
            <Chip key="t">{target.name}</Chip>,
            <Chip key="f">{FAMILY_TITLES[family]}</Chip>,
            ...(mode === null ? [] : [<Chip key="m">Mode {mode}</Chip>]),
          ]}
        >
          {data.targets.length > 1 ? (
            <div className="flex items-center gap-2 text-sm">
              <label htmlFor="earth-target" className="font-medium">
                Target
              </label>
              <select
                id="earth-target"
                value={target.id}
                onChange={(e) => {
                  setSelected(null);
                  commit({ ...view, target: e.target.value });
                }}
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
          <div role="group" aria-label="Distance family" className="flex flex-col gap-2">
            <span className="text-sm font-medium">Colour the map by</span>
            <div className="flex flex-wrap gap-2">
              {families.map((f) => (
                <Button
                  key={f}
                  type="button"
                  size="sm"
                  variant={f === family ? 'default' : 'outline'}
                  aria-pressed={f === family}
                  onClick={() => {
                    commit({ ...view, family: f });
                  }}
                >
                  {FAMILY_TITLES[f]}
                  {f === 'model_embedding' && !ranked ? ' (Predicted, unranked)' : ''}
                </Button>
              ))}
            </div>
          </div>
          {regions.length < 2 ? null : (
            <div role="group" aria-label="Region" className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">Zoom to</span>
              {[null, ...regions].map((r) => (
                <Button
                  key={r ?? 'all'}
                  type="button"
                  size="sm"
                  variant={r === region ? 'default' : 'outline'}
                  aria-pressed={r === region}
                  onClick={() => {
                    setSelected(null);
                    setRegion(r);
                  }}
                >
                  {r ?? 'All regions'}
                </Button>
              ))}
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            Order: as stored in the comparisons file{sortRule === null ? ' (no sort_rule is stored)' : `, sort_rule: ${sortRule}`}. No combined
            score exists; each family is shown on its own.
          </p>
          {!target.comparisonsPresent ? (
            <UnavailablePanel
              title={`Candidates for ${target.name}`}
              reason="No comparison records for this target are in the bundle, so no candidate is listed and none is drawn."
              missing={target.comparisonsFile}
            />
          ) : target.candidates.length === 0 ? (
            <p className="text-sm">
              <span data-slot="candidate-count" className="font-semibold">0</span> comparison records for {target.name}; none are listed.
            </p>
          ) : (
            <EarthCards candidates={target.candidates} selected={selected} onSelect={setSelected} modelInBundle={data.modelInBundle} />
          )}
        </ResultsPanel>

        <div className="flex min-w-0 flex-col gap-2 lg:sticky lg:top-4 lg:self-start">
          <div
            role="img"
            aria-label={`Globe with ${String(target.candidates.filter((c) => c.centroidLonLat !== null).length)} numbered candidate markers for ${target.name}, coloured by ${FAMILY_TITLES[family]} background percentile. The result cards list the same windows.`}
            className="relative h-[34rem] overflow-hidden rounded-2xl border border-border bg-black/70 lg:h-[calc(100dvh-8rem)]"
            data-slot="earth-map"
          >
            {mapsEnabled && drawable ? (
              <EarthCanvas candidates={target.candidates} family={family} selected={selected} focus={focus} onSelect={setSelected} />
            ) : (
              <div className="flex h-full items-center justify-center p-6">
                <UnavailablePanel
                  title="Candidate globe"
                  reason={
                    !mapsEnabled
                      ? 'Maps are switched off for this build; the candidate table below is the same data.'
                      : !target.comparisonsPresent
                        ? 'There are no candidates to place.'
                        : data.earthWindowsPresent
                          ? 'None of the candidates has a window footprint in windows/earth.json, so nothing is drawn.'
                          : 'Footprints are needed to place candidates on the globe.'
                  }
                  missing={!mapsEnabled || !target.comparisonsPresent ? undefined : 'windows/earth.json footprints'}
                />
              </div>
            )}
            {mapsEnabled && drawable ? (
              <div className="pointer-events-none absolute top-3 right-3 flex flex-col gap-1 rounded-xl border border-border bg-card/90 p-3 text-xs text-card-foreground">
                <span className="font-semibold">{FAMILY_TITLES[family]} background percentile</span>
                <span
                  role="img"
                  aria-label="viridis colour scale from percentile 0 to 100"
                  className="h-2.5 w-44 rounded-sm border border-border"
                  style={{ backgroundImage: scaleGradient('viridis') }}
                />
                <span className="flex justify-between">
                  <span>0</span>
                  <span>100 (as serialized)</span>
                </span>
                <span>Grey: Unavailable</span>
              </div>
            ) : null}
          </div>
          <p className="text-xs text-muted-foreground">
            No third-party basemap: sphere, graticule and Natural Earth 110m coastlines (public domain) only. Markers sit at footprint
            centroids (WGS84).
          </p>
        </div>
      </div>

      {target.comparisonsPresent && target.candidates.length > 0 ? (
        <details className="rounded-2xl border border-border bg-card p-4">
          <summary className="cursor-pointer text-base font-semibold">
            All {String(target.candidates.length)} candidates, per-family table
          </summary>
          <div className="mt-4">
        <CandidateTable
          targetName={target.name}
          candidates={target.candidates}
          family={family}
          g5={data.g5}
          modelInBundle={data.modelInBundle}
        />
          </div>
        </details>
      ) : null}

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
