// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The 2D atlas map (PRD 8.6, 8.10, 10). Leaflet with NASA imagery basemaps for global context,
// the bundle's own hillshade pyramids, and the window polygons from windows/<body>.json. Three
// frames: Web Mercator (Earth, where the bundle tiles are EPSG:3857 XYZ and sit on top as an
// overlay), equirectangular (NASA Trek Moon/Mars mosaics, lon/lat window rings), and "projected":
// the target's own metre frame read from TargetRecord.tile_layers (polar stereographic for the
// Moon, local AEQD for Mars), where one Leaflet unit is one tile pixel at zoom 0. This module is
// only ever imported through next/dynamic with ssr:false, so Leaflet stays off the landing bundle.
'use client';

import 'leaflet/dist/leaflet.css';
import './body-map.css';

import L from 'leaflet';
import { useEffect, useRef, useState } from 'react';

import type { BundleTileLayer } from './atlas-data.ts';
import type { MapLayer } from './layers.ts';

export type MapFrame = 'mercator' | 'equirect' | 'projected';

/** A polygon in map coordinates ([lat, lng]; in the projected frame lat = -unitY, lng = unitX). */
export interface MapShape {
  readonly id: string;
  readonly ring: readonly (readonly [number, number])[];
  readonly status: 'eligible' | 'rejected' | 'unknown' | null;
  readonly highlight: 'primary' | 'card' | null;
  readonly label: string;
}

export interface MapPin {
  readonly id: string;
  readonly at: readonly [number, number];
  readonly color: string;
  readonly label: string;
}

/** A fly-to request; a new nonce re-runs it even when the place is the same. */
export interface MapFocus {
  readonly at: readonly [number, number];
  readonly nonce: number;
}

interface Props {
  readonly frame: MapFrame;
  readonly base: MapLayer | null;
  /** In the projected frame the base; in the Mercator frame an overlay. */
  readonly bundleLayers: readonly BundleTileLayer[];
  readonly shapes: readonly MapShape[];
  readonly pins: readonly MapPin[];
  readonly activeId: string | null;
  readonly onSelect: (id: string) => void;
  readonly initial?: { readonly at: readonly [number, number]; readonly zoom: number } | undefined;
  readonly focus: MapFocus | null;
}

const STATUS_STYLE: Record<'eligible' | 'rejected' | 'unknown', L.PathOptions> = {
  eligible: { color: '#34d399', fillColor: '#34d399', dashArray: undefined },
  rejected: { color: '#fbbf24', fillColor: '#fbbf24', dashArray: '6 4' },
  unknown: { color: '#94a3b8', fillColor: '#94a3b8', dashArray: '2 4' },
};

function pinIcon(p: MapPin, active: boolean) {
  return L.divIcon({
    className: '',
    iconSize: [18, 18],
    iconAnchor: [9, 9],
    html: `<div class="tl-pin${active ? ' active' : ''}" style="--pin:${p.color}"><span class="ring"></span><span class="core"></span></div>`,
  });
}

function niceMetres(m: number): { value: number; label: string } {
  const pow = 10 ** Math.floor(Math.log10(m));
  const step = [1, 2, 5, 10].find((s) => s * pow >= m * 0.6) ?? 10;
  const value = step * pow;
  return { value, label: value >= 1000 ? `${String(value / 1000)} km` : `${String(value)} m` };
}

