'use client';

// Research-post figures for /pitch: a white bordered card with title, subtitle and a "Figure N"
// caption; a thin-line chart with a hover crosshair and direct end labels; a pastel bar chart with
// value labels. Every value plotted is passed in from pitch-data.ts (bundle or generated numbers).
import { motion } from 'motion/react';
import { type PointerEvent, type ReactNode, useState } from 'react';

import { EASE } from './bits';

export const C = {
  ink: '#1A1613',
  sub: '#4D4843',
  muted: '#A39E95',
  hair: 'rgba(26,22,19,0.10)',
  grid: 'rgba(26,22,19,0.07)',
  orange: '#CC5800',
  green: '#4A8044',
  blue: '#7AA7C7',
  grey: '#A39E95',
} as const;

export function FigureCard({ title, sub, legend, caption, children, className = '' }: {
  title: ReactNode;
  sub?: ReactNode;
  legend?: readonly { label: string; color: string }[];
  caption?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <figure className={`flex h-full flex-col rounded-xl border bg-white p-7 ${className}`} style={{ borderColor: C.hair }} onClick={(e) => e.stopPropagation()}>
      <div className="flex flex-col gap-1">
        <h3 className="text-[1.375rem] font-medium leading-tight tracking-[-0.01em]" style={{ color: C.ink }}>{title}</h3>
        {sub ? <p className="text-sm" style={{ color: C.muted }}>{sub}</p> : null}
      </div>
      {legend?.length ? (
        <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-sm" style={{ color: C.sub }}>
          {legend.map((l) => (
            <span key={l.label} className="flex items-center gap-2">
              <span className="size-2 rounded-full" style={{ background: l.color }} />
              {l.label}
            </span>
          ))}
        </div>
      ) : null}
      <div className="mt-4 flex min-h-0 flex-1 flex-col justify-center">{children}</div>
      {caption ? <figcaption className="mt-4 text-sm" style={{ color: C.muted }}>{caption}</figcaption> : null}
    </figure>
  );
}

export interface LineSeries {
  readonly id: string;
  readonly label: string;
  readonly color: string;
  readonly points: readonly { x: number; y: number }[];
}

const tf = (log: boolean) => (v: number) => (log ? Math.log10(v) : v);

