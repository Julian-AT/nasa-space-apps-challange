'use client';
// The Moon and Mars explorer (PRD 8.6, 8.10, 10): a full-width map panel and a side panel. The map
// is deck.gl, loaded through deck-loader.tsx only; everything around it (controls, URL state,
// keyboard, side panel, Unavailable panels) renders on the server and works before deck.gl loads.
// The view state lives in the query string (view-state.ts) and the keyboard reducer is pure.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';

import { DeckCanvas } from './deck-loader.tsx';
import type { BodyData, MapTarget } from './explore-data.ts';
import { Chip, ResultsPanel } from './results-panel.tsx';
import { LayersPanel, NuPanel, ProductsPanel, TargetInfo, WindowsPanel } from './target-panels.tsx';
import {
  extentToWorld,
  fitZoom,
  fromWorld,
  type Point,
  resolveLayers,
  type TileLayerRecord,
  toWorld,
} from './tile-frame.ts';
import { UnavailablePanel } from './unavailable-panel.tsx';
import {
  applyKey,
  type Camera,
  DEFAULT_VIEW,
  isMapKey,
  parseViewState,
  serializeViewState,
  type ViewState,
} from './view-state.ts';

export interface ExplorerMapProps {
  readonly data: BodyData;
  readonly body: 'moon' | 'mars';
  /** NEXT_PUBLIC_ASSET_BASE, or null when it is not set. */
  readonly assetBase: string | null;
}

const HELP = 'Arrow keys pan, plus and minus zoom, Home resets the view.';

const CONTROLS = [
  ['Pan north', 'ArrowUp'],
  ['Pan south', 'ArrowDown'],
  ['Pan west', 'ArrowLeft'],
  ['Pan east', 'ArrowRight'],
  ['Zoom in', '+'],
  ['Zoom out', '-'],
  ['Reset view', 'Home'],
] as const;

function unionExtent(layers: readonly TileLayerRecord[]): [number, number, number, number] {
  const boxes = layers.map((l) => {
    const [x0, y0, x1, y1] = l.extent_m as [number, number, number, number];
    return [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)] as const;
  });
  return [
    Math.min(...boxes.map((b) => b[0])),
    Math.min(...boxes.map((b) => b[1])),
    Math.max(...boxes.map((b) => b[2])),
    Math.max(...boxes.map((b) => b[3])),
  ];
}

