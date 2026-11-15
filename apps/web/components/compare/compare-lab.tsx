// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The compare lab for one card pair (PRD 8.6, 10), a server component: the pair header, the map
// stage (client), the nu(L) curves of both windows in a framed panel, one stat tile per distance
// family and the card's claim limits, then the full ComparisonRecord detail. Every number shown
// is a stored bundle field; radar and model show their recorded reason, never a zero.
import type { CardRecord } from '@terraluna/schema';
import Link from 'next/link';
import type { ReactNode } from 'react';

import { CompareView, namedSeries } from '@/components/charts/compare-view';
import type { PairRecords } from '@/components/charts/compare-data';
import { findWindow } from '@/components/charts/compare-data';
import { NuChart } from '@/components/charts/nu-chart';
import type { NamedSeries } from '@/components/charts/series';
import { EvidenceLabel, isEvidenceLabel } from '@/components/evidence-label';
import { UnavailablePanel } from '@/components/model/unavailable-panel';
import { buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import { CompareStage } from './compare-stage.tsx';
import type { PairOption } from './kit.tsx';
import type { LabPair } from './lab-data.ts';

type Entry = CardRecord['distances_by_family']['terrain_relief'];

const show = (v: number): string => String(Number(v.toPrecision(4)));

function SectionHead({ n, kicker, title, text }: { readonly n: string; readonly kicker: string; readonly title: ReactNode; readonly text?: string }) {
  return (
    <div className="mb-8">
      <div className="flex flex-wrap items-center gap-4">
        <span className="font-mono text-xs font-semibold text-primary">{n}</span>
        <span aria-hidden="true" className="h-px w-8 bg-border" />
        <span className="text-xs font-medium text-muted-foreground">{kicker}</span>
      </div>
      <h2 className="mt-3 max-w-[900px] text-2xl leading-tight font-semibold tracking-tight sm:text-3xl">{title}</h2>
      {text === undefined ? null : <p className="mt-3 max-w-[680px] text-sm leading-relaxed text-muted-foreground">{text}</p>}
    </div>
  );
}

function Em({ children }: { readonly children: ReactNode }) {
  return <span className="font-normal text-muted-foreground">{children}</span>;
}

function StatTile({ name, entry, note }: { readonly name: string; readonly entry: Entry; readonly note?: string }) {
  const has = entry.status === 'available' && entry.value !== null && entry.evidence_label !== 'Unavailable';
  return (
    <div className="min-w-0 rounded-xl border bg-card p-5 shadow-xs" data-family-stat={name}>
      <p className="text-xs font-medium text-muted-foreground">{name}</p>
      {has ? (
        <>
          <p className="mt-3 font-mono text-3xl leading-none text-foreground tabular-nums" title={String(entry.value)}>
            {show(entry.value ?? Number.NaN)}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">{entry.unit}</p>
          <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            {isEvidenceLabel(entry.evidence_label) ? <EvidenceLabel label={entry.evidence_label} /> : null}
            {entry.background_percentile === null ? (
              <span>percentile unavailable — {entry.background_percentile_reason ?? 'no reason recorded'}</span>
            ) : (
              <span title={String(entry.background_percentile)}>background percentile {show(entry.background_percentile)}</span>
            )}
          </div>
        </>
      ) : (
        <>
          <p className="mt-3 text-lg leading-snug text-foreground">Unavailable</p>
          <p className="mt-1 text-sm wrap-anywhere text-muted-foreground">— {entry.reason ?? entry.status}</p>
          <div className="mt-3 text-xs">
            <EvidenceLabel label="Unavailable" />
          </div>
        </>
      )}
      {note === undefined ? null : <p className="mt-3 text-xs text-muted-foreground">{note}</p>}
    </div>
  );
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
  return (
    <div className="pb-24 font-sans">
      {card.mock ? (
        <div className="bg-mock">
          <p role="status" className="mx-auto max-w-[1400px] px-4 py-1.5 text-xs font-bold text-mock-foreground sm:px-6">
            MOCK card preview: not a scientific result and not part of a production bundle.
          </p>
        </div>
      ) : null}
      <header className="mx-auto max-w-[1400px] px-4 pt-8 pb-6 sm:px-6 sm:pt-12">
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs font-medium text-muted-foreground">
          <span className="text-primary">Compare</span>
          <span>
            Card {String(index + 1).padStart(2, '0')} of {String(total).padStart(2, '0')}
          </span>
          <span>Mode {card.shipping_mode}</span>
          <span className="font-mono wrap-anywhere">Bundle {bundleVersion}</span>
        </p>
        <h1 className="mt-4 text-3xl leading-tight font-semibold tracking-tight text-balance sm:text-5xl">
          <span className="inline-flex items-center gap-3">
            <span aria-hidden="true" className="size-3 rounded-full" style={{ background: `var(--${pair.body})` }} />
            {pair.targetName}
          </span>{' '}
          <Em>↔</Em>{' '}
          <span className="inline-flex items-center gap-3">
            <span aria-hidden="true" className="size-3 rounded-full" style={{ background: 'var(--earth)' }} />
            {pair.earthName}
          </span>
        </h1>
        <p className="mt-4 max-w-3xl text-sm leading-relaxed text-muted-foreground">{card.wording}</p>
        <p className="mt-2 font-mono text-[11px] wrap-anywhere text-muted-foreground">
          {pair.a} · {pair.b}
        </p>
      </header>

      <CompareStage pair={pair} current={current} options={options} />

      <div className="mx-auto max-w-[1400px] space-y-16 px-4 pt-16 sm:px-6">
        <p className="max-w-3xl text-sm text-muted-foreground">
          3D terrain twins are not shown: this release bundle ships hillshade images, not height arrays, for these windows, and a 3D
          surface would need heights the bundle does not hold.
        </p>

        <section id="curves" aria-labelledby="curves-title" className="scroll-mt-32">
          <SectionHead
            n="01"
            kicker="Structure function"
            title={
              <span id="curves-title">
                Roughness across scales, <Em>ν(L) for both windows.</Em>
              </span>
            }
            text="Log-log axes. Solid blue with circles is the target; dashed orange with squares is the Earth window. Dotted vertical lines mark lags without a value; the reason is in the table."
          />
          <div className="grid gap-6 rounded-xl border bg-card p-4 shadow-xs sm:p-6 lg:grid-cols-2 [&>*]:min-w-0">
            {(['relief', 'texture'] as const).map((observable) => {
              const series = [namedSeries('Target', wa, pair.a, observable), namedSeries('Earth candidate', wb, pair.b, observable)].filter(
                (s): s is NamedSeries => s !== null,
              );
              return series.length === 0 ? (
                <UnavailablePanel key={observable} title={`${observable} ν(L) curves`}>
                  <p>Neither window has a terrain fingerprint in the bundle (windows/&lt;body&gt;.json).</p>
                </UnavailablePanel>
              ) : (
                <NuChart key={observable} name={`${observable === 'relief' ? 'Relief' : 'Texture'} ν(L), ${pair.targetName} and ${pair.earthName}`} series={series} />
              );
            })}
          </div>
        </section>

        <section id="distances" aria-labelledby="distances-title" className="scroll-mt-32">
          <SectionHead
            n="02"
            kicker="Distances by family"
            title={
              <span id="distances-title">
                Four families, <Em>never summed.</Em>
              </span>
            }
            text="Each family keeps its own unit and evidence label. Terrain distances come from the card; radar and model show the reason they are unavailable."
          />
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile name="Terrain relief" entry={d.terrain_relief} />
            <StatTile name="Terrain texture" entry={d.terrain_texture} />
            <StatTile name="Radar CPR" entry={d.radar_cpr} />
            <StatTile name="Model embedding" entry={d.model_embedding} note={`Experimental, outside every ranking (G5 ${card.g5_status}).`} />
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <Link href={`/site/${card.card_id}`} className={cn(buttonVariants({ size: 'lg' }), 'no-underline')}>
              Open the evidence card
            </Link>
            <span className="font-mono text-[11px] wrap-anywhere text-muted-foreground">{card.card_id}</span>
          </div>
          <div className="mt-8 rounded-xl border border-primary/40 bg-primary/5 p-5">
            <h3 className="text-sm font-semibold">Claim limits</h3>
            {card.claim_limits.length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">The card lists no claim limits.</p>
            ) : (
              <ul className="mt-3 list-disc space-y-1.5 pl-5 text-[13.5px] leading-relaxed text-muted-foreground">
                {card.claim_limits.map((limit) => (
                  <li key={limit}>{limit}</li>
                ))}
              </ul>
            )}
          </div>
        </section>

        <section id="record" aria-labelledby="record-title" className="scroll-mt-32">
          <SectionHead n="03" kicker="Full record" title={<span id="record-title">The comparison record, <Em>field by field.</Em></span>} />
          <CompareView records={records} a={pair.a} b={pair.b} detailOnly />
        </section>
      </div>
    </div>
  );
}
