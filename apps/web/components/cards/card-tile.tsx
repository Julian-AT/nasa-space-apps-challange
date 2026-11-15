// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// One tile of the card atlas: the image pair, the headline, the two terrain numbers from the card
// and honest Unavailable chips for radar and model. The whole tile is the link.
import type { CardRecord } from '@terraluna/schema';
import Link from 'next/link';

import { EvidenceLabel, isEvidenceLabel } from '@/components/evidence-label';

import { CardPair } from './card-pair.tsx';
import type { CardVisual } from './card-visual.ts';

const show = (v: number): string => String(Number(v.toPrecision(4)));

function Stat({ name, d }: { readonly name: string; readonly d: CardRecord['distances_by_family']['terrain_relief'] }) {
  const has = d.status === 'available' && d.value !== null;
  return (
    <div className="min-w-0 rounded-xl border border-line bg-background/40 p-3" data-family-stat={name}>
      <p className="font-mono text-[10px] tracking-[0.2em] text-muted-foreground uppercase">{name}</p>
      {has ? (
        <>
          <p className="mt-1 font-mono text-2xl leading-none">
            {show(d.value ?? 0)} <span className="text-xs text-muted-foreground">{d.unit}</span>
          </p>
          <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            {isEvidenceLabel(d.evidence_label) ? <EvidenceLabel label={d.evidence_label} /> : null}
            {d.background_percentile === null ? null : <span>percentile {show(d.background_percentile)}</span>}
          </p>
        </>
      ) : (
        <p className="mt-1 text-sm">Unavailable{d.reason === null ? '' : ` — ${d.reason}`}</p>
      )}
    </div>
  );
}

function Chip({ name, d }: { readonly name: string; readonly d: CardRecord['distances_by_family']['radar_cpr'] }) {
  const has = d.status === 'available' && d.value !== null;
  return (
    <li className="max-w-full rounded-full border border-dashed border-line-2 px-3 py-1 font-mono text-[11px] wrap-anywhere text-muted-foreground">
      {name}: {has ? `${show(d.value ?? 0)} ${d.unit}` : `Unavailable — ${d.reason ?? d.status}`}
    </li>
  );
}

export function CardTile({ card, visual, index }: { readonly card: CardRecord; readonly visual: CardVisual; readonly index: number }) {
  const d = card.distances_by_family;
  return (
    <Link
      href={`/site/${card.card_id}`}
      data-slot="card-tile"
      className="group flex h-full flex-col gap-4 rounded-3xl border border-line bg-card p-4 no-underline transition-[border-color,box-shadow] hover:border-flare/60 hover:shadow-[0_0_0_1px_color-mix(in_oklab,var(--flare)_55%,transparent),0_0_36px_-8px_color-mix(in_oklab,var(--flare)_40%,transparent)] focus-visible:border-flare motion-reduce:transition-none sm:p-5"
    >
      <CardPair visual={visual} />
      <div className="flex flex-col gap-1">
        <p className="font-mono text-[10px] tracking-[0.22em] text-muted-foreground uppercase">
          Card {String(index + 1).padStart(2, '0')} · Mode {card.shipping_mode}
          {card.mock ? ' · MOCK' : ''}
        </p>
        <h2 className="font-serif text-2xl leading-tight font-normal text-balance text-foreground">{visual.headline}</h2>
        <p className="text-sm text-muted-foreground">{card.wording}</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Stat name="Terrain relief" d={d.terrain_relief} />
        <Stat name="Terrain texture" d={d.terrain_texture} />
      </div>
      <ul className="mt-auto flex flex-wrap gap-2">
        <Chip name="Radar CPR" d={d.radar_cpr} />
        <Chip name="Model embedding (experimental)" d={d.model_embedding} />
      </ul>
    </Link>
  );
}
