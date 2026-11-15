// The text of a card's Open Graph image (PRD 10, D08), a pure function of the card and the
// manifest so a screenshot of the image carries the target, mode, bundle version and any evidence
// label that goes with a printed distance. No number is computed here.
import type { CardRecord } from '@terraluna/schema';

import { MODE_TITLES } from '../mode-banner.tsx';

export const FAMILY_ORDER = ['terrain_relief', 'terrain_texture', 'radar_cpr', 'model_embedding'] as const;

export interface OgText {
  readonly mock: boolean;
  readonly lines: readonly string[];
}

export function ogText(card: CardRecord, manifest: { readonly bundle_version: string }): OgText {
  const lines: string[] = [];
  if (card.mock) lines.push('MOCK PREVIEW - not production evidence');
  lines.push(card.target.target_id);
  lines.push(`Mode ${card.shipping_mode}: ${MODE_TITLES[card.shipping_mode]}`);
  for (const family of FAMILY_ORDER) {
    const entry = card.distances_by_family[family];
    if (entry.status !== 'available' || entry.value === null) continue;
    const experimental = family === 'model_embedding' ? ' EXPERIMENTAL' : '';
    lines.push(`${family}: ${String(entry.value)} ${entry.unit} [${entry.evidence_label}]${experimental}`);
    break;
  }
  lines.push(card.wording);
  lines.push(`Bundle ${manifest.bundle_version}`);
  return { mock: card.mock, lines };
}
