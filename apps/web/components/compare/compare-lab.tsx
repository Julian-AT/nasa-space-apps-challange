// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The compare page for one card pair (PRD 8.6, 10), a server component: header and map stage
// (client), the two terrain distances with their evidence labels and the evidence card link, one
// nu(L) chart card, and a collapsed Details section with mode, bundle version, the other distance
// families' recorded reasons, claim limits and the stored nu(L) values. Every number shown is a
// stored bundle field; a missing value shows its recorded reason, never a zero.
import { ArrowRight01Icon } from '@hugeicons/core-free-icons';
import { HugeiconsIcon } from '@hugeicons/react';
import type { CardRecord } from '@terraluna/schema';
import Link from 'next/link';

import { namedSeries } from '@/components/charts/compare-view';
import type { PairRecords } from '@/components/charts/compare-data';
import { findWindow } from '@/components/charts/compare-data';
import { NuTable } from '@/components/charts/nu-table';
import type { NamedSeries } from '@/components/charts/series';
import { EvidenceLabel, isEvidenceLabel } from '@/components/evidence-label';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import { buttonVariants } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { cn } from '@/lib/utils';

import { CompareStage } from './compare-stage.tsx';
import { CurvesCard } from './curves-card.tsx';
import type { PairOption } from './kit.tsx';
import type { LabPair } from './lab-data.ts';

type Entry = CardRecord['distances_by_family']['terrain_relief'];

const show = (v: number): string => String(Number(v.toPrecision(4)));
const hasValue = (e: Entry): boolean => e.status === 'available' && e.value !== null && e.evidence_label !== 'Unavailable';

function DistanceCard({ name, hint, entry }: { readonly name: string; readonly hint: string; readonly entry: Entry }) {
  const has = hasValue(entry);
  return (
    <Card size="sm" className="min-w-0" data-family-stat={name}>
      <CardContent className="flex h-full flex-col gap-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-medium">{name}</p>
            <p className="text-xs text-muted-foreground">{hint}</p>
          </div>
          {isEvidenceLabel(entry.evidence_label) ? <EvidenceLabel label={entry.evidence_label} /> : null}
        </div>
        {has ? (
          <p className="mt-auto flex items-baseline gap-2">
            <span className="text-4xl font-semibold tracking-tight tabular-nums" title={String(entry.value)}>
              {show(entry.value ?? Number.NaN)}
            </span>
            <span className="text-sm text-muted-foreground">{entry.unit}</span>
          </p>
        ) : (
          <p className="mt-auto text-sm text-muted-foreground">No value in this card ({entry.reason ?? entry.status}).</p>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ k, children }: { readonly k: string; readonly children: React.ReactNode }) {
  return (
    <div className="grid gap-1 py-2.5 sm:grid-cols-[200px_1fr] sm:gap-4">
      <dt className="text-sm text-muted-foreground">{k}</dt>
      <dd className="min-w-0 text-sm wrap-anywhere">{children}</dd>
    </div>
  );
}

function familyText(e: Entry): string {
  if (hasValue(e)) {
    const pct = e.background_percentile === null ? `background percentile not recorded (${e.background_percentile_reason ?? 'no reason recorded'})` : `background percentile ${String(e.background_percentile)}`;
    return `${String(e.value)} ${e.unit}, ${e.evidence_label}; ${pct}`;
  }
  return `No value (${e.reason ?? e.status}), ${e.evidence_label}`;
}

export interface CompareLabProps {
  readonly card: CardRecord;
  readonly pair: LabPair;
  readonly index: number;
  readonly total: number;
  readonly options: readonly PairOption[];
  readonly current: string;
  readonly records: PairRecords;
  readonly bundleVersion: string;
}

export function CompareLab({ card, pair, index, total, options, current, records, bundleVersion }: CompareLabProps) {
  const d = card.distances_by_family;
  const wa = findWindow(records, pair.a);
  const wb = findWindow(records, pair.b);
  const seriesFor = (observable: 'relief' | 'texture'): NamedSeries[] =>
    [namedSeries(pair.targetName, wa, pair.a, observable), namedSeries(pair.earthName, wb, pair.b, observable)].filter((s): s is NamedSeries => s !== null);
  const series = { relief: seriesFor('relief'), texture: seriesFor('texture') };
  return (
    <div className="pb-24">
      {card.mock ? (
        <div className="bg-mock">
          <p role="status" className="mx-auto max-w-[1400px] px-4 py-1.5 text-xs font-bold text-mock-foreground sm:px-6">
            MOCK card preview: not a scientific result and not part of a production bundle.
          </p>
        </div>
      ) : null}
      <div className="mx-auto flex max-w-[1400px] flex-col gap-6 px-4 pt-8 sm:px-6 sm:pt-10">
        <div>
          <CompareStage pair={pair} current={current} options={options} />
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          <DistanceCard name="Relief distance" hint="Shape of the height field" entry={d.terrain_relief} />
          <DistanceCard name="Texture distance" hint="Fine surface roughness" entry={d.terrain_texture} />
          <Card size="sm" className="min-w-0">
            <CardContent className="flex h-full flex-col gap-3">
              <div>
                <p className="text-sm font-medium">Evidence card</p>
                <p className="text-xs text-muted-foreground">{card.wording}</p>
              </div>
              <Link href={`/site/${card.card_id}`} className={cn(buttonVariants({ size: 'lg' }), 'mt-auto w-full no-underline')}>
                Open evidence card
                <HugeiconsIcon icon={ArrowRight01Icon} size={16} aria-hidden="true" />
              </Link>
            </CardContent>
          </Card>
        </div>

        <CurvesCard targetName={pair.targetName} earthName={pair.earthName} series={series} />

        <Accordion type="single" collapsible className="rounded-xl bg-card px-4 ring-1 ring-foreground/10 sm:px-6">
          <AccordionItem value="details" className="border-b-0">
            <AccordionTrigger>Details</AccordionTrigger>
            <AccordionContent>
              <dl className="divide-y">
                <Row k="Card">
                  {String(index + 1)} of {String(total)} · {card.card_id}
                </Row>
                <Row k="Mode · bundle">
                  Mode {card.shipping_mode} · {bundleVersion}
                </Row>
                <Row k="Windows">
                  {pair.a} ↔ {pair.b}
                </Row>
                <Row k="Relief">{familyText(d.terrain_relief)}</Row>
                <Row k="Texture">{familyText(d.terrain_texture)}</Row>
                <Row k="Radar CPR">{familyText(d.radar_cpr)}</Row>
                <Row k="Model embedding (experimental)">{familyText(d.model_embedding)}</Row>
                <Row k="Map frames">
                  {pair.target?.units ?? 'no target tiles'}; {pair.earth?.units ?? 'no Earth tiles'}
                </Row>
                <Row k="Claim limits">
                  {card.claim_limits.length === 0 ? (
                    'None listed.'
                  ) : (
                    <ul className="list-disc space-y-1 pl-4 text-muted-foreground">
                      {card.claim_limits.map((limit) => (
                        <li key={limit}>{limit}</li>
                      ))}
                    </ul>
                  )}
                </Row>
              </dl>
              <div className="mt-4 grid gap-6 lg:grid-cols-2 [&>*]:min-w-0">
                {series.relief.length > 0 ? <NuTable caption="Relief ν(L): stored values" series={series.relief} /> : null}
                {series.texture.length > 0 ? <NuTable caption="Texture ν(L): stored values" series={series.texture} /> : null}
              </div>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </div>
    </div>
  );
}
