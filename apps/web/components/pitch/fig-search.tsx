// "We searched every Earth window": for each target, a beeswarm strip of the terrain_relief
// distance of every candidate Earth window in the release bundle's comparisons/<target_id>.json,
// on a shared log axis. Dots are coloured by Earth search region, the closest window is
// highlighted and windows that became evidence cards are ringed. Every value is read by
// buildSearchData from the bundle; a window without a relief value is counted as unavailable and
// never drawn as zero. Hover and focus tooltips are CSS-only, so the component renders in server
// and client trees alike. Hand-written SVG.
import type { PulledBundle } from '@/lib/bundle';
import { earthRegionName } from '@/components/cards/card-geo';

export interface SearchWindow {
  readonly windowId: string;
  readonly regionId: string;
  readonly regionName: string;
  /** terrain_relief distance; null when the bundle marks it unavailable. */
  readonly relief: number | null;
  readonly texture: number | null;
  /** True when this window became an evidence card for the target. */
  readonly card: boolean;
}

export interface SearchTarget {
  readonly targetId: string;
  readonly targetName: string;
  readonly body: string;
  readonly windows: readonly SearchWindow[];
}

export interface SearchData {
  readonly bundleVersion: string;
  readonly mock: boolean;
  /** Unit of terrain_relief as the bundle states it. */
  readonly reliefUnit: string | null;
  readonly textureUnit: string | null;
  readonly targets: readonly SearchTarget[];
}

const TARGET_ORDER = ['mars-jezero-w000', 'mars-arcadia-w000', 'moon-cr-site01-w000'] as const;
// Display names, as in pitch-data.ts (not imported: that module reads files).
const TARGET_NAMES: Record<string, string> = {
  'mars-jezero-w000': 'Jezero crater',
  'mars-arcadia-w000': 'Arcadia Planitia',
  'moon-cr-site01-w000': 'Connecting Ridge',
};
const BODY_NAMES: Record<string, string> = { mars: 'Mars', moon: 'Moon' };
// Last-resort region from the window id's UTM zone, used only if neither the window record nor a
// card names the search region.
const ZONE_REGION: Record<string, string> = { utm11n: 'rosamond-dry-lake', utm27n: 'holuhraun' };

/** Server-side: collects every comparison record of each target from the bundle. */
export function buildSearchData(bundle: PulledBundle): SearchData {
  const { records } = bundle;
  const regionByWindow = new Map<string, string>();
  for (const w of records.windows.get('earth') ?? []) {
    const id = w.window.environment_flags.search_region_id;
    if (typeof id === 'string') regionByWindow.set(w.window.window_id, id);
  }
  const cardWindows = new Map<string, Set<string>>();
  for (const card of records.cards.values()) {
    const set = cardWindows.get(card.target.target_id) ?? new Set<string>();
    set.add(card.earth.earth_window_id);
    cardWindows.set(card.target.target_id, set);
    if (!regionByWindow.has(card.earth.earth_window_id) && card.earth.search_region_id !== null) {
      regionByWindow.set(card.earth.earth_window_id, card.earth.search_region_id);
    }
  }
  let reliefUnit: string | null = null;
  let textureUnit: string | null = null;
  const ids = [...records.comparisons.keys()].sort(
    (a, b) => rank(a) - rank(b) || a.localeCompare(b),
  );
  const targets: SearchTarget[] = ids.map((targetId) => {
    const list = records.comparisons.get(targetId) ?? [];
    const cards = cardWindows.get(targetId) ?? new Set<string>();
    const windows = list.map((rec): SearchWindow => {
      const relief = rec.distances_by_family.terrain_relief;
      const texture = rec.distances_by_family.terrain_texture;
      reliefUnit ??= relief?.unit ?? null;
      textureUnit ??= texture?.unit ?? null;
      const zone = rec.earth_window_id.split('-')[1] ?? '';
      const regionId = regionByWindow.get(rec.earth_window_id) ?? ZONE_REGION[zone] ?? 'unknown';
      return {
        windowId: rec.earth_window_id,
        regionId,
        regionName: earthRegionName(regionId),
        relief: relief?.status === 'available' && typeof relief.value === 'number' ? relief.value : null,
        texture: texture?.status === 'available' && typeof texture.value === 'number' ? texture.value : null,
        card: cards.has(rec.earth_window_id),
      };
    });
    return {
      targetId,
      targetName: TARGET_NAMES[targetId] ?? targetId,
      body: targetId.split('-')[0] ?? '',
      windows,
    };
  });
  return {
    bundleVersion: bundle.manifest.bundle_version,
    mock: bundle.manifest.mock,
    reliefUnit,
    textureUnit,
    targets,
  };
}

