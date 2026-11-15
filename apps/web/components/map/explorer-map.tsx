'use client';
// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The Moon and Mars explorer (PRD 8.6, 8.10, 10): the Atlas three-column layout around the map. The map
// is deck.gl, loaded through deck-loader.tsx only; everything around it (controls, URL state,
// keyboard, side panel, Unavailable panels) renders on the server and works before deck.gl loads.
// The view state lives in the query string (view-state.ts) and the keyboard reducer is pure.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';

import { DeckCanvas } from './deck-loader.tsx';
import type { BodyData, MapTarget } from './explore-data.ts';
import { AtlasShell, FilterChip, PanelHeading } from './atlas-shell.tsx';
import { ELIGIBILITY_TONES, type EligibilityStatus, summariseWindows } from './eligibility.ts';
import { LayersPanel, NuPanel, ProductsPanel, TargetInfo, WindowList, WindowsPanel } from './target-panels.tsx';
import {
  extentToWorld,
  fitZoom,
  fromWorld,
  type Point,
  METRIC_FRAME,
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
  const [statusFilter, setStatusFilter] = useState<EligibilityStatus | 'all'>('all');
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

  const empty = (reason: string, missing: string) => (
    <AtlasShell
      world={body}
      title={title}
      subtitle={`Bundle ${data.bundleVersion}`}
      leftLabel="Layers and filters"
      rightLabel="Windows"
      left={<p className="text-sm text-dim">No layers: the bundle holds no {body} target.</p>}
      right={<p className="text-sm text-dim">No windows: the bundle holds no {body} target.</p>}
    >
      <div className="flex h-full items-center justify-center p-6 pt-40">
        <UnavailablePanel className="max-w-xl" title={`${title} map`} reason={reason} missing={missing} />
      </div>
    </AtlasShell>
  );
  if (data.targetsMissing) {
    return empty('The map needs target records, projections and tile layers, and this bundle holds none.', 'targets.json');
  }
  if (target === undefined || resolved === null) {
    return empty(`targets.json lists no ${body} target.`, `a ${body} TargetRecord in targets.json`);
  }

  const { drawable } = resolved;
  const hasTiles = resolved.frame !== null && drawable.length > 0;
  const frame = hasTiles ? resolved.frame : METRIC_FRAME;
  const active = (view.layers ?? drawable.slice(0, 1).map((l) => l.layer)).filter((name) =>
    drawable.some((l) => l.layer === name),
  );
  const activeLayers = drawable.filter((l) => active.includes(l.layer));

  // The drawn extent: the tile layers' extents, else the window rings (and footprint) of the bundle.
  let extent: [number, number, number, number] | null = null;
  if (hasTiles) {
    extent = unionExtent(drawable);
  } else {
    const points = [...target.windows.flatMap((w) => w.ringM), ...(target.footprintRingM ?? [])];
    if (points.length > 0) {
      const xs = points.map((p) => p[0]);
      const ys = points.map((p) => p[1]);
      extent = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    }
  }

  let camera: Camera | null = null;
  let range = { min: 0, max: 0 };
  if (extent !== null) {
    const fitted = fitZoom(extentToWorld(frame, extent), size.w, size.h);
    camera = {
      x: view.x ?? (extent[0] + extent[2]) / 2,
      y: view.y ?? (extent[1] + extent[3]) / 2,
      zoom: view.zoom ?? fitted,
    };
    range = hasTiles ? { min: frame.zoomMin - 3, max: frame.zoomMax + 3 } : { min: fitted - 6, max: fitted + 8 };
  }
  const canDraw = camera !== null && (activeLayers.length > 0 ? assetBase !== null : target.windows.length > 0);

  const apply = (key: string): void => {
    if (!isMapKey(key) || camera === null) return;
    const next = applyKey(camera, key, frame.metresPerUnit, range);
    commit(
      next === null ? { ...view, x: null, y: null, zoom: null } : { ...view, x: next.x, y: next.y, zoom: next.zoom },
    );
  };

  const toggleLayer = (layer: string, on: boolean): void => {
    commit({ ...view, layers: on ? [...new Set([...active, layer])] : active.filter((n) => n !== layer) });
  };

  const onViewChange = (centre: Point, zoom: number): void => {
    const [x, y] = fromWorld(frame, centre);
    commit({ ...view, x, y, zoom });
  };

  const centreWorld: Point = camera === null ? [0, 0] : toWorld(frame, [camera.x, camera.y]);

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

  const summary = summariseWindows(target.windows);
  const shownWindows = statusFilter === 'all' ? target.windows : target.windows.filter((w) => w.status === statusFilter);
  const subtitle = [
    `${String(data.targets.length)} ${data.targets.length === 1 ? 'target' : 'targets'}`,
    target.windowsFilePresent ? `${String(summary.total)} windows (${String(summary.byStatus.eligible)} eligible)` : 'windows unavailable',
    `${String(target.layers.length)} ${target.layers.length === 1 ? 'layer' : 'layers'}`,
    `bundle ${data.bundleVersion}`,
  ].join(' \u00b7 ');
  const modeNote = target.shippingMode === 'C' ? 'Mode C: terrain only; radar comparison not completed' : body === 'mars' ? 'Terrain only; no radar comparison on Mars' : null;

  const left = (
    <>
      <div className="flex flex-col gap-3">
        <PanelHeading>Target</PanelHeading>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Target">
          {data.targets.map((t) => (
            <FilterChip
              key={t.id}
              active={t.id === target.id}
              onClick={() => {
                setStatusFilter('all');
                commit({ ...DEFAULT_VIEW, target: t.id });
              }}
            >
              {t.name}
            </FilterChip>
          ))}
        </div>
      </div>
      <div className="flex flex-col gap-3">
        <PanelHeading>Windows shown</PanelHeading>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Window eligibility filter">
          <FilterChip active={statusFilter === 'all'} onClick={() => { setStatusFilter('all'); }}>
            All {String(summary.total)}
          </FilterChip>
          {(['eligible', 'rejected', 'unknown'] as const).map((status) => (
            <FilterChip key={status} active={statusFilter === status} onClick={() => { setStatusFilter(status); }}>
              {ELIGIBILITY_TONES[status].label.replace(' (excluded)', '')} {String(summary.byStatus[status])}
            </FilterChip>
          ))}
        </div>
      </div>
      <LayersPanel body={body} target={target} active={active} onToggle={toggleLayer} />
      <div className="flex flex-col gap-2 border-t border-line pt-4">
        <PanelHeading>Map controls</PanelHeading>
        <p id="map-help" className="text-[12px] leading-relaxed text-dim">
          {HELP} Drag to pan and scroll to zoom with a pointer. The view is kept in the address bar so it can be shared.
        </p>
        <div role="toolbar" aria-label="Map controls" className="flex flex-wrap gap-1.5">
          {CONTROLS.map(([label, key]) => (
            <Button
              key={key}
              type="button"
              variant="outline"
              size="sm"
              className="rounded-full border-line-2 text-[12px]"
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
      <WindowsPanel target={target} hideList />
      <ProductsPanel target={target} />
    </>
  );

  const right = (
    <>
      <div className="flex flex-col gap-3">
        <PanelHeading count={target.windowsFilePresent ? String(shownWindows.length) : undefined}>Windows</PanelHeading>
        <div className="flex flex-wrap gap-1.5 text-[11px]">
          <span className="rounded-full border border-line-2 px-2.5 py-1 text-chrome">{target.name}</span>
          {target.shippingMode === null ? null : <span className="rounded-full border border-line-2 px-2.5 py-1 text-chrome">Mode {target.shippingMode}</span>}
          <span className="rounded-full border border-line-2 px-2.5 py-1 text-chrome">{target.projectionKind.replaceAll('_', ' ')}</span>
        </div>
        {modeNote === null ? null : (
          <p role="note" className="rounded-xl border border-flare/30 bg-flare/5 px-3 py-2 text-[12px] text-platinum">{modeNote}</p>
        )}
        <WindowList target={target} windows={shownWindows} />
      </div>
      <TargetInfo target={target} />
      <NuPanel nu={target.nu} />
    </>
  );

  return (
    <AtlasShell world={body} title={title} subtitle={subtitle} left={left} right={right} leftLabel="Layers and filters" rightLabel="Target details and windows">
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
        className="absolute inset-0 overflow-hidden outline-offset-[-3px] focus-visible:outline-2 focus-visible:outline-ring"
        data-slot="map-stage"
      >
        {canDraw && camera !== null ? (
          <DeckCanvas
            frame={frame}
            layers={activeLayers}
            windows={shownWindows}
            showWindows
            assetBase={assetBase ?? ''}
            zoomRange={range}
            bundleVersion={data.bundleVersion}
            target={centreWorld}
            zoom={camera.zoom}
            onViewChange={onViewChange}
            onTileError={onTileError}
          />
        ) : (
          <div className="flex h-full items-center justify-center p-6 pt-40">
            <UnavailablePanel className="max-w-xl" title={`${target.name} map`} reason={missingReason} missing={missingFile} />
          </div>
        )}
        {canDraw && activeLayers.length === 0 ? (
          <p role="status" className="pointer-events-none absolute right-3 bottom-10 rounded-full border border-line-2 bg-void/80 px-3 py-1 text-xs text-chrome">
            {target.layers.length === 0
              ? 'No tile layers in this bundle (TargetRecord.tile_layers is empty); only the window grid is drawn.'
              : 'No tile layer is switched on; only the window grid is drawn.'}
          </p>
        ) : null}
        <p className="pointer-events-none absolute bottom-3 left-3 rounded-full border border-line bg-void/75 px-3 py-1 font-mono text-[11px] text-chrome backdrop-blur-xl">
          Bundle {data.bundleVersion}
          {camera === null ? '' : `; centre ${camera.x.toFixed(0)}, ${camera.y.toFixed(0)} m; zoom ${camera.zoom.toFixed(2)}`}
        </p>
      </div>
    </AtlasShell>
  );
}
