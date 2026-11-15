// /compare/[a]/[b] (T7B-06): distances in separate rows, Mode B radar context, table
// alternatives, accessible chart names, and the not-found state. The records are synthetic.
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { pairsInBundle } from '../components/charts/compare-data.ts';
import { CompareView } from '../components/charts/compare-view.tsx';
import { comparisonRecord, pairRecords, radarRecord, terrainRecord, variant, windowEntries } from './explore-compare-records.ts';
import { elements, textOf } from './markup.ts';

const A = 'moon-fake-target';
const B = 'earth-fake-candidate';

function records(mode: 'A' | 'B' | 'C', radar: number | null = null) {
  const relief = variant({ '90': 1, '900': 3 }, [{ lag_m: 270, reason: 'fake_window_too_small' }]);
  const texture = variant({ '90': 0.5, '270': 0.7, '900': 0.9 });
  return pairRecords({
    comparison: comparisonRecord(A, B, mode, { relief: 1.25, texture: 2.5, radar, model: null }),
    windows: [
      windowEntries(A, terrainRecord(A, relief, texture), [radarRecord(A, 'fake-S')]),
      windowEntries(B, terrainRecord(B, relief, texture), [radarRecord(B, 'fake-L')]),
    ],
  });
}

function html(mode: 'A' | 'B' | 'C', radar: number | null = null) {
  return renderToStaticMarkup(<CompareView records={records(mode, radar)} a={A} b={B} />);
}

describe('/compare', () => {
  it('lists the pairs of the bundle for static params', () => {
    expect(pairsInBundle(records('C'))).toEqual([{ a: A, b: B }]);
  });

  it('shows an explicit not-found state for a pair absent from the bundle', () => {
    const out = renderToStaticMarkup(<CompareView records={records('C')} a={A} b="earth-fake-other" />);
    expect(textOf(out)).toContain('Pair not in this bundle');
    expect(textOf(out)).toContain('Nothing is computed on request');
  });

  it('lists terrain, radar and model distances in separate rows and never sums them', () => {
    const out = html('A', 0.75);
    const rows = elements(out).filter((e) => e.attributes.has('data-family'));
    expect(rows.map((r) => r.attributes.get('data-family'))).toEqual(['Terrain relief', 'Terrain texture', 'Radar CPR', 'Model embedding']);
    const text = textOf(out);
    expect(text).toContain('never summed');
    for (const sum of ['4.5', '1.25 + 2.5', '3.75', '2.5 + 0.75']) expect(text).not.toContain(sum);
    expect(text).toMatch(/Unavailable[^]*fake_no_model/);
  });

  it('Mode B shows radar context per band and no radar distance', () => {
    const out = html('B');
    const families = elements(out).filter((e) => e.attributes.has('data-family')).map((e) => e.attributes.get('data-family'));
    expect(families).not.toContain('Radar CPR');
    expect(elements(out).filter((e) => e.attributes.has('data-band')).map((e) => e.attributes.get('data-band'))).toEqual(['fake-S', 'fake-L']);
    expect(textOf(out)).toContain('no radar distance in Mode B');
  });

  it('Mode C says the radar comparison was not completed', () => {
    expect(textOf(html('C'))).toContain('Radar comparison not completed');
  });

  it('gives every chart an accessible name and a text table with the Unavailable reason', () => {
    const out = html('C');
    const charts = elements(out).filter((e) => e.attributes.get('data-chart') === 'nu');
    expect(charts).toHaveLength(2);
    for (const chart of charts) expect(chart.attributes.get('aria-label')).toMatch(/ν\(L\)/);
    const tables = elements(out).filter((e) => e.tag === 'table' && /stored values/.test(textOf(out)));
    expect(tables.length).toBeGreaterThanOrEqual(2);
    expect(textOf(out)).toContain('fake_window_too_small');
    expect(textOf(out)).toContain('Unavailable');
  });

  it('has exactly one h1', () => {
    expect(elements(html('C')).filter((e) => e.tag === 'h1')).toHaveLength(1);
  });
});
