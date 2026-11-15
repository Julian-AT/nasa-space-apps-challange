'use client';

// Deep zoom for the finder slide: after the globe dolly, the round lens crossfades into stacked
// Web-Mercator mosaics of EOX Sentinel-2 cloudless 2020 and keeps zooming to the card's Earth
// window, then draws that window true to scale. Drawn on a devicePixelRatio-sized canvas; each
// level is a tile zoom one step finer than the view zoom it serves, so tiles are shown at about
// half a CSS pixel (one device pixel on 2x screens) and stay crisp. Imagery only, no measurement.
import { ANALYSIS_GRID } from '@terraluna/core';
import { motion } from 'motion/react';
import { useEffect, useRef, useState } from 'react';

const TILE = 256;
const TILE_URL = (z: number, x: number, y: number) =>
  `https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/GoogleMapsCompatible/${z}/${y}/${x}.jpg`;
/** Tile zooms of the stacked mosaics, coarse to fine (EOX serves this layer to about z14). */
const LEVELS = [7, 9, 11, 13] as const;
/** View zoom at the crossfade from the globe (~1,900 km across the lens, matching the dolly). */
const Z_START = 5;
/** The analysis window fills this fraction of the lens width at the end. */
const WINDOW_FILL = 1 / 3;
export const DEEP_FADE_MS = 400;
export const DEEP_ZOOM_MS = 2200;
/** Web Mercator ground resolution, m per pixel at zoom z. */
const groundRes = (latDeg: number, z: number) => (156543.03 * Math.cos((latDeg * Math.PI) / 180)) / 2 ** z;

