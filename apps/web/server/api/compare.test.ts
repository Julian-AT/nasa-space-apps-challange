// GET /api/v1/compare (PRD 8.5, T15B-05): stored pairs are returned unchanged; nothing is computed
// on request until @terraluna/core has the terrain distance.
import { describe, expect, it } from 'vitest';

import {
  comparison,
  EARTH_ID,
  memoryReader,
  TARGET_ID,
  TARGET_WINDOW_ID,
  windowEntry,
} from './test-records.ts';
import { expectProblem, quietApi } from './test-support.ts';

const stored = comparison({ earth: EARTH_ID(1), relief: 0.25, texture: 0.5 });
const reader = memoryReader({
  windows: {
    moon: [windowEntry(TARGET_WINDOW_ID)],
    earth: [windowEntry(EARTH_ID(1)), windowEntry(EARTH_ID(2))],
  },
  comparisons: { [TARGET_ID]: [stored] },
});
const get = (a: string, b: string) => quietApi(reader).api.request(`/api/v1/compare?a=${a}&b=${b}`);

describe('GET /api/v1/compare', () => {
  it('returns a stored pair unchanged, in either order', async () => {
    for (const [a, b] of [
      [TARGET_WINDOW_ID, EARTH_ID(1)],
      [EARTH_ID(1), TARGET_WINDOW_ID],
    ] as const) {
      const response = await get(a, b);
      expect(response.status).toBe(200);
      const body = (await response.json()) as { source: string; comparison: unknown };
      expect(body.source).toBe('stored');
      expect(body.comparison).toEqual(JSON.parse(JSON.stringify(stored)));
    }
  });

  it('is a 501 problem, computing nothing, for stored windows without a stored pair', async () => {
    const problem = await expectProblem(await get(TARGET_WINDOW_ID, EARTH_ID(2)), 501, 'Not Implemented');
    expect(problem.detail).toContain('not computed on request');
  });

  it('is a 404 problem for an unknown window and a 400 problem for a malformed id', async () => {
    await expectProblem(await get(TARGET_WINDOW_ID, EARTH_ID(9)), 404, 'Not Found');
    await expectProblem(await get('nope', EARTH_ID(1)), 400, 'Bad Request');
  });
});
