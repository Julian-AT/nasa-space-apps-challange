// GET /api/v1/windows/{id} (PRD 8.5, T15B-03) over test-local, schema-valid window files.
import { describe, expect, it } from 'vitest';

import type { WindowResponse } from './windows.ts';
import { EARTH_ID, memoryReader, windowEntry } from './test-records.ts';
import { expectProblem, mockReader, quietApi } from './test-support.ts';

const get = (r: ReturnType<typeof memoryReader>, id: string) =>
  quietApi(r).api.request(`/api/v1/windows/${id}`);

describe('GET /api/v1/windows/{id}', () => {
  const reader = memoryReader({
    windows: { earth: [windowEntry(EARTH_ID(1), { card_id: 'card-test-1' }), windowEntry(EARTH_ID(2))] },
    cards: ['card-test-1'],
  });

  it('returns the WindowEntry and its card link', async () => {
    const response = await get(reader, EARTH_ID(1));
    expect(response.status).toBe(200);
    const body = (await response.json()) as WindowResponse;
    expect(body.body).toBe('earth');
    expect(body.window.window.window_id).toBe(EARTH_ID(1));
    expect(body.card).toEqual({ card_id: 'card-test-1', path: 'cards/card-test-1.json', in_bundle: true });
  });

  it('has no card link for a window that stores no card_id', async () => {
    const body = (await (await get(reader, EARTH_ID(2))).json()) as { card: unknown };
    expect(body.card).toBeNull();
  });

  it('is a 404 problem for an unknown id and on a bundle without windows', async () => {
    await expectProblem(await get(reader, EARTH_ID(9)), 404, 'Not Found');
    await expectProblem(await get(mockReader, EARTH_ID(1)), 404, 'Not Found');
  });

  it('is a 400 problem for an id outside the pattern', async () => {
    for (const id of ['x-test', 'Earth-test', 'earth-', 'earth_test']) {
      await expectProblem(await get(reader, id), 400, 'Bad Request');
    }
  });
});
