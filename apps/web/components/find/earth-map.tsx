'use client';

// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The Earth picker map of /find (ShadowAstra's BodyMap, Earth only). Loaded with next/dynamic and
// ssr:false so Leaflet stays out of the server render and the landing bundle. A click drops the
// pin; the outline is the probe's 5 km window drawn as a local square (approximate on the map).
import 'leaflet/dist/leaflet.css';
import './find-map.css';

import L from 'leaflet';
import { useEffect, useRef, useState } from 'react';

import { EARTH_LAYERS, type EarthLayer } from './layers.ts';

export interface MapMarker {
  id: string;
  lat: number;
  lon: number;
  label: string;
}

interface Props {
  layerId: string;
  pin: { lat: number; lon: number } | null;
  markers: readonly MapMarker[];
  onPick: (lat: number, lon: number) => void;
  onLayerBlocked: (layerId: string) => void;
}

const LAYER_BY_ID = Object.fromEntries(EARTH_LAYERS.map((l) => [l.id, l])) as Record<string, EarthLayer>;
const HALF_WINDOW_KM = 2.5;

function pinIcon(active: boolean, colour: string): L.DivIcon {
  return L.divIcon({
    className: '',
    iconSize: [18, 18],
    iconAnchor: [9, 9],
    html: `<div class="sa-pin ${active ? 'active' : ''}" style="--pin:${colour}"><span class="ring"></span><span class="core"></span></div>`,
  });
}

function windowSquare(lat: number, lon: number): L.LatLngExpression[] {
  const dLat = HALF_WINDOW_KM / 111.32;
  const dLon = HALF_WINDOW_KM / (111.32 * Math.max(0.01, Math.cos((lat * Math.PI) / 180)));
  return [
    [lat - dLat, lon - dLon],
    [lat - dLat, lon + dLon],
    [lat + dLat, lon + dLon],
    [lat + dLat, lon - dLon],
  ];
}

export default function EarthMap({ layerId, pin, markers, onPick, onLayerBlocked }: Readonly<Props>) {
  const host = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const baseRef = useRef<L.Layer | null>(null);
  const markerLayer = useRef<L.LayerGroup | null>(null);
  const pinLayer = useRef<L.LayerGroup | null>(null);
  const cb = useRef({ onPick, onLayerBlocked });
  cb.current = { onPick, onLayerBlocked };
  const pinRef = useRef(pin);
  pinRef.current = pin;
  const [ready, setReady] = useState(0);

  const layer = LAYER_BY_ID[layerId] ?? (EARTH_LAYERS[0] as EarthLayer);
  const projection = layer.projection;

  // (Re)create the map whenever the projection changes: each needs its own CRS.
  useEffect(() => {
    const el = host.current;
    if (el === null) return;
    const map = L.map(el, {
      crs: projection === 'mercator' ? L.CRS.EPSG3857 : L.CRS.EPSG4326,
      zoomControl: false,
      attributionControl: true,
      worldCopyJump: projection === 'mercator',
      zoomSnap: 0.25,
      zoomDelta: 0.5,
      keyboard: true,
      scrollWheelZoom: false,
      minZoom: projection === 'mercator' ? 1.5 : 1,
      maxZoom: projection === 'mercator' ? 14 : 8,
      maxBounds: projection === 'equirect' ? L.latLngBounds([-90, -180], [90, 180]) : undefined,
      maxBoundsViscosity: 0.8,
    });
    L.control.zoom({ position: 'topright' }).addTo(map);
    map.attributionControl.setPrefix(false);
    markerLayer.current = L.layerGroup().addTo(map);
    pinLayer.current = L.layerGroup().addTo(map);
    const p = pinRef.current;
    if (p === null) map.setView([25, 0], projection === 'mercator' ? 2 : 1.5);
    else map.setView([p.lat, p.lon], projection === 'mercator' ? 9 : 4);
    map.on('click', (e: L.LeafletMouseEvent) => {
      const ll = e.latlng.wrap();
      cb.current.onPick(Math.max(-90, Math.min(90, ll.lat)), ll.lng);
    });
    mapRef.current = map;
    const ro = new ResizeObserver(() => {
      map.invalidateSize();
    });
    ro.observe(el);
    setReady((n) => n + 1);
    return () => {
      ro.disconnect();
      map.off();
      map.remove();
      mapRef.current = null;
      baseRef.current = null;
    };
  }, [projection]);

  // Base layer
  useEffect(() => {
    const map = mapRef.current;
    if (map === null) return;
    if (baseRef.current !== null) map.removeLayer(baseRef.current);
    const attribution = `${layer.provider} · ${layer.instrument}`;
    if (layer.image === true) {
      baseRef.current = L.imageOverlay(layer.url, [[-90, -180], [90, 180]], { attribution }).addTo(map);
    } else {
      let errors = 0;
      const tiles = L.tileLayer(layer.url, {
        attribution,
        maxNativeZoom: layer.maxNativeZoom,
        maxZoom: 14,
        crossOrigin: true,
      });
      tiles.on('tileerror', () => {
        errors += 1;
        if (errors === 3) cb.current.onLayerBlocked(layer.id);
      });
      tiles.on('tileload', () => {
        errors = -1_000_000;
      });
      baseRef.current = tiles.addTo(map);
    }
  }, [layer, ready]);

  // Example markers
  useEffect(() => {
    const group = markerLayer.current;
    if (group === null) return;
    group.clearLayers();
    for (const m of markers) {
      L.marker([m.lat, m.lon], { icon: pinIcon(false, '#8fb4ea'), keyboard: true, title: m.label })
        .bindTooltip(m.label, { className: 'sa-tip', direction: 'top', offset: [0, -10] })
        .on('click', () => {
          cb.current.onPick(m.lat, m.lon);
        })
        .addTo(group);
    }
  }, [markers, ready]);

  // The dropped pin and its 5 km window
  useEffect(() => {
    const group = pinLayer.current;
    const map = mapRef.current;
    if (group === null || map === null) return;
    group.clearLayers();
    if (pin === null) return;
    const square = windowSquare(pin.lat, pin.lon);
    L.polygon(square, { color: '#07080a', opacity: 0.6, weight: 5, fill: false, interactive: false }).addTo(group);
    L.polygon(square, { color: '#f2c879', weight: 2, dashArray: '6 4', fillColor: '#f2c879', fillOpacity: 0.12, interactive: false }).addTo(group);
    L.marker([pin.lat, pin.lon], { icon: pinIcon(true, '#f2c879'), keyboard: true, title: 'Probe point', zIndexOffset: 1000 }).addTo(group);
    if (!map.getBounds().pad(-0.1).contains([pin.lat, pin.lon])) map.panTo([pin.lat, pin.lon]);
  }, [pin, ready]);

  return (
    <div className="relative size-full">
      <div ref={host} className="absolute inset-0" role="application" aria-label="Earth map: click or press Enter on a marker to choose the probe point. Arrow keys pan, plus and minus zoom." />
    </div>
  );
}
