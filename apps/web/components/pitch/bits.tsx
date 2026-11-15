'use client';

// Small animated pieces of the /pitch deck: a count-up number and the nu(L) curve drawn from the
// stored fingerprint points (log-log, points exactly as stored, a line between stored lags only).
import { animate, motion, useMotionValue, useReducedMotion, useTransform } from 'motion/react';
import { type PointerEvent, useEffect, useState } from 'react';

import type { TileView } from '@/components/cards/card-visual';

import type { NuCurve } from './pitch-data';

/** Neutral shadcn palette: ink, muted captions, hairlines. Emphasis is weight, not colour. */
export const INK = '#1A1613';
export const MUTED = '#4D4843';
export const FAINT = '#A39E95';
export const ACCENT = '#CC5800';
export const PAPER = '#F9F8F5';
export const LINE = 'rgba(26,22,19,0.1)';
/** The only data colours: world tints, used where two series must be told apart. */
export const WORLD = { mars: '#CC5800', moon: '#A39E95', earth: '#1A1613' } as const;

export const EASE = [0.22, 1, 0.36, 1] as const;

export function CountUp({ value, digits = 0, delay = 0.2, duration = 1.4 }: { value: number; digits?: number; delay?: number; duration?: number }) {
  const reduce = useReducedMotion();
  const mv = useMotionValue(reduce ? value : 0);
  const text = useTransform(mv, (v) =>
    v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }),
  );
  useEffect(() => {
    if (reduce) return;
    const c = animate(mv, value, { duration, delay, ease: EASE });
    return () => c.stop();
  }, [mv, value, delay, duration, reduce]);
  return (
    <>
      <span className="sr-only">{value.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}</span>
      <motion.span aria-hidden className="tabular-nums">
        {text}
      </motion.span>
    </>
  );
}

const W = 640;
const H = 360;
const PAD = { l: 64, r: 24, t: 24, b: 52 };

export function NuChart({ curves, lags, colors = [WORLD.mars, WORLD.earth] }: { curves: readonly NuCurve[]; lags: readonly number[]; colors?: readonly string[] }) {
  const all = curves.flatMap((c) => c.points);
  const xs = all.map((p) => Math.log10(p.lag_m));
  const ys = all.map((p) => Math.log10(p.nu));
  const x0 = Math.min(...xs) - 0.05;
  const x1 = Math.max(...xs) + 0.05;
  const yPad = (Math.max(...ys) - Math.min(...ys)) * 0.25 + 0.05;
  const y0 = Math.min(...ys) - yPad;
  const y1 = Math.max(...ys) + yPad;
  const sx = (lag: number) => PAD.l + ((Math.log10(lag) - x0) / (x1 - x0)) * (W - PAD.l - PAD.r);
  const sy = (nu: number) => H - PAD.b - ((Math.log10(nu) - y0) / (y1 - y0)) * (H - PAD.t - PAD.b);
  const [hover, setHover] = useState<number | null>(null);
  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * W;
    let best = 0;
    lags.forEach((lag, k) => {
      if (Math.abs(sx(lag) - x) < Math.abs(sx(lags[best] ?? lag) - x)) best = k;
    });
    setHover(best);
  };
  const hl = hover === null ? null : lags[hover];
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-auto w-full cursor-crosshair touch-none"
      role="img"
      aria-label="Relief structure function nu(L), log-log, for the best-matching Mars and Earth windows"
      onPointerMove={onMove}
      onPointerDown={onMove}
      onPointerLeave={() => setHover(null)}
      onClick={(e) => e.stopPropagation()}
    >
      <line x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} stroke="rgba(26,22,19,0.12)" />
      <line x1={PAD.l} x2={PAD.l} y1={PAD.t} y2={H - PAD.b} stroke="rgba(26,22,19,0.12)" />
      {lags.map((lag) => (
        <g key={lag}>
          <line x1={sx(lag)} x2={sx(lag)} y1={PAD.t} y2={H - PAD.b} stroke="rgba(26,22,19,0.06)" />
          <text x={sx(lag)} y={H - PAD.b + 22} textAnchor="middle" className="fill-[#A39E95] font-mono" fontSize={13}>
            {lag}
          </text>
        </g>
      ))}
      <text x={(PAD.l + W - PAD.r) / 2} y={H - 6} textAnchor="middle" className="fill-[#A39E95]" fontSize={13}>
        lag L (m)
      </text>
      <text x={18} y={(PAD.t + H - PAD.b) / 2} textAnchor="middle" transform={`rotate(-90 18 ${(PAD.t + H - PAD.b) / 2})`} className="fill-[#A39E95]" fontSize={13}>
        ν(L) (m)
      </text>
      {curves.map((c, k) => {
        const d = c.points.map((p, i) => `${i ? 'L' : 'M'}${sx(p.lag_m).toFixed(1)},${sy(p.nu).toFixed(1)}`).join(' ');
        const color = colors[k % colors.length] ?? INK;
        return (
          <g key={c.id}>
            <motion.path
              d={d}
              fill="none"
              stroke={color}
              strokeWidth={3.5}
              strokeLinecap="round"
              strokeLinejoin="round"
              initial={{ pathLength: 0 }}
              animate={{ pathLength: 1 }}
              transition={{ duration: 1.6, delay: 0.3 + k * 0.35, ease: EASE }}
            />
            {c.points.map((p, i) => (
              <motion.circle
                key={p.lag_m}
                cx={sx(p.lag_m)}
                cy={sy(p.nu)}
                r={5}
                fill="#F9F8F5"
                stroke={color}
                strokeWidth={2.5}
                initial={{ opacity: 0, scale: 0 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: 0.4 + k * 0.35 + i * 0.25, duration: 0.4, ease: EASE }}
              >
                <title>{`${c.role}: ν(${p.lag_m} m) = ${p.nu.toPrecision(4)} m`}</title>
              </motion.circle>
            ))}
          </g>
        );
      })}
      {hl !== null && hl !== undefined ? (
        <g pointerEvents="none">
          <line x1={sx(hl)} x2={sx(hl)} y1={PAD.t} y2={H - PAD.b} stroke="rgba(26,22,19,0.25)" strokeDasharray="3 3" />
          {curves.map((c, k) => {
            const p = c.points.find((q) => q.lag_m === hl);
            if (p === undefined) return null;
            const left = sx(hl) > W * 0.62;
            return (
              <g key={c.id}>
                <circle cx={sx(hl)} cy={sy(p.nu)} r={7} fill={colors[k % colors.length] ?? INK} />
                <text x={sx(hl) + (left ? -12 : 12)} y={sy(p.nu) + (k === 0 ? -12 : 20)} textAnchor={left ? 'end' : 'start'} fontSize={14} className="font-mono" fill={colors[k % colors.length] ?? INK}>
                  {`ν(${hl} m) = ${p.nu.toPrecision(3)} m`}
                </text>
              </g>
            );
          })}
        </g>
      ) : null}
    </svg>
  );
}