/** Thin smooth lines, faint horizontal gridlines, direct labels at line ends, hover crosshair. */
export function LineFigure({ series, xTicks, yTicks, xLog = false, yLog = false, xLabel, yLabel, fmtX, fmtY, ariaLabel, width = 760, height = 380, dots = false }: {
  series: readonly LineSeries[];
  xTicks: readonly number[];
  yTicks: readonly number[];
  xLog?: boolean;
  yLog?: boolean;
  xLabel: string;
  yLabel: string;
  fmtX: (x: number) => string;
  fmtY: (y: number) => string;
  ariaLabel: string;
  width?: number;
  height?: number;
  dots?: boolean;
}) {
  const W = width;
  const H = height;
  const P = { l: 64, r: 150, t: 12, b: 50 };
  const fx = tf(xLog);
  const fy = tf(yLog);
  const xsAll = series.flatMap((s) => s.points.map((p) => p.x));
  const ysAll = [...series.flatMap((s) => s.points.map((p) => p.y)), ...yTicks];
  const x0 = Math.min(...xsAll.map(fx), ...xTicks.map(fx));
  const x1 = Math.max(...xsAll.map(fx), ...xTicks.map(fx));
  const y0 = Math.min(...ysAll.map(fy));
  const y1 = Math.max(...ysAll.map(fy));
  const sx = (x: number) => P.l + ((fx(x) - x0) / (x1 - x0 || 1)) * (W - P.l - P.r);
  const sy = (y: number) => H - P.b - ((fy(y) - y0) / (y1 - y0 || 1)) * (H - P.t - P.b);
  const xs = [...new Set(xsAll)].sort((a, b) => a - b);
  const [hx, setHx] = useState<number | null>(null);
  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    let best = xs[0] ?? null;
    for (const x of xs) if (best === null || Math.abs(sx(x) - px) < Math.abs(sx(best) - px)) best = x;
    setHx(best);
  };
  // smooth path through the points (monotone-ish cardinal with low tension)
  const path = (pts: readonly { x: number; y: number }[]) => {
    const q = pts.map((p) => [sx(p.x), sy(p.y)] as const);
    if (q.length < 3) return q.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    let d = `M${q[0]![0].toFixed(1)},${q[0]![1].toFixed(1)}`;
    for (let i = 0; i < q.length - 1; i++) {
      const p0 = q[Math.max(0, i - 1)]!;
      const p1 = q[i]!;
      const p2 = q[i + 1]!;
      const p3 = q[Math.min(q.length - 1, i + 2)]!;
      const t = 0.16;
      d += ` C${(p1[0] + (p2[0] - p0[0]) * t).toFixed(1)},${(p1[1] + (p2[1] - p0[1]) * t).toFixed(1)} ${(p2[0] - (p3[0] - p1[0]) * t).toFixed(1)},${(p2[1] - (p3[1] - p1[1]) * t).toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
    }
    return d;
  };
  // direct end labels, pushed apart vertically
  const ends = series
    .map((s) => {
      const last = s.points[s.points.length - 1];
      return { s, y: last === undefined ? 0 : sy(last.y) };
    })
    .sort((a, b) => a.y - b.y);
  for (let k = 1; k < ends.length; k++) {
    const prev = ends[k - 1]!;
    const cur = ends[k]!;
    if (cur.y - prev.y < 18) cur.y = prev.y + 18;
  }
  const hoverVals = hx === null ? [] : series.flatMap((s) => {
    const p = s.points.find((q) => q.x === hx);
    return p === undefined ? [] : [{ s, p }];
  });
  const tipLeft = hx !== null && sx(hx) > (W - P.r) * 0.6;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full cursor-crosshair touch-none select-none" role="img" aria-label={ariaLabel} onPointerMove={onMove} onPointerDown={onMove} onPointerLeave={() => setHx(null)}>
      {yTicks.map((t) => (
        <g key={t}>
          <line x1={P.l} x2={W - P.r} y1={sy(t)} y2={sy(t)} stroke={C.grid} />
          <text x={P.l - 10} y={sy(t) + 4} textAnchor="end" fontSize={12} fill={C.muted}>{fmtY(t)}</text>
        </g>
      ))}
      {xTicks.map((t) => (
        <text key={t} x={sx(t)} y={H - P.b + 22} textAnchor="middle" fontSize={12} fill={C.muted}>{fmtX(t)}</text>
      ))}
      <line x1={P.l} x2={W - P.r} y1={H - P.b} y2={H - P.b} stroke={C.hair} />
      <text x={(P.l + W - P.r) / 2} y={H - 8} textAnchor="middle" fontSize={12} fill={C.muted}>{xLabel}</text>
      <text x={14} y={(P.t + H - P.b) / 2} textAnchor="middle" fontSize={12} fill={C.muted} transform={`rotate(-90 14 ${(P.t + H - P.b) / 2})`}>{yLabel}</text>
      {series.map((s, k) => (
        <g key={s.id}>
          <motion.path d={path(s.points)} fill="none" stroke={s.color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 1.4, delay: 0.25 + k * 0.15, ease: EASE }} />
          {dots ? s.points.map((p) => <circle key={p.x} cx={sx(p.x)} cy={sy(p.y)} r={3} fill="#fff" stroke={s.color} strokeWidth={1.5} />) : null}
        </g>
      ))}
      {ends.map(({ s, y }) => {
        const last = s.points[s.points.length - 1];
        if (last === undefined) return null;
        return (
          <g key={s.id}>
            <circle cx={sx(last.x)} cy={sy(last.y)} r={3} fill={s.color} />
            <text x={sx(last.x) + 12} y={y + 4} fontSize={13} fontWeight={500} fill={s.color}>{s.label}</text>
          </g>
        );
      })}
      {hx !== null ? (
        <g pointerEvents="none">
          <line x1={sx(hx)} x2={sx(hx)} y1={P.t} y2={H - P.b} stroke="rgba(26,22,19,0.22)" />
          {hoverVals.map(({ s, p }) => <circle key={s.id} cx={sx(hx)} cy={sy(p.y)} r={4} fill="#fff" stroke={s.color} strokeWidth={2} />)}
          <g transform={`translate(${sx(hx) + (tipLeft ? -12 : 12)},${P.t + 6})`}>
            <rect x={tipLeft ? -210 : 0} y={0} width={210} height={30 + hoverVals.length * 20} rx={8} fill="#fff" stroke={C.hair} />
            <text x={(tipLeft ? -210 : 0) + 12} y={20} fontSize={12} fill={C.muted}>{xLabel.split(' (')[0]} {fmtX(hx)}</text>
            {hoverVals.map(({ s, p }, k) => (
              <g key={s.id} transform={`translate(${(tipLeft ? -210 : 0) + 12},${40 + k * 20})`}>
                <circle cx={4} cy={-4} r={3.5} fill={s.color} />
                <text x={14} y={0} fontSize={12.5} fill={C.ink}>{s.label}</text>
                <text x={186} y={0} fontSize={12.5} textAnchor="end" fill={C.ink} className="tabular-nums">{fmtY(p.y)}</text>
              </g>
            ))}
          </g>
        </g>
      ) : null}
    </svg>
  );
}

export interface Bar {
  readonly label: string;
  readonly value: number;
  readonly color: string;
  readonly text: string;
}

/** Thin pastel bars with a 1px outline, coloured value labels above, faint axis, rotated title. */
export function BarFigure({ bars, yMin = 0, yMax, yTicks, fmtY, yLabel, refLine, ariaLabel }: {
  bars: readonly Bar[];
  yMin?: number;
  yMax: number;
  yTicks: readonly number[];
  fmtY: (y: number) => string;
  yLabel: string;
  refLine?: { value: number; label: string };
  ariaLabel: string;
}) {
  const W = 420;
  const H = 320;
  const P = { l: 70, r: 16, t: 26, b: 56 };
  const sy = (v: number) => H - P.b - ((Math.max(yMin, Math.min(yMax, v)) - yMin) / (yMax - yMin)) * (H - P.t - P.b);
  const slot = (W - P.l - P.r) / bars.length;
  const bw = Math.min(56, slot * 0.42);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={ariaLabel}>
      {yTicks.map((t) => (
        <g key={t}>
          <line x1={P.l} x2={W - P.r} y1={sy(t)} y2={sy(t)} stroke={C.grid} />
          <text x={P.l - 10} y={sy(t) + 4} textAnchor="end" fontSize={12} fill={C.muted}>{fmtY(t)}</text>
        </g>
      ))}
      <line x1={P.l} x2={W - P.r} y1={H - P.b} y2={H - P.b} stroke={C.hair} />
      <text x={16} y={(P.t + H - P.b) / 2} textAnchor="middle" fontSize={12} fill={C.muted} transform={`rotate(-90 16 ${(P.t + H - P.b) / 2})`}>{yLabel}</text>
      {refLine ? (
        <g>
          <line x1={P.l} x2={W - P.r} y1={sy(refLine.value)} y2={sy(refLine.value)} stroke={C.sub} strokeDasharray="4 4" strokeWidth={1} />
          <text x={W - P.r} y={sy(refLine.value) - 6} textAnchor="end" fontSize={12} fill={C.sub}>{refLine.label}</text>
        </g>
      ) : null}
      {bars.map((b, k) => {
        const cx = P.l + slot * (k + 0.5);
        const top = sy(b.value);
        const h = H - P.b - top;
        return (
          <g key={b.label}>
            <motion.rect
              x={cx - bw / 2}
              width={bw}
              rx={3}
              fill={b.color}
              fillOpacity={0.22}
              stroke={b.color}
              strokeWidth={1}
              initial={{ y: H - P.b, height: 0 }}
              animate={{ y: top, height: h }}
              transition={{ duration: 0.9, delay: 0.25 + k * 0.12, ease: EASE }}
            />
            <motion.text x={cx} y={top - 8} textAnchor="middle" fontSize={13} fontWeight={500} fill={b.color} className="tabular-nums" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.7 + k * 0.12 }}>
              {b.text}
            </motion.text>
            <text x={cx} y={H - P.b + 22} textAnchor="middle" fontSize={12.5} fill={C.sub}>{b.label}</text>
          </g>
        );
      })}
    </svg>
  );
}
