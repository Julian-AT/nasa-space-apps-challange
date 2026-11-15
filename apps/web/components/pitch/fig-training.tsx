'use client';
// Figure 2 of the pitch deck: encoder training losses per epoch (report/curves.json), drawn as
// hand-written SVG small multiples. Every plotted number is a field of the bundle record; nothing
// is typed in. Three panels, one line each on a linear axis, faint min to max band over seeds.
import { useId, useMemo, useState, type KeyboardEvent, type PointerEvent } from 'react';

import type { Curves } from '@terraluna/schema';

export type CurvesJson = Curves;

const INK = '#1A1613';
const BODY = '#4D4843';
const MUTED = '#A39E95';
const GRID = 'rgba(26,22,19,0.07)';
const BORDER = 'rgba(26,22,19,0.10)';
const SANS = 'var(--pitch-sans, var(--font-dm-sans)), ui-sans-serif, system-ui, sans-serif';
const MONO = 'var(--pitch-mono, var(--font-dm-mono)), ui-monospace, Menlo, monospace';

type Epoch = CurvesJson['loss_terms'][number]['epochs'][number];

function fmt(v: number): string {
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toPrecision(2);
}

function tickText(v: number, step: number): string {
  const decimals = Math.max(0, -Math.floor(Math.log10(step) + 1e-9) + (step % 1 === 0 ? 0 : 1));
  return Number(v.toFixed(Math.min(decimals, 3))).toString();
}

