// The body of /compare/[a]/[b] (PRD 6.3, 6.5, 8.6, 10), a server component. It shows the windows
// side by side, the nu(L) charts for relief and texture, and the ComparisonRecord's distances in
// separate rows per family: terrain, radar and model distances are never summed, and a Mode B
// pair shows radar context per band without a radar distance. Everything displayed is a stored
// record field; nothing is computed on request.
import type { ComparisonRecord, FingerprintRecord } from '@terraluna/schema';

import { EvidenceLabel, type EvidenceLabelName, isEvidenceLabel } from '@/components/evidence-label';
import { LabelledValue } from '@/components/labelled-value';
import { PageHeader } from '@/components/page-header';
import { Section } from '@/components/section';
import { UnavailablePanel } from '@/components/model/unavailable-panel';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

import { findComparison, findWindow, type PairRecords, type WindowEntry } from './compare-data.ts';
import { NuChart } from './nu-chart.tsx';
import { buildNuSeries, type NamedSeries, type Observable, variantOf } from './series.ts';

type Distances = ComparisonRecord['distances_by_family'];
type Entry = Distances['terrain_relief'];

const NO_REASON = 'no_reason_in_record';

function DistanceValue({ entry, source }: { readonly entry: Entry; readonly source: string }) {
  const label: EvidenceLabelName = isEvidenceLabel(entry.evidence_label) ? entry.evidence_label : 'Unavailable';
  if (entry.status !== 'available' || entry.value === null || label === 'Unavailable') {
    return (
      <span data-status={entry.status}>
        <LabelledValue label="Unavailable" reason={entry.reason ?? NO_REASON} source={source} />{' '}
        <span className="text-muted-foreground">({entry.status})</span>
      </span>
    );
  }
  return <LabelledValue label={label} value={entry.value} unit={entry.unit} source={source} />;
}

// Display rounding to n significant digits; the full value stays in a title attribute.
const sig = (v: number, n: number): string => (Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(n))));

