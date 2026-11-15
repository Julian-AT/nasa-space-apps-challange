// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Numbered candidate cards for the Earth results panel (PRD 8.6, 10, D07). Each card shows the
// separate per-family distances with unit and evidence label; there is no single match score and
// no rank number other than the position in the comparisons file. The encoder distance appears only
// as "Predicted · EXPERIMENTAL" and is never ranked unless G5 is PASS. A bundle figure is shown as
// a thumbnail only when the bundle holds one for the window; no placeholder image exists.
import Link from 'next/link';

import { EvidenceLabel } from '@/components/evidence-label';
import { LabelledValue } from '@/components/labelled-value';
import { cn } from '@/lib/utils';

import type { Candidate, Family } from './explore-data.ts';
import { Chip } from './results-panel.tsx';

const CARD_FAMILIES: readonly Family[] = ['terrain_relief', 'terrain_texture', 'radar_cpr', 'model_embedding'];
const SHORT: Readonly<Record<Family, string>> = {
  terrain_relief: 'Terrain relief',
  terrain_texture: 'Terrain texture',
  radar_cpr: 'Radar CPR',
  model_embedding: 'Encoder embedding',
};

function FamilyLine({ family, candidate, modelInBundle }: { readonly family: Family; readonly candidate: Candidate; readonly modelInBundle: boolean }) {
  const entry = candidate.distances[family];
  if (family === 'model_embedding' && !modelInBundle && entry.status !== 'available') return null;
  let body;
  if (entry.status === 'available' && entry.value !== null) {
    const label = family === 'model_embedding' ? 'Predicted' : entry.evidence_label === 'Unavailable' ? 'Derived' : entry.evidence_label;
    body = (
      <>
        <LabelledValue label={label} value={entry.value} unit={entry.unit} source={SHORT[family]} />
        {entry.background_percentile === null ? null : (
          <span className="text-xs text-muted-foreground">background percentile {Number(entry.background_percentile.toFixed(1))}</span>
        )}
        {entry.tied === true ? (
          <Chip className="font-semibold">Tied{entry.tie_group === null ? '' : ` (${entry.tie_group})`}</Chip>
        ) : null}
        {family === 'model_embedding' ? <Chip className="font-semibold">Predicted · EXPERIMENTAL · unranked</Chip> : null}
      </>
    );
  } else {
    const modeC = family === 'radar_cpr' && candidate.shippingMode === 'C';
    body = (
      <LabelledValue
        label="Unavailable"
        reason={`${modeC ? 'not completed (Mode C); ' : ''}${entry.reason ?? entry.status}`}
      />
    );
  }
  return (
    <div className="flex flex-col gap-0.5 text-sm" data-family={family}>
      <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{SHORT[family]}</span>
      <div className="flex flex-wrap items-center gap-2">{body}</div>
    </div>
  );
}

export interface EarthCardsProps {
  readonly candidates: readonly Candidate[];
  readonly selected: number | null;
  readonly onSelect: (index: number) => void;
  readonly modelInBundle: boolean;
  /** The family chosen on the switch: its value leads each row. */
  readonly family?: Family;
}

export function EarthCards({ candidates, selected, onSelect, modelInBundle, family = 'terrain_relief' }: EarthCardsProps) {
  return (
    <ol className="flex flex-col gap-1.5" aria-label="Candidate windows, in comparisons file order">
      {candidates.map((c, index) => (
        <li
          key={`${c.targetWindowId}|${c.earthWindowId}`}
          data-selected={selected === index}
          className={cn(
            'flex flex-col gap-2 rounded-xl border bg-ink px-3 py-2.5',
            selected === index ? 'border-flare/70' : 'border-line',
          )}
        >
          <div className="flex items-center gap-2.5">
            <span
              aria-hidden="true"
              className="flex size-6 shrink-0 items-center justify-center rounded-full bg-earth font-mono text-[11px] font-bold text-void"
            >
              {index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <button
                type="button"
                onClick={() => {
                  onSelect(index);
                }}
                className="block w-full text-left text-[13px] font-medium break-all text-platinum underline-offset-2 hover:underline"
                aria-label={`Candidate ${String(index + 1)}: show ${c.earthWindowId} on the map`}
              >
                {c.earthWindowId}
              </button>
              <p className="text-[11px] text-dim">
                {c.regionId === null ? 'no region' : c.regionId} {'\u00b7'} {c.windowStatus === null ? 'no window record' : `window ${c.windowStatus}`}
                {c.centroidLonLat === null ? ' \u00b7 not on map: no footprint in windows/earth.json' : ''}
              </p>
            </div>
            {c.figureDataUrl === null ? null : (
              // A bundle figure, not a placeholder; a data URL so no extra request is made.
              <img src={c.figureDataUrl} alt={`Figure ${c.figurePath ?? ''} for ${c.earthWindowId}`} className="h-10 w-14 shrink-0 rounded-md border border-line-2 object-cover" />
            )}
          </div>
          <FamilyLine family={family} candidate={c} modelInBundle={modelInBundle} />
          <details className="text-sm">
            <summary className="cursor-pointer text-[11px] text-dim">All distance families</summary>
            <div className="mt-2 flex flex-col gap-2">
              {CARD_FAMILIES.map((f) => (
                <FamilyLine key={f} family={f} candidate={c} modelInBundle={modelInBundle} />
              ))}
              {c.exclusions.length === 0 ? null : (
                <p className="text-xs text-muted-foreground">
                  Excluded: {c.exclusions.map((x) => `${x.observable} (${x.reasons.join(', ')})`).join('; ')}
                </p>
              )}
            </div>
          </details>
          <div className="flex flex-wrap gap-2 text-[12px]">
            {c.cardId === null ? null : (
              <Link className="rounded-full border border-flare/50 px-2.5 py-0.5 text-flare hover:bg-flare/10" href={`/site/${c.cardId}`}>
                Evidence card
              </Link>
            )}
            <Link className="rounded-full border border-line-2 px-2.5 py-0.5 text-chrome hover:text-platinum" href={`/compare/${c.targetWindowId}/${c.earthWindowId}`}>
              Compare curves
            </Link>
          </div>
        </li>
      ))}
      <li className="list-none pt-1">
        <EvidenceLabel label="Derived" /> <span className="text-xs text-muted-foreground">Numbers are as serialized in the bundle.</span>
      </li>
    </ol>
  );
}
