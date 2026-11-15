'use client';
// The Earth candidate map (PRD 8.6, 10), a deck.gl MapView with the candidate windows only. There
// is no third-party basemap (LIC-02 is decided: no), so nothing is drawn outside the bundle's own
// windows. Loaded through earth-loader.tsx so deck.gl stays off every other route.
import { MapView, WebMercatorViewport } from '@deck.gl/core';
import { PolygonLayer } from '@deck.gl/layers';
import DeckGL from '@deck.gl/react';
import { useMemo } from 'react';

import { scaleColour } from './colour-scales.ts';
import type { Candidate, Family } from './explore-data.ts';

export interface EarthCanvasProps {
  readonly candidates: readonly Candidate[];
  readonly family: Family;
  readonly width: number;
  readonly height: number;
}

const UNAVAILABLE: [number, number, number, number] = [120, 120, 120, 90];

export default function EarthCanvas({ candidates, family, width, height }: EarthCanvasProps) {
  const drawn = useMemo(() => candidates.filter((c) => c.ringLonLat !== null), [candidates]);
  const initial = useMemo(() => {
    const points = drawn.flatMap((c) => c.ringLonLat ?? []);
    if (points.length === 0) return null;
    const lons = points.map((p) => p[0]);
    const lats = points.map((p) => p[1]);
    const viewport = new WebMercatorViewport({ width, height });
    const fitted = viewport.fitBounds(
      [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ],
      { padding: 48, maxZoom: 14 },
    );
    return { longitude: fitted.longitude, latitude: fitted.latitude, zoom: fitted.zoom };
  }, [drawn, width, height]);

  const layers = useMemo(
    () => [
      new PolygonLayer<Candidate>({
        id: 'earth-candidates',
        data: drawn,
        pickable: true,
        stroked: true,
        filled: true,
        lineWidthMinPixels: 2,
        getPolygon: (c) => c.ringLonLat ?? [],
        getFillColor: (c) => {
          const percentile = c.distances[family].background_percentile;
          if (percentile === null) return UNAVAILABLE;
          const [r, g, b] = scaleColour('viridis', percentile / 100);
          return [r, g, b, 190];
        },
        getLineColor: [255, 255, 255, 220],
        updateTriggers: { getFillColor: [family] },
      }),
    ],
    [drawn, family],
  );

  if (initial === null) return null;
  return (
    <DeckGL
      key={`${family}-${String(drawn.length)}`}
      views={new MapView({ id: 'earth' })}
      initialViewState={initial}
      controller={{ keyboard: false }}
      layers={layers}
      getTooltip={({ object }) => {
        const c = object as Candidate | null | undefined;
        if (c === null || c === undefined) return null;
        const entry = c.distances[family];
        return {
          text: `${c.earthWindowId}\n${entry.value === null ? `Unavailable: ${entry.reason ?? 'no reason'}` : `${String(entry.value)} ${entry.unit}`}`,
        };
      }}
    />
  );
}
