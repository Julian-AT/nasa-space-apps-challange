// "Results": a comparison grid, one row per target and one column per searched Earth region. Each
// cell shows the best terrain_relief distance found in that region and how many times closer it is
// than the target's median window over all regions, with a thin bar on a shared log scale. The
// overall best per row is highlighted. Every value is computed from the bundle's comparison records
// via buildSearchData; a region without relief values shows Unavailable, never zero.
import type { SearchData, SearchTarget } from './fig-search';

const INK = '#1A1613';
const SECONDARY = '#4D4843';
const MUTED = '#A39E95';
const HAIR = 'rgba(26,22,19,0.10)';
const BEST = '#CC5800';
const TRACK = '#EFECE6';
const BODY_DOT: Record<string, string> = { mars: '#E08A5F', moon: '#A39E95' };
const BODY_NAME: Record<string, string> = { mars: 'Mars', moon: 'Moon' };
const REGION_COLOURS: Record<string, string> = { holuhraun: '#7AA7C7', 'rosamond-dry-lake': '#4A8044' };
const REGION_ORDER = ['holuhraun', 'rosamond-dry-lake'];
const MONO = 'var(--pitch-mono, ui-monospace), ui-monospace, SFMono-Regular, monospace';

function fmt(v: number): string {
  return v < 1 ? v.toFixed(3) : v.toFixed(2);
}

function median(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 === 1 ? (s[m] as number) : ((s[m - 1] as number) + (s[m] as number)) / 2;
}

interface Region {
  readonly id: string;
  readonly name: string;
}

interface Cell {
  readonly best: number | null;
  readonly searched: number;
}

interface Row {
  readonly target: SearchTarget;
  readonly med: number | null;
  readonly cells: readonly Cell[];
  readonly bestIdx: number | null;
}

function regionsOf(data: SearchData): Region[] {
  const names = new Map<string, string>();
  for (const t of data.targets) for (const w of t.windows) if (!names.has(w.regionId)) names.set(w.regionId, w.regionName);
  const rank = (id: string) => {
    const k = REGION_ORDER.indexOf(id);
    return k === -1 ? REGION_ORDER.length : k;
  };
  return [...names.entries()].map(([id, name]) => ({ id, name })).sort((a, b) => rank(a.id) - rank(b.id) || a.name.localeCompare(b.name));
}

function row(target: SearchTarget, regions: readonly Region[]): Row {
  const avail = target.windows.filter((w) => w.relief !== null && w.relief > 0);
  const cells = regions.map((r) => {
    const vals = avail.filter((w) => w.regionId === r.id).map((w) => w.relief as number);
    return { best: vals.length > 0 ? Math.min(...vals) : null, searched: target.windows.filter((w) => w.regionId === r.id).length };
  });
  let bestIdx: number | null = null;
  cells.forEach((c, k) => {
    if (c.best === null) return;
    const cur = bestIdx === null ? null : (cells[bestIdx]?.best ?? null);
    if (cur === null || c.best < cur) bestIdx = k;
  });
  return { target, med: median(avail.map((w) => w.relief as number)), cells, bestIdx };
}