export function BodyMap({ frame, base, bundleLayers, shapes, pins, activeId, onSelect, initial, focus }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const tileRefs = useRef<L.Layer[]>([]);
  const shapeLayer = useRef<L.LayerGroup | null>(null);
  const pinLayer = useRef<L.LayerGroup | null>(null);
  const cb = useRef(onSelect);
  cb.current = onSelect;
  const [scale, setScale] = useState<{ px: number; label: string } | null>(null);
  const projected = frame === 'projected' ? (bundleLayers[0] ?? null) : null;
  const projectedKey = projected?.id ?? '';

  // (Re)create the map whenever the frame changes; each frame needs its own CRS.
  useEffect(() => {
    if (!host.current) return;
    const crs = frame === 'mercator' ? L.CRS.EPSG3857 : frame === 'equirect' ? L.CRS.EPSG4326 : L.CRS.Simple;
    const map = L.map(host.current, {
      crs,
      zoomControl: false,
      attributionControl: true,
      worldCopyJump: frame === 'mercator',
      zoomSnap: 0,
      zoomDelta: 0.5,
      wheelPxPerZoomLevel: 90,
      keyboard: true,
      minZoom: frame === 'projected' ? (projected?.zoomMin ?? 0) - 3 : frame === 'mercator' ? 1.5 : 0.5,
      maxZoom: frame === 'projected' ? (projected?.zoomMax ?? 2) + 2 : frame === 'mercator' ? 18.5 : 14,
      maxBounds: frame === 'equirect' ? L.latLngBounds([-90, -180], [90, 180]) : undefined,
      maxBoundsViscosity: 0.8,
    });
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    if (frame === 'mercator') L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(map);
    map.attributionControl.setPrefix(false);
    shapeLayer.current = L.layerGroup().addTo(map);
    pinLayer.current = L.layerGroup().addTo(map);

    if (frame === 'projected' && projected !== null) {
      const [x0, y0, x1, y1] = projected.extentM;
      const w = Math.abs(x1 - x0) / projected.metresPerUnit;
      const h = Math.abs(y1 - y0) / projected.metresPerUnit;
      map.fitBounds(L.latLngBounds([-h, 0], [0, w]), { padding: [24, 24], animate: false });
    } else if (initial) {
      map.setView([initial.at[0], initial.at[1]], initial.zoom);
    } else {
      map.setView([15, 0], frame === 'mercator' ? 2 : 1.4);
    }

    let alive = true;
    const emit = () => {
      if (!alive || frame !== 'projected' || projected === null) return;
      const mpp = projected.metresPerUnit / 2 ** map.getZoom();
      const nice = niceMetres(mpp * 110);
      setScale({ px: nice.value / mpp, label: nice.label });
    };
    map.on('zoomend moveend', emit);
    mapRef.current = map;
    const raf = requestAnimationFrame(() => {
      if (!alive) return;
      map.invalidateSize();
      emit();
    });
    const ro = new ResizeObserver(() => {
      if (alive) map.invalidateSize();
    });
    ro.observe(host.current);
    return () => {
      alive = false;
      cancelAnimationFrame(raf);
      ro.disconnect();
      map.stop();
      shapeLayer.current?.clearLayers();
      pinLayer.current?.clearLayers();
      shapeLayer.current = null;
      pinLayer.current = null;
      map.off();
      map.remove();
      mapRef.current = null;
      tileRefs.current = [];
      setScale(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame, projectedKey]);

  // Tile layers: NASA base (Mercator, equirect), bundle pyramids (Mercator overlay or projected base).
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    for (const l of tileRefs.current) map.removeLayer(l);
    tileRefs.current = [];
    if (frame !== 'projected' && base !== null) {
      const tiles = (l: MapLayer, extra: L.TileLayerOptions = {}) =>
        L.tileLayer(l.url, {
          attribution: `${l.provider} · ${l.instrument}`,
          tileSize: l.tileSize ?? 256,
          zoomOffset: l.zoomOffset ?? 0,
          maxNativeZoom: l.maxNativeZoom,
          maxZoom: 20,
          noWrap: l.projection === 'equirect',
          bounds: l.projection === 'equirect' ? L.latLngBounds([-90, -180], [90, 180]) : undefined,
          crossOrigin: true,
          ...extra,
        });
      const b = tiles(base).addTo(map);
      b.bringToBack();
      tileRefs.current.push(b);
    }
    for (const bl of bundleLayers) {
      if (frame === 'mercator' && bl.kind === 'mercator') {
        const [x0, y0, x1, y1] = bl.extentM;
        const sw = L.Projection.SphericalMercator.unproject(L.point(Math.min(x0, x1), Math.min(y0, y1)));
        const ne = L.Projection.SphericalMercator.unproject(L.point(Math.max(x0, x1), Math.max(y0, y1)));
        // 512 px XYZ tiles: tile zoom t covers what a 256 px tile covers at map zoom t + 1.
        const t = L.tileLayer(bl.urlTemplate, {
          tileSize: bl.tilePx,
          zoomOffset: -Math.log2(bl.tilePx / 256),
          minNativeZoom: bl.zoomMin + Math.log2(bl.tilePx / 256),
          maxNativeZoom: bl.zoomMax + Math.log2(bl.tilePx / 256),
          maxZoom: 20,
          bounds: L.latLngBounds(sw, ne),
          opacity: 0.85,
          className: 'tl-bundle-tiles',
          attribution: `Terraluna bundle · ${bl.layer} (${bl.evidenceLabel})`,
        }).addTo(map);
        tileRefs.current.push(t);
      }
      if (frame === 'projected' && projected !== null && bl.id === projected.id) {
        const [x0, y0, x1, y1] = bl.extentM;
        const w = Math.abs(x1 - x0) / bl.metresPerUnit;
        const h = Math.abs(y1 - y0) / bl.metresPerUnit;
        const t = L.tileLayer(bl.urlTemplate, {
          tileSize: bl.tilePx,
          minNativeZoom: bl.zoomMin,
          maxNativeZoom: bl.zoomMax,
          minZoom: bl.zoomMin - 3,
          maxZoom: bl.zoomMax + 2,
          noWrap: true,
          bounds: L.latLngBounds([-h, 0], [0, w]),
          className: 'tl-bundle-tiles',
          attribution: `Terraluna bundle · ${bl.layer} (${bl.evidenceLabel}) · ${bl.units}`,
        }).addTo(map);
        tileRefs.current.push(t);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, bundleLayers, frame, projectedKey]);

  // Window polygons (SVG; a few hundred at most).
  useEffect(() => {
    const group = shapeLayer.current;
    if (!group) return;
    group.clearLayers();
    for (const s of shapes) {
      const active = s.id === activeId;
      const tone = STATUS_STYLE[s.status ?? 'unknown'];
      const ring = s.ring.map(([la, lo]) => L.latLng(la, lo));
      L.polygon(ring, {
        ...tone,
        color: active ? '#edeae4' : s.highlight !== null ? '#ffffff' : tone.color,
        weight: active ? 3 : s.highlight !== null ? 2.2 : 1,
        opacity: active ? 1 : 0.85,
        fillOpacity: active ? 0.22 : s.highlight !== null ? 0.14 : 0.06,
      })
        .bindTooltip(s.label, { className: 'tl-tip', sticky: true })
        .on('click', () => {
          cb.current(s.id);
        })
        .addTo(group);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shapes, activeId, frame, projectedKey]);

  // Pins.
  useEffect(() => {
    const group = pinLayer.current;
    if (!group) return;
    group.clearLayers();
    for (const p of pins) {
      const active = p.id === activeId;
      L.marker(L.latLng(p.at[0], p.at[1]), { icon: pinIcon(p, active), zIndexOffset: active ? 1000 : 0, keyboard: true, title: p.label, alt: p.label })
        .bindTooltip(p.label, { className: 'tl-tip', direction: 'top', offset: [0, -10] })
        .on('click', () => {
          cb.current(p.id);
        })
        .addTo(group);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pins, activeId, frame, projectedKey]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || focus === null) return;
    const z = frame === 'projected' ? Math.max(map.getZoom(), (projected?.zoomMin ?? 0) + 1) : frame === 'mercator' ? 9 : 7;
    map.stop();
    map.flyTo(L.latLng(focus.at[0], focus.at[1]), z, { duration: 1.1 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.nonce]);

  return (
    <div className="relative h-full w-full">
      <div ref={host} className="tl-map absolute inset-0" aria-label="2D map. Arrow keys pan, plus and minus zoom." />
      {scale === null ? null : (
        <div className="pointer-events-none absolute bottom-3 left-3 z-[500] flex flex-col gap-1">
          <div className="font-mono text-[10px] tracking-wider text-chrome uppercase">{scale.label}</div>
          <div className="h-1.5 border-x border-b border-platinum/80" style={{ width: Math.round(scale.px) }} />
        </div>
      )}
    </div>
  );
}

export default BodyMap;
