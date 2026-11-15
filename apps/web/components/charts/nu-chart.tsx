'use client';
// Log-log nu(L) chart (PRD 6.3, 10) drawn with Observable Plot, which is imported dynamically
// inside the effect so it stays out of every other route's bundle. The server render holds the
// named figure and its text table; the plot is added in the browser. Solid and dashed strokes
// with different markers separate the two windows without relying on colour (Okabe-Ito pair).
import { useEffect, useRef, useState } from 'react';

import { NuTable } from './nu-table.tsx';
import { type NamedSeries, offLogAxis, plottableOnLog } from './series.ts';

const STYLE = [
  { stroke: '#56B4E9', dash: null, symbol: 'circle' },
  { stroke: '#E69F00', dash: '6 4', symbol: 'square' },
] as const;

function styleFor(index: number) {
  return index % 2 === 0 ? STYLE[0] : STYLE[1];
}

export interface NuChartProps {
  readonly name: string;
  readonly series: readonly NamedSeries[];
}

export function NuChart({ name, series }: NuChartProps) {
  const host = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const drawn = series.some((s) => plottableOnLog(s.series.points).length > 0);

  useEffect(() => {
    const node = host.current;
    if (node === null || !drawn) return;
    let cancelled = false;
    let plot: Element | null = null;
    void import('@observablehq/plot')
      .then((Plot) => {
        if (cancelled) return;
        const marks = series.flatMap((s, i) => {
          const style = styleFor(i);
          const data = plottableOnLog(s.series.points).map((p) => ({ ...p, role: s.role }));
          const gaps = s.series.gaps.map((g) => ({ ...g, role: s.role }));
          return [
            Plot.ruleX(gaps, { x: 'lag_m', stroke: style.stroke, strokeDasharray: '2 4', strokeOpacity: 0.6 }),
            Plot.line(data, {
              x: 'lag_m',
              y: 'nu',
              z: 'segment',
              stroke: style.stroke,
              ...(style.dash === null ? {} : { strokeDasharray: style.dash }),
              strokeWidth: 2,
            }),
            Plot.dot(data, {
              x: 'lag_m',
              y: 'nu',
              stroke: style.stroke,
              fill: style.stroke,
              symbol: style.symbol,
              r: 4,
              tip: true,
              title: (d: { role: string; lag_m: number; nu: number }) => `${d.role}\nL = ${String(d.lag_m)} m\nν = ${String(d.nu)}`,
            }),
          ];
        });
        plot = Plot.plot({
          width: 560,
          height: 340,
          marginLeft: 56,
          style: { background: 'transparent', color: '#e5e7eb', fontSize: '12px' },
          x: { type: 'log', label: 'Lag L (m) →', grid: true },
          y: { type: 'log', label: '↑ ν(L)', grid: true },
          marks,
        });
        node.replaceChildren(plot);
        node.setAttribute('data-plot', 'ready');
      })
      .catch((error: unknown) => {
        if (!cancelled) setFailed(error instanceof Error ? error.message : 'chart library failed to load');
      });
    return () => {
      cancelled = true;
      plot?.remove();
    };
  }, [series, drawn]);

  const offLog = series.flatMap((s) => offLogAxis(s.series.points).map((p) => `${s.role} at ${String(p.lag_m)} m`));
  return (
    <figure className="flex flex-col gap-3" aria-label={name}>
      <figcaption className="font-semibold">{name}</figcaption>
      <div
        ref={host}
        role="img"
        aria-label={`${name}. Log-log plot of ν against lag in metres; the same values are in the table below.`}
        data-chart="nu"
        className="min-h-[340px] overflow-x-auto rounded-lg border border-line bg-card p-2"
      >
        {drawn ? (
          <p className="p-4 text-sm text-muted-foreground">Loading chart… the table below has every value.</p>
        ) : (
          <p className="p-4 text-sm text-muted-foreground">
            No lag has a plottable value on both fingerprints; see the table for the reasons.
          </p>
        )}
      </div>
      {failed === null ? null : (
        <p role="status" className="text-sm">
          Chart unavailable ({failed}); the table below has every value.
        </p>
      )}
      {offLog.length === 0 ? null : (
        <p className="text-sm text-muted-foreground">
          Zero values cannot be placed on a log axis and are listed only in the table: {offLog.join('; ')}.
        </p>
      )}
      <NuTable caption={`${name}: stored values`} series={series} />
    </figure>
  );
}