function DistanceRow({ name, entry, source }: { readonly name: string; readonly entry: Entry; readonly source: string }) {
  const spread = entry.reference_spread;
  return (
    <div data-family={name} className="[&_[data-slot=source]]:wrap-anywhere flex flex-col gap-2 border-b py-4 last:border-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="font-semibold">{name}</h4>
        <span className="font-mono text-lg [&_[data-slot=source]]:block [&_[data-slot=source]]:font-sans [&_[data-slot=source]]:text-sm [&_[data-slot=source]]:font-normal [&_[data-slot=source]]:text-muted-foreground">
          <DistanceValue entry={entry} source={source} />
        </span>
      </div>
      <dl className="grid gap-x-6 gap-y-1 text-sm text-muted-foreground sm:grid-cols-3">
        <div>
          <dt className="text-xs font-medium">Reference-set spread</dt>
          <dd>
            {spread === null ? (
              <LabelledValue label="Unavailable" reason={entry.reference_spread_reason ?? NO_REASON} />
            ) : (
              <span title={`min ${String(spread.min)}, median ${String(spread.median)}, max ${String(spread.max)}`}>
                {`min ${sig(spread.min, 4)}, median ${sig(spread.median, 4)}, max ${sig(spread.max, 4)} (n = ${String(spread.n)}) ${entry.unit}`}
              </span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium">Background percentile</dt>
          <dd>
            {entry.background_percentile === null ? (
              <LabelledValue label="Unavailable" reason={entry.background_percentile_reason ?? NO_REASON} />
            ) : (
              <span title={String(entry.background_percentile)}>{sig(entry.background_percentile, 4)}</span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium">Ties</dt>
          <dd>{entry.tied === true ? `tied (${entry.tie_group ?? NO_REASON})` : entry.tied === false ? 'not tied' : 'not recorded'}</dd>
        </div>
      </dl>
    </div>
  );
}

function FamilyTable({ title, note, children }: { readonly title: string; readonly note?: string; readonly children: React.ReactNode }) {
  return (
    <Card data-family-group={title} className="rounded-xl">
      <CardHeader>
        <p className="text-xs font-medium text-muted-foreground">Distance family</p>
        <CardTitle className="text-xl font-semibold">{title}</CardTitle>
        {note === undefined ? null : <p className="text-sm text-muted-foreground">{note}</p>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function RadarContext({ window: entry }: { readonly window: WindowEntry | null }) {
  const records: readonly FingerprintRecord[] = entry?.radar ?? [];
  if (entry === null || records.length === 0) {
    return <LabelledValue label="Unavailable" reason="no_radar_fingerprint_in_bundle_for_this_window" source="windows/<body>.json" />;
  }
  return (
    <ul className="flex flex-col gap-2">
      {records.map((record) => {
        const radar = record.radar;
        const key = `${record.window_id}-${record.product_ids.join('+')}`;
        if (radar === null) {
          return (
            <li key={key}>
              <LabelledValue label="Unavailable" reason="radar_block_absent_in_fingerprint" source={record.product_ids.join(', ')} />
            </li>
          );
        }
        const cpr = radar.cpr_ratio_of_mean_powers;
        return (
          <li key={key} data-band={radar.band}>
            <strong>Band {radar.band}</strong> ({radar.frequency_or_wavelength}, support {radar.support_m} m, valid{' '}
            {radar.valid_fraction}, n_valid {radar.n_valid}):{' '}
            {cpr === null || cpr.median === null || !isEvidenceLabel(record.evidence_label) || record.evidence_label === 'Unavailable' ? (
              <LabelledValue label="Unavailable" reason={radar.reason ?? NO_REASON} source={radar.product_id} />
            ) : (
              <LabelledValue label={record.evidence_label} value={cpr.median} unit="CPR, median of ratio of mean powers" source={radar.product_id} />
            )}
          </li>
        );
      })}
    </ul>
  );
}

function WindowCard({ role, id, entry }: { readonly role: string; readonly id: string; readonly entry: WindowEntry | null }) {
  return (
    <Card data-window={id} className="rounded-xl">
      <CardHeader>
        <p className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
          <span aria-hidden="true" className={role === 'Target' ? 'size-2.5 rounded-full bg-[#56B4E9]' : 'size-2.5 rounded-full bg-[#E69F00]'} />
          {role}
          <span>{role === 'Target' ? '(solid line, circles)' : '(dashed line, squares)'}</span>
        </p>
        <CardTitle className="text-xl font-semibold wrap-anywhere">{id}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm [&_[data-slot=labelled-value]]:max-w-full [&_[data-slot=source]]:max-w-full [&_[data-slot=source]]:wrap-anywhere">
        {entry === null ? (
          <LabelledValue label="Unavailable" reason="window_not_in_bundle_top_n_subset" source="windows/<body>.json" />
        ) : (
          <>
            <p>
              Body {entry.window.body}; area {entry.window.area_m2} m²; analysis grid {entry.window.analysis_grid_m} m; status{' '}
              {entry.status}
              {entry.reasons.length > 0 ? ` (${entry.reasons.join(', ')})` : ''}.
            </p>
            {(['relief', 'texture'] as const).map((observable) => {
              const variant = variantOf(entry.terrain, observable);
              if (variant === null || entry.terrain === null) {
                return <LabelledValue key={observable} label="Unavailable" reason="no_terrain_fingerprint_in_bundle" source={`${id} ${observable}`} />;
              }
              const label = entry.terrain.evidence_label;
              return variant.hurst !== null && isEvidenceLabel(label) && label !== 'Unavailable' ? (
                <p key={observable}>
                  {observable} Hurst exponent H:{' '}
                  <LabelledValue label={label} value={variant.hurst} unit={null} source={entry.terrain.product_ids.join(', ')} />
                </p>
              ) : (
                <p key={observable}>
                  {observable} Hurst exponent H:{' '}
                  <LabelledValue label="Unavailable" reason={variant.hurst_reason ?? NO_REASON} source={`${id} ${observable}`} />
                </p>
              );
            })}
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function namedSeries(role: string, entry: WindowEntry | null, id: string, observable: Observable): NamedSeries | null {
  const variant = variantOf(entry?.terrain ?? null, observable);
  if (variant === null || entry?.terrain == null) return null;
  return { id, role, series: buildNuSeries(variant), evidence: entry.terrain.evidence_label };
}

export function NotFound({ a, b }: { readonly a: string; readonly b: string }) {
  return (
    <>
      <PageHeader eyebrow="Compare" title="Pair not in this bundle" />
      <div className="mt-6">
        <UnavailablePanel title="No ComparisonRecord for this pair">
          <p>
            The bundle holds no comparison between <code>{a}</code> and <code>{b}</code>. Nothing is computed on request;
            only pairs listed in <code>comparisons/&lt;target_id&gt;.json</code> can be shown.
          </p>
        </UnavailablePanel>
      </div>
    </>
  );
}

export function CompareView({ records, a, b, detailOnly = false }: { readonly records: PairRecords; readonly a: string; readonly b: string; readonly detailOnly?: boolean }) {
  const record = findComparison(records, a, b);
  if (record === null) return <NotFound a={a} b={b} />;
  const wa = findWindow(records, a);
  const wb = findWindow(records, b);
  const d = record.distances_by_family;
  const src = record.source_ids.join(', ') || 'comparisons/' + a + '.json';
  const mode = record.shipping_mode;
  return (
    <>
      {detailOnly ? null : (
      <div className="flex flex-col gap-3 pt-2">
        <p className="text-xs font-medium text-primary">Compare</p>
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Compare two windows</h1>
        <p className="flex flex-wrap items-center gap-2 font-mono text-sm">
          <span className="inline-flex items-center gap-2 rounded-md border bg-card px-3 py-1 wrap-anywhere">
            <span aria-hidden="true" className="size-2.5 rounded-full bg-[#56B4E9]" />
            {a}
          </span>
          <span className="text-muted-foreground">vs</span>
          <span className="inline-flex items-center gap-2 rounded-md border bg-card px-3 py-1 wrap-anywhere">
            <span aria-hidden="true" className="size-2.5 rounded-full bg-[#E69F00]" />
            {b}
          </span>
        </p>
        <p className="max-w-3xl text-muted-foreground">
          Shipping mode {mode ?? 'not set'}. Curves and distances are read from the release bundle; nothing is computed on request.
        </p>
      </div>
      )}
      <div className="mt-6 grid gap-4 md:grid-cols-2 [&>*]:min-w-0">
        <WindowCard role="Target" id={a} entry={wa} />
        <WindowCard role="Earth candidate" id={b} entry={wb} />
      </div>

      {detailOnly ? null : (
      <Section title="Structure function ν(L)" description="Log-log axes. Solid blue with circles is the target; dashed orange with squares is the Earth candidate. Dotted vertical lines mark lags without a value, and the reason is in the table.">
        <div className="grid gap-6 lg:grid-cols-2 [&>*]:min-w-0">
          {(['relief', 'texture'] as const).map((observable) => {
            const series = [namedSeries('Target', wa, a, observable), namedSeries('Earth candidate', wb, b, observable)].filter(
              (s): s is NamedSeries => s !== null,
            );
            return series.length === 0 ? (
              <UnavailablePanel key={observable} title={`${observable} ν(L) curves`}>
                <p>Neither window has a terrain fingerprint in the bundle (windows/&lt;body&gt;.json).</p>
              </UnavailablePanel>
            ) : (
              <NuChart key={observable} name={`${observable === 'relief' ? 'Relief' : 'Texture'} ν(L) for ${a} and ${b}`} series={series} />
            );
          })}
        </div>
        <UnavailablePanel title="Target clone envelope">
          <p>
            Drawn only when the bundle carries it. This bundle has no per-lag clone envelope in{' '}
            <code>comparisons/{a}.json</code> or <code>windows/&lt;body&gt;.json</code>; the reference-set spread per family is
            shown with each distance below.
          </p>
        </UnavailablePanel>
      </Section>
      )}

      <Section title="Distances" description="One distance per family, each with its own unit and evidence label. They are never summed or averaged into one score.">
        <div className="flex flex-col gap-4">
          <FamilyTable title="Terrain distances" note="Relief and texture are separate distances.">
            <DistanceRow name="Terrain relief" entry={d.terrain_relief} source={src} />
            <DistanceRow name="Terrain texture" entry={d.terrain_texture} source={src} />
          </FamilyTable>
          {mode === 'B' ? (
            <Card data-family-group="Radar context">
              <CardHeader>
                <CardTitle>Radar context (no radar distance in Mode B)</CardTitle>
                <p className="text-sm text-muted-foreground">Radar is shown per band as context for each window and is not compared.</p>
              </CardHeader>
              <CardContent className="grid gap-4 md:grid-cols-2">
                <div><h3 className="mb-1 text-sm font-semibold">{a}</h3><RadarContext window={wa} /></div>
                <div><h3 className="mb-1 text-sm font-semibold">{b}</h3><RadarContext window={wb} /></div>
              </CardContent>
            </Card>
          ) : mode === 'C' ? (
            <UnavailablePanel title="Radar distance">
              <p>Terrain fingerprint comparison.</p>
            </UnavailablePanel>
          ) : (
            <FamilyTable title="Radar distance">
              <DistanceRow name="Radar CPR" entry={d.radar_cpr} source={src} />
            </FamilyTable>
          )}
          <FamilyTable
            title="Model distance (EXPERIMENTAL)"
            note="The encoder is outside every ranking until gate G5 passes; this distance is Predicted and is not added to any other family."
          >
            <DistanceRow name="Model embedding" entry={d.model_embedding} source={src} />
          </FamilyTable>
        </div>
      </Section>

      <Section title="Uncertainty and limits">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">Component</TableHead>
              <TableHead scope="col">Evidence</TableHead>
              <TableHead scope="col">Status</TableHead>
              <TableHead scope="col">Reason or note</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {Object.entries(record.uncertainty_components).map(([name, component]) => (
              <TableRow key={name}>
                <TableHead scope="row">{name}</TableHead>
                <TableCell>
                  <EvidenceLabel label={component.evidence_label} />
                </TableCell>
                <TableCell className="whitespace-nowrap">{component.status}</TableCell>
                <TableCell>{component.reason ?? component.note ?? 'none recorded'}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <h3 className="font-semibold">Claim limits</h3>
        {record.claim_limits.length === 0 ? (
          <p className="text-muted-foreground">The record lists no claim limits.</p>
        ) : (
          <ul className="list-disc pl-6">
            {record.claim_limits.map((limit) => (
              <li key={limit}>{limit}</li>
            ))}
          </ul>
        )}
        {record.excluded_observables_and_reasons.length === 0 ? null : (
          <>
            <h3 className="font-semibold">Excluded observables</h3>
            <ul className="list-disc pl-6">
              {record.excluded_observables_and_reasons.map((x) => (
                <li key={x.observable}>
                  {x.observable}: {x.reasons.join(', ')}
                </li>
              ))}
            </ul>
          </>
        )}
        <p className="text-sm break-all text-muted-foreground">
          Sources: {record.source_ids.join(', ')}. Run {record.run_id}.
        </p>
      </Section>
    </>
  );
}
