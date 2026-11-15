'use client';
// Figure 2 of the pitch deck: encoder training losses per epoch (report/curves.json), drawn as
// hand-written SVG. Every plotted number is a field of the bundle record; nothing is typed in.
// Left: mean loss per term over seeds on a log axis, min to max band shaded, direct labels.
// Right: the invariance trade-off over lambda_max; unavailable points stay visibly unavailable.
import { useId, useMemo, useState, type KeyboardEvent, type PointerEvent } from 'react';

import type { Curves } from '@terraluna/schema';

export type CurvesJson = Curves;

const INK = '#1A1613';
const MUTED = '#A39E95';
const GRID = 'rgba(26,22,19,0.06)';
const SANS = 'var(--pitch-sans, var(--font-dm-sans)), ui-sans-serif, system-ui, sans-serif';
const MONO = 'var(--pitch-mono, var(--font-dm-mono)), ui-monospace, Menlo, monospace';

// Display names and styles are presentation only; unknown terms fall back to their record name.
const STYLE: Record<string, { label: string; color: string; dash?: string; width?: number }> = {
  total: { label: 'Total', color: '#CC5800', width: 2 },
  vicreg: { label: 'VICReg', color: MUTED },
  phys: { label: 'Physics head', color: '#4A8044' },
  body: { label: 'Body head', color: '#7AA7C7' },
  vicreg_inv: { label: 'Invariance', color: MUTED, dash: '3 3' },
  vicreg_var: { label: 'Variance', color: MUTED, dash: '3 3' },
  vicreg_cov: { label: 'Covariance', color: MUTED, dash: '3 3' },
};
const styleOf = (term: string) => STYLE[term] ?? { label: term, color: MUTED, dash: '1 3' };

function fmt(v: number): string {
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toPrecision(2);
}

function tickLabel(v: number): string {
  return v >= 1 ? String(v) : String(Number(v.toPrecision(1)));
}

/** Monotone cubic (Fritsch-Carlson) path through points with increasing x: smooth, no overshoot. */
function monotonePath(pts: ReadonlyArray<readonly [number, number]>): string {
  const n = pts.length;
  if (n === 0) return '';
  const first = pts[0]!;
  if (n === 1) return `M${first[0]},${first[1]}`;
  const dx: number[] = [];
  const s: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    const a = pts[i]!;
    const b = pts[i + 1]!;
    dx.push(b[0] - a[0]);
    s.push((b[1] - a[1]) / (b[0] - a[0]));
  }
  const m: number[] = [s[0]!];
  for (let i = 1; i < n - 1; i++) m.push(s[i - 1]! * s[i]! <= 0 ? 0 : (s[i - 1]! + s[i]!) / 2);
  m.push(s[n - 2]!);
  for (let i = 0; i < n - 1; i++) {
    if (s[i] === 0) {
      m[i] = 0;
      m[i + 1] = 0;
      continue;
    }
    const a = m[i]! / s[i]!;
    const b = m[i + 1]! / s[i]!;
    const h = a * a + b * b;
    if (h > 9) {
      const t = 3 / Math.sqrt(h);
      m[i] = t * a * s[i]!;
      m[i + 1] = t * b * s[i]!;
    }
  }
  let d = `M${first[0].toFixed(2)},${first[1].toFixed(2)}`;
  for (let i = 0; i < n - 1; i++) {
    const p = pts[i]!;
    const q = pts[i + 1]!;
    const h = dx[i]! / 3;
    d += `C${(p[0] + h).toFixed(2)},${(p[1] + m[i]! * h).toFixed(2)} ${(q[0] - h).toFixed(2)},${(q[1] - m[i + 1]! * h).toFixed(2)} ${q[0].toFixed(2)},${q[1].toFixed(2)}`;
  }
  return d;
}

/** Spread label y positions so no two sit closer than gap, keeping them inside [lo, hi]. */
function spread(ys: number[], gap: number, lo: number, hi: number): number[] {
  const order = ys.map((y, i) => ({ y, i })).sort((a, b) => a.y - b.y);
  for (let k = 1; k < order.length; k++) order[k]!.y = Math.max(order[k]!.y, order[k - 1]!.y + gap);
  const last = order[order.length - 1];
  if (last && last.y > hi) {
    last.y = hi;
    for (let k = order.length - 2; k >= 0; k--) order[k]!.y = Math.min(order[k]!.y, order[k + 1]!.y - gap);
  }
  const out = new Array<number>(ys.length);
  for (const o of order) out[o.i] = Math.max(lo, o.y);
  return out;
}

