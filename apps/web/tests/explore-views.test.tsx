// Rendered explorer views (PRD 10): Unavailable panels, banner and bundle version, Mars radar note,
// Earth table family separation. deck.gl is stubbed; it is not needed for server-rendered panels.
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import { CandidateTable } from '../components/map/earth-table.tsx';
import { buildBodyData, buildEarthData } from '../components/map/explore-data.ts';
import { ExplorerMap } from '../components/map/explorer-map.tsx';
import { testBundle, testComparison, testDistance, testLayer, testTarget, testWindow } from './explore-fixtures.ts';

vi.mock('../components/map/deck-loader.tsx', () => ({ DeckCanvas: () => <div data-testid="deck" /> }));
vi.mock('../components/map/earth-loader.tsx', () => ({ EarthCanvas: () => <div data-testid="deck" /> }));
vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));

describe('ExplorerMap', () => {
  it('shows an Unavailable panel naming targets.json when it is missing', () => {
    const html = renderToStaticMarkup(
      <ExplorerMap data={buildBodyData(testBundle(), 'moon')} body="moon" assetBase="https://a.test" />,
    );
    expect(html).toContain('Unavailable');
    expect(html).toContain('targets.json');
    expect(html).not.toContain('data-testid="deck"');
  });
  it('shows Unavailable naming tile_layers when the target has none, and the permanent-shadow reason', () => {
    const data = buildBodyData(testBundle({ targets: testTarget({ tile_layers: [] }) }), 'moon');
    const html = renderToStaticMarkup(<ExplorerMap data={data} body="moon" assetBase="https://a.test" />);
    expect(html).toContain('TargetRecord.tile_layers');
    expect(html).toContain('G0-10');
    expect(html).toContain('windows/moon.json');
    expect(html).not.toContain('data-testid="deck"');
  });
  it('shows the layer legend, evidence label from the record, window count 0 and reasons for the rest', () => {
    const cpr = testLayer({ layer: 'test-minirf-cpr', evidence_label: 'Observed', units: null });
    const data = buildBodyData(
      testBundle({
        targets: testTarget({ tile_layers: [testLayer(), cpr] }),
        windows: new Map([['moon', [testWindow('moon-test-window-a', 'rejected', ['too_steep'])]]]),
      }),
      'moon',
    );
    const html = renderToStaticMarkup(<ExplorerMap data={data} body="moon" assetBase="https://a.test" />);
    expect(html).toContain('data-testid="deck"');
    expect(html).toContain('data-scale="viridis"');
    expect(html).toContain('data-label="Observed"');
    expect(html).toContain('too_steep');
    expect(html).toContain('Bundle test-bundle');
    expect(html).toContain('aria-label="Map controls"');
    expect(html).toContain('Pan north');
  });
  it('reports a count of 0 for a target with no windows', () => {
    const data = buildBodyData(testBundle({ targets: testTarget(), windows: new Map([['moon', []]]) }), 'moon');
    const html = renderToStaticMarkup(<ExplorerMap data={data} body="moon" assetBase="https://a.test" />);
    expect(html).toMatch(/data-slot="window-count"[^>]*>0</);
  });
  it('Mars has only an Unavailable radar note', () => {
    const data = buildBodyData(
      testBundle({ targets: testTarget({ target_id: 'mars-test', body: 'mars', primary_window_id: null, shortlist_reason: 'x' }) }),
      'mars',
    );
    const html = renderToStaticMarkup(<ExplorerMap data={data} body="mars" assetBase={null} />);
    expect(html).toContain('Radar on Mars');
    expect(html).toContain('NEXT_PUBLIC_ASSET_BASE');
  });
});

describe('Earth candidate table', () => {
  let modelValues = false;
  const records = () => [
    testComparison('earth-test-a', {
      terrain_relief: testDistance({ value: 2, tied: true, tie_group: 'g1', background_percentile: 40 }),
      terrain_texture: testDistance({ value: 0.5 }),
    }),
    testComparison('earth-test-b', { terrain_relief: testDistance({ value: 1 }), ...(modelValues ? { model_embedding: testDistance({ value: 0.3, evidence_label: 'Predicted' }) } : {}) }),
  ];
  const bundle = (g5: string, withModel = false) =>
    buildEarthData(
      testBundle(
        {
          targets: testTarget(),
          comparisons: new Map([['moon-test-target', records()]]),
          windows: new Map([['earth', [testWindow('earth-test-a', 'eligible', [])]]]),
          modelCard: withModel ? {} : null,
        },
        g5,
      ),
    );
  const render = (family: 'terrain_relief' | 'radar_cpr' | 'model_embedding', g5 = 'NOT_RUN', withModel = false) => {
    const d = bundle(g5, withModel);
    const t = d.targets[0];
    if (t === undefined) throw new Error('no target');
    return renderToStaticMarkup(
      <CandidateTable targetName={t.name} candidates={t.candidates} family={family} g5={d.g5} modelInBundle={d.modelInBundle} />,
    );
  };
  it('keeps families in separate columns, in comparisons-file order, shows ties and percentiles as serialized', () => {
    const html = render('terrain_relief');
    expect(html.indexOf('earth-test-a')).toBeLessThan(html.indexOf('earth-test-b'));
    expect(html).toContain('data-family="terrain_texture"');
    expect(html).toContain('Tied (group g1)');
    expect(html).toContain('>40<');
    expect(html).toContain('No combined score');
    expect(html).toContain('/compare/moon-test-window-a/earth-test-a');
  });
  it('shows unknown radar as Unavailable, never zero', () => {
    const html = render('radar_cpr');
    expect(html).toContain('radar_not_observed');
    expect(html).not.toMatch(/data-family="radar_cpr" data-status="available"/);
  });
  it('has no model column with no model, and an unranked Predicted one while G5 is not PASS', () => {
    expect(render('terrain_relief')).not.toContain('data-family="model_embedding"');
    modelValues = true;
    const html = render('model_embedding', 'NOT_RUN', true);
    modelValues = false;
    expect(html).toContain('unranked');
    expect(html).toContain('data-label="Predicted"');
    expect(html.indexOf('earth-test-a')).toBeLessThan(html.indexOf('earth-test-b'));
  });
  it('shows the actual candidate count', () => {
    expect(render('radar_cpr')).toMatch(/data-slot="candidate-count"[^>]*><span[^>]*>2</);
  });
});

describe('Earth result cards', () => {
  it('number cards, show radar as not completed in Mode C, and render no thumbnail without a bundle figure', async () => {
    const { EarthCards } = await import('../components/map/earth-cards.tsx');
    const d = buildEarthData(
      testBundle({
        targets: testTarget(),
        comparisons: new Map([['moon-test-target', [testComparison('earth-test-a', { terrain_relief: testDistance() })]]]),
      }),
    );
    const html = renderToStaticMarkup(
      <EarthCards candidates={d.targets[0]?.candidates ?? []} selected={null} onSelect={() => undefined} modelInBundle={false} />,
    );
    expect(html).toContain('not completed (Mode C)');
    expect(html).not.toContain('<img');
    expect(html).not.toContain('data-family="model_embedding"');
    expect(html).toContain('>1<');
  });
});
