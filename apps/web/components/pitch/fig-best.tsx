// "Results": one row per target with the best Earth window, its relief distance and how many
// times closer it is than the median window of the same search. Every value is computed from the
// bundle's comparison records via buildSearchData; a target without relief values shows
// Unavailable, never zero. The bar under each row compares best and median on a shared log scale.
import type { SearchData, SearchTarget } from './fig-search';

const INK = '#1A1613';
const SECONDARY = '#4D4843';
const MUTED = '#A39E95';
const HAIR = 'rgba(26,22,19,0.10)';
const BEST = '#CC5800';
const TRACK = '#E8E4DC';
const BODY_DOT: Record<string, string> = { mars: '#E08A5F', moon: '#A39E95' };
const BODY_NAME: Record<string, string> = { mars: 'Mars', moon: 'Moon' };
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

interface Row {
  readonly target: SearchTarget;
  readonly best: { readonly region: string; readonly relief: number } | null;
  readonly med: number | null;
  readonly searched: number;
}

function row(target: SearchTarget): Row {
  const avail = target.windows.filter((w) => w.relief !== null && w.relief > 0);
  const sorted = [...avail].sort((a, b) => (a.relief as number) - (b.relief as number) || a.windowId.localeCompare(b.windowId));
  const first = sorted[0];
  return {
    target,
    best: first ? { region: first.regionName, relief: first.relief as number } : null,
    med: median(avail.map((w) => w.relief as number)),
    searched: target.windows.length,
  };
}

export function FigBest({ data }: { data: SearchData }) {
  const rows = data.targets.map(row);
  const vals = rows.flatMap((r) => [r.best?.relief, r.med]).filter((v): v is number => typeof v === 'number' && v > 0);
  const lo = vals.length > 0 ? Math.min(...vals) / 2 : 0.01;
  const hi = vals.length > 0 ? Math.max(...vals) * 1.25 : 10;
  const pct = (v: number) => ((Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * 100;

  return (
    <figure style={{ margin: 0, color: INK }}>
      {data.mock && (
        <div role="status" style={{ marginBottom: 16, padding: '8px 12px', border: `1px solid ${BEST}`, borderRadius: 8, color: BEST, fontSize: 14 }}>
          Mock preview: values come from a mock bundle and are not results.
        </div>
      )}
      <div className="rounded-xl bg-white" style={{ border: `1px solid ${HAIR}`, padding: '4px 36px' }}>
        {rows.map((r, i) => {
          const ratio = r.best !== null && r.med !== null ? Math.round(r.med / r.best.relief) : null;
          return (
            <div key={r.target.targetId} style={{ padding: '34px 0 30px', borderTop: i === 0 ? 'none' : `1px solid ${HAIR}` }}>
              <div className="grid items-baseline" style={{ gridTemplateColumns: '1fr 1.15fr 1.25fr', gap: 24 }}>
                <div className="flex items-baseline gap-3 whitespace-nowrap">
                  <span aria-hidden="true" className="inline-block size-2.5 shrink-0 self-center rounded-full" style={{ background: BODY_DOT[r.target.body] ?? MUTED }} />
                  <span style={{ fontSize: 22, fontWeight: 500 }}>{r.target.targetName}</span>
                  <span style={{ fontSize: 15, color: MUTED }}>{BODY_NAME[r.target.body] ?? r.target.body}</span>
                </div>
                <div className="flex items-baseline gap-3 whitespace-nowrap">
                  {r.best !== null ? (
                    <>
                      <span style={{ fontSize: 22, fontWeight: 400 }}>{r.best.region}</span>
                      <span style={{ fontFamily: MONO, fontSize: 15, color: SECONDARY }}>{fmt(r.best.relief)}</span>
                    </>
                  ) : (
                    <span style={{ fontSize: 18, color: MUTED }}>Unavailable (no relief distance in bundle)</span>
                  )}
                </div>
                <div className="flex items-baseline justify-end gap-2.5 whitespace-nowrap">
                  {ratio !== null ? (
                    <>
                      <span style={{ fontSize: 40, fontWeight: 500, lineHeight: 1, color: BEST, letterSpacing: '-0.02em', fontVariantNumeric: 'tabular-nums' }}>{ratio}x</span>
                      <span style={{ fontSize: 18, color: SECONDARY }}>closer than the median window</span>
                    </>
                  ) : (
                    <span style={{ fontSize: 18, color: MUTED }}>Unavailable</span>
                  )}
                </div>
              </div>
              {r.best !== null && r.med !== null && (
                <div
                  role="img"
                  aria-label={`${r.target.targetName}: best ${fmt(r.best.relief)}, median ${fmt(r.med)} of ${r.searched} windows`}
                  className="relative"
                  style={{ height: 32, marginTop: 20 }}
                >
                  <div className="absolute left-0 rounded-full" style={{ top: 0, height: 6, width: `${pct(r.med)}%`, background: TRACK }} />
                  <div className="absolute left-0 rounded-full" style={{ top: 0, height: 6, width: `${Math.max(pct(r.best.relief), 0.8)}%`, background: BEST }} />
                  <div className="absolute" style={{ top: -3, height: 12, width: 1, left: `${pct(r.med)}%`, background: SECONDARY }} />
                  <div
                    className="absolute whitespace-nowrap"
                    style={{ top: 12, left: `${Math.max(pct(r.best.relief), 0.8)}%`, transform: 'translateX(-50%)', fontSize: 13, color: BEST, fontWeight: 500 }}
                  >
                    best
                  </div>
                  <div
                    className="absolute whitespace-nowrap"
                    style={{ top: 12, left: `${pct(r.med)}%`, transform: 'translateX(-50%)', fontSize: 13, color: MUTED }}
                  >
                    median <span style={{ fontFamily: MONO }}>{fmt(r.med)}</span> of {r.searched}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <figcaption className="flex justify-between gap-6" style={{ marginTop: 14, fontSize: 14, lineHeight: 1.5, color: SECONDARY }}>
        <span>
          <span style={{ color: INK, fontWeight: 500 }}>Figure 7.</span> Best Earth window per target, relief distance, lower is more alike.
          {data.reliefUnit !== null && <> Distance in {data.reliefUnit}, log scale.</>}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 12, color: MUTED, whiteSpace: 'nowrap' }}>bundle {data.bundleVersion}</span>
      </figcaption>
    </figure>
  );
}
