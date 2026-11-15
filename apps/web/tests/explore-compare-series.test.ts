// The pure series builder behind the /compare nu(L) chart (PRD 6.3, 10).
import { describe, expect, it } from 'vitest';

import { buildNuSeries, NU_AXES, offLogAxis, plottableOnLog } from '../components/charts/series.ts';
import { variant } from './explore-compare-records.ts';

describe('buildNuSeries', () => {
  it('plots exactly the record values, element for element, sorted by lag', () => {
    const v = variant({ '900': 3, '90': 1, '270': 2 });
    const { points, gaps } = buildNuSeries(v);
    expect(points.map((p) => [p.lag_m, p.nu])).toEqual(Object.entries(v.nu_m).map(([k, nu]) => [Number(k), nu]).sort((a, b) => (a[0] ?? 0) - (b[0] ?? 0)));
    expect(gaps).toEqual([]);
  });

  it('uses log-log axes', () => {
    expect(NU_AXES).toEqual({ x: 'log', y: 'log' });
  });

  it('draws unsupported lags as gaps with the record reason, never as zero, and breaks the line', () => {
    const v = variant({ '90': 1, '900': 3 }, [{ lag_m: 270, reason: 'fake_window_too_small' }]);
    const { points, gaps } = buildNuSeries(v);
    expect(gaps).toEqual([{ lag_m: 270, reason: 'fake_window_too_small' }]);
    expect(points.some((p) => p.nu === 0 || p.lag_m === 270)).toBe(false);
    expect(points.map((p) => p.segment)).toEqual([0, 1]);
  });

  it('treats a null stored value as a gap with a named reason', () => {
    const { points, gaps } = buildNuSeries(variant({ '90': 1, '270': null }));
    expect(points).toHaveLength(1);
    expect(gaps).toEqual([{ lag_m: 270, reason: 'nu_null_in_record' }]);
  });

  it('keeps a stored zero in the series but off the log plot', () => {
    const { points } = buildNuSeries(variant({ '90': 0, '270': 2 }));
    expect(points).toHaveLength(2);
    expect(plottableOnLog(points).map((p) => p.lag_m)).toEqual([270]);
    expect(offLogAxis(points).map((p) => p.lag_m)).toEqual([90]);
  });
});
