// Results figure for the pitch deck: a leaderboard of the release bundle's evidence cards, one row
// per card, grouped by target and sorted by terrain relief distance (lower is more alike). Every
// value is read by buildResultsData from the bundle's card records; nothing is typed in, and a
// distance the card does not carry renders as Unavailable, never as zero.
import type { PulledBundle } from '@/lib/bundle';
import { earthRegionName } from '@/components/cards/card-geo';

export interface ResultsRow {
  readonly cardId: string;
  readonly targetId: string;
  readonly targetName: string;
  readonly body: string;
  /** Earth region display name and its country or state, split for typography. */
  readonly earthPlace: string;
  readonly earthCountry: string | null;
  readonly earthWindowId: string;
  readonly relief: number | null;
  readonly reliefReason: string | null;
  /** floor(100 - background_percentile) of the relief distance, or null when the card has none. */
  readonly closerThan: number | null;
  readonly texture: number | null;
  readonly textureReason: string | null;
  readonly evidenceLabel: string | null;
  /** Lowest relief distance among this target's cards. */
  readonly best: boolean;
}

export interface ResultsGroup {
  readonly targetId: string;
  readonly targetName: string;
  readonly body: string;
  readonly rows: readonly ResultsRow[];
}

export interface ResultsData {
  readonly bundleVersion: string;
  readonly mock: boolean;
  readonly shippingModes: readonly string[];
  readonly unit: string | null;
  readonly cardCount: number;
  readonly groups: readonly ResultsGroup[];
}

// Display names, as in pitch-data.ts and fig-benchmarks.tsx.
const TARGET_NAMES: Record<string, string> = {
  'mars-jezero-w000': 'Jezero crater',
  'mars-arcadia-w000': 'Arcadia Planitia',
  'moon-cr-site01-w000': 'Connecting Ridge',
};
const TARGET_ORDER = ['mars-jezero-w000', 'mars-arcadia-w000', 'moon-cr-site01-w000'];
const BODY_ORDER: Record<string, number> = { mars: 0, moon: 1 };

function available(f: { status?: string; value?: number | null } | null | undefined): number | null {
  if (f === null || f === undefined || f.status !== 'available') return null;
  return typeof f.value === 'number' && Number.isFinite(f.value) ? f.value : null;
}

export function buildResultsData(bundle: PulledBundle): ResultsData {
  const cards = [...bundle.records.cards.values()];
  const byTarget = new Map<string, ResultsRow[]>();
  let unit: string | null = null;
  const modes = new Set<string>();
  for (const card of cards) {
    const rel = card.distances_by_family.terrain_relief;
    const tex = card.distances_by_family.terrain_texture;
    const relief = available(rel);
    const pct = rel?.background_percentile;
    unit ??= rel?.unit ?? null;
    modes.add(String(card.shipping_mode));
    const regionId = card.earth.search_region_id ?? card.earth.earth_window_id;
    const [place, country] = earthRegionName(regionId).split(', ');
    const id = card.target.target_id;
    const row: ResultsRow = {
      cardId: card.card_id,
      targetId: id,
      targetName: TARGET_NAMES[id] ?? id,
      body: card.target.body,
      earthPlace: place ?? regionId,
      earthCountry: country ?? null,
      earthWindowId: card.earth.earth_window_id,
      relief,
      reliefReason: relief === null ? (rel?.reason ?? 'not in card') : null,
      closerThan: relief !== null && typeof pct === 'number' && Number.isFinite(pct) ? Math.floor(100 - pct) : null,
      texture: available(tex),
      textureReason: available(tex) === null ? (tex?.reason ?? 'not in card') : null,
      evidenceLabel: rel?.evidence_label ?? null,
      best: false,
    };
    byTarget.set(id, [...(byTarget.get(id) ?? []), row]);
  }
  const rank = (id: string): number => {
    const i = TARGET_ORDER.indexOf(id);
    return i === -1 ? TARGET_ORDER.length : i;
  };
  const groups: ResultsGroup[] = [...byTarget.entries()]
    .map(([targetId, rows]) => {
      const sorted = [...rows].sort((a, b) => (a.relief ?? Infinity) - (b.relief ?? Infinity));
      const bestValue = sorted[0]?.relief ?? null;
      const marked = sorted.map((r) => ({ ...r, best: bestValue !== null && r.relief === bestValue }));
      const first = marked[0];
      return { targetId, targetName: first?.targetName ?? targetId, body: first?.body ?? '', rows: marked };
    })
    .sort((a, b) => (BODY_ORDER[a.body] ?? 9) - (BODY_ORDER[b.body] ?? 9) || rank(a.targetId) - rank(b.targetId));
  return {
    bundleVersion: bundle.manifest.bundle_version,
    mock: bundle.manifest.mock || cards.some((c) => c.mock),
    shippingModes: [...modes].sort(),
    unit,
    cardCount: cards.length,
    groups,
  };
}