const pct = (n: number): string => `${String(n * 100)}%`;

/** A bundle hillshade view (Derived) as a clean rounded image, window outlined in white. */
export function HillTile({ tile, className = '' }: { tile: TileView | null; className?: string }) {
  return (
    <div className={`relative aspect-square overflow-hidden rounded-2xl bg-zinc-100 ${className}`}>
      {tile === null ? (
        <div className="grid size-full place-items-center p-4 text-center text-sm text-zinc-400">Unavailable (no hillshade tile in bundle)</div>
      ) : (
        <>
          {tile.tiles.map((t) => (
            <img
              key={`${String(t.x)}/${String(t.y)}`}
              src={t.src}
              alt={`${tile.caption}, tile ${String(tile.pick.z)}/${String(t.x)}/${String(t.y)}`}
              decoding="async"
              className="absolute max-w-none"
              style={{
                width: pct(1 / tile.pick.view.side),
                height: pct(1 / tile.pick.view.side),
                left: pct((t.x - tile.pick.view.x0) / tile.pick.view.side),
                top: pct((t.y - tile.pick.view.y0) / tile.pick.view.side),
              }}
            />
          ))}
          <span
            aria-hidden
            className="absolute rounded-[4px] border-2"
            style={{ borderColor: 'rgba(255,255,255,0.95)', boxShadow: '0 0 0 1px rgba(9,9,11,0.35)', left: pct(tile.pick.box.left), top: pct(tile.pick.box.top), width: pct(tile.pick.box.width), height: pct(tile.pick.box.height) }}
          />
        </>
      )}
    </div>
  );
}

/**
 * Schematic elevation profile (an illustration, not data) with brackets for the stored lags:
 * the fingerprint asks how much height changes over each distance.
 */
export function LagProfile({ lags }: { lags: readonly number[] }) {
  // three doubling scales from the stored lags (e.g. 270, 540, 1080 m), else the first three
  const first = lags[0] ?? 1;
  const doubling = lags.filter((l) => Number.isInteger(Math.log2(l / first)));
  const shown = (doubling.length >= 3 ? doubling : lags).slice(0, 3);
  const PW = 480;
  const PH = 330;
  const span = Math.max(...shown, 1) * 1.45;
  const xs = Array.from({ length: 121 }, (_, i) => (i / 120) * span);
  const h = (x: number) => 40 * Math.sin(x / 210) + 20 * Math.sin(x / 83 + 1.3) + 8 * Math.sin(x / 29 + 0.4);
  const sx = (x: number) => 10 + (x / span) * (PW - 20);
  const sy = (v: number) => 100 - v;
  const d = xs.map((x, i) => `${i ? 'L' : 'M'}${sx(x).toFixed(1)},${sy(h(x)).toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${PW} ${PH}`} className="h-auto w-full" role="img" aria-label={`Schematic terrain profile with brackets of ${shown.join(', ')} metres`}>
      <path d={`${d} L${sx(span)},170 L${sx(0)},170 Z`} fill="rgba(26,22,19,0.08)" />
      <motion.path d={d} fill="none" stroke={INK} strokeWidth={3.5} strokeLinejoin="round" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 1.4, delay: 0.3, ease: EASE }} />
      {shown.map((lag, k) => {
        const x0 = sx(0);
        const x1 = sx(lag);
        const y = 212 + k * 46;
        return (
          <motion.g key={lag} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.8 + k * 0.25, duration: 0.5, ease: EASE }}>
            <path d={`M${x0 + 1},${y - 12} V${y} H${x1} V${y - 12}`} fill="none" stroke={INK} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" />
            <text x={x1 + 14} y={y + 2} fontSize={26} fontWeight={700} fill={INK}>{`${lag} m`}</text>
          </motion.g>
        );
      })}
    </svg>
  );
}