// Main chart geometry in viewBox units (1 unit ~ 1 CSS px at the deck's figure width).
const W = 760;
const H = 500;
const M = { top: 16, right: 112, bottom: 36, left: 44 };
const PW = W - M.left - M.right;
const PH = H - M.top - M.bottom;

function LossChart({ curves }: { curves: CurvesJson }) {
  const [hover, setHover] = useState<number | null>(null);
  const titleId = useId();

  const model = useMemo(() => {
    const terms = curves.loss_terms.filter((t) => t.epochs.length > 0);
    const epochs = [...new Set(terms.flatMap((t) => t.epochs.map((e) => e.epoch)))].sort((a, b) => a - b);
    const positive = terms.flatMap((t) => t.epochs.flatMap((e) => [e.min, e.mean, e.max])).filter((v) => v > 0);
    const e0 = epochs[0] ?? 0;
    const e1 = epochs[epochs.length - 1] ?? 1;
    const lo = Math.pow(10, Math.floor(Math.log10(Math.min(...positive))));
    const hi = Math.pow(10, Math.ceil(Math.log10(Math.max(...positive))));
    const x = (e: number) => M.left + (e1 === e0 ? PW / 2 : ((e - e0) / (e1 - e0)) * PW);
    const y = (v: number) => M.top + PH - ((Math.log10(Math.max(v, lo)) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * PH;
    const ticks: number[] = [];
    for (let t = lo; t <= hi * 1.0001; t *= 10) ticks.push(t);
    const minor: number[] = [];
    for (const t of ticks.slice(0, -1)) for (const k of [2, 5]) minor.push(t * k);
    const series = terms.map((t) => {
      const st = styleOf(t.term);
      const pts = t.epochs.map((e) => [x(e.epoch), y(e.mean)] as const);
      const upper = t.epochs.map((e) => [x(e.epoch), y(e.max)] as const);
      const lower = t.epochs.map((e) => [x(e.epoch), y(e.min)] as const).reverse();
      const band = `${monotonePath(upper)}L${lower.map((p) => `${p[0].toFixed(2)},${p[1].toFixed(2)}`).join('L')}Z`;
      return { term: t.term, st, epochs: t.epochs, line: monotonePath(pts), band, endY: pts[pts.length - 1]![1] };
    });
    const labelY = spread(series.map((s) => s.endY), 14, M.top + 4, M.top + PH);
    const nSeeds = Math.max(...terms.flatMap((t) => t.epochs.map((e) => e.n_seeds)));
    // Draw the highlighted series last so it sits on top.
    const drawOrder = [...series].sort((a, b) => (a.term === 'total' ? 1 : 0) - (b.term === 'total' ? 1 : 0));
    return { series, drawOrder, labelY, epochs, x, y, ticks, minor, nSeeds };
  }, [curves]);

  const { series, drawOrder, labelY, epochs, x, y, ticks, minor, nSeeds } = model;
  if (series.length === 0) {
    return <p style={{ fontSize: 13, color: MUTED }}>Unavailable: report/curves.json lists no loss term.</p>;
  }

  const onMove = (ev: PointerEvent<SVGSVGElement>) => {
    const r = ev.currentTarget.getBoundingClientRect();
    const vx = ((ev.clientX - r.left) / r.width) * W;
    let best = 0;
    for (let i = 1; i < epochs.length; i++) if (Math.abs(x(epochs[i]!) - vx) < Math.abs(x(epochs[best]!) - vx)) best = i;
    setHover(best);
  };
  const onKey = (ev: KeyboardEvent<SVGSVGElement>) => {
    if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') {
      ev.preventDefault();
      const step = ev.key === 'ArrowRight' ? 1 : -1;
      setHover((h) => Math.min(epochs.length - 1, Math.max(0, (h ?? (step > 0 ? -1 : epochs.length)) + step)));
    } else if (ev.key === 'Escape') setHover(null);
  };

  const he = hover === null ? null : epochs[hover]!;
  const hx = he === null ? 0 : x(he);
  const tipLeft = (hx / W) * 100;
  const tipRows =
    he === null
      ? []
      : series
          .map((s) => ({ s, e: s.epochs.find((e) => e.epoch === he) }))
          .filter((r) => r.e !== undefined)
          .sort((a, b) => b.e!.mean - a.e!.mean);

  return (
    <div style={{ position: 'relative' }}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        role="img"
        aria-labelledby={titleId}
        tabIndex={0}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
        onKeyDown={onKey}
        onBlur={() => setHover(null)}
        style={{ display: 'block', overflow: 'visible', outline: 'none', touchAction: 'pan-y' }}
      >
        <title id={titleId}>
          {`Loss per epoch on a log axis for ${series.map((s) => s.st.label).join(', ')}; mean over ${nSeeds} seeds. Use left and right arrow keys to read values.`}
        </title>
        {minor.map((t) => (
          <line key={`m${t}`} x1={M.left} x2={M.left + PW} y1={y(t)} y2={y(t)} stroke={GRID} strokeWidth={1} strokeDasharray="2 4" />
        ))}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={M.left + PW} y1={y(t)} y2={y(t)} stroke={GRID} strokeWidth={1} />
            <text x={M.left - 10} y={y(t)} dy="0.32em" textAnchor="end" fontFamily={MONO} fontSize={11} fill={MUTED}>
              {tickLabel(t)}
            </text>
          </g>
        ))}
        {epochs.map((e) => (
          <text key={e} x={x(e)} y={M.top + PH + 20} textAnchor="middle" fontFamily={MONO} fontSize={11} fill={MUTED}>
            {e}
          </text>
        ))}
        <text x={M.left + PW} y={M.top + PH + 34} textAnchor="end" fontFamily={MONO} fontSize={10} fill={MUTED} opacity={0.8}>
          epoch
        </text>

        {drawOrder.map((s) => (
          <path key={`b${s.term}`} d={s.band} fill={s.st.color} opacity={0.12} />
        ))}
        {drawOrder.map((s) => (
          <path
            key={`l${s.term}`}
            d={s.line}
            fill="none"
            stroke={s.st.color}
            strokeWidth={s.st.width ?? 1.5}
            strokeDasharray={s.st.dash}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ))}
        {series.map((s, i) => (
          <g key={`t${s.term}`}>
            <circle cx={M.left + PW} cy={s.endY} r={2.25} fill={s.st.color} />
            {Math.abs(labelY[i]! - s.endY) > 2 ? (
              <path d={`M${M.left + PW + 4},${s.endY}L${M.left + PW + 9},${labelY[i]}`} stroke={s.st.color} strokeWidth={0.75} opacity={0.6} fill="none" />
            ) : null}
            <text
              x={M.left + PW + 12}
              y={labelY[i]}
              dy="0.32em"
              fontFamily={SANS}
              fontSize={12}
              fontWeight={s.term === 'total' ? 600 : 400}
              fill={s.term === 'total' ? s.st.color : s.st.color === MUTED ? '#6F6A62' : s.st.color}
            >
              {s.st.label}
            </text>
          </g>
        ))}

        {he !== null ? (
          <g pointerEvents="none">
            <line x1={hx} x2={hx} y1={M.top} y2={M.top + PH} stroke={INK} strokeOpacity={0.25} strokeWidth={1} />
            {tipRows.map(({ s, e }) => (
              <circle key={s.term} cx={hx} cy={y(e!.mean)} r={3} fill="#fff" stroke={s.st.color} strokeWidth={1.5} />
            ))}
          </g>
        ) : null}
      </svg>

      {he !== null ? (
        <div
          role="status"
          aria-live="polite"
          style={{
            position: 'absolute',
            top: 8,
            left: `${tipLeft}%`,
            transform: tipLeft > 55 ? 'translateX(calc(-100% - 12px))' : 'translateX(12px)',
            background: '#fff',
            border: '1px solid rgba(26,22,19,0.10)',
            borderRadius: 8,
            boxShadow: '0 4px 16px rgba(26,22,19,0.06)',
            padding: '8px 10px',
            pointerEvents: 'none',
            minWidth: 168,
            fontFamily: SANS,
            fontSize: 12,
            color: INK,
          }}
        >
          <div style={{ fontFamily: MONO, fontSize: 11, color: MUTED, marginBottom: 4 }}>Epoch {he}</div>
          {tipRows.map(({ s, e }) => (
            <div key={s.term} style={{ display: 'flex', alignItems: 'center', gap: 8, lineHeight: '18px' }}>
              <span style={{ width: 8, height: 2, background: s.st.color, borderRadius: 1, flex: 'none' }} />
              <span style={{ flex: 1 }}>{s.st.label}</span>
              <span style={{ fontFamily: MONO, fontSize: 11 }}>{fmt(e!.mean)}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

type Metric = { value: number | null; ci_low: number | null; ci_high: number | null };

function TradeoffMini({
  title,
  points,
  pick,
  domain,
  color,
  reference,
}: {
  title: string;
  points: CurvesJson['tradeoff'];
  pick: (p: CurvesJson['tradeoff'][number]) => Metric;
  domain: readonly [number, number];
  color: string;
  reference?: { value: number; label: string };
}) {
  const w = 300;
  const h = 148;
  const m = { top: 10, right: 12, bottom: 28, left: 40 };
  const pw = w - m.left - m.right;
  const ph = h - m.top - m.bottom;
  const n = points.length;
  const x = (i: number) => m.left + (n <= 1 ? pw / 2 : (i / (n - 1)) * pw);
  const y = (v: number) => m.top + ph - ((v - domain[0]) / (domain[1] - domain[0])) * ph;
  const ticks = niceTicks(domain);
  const available = points.map((p, i) => ({ i, v: pick(p) })).filter((r) => r.v.value !== null);
  return (
    <figure style={{ margin: 0 }}>
      <figcaption style={{ fontFamily: SANS, fontSize: 12, color: INK, marginBottom: 4 }}>{title}</figcaption>
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label={`${title} by lambda_max`} style={{ display: 'block', overflow: 'visible' }}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={m.left} x2={m.left + pw} y1={y(t)} y2={y(t)} stroke={GRID} />
            <text x={m.left - 8} y={y(t)} dy="0.32em" textAnchor="end" fontFamily={MONO} fontSize={10.5} fill={MUTED}>
              {t.toFixed(2)}
            </text>
          </g>
        ))}
        {reference ? (
          <g>
            <line x1={m.left} x2={m.left + pw} y1={y(reference.value)} y2={y(reference.value)} stroke={MUTED} strokeDasharray="3 3" strokeWidth={1} />
            <text x={m.left + pw} y={y(reference.value) - 5} textAnchor="end" fontFamily={MONO} fontSize={10} fill={MUTED}>
              {reference.label}
            </text>
          </g>
        ) : null}
        {available.length > 1 ? (
          <path
            d={available.map((r, k) => `${k === 0 ? 'M' : 'L'}${x(r.i)},${y(r.v.value!)}`).join('')}
            fill="none"
            stroke={color}
            strokeOpacity={0.35}
            strokeWidth={1}
          />
        ) : null}
        {points.map((p, i) => {
          const v = pick(p);
          return (
            <g key={p.lambda_max}>
              <text x={x(i)} y={m.top + ph + 18} textAnchor="middle" fontFamily={MONO} fontSize={10.5} fill={MUTED}>
                {p.lambda_max}
              </text>
              {v.value === null ? (
                <g>
                  <title>{p.reason ?? 'unavailable in the record'}</title>
                  <text x={x(i)} y={m.top + ph - 6} textAnchor="middle" fontFamily={MONO} fontSize={9.5} fill={MUTED}>
                    n/a
                  </text>
                </g>
              ) : (
                <g>
                  <title>{`lambda_max ${p.lambda_max}: ${v.value.toFixed(3)} [${v.ci_low?.toFixed(3) ?? 'n/a'}, ${v.ci_high?.toFixed(3) ?? 'n/a'}]`}</title>
                  {v.ci_low !== null && v.ci_high !== null ? (
                    <line x1={x(i)} x2={x(i)} y1={y(v.ci_low)} y2={y(v.ci_high)} stroke={color} strokeWidth={1.5} strokeOpacity={0.45} strokeLinecap="round" />
                  ) : null}
                  <circle cx={x(i)} cy={y(v.value)} r={3.5} fill={color} />
                </g>
              )}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

function niceStep(span: number): number {
  const raw = span / 3;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const k = raw / p;
  return (k <= 1 ? 1 : k <= 2 ? 2 : k <= 5 ? 5 : 10) * p;
}

/** Domain snapped to a round step so the three to four ticks read as clean numbers. */
function niceDomain(vals: number[], floor?: number): readonly [number, number] {
  const lo = Math.min(...vals, ...(floor === undefined ? [] : [floor]));
  const hi = Math.max(...vals);
  const step = niceStep(hi - lo || 0.1);
  return [Math.max(0, Math.floor(lo / step) * step), Math.min(1, Math.ceil(hi / step) * step)];
}

function niceTicks(domain: readonly [number, number]): number[] {
  // Largest step from the 1-2-5 ladder that still leaves at least three ticks on the domain.
  const span = domain[1] - domain[0];
  const p = Math.pow(10, Math.floor(Math.log10(span)));
  const step = [5, 2, 1, 0.5, 0.2, 0.1].map((k) => k * p).find((st) => span / st >= 2 - 1e-9 && Math.abs(domain[0] / st - Math.round(domain[0] / st)) < 1e-6) ?? span / 2;
  const out: number[] = [];
  for (let t = domain[0]; t <= domain[1] + step * 1e-6; t += step) out.push(Number(t.toFixed(6)));
  return out;
}

function Tradeoff({ curves }: { curves: CurvesJson }) {
  const pts = [...curves.tradeoff].sort((a, b) => a.lambda_max - b.lambda_max);
  const bounds = (pick: (p: (typeof pts)[number]) => Metric) =>
    pts.flatMap((p) => {
      const v = pick(p);
      return [v.value, v.ci_low, v.ci_high].filter((n): n is number => n !== null);
    });
  const r2 = bounds((p) => p.physics_r2);
  const acc = bounds((p) => p.body_probe_balanced_accuracy);
  if (pts.length === 0 || r2.length === 0 || acc.length === 0) return null;
  const missing = pts.filter((p) => p.status !== 'available');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div>
        <div style={{ fontFamily: SANS, fontSize: 13, fontWeight: 500, color: INK }}>Invariance trade-off</div>
        <div style={{ fontFamily: SANS, fontSize: 12, color: MUTED }}>By lambda_max; bars span the recorded interval</div>
      </div>
      <TradeoffMini title="Physics R²" points={pts} pick={(p) => p.physics_r2} domain={niceDomain(r2)} color="#4A8044" />
      <TradeoffMini
        title="Body probe balanced accuracy"
        points={pts}
        pick={(p) => p.body_probe_balanced_accuracy}
        domain={niceDomain(acc, curves.chance_level_body_probe)}
        color="#7AA7C7"
        reference={{ value: curves.chance_level_body_probe, label: 'chance' }}
      />
      {missing.length > 0 ? (
        <div style={{ fontFamily: SANS, fontSize: 11.5, color: MUTED, lineHeight: 1.45 }}>
          {missing.map((p) => (
            <div key={p.lambda_max}>
              n/a at lambda_max {p.lambda_max}: {p.reason ?? 'unavailable in the record'}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function FigTraining({ curves }: { curves: CurvesJson }) {
  const seeds = Math.max(0, ...curves.loss_terms.flatMap((t) => t.epochs.map((e) => e.n_seeds)));
  const hasTradeoff = curves.tradeoff.some((p) => p.status === 'available');
  return (
    <figure
      style={{
        margin: 0,
        background: '#fff',
        border: '1px solid rgba(26,22,19,0.10)',
        borderRadius: 12,
        padding: 32,
        color: INK,
        fontFamily: SANS,
        width: '100%',
        boxSizing: 'border-box',
      }}
    >
      {curves.mock ? (
        <div role="alert" style={{ background: '#FDECDF', color: '#8A3B00', fontSize: 12, fontWeight: 500, padding: '6px 10px', borderRadius: 6, marginBottom: 16 }}>
          Mock preview: report/curves.json is marked mock. Not for production.
        </div>
      ) : null}
      <header style={{ marginBottom: 20 }}>
        <h3 style={{ margin: 0, fontFamily: SANS, fontWeight: 500, fontSize: 22, letterSpacing: '-0.01em', color: INK }}>Training losses</h3>
        <p style={{ margin: '4px 0 0', fontSize: 13, color: MUTED }}>Lower is better, per epoch</p>
      </header>
      <div style={{ display: 'grid', gridTemplateColumns: hasTradeoff ? 'minmax(0, 2.15fr) minmax(0, 1fr)' : 'minmax(0, 1fr)', gap: 40, alignItems: 'start' }}>
        <LossChart curves={curves} />
        {hasTradeoff ? <Tradeoff curves={curves} /> : null}
      </div>
      <footer style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 16, marginTop: 20 }}>
        <figcaption style={{ fontSize: 12, color: MUTED }}>Figure 2. Encoder training losses per epoch.</figcaption>
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: MUTED }}>
          {curves.mock ? 'Mock' : 'Measured'} · log axis, mean of {seeds} seeds, band min to max · report/curves.json
          {curves.run_id ? ` · run ${curves.run_id.slice(7, 15)}` : ''}
        </span>
      </footer>
    </figure>
  );
}
