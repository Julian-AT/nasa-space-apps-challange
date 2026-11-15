// The model report (PRD 11.9, 8.6, 11.7, 11.10): renders the ModelRecord and Metrics the ML
// team ships in the bundle and the G5 GateRecord of validation.json. Nothing here is typed in:
// every status, threshold and number is a record field. The encoder is EXPERIMENTAL and outside
// every ranking while G5 is not PASS; every metric is Predicted.
import type { Curves, Examples, GateRecord, Metrics, ModelRecord } from '@terraluna/schema';

import { EvidenceLabel } from '@/components/evidence-label';
import { LabelledValue } from '@/components/labelled-value';
import { PageHeader } from '@/components/page-header';
import { Section } from '@/components/section';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

import { CurvesSection, ExamplesSection } from './curves-examples.tsx';
import { UnavailablePanel } from './unavailable-panel.tsx';

type MetricRecord = Metrics['tests'][number];
type Condition = Metrics['g5']['conditions'][number];

const VARIANT = { PASS: 'pass', FAIL: 'fail', PARTIAL: 'partial', NOT_RUN: 'not-run', NOT_APPLICABLE: 'not-applicable' } as const;

export function G5Status({ status }: { readonly status: string }) {
  const variant = VARIANT[status as keyof typeof VARIANT];
  return (
    <Badge variant={variant} data-slot="g5-status" className="h-8 px-3 text-base">
      {status}
    </Badge>
  );
}

function ExperimentalBanner({ g5 }: { readonly g5: GateRecord | null }) {
  const status = g5?.status ?? null;
  return (
    <div data-slot="experimental" className="flex flex-col gap-3 rounded-2xl border border-evidence-predicted/60 bg-card p-6">
      <p className="font-mono text-xs tracking-[0.18em] text-muted-foreground uppercase">Encoder status</p>
      <p className="font-serif text-6xl leading-none text-evidence-predicted">EXPERIMENTAL</p>
      <p className="flex flex-wrap items-center gap-3 text-lg">
        <span>Gate G5 status:</span>
        {status === null ? (
          <LabelledValue label="Unavailable" reason="validation.json has no G5 GateRecord" source="validation.json" />
        ) : (
          <>
            <G5Status status={status} />
          </>
        )}
      </p>
      <p>
        {status === 'PASS'
          ? 'G5 passed, but the encoder stays a Predicted second opinion: it never replaces the deterministic fingerprint and supports no radar, trafficability, regolith or ice claim.'
          : 'The encoder is outside every ranking while G5 is not PASS. It enters no ranking, no evidence card and no shortlist.'}
      </p>
      {g5?.result == null ? null : <p className="text-muted-foreground">G5 result: {g5.result}</p>}
      {g5?.consequence == null ? null : <p className="text-muted-foreground">Consequence: {g5.consequence}</p>}
    </div>
  );
}

function StatTile({ eyebrow, children, note }: { readonly eyebrow: string; readonly children: React.ReactNode; readonly note?: string }) {
  return (
    <div data-slot="stat-tile" className="flex flex-col gap-2 rounded-xl border border-line bg-card p-4">
      <p className="font-mono text-xs tracking-[0.18em] text-muted-foreground uppercase">{eyebrow}</p>
      <p className="font-mono text-3xl">{children}</p>
      {note === undefined ? null : <p className="text-sm text-muted-foreground">{note}</p>}
    </div>
  );
}

function StatTiles({ modelCard, metrics, g5 }: ModelReportProps) {
  return (
    <div data-slot="stat-tiles" className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <StatTile eyebrow="Gate G5" note="from validation.json">
        {g5 === null ? 'no record' : g5.status}
      </StatTile>
      {metrics === null ? null : (
        <StatTile eyebrow="Failed tests" note="named in the G5 section">
          {String(metrics.g5.failed_tests.length)}
        </StatTile>
      )}
      {metrics === null ? null : (
        <StatTile eyebrow="Metrics reported" note={`${metrics.evaluated_split} split, Predicted`}>
          {String(metrics.tests.length)}
        </StatTile>
      )}
      {modelCard === null ? null : (
        <StatTile eyebrow="Parameters" note={modelCard.architecture}>
          {String(modelCard.parameter_count)}
        </StatTile>
      )}
    </div>
  );
}