// Medlyn palette.
const INK = '#1A1613';
const SECOND = '#4D4843';
const MUTED = '#A39E95';
const HAIR = 'rgba(26,22,19,0.08)';
const ORANGE = '#CC5800';
const PASTEL = '#FFE8CC';
const GREY_FILL = '#EDEAE4';
const GREY_LINE = 'rgba(26,22,19,0.14)';
const DOT: Record<string, string> = { mars: '#E08A5F', moon: '#A39E95' };
const SANS = 'var(--pitch-sans, var(--font-dm-sans)), ui-sans-serif, system-ui, sans-serif';
const MONO = 'var(--pitch-mono, var(--font-dm-mono)), ui-monospace, monospace';

const fmt = (v: number): string => (v >= 1 ? v.toFixed(2) : v.toFixed(3));

/** Log-scale domain covering every relief value, padded to round decades and half-decades. */
function logDomain(values: readonly number[]): { lo: number; hi: number; ticks: number[] } {
  const pos = values.filter((v) => v > 0);
  if (pos.length === 0) return { lo: 0.01, hi: 10, ticks: [0.01, 0.1, 1, 10] };
  const lo = Math.min(...pos) / 1.8;
  const hi = Math.max(...pos) * 1.25;
  const ticks: number[] = [];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e += 1) {
    for (const m of [1, 3]) {
      const t = m * 10 ** e;
      if (t >= lo && t <= hi) ticks.push(t);
    }
  }
  return { lo, hi, ticks };
}

const COLS = '210px 330px minmax(240px, 1fr) 160px 88px';

