'use client';
// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The Earth explorer (PRD 8.6, 10): target and distance-family switches, the candidate map and the
// candidate table. Only one family drives colour and order at a time; families are never mixed.
// With maps disabled (NEXT_PUBLIC_MAPS_ENABLED=0) the same route renders the table alone.
import { useCallback, useEffect, useState } from 'react';

import { scaleGradient } from './colour-scales.ts';
import { EarthCards } from './earth-cards.tsx';
import { CandidateTable, FAMILY_TITLES, modelIsRanked, visibleFamilies } from './earth-table.tsx';
import { AtlasShell, FilterChip, PanelHeading } from './atlas-shell.tsx';
import { Button } from '@/components/ui/button';
import { DEFAULT_EARTH_VIEW, type EarthView, focusOn, markerScale, parseEarthView, serializeEarthView } from './earth-state.ts';
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
      <AtlasShell
        world="earth"
        title="Earth"
        subtitle={`no candidates \u00b7 bundle ${data.bundleVersion}`}
        leftLabel="Filters"
        rightLabel="Candidates"
        left={<p className="text-sm text-dim">No filters: there are no candidate records.</p>}
        right={<UnavailablePanel title="Earth candidates" reason={noTargets?.reason ?? ''} missing={noTargets?.missing} />}
      >
        <div className="flex h-full items-center justify-center p-6">
          <UnavailablePanel title="Candidate globe" reason="Nothing to draw: no candidate records." missing={noTargets?.missing} />
        </div>
      </AtlasShell>
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
  const scale = markerScale(target.candidates, family);
  const sortRule = target.candidates.find((c) => c.sortRule !== null)?.sortRule ?? null;
  const mode = target.candidates.find((c) => c.shippingMode !== null)?.shippingMode ?? null;

  const nPlaced = placed.length;
  const subtitle = [
    `${String(target.candidates.length)} candidate ${target.candidates.length === 1 ? 'window' : 'windows'}`,
    `${String(nPlaced)} on the map`,
    ...(regions.length === 0 ? [] : [`${String(regions.length)} ${regions.length === 1 ? 'region' : 'regions'}`]),
    `compared with ${target.name}`,
    `bundle ${data.bundleVersion}`,
  ].join(' \u00b7 ');

  const left = (
    <>
      {data.targets.length > 1 ? (
        <div className="flex flex-col gap-3">
          <PanelHeading>Target</PanelHeading>
          <div role="group" aria-label="Target" className="flex flex-wrap gap-1.5">
            {data.targets.map((t) => (
              <FilterChip
                key={t.id}
                active={t.id === target.id}
                onClick={() => {
                  setSelected(null);
                  commit({ ...view, target: t.id });
                }}
              >
                {t.name}
              </FilterChip>
            ))}
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <PanelHeading>Target</PanelHeading>
          <p className="text-[13px] text-platinum">{target.name}</p>
          {mode === null ? null : <p className="font-mono text-[11px] text-dim">Mode {mode}</p>}
        </div>
      )}
      <div role="group" aria-label="Distance family" className="flex flex-col gap-3">
        <PanelHeading>Colour the map by</PanelHeading>
        <div className="flex flex-wrap gap-1.5">
          {families.map((f) => (
            <FilterChip
              key={f}
              active={f === family}
              onClick={() => {
                commit({ ...view, family: f });
              }}
            >
              {FAMILY_TITLES[f]}
              {f === 'model_embedding' && !ranked ? ' (Predicted, unranked)' : ''}
            </FilterChip>
          ))}
        </div>
      </div>
      {regions.length < 2 ? null : (
        <div role="group" aria-label="Region" className="flex flex-col gap-3">
          <PanelHeading>Zoom to</PanelHeading>
          <div className="flex flex-wrap gap-1.5">
            {[null, ...regions].map((r) => (
              <FilterChip
                key={r ?? 'all'}
                active={r === region}
                onClick={() => {
                  setSelected(null);
                  setRegion(r);
                }}
              >
                {r ?? 'All regions'}
              </FilterChip>
            ))}
          </div>
        </div>
      )}
      {mapsEnabled && drawable ? (
        <div className="flex flex-col gap-2 border-t border-line pt-4" data-slot="earth-legend">
          <PanelHeading>Legend</PanelHeading>
          <p className="text-[13px] text-platinum">
            {FAMILY_TITLES[family]}
            {scale.mode === 'percentile' ? ' background percentile' : ' distance'}
          </p>
          <span
            role="img"
            aria-label="viridis colour scale"
            className="h-3 w-full rounded-sm border border-line-2"
            style={{ backgroundImage: scaleGradient('viridis') }}
          />
          <span className="flex justify-between font-mono text-[11px] text-chrome">
            <span>{String(scale.min)}</span>
            <span>{String(scale.max)}</span>
          </span>
          <p className="font-mono text-[11px] text-dim">
            {scale.mode === 'percentile' ? 'as serialized (0 to 100)' : `${scale.unit ?? 'no unit'}; Derived, smaller is closer`}. Grey: Unavailable
          </p>
        </div>
      ) : null}
      <p className="border-t border-line pt-4 text-[12px] leading-relaxed text-dim">
        Order: as stored in the comparisons file{sortRule === null ? ' (no sort_rule is stored)' : `, sort_rule: ${sortRule}`}. No combined
        score exists; each family is shown on its own.
      </p>
      <p className="text-[12px] leading-relaxed text-dim">
        No third-party basemap: sphere, graticule and Natural Earth 110m coastlines (public domain) only. Markers sit at footprint
        centroids (WGS84).
      </p>
      {probeEnabled ? (
        <form action="/probe" method="get" className="flex flex-col gap-2 border-t border-line pt-4">
          <PanelHeading>Probe a location</PanelHeading>
          <label className="flex flex-col gap-1 text-sm">
            Latitude
            <input name="lat" type="number" step="any" min="-90" max="90" required className="h-9 rounded-xl border border-line-2 bg-ink px-3" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Longitude
            <input name="lon" type="number" step="any" min="-180" max="180" required className="h-9 rounded-xl border border-line-2 bg-ink px-3" />
          </label>
          <Button type="submit" className="rounded-full">Open live probe</Button>
          <p className="text-[12px] text-dim">Live estimate from public elevation data; not a site assessment.</p>
        </form>
      ) : null}
    </>
  );

  const right = (
    <>
      <div className="flex flex-col gap-3">
        <PanelHeading count={target.comparisonsPresent ? String(target.candidates.length) : undefined}>Candidates</PanelHeading>
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
          <EarthCards candidates={target.candidates} selected={selected} onSelect={setSelected} modelInBundle={data.modelInBundle} family={family} />
        )}
      </div>
    </>
  );

  return (
    <>
      <AtlasShell world="earth" title="Earth" subtitle={subtitle} left={left} right={right} leftLabel="Filters and legend" rightLabel="Candidate windows">
        <div
          role="img"
          aria-label={`Globe with ${String(nPlaced)} numbered candidate markers for ${target.name}, coloured by ${FAMILY_TITLES[family]}. The result cards list the same windows.`}
          className="absolute inset-0"
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
        </div>
      </AtlasShell>

      {target.comparisonsPresent && target.candidates.length > 0 ? (
        <div data-full-bleed className="px-3 pb-4 sm:px-4">
          <details className="rounded-3xl border border-line-2 bg-panel p-4">
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
        </div>
      ) : null}
    </>
  );
}
