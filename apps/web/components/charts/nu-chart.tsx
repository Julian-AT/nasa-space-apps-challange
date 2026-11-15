'use client';
// Log-log nu(L) chart (PRD 6.3, 10) drawn with Observable Plot, which is imported dynamically
// inside the effect so it stays out of every other route's bundle. The server render holds the
// named figure and its text table; the plot is added in the browser. Solid and dashed strokes
// with different markers separate the two windows without relying on colour; the colours are read
// from the theme tokens (--primary, --muted-foreground) and redrawn when the theme or width changes.
import { type RefObject, useEffect, useRef, useState } from 'react';

import { NuTable } from './nu-table.tsx';
import { type NamedSeries, offLogAxis, plottableOnLog } from './series.ts';

const STYLE = [
  { token: '--primary', dash: null, symbol: 'circle' },
  { token: '--muted-foreground', dash: '6 4', symbol: 'square' },
] as const;

/** Resolve a CSS custom property to a concrete colour SVG attributes accept. */
function tokenColour(token: string): string {
  const probe = document.createElement('span');
  probe.style.color = `var(${token})`;
  probe.style.display = 'none';
  document.body.appendChild(probe);
  const c = getComputedStyle(probe).color;
  probe.remove();
  return c === '' ? 'currentColor' : c;
}

function styleFor(index: number) {
  return index % 2 === 0 ? STYLE[0] : STYLE[1];
}

export interface NuChartProps {
  readonly name: string;
  readonly series: readonly NamedSeries[];
  /** Compact: no caption, no frame and no table (the caller shows them elsewhere). */
  readonly compact?: boolean;
}

/** Bumps when the theme class/attribute on <html> changes or the host resizes. */
function useRedraw(host: RefObject<HTMLDivElement | null>): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const bump = (): void => {
      setTick((t) => t + 1);
    };
    const mo = new MutationObserver(bump);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme', 'style'] });
    let last = host.current?.clientWidth ?? 0;
    const ro = new ResizeObserver(() => {
      const w = host.current?.clientWidth ?? 0;
      if (Math.abs(w - last) > 24) {
        last = w;
        bump();
      }
    });
    if (host.current !== null) ro.observe(host.current);
    return () => {
      mo.disconnect();
      ro.disconnect();
    };
  }, [host]);
  return tick;
}

export function NuChart({ name, series, compact = false }: NuChartProps) {
  const host = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const drawn = series.some((s) => plottableOnLog(s.series.points).length > 0);
  const tick = useRedraw(host);

  useEffect(() => {
    const node = host.current;
    if (node === null || !drawn) return;
    let cancelled = false;
    let plot: Element | null = null;
    void import('@observablehq/plot')
      .then((Plot) => {
        if (cancelled) return;
        const fg = tokenColour('--muted-foreground');
        const marks = series.flatMap((s, i) => {
          const base = styleFor(i);
          const style = { ...base, stroke: tokenColour(base.token) };
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
        const width = Math.max(300, node.clientWidth - (compact ? 0 : 16));
        plot = Plot.plot({
          width,
          height: compact ? 320 : 340,
          marginLeft: 56,
          marginBottom: 44,
          style: { background: 'transparent', color: fg, fontSize: '12px', fontFamily: 'inherit' },
          x: { type: 'log', label: 'Lag L (m)', labelAnchor: 'center', labelArrow: 'none', grid: true },
          y: { type: 'log', label: 'ν(L)', labelArrow: 'none', grid: true },
          marks,
        });
        // The container is role="img" with the chart's name, and the table below carries the
        // values; Plot's own aria-label attributes on <g> groups are prohibited without a role, so
        // the drawn SVG is hidden from assistive technology.
        plot.setAttribute('aria-hidden', 'true');
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
  }, [series, drawn, tick, compact]);

  const offLog = series.flatMap((s) => offLogAxis(s.series.points).map((p) => `${s.role} at ${String(p.lag_m)} m`));
  return (
    <figure className="flex flex-col gap-3" aria-label={name}>
      {compact ? null : <figcaption className="font-semibold">{name}</figcaption>}
      <div
        ref={host}
        role="img"
        aria-label={`${name}. Log-log plot of ν against lag in metres; the same values are in the table ${compact ? "under Details" : "below"}.`}
        data-chart="nu"
        className={compact ? 'min-h-[320px] overflow-hidden' : 'min-h-[340px] overflow-x-auto rounded-lg border bg-card p-2'}
      >
        {drawn ? (
          <p className="p-4 text-sm text-muted-foreground">Loading chart…</p>
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
      {compact ? null : <NuTable caption={`${name}: stored values`} series={series} />}
    </figure>
  );
}