export function FigResults({ data }: { data: ResultsData }) {
  const reliefs = data.groups.flatMap((g) => g.rows.map((r) => r.relief)).filter((v): v is number => v !== null);
  const { lo, hi, ticks } = logDomain(reliefs);
  const frac = (v: number): number => Math.max(0.015, Math.min(1, (Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))));
  const tickLabel = (t: number): string => (t >= 1 ? String(t) : t.toPrecision(1));

  return (
    <figure style={{ margin: 0, fontFamily: SANS, color: INK }}>
      <div
        className="rounded-xl"
        style={{ background: '#FFFFFF', border: '1px solid rgba(26,22,19,0.10)', padding: 32 }}
      >
        {data.mock ? (
          <div style={{ marginBottom: 20, padding: '8px 12px', borderRadius: 8, background: '#FFF4D6', color: '#7A4B00', fontSize: 13, fontWeight: 500 }}>
            Mock preview: values come from the mock bundle and are not results.
          </div>
        ) : null}
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 24 }}>
          <div>
            <h3 style={{ margin: 0, fontWeight: 500, fontSize: 22, lineHeight: 1.25, letterSpacing: '-0.01em' }}>Best Earth matches</h3>
            <p style={{ margin: '6px 0 0', fontSize: 14, color: SECOND }}>
              {data.cardCount} evidence cards, sorted by relief distance, lower is more alike
            </p>
          </div>
          <div style={{ display: 'flex', gap: 16, fontSize: 12, color: MUTED, whiteSpace: 'nowrap' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 14, height: 8, borderRadius: 2, background: PASTEL, border: `1px solid ${ORANGE}` }} />
              Best per target
            </span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 14, height: 8, borderRadius: 2, background: GREY_FILL, border: `1px solid ${GREY_LINE}` }} />
              Other cards
            </span>
          </div>
        </div>

        <div role="table" aria-label="Evidence cards sorted by terrain relief distance" style={{ marginTop: 28 }}>
          <div
            role="row"
            style={{ display: 'grid', gridTemplateColumns: COLS, columnGap: 24, paddingBottom: 10, borderBottom: `1px solid rgba(26,22,19,0.14)`, fontSize: 12, color: MUTED, letterSpacing: '0.01em' }}
          >
            <span role="columnheader">Target</span>
            <span role="columnheader">Earth window</span>
            <span role="columnheader" style={{ position: 'relative' }}>
              Relief distance <span style={{ color: MUTED, opacity: 0.8 }}>(log scale)</span>
            </span>
            <span role="columnheader">Closer than</span>
            <span role="columnheader" style={{ textAlign: 'right' }}>Texture</span>
          </div>

          {data.groups.map((g, gi) => (
            <div key={g.targetId} role="rowgroup" style={{ borderTop: gi === 0 ? 'none' : '1px solid rgba(26,22,19,0.12)' }}>
              {g.rows.map((r, ri) => (
                <div
                  key={r.cardId}
                  role="row"
                  style={{
                    display: 'grid',
                    gridTemplateColumns: COLS,
                    columnGap: 24,
                    alignItems: 'center',
                    minHeight: 58,
                    borderTop: ri === 0 ? 'none' : `1px solid ${HAIR}`,
                  }}
                >
                  <span role="cell" style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 15, fontWeight: 500 }}>
                    {ri === 0 ? (
                      <>
                        <span aria-hidden style={{ width: 9, height: 9, borderRadius: 999, background: DOT[g.body] ?? MUTED, flex: 'none' }} />
                        <span>{g.targetName}</span>
                        <span style={{ fontSize: 12, fontWeight: 400, color: MUTED, textTransform: 'capitalize' }}>{g.body}</span>
                      </>
                    ) : (
                      <span className="sr-only">{g.targetName}</span>
                    )}
                  </span>

                  <span role="cell" style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <span style={{ fontSize: 15, fontWeight: r.best ? 500 : 400, color: INK, whiteSpace: 'nowrap' }}>
                      <span style={{ fontSize: 13, fontWeight: 400, color: MUTED }}>matched with </span>
                      {r.earthPlace}
                      {r.earthCountry !== null ? <span style={{ fontSize: 13, fontWeight: 400, color: SECOND }}>, {r.earthCountry}</span> : null}
                    </span>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: MUTED }}>{r.earthWindowId.replace(/^earth-/u, '')}</span>
                  </span>

                  <span role="cell" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    {r.relief === null ? (
                      <span style={{ fontSize: 13, color: MUTED }}>Unavailable ({r.reliefReason})</span>
                    ) : (
                      <>
                        <span style={{ position: 'relative', flex: 1, height: 14 }}>
                          {ticks.map((t) => (
                            <span key={t} aria-hidden style={{ position: 'absolute', left: `${frac(t) * 100}%`, top: -6, bottom: -6, width: 1, background: 'rgba(26,22,19,0.05)' }} />
                          ))}
                          <span
                            aria-hidden
                            style={{
                              position: 'absolute',
                              left: 0,
                              top: 0,
                              bottom: 0,
                              width: `${frac(r.relief) * 100}%`,
                              borderRadius: 3,
                              background: r.best ? PASTEL : GREY_FILL,
                              border: `1px solid ${r.best ? ORANGE : GREY_LINE}`,
                            }}
                          />
                        </span>
                        <span style={{ fontFamily: MONO, fontSize: 14, width: 48, textAlign: 'right', color: r.best ? ORANGE : SECOND, fontWeight: r.best ? 500 : 400 }}>
                          {fmt(r.relief)}
                        </span>
                      </>
                    )}
                  </span>

                  <span role="cell" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    {r.closerThan === null ? (
                      <span style={{ fontSize: 13, color: MUTED }}>Unavailable</span>
                    ) : (
                      <>
                        <span aria-hidden style={{ position: 'relative', width: 72, height: 4, borderRadius: 999, background: 'rgba(26,22,19,0.07)' }}>
                          <span style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${r.closerThan}%`, borderRadius: 999, background: r.best ? ORANGE : '#B9B3AA' }} />
                        </span>
                        <span style={{ fontFamily: MONO, fontSize: 13, color: r.best ? INK : SECOND }}>{r.closerThan}%</span>
                        <span className="sr-only">of background windows</span>
                      </>
                    )}
                  </span>

                  <span role="cell" style={{ fontFamily: MONO, fontSize: 14, textAlign: 'right', color: SECOND }}>
                    {r.texture === null ? <span style={{ fontFamily: SANS, fontSize: 13, color: MUTED }}>Unavailable</span> : fmt(r.texture)}
                  </span>
                </div>
              ))}
            </div>
          ))}

          <div style={{ display: 'grid', gridTemplateColumns: COLS, columnGap: 24, borderTop: '1px solid rgba(26,22,19,0.14)', paddingTop: 6 }}>
            <span />
            <span />
            <span style={{ display: 'flex', gap: 12 }}>
              <span style={{ position: 'relative', flex: 1, height: 16 }}>
                {ticks.map((t) => (
                  <span key={t} style={{ position: 'absolute', left: `${frac(t) * 100}%`, transform: 'translateX(-50%)', fontFamily: MONO, fontSize: 11, color: MUTED }}>
                    {tickLabel(t)}
                  </span>
                ))}
              </span>
              <span style={{ width: 48 }} />
            </span>
            <span style={{ fontSize: 11, color: MUTED }}>of background windows</span>
            <span />
          </div>
        </div>

        <p style={{ margin: '20px 0 0', fontSize: 12, lineHeight: 1.5, color: MUTED }}>
          Terrain distances only, in {data.unit ?? 'card units'}; {data.groups[0]?.rows[0]?.evidenceLabel ?? 'Derived'} evidence. Shipping mode{' '}
          {data.shippingModes.join(', ')}: radar comparison not completed, encoder experimental and outside the ranking. Similarity is not a
          claim about soil strength, buried structure or mobility.
        </p>
      </div>
      <figcaption style={{ marginTop: 12, display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 13, color: SECOND }}>
        <span>
          <span style={{ fontWeight: 500, color: INK }}>Figure 7.</span> Evidence cards from the release bundle{' '}
          <span style={{ fontFamily: MONO, fontSize: 12, color: MUTED }}>{data.bundleVersion}</span>.
        </span>
        <span style={{ color: MUTED }}>
          Open all cards: <span style={{ fontFamily: MONO, fontSize: 12, color: SECOND }}>terraluna-lovat.vercel.app/site</span>
        </span>
      </figcaption>
    </figure>
  );
}
