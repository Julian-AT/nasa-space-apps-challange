// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The Maps atlas (PRD 8.6, 8.10, 10): a full-bleed stage with the world switcher, a 3D | 2D
// toggle, a big world title, a left panel (target filter, map layers, legend) and a right panel
// (targets and windows). The 3D view is the textured globe; the 2D view is a Leaflet map with
// NASA imagery for context and the bundle's hillshade tiles and window polygons on top. Every
// value comes from AtlasData, which atlas-data.ts reads from the release bundle; nothing
// scientific is typed in here. View, layer, filter and selection live in the URL query.
'use client';

import clsx from 'clsx';
import { Box, ChevronLeft, ChevronRight, ExternalLink, Map as MapIcon } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';

import { EvidenceLabel, isEvidenceLabel } from '@/components/evidence-label';
import { LabelledValue } from '@/components/labelled-value';
import { Globe, type GlobePin } from '@/components/globe';

import { UnavailablePanel } from '../map/unavailable-panel.tsx';
import type { AtlasBody, AtlasData, AtlasTarget, AtlasWindow, BundleTileLayer } from './atlas-data.ts';
import type { MapFocus, MapPin, MapShape } from './body-map.tsx';
import { DEFAULT_LAYER, LAYERS, LAYERS_BY_BODY, type MapLayer } from './layers.ts';

// Leaflet only loads in the browser, and only once the 2D view is opened.
const BodyMap = dynamic(() => import('./body-map.tsx'), { ssr: false, loading: () => <StageLoading label="Loading 2D map" /> });

const EASE = [0.22, 1, 0.36, 1] as const;
const BODIES: readonly { readonly id: AtlasBody; readonly label: string }[] = [
  { id: 'earth', label: 'Earth' },
  { id: 'moon', label: 'Moon' },
  { id: 'mars', label: 'Mars' },
];
const TEX: Record<AtlasBody, string> = { earth: '/textures/earth_day_1k.webp', moon: '/textures/moon_lroc_1k.webp', mars: '/textures/mars_viking_1k.webp' };
const WORLD_COLOR: Record<AtlasBody, string> = { earth: '#8fb4ea', moon: '#c9c5bc', mars: '#e08a5f' };
const STATUS_DOT: Record<'eligible' | 'rejected' | 'unknown', string> = { eligible: '#34d399', rejected: '#fbbf24', unknown: '#94a3b8' };
const FLARE = '#ffffff';
const MAX_ROWS = 40;
const MODE_C_NOTE = 'Mode C: terrain only; radar comparison not completed';

type View = '3d' | '2d';

function fmt(v: number): string {
  return Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toPrecision(3);
}

function short(id: string): string {
  return id.length > 34 ? `${id.slice(0, 16)}…${id.slice(-14)}` : id;
}

/** Map coordinates [lat, lng] of a metre point in a bundle layer's projected frame (y down). */
function toUnits(l: BundleTileLayer, [x, y]: readonly [number, number]): [number, number] {
  return [-(l.originY - y) / l.metresPerUnit, (x - l.originX) / l.metresPerUnit];
}

function ringCentre(ring: readonly (readonly [number, number])[]): [number, number] {
  const pts = ring.length > 1 ? ring.slice(0, -1) : ring;
  let a = 0;
  let b = 0;
  for (const p of pts) {
    a += p[0];
    b += p[1];
  }
  return [a / pts.length, b / pts.length];
}

function readQuery(): URLSearchParams {
  return typeof window === 'undefined' ? new URLSearchParams() : new URLSearchParams(window.location.search);
}

