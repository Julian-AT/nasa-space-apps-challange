// The evidence card page body (PRD 10, D08) and its OG text, rendered from synthetic CardRecords.
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { EvidenceCardView } from '../components/card/evidence-card.tsx';
import { ogText } from '../components/card/og-text.ts';
import { MODE_TITLES } from '../components/mode-banner.tsx';
import { SYNTH_SVG, synthCard } from './site-fixtures.ts';
import { textOf } from './markup.ts';

const FIGS = new Map([['figures/synth-a.svg', SYNTH_SVG]]);

function render(card = synthCard(), g5 = 'NOT_RUN', figures = FIGS): string {
  return renderToStaticMarkup(
    <EvidenceCardView card={card} bundleVersion="synth-9.9.9" g5Gate={g5 as 'NOT_RUN'} figures={figures} />,
  );
}

describe('evidence card page', () => {
  it('shows the mode, its title wording, the bundle version and the claim wording', () => {
    const text = textOf(render());
    expect(text).toContain('Mode C');
    expect(text).toContain(MODE_TITLES.C);
    expect(text).toContain('synth-9.9.9');
    expect(text).toContain('candidate window identified by this search');
    expect(text).toContain('SYNTH claim limit');
  });

  it('prints serialized numbers exactly', () => {
    const card = synthCard();
    const text = textOf(render());
    const value = card.distances_by_family.terrain_relief.value;
    expect(value).toBe(987.125);
    expect(text).toContain(String(value));
    expect(text).toContain(String(card.q3_measurements[0]?.value));
  });

  it('refuses a table D row without an evidence label', () => {
    const card = synthCard();
    const row = { ...card.q3_measurements[0], evidence_label: undefined };
    const bad = { ...card, q3_measurements: [row] } as unknown as typeof card;
    expect(() => render(bad)).toThrow(/evidence label/);
  });

  it('never renders a radar match claim in Mode B', () => {
    const text = textOf(render(synthCard({ mode: 'B' }))).toLowerCase();
    expect(text).not.toContain('radar match: passed');
    expect(text).not.toContain('radar match');
  });

  it('shows the Unavailable reason for radar in Mode C', () => {
    const html = render(synthCard({ mode: 'C' }));
    const text = textOf(html);
    expect(text).toContain('radar_cpr');
    expect(text).toContain('synth_not_run');
    expect(text).toContain('Unavailable');
  });

  it('renders unknown campaign entries as unknown', () => {
    const text = textOf(render());
    expect((text.match(/unknown/g) ?? []).length).toBeGreaterThanOrEqual(4);
  });

  it('keeps distance families in separate rows and never sums them', () => {
    const html = render(synthCard({ available: ['terrain_relief', 'terrain_texture'] }));
    for (const f of ['terrain_relief', 'terrain_texture', 'radar_cpr', 'model_embedding']) {
      expect(html).toContain(`data-family="${f}"`);
    }
    expect(html).not.toMatch(/total|sum of|combined/i);
  });

  it('gives the encoder the Predicted label, EXPERIMENTAL and no rank while G5 is not PASS', () => {
    const card = synthCard({ available: ['model_embedding'], g5: 'NOT_RUN' });
    const html = render(card);
    const row = /<tr[^>]*data-family="model_embedding"[\s\S]*?<\/tr>/.exec(html)?.[0] ?? '';
    expect(row).toContain('Predicted');
    expect(row).toContain('EXPERIMENTAL');
    expect(textOf(row).toLowerCase()).not.toMatch(/rank \d|#\d|rank:/);
    expect(html).toContain('data-rank="none"');
  });

  it('marks the field experiment as a proposal', () => {
    const text = textOf(render());
    expect(text).toContain('Proposed');
    expect(text).toContain('SYNTH field proposal');
  });

  it('shows the mock banner on a mock card', () => {
    expect(textOf(render(synthCard({ mock: true })))).toContain('MOCK');
    expect(textOf(render())).not.toContain('MOCK');
  });

  it('embeds a present figure and names the file of a missing one', () => {
    const html = render();
    expect(html).toContain('data:image/svg+xml');
    expect(html).toContain('alt="SYNTH alt A"');
    expect(textOf(html)).toContain('synth_not_rendered');
    const missing = textOf(render(synthCard(), 'NOT_RUN', new Map()));
    expect(missing).toContain('figures/synth-a.svg');
  });
});

describe('ogText', () => {
  it('is built from the card and manifest only', () => {
    const card = synthCard();
    const a = ogText(card, { bundle_version: 'synth-9.9.9' });
    expect(a).toEqual(ogText(card, { bundle_version: 'synth-9.9.9' }));
    expect(a.lines.join(' ')).toContain('moon-synth-a');
    expect(a.lines.join(' ')).toContain('Mode C');
    expect(a.lines.join(' ')).toContain(MODE_TITLES.C);
    expect(a.lines.join(' ')).toContain('synth-9.9.9');
    expect(a.mock).toBe(false);
  });

  it('flags mock cards and labels any printed distance', () => {
    const card = synthCard({ mock: true });
    const t = ogText(card, { bundle_version: 'v' });
    expect(t.mock).toBe(true);
    expect(t.lines.join(' ')).toContain('MOCK');
    expect(t.lines.join(' ')).toContain('Derived');
    expect(t.lines.join(' ')).toContain('987.125');
  });
});
