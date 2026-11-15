// The Earth candidate table (PRD 8.6, 8.7, 10): one row per ComparisonRecord, one column per
// distance family. Terrain relief, terrain texture, radar CPR and the encoder's embedding distance
// stay in separate columns and are never summed, averaged or merged into one rank (PRD D07). Rows
// are ordered by the single family chosen on the switch, by that family's own serialized value;
// no rank number is computed here. background_percentile, tied and tie_group are shown exactly as
// the DistanceEntry holds them. Missing evidence is shown as Unavailable with its reason, never 0.
import Link from 'next/link';

import { EvidenceLabel } from '@/components/evidence-label';
import { LabelledValue } from '@/components/labelled-value';
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

import type { Candidate, DistanceEntry, Family } from './explore-data.ts';

export type FamilyChoice = Family;

export const FAMILY_TITLES: Readonly<Record<Family, string>> = {
  terrain_relief: 'Terrain relief',
  terrain_texture: 'Terrain texture',
  radar_cpr: 'Radar CPR',
  model_embedding: 'Model embedding',
};

/** True when the model family may be ranked: only after G5 passes (PRD D25). */
export function modelIsRanked(g5: string | null): boolean {
  return g5 === 'PASS';
}

/** The families that get a column: the three measured ones, and the model only when the bundle holds one. */
export function visibleFamilies(modelInBundle: boolean, candidates: readonly Candidate[]): Family[] {
  const model = modelInBundle || candidates.some((c) => c.distances.model_embedding.status === 'available');
  return model
    ? ['terrain_relief', 'terrain_texture', 'radar_cpr', 'model_embedding']
    : ['terrain_relief', 'terrain_texture', 'radar_cpr'];
}

/**
 * Rows ordered by one family's own serialized value, smallest first, with unavailable entries
 * after the available ones in their serialized order. An unranked family keeps the serialized order.
 */
export function orderBy(candidates: readonly Candidate[], family: Family, g5: string | null): Candidate[] {
  if (family === 'model_embedding' && !modelIsRanked(g5)) return [...candidates];
  const available = candidates.filter((c) => c.distances[family].status === 'available' && c.distances[family].value !== null);
  const rest = candidates.filter((c) => !available.includes(c));
  available.sort((a, b) => (a.distances[family].value as number) - (b.distances[family].value as number));
  return [...available, ...rest];
}

function percentile(entry: DistanceEntry) {
  if (entry.background_percentile === null) {
    return (
      <LabelledValue
        label="Unavailable"
        reason={`background percentile: ${entry.background_percentile_reason ?? 'no reason recorded'}`}
      />
    );
  }
  const label = entry.evidence_label === 'Unavailable' ? 'Derived' : entry.evidence_label;
  return (
    <span>
      background percentile{' '}
      <LabelledValue label={label} value={entry.background_percentile} unit={null} source="ComparisonRecord" />
    </span>
  );
}

export function DistanceCell({ family, entry, ranked }: { readonly family: Family; readonly entry: DistanceEntry; readonly ranked: boolean }) {
  if (entry.status !== 'available' || entry.value === null) {
    return (
      <div className="flex flex-col gap-1" data-family={family} data-status={entry.status}>
        <LabelledValue
          label="Unavailable"
          reason={`${entry.status === 'not_comparable' ? 'not comparable' : 'unavailable'}: ${entry.reason ?? 'no reason recorded'}`}
        />
      </div>
    );
  }
  const label = family === 'model_embedding' ? 'Predicted' : entry.evidence_label;
  return (
    <div className="flex flex-col gap-1 text-xs" data-family={family} data-status="available">
      <LabelledValue
        label={label === 'Unavailable' ? 'Derived' : label}
        value={entry.value}
        unit={entry.unit}
        source={FAMILY_TITLES[family]}
      />
      {percentile(entry)}
      {entry.tied === true ? (
        <span data-slot="tied" className="font-semibold">
          Tied{entry.tie_group === null ? '' : ` (group ${entry.tie_group})`}
        </span>
      ) : null}
      {entry.tied === null ? (
        <span data-slot="tie-unknown" className="text-muted-foreground">
          Tie status unavailable{entry.tie_reason === null ? '' : `: ${entry.tie_reason}`}
        </span>
      ) : null}
      {family === 'model_embedding' && !ranked ? (
        <span data-slot="unranked" className="font-semibold">
          Predicted; unranked (G5 is not PASS)
        </span>
      ) : null}
    </div>
  );
}

export interface CandidateTableProps {
  readonly targetName: string;
  readonly candidates: readonly Candidate[];
  readonly family: Family;
  readonly g5: string | null;
  readonly modelInBundle: boolean;
}

export function CandidateTable({ targetName, candidates, family, g5, modelInBundle }: CandidateTableProps) {
  const families = visibleFamilies(modelInBundle, candidates);
  const ranked = modelIsRanked(g5);
  const ordered = orderBy(candidates, family, g5);
  const unrankedFamily = family === 'model_embedding' && !ranked;
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm" data-slot="candidate-count">
        <span className="font-semibold">{String(candidates.length)}</span> comparison records for {targetName}. Rows are ordered
        {unrankedFamily
          ? ' as serialized, because the model family is unranked while G5 is not PASS.'
          : ` by ${FAMILY_TITLES[family]} distance only; other families are shown, not combined.`}{' '}
        <EvidenceLabel label="Derived" />
      </p>
      {candidates.length === 0 ? null : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table>
            <TableCaption>
              Candidate Earth windows for {targetName}, one column per distance family. No combined score exists.
            </TableCaption>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Earth window</TableHead>
                {families.map((f) => (
                  <TableHead key={f} scope="col" data-family={f}>
                    {FAMILY_TITLES[f]}
                    {f === 'model_embedding' ? (ranked ? '' : ' (Predicted, unranked)') : ''}
                  </TableHead>
                ))}
                <TableHead scope="col">Excluded observables</TableHead>
                <TableHead scope="col">Links</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ordered.map((c) => (
                <TableRow key={`${c.targetWindowId}|${c.earthWindowId}`}>
                  <TableCell className="align-top">
                    <code className="text-xs">{c.earthWindowId}</code>
                    {c.windowStatus === null ? null : (
                      <div className="text-xs text-muted-foreground">
                        window {c.windowStatus}
                        {c.windowReasons.length === 0 ? '' : `: ${c.windowReasons.join(', ')}`}
                      </div>
                    )}
                    {c.regionId === null ? null : <div className="text-xs text-muted-foreground">region {c.regionId}</div>}
                  </TableCell>
                  {families.map((f) => (
                    <TableCell key={f} className="align-top">
                      <DistanceCell family={f} entry={c.distances[f]} ranked={ranked} />
                    </TableCell>
                  ))}
                  <TableCell className="align-top text-xs">
                    {c.exclusions.length === 0
                      ? 'none'
                      : c.exclusions.map((x) => (
                          <div key={x.observable}>
                            <code>{x.observable}</code>: {x.reasons.join(', ')}
                          </div>
                        ))}
                  </TableCell>
                  <TableCell className="align-top text-sm">
                    <div className="flex flex-col gap-1">
                      {c.cardId === null ? (
                        <span className="text-xs text-muted-foreground">no evidence card</span>
                      ) : (
                        <Link className="underline" href={`/site/${c.cardId}`}>
                          Evidence card
                        </Link>
                      )}
                      <Link className="underline" href={`/compare/${c.targetWindowId}/${c.earthWindowId}`}>
                        Compare curves
                      </Link>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
