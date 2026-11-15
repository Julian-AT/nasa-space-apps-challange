// Adapted in part from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The evidence card (PRD 10, D08): the seven answers, table D, the four distance families side by
// side, figures A to C and the claim limits, drawn from one CardRecord. A server component with no
// client JS. Every number is String() of the parsed JSON number; nothing is rounded or computed.
// The page repeats shipping mode, mode title, evidence labels and bundle version in its body so a
// screenshot carries them. The families are never summed or ranked together.
import type { CardRecord } from '@terraluna/schema';
import type { ReactNode } from 'react';

import { EvidenceLabel, isEvidenceLabel } from '@/components/evidence-label';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

import { CardPair } from '../cards/card-pair.tsx';
import type { CardVisual } from '../cards/card-visual.ts';
import { MODE_TITLES } from '../mode-banner.tsx';
import { FAMILY_ORDER } from './og-text.ts';

type GateStatus = CardRecord['g5_status'];
type Family = (typeof FAMILY_ORDER)[number];

// Display rounding to 6 significant digits; the bundle value is unchanged.
const show = (v: number | string | boolean | null): string => (typeof v === 'number' && !Number.isInteger(v) ? String(Number(v.toPrecision(6))) : String(v));

/** One entry per observable (the text before the first colon): "x: reason" and "x: unavailable (reason)" are one fact. */
function dedupeByObservable(items: readonly string[]): string[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = item.split(':')[0]?.trim() ?? item;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export interface EvidenceCardViewProps {
  readonly card: CardRecord;
  /** manifest.bundle_version: the card record carries none. */
  readonly bundleVersion: string;
  /** validation.json's G5 status. */
  readonly g5Gate: GateStatus | null;
  /** bundle.records.figures: 'figures/<name>.svg' to allowlisted SVG text. */
  readonly figures: ReadonlyMap<string, string>;
  /** Hillshade pair and display names, from the bundle's tile layers. Optional: the card renders without images. */
  readonly visual?: CardVisual;
}

const FAMILY_NAMES: Readonly<Record<Family, string>> = {
  terrain_relief: 'Terrain relief',
  terrain_texture: 'Terrain texture',
  radar_cpr: 'Radar CPR',
  model_embedding: 'Model embedding',
};

const QUESTIONS = [
  'Compared windows',
  'What it could help investigate',
  'Supporting measurements',
  'What differs',
  'Stability under uncertainty',
  'Campaign questions',
  'Field experiment',
] as const;

function Q({ n, children }: { readonly n: number; readonly children: ReactNode }) {
  return (
    <section
      data-question={`q${String(n)}`}
      aria-labelledby={`q${String(n)}-title`}
      className="flex flex-col gap-4 rounded-xl border bg-card p-5 text-card-foreground shadow-xs sm:p-6"
    >
      <div className="flex items-center gap-4">
        <span
          aria-hidden="true"
          className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/15 font-mono text-sm font-semibold text-primary"
        >
          {String(n).padStart(2, '0')}
        </span>
        <div className="min-w-0">
          <p className="text-xs font-medium text-muted-foreground">Question {n}</p>
          <h2 id={`q${String(n)}-title`} className="text-lg leading-tight font-semibold">
            {QUESTIONS[n - 1]}
          </h2>
        </div>
      </div>
      <div className="flex min-w-0 flex-col gap-3">{children}</div>
    </section>
  );
}

function Label_({ label }: { readonly label: unknown }) {
  if (!isEvidenceLabel(label)) {
    throw new Error(`evidence card: a row has no evidence label (got ${JSON.stringify(label)})`);
  }
  return <EvidenceLabel label={label} />;
}

function Reason({ reason }: { readonly reason: string | null }) {
  return reason === null ? null : <code className="rounded bg-muted px-1.5 py-0.5 text-xs">{reason}</code>;
}

function List({ items, empty }: { readonly items: readonly string[]; readonly empty: string }) {
  if (items.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm">
      {items.map((item) => (
        <li key={item} className="wrap-anywhere">{item}</li>
      ))}
    </ul>
  );
}

function Polygon({
  title,
  polygon,
}: {
  readonly title: string;
  readonly polygon: CardRecord['target']['polygon'];
}) {
  const short = [
    polygon.frame,
    polygon.epsg === null ? null : `EPSG:${String(polygon.epsg)}`,
    polygon.utm_zone === null ? null : `zone ${polygon.utm_zone}`,
  ]
    .filter((x) => x !== null)
    .join(' · ');
  return (
    <div className="min-w-0 rounded-lg border bg-muted/30 p-3 text-sm">
      <p className="font-semibold wrap-anywhere">{title}</p>
      <p className="text-muted-foreground">
        {short} · {polygon.ring_lonlat.length} vertices
      </p>
      <details className="mt-1 text-xs text-muted-foreground">
        <summary className="cursor-pointer">Full CRS</summary>
        <p className="mt-1 break-all">{polygon.crs}</p>
      </details>
    </div>
  );
}

function Distances({ card, g5Passed }: { readonly card: CardRecord; readonly g5Passed: boolean }) {
  return (
    <ul data-slot="distances" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {FAMILY_ORDER.map((family) => {
        const d = card.distances_by_family[family];
        const encoder = family === 'model_embedding';
        const experimental = encoder && !g5Passed;
        const has = d.status === 'available' && d.value !== null;
        return (
          <li
            key={family}
            data-family={family}
            data-status={d.status}
            className="flex flex-col gap-3 rounded-xl border bg-card p-4 shadow-xs"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-muted-foreground">
                {FAMILY_NAMES[family]}
              </span>
              {experimental ? <Badge variant="outline">EXPERIMENTAL</Badge> : null}
            </div>
            {has ? (
              <p className="font-mono text-3xl leading-none font-semibold tabular-nums">
                <span data-slot="value">{show(d.value)}</span>{' '}
                <span data-slot="unit" className="text-sm text-muted-foreground">
                  {d.unit}
                </span>
              </p>
            ) : (
              <p className="text-lg leading-snug">
                {family === 'radar_cpr' && card.shipping_mode === 'C'
                  ? 'Unavailable: not completed (Mode C)'
                  : 'Unavailable'}
              </p>
            )}
            <div className="mt-auto flex flex-col gap-1.5 text-sm">
              <Label_ label={d.evidence_label} />
              <span className="text-muted-foreground">
                {d.status === 'not_comparable' ? 'not comparable' : d.status} <Reason reason={d.reason} />
              </span>
              <span data-rank="none" className="text-xs text-muted-foreground">
                {experimental ? 'unranked (G5 not passed)' : 'unranked: no single score'}
              </span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function Stability({ card, g5Passed }: { readonly card: CardRecord; readonly g5Passed: boolean }) {
  const { uncertainty_components: parts } = card.q5_stability;
  const names = {
    heterogeneity: 'Heterogeneity',
    measurement_error: 'Measurement error',
    statistical_prediction_error: 'Statistical prediction error',
    scenario_sensitivity: 'Scenario sensitivity',
  } as const;
  return (
    <ul className="space-y-3 text-sm">
      {(Object.keys(names) as (keyof typeof names)[]).map((key) => {
        const c = parts[key];
        const families = c.by_family ?? {};
        const freq = c.rank_frequency ?? {};
        return (
          <li key={key} data-component={key} className="rounded-lg border border-border p-3">
            <p className="flex flex-wrap items-center gap-2">
              <span className="font-semibold">{names[key]}</span>
              <Label_ label={c.evidence_label} />
              <span className="text-muted-foreground">{c.status}</span>
              <Reason reason={c.reason} />
            </p>
            {c.note === null ? null : <p className="mt-1">{c.note}</p>}
            {c.n_requested === null && c.n_used === null ? null : (
              <p className="mt-1 text-muted-foreground">
                n requested {String(c.n_requested)}, n used {String(c.n_used)}
              </p>
            )}
            <ul className="mt-1 space-y-0.5">
              {FAMILY_ORDER.map((f) => {
                const hide = f === 'model_embedding' && !g5Passed;
                const s = families[f];
                const r = freq[f];
                if (hide && (s !== undefined || r !== undefined)) {
                  return (
                    <li key={f} data-family={f}>
                      {f}: EXPERIMENTAL, not shown while G5 has not passed
                    </li>
                  );
                }
                if (s === undefined && r === undefined) return null;
                return (
                  <li key={f} data-family={f}>
                    {f}
                    {s === undefined
                      ? ''
                      : `: spread min ${show(s.spread_min)}, median ${show(s.spread_median)}, max ${show(s.spread_max)}, n ${String(s.n)}`}
                    {r === undefined
                      ? ''
                      : `; rank held ${String(r.count)} of ${String(r.n)} runs (frequency ${show(r.frequency)})`}
                  </li>
                );
              })}
            </ul>
          </li>
        );
      })}
    </ul>
  );
}

function dataUri(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function Figures({ card, figures }: Pick<EvidenceCardViewProps, 'card' | 'figures'>) {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      {card.figures.map((f) => {
        const svg = f.path === null ? undefined : figures.get(f.path);
        return (
          <figure key={f.figure} data-figure={f.figure} className="m-0 flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-4 shadow-xs">
            <figcaption className="text-sm font-medium text-primary">Figure {f.figure}</figcaption>
            {svg !== undefined && f.path !== null ? (
              <>
                <img src={dataUri(svg)} alt={f.alt} className="w-full rounded-lg bg-white" />
                <p className="text-xs text-muted-foreground">{f.alt}</p>
              </>
            ) : (
              <div role="img" aria-label={f.alt} className="rounded border border-dashed border-border p-4 text-sm">
                <p>
                  <EvidenceLabel label="Unavailable" />
                </p>
                <p className="mt-2">
                  {f.path === null ? (
                    <>
                      Not rendered: <Reason reason={f.reason} />
                    </>
                  ) : (
                    <>
                      Missing from the bundle: <code>{f.path}</code>
                    </>
                  )}
                </p>
                <p className="mt-1 text-muted-foreground">{f.alt}</p>
              </div>
            )}
          </figure>
        );
      })}
    </div>
  );
}

function SectionTitle({ id, eyebrow, children }: { readonly id: string; readonly eyebrow: string; readonly children: ReactNode }) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{eyebrow}</p>
      <h2 id={id} className="text-2xl font-semibold tracking-tight">{children}</h2>
    </div>
  );
}

export function EvidenceCardView({ card, bundleVersion, g5Gate, figures, visual }: EvidenceCardViewProps) {
  // The encoder is out of rankings until G5 passes, both on the card's build and in validation.json.
  const g5Passed = g5Gate === 'PASS' && card.g5_status === 'PASS';
  const marsLimits = card.target.body === 'mars';
  const t = card.target;
  return (
    <article data-slot="evidence-card" className="flex min-w-0 flex-col gap-8 wrap-anywhere">
      {card.mock ? (
        <p role="status" className="rounded-lg bg-mock px-4 py-3 font-bold text-mock-foreground">
          MOCK PREVIEW: synthetic data, not production evidence
        </p>
      ) : null}
      <header className="grid gap-8 rounded-xl border bg-card p-6 text-card-foreground shadow-xs sm:p-8 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-5">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-xs font-medium text-muted-foreground">
              Evidence card
            </span>
            <Badge data-slot="mode-pill" className="h-auto max-w-full py-1 whitespace-normal">
              {t.body === 'mars' && card.shipping_mode === 'C'
                ? 'Mode C: terrain only; radar comparison not completed'
                : `Mode ${card.shipping_mode}: ${MODE_TITLES[card.shipping_mode]}`}
            </Badge>
          </div>
          {visual === undefined ? null : (
            <p className="text-base font-medium text-primary">{visual.headline}</p>
          )}
          <h1 className="text-3xl leading-tight font-semibold tracking-tight text-balance sm:text-4xl">{card.title}</h1>
          <div className="flex flex-wrap gap-2 text-sm">
            <span className="rounded-md border bg-muted/40 px-2.5 py-1">
              <span className="text-muted-foreground">Target</span> {t.target_id} ({t.body})
            </span>
            <span className="rounded-md border bg-muted/40 px-2.5 py-1">
              <span className="text-muted-foreground">Earth window</span> {card.earth.earth_window_id}
            </span>
          </div>
          <p className="text-sm text-muted-foreground">{card.wording}</p>
          <p className="mt-auto font-mono text-[11px] tracking-wider text-muted-foreground uppercase">
            Bundle version {bundleVersion} · {g5Gate === null ? 'G5 not recorded' : `G5 ${g5Gate}`}
          </p>
        </div>
        {visual === undefined ? null : (
          <div className="min-w-0">
            <CardPair visual={visual} big />
            <p className="mt-3 text-xs text-muted-foreground">
              <EvidenceLabel label="Derived" /> Bundle hillshade tiles (45° altitude, 315° azimuth); the outline marks each
              5 km analysis window.
            </p>
          </div>
        )}
      </header>

      <section aria-labelledby="distances" className="flex flex-col gap-4">
        <SectionTitle id="distances" eyebrow="Four families">Distances by family</SectionTitle>
        <Distances card={card} g5Passed={g5Passed} />
        <p className="text-sm text-muted-foreground">
          Four families, each in its own units: never summed, averaged or ranked together.
        </p>
        {card.shipping_mode === 'C' ? (
          <p className="text-sm text-muted-foreground">
            Mode C: the radar comparison was not completed. The radar_cpr row above carries its Unavailable
            reason.
          </p>
        ) : null}
      </section>

      <section
        aria-labelledby="claim-limits"
        data-slot="limits"
        className="flex flex-col gap-3 rounded-xl border border-primary/40 bg-primary/5 p-5 sm:p-6"
      >
        <SectionTitle id="claim-limits" eyebrow="What this card does not claim">Claim limits</SectionTitle>
        <List items={card.claim_limits} empty="The record lists no claim limits." />
      </section>

      <section aria-labelledby="figures" className="flex flex-col gap-4">
        <SectionTitle id="figures" eyebrow="Generated from the bundle">Figures A to C</SectionTitle>
        <Figures card={card} figures={figures} />
      </section>

      <div className="flex flex-col gap-5">
        <Q n={1}>
          <p>{card.q1_compared}</p>
          <div className="grid gap-2 sm:grid-cols-2">
            <Polygon title={`Target ${t.target_window_id}`} polygon={t.polygon} />
            <Polygon title={`Earth ${card.earth.earth_window_id}`} polygon={card.earth.polygon} />
          </div>
        </Q>
        <Q n={2}>
          <p>{card.q2_interpretation.text}</p>
          <EvidenceLabel label={card.q2_interpretation.evidence_label} />
        </Q>
        <Q n={3}>
          <div className="overflow-x-auto rounded-lg border">
            <Table data-slot="table-d">
              <TableHeader>
                <TableRow>
                  <TableHead>Observable</TableHead>
                  <TableHead>Value</TableHead>
                  <TableHead>Evidence</TableHead>
                  <TableHead>Support</TableHead>
                  <TableHead>Product, band, geometry</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {card.q3_measurements.map((m, i) => (
                  <TableRow key={`${m.observable}-${String(i)}`}>
                    <TableCell>{m.observable}</TableCell>
                    <TableCell>
                      {m.value === null ? (
                        <span>
                          Unavailable <Reason reason={m.reason} />
                        </span>
                      ) : (
                        <>
                          <span data-slot="value" className="font-mono">
                            {show(m.value)}
                          </span>
                          {m.unit === null ? null : <> {m.unit}</>}
                        </>
                      )}
                    </TableCell>
                    <TableCell>
                      <Label_ label={m.evidence_label} />
                    </TableCell>
                    <TableCell>{m.support}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {[m.product_id, m.band, m.geometry].filter((x) => x !== null).join(' · ') || 'not recorded'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {card.radar_context.length === 0 ? null : (
            <div data-slot="radar-context" className="flex flex-col gap-2">
              <h3 className="text-sm font-semibold">Observed radar context</h3>
              <ul className="space-y-1 text-sm">
                {card.radar_context.map((r) => (
                  <li key={`${r.product_id}-${r.band}-${r.observable}-${r.statistic}`}>
                    {r.observable} ({r.statistic}, {r.band}):{' '}
                    {r.value === null ? (
                      <>
                        Unavailable <Reason reason={r.reason} />
                      </>
                    ) : (
                      <>
                        {show(r.value)}
                        {r.unit === null ? '' : ` ${r.unit}`}
                      </>
                    )}{' '}
                    <Label_ label={r.evidence_label} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Q>
        <Q n={4}>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h3 className="text-sm font-semibold">{marsLimits ? 'Mars limits' : "Earth's limits"}</h3>
              <List items={card.q4_differences.limits} empty="No limits listed in the record." />
            </div>
            <div>
              <h3 className="text-sm font-semibold">Differ</h3>
              <List items={card.q4_differences.differing} empty="None listed." />
            </div>
            <div>
              <h3 className="text-sm font-semibold">Ambiguous</h3>
              <List items={card.q4_differences.ambiguous} empty="None listed." />
            </div>
            <div>
              <h3 className="text-sm font-semibold">Not observed</h3>
              <List items={dedupeByObservable(card.q4_differences.not_observed)} empty="None listed." />
            </div>
          </div>
        </Q>
        <Q n={5}>
          <Stability card={card} g5Passed={g5Passed} />
        </Q>
        <Q n={6}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            {(['access', 'permits', 'season', 'land_status', 'logistics'] as const).map((k) => {
              const a = card.q6_campaign[k];
              return (
                <div key={k} className="contents" data-campaign={k}>
                  <dt className="font-semibold capitalize">{k.replace('_', ' ')}</dt>
                  <dd>
                    {a.value}
                    {a.source === null ? null : <span className="text-muted-foreground"> (source: {a.source})</span>}
                  </dd>
                </div>
              );
            })}
          </dl>
        </Q>
        <Q n={7}>
          <div data-question-body="q7" className="flex flex-col gap-2">
            <p className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">Proposed</Badge>
              <EvidenceLabel label={card.q7_field_experiment.evidence_label} />
              <span className="text-sm text-muted-foreground">A proposal, not a completed measurement.</span>
            </p>
            <p>{card.q7_field_experiment.text}</p>
          </div>
        </Q>
      </div>

      <footer className="border-t border-border pt-4 font-mono text-xs break-all text-muted-foreground">
        Mode {card.shipping_mode} · bundle {bundleVersion} · run {card.run_id}
      </footer>
    </article>
  );
}
