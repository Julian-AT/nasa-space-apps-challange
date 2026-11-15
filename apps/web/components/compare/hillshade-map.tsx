'use client';
// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// One Leaflet map of a bundle hillshade tile layer in the layer's own projected frame (Moon south
// polar stereographic, Mars local AEQD, Earth Web Mercator), with the 5 km window outlined. The
// view is exchanged with the other pane as (offset from the window centre, ground metres per
// pixel), so linked views match ground scale across frames. Load this module only through
// next/dynamic with ssr: false: Leaflet needs the browser.
import 'leaflet/dist/leaflet.css';

import L from 'leaflet';
import { type Ref, useEffect, useImperativeHandle, useRef } from 'react';

import type { PaneLayer } from './lab-data.ts';

/** A linked view: ground metres east/north of the window centre and ground metres per pixel. */
export interface LinkedView {
  readonly dx: number;
  readonly dy: number;
  readonly mpp: number;
}

export interface HillshadeMapHandle {
  setLinkedView: (v: LinkedView) => void;
  reset: () => void;
}

interface Props {
  readonly pane: PaneLayer;
  readonly label: string;
  readonly onView?: (v: LinkedView) => void;
  /** A plain prop, not ref: next/dynamic wrappers pass it through untouched. */
  readonly control?: Ref<HillshadeMapHandle>;
}

function frameCrs(p: PaneLayer): L.CRS {
  // Map "latlng" = (y, x) metres of the layer frame; pixel = (x - origin_x, origin_y - y) / mpp(z),
  // which is exactly the bundle's XYZ tile grid (y down from origin_y).
  return L.Util.extend({}, L.CRS.Simple, {
    transformation: new L.Transformation(1, -p.originX, -1, p.originY),
    scale: (z: number) => 2 ** (z - p.zoomMin) / p.mppZoomMin,
    zoom: (s: number) => Math.log2(s * p.mppZoomMin) + p.zoomMin,
    infinite: true,
  }) as L.CRS;
}

const groundMpp = (p: PaneLayer, z: number): number => (p.mppZoomMin * 2 ** -(z - p.zoomMin)) / p.scaleFactor;
const zoomForGround = (p: PaneLayer, mpp: number): number => p.zoomMin + Math.log2(p.mppZoomMin / (mpp * p.scaleFactor));

export function HillshadeMap({ pane, label, onView, control }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const silent = useRef(false);
  const cb = useRef(onView);
  cb.current = onView;

  useEffect(() => {
    const el = host.current;
    if (el === null) return;
    const [minX, minY, maxX, maxY] = pane.extent;
    const map = L.map(el, {
      crs: frameCrs(pane),
      zoomControl: false,
      attributionControl: false,
      zoomSnap: 0,
      zoomDelta: 0.5,
      scrollWheelZoom: false,
      keyboard: true,
      minZoom: pane.zoomMin - 3,
      maxZoom: pane.zoomMax + 3,
    });
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    L.tileLayer(pane.url, {
      tileSize: pane.tilePx,
      minNativeZoom: pane.zoomMin,
      maxNativeZoom: pane.zoomMax,
      minZoom: pane.zoomMin - 3,
      maxZoom: pane.zoomMax + 3,
      noWrap: true,
      bounds: L.latLngBounds([minY, minX], [maxY, maxX]),
    }).addTo(map);
    const ring = pane.ring.map(([x, y]) => L.latLng(y, x));
    L.polygon(ring, { color: '#f2c879', weight: 2, fill: false, interactive: false }).addTo(map);
    const fit = (): void => {
      map.fitBounds(L.latLngBounds(ring), { padding: [Math.max(24, el.clientWidth / 3), Math.max(24, el.clientHeight / 3)], animate: false });
    };
    const emit = (): void => {
      if (silent.current) return;
      const c = map.getCenter();
      cb.current?.({
        dx: (c.lng - pane.centre[0]) / pane.scaleFactor,
        dy: (c.lat - pane.centre[1]) / pane.scaleFactor,
        mpp: groundMpp(pane, map.getZoom()),
      });
    };
    mapRef.current = map;
    silent.current = true;
    fit();
    silent.current = false;
    map.on('moveend zoomend', emit);
    const onWheel = (e: WheelEvent): void => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      const z = Math.min(map.getMaxZoom(), Math.max(map.getMinZoom(), map.getZoom() - e.deltaY / 180));
      map.setZoomAround(map.mouseEventToContainerPoint(e as unknown as MouseEvent), z, { animate: false });
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    const ro = new ResizeObserver(() => {
      map.invalidateSize({ animate: false });
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      el.removeEventListener('wheel', onWheel);
      map.off();
      map.remove();
      mapRef.current = null;
    };
  }, [pane]);

  useImperativeHandle(
    control,
    () => ({
      setLinkedView: (v) => {
        const map = mapRef.current;
        if (map === null) return;
        silent.current = true;
        map.setView(L.latLng(pane.centre[1] + v.dy * pane.scaleFactor, pane.centre[0] + v.dx * pane.scaleFactor), zoomForGround(pane, v.mpp), { animate: false });
        silent.current = false;
      },
      reset: () => {
        const map = mapRef.current;
        if (map === null) return;
        const ring = pane.ring.map(([x, y]) => L.latLng(y, x));
        const el = map.getContainer();
        map.fitBounds(L.latLngBounds(ring), { padding: [Math.max(24, el.clientWidth / 3), Math.max(24, el.clientHeight / 3)], animate: false });
      },
    }),
    [pane],
  );

  return (
    <div
      ref={host}
      role="region"
      aria-label={`${label} hillshade map. Arrow keys pan, plus and minus zoom.`}
      className="size-full"
      style={{ background: 'var(--void)' }}
    />
  );
}

export default HillshadeMap;
