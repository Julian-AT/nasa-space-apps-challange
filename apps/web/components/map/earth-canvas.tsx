'use client';
// The Earth candidate globe (PRD 8.6, 10): deck.gl _GlobeView on a dark sphere with a graticule,
// Natural Earth 110m coastlines (public domain, /geo/ne_110m_land.geojson, see public/geo/SOURCE.txt)
// and numbered markers at the centroids of the candidate windows, matching the result cards. There
// is no third-party basemap (LIC-02 decided: no). Loaded through earth-loader.tsx only.
import { _GlobeView as GlobeView } from '@deck.gl/core';
import { GeoJsonLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers';
import DeckGL from '@deck.gl/react';
import { useEffect, useMemo, useState } from 'react';

import { scaleColour } from './colour-scales.ts';
import { markerScale, markerT } from './earth-state.ts';
import type { Candidate, Family } from './explore-data.ts';

export interface EarthCanvasProps {
  readonly candidates: readonly Candidate[];
  readonly family: Family;
  readonly selected: number | null;
  /** Where to point the globe: set by the region buttons, the selected card or the candidate extent. */
  readonly focus: { readonly longitude: number; readonly latitude: number; readonly zoom: number };
  readonly onSelect: (index: number) => void;
}

interface Marker {
  readonly index: number;
  readonly position: [number, number];
  readonly t: number | null;
}

const GRATICULE_STEP_DEG = 30;
const SPHERE: number[][] = [
  [-180, 90],
  [0, 90],
  [180, 90],
  [180, -90],
  [0, -90],
  [-180, -90],
];

function graticule(): [number, number][][] {
  const lines: [number, number][][] = [];
  for (let lon = -180; lon < 180; lon += GRATICULE_STEP_DEG) {
    const line: [number, number][] = [];
    for (let lat = -90; lat <= 90; lat += 5) line.push([lon, lat]);
    lines.push(line);
  }
  for (let lat = -90 + GRATICULE_STEP_DEG; lat < 90; lat += GRATICULE_STEP_DEG) {
    const line: [number, number][] = [];
    for (let lon = -180; lon <= 180; lon += 5) line.push([lon, lat]);
    lines.push(line);
  }
  return lines;
}

const GRID = graticule();

export default function EarthCanvas({ candidates, family, selected, focus, onSelect }: EarthCanvasProps) {
  const markers = useMemo<Marker[]>(() => {
    const scale = markerScale(candidates, family);
    return candidates.flatMap((c, index) =>
      c.centroidLonLat === null
        ? []
        : [{ index, position: [c.centroidLonLat[0], c.centroidLonLat[1]] as [number, number], t: markerT(c.distances[family], scale) }],
    );
  }, [candidates, family]);
  const [viewState, setViewState] = useState({ ...focus });
  useEffect(() => {
    setViewState({ longitude: focus.longitude, latitude: focus.latitude, zoom: focus.zoom });
  }, [focus.longitude, focus.latitude, focus.zoom]);

  const layers = useMemo(
    () => [
      new PolygonLayer<number[][]>({
        id: 'sphere',
        data: [SPHERE],
        getPolygon: (d) => d,
        filled: true,
        stroked: false,
        getFillColor: [10, 14, 26],
      }),
      new GeoJsonLayer({
        id: 'land',
        data: '/geo/ne_110m_land.geojson',
        filled: true,
        stroked: true,
        getFillColor: [44, 54, 74],
        getLineColor: [96, 110, 136],
        lineWidthMinPixels: 1,
      }),
      new PathLayer<[number, number][]>({
        id: 'graticule',
        data: GRID,
        getPath: (d) => d,
        getColor: [90, 100, 125, 90],
        widthMinPixels: 1,
      }),
      new ScatterplotLayer<Marker>({
        id: 'markers',
        data: markers,
        pickable: true,
        radiusUnits: 'pixels',
        getPosition: (m) => m.position,
        getRadius: (m) => (m.index === selected ? 15 : 12),
        getFillColor: (m) => {
          if (m.t === null) return [140, 140, 140, 240];
          const [r, g, b] = scaleColour('viridis', m.t);
          return [r, g, b, 240];
        },
        getLineColor: (m) => (m.index === selected ? [255, 255, 255, 255] : [20, 20, 30, 255]),
        lineWidthMinPixels: 2,
        stroked: true,
        updateTriggers: { getFillColor: [family], getRadius: [selected], getLineColor: [selected] },
        onClick: ({ object }) => {
          if (object !== undefined) onSelect((object as Marker).index);
        },
      }),
      new TextLayer<Marker>({
        id: 'marker-numbers',
        data: markers,
        getPosition: (m) => m.position,
        getText: (m) => String(m.index + 1),
        getSize: 12,
        sizeUnits: 'pixels',
        getColor: [255, 255, 255, 255],
        fontWeight: 700,
        outlineWidth: 3,
        outlineColor: [0, 0, 0, 255],
        getTextAnchor: 'middle',
        getAlignmentBaseline: 'center',
        background: false,
      }),
    ],
    [markers, family, selected, onSelect],
  );

  return (
    <DeckGL
      views={new GlobeView({ id: 'globe' })}
      viewState={viewState}
      onViewStateChange={({ viewState: next }) => {
        setViewState(next);
      }}
      controller={{ keyboard: false }}
      layers={layers}
    />
  );
}