export function Atlas({ data }: { readonly data: AtlasData }) {
  const body = data.body;
  const firstTarget = data.targets[0] ?? null;
  const [view, setView] = useState<View>('3d');
  const [filter, setFilter] = useState<string>('all');
  const [layerId, setLayerId] = useState<string>(body === 'earth' ? DEFAULT_LAYER.earth : (firstTarget?.tileLayerId ?? DEFAULT_LAYER[body]));
  const [overlay, setOverlay] = useState(true);
  const [sel, setSel] = useState<string | null>(null);
  const [panels, setPanels] = useState({ left: true, right: true });
  const [focus, setFocus] = useState<MapFocus | null>(null);
  const [ready, setReady] = useState(false);

  // Shared URL state: ?view=2d&layer=…&target=…&sel=…
  useEffect(() => {
    const q = readQuery();
    const v = q.get('view');
    if (v === '2d' || v === '3d') setView(v);
    const t = q.get('target');
    if (t !== null && data.targets.some((x) => x.id === t)) setFilter(t);
    const l = q.get('layer');
    if (l !== null && (LAYERS.some((x) => x.id === l && x.body === body) || data.tileLayers.some((x) => x.id === l))) setLayerId(l);
    const s = q.get('sel');
    if (s !== null) setSel(s);
    setReady(true);
  }, [body, data.targets, data.tileLayers]);

  useEffect(() => {
    if (!ready) return;
    const q = readQuery();
    const set = (k: string, v: string | null) => (v === null ? q.delete(k) : q.set(k, v));
    set('view', view === '3d' ? null : view);
    set('target', filter === 'all' ? null : filter);
    set('layer', layerId);
    set('sel', sel);
    const s = q.toString();
    window.history.replaceState(window.history.state, '', `${window.location.pathname}${s === '' ? '' : `?${s}`}`);
  }, [ready, view, filter, layerId, sel]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSel(null);
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  const nasa = LAYERS.find((l) => l.id === layerId && l.body === body) ?? null;
  const projectedLayer = data.tileLayers.find((l) => l.id === layerId && l.kind === 'projected') ?? null;
  const frame = projectedLayer !== null ? 'projected' : (nasa?.projection ?? (body === 'earth' ? 'mercator' : 'equirect'));
  const baseLayer = nasa ?? LAYERS.find((l) => l.id === DEFAULT_LAYER[body]) ?? null;

  const targetById = useMemo(() => new Map(data.targets.map((t) => [t.id, t])), [data.targets]);
  const windows = useMemo(
    () =>
      data.windows.filter((w) =>
        filter === 'all' ? true : body === 'earth' ? w.comparedWith.includes(filter) : w.targetId === filter,
      ),
    [data.windows, filter, body],
  );
  const shownTargets = body === 'earth' ? data.targets : data.targets.filter((t) => filter === 'all' || t.id === filter);
  const regions = useMemo(() => {
    if (body !== 'earth') return [];
    const by = new Map<string, AtlasWindow[]>();
    for (const w of windows) {
      if (w.regionId === null) continue;
      by.set(w.regionId, [...(by.get(w.regionId) ?? []), w]);
    }
    return [...by.entries()].map(([id, ws]) => {
      const pts = ws.filter((w) => w.lat !== null && w.lon !== null);
      return {
        id,
        count: ws.length,
        cards: ws.filter((w) => w.cardId !== null).length,
        lat: pts.reduce((a, w) => a + (w.lat as number), 0) / Math.max(1, pts.length),
        lon: pts.reduce((a, w) => a + (w.lon as number), 0) / Math.max(1, pts.length),
      };
    });
  }, [windows, body]);

  // Where a selection sits in a given frame.
  const placeOf = useCallback(
    (id: string): { lat: number; lon: number; units: [number, number] | null } | null => {
      const target = id.startsWith('target:') ? targetById.get(id.slice(7)) : undefined;
      const region = id.startsWith('region:') ? regions.find((r) => r.id === id.slice(7)) : undefined;
      const w = data.windows.find((x) => x.id === (target?.primaryWindowId ?? id));
      const pl = projectedLayer;
      const units = pl !== null && w?.ringM != null && (w.targetId === pl.targetId) ? ringCentre(w.ringM.map((p) => toUnits(pl, p))) : null;
      if (region !== undefined) return { lat: region.lat, lon: region.lon, units: null };
      if (w === undefined || w.lat === null || w.lon === null) return target?.lat != null && target.lon != null ? { lat: target.lat, lon: target.lon, units } : null;
      return { lat: w.lat, lon: w.lon, units };
    },
    [targetById, regions, data.windows, projectedLayer],
  );

  const select = useCallback(
    (id: string) => {
      setSel(id);
      setPanels((p) => ({ ...p, right: true }));
      // A target opens its own projected frame in 2D.
      if (id.startsWith('target:')) {
        const t = targetById.get(id.slice(7));
        if (t?.tileLayerId != null && projectedLayer !== null && projectedLayer.targetId !== t.id) setLayerId(t.tileLayerId);
      }
    },
    [targetById, projectedLayer],
  );

  useEffect(() => {
    if (sel === null) return;
    const p = placeOf(sel);
    if (p === null) return;
    const at: [number, number] | null = frame === 'projected' ? p.units : [p.lat, p.lon];
    if (at !== null) setFocus((f) => ({ at, nonce: (f?.nonce ?? 0) + 1 }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel, frame, layerId]);

  // 3D pins.
  const globePins = useMemo((): GlobePin[] => {
    const pins: GlobePin[] = [];
    if (body === 'earth') {
      for (const r of regions) pins.push({ id: `region:${r.id}`, lat: r.lat, lon: r.lon, label: `Search region ${r.id} · ${String(r.count)} windows`, href: `/explore/earth?sel=region:${r.id}`, tone: 'earth' });
    } else {
      for (const t of shownTargets) {
        if (t.lat === null || t.lon === null) continue;
        pins.push({ id: `target:${t.id}`, lat: t.lat, lon: t.lon, label: t.name, href: t.cardId === null ? `/explore/${body}?sel=target:${t.id}` : `/site/${t.cardId}`, tone: body });
      }
    }
    for (const w of windows) {
      if (w.cardId === null || w.lat === null || w.lon === null) continue;
      pins.push({ id: w.id, lat: w.lat, lon: w.lon, label: `Card window ${w.id}`, href: `/site/${w.cardId}`, tone: 'flare' });
    }
    return pins;
  }, [body, regions, shownTargets, windows]);

  // 2D shapes and pins in the current frame.
  const { shapes, mapPins } = useMemo(() => {
    const shapes: MapShape[] = [];
    const mapPins: MapPin[] = [];
    const pl = projectedLayer;
    for (const w of windows) {
      let ring: [number, number][] | null = null;
      if (pl !== null) {
        if (w.targetId !== pl.targetId || w.ringM === null) continue;
        ring = w.ringM.map((p) => toUnits(pl, p));
      } else if (w.ringLonLat !== null) {
        ring = w.ringLonLat.map(([lon, lat]) => [lat, lon]);
      }
      if (ring === null) continue;
      shapes.push({
        id: w.id,
        ring,
        status: w.status,
        highlight: w.isPrimary ? 'primary' : w.cardId !== null ? 'card' : null,
        label: `${w.id} · ${w.status ?? 'status not in bundle'}${w.isPrimary ? ' · primary' : ''}${w.cardId !== null ? ' · card' : ''}`,
      });
    }
    if (body !== 'earth') {
      for (const t of shownTargets) {
        const p = placeOf(`target:${t.id}`);
        const at = pl !== null ? (pl.targetId === t.id ? p?.units : null) : p === null ? null : ([p.lat, p.lon] as [number, number]);
        if (at == null) continue;
        mapPins.push({ id: `target:${t.id}`, at, color: WORLD_COLOR[body], label: t.name });
      }
    } else {
      for (const r of regions) mapPins.push({ id: `region:${r.id}`, at: [r.lat, r.lon], color: WORLD_COLOR.earth, label: `Search region ${r.id}` });
    }
    return { shapes, mapPins };
  }, [windows, projectedLayer, body, shownTargets, regions, placeOf]);

  const bundleLayersForMap = useMemo(
    () => (projectedLayer !== null ? [projectedLayer] : body === 'earth' && overlay ? data.tileLayers.filter((l) => l.kind === 'mercator') : []),
    [projectedLayer, body, overlay, data.tileLayers],
  );

  const activeTarget = sel?.startsWith('target:') ? (targetById.get(sel.slice(7)) ?? null) : null;
  const activeWindow = sel !== null && !sel.includes(':') ? (data.windows.find((w) => w.id === sel) ?? null) : null;
  const modeC = data.targets.some((t) => t.shippingMode === 'C');
  const layerName = projectedLayer?.name ?? nasa?.name ?? null;
  const cardCount = windows.filter((w) => w.cardId !== null).length;

  return (
    <div data-full-bleed className="bg-void">
      <div className="relative lg:h-[calc(100dvh-7.75rem)] lg:min-h-[680px]">
        {/* ------------------------------------------------ stage */}
        <section aria-label={`${BODIES.find((b) => b.id === body)?.label ?? body} map`} className="relative h-[62vh] min-h-[420px] overflow-hidden border-b border-line bg-void lg:absolute lg:inset-0 lg:h-auto lg:border-b-0">
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.div key={`${body}-${view}-${frame}`} className={clsx('absolute inset-0', view === '2d' && panels.left && 'lg:left-[320px]', view === '2d' && panels.right && 'lg:right-[400px]')} initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.6, ease: EASE }}>
              {view === '3d' ? (
                <div className="absolute inset-0 flex items-center justify-center pt-14 pb-10">
                  <div className="aspect-square h-full max-w-full">
                    <Globe body={body} pins={globePins} selectedId={sel} onSelect={select} autoRotate={sel === null} className="h-full" />
                  </div>
                </div>
              ) : (
                <BodyMap
                  frame={frame}
                  base={frame === 'projected' ? null : baseLayer}
                  bundleLayers={bundleLayersForMap}
                  shapes={shapes}
                  pins={mapPins}
                  activeId={sel}
                  onSelect={select}
                  focus={focus}
                  initial={(() => {
                    const p = sel === null ? null : placeOf(sel);
                    return p === null || frame === 'projected' ? undefined : { at: [p.lat, p.lon] as const, zoom: frame === 'mercator' ? 6 : 4 };
                  })()}
                />
              )}
            </motion.div>
          </AnimatePresence>
          <div className="pointer-events-none absolute inset-0 z-[500] bg-[radial-gradient(120%_80%_at_50%_50%,transparent_55%,rgba(7,8,10,0.55))]" />

          <div className="pointer-events-none absolute inset-x-0 top-0 z-[600] flex flex-wrap items-start justify-center gap-2 p-3 sm:gap-3 sm:p-4 lg:pt-5">
            <nav aria-label="World" className="pointer-events-auto flex gap-1 rounded-full border border-line-2 bg-void/70 p-1 shadow-[0_20px_50px_-20px_rgba(0,0,0,0.8)] backdrop-blur-xl">
              {BODIES.map((b) => (
                <Link
                  key={b.id}
                  href={`/explore/${b.id}${view === '2d' ? '?view=2d' : ''}`}
                  aria-current={body === b.id ? 'page' : undefined}
                  className={clsx('relative flex items-center gap-1.5 rounded-full py-1 pr-2.5 pl-1 text-[12px] no-underline transition-colors focus-visible:outline-2 focus-visible:outline-ring sm:gap-2 sm:py-1.5 sm:pr-4 sm:pl-1.5 sm:text-[13px]', body === b.id ? 'bg-platinum/[0.09] text-platinum ring-1 ring-platinum/15' : 'text-dim hover:text-chrome')}
                >
                  <PlanetThumb body={b.id} on={body === b.id} />
                  <span className="relative">{b.label}</span>
                </Link>
              ))}
            </nav>
            <div role="group" aria-label="View" className="pointer-events-auto flex gap-1 rounded-full border border-line-2 bg-void/70 p-1 backdrop-blur-xl lg:absolute lg:top-5 lg:right-[412px]">
              <ViewBtn on={view === '3d'} onClick={() => setView('3d')} icon={<Box size={14} aria-hidden="true" />} label="3D" />
              <ViewBtn on={view === '2d'} onClick={() => setView('2d')} icon={<MapIcon size={14} aria-hidden="true" />} label="2D" />
            </div>
          </div>

          {view === '2d' ? null : <WorldTitle body={body} left={panels.left} line={`${String(data.targets.length)} ${data.targets.length === 1 ? 'target' : 'targets'}${body === 'earth' ? ' compared' : ''} · ${String(windows.length)} windows · ${String(cardCount)} with cards`} sub={`Bundle ${data.bundleVersion}${data.mock ? ' · MOCK preview, not production' : ''}`} />}

          <div className="pointer-events-none absolute bottom-4 left-1/2 z-[600] hidden -translate-x-1/2 items-center gap-4 rounded-full border border-line bg-void/75 px-4 py-2 text-[11px] whitespace-nowrap text-chrome backdrop-blur-xl md:flex">
            <Key color={STATUS_DOT.eligible} label="Eligible" />
            <Key color={STATUS_DOT.rejected} label="Rejected" dashed />
            <Key color={FLARE} label={body === 'earth' ? 'Card window' : 'Primary or card window'} />
            <Key color={WORLD_COLOR[body]} label={body === 'earth' ? 'Search region' : 'Target'} />
            <span className="font-mono text-dim">
              {windows.length} windows{view === '2d' && layerName !== null ? ` · ${layerName}` : ''}
            </span>
          </div>
        </section>

        {/* ------------------------------------------------ left panel */}
        <Panel side="left" open={panels.left} onToggle={() => setPanels((p) => ({ ...p, left: !p.left }))} label="Filters and layers">
          <div className="space-y-8 p-5">
            {data.mock ? <p className="rounded-xl border border-mock bg-mock/20 px-3 py-2 text-[12px] font-semibold text-mock-foreground">Mock preview: synthetic records, not production results.</p> : null}
            <Section title={body === 'earth' ? 'Compared with' : 'Target'}>
              <div className="flex flex-wrap gap-1.5">
                <Chip on={filter === 'all'} onClick={() => setFilter('all')}>
                  All
                </Chip>
                {data.targets.map((t) => (
                  <Chip key={t.id} on={filter === t.id} onClick={() => setFilter(t.id)}>
                    {t.name}
                  </Chip>
                ))}
              </div>
              {modeC ? <p className="mt-3 rounded-xl border border-line bg-ink px-3 py-2 text-[12px] leading-relaxed text-platinum">{MODE_C_NOTE}</p> : null}
            </Section>

            <Section title={`${BODIES.find((b) => b.id === body)?.label ?? body} map layers`}>
              <div className="space-y-2">
                {data.tileLayers
                  .filter((l) => l.kind === 'projected')
                  .map((l) => (
                    <LayerCard
                      key={l.id}
                      name={l.name}
                      sub={`${l.units} · ${l.evidenceLabel}`}
                      resolution={`${fmt(l.finestMetresPerPixel)} m/px`}
                      thumb={l.thumbUrl}
                      on={view === '2d' && layerId === l.id}
                      onClick={() => {
                        setLayerId(l.id);
                        setView('2d');
                      }}
                    />
                  ))}
                {LAYERS_BY_BODY[body]
                  .filter((l) => l.overlay !== true)
                  .map((l) => (
                    <LayerCard
                      key={l.id}
                      name={l.name}
                      sub={l.instrument}
                      resolution={l.resolution}
                      thumb={previewUrl(l)}
                      on={view === '2d' && layerId === l.id}
                      onClick={() => {
                        setLayerId(l.id);
                        setView('2d');
                      }}
                    />
                  ))}
                {body === 'earth' && data.tileLayers.length > 0 ? (
                  <label className="flex cursor-pointer items-center justify-between gap-3 rounded-xl border border-dashed border-line-2 px-3 py-2.5">
                    <span>
                      <span className="block text-[13px] text-platinum">+ Bundle hillshade ({data.tileLayers.length} sites)</span>
                      <span className="block text-[11px] text-dim">Terraluna COP30 hillshade tiles · Derived · 2D only</span>
                    </span>
                    <input
                      type="checkbox"
                      checked={overlay}
                      onChange={(e) => {
                        setOverlay(e.target.checked);
                        if (e.target.checked) setView('2d');
                      }}
                      className="size-4 accent-[#edeae4]"
                    />
                  </label>
                ) : null}
              </div>
              <p className="mt-3 text-[11px] leading-relaxed text-dim">
                {body === 'earth'
                  ? 'The 3D globe uses a NASA mosaic; a layer opens the live 2D tile map. NASA imagery is context only.'
                  : 'Bundle hillshade layers open the target in its own projected frame (no reprojection). NASA Trek mosaics are global context only.'}
              </p>
            </Section>

            <Section title="Legend">
              <ul className="space-y-1.5 text-[12px] text-chrome">
                <li><Key color={STATUS_DOT.eligible} label="Eligible window (status from the windows file)" /></li>
                <li><Key color={STATUS_DOT.rejected} label="Rejected: excluded, not a poor match" dashed /></li>
                <li><Key color={STATUS_DOT.unknown} label="Unknown status" dashed /></li>
                <li><Key color={FLARE} label={body === 'earth' ? 'Window with an evidence card' : 'Primary window or window with a card'} /></li>
              </ul>
              <p className="mt-3 text-[11px] leading-relaxed text-dim">
                Relief is ν at the smallest supported lag, with its evidence label. Missing evidence is shown as Unavailable with its reason, never as zero.
              </p>
              {body === 'earth' && data.modelInBundle ? (
                <p className="mt-2 text-[11px] leading-relaxed text-dim">
                  Encoder embedding distance: <EvidenceLabel label="Predicted" /> unranked{data.g5 === null ? '' : ` (G5 ${data.g5})`}; it never orders these lists.
                </p>
              ) : null}
            </Section>

            {data.unavailable.length > 0 ? (
              <Section title="Unavailable">
                <div className="space-y-2">
                  {data.unavailable.map((u) => (
                    <UnavailablePanel key={u.title} title={u.title} reason={u.reason} missing={u.missing} />
                  ))}
                </div>
              </Section>
            ) : null}
          </div>
        </Panel>

        {/* ------------------------------------------------ right panel */}
        <Panel side="right" open={panels.right} onToggle={() => setPanels((p) => ({ ...p, right: !p.right }))} label="Targets and windows">
          <div className="space-y-6 p-5">
            {activeWindow !== null ? <WindowDetail w={activeWindow} target={activeWindow.targetId === null ? null : (targetById.get(activeWindow.targetId) ?? null)} onClose={() => setSel(null)} /> : null}
            {body !== 'earth' ? (
              <Section title={`Targets · ${String(shownTargets.length)}`}>
                <ul className="space-y-1.5">
                  {shownTargets.map((t) => (
                    <TargetRow key={t.id} t={t} body={body} on={activeTarget?.id === t.id} onClick={() => select(`target:${t.id}`)} />
                  ))}
                </ul>
                {shownTargets.length === 0 ? <UnavailablePanel title="Targets" reason={`No ${body} target in targets.json`} missing="targets.json" /> : null}
              </Section>
            ) : (
              <Section title={`Search regions · ${String(regions.length)}`}>
                <ul className="space-y-1.5">
                  {regions.map((r) => (
                    <li key={r.id}>
                      <button type="button" onClick={() => select(`region:${r.id}`)} aria-pressed={sel === `region:${r.id}`} className={clsx('flex w-full items-center gap-3 rounded-xl border p-2.5 text-left transition-colors', sel === `region:${r.id}` ? 'border-flare/50 bg-flare/[0.06]' : 'border-line hover:border-line-2')}>
                        <span className="size-2.5 shrink-0 rounded-full" style={{ background: WORLD_COLOR.earth }} aria-hidden="true" />
                        <span className="min-w-0 flex-1 truncate text-[13px] text-platinum">{r.id}</span>
                        <span className="shrink-0 font-mono text-[10.5px] text-dim">{r.count} windows · {r.cards} cards</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </Section>
            )}
            <WindowList windows={windows} sel={sel} onSelect={select} />
          </div>
        </Panel>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- pieces */

function sortWindows(ws: readonly AtlasWindow[]): AtlasWindow[] {
  const rank = (w: AtlasWindow) => (w.isPrimary ? 0 : w.cardId !== null ? 1 : w.isReference ? 2 : w.comparedWith.length > 0 ? 3 : w.status === 'eligible' ? 4 : 5);
  return [...ws].sort((a, b) => rank(a) - rank(b) || a.id.localeCompare(b.id));
}

function WindowList({ windows, sel, onSelect }: { readonly windows: readonly AtlasWindow[]; readonly sel: string | null; readonly onSelect: (id: string) => void }) {
  const [all, setAll] = useState(false);
  const sorted = useMemo(() => sortWindows(windows), [windows]);
  const shown = all ? sorted : sorted.slice(0, MAX_ROWS);
  return (
    <Section title={`Windows · ${String(windows.length)}`}>
      {windows.length === 0 ? <UnavailablePanel title="Windows" reason="No window records for this filter in the bundle" missing="windows/*.json" /> : null}
      <ul className="space-y-1">
        {shown.map((w) => (
          <li key={w.id}>
            <div className={clsx('flex items-center gap-2 rounded-xl border p-2 transition-colors', sel === w.id ? 'border-flare/50 bg-flare/[0.06]' : 'border-line hover:border-line-2')}>
              <button type="button" onClick={() => onSelect(w.id)} aria-pressed={sel === w.id} className="flex min-w-0 flex-1 items-center gap-2.5 text-left">
                <span aria-hidden="true" className={clsx('size-2.5 shrink-0 rounded-full', w.status === 'rejected' && 'border border-dashed')} style={w.status === 'rejected' ? { borderColor: STATUS_DOT.rejected } : { background: w.isPrimary || w.cardId !== null ? FLARE : STATUS_DOT[w.status ?? 'unknown'] }} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-[11.5px] text-platinum" title={w.id}>
                    {short(w.id)}
                    {w.isPrimary ? ' · primary' : w.isReference ? ' · reference' : ''}
                  </span>
                  <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[11px] text-dim">
                    <ReliefInline w={w} />
                  </span>
                </span>
              </button>
              {w.cardId !== null ? (
                <Link href={`/site/${w.cardId}`} className="inline-flex shrink-0 items-center gap-1 rounded-full border border-line-2 px-2 py-1 text-[11px] text-chrome no-underline hover:text-platinum focus-visible:outline-2 focus-visible:outline-ring">
                  Card <ExternalLink size={11} aria-hidden="true" />
                </Link>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      {sorted.length > MAX_ROWS ? (
        <button type="button" onClick={() => setAll((a) => !a)} className="mt-2 text-[12px] text-chrome underline hover:text-platinum">
          {all ? 'Show fewer' : `Show all ${String(sorted.length)}`}
        </button>
      ) : null}
    </Section>
  );
}

function ReliefInline({ w }: { readonly w: AtlasWindow }) {
  const r = w.relief;
  if (r === null || r.evidenceLabel === 'Unavailable') {
    return (
      <>
        <EvidenceLabel label="Unavailable" />
        <span className="truncate">{w.reasons.length > 0 ? w.reasons.join(', ') : 'no relief fingerprint'}</span>
      </>
    );
  }
  return (
    <>
      <EvidenceLabel label={isEvidenceLabel(r.evidenceLabel) ? r.evidenceLabel : 'Derived'} />
      <span className="font-mono text-chrome">ν {fmt(r.valueM)} m</span>
      <span>@ {r.lagM} m</span>
    </>
  );
}

function TargetRow({ t, body, on, onClick }: { readonly t: AtlasTarget; readonly body: AtlasBody; readonly on: boolean; readonly onClick: () => void }) {
  const r = t.primaryRelief;
  return (
    <li>
      <div className={clsx('rounded-xl border p-2.5 transition-colors', on ? 'border-flare/50 bg-flare/[0.06]' : 'border-line hover:border-line-2')}>
        <button type="button" onClick={onClick} aria-pressed={on} className="flex w-full items-start gap-3 text-left">
          <span aria-hidden="true" className="mt-1 size-2.5 shrink-0 rounded-full" style={{ background: WORLD_COLOR[body] }} />
          <span className="min-w-0 flex-1">
            <span className="block text-[13.5px] text-platinum">{t.name}</span>
            <span className="mt-0.5 block font-mono text-[10.5px] text-dim">
              {t.eligibleCount} of {t.windowCount} windows eligible{t.shippingMode === null ? '' : ` · Mode ${t.shippingMode}`}
            </span>
            <span className="mt-1 flex flex-wrap items-center gap-x-1.5 text-[11px] text-dim">
              {r === null || r.evidenceLabel === 'Unavailable' ? (
                <>
                  <EvidenceLabel label="Unavailable" />
                  <span>{t.primaryWindowId === null ? 'primary window not frozen' : 'no relief fingerprint for the primary window'}</span>
                </>
              ) : (
                <>
                  <EvidenceLabel label={isEvidenceLabel(r.evidenceLabel) ? r.evidenceLabel : 'Derived'} />
                  <span className="font-mono text-chrome">ν {fmt(r.valueM)} m</span>
                  <span>@ {r.lagM} m, primary window</span>
                </>
              )}
            </span>
          </span>
        </button>
        {t.shippingMode === 'C' ? <p className="mt-2 pl-5.5 text-[11px] text-chrome">{MODE_C_NOTE}</p> : null}
        {t.cardId !== null ? (
          <Link href={`/site/${t.cardId}`} className="mt-2 ml-5.5 inline-flex items-center gap-1 text-[12px] text-chrome underline hover:text-platinum">
            Open evidence card <ExternalLink size={11} aria-hidden="true" />
          </Link>
        ) : (
          <p className="mt-2 pl-5.5 text-[11px] text-dim">No evidence card for this target in the bundle.</p>
        )}
      </div>
    </li>
  );
}

function WindowDetail({ w, target, onClose }: { readonly w: AtlasWindow; readonly target: AtlasTarget | null; readonly onClose: () => void }) {
  const r = w.relief;
  return (
    <div className="rounded-2xl border border-line-2 bg-ink p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-mono text-[10px] tracking-[0.2em] text-dim uppercase">{target?.name ?? w.regionId ?? 'Window'}</div>
          <div className="mt-1 font-mono text-[12px] break-all text-platinum">{w.id}</div>
        </div>
        <button type="button" onClick={onClose} className="shrink-0 rounded-full border border-line-2 px-2 py-0.5 text-[11px] text-chrome hover:text-platinum" aria-label="Clear selection">
          Close
        </button>
      </div>
      <dl className="mt-3 space-y-2 text-[12px] text-chrome">
        <div>
          <dt className="text-dim">Status</dt>
          <dd>{w.status === null ? <LabelledValue label="Unavailable" reason="status not in the windows file" /> : `${w.status}${w.reasons.length > 0 ? ` · ${w.reasons.join(', ')}` : ''}`}</dd>
        </div>
        <div>
          <dt className="text-dim">Terrain relief</dt>
          <dd>
            {r === null || r.evidenceLabel === 'Unavailable' ? (
              <LabelledValue label="Unavailable" reason={w.reasons.length > 0 ? `not computed: ${w.reasons.join(', ')}` : 'no relief fingerprint in the bundle'} source="windows file" />
            ) : (
              <LabelledValue label={r.evidenceLabel} value={r.valueM} unit="m" source={`ν at ${r.lagM} m lag, windows file terrain fingerprint`} />
            )}
          </dd>
        </div>
        {w.comparedWith.length > 0 ? (
          <div>
            <dt className="text-dim">Compared with</dt>
            <dd>{w.comparedWith.join(', ')}</dd>
          </div>
        ) : null}
      </dl>
      {w.cardId !== null ? (
        <Link href={`/site/${w.cardId}`} className="btn-primary mt-4 inline-flex items-center gap-2 rounded-full px-4 py-2 text-[12.5px] no-underline">
          Open evidence card <ExternalLink size={12} aria-hidden="true" />
        </Link>
      ) : (
        <p className="mt-3 text-[11px] text-dim">No evidence card for this window in the bundle.</p>
      )}
    </div>
  );
}

/** A tiny planet from the real NASA mosaic in public/textures. */
function PlanetThumb({ body, on }: { readonly body: AtlasBody; readonly on: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={clsx('relative block size-[22px] shrink-0 rounded-full transition-[opacity,transform] duration-300', on ? 'scale-[1.08] opacity-100' : 'opacity-65')}
      style={{ backgroundImage: `url(${TEX[body]})`, backgroundSize: '200% 100%', boxShadow: 'inset -6px -2px 8px rgba(0,0,0,0.85), inset 2px 1px 3px rgba(255,255,255,0.12)' }}
    />
  );
}

const WORLD_KICKER: Record<AtlasBody, string> = { earth: 'Analog ground', moon: 'Target body', mars: 'Target body' };

function WorldTitle({ body, left, line, sub }: { readonly body: AtlasBody; readonly left: boolean; readonly line: string; readonly sub: string }) {
  const name = BODIES.find((b) => b.id === body)?.label ?? body;
  return (
    <div className={clsx('pointer-events-none absolute top-24 z-[550] hidden transition-[left] duration-500 lg:block', left ? 'left-[336px]' : 'left-8')}>
      <motion.div key={body} initial="hide" animate="show" variants={{ show: { transition: { staggerChildren: 0.05 } }, hide: {} }}>
        <motion.div variants={{ hide: { opacity: 0, y: 8 }, show: { opacity: 1, y: 0 } }} className="font-mono text-[10.5px] tracking-[0.26em] text-chrome uppercase">
          {WORLD_KICKER[body]}
        </motion.div>
        <h1 className="mt-1 flex overflow-hidden text-[clamp(56px,6.4vw,104px)] leading-[0.9] font-medium tracking-[-0.05em] text-platinum">
          {name.split('').map((ch, i) => (
            <motion.span key={i} variants={{ hide: { y: '100%', opacity: 0 }, show: { y: '0%', opacity: 1 } }} transition={{ duration: 0.7, ease: EASE }}>
              {ch}
            </motion.span>
          ))}
        </h1>
        <motion.div variants={{ hide: { opacity: 0 }, show: { opacity: 1 } }} transition={{ delay: 0.3 }} className="mt-2 max-w-[340px] text-[12.5px] leading-relaxed text-chrome">
          {line}
          <span className="block font-mono text-[11px] text-dim">{sub}</span>
        </motion.div>
      </motion.div>
    </div>
  );
}

/** Floating glass panel on wide screens; a normal block below the stage on small ones. */
function Panel({ side, open, onToggle, label, children }: { readonly side: 'left' | 'right'; readonly open: boolean; readonly onToggle: () => void; readonly label: string; readonly children: ReactNode }) {
  const left = side === 'left';
  return (
    <>
      <aside
        style={{ ['--px' as string]: left ? '-24px' : '24px' }}
        className={clsx(
          'relative z-[700] min-w-0 border-line bg-void transition-[opacity,transform] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)] [scrollbar-width:thin] lg:absolute lg:top-4 lg:bottom-4 lg:overflow-y-auto lg:rounded-3xl lg:border lg:bg-panel/80 lg:shadow-[0_40px_120px_-40px_rgba(0,0,0,0.85)] lg:backdrop-blur-xl',
          left ? 'lg:left-4 lg:w-[304px]' : 'lg:right-4 lg:w-[384px]',
          !open && 'lg:pointer-events-none lg:translate-x-[var(--px)] lg:opacity-0',
        )}
        aria-label={label}
        inert={!open ? true : undefined}
      >
        <button type="button" onClick={onToggle} className={clsx('absolute top-3 z-10 hidden size-8 place-items-center rounded-full border border-line-2 bg-void/70 text-chrome hover:text-platinum lg:grid', left ? 'right-3' : 'left-3')} aria-label={`Hide ${label.toLowerCase()}`}>
          {left ? <ChevronLeft size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
        </button>
        <div className={clsx(left ? 'lg:pr-8' : 'lg:pl-8')}>{children}</div>
      </aside>
      <AnimatePresence>
        {!open && (
          <motion.button
            type="button"
            initial={{ opacity: 0, x: left ? -10 : 10 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0 }}
            onClick={onToggle}
            className={clsx('absolute top-1/2 z-[700] hidden -translate-y-1/2 items-center gap-2 rounded-full border border-line-2 bg-void/80 px-3 py-2 text-[12px] text-chrome backdrop-blur-xl hover:text-platinum lg:flex', left ? 'left-4' : 'right-4')}
          >
            {left ? <ChevronRight size={14} aria-hidden="true" /> : <ChevronLeft size={14} aria-hidden="true" />} {label}
          </motion.button>
        )}
      </AnimatePresence>
    </>
  );
}

function previewUrl(l: MapLayer): string {
  const merc = l.projection === 'mercator';
  return l.url
    .replace('{z}', merc ? '2' : '1')
    .replace('{x}', merc ? '2' : '1')
    .replace('{y}', merc ? '1' : '0');
}

function LayerCard({ name, sub, resolution, thumb, on, onClick }: { readonly name: string; readonly sub: string; readonly resolution: string; readonly thumb: string; readonly on: boolean; readonly onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={on} className={clsx('group flex w-full items-center gap-3 rounded-xl border p-2 text-left transition-colors duration-150', on ? 'border-flare/50 bg-flare/[0.06]' : 'border-line hover:border-line-2')}>
      <span className="relative h-11 w-16 shrink-0 overflow-hidden rounded-lg bg-ink">
        {/* An <img> with CORS, not a CSS background: the site's COEP blocks no-cors cross-origin images. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={thumb} alt="" crossOrigin="anonymous" loading="lazy" decoding="async" className="absolute inset-0 size-full object-cover transition-transform duration-700 group-hover:scale-110" />
        {on && <span className="absolute inset-0 rounded-lg ring-2 ring-flare/70 ring-inset" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center justify-between gap-2">
          <span className="line-clamp-2 text-[12.5px] leading-snug text-platinum" title={name}>
            {name}
          </span>
          <span className="shrink-0 font-mono text-[10px] text-dim">{resolution}</span>
        </span>
        <span className="mt-0.5 block truncate text-[11px] text-dim">{sub}</span>
      </span>
    </button>
  );
}

function Section({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  return (
    <section>
      <h2 className="mb-3 font-mono text-[10px] tracking-[0.2em] text-dim uppercase">{title}</h2>
      {children}
    </section>
  );
}

function Chip({ on, onClick, children }: { readonly on: boolean; readonly onClick: () => void; readonly children: ReactNode }) {
  return (
    <button type="button" aria-pressed={on} onClick={onClick} className={clsx('rounded-full border px-3 py-1.5 text-[12px] transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-ring', on ? 'border-flare/60 bg-flare/10 text-platinum' : 'border-line-2 text-chrome hover:border-chrome')}>
      {children}
    </button>
  );
}

function ViewBtn({ on, onClick, icon, label }: { readonly on: boolean; readonly onClick: () => void; readonly icon: ReactNode; readonly label: string }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={on} className={clsx('flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[12px] transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-ring', on ? 'bg-platinum text-void' : 'text-chrome hover:text-platinum')}>
      {icon}
      {label}
    </button>
  );
}

function Key({ color, label, dashed }: { readonly color: string; readonly label: string; readonly dashed?: boolean }) {
  return (
    <span className="flex items-center gap-1.5">
      <span aria-hidden="true" className={clsx('size-2.5 shrink-0 rounded-full', dashed && 'border border-dashed')} style={dashed ? { borderColor: color } : { background: color }} />
      {label}
    </span>
  );
}

function StageLoading({ label }: { readonly label: string }) {
  return (
    <div className="grid h-full place-items-center">
      <div className="flex items-center gap-3 font-mono text-[11px] tracking-widest text-chrome uppercase">
        <span className="size-2 animate-ping rounded-full bg-earth" /> {label}
      </div>
    </div>
  );
}