/** Web Mercator world pixel (x, y) of a point at zoom z. */
function worldPx(latDeg: number, lonDeg: number, z: number): [number, number] {
  const n = TILE * 2 ** z;
  const s = Math.sin((latDeg * Math.PI) / 180);
  return [((lonDeg + 180) / 360) * n, (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n];
}

interface Tile {
  readonly z: number;
  /** Column, unwrapped across the antimeridian for placement. */
  readonly x: number;
  readonly y: number;
  readonly img: HTMLImageElement;
  ok: boolean;
}

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
/** Level opacity at view zoom Z: fades in over [zl - 2, zl - 1.7], where it already covers the lens. */
const levelAlpha = (zl: number, Z: number, first: boolean) => (first ? 1 : clamp01((Z - (zl - 2)) / 0.3));

export function DeepZoom({
  lat,
  lon,
  label,
  play,
  done,
  reduce,
}: {
  readonly lat: number;
  readonly lon: number;
  readonly label: string;
  /** Crossfade in and zoom (true from the end of the globe dolly). */
  readonly play: boolean;
  /** Show the true-to-scale window marker. */
  readonly done: boolean;
  readonly reduce: boolean;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const tiles = useRef<Tile[]>([]);
  const zNow = useRef(Z_START);
  const [size, setSize] = useState(0);

  // the lens diameter in CSS px
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize(el.clientWidth));
    ro.observe(el);
    setSize(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const zEnd = size > 0 ? Math.log2((156543.03 * Math.cos((lat * Math.PI) / 180) * size * WINDOW_FILL) / ANALYSIS_GRID.widthM) : 12;
  const boxPx = size > 0 ? ANALYSIS_GRID.widthM / groundRes(lat, zEnd) : 0;

  const draw = useRef<() => void>(() => undefined);
  draw.current = () => {
    const c = canvas.current;
    if (!c || size === 0) return;
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    const w = Math.round(size * dpr);
    if (c.width !== w) {
      c.width = w;
      c.height = w;
    }
    const ctx = c.getContext('2d');
    if (!ctx) return;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.fillStyle = '#0d1a26';
    ctx.fillRect(0, 0, w, w);
    const Z = zNow.current;
    const half = w / 2;
    // skip levels hidden under a finer level that is fully loaded and opaque
    let from = 0;
    LEVELS.forEach((zl, i) => {
      if (i > 0 && levelAlpha(zl, Z, false) >= 1 && tiles.current.every((t) => t.z !== zl || t.ok)) from = i;
    });
    for (let i = from; i < LEVELS.length; i += 1) {
      const zl = LEVELS[i] as number;
      const a = levelAlpha(zl, Z, i === 0);
      if (a <= 0) continue;
      const k = 2 ** (Z - zl) * dpr; // device px per tile px
      const [cx, cy] = worldPx(lat, lon, zl);
      ctx.globalAlpha = a;
      for (const t of tiles.current) {
        if (t.z !== zl || !t.ok) continue;
        const dx = half + (t.x * TILE - cx) * k;
        const dy = half + (t.y * TILE - cy) * k;
        const s = TILE * k;
        if (dx > w || dy > w || dx + s < 0 || dy + s < 0) continue;
        // overdraw by a hair so tile seams never show through the dark backing
        ctx.drawImage(t.img, dx - 0.25, dy - 0.25, s + 0.5, s + 0.5);
      }
    }
    ctx.globalAlpha = 1;
  };

  // preload every mosaic when the slide mounts: tiles covering the lens from each level's fade-in
  useEffect(() => {
    if (size === 0) return;
    let live = true;
    const r = size / 2;
    const list: Tile[] = [];
    LEVELS.forEach((zl, i) => {
      const zIn = i === 0 ? Z_START : zl - 2;
      const reach = r / 2 ** (zIn - zl) + TILE * 0.5; // tile px from the target to the lens rim, plus margin
      const [cx, cy] = worldPx(lat, lon, zl);
      const n = 2 ** zl;
      for (let ty = Math.floor((cy - reach) / TILE); ty <= Math.floor((cy + reach) / TILE); ty += 1) {
        for (let tx = Math.floor((cx - reach) / TILE); tx <= Math.floor((cx + reach) / TILE); tx += 1) {
          if (ty < 0 || ty >= n) continue;
          // nearest point of the tile to the target, kept only if inside the lens disc
          const nx = Math.max(tx * TILE, Math.min(cx, (tx + 1) * TILE));
          const ny = Math.max(ty * TILE, Math.min(cy, (ty + 1) * TILE));
          if (Math.hypot(nx - cx, ny - cy) > reach) continue;
          const img = new Image();
          img.crossOrigin = 'anonymous';
          img.decoding = 'async';
          const t: Tile = { z: zl, x: tx, y: ty, img, ok: false };
          img.src = TILE_URL(zl, ((tx % n) + n) % n, ty);
          img
            .decode()
            .then(() => {
              if (!live) return;
              t.ok = true;
              draw.current();
            })
            .catch(() => undefined);
          list.push(t);
        }
      }
    });
    tiles.current = list;
    return () => {
      live = false;
      tiles.current = [];
    };
  }, [lat, lon, size]);

  // the zoom: Z_START to zEnd, eased, after the crossfade; reduced motion jumps to the end
  useEffect(() => {
    if (!play) {
      zNow.current = Z_START;
      draw.current();
      return;
    }
    if (reduce) {
      zNow.current = zEnd;
      draw.current();
      return;
    }
    let raf = 0;
    const t0 = performance.now() + DEEP_FADE_MS;
    const tick = (now: number) => {
      const p = clamp01((now - t0) / DEEP_ZOOM_MS);
      zNow.current = Z_START + (zEnd - Z_START) * easeInOut(p);
      draw.current();
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [play, reduce, zEnd]);

  return (
    <motion.div
      ref={wrap}
      aria-hidden
      className="pointer-events-none absolute inset-0"
      initial={false}
      animate={{ opacity: play ? 1 : 0 }}
      transition={{ duration: reduce || !play ? 0 : DEEP_FADE_MS / 1000, ease: 'easeInOut' }}
    >
      <canvas ref={canvas} className="absolute inset-0 size-full" />
      {done && boxPx > 0 ? (
        <motion.div
          className="absolute left-1/2 top-1/2"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: reduce ? 0 : 0.45 }}
        >
          <span
            className="absolute left-0 top-0 block -translate-x-1/2 -translate-y-1/2 border-[1.5px] border-[#CC5800]"
            style={{ width: boxPx, height: boxPx, boxShadow: '0 0 0 1px rgba(255,255,255,0.35), 0 0 12px 1px rgba(255,138,42,0.65), inset 0 0 10px rgba(255,138,42,0.35)' }}
          />
          <motion.span
            className="absolute left-0 top-0 block size-5 rounded-full border-[1.5px] border-[#CC5800]"
            style={{ x: '-50%', y: '-50%' }}
            animate={reduce ? { opacity: 0.7 } : { scale: [1, 2.4], opacity: [0.9, 0] }}
            transition={reduce ? { duration: 0 } : { duration: 1.6, repeat: Infinity, ease: 'easeOut' }}
          />
          <span className="absolute left-0 top-0 block size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#CC5800]" />
          <span
            className="absolute whitespace-nowrap rounded-md bg-white/92 px-2 py-0.5 text-sm font-medium text-[#1A1613] shadow-[0_1px_2px_rgba(0,0,0,0.12)]"
            style={{ left: -boxPx / 2, top: boxPx / 2 + 8 }}
          >
            {label}
          </span>
        </motion.div>
      ) : null}
    </motion.div>
  );
}