// Display rounding to 6 significant digits; the full value stays in a title attribute.
const r6 = (v: number): string => (Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(6))));
function N({ v }: { readonly v: number | string }) {
  return typeof v === 'number' ? <span title={String(v)}>{r6(v)}</span> : <>{v}</>;
}
function fmt(v: number | null): string {
  return v === null ? '' : r6(v);
}

function ConditionsTable({ metrics }: { readonly metrics: Metrics }) {
  const { g5 } = metrics;
  return (
    <>
      <p>
        Metrics G5 status: <span className="font-mono">{g5.status}</span>. Failed tests:{' '}
        {g5.failed_tests.length === 0 ? (
          <span data-slot="failed-tests">none listed</span>
        ) : (
          <span data-slot="failed-tests">
            {g5.failed_tests.map((t, i) => (
              <span key={t}>
                {i > 0 ? ', ' : ''}
                <code>{t}</code>
              </span>
            ))}
          </span>
        )}
        .
      </p>
      <Table>
        <TableCaption>G5 conditions as serialized in report/metrics.json</TableCaption>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">Condition</TableHead>
            <TableHead scope="col">Test</TableHead>
            <TableHead scope="col">Statistic</TableHead>
            <TableHead scope="col">Threshold</TableHead>
            <TableHead scope="col">Observed</TableHead>
            <TableHead scope="col">Passed</TableHead>
            <TableHead scope="col">Reason</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {g5.conditions.map((c: Condition) => (
            <TableRow key={`${String(c.condition)}-${c.test_id}`} data-passed={String(c.passed)}>
              <TableCell>{c.condition}</TableCell>
              <TableCell className="font-mono text-xs whitespace-nowrap">{c.test_id}</TableCell>
              <TableCell>{c.statistic}</TableCell>
              <TableCell>{c.threshold === null ? <NotRecorded what="threshold" /> : <N v={c.threshold} />}</TableCell>
              <TableCell>{c.observed === null ? <NotRecorded what="observed value" /> : <N v={c.observed} />}</TableCell>
              <TableCell>{c.passed === null ? 'not evaluated' : c.passed ? 'passed' : 'failed'}</TableCell>
              <TableCell>{c.reason ?? ''}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  );
}

function NotRecorded({ what }: { readonly what: string }) {
  return <LabelledValue label="Unavailable" reason={`${what} is null in the record`} />;
}

function MetricsTable({ caption, tests }: { readonly caption: string; readonly tests: readonly MetricRecord[] }) {
  return (
    <Table>
      <TableCaption>{caption}</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">Test</TableHead>
          <TableHead scope="col">Measure</TableHead>
          <TableHead scope="col">Target and body</TableHead>
          <TableHead scope="col">Value (95% interval)</TableHead>
          <TableHead scope="col">Baselines</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {tests.map((m: MetricRecord, i) => (
          <TableRow key={`${m.test_id}-${m.measure}-${m.target ?? ''}-${m.body ?? ''}-${String(i)}`}>
            <TableCell className="font-mono text-xs whitespace-nowrap">{m.test_id}</TableCell>
            <TableCell>{m.measure}</TableCell>
            <TableCell>
              {m.target ?? 'no target'}; {m.body ?? 'no body'}
            </TableCell>
            <TableCell>
              {m.value === null ? (
                <LabelledValue label="Unavailable" reason={m.unavailable_reason ?? 'value is null in the record'} source="report/metrics.json" />
              ) : (
                <span>
                  <EvidenceLabel label="Predicted" /> <strong><N v={m.value} /></strong>{' '}
                  {m.ci_low === null || m.ci_high === null ? (
                    <span>(interval not recorded)</span>
                  ) : (
                    <span title={`[${String(m.ci_low)}, ${String(m.ci_high)}]`}>{`[${r6(m.ci_low)}, ${r6(m.ci_high)}]`}</span>
                  )}
                  {m.n_blocks === null ? null : <span> n_blocks {m.n_blocks}</span>}
                  {m.n_bootstrap === null ? null : <span>, {m.n_bootstrap} bootstrap draws</span>}
                </span>
              )}
            </TableCell>
            <TableCell>
              {m.baselines.length === 0 ? (
                'none recorded'
              ) : (
                <ul>
                  {m.baselines.map((b) => (
                    <li key={b.name}>
                      {b.name}:{' '}
                      {b.value === null ? (
                        <LabelledValue label="Unavailable" reason={b.unavailable_reason ?? 'value is null in the record'} />
                      ) : (
                        <>
                          <N v={b.value} />
                          {b.ci_low === null || b.ci_high === null ? '' : ` [${fmt(b.ci_low)}, ${fmt(b.ci_high)}]`}
                          {b.paired_diff_ci_low === null || b.paired_diff_ci_high === null
                            ? ''
                            : `; paired difference [${fmt(b.paired_diff_ci_low)}, ${fmt(b.paired_diff_ci_high)}]`}
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function Field({ name, children }: { readonly name: string; readonly children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 border-b border-line py-2 sm:flex-row sm:gap-6">
      <dt className="w-48 shrink-0 text-sm font-semibold text-muted-foreground">{name}</dt>
      <dd className="wrap-anywhere">{children}</dd>
    </div>
  );
}

function CardFields({ card }: { readonly card: ModelRecord }) {
  const t = card.training_data;
  return (
    <dl data-slot="model-card">
      <Field name="model_id">{card.model_id}</Field>
      <Field name="version">{card.version}</Field>
      <Field name="architecture">{card.architecture}</Field>
      <Field name="parameter_count">{card.parameter_count}</Field>
      <Field name="g5_status">{card.g5_status}</Field>
      <Field name="training bodies">{t.bodies.join(', ')}</Field>
      <Field name="training products">{t.product_ids.join(', ')}</Field>
      <Field name="window counts per body and split">
        {Object.entries(t.window_counts)
          .map(([body, splits]) =>
            `${body}: ${Object.entries(splits)
              .map(([split, n]) => `${split} ${n === null ? 'not counted' : String(n)}`)
              .join(', ')}`,
          )
          .join('; ')}
      </Field>
      <Field name="split grouping">
        {Object.entries(t.split_groups).map(([body, group]) => `${body}: ${group}`).join('; ')}
      </Field>
      <Field name="seeds">{card.seeds.join(', ')}</Field>
      <Field name="intended_use">{card.intended_use}</Field>
      <Field name="limitations">{card.limitations}</Field>
      <Field name="licence">{card.licence}</Field>
      <Field name="onnx_sha256">
        {card.onnx_sha256 === null ? <LabelledValue label="Unavailable" reason="no ONNX export in the record" /> : <code>{card.onnx_sha256}</code>}
      </Field>
    </dl>
  );
}

export interface ModelReportProps {
  readonly modelCard: ModelRecord | null;
  readonly metrics: Metrics | null;
  /** The G5 GateRecord of validation.json, or null when it lists none. */
  readonly g5: GateRecord | null;
  readonly curves?: Curves | null;
  readonly examples?: Examples | null;
}

export function ModelReport({ modelCard, metrics, g5, curves = null, examples = null }: ModelReportProps) {
  return (
    <>
      <PageHeader
        eyebrow="Model report"
        title="Physics-informed encoder"
        lead={`An experimental encoder.${g5 === null ? ' Its promotion gate G5 is not recorded in this bundle.' : g5.status === 'PASS' ? ' It passed its promotion gate (G5 PASS) and stays a Predicted second opinion.' : g5.status === 'FAIL' ? ' It failed its promotion gate (G5 FAIL) and takes no part in any ranking.' : ' It has not passed its promotion gate and takes no part in any ranking.'} Every number below is Predicted by a fitted model and is read from the release bundle.`}
      />
      <div className="mt-6">
        <ExperimentalBanner g5={g5} />
      </div>
      {modelCard === null && metrics === null ? null : <StatTiles modelCard={modelCard} metrics={metrics} g5={g5} />}
      {modelCard === null && metrics === null ? (
        <Section title="Model card and metrics">
          <UnavailablePanel title="No model report in this bundle">
            <p>
              The bundle does not ship <code>model/model_card.json</code> or <code>report/metrics.json</code>, so there is no
              model card, no metric and no confidence interval to show. Nothing is estimated here.
            </p>
            <p>The G5 gate record above is the only model statement this page can make.</p>
          </UnavailablePanel>
        </Section>
      ) : (
        <>
          <Section title="G5 verdict">
            {metrics === null ? (
              <UnavailablePanel title="G5 conditions">
                <p>
                  <code>report/metrics.json</code> is not in this bundle, so no condition, threshold or failed test is shown.
                </p>
              </UnavailablePanel>
            ) : (
              <ConditionsTable metrics={metrics} />
            )}
          </Section>
          <Section title="Metrics with 95% intervals">
            {metrics === null ? (
              <UnavailablePanel title="Metrics">
                <p>
                  <code>report/metrics.json</code> is not in this bundle.
                </p>
              </UnavailablePanel>
            ) : (
              <MetricsTable
                caption={`Test metrics on the ${metrics.evaluated_split} split, model ${metrics.model_id}, with 95% intervals`}
                tests={metrics.tests}
              />
            )}
          </Section>
          {metrics === null ? null : (
            <Section title="Ablations and run record">
              {metrics.ablations.length === 0 ? (
                <LabelledValue label="Unavailable" reason="report/metrics.json lists no ablation run" source="report/metrics.json" />
              ) : (
                metrics.ablations.map((run) => (
                  <div key={run.run_name} data-ablation={run.run_name} className="flex flex-col gap-2">
                    <h3 className="font-semibold">{run.run_name}</h3>
                    {run.status === 'unavailable' ? (
                      <LabelledValue label="Unavailable" reason={run.reason ?? 'no reason in the record'} source="report/metrics.json" />
                    ) : (
                      <MetricsTable caption={`Ablation ${run.run_name}`} tests={run.metrics} />
                    )}
                  </div>
                ))
              )}
              <dl data-slot="run-record">
                <Field name="hardware">{metrics.run_record.hardware}</Field>
                <Field name="GPU hours">
                  {metrics.run_record.gpu_hours_total === null ? (
                    <LabelledValue label="Unavailable" reason="gpu_hours_total is null in the record" />
                  ) : (
                    `${String(metrics.run_record.gpu_hours_total)}${metrics.run_record.gpu_hours_partial ? ' (partial)' : ''}`
                  )}
                </Field>
                <Field name="config_hash">
                  <code>{metrics.run_record.config_hash}</code>
                </Field>
                <Field name="evaluation run_id">{metrics.run_id ?? 'not recorded'}</Field>
                {metrics.mock ? <Field name="mock">This metrics file is marked mock.</Field> : null}
              </dl>
            </Section>
          )}
          <Section title="Model card">
            {modelCard === null ? (
              <UnavailablePanel title="Model card">
                <p>
                  <code>model/model_card.json</code> is not in this bundle.
                </p>
              </UnavailablePanel>
            ) : (
              <CardFields card={modelCard} />
            )}
          </Section>
        </>
      )}
      <CurvesSection curves={curves} />
      <ExamplesSection examples={examples} />
    </>
  );
}
