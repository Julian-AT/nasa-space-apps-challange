// Adapted in part from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The evidence card (PRD 10, D08): the seven answers, table D, the four distance families side by
// side, figures A to C and the claim limits, drawn from one CardRecord. A server component with no
// client JS. Every number is String() of the parsed JSON number; nothing is rounded or computed.
// The page repeats shipping mode, mode title, evidence labels and bundle version in its body so a
// screenshot carries them. The families are never summed or ranked together.
import type { CardRecord } from '@terraluna/schema';
import type { ReactNode } from 'react';
import Link from 'next/link';

import { EvidenceLabel, isEvidenceLabel } from '@/components/evidence-label';
import { Badge } from '@/components/ui/badge';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

import { Metric, closerThan } from '../cards/card-metric.tsx';
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
    <div className="min-w-0 rounded-md bg-muted p-3 text-sm">
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
                      {f}: experimental
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
    <div className="grid gap-5 lg:grid-cols-2">
      {card.figures.map((f) => {
        const svg = f.path === null ? undefined : figures.get(f.path);
        return (
          <Card key={f.figure} data-figure={f.figure} className="gap-4 py-4">
            <CardHeader className="px-4">
              <CardTitle className="text-base">Figure {f.figure}</CardTitle>
              <CardDescription className="text-sm">{f.alt}</CardDescription>
            </CardHeader>
            <CardContent className="px-4">
              {svg !== undefined && f.path !== null ? (
                <img src={dataUri(svg)} alt={f.alt} className="w-full rounded-lg border bg-white" />
              ) : (
                <div role="img" aria-label={f.alt} className="flex flex-col gap-2 rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                  <EvidenceLabel label="Unavailable" />
                  {f.path === null ? (
                    <p>Not rendered: <Reason reason={f.reason} /></p>
                  ) : (
                    <p>Missing from the bundle: <code>{f.path}</code></p>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

const human = (code: string | null): string => (code === null ? 'no reason recorded' : code.replaceAll('_', ' '));

function Prose({ title, label, badge, children }: { readonly title: string; readonly label: unknown; readonly badge?: string; readonly children: ReactNode }) {
  return (
    <Card className="gap-3 py-5">
      <CardHeader className="px-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">{title}</CardTitle>
          <div className="flex items-center gap-2">
            {badge === undefined ? null : <Badge variant="outline">{badge}</Badge>}
            <Label_ label={label} />
          </div>
        </div>
      </CardHeader>
      <CardContent className="px-5 text-sm leading-relaxed text-muted-foreground">{children}</CardContent>
    </Card>
  );
}

function Detail({ n, children }: { readonly n: number; readonly children: ReactNode }) {
  return (
    <AccordionItem value={`q${String(n)}`} data-question={`q${String(n)}`} className="px-2">
      <AccordionTrigger className="py-3.5 text-sm hover:no-underline">
        <span className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground tabular-nums">Q{n}</span>
          <span>{QUESTIONS[n - 1]}</span>
        </span>
      </AccordionTrigger>
      <AccordionContent className="flex flex-col gap-3 text-sm leading-relaxed">{children}</AccordionContent>
    </AccordionItem>
  );
}

export function EvidenceCardView({ card, bundleVersion, g5Gate, figures, visual }: EvidenceCardViewProps) {
  // The encoder is out of rankings until G5 passes, both on the card's build and in validation.json.
  const g5Passed = g5Gate === 'PASS' && card.g5_status === 'PASS';
  const marsLimits = card.target.body === 'mars';
  const t = card.target;
  const d = card.distances_by_family;
  const missing = FAMILY_ORDER.filter((f) => d[f].status !== 'available' || d[f].value === null);
  const modeTitle = card.shipping_mode === 'C' ? 'Terrain fingerprint' : MODE_TITLES[card.shipping_mode];
  return (
    <article data-slot="evidence-card" className="flex min-w-0 flex-col gap-8 pt-6 wrap-anywhere sm:pt-8">
      {card.mock ? (
        <p role="status" className="rounded-lg bg-mock px-4 py-3 font-bold text-mock-foreground">
          MOCK PREVIEW: synthetic data, not production evidence
        </p>
      ) : null}
      <header className="flex flex-col gap-6">
        <Link href="/site" className="w-fit text-sm text-muted-foreground no-underline hover:text-foreground">
          ← All evidence cards
        </Link>
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary" className="capitalize">{t.body}</Badge>
            <Badge data-slot="mode-pill" variant="outline">{modeTitle}</Badge>
          </div>
          <h1 className="text-3xl font-semibold tracking-tight text-balance sm:text-4xl">{visual?.headline ?? card.title}</h1>
          <p className="max-w-3xl text-base text-muted-foreground">
            {card.title}. <span className="first-letter:uppercase">{card.wording}</span>.
          </p>
        </div>
        {visual === undefined ? null : (
          <div className="flex flex-col gap-2">
            <CardPair visual={visual} big />
            <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <EvidenceLabel label="Derived" /> Bundle hillshade tiles (45° altitude, 315° azimuth); the outline marks each 5 km analysis window.
            </p>
          </div>
        )}
      </header>

      <section aria-label="Terrain distances" data-slot="distances" className="grid gap-4 md:grid-cols-3">
        <Card className="py-5" data-family="terrain_relief" data-status={d.terrain_relief.status}>
          <CardContent className="px-5"><Metric name="Terrain relief" d={d.terrain_relief} size="lg" showLabel /></CardContent>
        </Card>
        <Card className="py-5" data-family="terrain_texture" data-status={d.terrain_texture.status}>
          <CardContent className="px-5"><Metric name="Terrain texture" d={d.terrain_texture} size="lg" showLabel /></CardContent>
        </Card>
        <Card className="gap-3 py-5">
          <CardContent className="flex flex-col gap-2 px-5">
            <p className="text-sm font-medium text-muted-foreground">How to read this</p>
            <p className="text-sm leading-relaxed">
              Each distance compares the two windows in its own units; lower is more similar. Families are never summed or ranked together.
            </p>
            {missing.length === 0 ? null : (
              <p className="text-xs text-muted-foreground">
                Not compared on this card: {missing.map((f) => `${FAMILY_NAMES[f]} (${human(d[f].reason)})`).join(', ')}.
              </p>
            )}
          </CardContent>
        </Card>
      </section>

      <Tabs defaultValue="overview" className="gap-5">
        <TabsList className="h-10 w-full sm:w-fit">
          <TabsTrigger value="overview" className="px-4 text-sm">Overview</TabsTrigger>
          <TabsTrigger value="figures" className="px-4 text-sm">Figures</TabsTrigger>
          <TabsTrigger value="details" className="px-4 text-sm">Details</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" forceMount className="flex flex-col gap-5 data-[state=inactive]:hidden">
          <div className="grid gap-5 lg:grid-cols-2">
            <div data-question="q2"><Prose title="What it could help investigate" label={card.q2_interpretation.evidence_label}>
              <p>{card.q2_interpretation.text}</p>
            </Prose></div>
            <div data-question="q7" data-question-body="q7"><Prose title="Proposed field test" label={card.q7_field_experiment.evidence_label} badge="Proposed">
              <p>{card.q7_field_experiment.text}</p>
            </Prose></div>
          </div>
          <Card data-slot="limits" className="gap-3 py-5">
            <CardHeader className="px-5"><CardTitle className="text-base">Scope of this card</CardTitle></CardHeader>
            <CardContent className="px-5 text-muted-foreground">
              <List items={card.claim_limits} empty="The record lists no claim limits." />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="figures" forceMount className="data-[state=inactive]:hidden">
          <Figures card={card} figures={figures} />
        </TabsContent>

        <TabsContent value="details" forceMount className="flex flex-col gap-5 data-[state=inactive]:hidden">
          <Accordion type="multiple" defaultValue={['q1']} className="rounded-xl bg-card">
            <Detail n={1}>
              <p>{card.q1_compared}</p>
              <div className="grid gap-2 sm:grid-cols-2">
                <Polygon title={`Target ${t.target_window_id}`} polygon={t.polygon} />
                <Polygon title={`Earth ${card.earth.earth_window_id}`} polygon={card.earth.polygon} />
              </div>
            </Detail>
            <Detail n={3}>
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
                            <span className="text-muted-foreground">Not available <Reason reason={m.reason} /></span>
                          ) : (
                            <>
                              <span data-slot="value" className="tabular-nums">{show(m.value)}</span>
                              {m.unit === null ? null : <span className="text-muted-foreground"> {m.unit}</span>}
                            </>
                          )}
                        </TableCell>
                        <TableCell><Label_ label={m.evidence_label} /></TableCell>
                        <TableCell>{m.support}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {[m.product_id, m.band, m.geometry].filter((x) => x !== null).join(' · ') || 'not recorded'}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <p className="text-xs text-muted-foreground">
                Background percentiles: relief {d.terrain_relief.background_percentile === null ? human(d.terrain_relief.background_percentile_reason) : show(d.terrain_relief.background_percentile)}
                {closerThan(d.terrain_relief) === null ? '' : ` (closer than ${String(closerThan(d.terrain_relief))}%)`}, texture {d.terrain_texture.background_percentile === null ? human(d.terrain_texture.background_percentile_reason) : show(d.terrain_texture.background_percentile)}
                {closerThan(d.terrain_texture) === null ? '' : ` (closer than ${String(closerThan(d.terrain_texture))}%)`}. Smaller distance is closer.
              </p>
              {card.radar_context.length === 0 ? null : (
                <div data-slot="radar-context" className="flex flex-col gap-2">
                  <h3 className="text-sm font-semibold">Observed radar context</h3>
                  <ul className="space-y-1 text-sm">
                    {card.radar_context.map((r) => (
                      <li key={`${r.product_id}-${r.band}-${r.observable}-${r.statistic}`}>
                        {r.observable} ({r.statistic}, {r.band}):{' '}
                        {r.value === null ? <>Not available <Reason reason={r.reason} /></> : <>{show(r.value)}{r.unit === null ? '' : ` ${r.unit}`}</>}{' '}
                        <Label_ label={r.evidence_label} />
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </Detail>
            <Detail n={4}>
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <h3 className="mb-1 text-sm font-semibold">{marsLimits ? 'Mars limits' : "Earth's limits"}</h3>
                  <List items={card.q4_differences.limits} empty="No limits listed in the record." />
                </div>
                <div>
                  <h3 className="mb-1 text-sm font-semibold">Differ</h3>
                  <List items={card.q4_differences.differing} empty="None listed." />
                </div>
                <div>
                  <h3 className="mb-1 text-sm font-semibold">Ambiguous</h3>
                  <List items={card.q4_differences.ambiguous} empty="None listed." />
                </div>
                <div>
                  <h3 className="mb-1 text-sm font-semibold">Not observed</h3>
                  <List items={dedupeByObservable(card.q4_differences.not_observed)} empty="None listed." />
                </div>
              </div>
            </Detail>
            <Detail n={5}>
              <Stability card={card} g5Passed={g5Passed} />
            </Detail>
            <Detail n={6}>
              <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
                {(['access', 'permits', 'season', 'land_status', 'logistics'] as const).map((k) => {
                  const a = card.q6_campaign[k];
                  return (
                    <div key={k} className="contents" data-campaign={k}>
                      <dt className="font-medium capitalize">{k.replace('_', ' ')}</dt>
                      <dd className="text-muted-foreground">
                        {a.value}
                        {a.source === null ? null : <span> (source: {a.source})</span>}
                      </dd>
                    </div>
                  );
                })}
              </dl>
            </Detail>
          </Accordion>
        </TabsContent>
      </Tabs>

      <footer className="flex flex-wrap gap-x-4 gap-y-1 border-t pt-4 text-xs text-muted-foreground">
        <span>Bundle {bundleVersion}</span>
        <span>Run {card.run_id}</span>
        <span>Card {card.card_id}</span>
      </footer>
    </article>
  );
}
