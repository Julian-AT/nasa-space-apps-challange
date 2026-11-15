// Benchmark figures for the pitch deck: training throughput, browser parity and relief distance to
// each target. Hand-written SVG bar charts. Every value is read by buildBenchData from shared
// artifacts (the release bundle's cards and model card, and the paper's generated numbers.tex);
// nothing is typed in, and a value that cannot be read renders as Unavailable.
import type { ReactNode } from 'react';

import type { PulledBundle } from '@/lib/bundle';

export interface ReliefTarget {
  readonly targetId: string;
  readonly targetName: string;
  /** Earth search region of the closest card for this target. */
  readonly earthName: string | null;
  /** Lowest terrain_relief distance over this target's cards. */
  readonly best: number | null;
  /** Median internal relief distance of the target site's reference set (numbers.tex). */
  readonly siteMedian: number | null;
}

export interface BenchData {
  readonly bundleVersion: string;
  readonly mock: boolean;
  readonly throughput: { readonly cpuMedian: number | null; readonly mpsMedian: number | null };
  readonly parity: {
    readonly minCosine: number | null;
    readonly minCosineText: string | null;
    readonly tolerance: number | null;
    readonly windows: number | null;
  };
  readonly relief: { readonly unit: string | null; readonly targets: readonly ReliefTarget[] };
  readonly parameters: number | null;
  readonly trainWindows: number | null;
}

const TARGET_ORDER = ['mars-jezero-w000', 'mars-arcadia-w000', 'moon-cr-site01-w000'] as const;
// Display names, as in pitch-data.ts (not imported: that module reads files and this one must stay
// safe for client components).
const TARGET_NAMES: Record<string, string> = {
  'mars-jezero-w000': 'Jezero crater',
  'mars-arcadia-w000': 'Arcadia Planitia',
  'moon-cr-site01-w000': 'Connecting Ridge',
};
const targetName = (id: string): string => TARGET_NAMES[id] ?? id;
const SITE_MEDIAN_KEY: Record<string, string> = {
  'mars-jezero-w000': 'JezeroRefReliefMedian',
  'mars-arcadia-w000': 'ArcadiaRefReliefMedian',
  'moon-cr-site01-w000': 'MoonRefReliefMedian',
};