export function ExplorerMap({ data, body, assetBase }: ExplorerMapProps) {
  const [view, setView] = useState<ViewState>(DEFAULT_VIEW);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 960, h: 560 });
  const [tileErrors, setTileErrors] = useState(0);
  const stage = useRef<HTMLDivElement | null>(null);
  const writeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The URL is the source of the first view; reading it after mount keeps the static HTML stable.
  useEffect(() => {
    setView(parseViewState(window.location.search));
  }, []);

  useEffect(() => {
    const el = stage.current;
    if (el === null) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry !== undefined) {
        const w = Math.max(1, Math.round(entry.contentRect.width));
        const h = Math.max(1, Math.round(entry.contentRect.height));
        setSize((prev) => (prev.w === w && prev.h === h ? prev : { w, h }));
      }
    });
    observer.observe(el);
    return () => { observer.disconnect(); };
  }, []);

  const commit = useCallback((next: ViewState) => {
    setView(next);
    if (writeTimer.current !== null) clearTimeout(writeTimer.current);
    writeTimer.current = setTimeout(() => {
      const query = serializeViewState(next);
      window.history.replaceState(null, '', `${window.location.pathname}${query === '' ? '' : `?${query}`}`);
    }, 150);
  }, []);

  const onTileError = useCallback(() => { setTileErrors((n) => n + 1); }, []);

  const target: MapTarget | undefined = data.targets.find((t) => t.id === view.target) ?? data.targets[0];
  const resolved = useMemo(() => (target === undefined ? null : resolveLayers(target.layers)), [target]);
  const title = body === 'moon' ? 'Moon' : 'Mars';

  if (data.targetsMissing) {
    return (
      <UnavailablePanel
        title={`${title} map`}
        reason="The map needs target records, projections and tile layers, and this bundle holds none."
        missing="targets.json"
      />
    );
  }
  if (target === undefined || resolved === null) {
    return (
      <UnavailablePanel
        title={`${title} map`}
        reason={`targets.json lists no ${body} target.`}
        missing={`a ${body} TargetRecord in targets.json`}
      />
    );
  }

  const { frame, drawable } = resolved;
  const active = (view.layers ?? drawable.slice(0, 1).map((l) => l.layer)).filter((name) =>
    drawable.some((l) => l.layer === name),
  );
  const activeLayers = drawable.filter((l) => active.includes(l.layer));

  let camera: Camera | null = null;
  if (frame !== null && drawable.length > 0) {
    const extent = unionExtent(drawable);
    const fitted = fitZoom(extentToWorld(frame, extent), size.w, size.h);
    camera = {
      x: view.x ?? (extent[0] + extent[2]) / 2,
      y: view.y ?? (extent[1] + extent[3]) / 2,
      zoom: view.zoom ?? fitted,
    };
  }
  const range = frame === null ? { min: 0, max: 0 } : { min: frame.zoomMin - 3, max: frame.zoomMax + 3 };

  const apply = (key: string): void => {
    if (!isMapKey(key) || camera === null || frame === null) return;
    const next = applyKey(camera, key, frame.metresPerUnit, range);
    commit(
      next === null ? { ...view, x: null, y: null, zoom: null } : { ...view, x: next.x, y: next.y, zoom: next.zoom },
    );
  };

  const toggleLayer = (layer: string, on: boolean): void => {
    commit({ ...view, layers: on ? [...new Set([...active, layer])] : active.filter((n) => n !== layer) });
  };

  const onViewChange = (centre: Point, zoom: number): void => {
    if (frame === null) return;
    const [x, y] = fromWorld(frame, centre);
    commit({ ...view, x, y, zoom });
  };

  const centreWorld: Point = camera === null || frame === null ? [0, 0] : toWorld(frame, [camera.x, camera.y]);

  let missingReason: string;
  let missingFile: string | undefined;
  if (target.layers.length === 0) {
    missingReason = 'This target has no tile layers, so the map has nothing to draw. No placeholder is shown.';
    missingFile = `TargetRecord.tile_layers for ${target.id} in targets.json (tiles/{layer}/{z}/{x}/{y}.webp)`;
  } else if (assetBase === null) {
    missingReason = 'NEXT_PUBLIC_ASSET_BASE is not set, so tile URLs cannot be formed.';
    missingFile = 'NEXT_PUBLIC_ASSET_BASE';
  } else if (drawable.length === 0) {
    missingReason = 'None of the target’s tile layers can be addressed (see the layer list).';
  } else {
    missingReason = 'No layer is switched on.';
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[26rem_minmax(0,1fr)]" data-slot="explorer">
      <ResultsPanel
        label="Target details"
        title={target.windowsFilePresent ? `${String(target.windows.length)} ${target.windows.length === 1 ? 'window' : 'windows'}` : target.name}
        chips={[
          <Chip key="t">{target.name}</Chip>,
          <Chip key="p">{target.projectionKind.replaceAll('_', ' ')}</Chip>,
          ...(target.shippingMode === null ? [] : [<Chip key="m">Mode {target.shippingMode}</Chip>]),
        ]}
      >
        <TargetInfo target={target} />
        <NuPanel nu={target.nu} />
        <WindowsPanel target={target} />
        <LayersPanel body={body} target={target} active={active} onToggle={toggleLayer} />
        <ProductsPanel target={target} />
      </ResultsPanel>
      <div className="flex min-w-0 flex-col gap-3">
        {data.targets.length > 1 ? (
          <div className="flex items-center gap-2 text-sm">
            <label htmlFor="target-select" className="font-medium">
              Target
            </label>
            <select
              id="target-select"
              value={target.id}
              onChange={(e) => { commit({ ...DEFAULT_VIEW, target: e.target.value }); }}
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

        <div
          ref={stage}
          role="group"
          aria-label={`Map of ${target.name}. ${HELP}`}
          aria-describedby="map-help"
          tabIndex={0}
          onKeyDown={(e) => {
            if (isMapKey(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) {
              e.preventDefault();
              apply(e.key);
            }
          }}
          className="relative h-[34rem] min-h-80 lg:h-[calc(100dvh-8rem)] overflow-hidden rounded-2xl border border-border bg-black/70 shadow-sm outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring"
          data-slot="map-stage"
        >
          {assetBase !== null && frame !== null && camera !== null && activeLayers.length > 0 ? (
            <DeckCanvas
              frame={frame}
              layers={activeLayers}
              windows={target.windows}
              showWindows
              assetBase={assetBase}
              bundleVersion={data.bundleVersion}
              target={centreWorld}
              zoom={camera.zoom}
              onViewChange={onViewChange}
              onTileError={onTileError}
            />
          ) : (
            <div className="flex h-full items-center justify-center p-6">
              <UnavailablePanel
                className="max-w-xl"
                title={`${target.name} map`}
                reason={missingReason}
                missing={missingFile}
              />
            </div>
          )}
          <p className="pointer-events-none absolute bottom-2 left-2 rounded bg-background/80 px-2 py-1 text-xs text-foreground">
            Bundle {data.bundleVersion}
            {camera === null ? '' : `; centre ${camera.x.toFixed(0)}, ${camera.y.toFixed(0)} m; zoom ${camera.zoom.toFixed(2)}`}
          </p>
        </div>

        <p id="map-help" className="text-sm text-muted-foreground">
          {HELP} Drag to pan and scroll to zoom with a pointer. The view is kept in the address bar so it can be shared.
        </p>

        <div role="toolbar" aria-label="Map controls" className="flex flex-wrap gap-2">
          {CONTROLS.map(([label, key]) => (
            <Button
              key={key}
              type="button"
              variant="outline"
              size="sm"
              disabled={camera === null}
              onClick={() => { apply(key); }}
            >
              {label}
            </Button>
          ))}
        </div>
        {tileErrors > 0 ? (
          <p role="alert" className="text-sm font-medium">
            {String(tileErrors)} tile requests failed; the affected areas are blank, not zero.
          </p>
        ) : null}
      </div>

    </div>
  );
}
