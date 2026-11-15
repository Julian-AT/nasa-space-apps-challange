'use client';
// The deck.gl canvas (PRD 8.10). This module is the only importer of deck.gl; it is loaded through
// next/dynamic from deck-loader.tsx and so only on /explore/* routes. OrthographicView works in
// pyramid units of the target's own projected frame (tile-frame.ts); TileLayer fetches bundle tiles
// and the window grid is drawn from the WindowEntry polygons, coloured by eligibility status.
import { OrthographicView } from '@deck.gl/core';
import { TileLayer } from '@deck.gl/geo-layers';
import { BitmapLayer, PathLayer, PolygonLayer, TextLayer } from '@deck.gl/layers';
import DeckGL from '@deck.gl/react';
import { useMemo } from 'react';

import { eligibilityTone } from './eligibility.ts';
import type { MapWindow } from './explore-data.ts';
import {
  extentToWorld,
  type Frame,
  isOmittedTile,
  type Point,
  tileBounds,
  type TileLayerRecord,
  tileUrl,
  toWorld,
} from './tile-frame.ts';

export interface DeckCanvasProps {
  readonly frame: Frame;
  readonly layers: readonly TileLayerRecord[];
  readonly windows: readonly MapWindow[];
  readonly showWindows: boolean;
  readonly assetBase: string;
  readonly bundleVersion: string;
  readonly zoomRange: { readonly min: number; readonly max: number };
  /** Camera centre in pyramid units and zoom. */
  readonly target: Point;
  readonly zoom: number;
  readonly onViewChange: (target: Point, zoom: number) => void;
  readonly onTileError: (url: string) => void;
}

function ringWorld(frame: Frame, ring: MapWindow['ringM']): [number, number][] {
  return ring.map((p) => {
    const [u, v] = toWorld(frame, p);
    return [u, v];
  });
}

const MAX_LABELLED = 150;

const HIGHLIGHT: [number, number, number, number] = [255, 255, 255, 255];

export default function DeckCanvas(props: DeckCanvasProps) {
  const { frame, layers, windows, showWindows, assetBase, bundleVersion, target, zoom } = props;
  const { onViewChange, onTileError } = props;
  const view = useMemo(() => new OrthographicView({ id: 'map', flipY: frame.flipY }), [frame.flipY]);

  const deckLayers = useMemo(() => {
    const tiles = layers.map(
      (layer, index) =>
        new TileLayer<ImageBitmap | null>({
          id: `tiles-${layer.layer}`,
          tileSize: layer.tile_px,
          minZoom: layer.zoom_min,
          maxZoom: layer.zoom_max,
          extent: extentToWorld(frame, layer.extent_m),
          opacity: index === 0 ? 1 : 0.75,
          getTileData: async ({ index: tile, signal }) => {
            if (isOmittedTile(layer, { z: tile.z, x: tile.x, y: tile.y })) return null;
            const url = tileUrl(assetBase, bundleVersion, layer.layer, tile);
            const response = await fetch(url, signal === undefined ? {} : { signal });
            if (!response.ok) {
              onTileError(url);
              return null;
            }
            return createImageBitmap(await response.blob());
          },
          renderSubLayers: (sub) => {
            const box = sub.tile.boundingBox;
            const [left, top, right, bottom] = [box[0][0], box[0][1], box[1][0], box[1][1]] as [number, number, number, number];
            if (!sub.data) return null;
            return new BitmapLayer({
              id: `${sub.id}-bitmap`,
              image: sub.data,
              bounds: tileBounds(frame, { left, top, right, bottom }),
              ...(sub.opacity === undefined ? {} : { opacity: sub.opacity }),
            });
          },
        }),
    );
    const windowLayers = showWindows
      ? [
          new PolygonLayer<MapWindow>({
            id: 'window-grid',
            data: windows as MapWindow[],
            pickable: true,
            getPolygon: (w) => ringWorld(frame, w.ringM),
            getFillColor: (w) => [...eligibilityTone(w.status).fill] as [number, number, number, number],
            getLineColor: (w) => [...eligibilityTone(w.status).line] as [number, number, number, number],
            lineWidthMinPixels: 1,
            stroked: true,
            filled: true,
          }),
          new PathLayer<MapWindow>({
            id: 'window-primary',
            data: windows.filter((w) => w.isPrimary),
            getPath: (w) => ringWorld(frame, w.ringM),
            getColor: HIGHLIGHT,
            widthMinPixels: 3,
          }),
          new TextLayer<MapWindow & { n: number }>({
            id: 'window-numbers',
            data: windows.map((w, i) => ({ ...w, n: i + 1 })).slice(0, MAX_LABELLED),
            getPosition: (w) => {
              const pts = ringWorld(frame, w.ringM);
              const sum = pts.reduce<[number, number]>((a, p) => [a[0] + p[0], a[1] + p[1]] as [number, number], [0, 0]);
              return [sum[0] / pts.length, sum[1] / pts.length];
            },
            getText: (w) => String(w.n),
            getSize: 12,
            getColor: [255, 255, 255, 255],
            outlineWidth: 3,
            outlineColor: [0, 0, 0, 255],
            fontWeight: 700,
          }),
        ]
      : [];
    return [...tiles, ...windowLayers];
  }, [layers, windows, showWindows, frame, assetBase, bundleVersion, onTileError]);

  const zoomMin = props.zoomRange.min;
  const zoomMax = props.zoomRange.max;
  return (
    <DeckGL
      views={view}
      viewState={{ target: [target[0], target[1], 0], zoom, minZoom: zoomMin, maxZoom: zoomMax }}
      controller={{ keyboard: false }}
      layers={deckLayers}
      onViewStateChange={({ viewState }) => {
        const vs = viewState as { target: number[]; zoom: number };
        onViewChange([vs.target[0] as number, vs.target[1] as number], vs.zoom);
      }}
      getTooltip={({ object }) => {
        const w = object as MapWindow | null | undefined;
        if (w === null || w === undefined || !('status' in w)) return null;
        const reasons = w.reasons.length === 0 ? '' : `\nreasons: ${w.reasons.join(', ')}`;
        return { text: `${w.id}\n${eligibilityTone(w.status).label}${reasons}` };
      }}
    />
  );
}