function regionName(id: string): string {
  return id
    .split('-')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

function num(v: string | undefined): number | null {
  if (v === undefined) return null;
  const n = Number(v.replace(/,/g, ''));
  return Number.isFinite(n) ? n : null;
}

/**
 * Builds the figure data from the bundle and the `\newcommand` values of paper/generated/numbers.tex
 * (name without backslash to raw value). Pure: the caller reads the files.
 */
export function buildBenchData(bundle: PulledBundle, numbers: ReadonlyMap<string, string>): BenchData {
  const cards = [...bundle.records.cards.values()];
  let unit: string | null = null;
  const targets: ReliefTarget[] = TARGET_ORDER.map((id) => {
    let best: number | null = null;
    let earth: string | null = null;
    for (const card of cards) {
      if (card.target.target_id !== id) continue;
      const rel = card.distances_by_family.terrain_relief;
      if (rel.status !== 'available' || rel.value === null) continue;
      unit = rel.unit;
      if (best === null || rel.value < best) {
        best = rel.value;
        earth = regionName(card.earth.search_region_id ?? card.earth.earth_window_id);
      }
    }
    const key = SITE_MEDIAN_KEY[id];
    return { targetId: id, targetName: targetName(id), earthName: earth, best, siteMedian: key === undefined ? null : num(numbers.get(key)) };
  });

  const mc = bundle.records.modelCard;
  let trainWindows: number | null = null;
  if (mc !== null) {
    const counts = Object.values(mc.training_data.window_counts) as { train?: number }[];
    trainWindows = counts.reduce((s, c) => s + (c.train ?? 0), 0);
  }

  return {
    bundleVersion: bundle.manifest.bundle_version,
    mock: bundle.manifest.mock,
    throughput: { cpuMedian: num(numbers.get('ThruCpuMedian')), mpsMedian: num(numbers.get('ThruMpsTwelveMedian')) },
    parity: {
      minCosine: num(numbers.get('WasmMinCosine')),
      minCosineText: numbers.get('WasmMinCosine') ?? null,
      tolerance: num(numbers.get('WasmThreshold')),
      windows: num(numbers.get('WasmWindows')),
    },
    relief: { unit, targets },
    parameters: mc?.parameter_count ?? null,
    trainWindows,
  };
}

// ---------------------------------------------------------------------------------------------
// Drawing

const INK = '#1A1613';
const MUTED = '#A39E95';
const AXIS = 'rgba(26,22,19,0.18)';
const SANS = 'var(--pitch-sans, var(--font-dm-sans)), ui-sans-serif, system-ui, sans-serif';
const MONO = 'var(--pitch-mono, var(--font-dm-mono)), ui-monospace, monospace';

interface Series {
  readonly fill: string;
  readonly stroke: string;
  readonly text: string;
}
const ORANGE: Series = { fill: '#FFE8CC', stroke: '#CC5800', text: '#CC5800' };
const GREEN: Series = { fill: '#DCEBD8', stroke: '#4A8044', text: '#4A8044' };
const BLUE: Series = { fill: '#DCEAF3', stroke: '#7AA7C7', text: '#5E8CAD' };
const LIGHT: Series = { fill: '#F6F3EE', stroke: '#CFC8BD', text: MUTED };

interface Bar {
  readonly value: number | null;
  readonly display: string;
  readonly series: Series;
}
interface Group {
  readonly label: readonly string[];
  readonly bars: readonly Bar[];
}

const VB_W = 360;
const VB_H = 272;
const MARGIN = { top: 26, right: 8, bottom: 44 };
const PLOT_H = VB_H - MARGIN.top - MARGIN.bottom;

function niceStep(raw: number): number {
  const p = 10 ** Math.floor(Math.log10(raw));
  const f = raw / p;
  const n = [1, 2, 2.5, 5, 10].find((c) => c >= f - 1e-9) ?? 10;
  return n * p;
}

function decimalsOf(step: number): number {
  const s = step.toString();
  const i = s.indexOf('.');
  return i < 0 ? 0 : s.length - i - 1;
}

function BarChart({
  groups,
  yMin,
  yMax,
  step,
  axisTitle,
  barW,
  refLine,
  ariaLabel,
}: {
  groups: readonly Group[];
  yMin: number;
  yMax: number;
  step: number;
  axisTitle: string;
  barW: number;
  refLine?: { value: number; label: string } | undefined;
  ariaLabel: string;
}) {
  const y = (v: number) => MARGIN.top + PLOT_H - ((Math.min(Math.max(v, yMin), yMax) - yMin) / (yMax - yMin)) * PLOT_H;
  const ticks: number[] = [];
  for (let t = yMin; t <= yMax + step * 1e-6; t += step) ticks.push(Number(t.toFixed(10)));
  const dec = decimalsOf(step);
  const tickText = (t: number) => t.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
  // left margin: rotated axis title, then the widest tick label (11px digits are about 6.3 units)
  const left = 30 + Math.max(...ticks.map((t) => tickText(t).length)) * 6.3;
  const M = { ...MARGIN, left };
  const PLOT_W = VB_W - M.left - M.right;
  const slot = PLOT_W / groups.length;
  const grouped = groups.some((g) => g.bars.length > 1);
  const gap = grouped ? 14 : 4;
  const valueSize = grouped ? 12 : 13;
  const base = y(yMin);

  return (
    <svg viewBox={`0 0 ${VB_W} ${VB_H}`} width="100%" role="img" aria-label={ariaLabel} style={{ display: 'block', overflow: 'visible' }}>
      {/* y axis */}
      <line x1={M.left} x2={M.left} y1={M.top - 6} y2={base} stroke={AXIS} strokeWidth={1} />
      {ticks.map((t) => (
        <g key={t}>
          <line x1={M.left - 4} x2={M.left} y1={y(t)} y2={y(t)} stroke={AXIS} strokeWidth={1} />
          <text x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill={MUTED} fontFamily={SANS}>
            {tickText(t)}
          </text>
        </g>
      ))}
      <text
        transform={`translate(10 ${M.top + PLOT_H / 2}) rotate(-90)`}
        textAnchor="middle"
        fontSize={12}
        fill={MUTED}
        fontFamily={SANS}
      >
        {axisTitle}
      </text>
      {/* baseline */}
      <line x1={M.left} x2={VB_W - M.right} y1={base} y2={base} stroke={AXIS} strokeWidth={1} />

      {refLine !== undefined && (
        <g>
          <line x1={M.left} x2={VB_W - M.right} y1={y(refLine.value)} y2={y(refLine.value)} stroke={INK} strokeOpacity={0.55} strokeWidth={1} strokeDasharray="4 3" />
          <text x={VB_W - M.right} y={y(refLine.value) - 5} textAnchor="end" fontSize={11} fill={INK} fillOpacity={0.7} fontFamily={MONO}>
            {refLine.label}
          </text>
        </g>
      )}

      {groups.map((g, gi) => {
        const cx = M.left + slot * gi + slot / 2;
        const total = g.bars.length * barW + (g.bars.length - 1) * gap;
        return (
          <g key={gi}>
            {g.bars.map((b, bi) => {
              const x = cx - total / 2 + bi * (barW + gap);
              if (b.value === null) {
                return (
                  <text key={bi} x={x + barW / 2} y={base - 8} textAnchor="middle" fontSize={11} fill={MUTED} fontFamily={SANS}>
                    Unavailable
                  </text>
                );
              }
              const top = y(b.value);
              const h = Math.max(base - top, 1);
              return (
                <g key={bi}>
                  <rect x={x + 0.5} y={base - h + 0.5} width={barW - 1} height={h - 0.5} fill={b.series.fill} stroke={b.series.stroke} strokeWidth={1} shapeRendering="crispEdges" />
                  <text x={x + barW / 2} y={base - h - 7} textAnchor="middle" fontSize={valueSize} fill={b.series.text} fontFamily={MONO}>
                    {b.display}
                  </text>
                </g>
              );
            })}
            {g.label.map((line, li) => (
              <text key={li} x={cx} y={base + 18 + li * 15} textAnchor="middle" fontSize={12} fill={MUTED} fontFamily={SANS}>
                {line}
              </text>
            ))}
          </g>
        );
      })}
    </svg>
  );
}

function LegendItem({ series, dashed, children }: { series?: Series; dashed?: boolean; children: ReactNode }) {
  return (
    <li style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      {dashed ? (
        <svg width={16} height={8} aria-hidden="true">
          <line x1={0} x2={16} y1={4} y2={4} stroke={INK} strokeOpacity={0.55} strokeDasharray="4 3" />
        </svg>
      ) : (
        <span
          aria-hidden="true"
          style={{ width: 8, height: 8, borderRadius: 999, background: series?.fill, border: `1px solid ${series?.stroke}`, display: 'inline-block' }}
        />
      )}
      <span>{children}</span>
    </li>
  );
}

function FigureCard({
  title,
  better,
  legend,
  figure,
  note,
  children,
}: {
  title: string;
  better: 'Higher is better' | 'Lower is better';
  legend: ReactNode;
  figure: number;
  note?: string | undefined;
  children: ReactNode;
}) {
  return (
    <figure
      style={{
        margin: 0,
        background: '#FFFFFF',
        border: '1px solid rgba(26,22,19,0.10)',
        borderRadius: 12,
        padding: 28,
        display: 'flex',
        flexDirection: 'column',
        minWidth: 0,
      }}
    >
      <h3 style={{ margin: 0, fontFamily: SANS, fontWeight: 500, fontSize: 20, lineHeight: 1.25, color: INK, letterSpacing: '-0.01em' }}>{title}</h3>
      <p style={{ margin: '6px 0 0', fontFamily: SANS, fontSize: 13, color: MUTED }}>{better}</p>
      <ul style={{ listStyle: 'none', margin: '16px 0 0', padding: 0, display: 'grid', gap: 6, fontFamily: SANS, fontSize: 13, color: INK }}>{legend}</ul>
      <div style={{ marginTop: 18, flex: 1 }}>{children}</div>
      <figcaption style={{ marginTop: 14, display: 'flex', justifyContent: 'space-between', gap: 12, fontFamily: SANS, fontSize: 13, color: INK, opacity: 0.8 }}>
        <span>Figure {figure}</span>
        {note !== undefined && <span style={{ color: MUTED }}>{note}</span>}
      </figcaption>
    </figure>
  );
}

const fmtInt = (v: number) => Math.round(v).toLocaleString('en-US');
const fmt3 = (v: number) => v.toFixed(3);

export function FigBenchmarks({ data }: { data: BenchData }) {
  const { throughput, parity, relief } = data;

  // (a) throughput
  const thrMax = Math.max(throughput.cpuMedian ?? 0, throughput.mpsMedian ?? 0, 1);
  const thrStep = niceStep(thrMax / 5);
  const thrTop = Math.ceil(thrMax / thrStep) * thrStep;

  // (b) parity: zoomed axis 0.998 to 1.0
  const PAR_MIN = 0.998;
  const PAR_MAX = 1.0;

  // (c) relief
  const relMax = Math.max(...relief.targets.flatMap((t) => [t.best ?? 0, t.siteMedian ?? 0]), 0.1);
  const relStep = niceStep(relMax / 5);
  const relTop = Math.ceil(relMax / relStep) * relStep;
  const anyMedian = relief.targets.some((t) => t.siteMedian !== null);

  const params = data.parameters === null ? null : `${(data.parameters / 1e6).toFixed(1)}M parameters`;
  const train = data.trainWindows === null ? null : `${fmtInt(data.trainWindows)} training windows`;
  const footer = [params, train].filter((s): s is string => s !== null).join(', ');

  return (
    <div style={{ fontFamily: SANS, color: INK }}>
      {data.mock && (
        <p role="status" style={{ margin: '0 0 16px', padding: '8px 12px', borderRadius: 8, background: '#FFE8CC', color: '#7A3500', fontSize: 13 }}>
          Mock preview: values come from a mock bundle and are not results.
        </p>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 20 }}>
        <FigureCard
          title="Training throughput"
          better="Higher is better"
          figure={5}
          note="median"
          legend={
            <>
              <LegendItem series={ORANGE}>CPU server</LegendItem>
              <LegendItem series={GREEN}>Laptop GPU (Apple MPS)</LegendItem>
            </>
          }
        >
          <BarChart
            ariaLabel={`Training throughput in windows per second: CPU server ${throughput.cpuMedian ?? 'unavailable'}, laptop GPU ${throughput.mpsMedian ?? 'unavailable'}`}
            axisTitle="Windows per second"
            yMin={0}
            yMax={thrTop}
            step={thrStep}
            barW={44}
            groups={[
              { label: ['CPU', 'server'], bars: [{ value: throughput.cpuMedian, display: throughput.cpuMedian === null ? '' : fmtInt(throughput.cpuMedian), series: ORANGE }] },
              { label: ['Laptop GPU', '(MPS)'], bars: [{ value: throughput.mpsMedian, display: throughput.mpsMedian === null ? '' : fmtInt(throughput.mpsMedian), series: GREEN }] },
            ]}
          />
        </FigureCard>

        <FigureCard
          title="Browser parity"
          better="Higher is better"
          figure={6}
          note="axis starts at 0.998"
          legend={
            <>
              <LegendItem series={BLUE}>
                Lowest cosine to the training model{parity.windows === null ? '' : `, ${fmtInt(parity.windows)} windows`}
              </LegendItem>
              {parity.tolerance !== null && <LegendItem dashed>Tolerance {parity.tolerance}</LegendItem>}
            </>
          }
        >
          <BarChart
            ariaLabel={`Browser parity: lowest cosine ${parity.minCosineText ?? 'unavailable'} against a tolerance of ${parity.tolerance ?? 'unavailable'}; axis from 0.998 to 1.0`}
            axisTitle="Cosine similarity"
            yMin={PAR_MIN}
            yMax={PAR_MAX}
            step={0.0005}
            barW={44}
            refLine={parity.tolerance === null ? undefined : { value: parity.tolerance, label: `tolerance ${parity.tolerance}` }}
            groups={[
              { label: ['Browser', '(ONNX, WASM)'], bars: [{ value: parity.minCosine, display: parity.minCosineText ?? '', series: BLUE }] },
            ]}
          />
        </FigureCard>

        <FigureCard
          title="Relief distance to target"
          better="Lower is better"
          figure={7}
          note={relief.unit ?? undefined}
          legend={
            <>
              <LegendItem series={ORANGE}>Best Earth match</LegendItem>
              {anyMedian && <LegendItem series={LIGHT}>Median distance within the target site</LegendItem>}
            </>
          }
        >
          <BarChart
            ariaLabel={`Relief distance to target, lower is better. ${relief.targets
              .map((t) => `${t.targetName}: best ${t.best === null ? 'unavailable' : fmt3(t.best)}${t.siteMedian === null ? '' : `, site median ${t.siteMedian}`}`)
              .join('; ')}`}
            axisTitle="Relief distance"
            yMin={0}
            yMax={relTop}
            step={relStep}
            barW={anyMedian ? 24 : 36}
            groups={relief.targets.map((t) => ({
              label: [t.targetName.replace(/ crater$/, ''), t.earthName === null ? '' : `vs ${t.earthName}`],
              bars: [
                { value: t.best, display: t.best === null ? '' : fmt3(t.best), series: ORANGE },
                ...(anyMedian ? [{ value: t.siteMedian, display: t.siteMedian === null ? '' : t.siteMedian.toFixed(2), series: LIGHT }] : []),
              ],
            }))}
          />
        </FigureCard>
      </div>
      <p style={{ margin: '20px 2px 0', fontFamily: SANS, fontSize: 13, color: MUTED, display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <span>{footer}</span>
        <span style={{ fontFamily: MONO }}>bundle {data.bundleVersion}</span>
      </p>
    </div>
  );
}
