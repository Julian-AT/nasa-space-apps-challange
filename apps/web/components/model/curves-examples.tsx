// Training curves (report/curves.json) and retrieval examples (report/examples.json) on /model
// (PRD 11.9 items 3, 5, 7, 8). Every value is a record field; absent files give an Unavailable
// line naming the file. Encoder output is Predicted and always sits beside, never inside, the
// deterministic ranking.
import { Card } from '@/components/ui/card';
import type { Curves, Examples } from '@terraluna/schema';

import { EvidenceLabel, isEvidenceLabel } from '@/components/evidence-label';
import { LabelledValue } from '@/components/labelled-value';
import { Section } from '@/components/section';
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

import { LossCurve } from './loss-curve.tsx';

type Entry = Examples['failure_cases']['entries'][number];

function interval(v: { value: number | null; ci_low: number | null; ci_high: number | null }): string {
  return v.value === null ? 'not recorded' : `${String(v.value)} [${String(v.ci_low ?? 'n/a')}, ${String(v.ci_high ?? 'n/a')}]`;
}

export function CurvesSection({ curves }: { readonly curves: Curves | null }) {
  if (curves === null) {
    return (
      <Section title="Training curves">
        <LabelledValue label="Unavailable" reason="report/curves.json is not in this bundle" source="report/curves.json" />
      </Section>
    );
  }
  return (
    <Section title="Training curves" description="Each loss term over training: mean over seeds with the min to max range shaded.">
      {curves.mock ? <p>report/curves.json is marked mock.</p> : null}
      {curves.loss_terms.length === 0 ? (
        <LabelledValue label="Unavailable" reason="curves.json lists no loss term" source="report/curves.json" />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {curves.loss_terms.map((t) => (
            <LossCurve key={t.term} term={t.term} epochs={t.epochs} />
          ))}
        </div>
      )}
      <details className="rounded-xl bg-card px-4 ring-1 ring-foreground/10">
      <summary className="cursor-pointer py-4 text-sm font-medium">Training run details: compute, invariance trade-off and probe metrics</summary>
      <div className="flex flex-col gap-4 pb-4">
      <h3 className="font-semibold">Compute hours</h3>
      <p>
        Total:{' '}
        {curves.gpu_hours.total_hours === null ? (
          <LabelledValue label="Unavailable" reason="total_hours is null in the record" source="report/curves.json" />
        ) : (
          <span>{curves.gpu_hours.total_hours}</span>
        )}
        {curves.gpu_hours.partial ? ' (partial: some runs have no hours)' : ''}
      </p>
      {curves.gpu_hours.runs.length === 0 ? null : (
        <Table>
          <TableCaption>GPU hours per run</TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">Run</TableHead>
              <TableHead scope="col">Hours</TableHead>
              <TableHead scope="col">Source</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {curves.gpu_hours.runs.map((r) => (
              <TableRow key={r.run_name}>
                <TableCell>{r.run_name}</TableCell>
                <TableCell>
                  {r.hours === null ? <LabelledValue label="Unavailable" reason={r.reason ?? 'no reason in the record'} /> : r.hours}
                </TableCell>
                <TableCell>{r.source ?? 'not recorded'}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <h3 className="font-semibold">Invariance trade-off over lambda_max</h3>
      <p className="text-sm text-muted-foreground">Chance level of the body probe: {curves.chance_level_body_probe}.</p>
      {curves.tradeoff.length === 0 ? (
        <LabelledValue label="Unavailable" reason="curves.json lists no trade-off point" source="report/curves.json" />
      ) : (
        <Table>
          <TableCaption>Body-probe balanced accuracy against physics R² per lambda_max, 95% intervals</TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">lambda_max</TableHead>
              <TableHead scope="col">Body-probe balanced accuracy</TableHead>
              <TableHead scope="col">Physics R²</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {curves.tradeoff.map((p) => (
              <TableRow key={String(p.lambda_max)}>
                <TableCell>{p.lambda_max}</TableCell>
                {p.status === 'unavailable' ? (
                  <TableCell colSpan={2}>
                    <LabelledValue label="Unavailable" reason={p.reason ?? 'no reason in the record'} source="report/curves.json" />
                  </TableCell>
                ) : (
                  <>
                    <TableCell>
                      <EvidenceLabel label="Predicted" /> {interval(p.body_probe_balanced_accuracy)}
                    </TableCell>
                    <TableCell>
                      <EvidenceLabel label="Predicted" /> {interval(p.physics_r2)}
                    </TableCell>
                  </>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <h3 className="font-semibold">Validation probe metrics</h3>
      {curves.validation_probe_metrics.status === 'unavailable' ? (
        <LabelledValue label="Unavailable" reason={curves.validation_probe_metrics.reason ?? 'no reason in the record'} source="report/curves.json" />
      ) : (
        <ul className="list-disc pl-6">
          {curves.validation_probe_metrics.metrics.map((m, i) => (
            <li key={`${m.test_id}-${m.measure}-${String(i)}`}>
              {m.test_id} / {m.measure}
              {m.target === null ? '' : ` / ${m.target}`}:{' '}
              {m.value === null ? (
                <LabelledValue label="Unavailable" reason={m.unavailable_reason ?? 'value is null in the record'} />
              ) : (
                <span>
                  <EvidenceLabel label="Predicted" /> {interval(m)}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      </div>
      </details>
    </Section>
  );
}

function EntryList({ title, entries }: { readonly title: string; readonly entries: readonly Entry[] }) {
  return (
    <div className="flex flex-col gap-1">
      <h4 className="font-mono text-xs text-muted-foreground">{title}</h4>
      {entries.length === 0 ? (
        <p>none in the record</p>
      ) : (
        <ol className="flex flex-col gap-1">
          {entries.map((e) => (
            <li key={`${String(e.rank)}-${e.window_id}`} className="flex flex-wrap items-center gap-2">
              <span className="font-mono">{e.rank}.</span> <span className="wrap-anywhere">{e.window_id}</span>
              {isEvidenceLabel(e.evidence_label) ? <EvidenceLabel label={e.evidence_label} /> : null}
              <span className="text-sm text-muted-foreground">
                {e.distance_family}:{' '}
                {e.distance === null ? 'distance not recorded' : <span title={String(e.distance)} className="font-mono tabular-nums">{e.distance.toFixed(3)}</span>}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export function ExamplesSection({ examples }: { readonly examples: Examples | null }) {
  if (examples === null) {
    return (
      <Section title="Retrieval examples">
        <LabelledValue label="Unavailable" reason="report/examples.json is not in this bundle" source="report/examples.json" />
      </Section>
    );
  }
  return (
    <Section
      title="Retrieval examples"
      description="Nearest neighbours from the experimental learned encoder (Predicted), shown beside the physics-fingerprint top 5 (Derived) that drives the rankings."
    >
      {examples.mock ? <p>report/examples.json is marked mock.</p> : null}
      {examples.retrieval_gallery.length === 0 ? (
        <LabelledValue label="Unavailable" reason="examples.json lists no retrieval target" source="report/examples.json" />
      ) : (
        examples.retrieval_gallery.map((t) => (
          <Card key={t.target_id} size="sm">
          <div data-retrieval-target={t.target_id} className="grid gap-4 px-4 md:grid-cols-2">
            <h3 className="font-semibold md:col-span-2 wrap-anywhere">{t.target_id}</h3>
            {t.deterministic_top5.length === 0 ? null : <EntryList title="Physics fingerprint top 5" entries={t.deterministic_top5} />}
            <EntryList title="Learned encoder neighbours (experimental)" entries={t.encoder_top5} />
            {t.deterministic_top5.length === 0 ? (
              <p className="text-sm text-muted-foreground">Physics-fingerprint rankings are shown on the evidence cards.</p>
            ) : null}
          </div>
          </Card>
        ))
      )}
    </Section>
  );
}
