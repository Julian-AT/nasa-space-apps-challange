'use client';

// Small animated pieces of the /pitch deck: a count-up number and the nu(L) curve drawn from the
// stored fingerprint points (log-log, points exactly as stored, a line between stored lags only).
import { animate, motion, useMotionValue, useReducedMotion, useTransform } from 'motion/react';
import { useEffect } from 'react';

import type { NuCurve } from './pitch-data';

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

export function NuChart({ curves, lags }: { curves: readonly NuCurve[]; lags: readonly number[] }) {
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
  const colors = ['var(--primary)', '#18181b'];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Relief structure function nu(L), log-log, for the best-matching Mars and Earth windows">
      <line x1={PAD.l} x2={W - PAD.r} y1={H - PAD.b} y2={H - PAD.b} stroke="#e4e4e7" />
      <line x1={PAD.l} x2={PAD.l} y1={PAD.t} y2={H - PAD.b} stroke="#e4e4e7" />
      {lags.map((lag) => (
        <g key={lag}>
          <line x1={sx(lag)} x2={sx(lag)} y1={PAD.t} y2={H - PAD.b} stroke="#f4f4f5" />
          <text x={sx(lag)} y={H - PAD.b + 22} textAnchor="middle" className="fill-zinc-400 font-mono" fontSize={13}>
            {lag}
          </text>
        </g>
      ))}
      <text x={(PAD.l + W - PAD.r) / 2} y={H - 6} textAnchor="middle" className="fill-zinc-400" fontSize={13}>
        lag L (m)
      </text>
      <text x={18} y={(PAD.t + H - PAD.b) / 2} textAnchor="middle" transform={`rotate(-90 18 ${(PAD.t + H - PAD.b) / 2})`} className="fill-zinc-400" fontSize={13}>
        ν(L) (m)
      </text>
      {curves.map((c, k) => {
        const d = c.points.map((p, i) => `${i ? 'L' : 'M'}${sx(p.lag_m).toFixed(1)},${sy(p.nu).toFixed(1)}`).join(' ');
        const color = colors[k % colors.length];
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
                fill="white"
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
    </svg>
  );
}