export function FigBest({ data }: { data: SearchData }) {
  const regions = regionsOf(data);
  const rows = data.targets.map((t) => row(t, regions));
  const vals = rows.flatMap((r) => [...r.cells.map((c) => c.best), r.med]).filter((v): v is number => typeof v === 'number' && v > 0);
  const lo = vals.length > 0 ? Math.min(...vals) / 2 : 0.01;
  const hi = vals.length > 0 ? Math.max(...vals) * 1.25 : 10;
  // bar length grows with closeness: full at the smallest distance, empty at the largest
  const closeness = (v: number) => Math.max(2, ((Math.log10(hi) - Math.log10(v)) / (Math.log10(hi) - Math.log10(lo))) * 100);
  // searched windows per region, as listed in the bundle (the same Earth windows for every target)
  const searchedPerRegion = regions.map((_, k) => Math.max(0, ...rows.map((r) => r.cells[k]?.searched ?? 0)));
  const cols = `minmax(260px, 0.9fr) ${regions.map(() => '1fr').join(' ')}`;

  return (
    <figure style={{ margin: 0, color: INK }}>
      {data.mock && (
        <div role="status" style={{ marginBottom: 16, padding: '8px 12px', border: `1px solid ${BEST}`, borderRadius: 8, color: BEST, fontSize: 14 }}>
          Mock preview: values come from a mock bundle and are not results.
        </div>
      )}
      <div role="table" aria-label="Best relief distance per target in each searched Earth region" className="overflow-hidden rounded-xl bg-white" style={{ border: `1px solid ${HAIR}` }}>
        <div role="row" className="grid" style={{ gridTemplateColumns: cols }}>
          <div role="columnheader" style={{ padding: '22px 32px 18px', fontSize: 14, color: MUTED, alignSelf: 'end' }}>
            Target
          </div>
          {regions.map((r, k) => (
            <div key={r.id} role="columnheader" style={{ padding: '22px 32px 18px', borderLeft: `1px solid ${HAIR}` }}>
              <div className="flex items-center gap-2.5 whitespace-nowrap" style={{ fontSize: 19, fontWeight: 500 }}>
                <span aria-hidden="true" className="inline-block size-2.5 shrink-0 rounded-full" style={{ background: REGION_COLOURS[r.id] ?? MUTED }} />
                {r.name}
              </div>
              <div style={{ marginTop: 4, fontSize: 14, color: MUTED }}>{searchedPerRegion[k]} windows searched</div>
            </div>
          ))}
        </div>
        {rows.map((r) => (
          <div key={r.target.targetId} role="row" className="grid" style={{ gridTemplateColumns: cols, borderTop: `1px solid ${HAIR}` }}>
            <div role="rowheader" className="flex flex-col justify-center" style={{ padding: '32px 32px' }}>
              <div className="flex items-center gap-3 whitespace-nowrap">
                <span aria-hidden="true" className="inline-block size-2.5 shrink-0 rounded-full" style={{ background: BODY_DOT[r.target.body] ?? MUTED }} />
                <span style={{ fontSize: 22, fontWeight: 500 }}>{r.target.targetName}</span>
              </div>
              <div style={{ marginTop: 4, marginLeft: 22, fontSize: 15, color: MUTED }}>{BODY_NAME[r.target.body] ?? r.target.body}</div>
            </div>
            {r.cells.map((c, k) => {
              const isBest = r.bestIdx === k;
              const ratio = c.best !== null && r.med !== null ? Math.max(1, Math.round(r.med / c.best)) : null;
              return (
                <div key={regions[k]?.id ?? k} role="cell" className="flex flex-col justify-center" style={{ padding: '32px 32px', borderLeft: `1px solid ${HAIR}` }}>
                  {c.best !== null ? (
                    <>
                      <div className="flex items-baseline gap-3">
                        <span style={{ fontFamily: MONO, fontSize: 28, lineHeight: 1.1, color: isBest ? BEST : INK, fontVariantNumeric: 'tabular-nums' }}>{fmt(c.best)}</span>
                        {isBest && (
                          <span className="rounded-full" style={{ padding: '2px 9px', fontSize: 12, fontWeight: 500, color: BEST, border: `1px solid ${BEST}`, lineHeight: 1.4 }}>
                            best
                          </span>
                        )}
                      </div>
                      <div style={{ marginTop: 6, fontSize: 14, color: SECONDARY }}>
                        {ratio !== null ? `${ratio}x closer than the median window` : 'Median unavailable'}
                      </div>
                      <div aria-hidden="true" className="relative rounded-full" style={{ marginTop: 12, height: 4, background: TRACK }}>
                        <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${closeness(c.best)}%`, background: isBest ? BEST : SECONDARY, opacity: isBest ? 1 : 0.35 }} />
                      </div>
                    </>
                  ) : (
                    <div style={{ fontSize: 16, color: MUTED }}>Unavailable (no relief distance in bundle)</div>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <figcaption className="flex justify-between gap-6" style={{ marginTop: 14, fontSize: 14, lineHeight: 1.5, color: SECONDARY }}>
        <span>
          <span style={{ color: INK, fontWeight: 500 }}>Figure 7.</span> Best Earth window per target in each searched region. Relief distance, lower is more alike.
        </span>
        <span style={{ fontFamily: MONO, fontSize: 12, color: MUTED, whiteSpace: 'nowrap' }}>bundle {data.bundleVersion}</span>
      </figcaption>
    </figure>
  );
}