/** Three to five round ticks covering [lo, hi] on a 1, 2, 2.5, 5 ladder. */
function niceAxis(lo: number, hi: number): { domain: [number, number]; ticks: number[]; step: number } {
  const span = hi - lo || Math.abs(hi) || 1;
  const raw = span / 3;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const k = raw / p;
  const step = (k <= 1 ? 1 : k <= 2 ? 2 : k <= 2.5 ? 2.5 : k <= 5 ? 5 : 10) * p;
  const d0 = Math.floor(lo / step + 1e-9) * step;
  const d1 = Math.ceil(hi / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let t = d0; t <= d1 + step * 1e-6; t += step) ticks.push(Number(t.toFixed(6)));
  return { domain: [d0, d1], ticks, step };
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

// Panel chart geometry in viewBox units (about 1 CSS px at the deck's figure width).
const W = 360;
const H = 210;
const M = { top: 12, right: 44, bottom: 30, left: 36 };
const PW = W - M.left - M.right;
const PH = H - M.top - M.bottom;

type Reference = { value: number; label: string };

function Panel({
  title,
  takeaway,
  color,
  epochs,
  epochOffset,
  reference,
}: {
  title: string;
  takeaway: string;
  color: string;
  epochs: Epoch[];
  epochOffset: number;
  reference?: Reference | undefined;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const titleId = useId();

  const g = useMemo(() => {
    const vals = epochs.flatMap((e) => [e.min, e.mean, e.max]);
    if (reference) vals.push(reference.value);
    const axis = niceAxis(Math.min(...vals), Math.max(...vals));
    const xs = epochs.map((e) => e.epoch + epochOffset);
    const e0 = xs[0] ?? 1;
    const e1 = xs[xs.length - 1] ?? 1;
    // Rounded so server and client render identical attribute strings.
    const r2 = (v: number) => Math.round(v * 100) / 100;
    const x = (e: number) => r2(M.left + (e1 === e0 ? PW / 2 : ((e - e0) / (e1 - e0)) * PW));
    const y = (v: number) => r2(M.top + PH - ((v - axis.domain[0]) / (axis.domain[1] - axis.domain[0])) * PH);
    const line = monotonePath(epochs.map((e, i) => [x(xs[i]!), y(e.mean)] as const));
    const upper = monotonePath(epochs.map((e, i) => [x(xs[i]!), y(e.max)] as const));
    const lower = epochs
      .map((e, i) => [x(xs[i]!), y(e.min)] as const)
      .reverse()
      .map((p) => `${p[0].toFixed(2)},${p[1].toFixed(2)}`)
      .join('L');
    const xStep = Math.max(1, Math.round((e1 - e0) / 4));
    const xTicks = [e0];
    for (let t = Math.ceil((e0 + 1) / xStep) * xStep; t < e1; t += xStep) if (e1 - t >= xStep / 2) xTicks.push(t);
    if (e1 !== e0) xTicks.push(e1);
    return { axis, xs, x, y, line, band: `${upper}L${lower}Z`, xTicks, e0, e1 };
  }, [epochs, epochOffset, reference]);

  const first = epochs[0]!;
  const last = epochs[epochs.length - 1]!;
  const { axis, xs, x, y } = g;
  const hv = hover === null ? null : { e: epochs[hover]!, ep: xs[hover]! };

  const onMove = (ev: PointerEvent<SVGSVGElement>) => {
    const r = ev.currentTarget.getBoundingClientRect();
    const vx = ((ev.clientX - r.left) / r.width) * W;
    let best = 0;
    for (let i = 1; i < xs.length; i++) if (Math.abs(x(xs[i]!) - vx) < Math.abs(x(xs[best]!) - vx)) best = i;
    setHover(best);
  };
  const onKey = (ev: KeyboardEvent<SVGSVGElement>) => {
    if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') {
      ev.preventDefault();
      const step = ev.key === 'ArrowRight' ? 1 : -1;
      setHover((h) => Math.min(xs.length - 1, Math.max(0, (h ?? (step > 0 ? -1 : xs.length)) + step)));
    } else if (ev.key === 'Escape') setHover(null);
  };

  return (
    <section
      style={{
        background: '#fff',
        border: `1px solid ${BORDER}`,
        borderRadius: 12,
        padding: '22px 22px 16px',
        display: 'flex',
        flexDirection: 'column',
        minWidth: 0,
      }}
    >
      <h3 style={{ margin: 0, fontFamily: SANS, fontWeight: 500, fontSize: 18, lineHeight: 1.3, color: INK }}>{title}</h3>
      <p style={{ margin: '4px 0 0', fontFamily: SANS, fontSize: 14, lineHeight: 1.4, color: BODY }}>{takeaway}</p>
      <div style={{ margin: '10px 0 8px', fontFamily: MONO, fontSize: 13, color: INK, display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span style={{ color: BODY }}>{fmt(first.mean)}</span>
        <span style={{ color: MUTED, fontSize: 12 }}>to</span>
        <span style={{ color }}>{fmt(last.mean)}</span>
        <span style={{ color: MUTED, fontSize: 11, marginLeft: 'auto' }} aria-live="polite">
          {hv ? `epoch ${hv.ep}: ${fmt(hv.e.mean)}` : ''}
        </span>
      </div>
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
          {`${title}: mean over seeds goes from ${fmt(first.mean)} at epoch ${xs[0]} to ${fmt(last.mean)} at epoch ${xs[xs.length - 1]}. Use left and right arrow keys to read each epoch.`}
        </title>
        {axis.ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={M.left + PW} y1={y(t)} y2={y(t)} stroke={GRID} strokeWidth={1} />
            <text x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end" fontFamily={MONO} fontSize={10.5} fill={MUTED}>
              {tickText(t, axis.step)}
            </text>
          </g>
        ))}
        {g.xTicks.map((e) => (
          <text key={e} x={x(e)} y={M.top + PH + 17} textAnchor="middle" fontFamily={MONO} fontSize={10.5} fill={MUTED}>
            {e}
          </text>
        ))}
        <text x={M.left + PW / 2} y={M.top + PH + 30} textAnchor="middle" fontFamily={MONO} fontSize={10} fill={MUTED}>
          epoch
        </text>

        {reference ? (
          <g>
            <line
              x1={M.left}
              x2={M.left + PW}
              y1={y(reference.value)}
              y2={y(reference.value)}
              stroke={BODY}
              strokeOpacity={0.55}
              strokeDasharray="4 4"
              strokeWidth={1}
            />
            <text x={M.left + PW} y={y(reference.value) - 6} textAnchor="end" fontFamily={MONO} fontSize={10.5} fill={BODY}>
              {reference.label}
            </text>
          </g>
        ) : null}

        <path d={g.band} fill={color} opacity={0.14} />
        <path d={g.line} fill="none" stroke={color} strokeWidth={2.25} strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={x(xs[0]!)} cy={y(first.mean)} r={2.5} fill={color} />
        <circle cx={x(xs[xs.length - 1]!)} cy={y(last.mean)} r={3.5} fill={color} />
        <text x={x(xs[xs.length - 1]!) + 7} y={y(last.mean)} dy="0.32em" fontFamily={MONO} fontSize={11.5} fontWeight={500} fill={color}>
          {fmt(last.mean)}
        </text>

        {hv ? (
          <g pointerEvents="none">
            <line x1={x(hv.ep)} x2={x(hv.ep)} y1={M.top} y2={M.top + PH} stroke={INK} strokeOpacity={0.2} strokeWidth={1} />
            <circle cx={x(hv.ep)} cy={y(hv.e.mean)} r={4} fill="#fff" stroke={color} strokeWidth={2} />
          </g>
        ) : null}
      </svg>
    </section>
  );
}

/** Plain-English direction of a curve, used only when the curve does not simply fall. */
function direction(epochs: Epoch[]): 'falls' | 'rises' | 'flat' {
  const a = epochs[0]?.mean ?? 0;
  const b = epochs[epochs.length - 1]?.mean ?? 0;
  const rel = (b - a) / (Math.abs(a) || 1);
  return rel < -0.05 ? 'falls' : rel > 0.05 ? 'rises' : 'flat';
}

export function FigTraining({ curves }: { curves: CurvesJson }) {
  const byTerm = new Map(curves.loss_terms.filter((t) => t.epochs.length > 0).map((t) => [t.term, t.epochs]));
  const all = [...byTerm.values()].flat();
  const seeds = Math.max(0, ...all.map((e) => e.n_seeds));
  // Records are 0-indexed; the deck counts epochs from 1.
  const epochOffset = all.length > 0 && Math.min(...all.map((e) => e.epoch)) === 0 ? 1 : 0;

  const vic = byTerm.get('vicreg');
  const phys = byTerm.get('phys');
  const body = byTerm.get('body');

  // The body head is a cross entropy over the classes; a uniform guess scores ln(1 / chance).
  const chance = curves.chance_level_body_probe;
  const ceChance = chance > 0 && chance < 1 ? Math.log(1 / chance) : null;
  const nClasses = chance > 0 ? Math.round(1 / chance) : null;

  let bodyTakeaway = '';
  if (body) {
    const end = body[body.length - 1]!.mean;
    const d = direction(body);
    if (ceChance !== null && Math.abs(end - ceChance) / ceChance < 0.1) {
      bodyTakeaway = 'stays near chance: the encoder hides which planet a window is from';
    } else if (ceChance !== null && end < ceChance) {
      bodyTakeaway = `${d === 'falls' ? 'falls' : 'sits'} as the adversary trains alongside`;
    } else {
      bodyTakeaway = d === 'flat' ? 'stays roughly flat over training' : `${d} over training`;
    }
  }
  const takeaway = (epochs: Epoch[], falling: string) => {
    const d = direction(epochs);
    return d === 'falls' ? falling : d === 'rises' ? 'rises over training' : 'stays roughly flat over training';
  };

  const panels = [
    vic && { key: 'vicreg', title: 'Self-supervised loss', color: '#CC5800', epochs: vic, takeaway: takeaway(vic, 'falls as the two views learn to agree') },
    phys && { key: 'phys', title: 'Physics error', color: '#4A8044', epochs: phys, takeaway: takeaway(phys, 'falls as the encoder learns the fingerprint') },
    body && {
      key: 'body',
      title: 'Planet classifier loss',
      color: '#7AA7C7',
      epochs: body,
      takeaway: bodyTakeaway,
      reference: ceChance !== null && nClasses !== null ? { value: ceChance, label: `chance, ln ${nClasses} = ${ceChance.toFixed(3)}` } : undefined,
    },
  ].filter((p): p is Exclude<typeof p, undefined> => Boolean(p));

  return (
    <figure style={{ margin: 0, width: '100%', color: INK, fontFamily: SANS }}>
      {curves.mock ? (
        <div role="alert" style={{ background: '#FDECDF', color: '#8A3B00', fontSize: 12, fontWeight: 500, padding: '6px 10px', borderRadius: 6, marginBottom: 16 }}>
          Mock preview: report/curves.json is marked mock. Not for production.
        </div>
      ) : null}
      {panels.length === 0 ? (
        <p style={{ fontSize: 13, color: MUTED }}>Unavailable: report/curves.json lists no VICReg, physics or body loss term.</p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 20 }}>
          {panels.map((p) => (
            <Panel
              key={p.key}
              title={p.title}
              takeaway={p.takeaway}
              color={p.color}
              epochs={p.epochs}
              epochOffset={epochOffset}
              reference={'reference' in p ? p.reference : undefined}
            />
          ))}
        </div>
      )}
      <figcaption style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 12, marginTop: 16 }}>
        <span style={{ fontSize: 13, color: BODY }}>{`Figure 2. Training losses per epoch, mean over ${seeds} seeds.`}</span>
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: MUTED }}>
          {curves.mock ? 'Mock' : 'Measured'} · shaded band min to max over seeds · report/curves.json
          {curves.run_id ? ` · run ${curves.run_id.slice(7, 15)}` : ''}
        </span>
      </figcaption>
    </figure>
  );
}