function rank(id: string): number {
  const i = (TARGET_ORDER as readonly string[]).indexOf(id);
  return i < 0 ? TARGET_ORDER.length : i;
}

// ---------------------------------------------------------------------------------------------
// Rendering

const INK = '#1A1613';
const SECONDARY = '#4D4843';
const MUTED = '#A39E95';
const BORDER = 'rgba(26,22,19,0.10)';
const BEST = '#CC5800';
const REGION_COLOURS: Record<string, string> = { holuhraun: '#7AA7C7', 'rosamond-dry-lake': '#4A8044' };
const OTHER = '#C9C5BC';
const SANS = 'var(--pitch-sans, ui-sans-serif), ui-sans-serif, system-ui, sans-serif';
const MONO = 'var(--pitch-mono, ui-monospace), ui-monospace, SFMono-Regular, monospace';

const W = 400;
const PAD_L = 14;
const PAD_R = 14;
const SWARM_H = 156;
const LABEL_Y = 14;
const AXIS_Y = SWARM_H + 6;
const H = AXIS_Y + 30;
const R = 4;
const TICKS = [0.05, 0.1, 0.2, 0.5, 1, 2, 5];

function fmt(v: number): string {
  return v < 1 ? v.toFixed(3) : v.toFixed(2);
}

function median(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 === 1 ? (s[m] as number) : ((s[m - 1] as number) + (s[m] as number)) / 2;
}

interface Placed {
  readonly w: SearchWindow & { readonly relief: number };
  readonly x: number;
  readonly y: number;
}

/** Deterministic beeswarm: each dot, in x order, takes the free offset nearest the centre line. */
function swarm(windows: readonly (SearchWindow & { relief: number })[], sx: (v: number) => number): Placed[] {
  const placed: Placed[] = [];
  const mid = (LABEL_Y + 14 + SWARM_H) / 2 + 3;
  const step = R * 2 + 0.8;
  const sorted = [...windows].sort((a, b) => a.relief - b.relief || a.windowId.localeCompare(b.windowId));
  for (const w of sorted) {
    const x = sx(w.relief);
    const near = placed.filter((p) => Math.abs(p.x - x) < step);
    for (let k = 0; k < 200; k += 1) {
      const off = k === 0 ? 0 : Math.ceil(k / 2) * (k % 2 === 1 ? -1 : 1) * (step / 2);
      const y = mid + off;
      if (near.every((p) => (p.x - x) ** 2 + (p.y - y) ** 2 >= step * step)) {
        placed.push({ w, x, y });
        break;
      }
    }
  }
  return placed;
}

