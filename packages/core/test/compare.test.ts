import { describe, expect, it } from 'vitest';

import { DISTANCE_ATOL_M, H_ATOL, LOG_NU_ATOL, RESAMPLED_GRID_RTOL, closeAbs, closeRel, reasonsEqual } from './compare.ts';
import { tolerancesOf } from './fixtures.ts';

describe('parity assertions', () => {
  it('closeRel fails just above and passes just below the tolerance', () => {
    expect(() => {
      closeRel(1000 * (1 + 0.9e-9), 1000, 1e-9);
    }).not.toThrow();
    expect(() => {
      closeRel(1000 * (1 + 1.1e-9), 1000, 1e-9);
    }).toThrow(/rtol/);
  });
  it('closeAbs fails just above and passes just below the tolerance', () => {
    expect(() => {
      closeAbs(2 + 0.9e-9, 2, 1e-9);
    }).not.toThrow();
    expect(() => {
      closeAbs(2 + 1.1e-9, 2, 1e-9);
    }).toThrow(/atol/);
  });
  it('treats missing and zero as distinct states', () => {
    expect(() => {
      closeAbs(0, null, 1e-9);
    }).toThrow(/missing/);
    expect(() => {
      closeAbs(Number.NaN, 0, 1e-9);
    }).toThrow(/missing/);
    expect(() => {
      closeRel(Number.NaN, null, 1e-9);
    }).not.toThrow();
  });
  it('reasonsEqual compares exact strings', () => {
    expect(() => {
      reasonsEqual('nu_zero', 'nu_zero');
    }).not.toThrow();
    expect(() => {
      reasonsEqual('nu_zero ', 'nu_zero');
    }).toThrow();
    expect(() => {
      reasonsEqual(null, 'nu_zero');
    }).toThrow();
  });
  it('exports the PRD 8.8 tolerances, equal to those stored in the fixtures', () => {
    expect([RESAMPLED_GRID_RTOL, LOG_NU_ATOL, H_ATOL, DISTANCE_ATOL_M]).toEqual([1e-9, 1e-9, 1e-9, 1e-9]);
    for (const file of ['terrain_synthetic.json', 'geometry_synthetic.json']) {
      expect(tolerancesOf(file)).toEqual({
        resampledGridRtol: RESAMPLED_GRID_RTOL,
        logNuAtol: LOG_NU_ATOL,
        hAtol: H_ATOL,
        distanceAtol: DISTANCE_ATOL_M,
      });
    }
  });
});
