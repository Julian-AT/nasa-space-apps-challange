// Training curves (report/curves.json) and retrieval examples (report/examples.json) on /model
// (PRD 11.9 items 3, 5, 7, 8). Every value is a record field; absent files give an Unavailable
// line naming the file. Encoder output is Predicted and always sits beside, never inside, the
// deterministic ranking.
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
    <Section title="Training curves" description="Mean over seeds with the min to max range. Predicted: fitted-model training diagnostics.">
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
      <h3 className="font-semibold">GPU hours</h3>
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
    </Section>
  );
}

function EntryList({ title, entries }: { readonly title: string; readonly entries: readonly Entry[] }) {
  return (
    <div className="flex flex-col gap-1">
      <h4 className="font-mono text-xs tracking-[0.14em] text-muted-foreground uppercase">{title}</h4>
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
                {e.distance === null ? 'distance not recorded' : String(e.distance)}
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
      description="Encoder neighbours (Predicted, EXPERIMENTAL, unranked) are shown beside the deterministic top 5 (Derived); they never replace it."
    >
      {examples.mock ? <p>report/examples.json is marked mock.</p> : null}
      {examples.retrieval_gallery.length === 0 ? (
        <LabelledValue label="Unavailable" reason="examples.json lists no retrieval target" source="report/examples.json" />
      ) : (
        examples.retrieval_gallery.map((t) => (
          <div key={t.target_id} data-retrieval-target={t.target_id} className="grid gap-4 rounded-xl border bg-card p-4 text-card-foreground md:grid-cols-2">
            <h3 className="font-semibold md:col-span-2 wrap-anywhere">{t.target_id}</h3>
            <EntryList title="Deterministic top 5" entries={t.deterministic_top5} />
            <EntryList title="Encoder top 5 (EXPERIMENTAL)" entries={t.encoder_top5} />
            {t.shortfall_reason === null ? null : <p className="md:col-span-2">Shortfall: {t.shortfall_reason}</p>}
          </div>
        ))
      )}
      <h3 className="font-semibold">Failure cases</h3>
      <p className="text-sm text-muted-foreground">Sort rule: {examples.failure_cases.sort_rule}</p>
      <EntryList title="Failure cases" entries={examples.failure_cases.entries} />
      {examples.failure_cases.shortfall_reason === null ? null : <p>Shortfall: {examples.failure_cases.shortfall_reason}</p>}
      <h3 className="font-semibold">Out-of-distribution examples</h3>
      <EntryList title="Out of distribution" entries={examples.ood_examples} />
    </Section>
  );
}