export function FigSearch({ data }: { data: SearchData }) {
  const all = data.targets.flatMap((t) => t.windows.map((w) => w.relief)).filter((v): v is number => v !== null && v > 0);
  const lo = all.length > 0 ? Math.min(0.04, Math.min(...all) * 0.85) : 0.04;
  const hi = all.length > 0 ? Math.max(5.5, Math.max(...all) * 1.1) : 5.5;
  const sx = (v: number) => PAD_L + ((Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * (W - PAD_L - PAD_R);
  const regions = [...new Set(data.targets.flatMap((t) => t.windows.map((w) => w.regionId)))].sort();

  return (
    <figure style={{ margin: 0, fontFamily: SANS, color: INK }}>
      {data.mock && (
        <div role="status" style={{ marginBottom: 16, padding: '8px 12px', border: `1px solid ${BEST}`, borderRadius: 8, color: BEST, fontSize: 14 }}>
          Mock preview: values come from a mock bundle and are not results.
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.max(1, data.targets.length)}, minmax(0, 1fr))`, gap: 20 }}>
        {data.targets.map((t) => (
          <TargetCard key={t.targetId} target={t} sx={sx} lo={lo} hi={hi} reliefUnit={data.reliefUnit} textureUnit={data.textureUnit} />
        ))}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 20, marginTop: 16, fontSize: 13, color: SECONDARY }}>
        {regions.map((r) => (
          <span key={r} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill={REGION_COLOURS[r] ?? OTHER} /></svg>
            {earthRegionName(r)}
          </span>
        ))}
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill={BEST} /></svg>
          Best match
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="4.4" fill="none" stroke={INK} strokeWidth="1.2" /></svg>
          Became an evidence card
        </span>
      </div>
      <figcaption style={{ marginTop: 14, fontSize: 14, lineHeight: 1.5, color: SECONDARY, display: 'flex', justifyContent: 'space-between', gap: 24 }}>
        <span>
          <span style={{ color: INK, fontWeight: 500 }}>Figure 1.</span> Relief distance of every candidate Earth window to the target. Lower is more alike.
          {data.reliefUnit !== null && <> Distance in {data.reliefUnit}, log scale.</>} Terrain only; a candidate is not a proven analog.
        </span>
        <span style={{ fontFamily: MONO, fontSize: 12, color: MUTED, whiteSpace: 'nowrap' }}>bundle {data.bundleVersion}</span>
      </figcaption>
    </figure>
  );
}

function TargetCard({
  target,
  sx,
  lo,
  hi,
  reliefUnit,
  textureUnit,
}: {
  target: SearchTarget;
  sx: (v: number) => number;
  lo: number;
  hi: number;
  reliefUnit: string | null;
  textureUnit: string | null;
}) {
  const avail = target.windows.filter((w): w is SearchWindow & { relief: number } => w.relief !== null && w.relief > 0);
  const missing = target.windows.length - avail.length;
  const placed = swarm(avail, sx);
  const best = placed[0] ?? null;
  const med = median(avail.map((w) => w.relief));
  const ratio = best !== null && med !== null && best.w.relief > 0 ? med / best.w.relief : null;
  const ticks = TICKS.filter((v) => v >= lo && v <= hi);
  const medX = med !== null ? sx(med) : null;
  // The best label reads rightward unless the median label would sit on it.
  const bestLabelRight = best !== null && (medX === null || medX - best.x > 120 || medX < best.x) && best.x < W * 0.7;

  return (
    <div style={{ background: '#FFFFFF', border: `1px solid ${BORDER}`, borderRadius: 12, padding: '20px 20px 14px' }}>
      <div style={{ fontSize: 13, color: MUTED, fontWeight: 500, letterSpacing: '0.02em' }}>
        {BODY_NAMES[target.body] ?? target.body} target
      </div>
      <div style={{ fontSize: 20, fontWeight: 500, marginTop: 2 }}>{target.targetName}</div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginTop: 14 }}>
        <span style={{ fontSize: 40, fontWeight: 500, letterSpacing: '-0.02em', color: best !== null ? BEST : MUTED, fontVariantNumeric: 'tabular-nums' }}>
          {best !== null ? fmt(best.w.relief) : 'Unavailable'}
        </span>
        <span style={{ fontSize: 14, color: SECONDARY, lineHeight: 1.35 }}>
          best match
          <br />
          {target.windows.length} windows searched{missing > 0 ? `, ${missing} unavailable` : ''}
        </span>
      </div>
      <div style={{ fontSize: 14, color: SECONDARY, marginTop: 6, minHeight: 21 }}>
        {best !== null && ratio !== null && (
          <>
            {best.w.regionName.split(',')[0]}, {ratio.toFixed(0)}x closer than the median window
          </>
        )}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={`${target.targetName}: relief distance of ${avail.length} Earth windows${best !== null ? `, best ${fmt(best.w.relief)}` : ''}${med !== null ? `, median ${fmt(med)}` : ''}`} style={{ display: 'block', marginTop: 10, overflow: 'visible' }}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={sx(v)} x2={sx(v)} y1={LABEL_Y + 14} y2={AXIS_Y} stroke={BORDER} strokeWidth="1" />
            <text x={sx(v)} y={AXIS_Y + 16} textAnchor="middle" fontSize="10.5" fill={MUTED} style={{ fontFamily: MONO }}>
              {v}
            </text>
          </g>
        ))}
        <line x1={PAD_L} x2={W - PAD_R} y1={AXIS_Y} y2={AXIS_Y} stroke="rgba(26,22,19,0.18)" strokeWidth="1" />
        {med !== null && (
          <g>
            <line x1={sx(med)} x2={sx(med)} y1={LABEL_Y + 5} y2={AXIS_Y} stroke={SECONDARY} strokeWidth="1" strokeDasharray="2 3" />
            <text x={sx(med)} y={LABEL_Y} textAnchor="middle" fontSize="11" fill={SECONDARY} style={{ fontFamily: SANS }}>
              median {fmt(med)}
            </text>
          </g>
        )}
        {placed.map((p) => {
          const isBest = best !== null && p.w.windowId === best.w.windowId;
          return (
            <circle
              key={p.w.windowId}
              cx={p.x}
              cy={p.y}
              r={isBest ? R + 1.6 : R}
              fill={isBest ? BEST : (REGION_COLOURS[p.w.regionId] ?? OTHER)}
              stroke={p.w.card ? INK : '#FFFFFF'}
              strokeWidth={p.w.card ? 1.2 : 0.6}
            />
          );
        })}
        {best !== null && (
          <g>
            <line x1={best.x} x2={best.x} y1={LABEL_Y + 5} y2={best.y - R - 3} stroke={BEST} strokeWidth="1" />
            <text
              x={bestLabelRight ? best.x - 2 : best.x + 2}
              y={LABEL_Y}
              textAnchor={bestLabelRight ? 'start' : 'end'}
              fontSize="12"
              fontWeight="500"
              fill={BEST}
              style={{ fontFamily: SANS }}
            >
              {best.w.regionName.split(',')[0]}, {fmt(best.w.relief)}
            </text>
          </g>
        )}
        {/* Hover and focus targets, drawn last so a tooltip sits above every dot. */}
        {placed.map((p) => {
          const tw = 232;
          const th = 70;
          const tx = Math.min(Math.max(p.x - tw / 2, 0), W - tw);
          const above = p.y > th + 14;
          const ty = above ? p.y - th - 10 : p.y + 10;
          return (
            <g key={`hit-${p.w.windowId}`} className="group" tabIndex={0} style={{ outline: 'none', cursor: 'default' }}>
              <title>{`${p.w.windowId}, ${p.w.regionName}, relief ${fmt(p.w.relief)}${p.w.texture !== null ? `, texture ${fmt(p.w.texture)}` : ', texture unavailable'}`}</title>
              <circle cx={p.x} cy={p.y} r={R + 1.5} fill="transparent" />
              <circle cx={p.x} cy={p.y} r={R + 2.4} fill="none" stroke={INK} strokeWidth="1.2" className="opacity-0 group-hover:opacity-100 group-focus:opacity-100" />
              <g className="pointer-events-none opacity-0 transition-opacity group-hover:opacity-100 group-focus:opacity-100">
                <rect x={tx} y={ty} width={tw} height={th} rx="6" fill="#FFFFFF" stroke="rgba(26,22,19,0.16)" />
                <text x={tx + 10} y={ty + 17} fontSize="10.5" fill={INK} style={{ fontFamily: MONO }}>
                  {p.w.windowId}
                </text>
                <text x={tx + 10} y={ty + 33} fontSize="11" fill={SECONDARY} style={{ fontFamily: SANS }}>
                  {p.w.regionName}{p.w.card ? ' · evidence card' : ''}
                </text>
                <text x={tx + 10} y={ty + 49} fontSize="11" fill={INK} style={{ fontFamily: SANS }}>
                  relief {fmt(p.w.relief)}
                  {reliefUnit !== null ? ` ${reliefUnit}` : ''}
                </text>
                <text x={tx + 10} y={ty + 63} fontSize="11" fill={INK} style={{ fontFamily: SANS }}>
                  texture {p.w.texture !== null ? fmt(p.w.texture) : 'Unavailable'}
                  {p.w.texture !== null && textureUnit !== null ? ` ${textureUnit}` : ''}
                </text>
              </g>
            </g>
          );
        })}
        <text x={PAD_L} y={AXIS_Y + 29} fontSize="10.5" fill={MUTED} style={{ fontFamily: SANS }}>
          more alike
        </text>
        <text x={W - PAD_R} y={AXIS_Y + 29} textAnchor="end" fontSize="10.5" fill={MUTED} style={{ fontFamily: SANS }}>
          less alike
        </text>
      </svg>
    </div>
  );
}
