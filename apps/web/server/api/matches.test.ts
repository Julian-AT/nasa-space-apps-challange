// GET /api/v1/targets/{id}/matches (PRD 8.5, T15B-03) over test-local, schema-valid comparison files.
import { describe, expect, it } from 'vitest';

import type { MatchesResponse } from './matches.ts';
import { comparison, EARTH_ID, memoryReader, TARGET_ID, targetRecord } from './test-records.ts';
import { expectProblem, quietApi } from './test-support.ts';

type Page = MatchesResponse;

const RECORDS = [
  comparison({ earth: EARTH_ID(1), relief: 0.5, texture: 0.1, radar: 0.3 }),
  comparison({ earth: EARTH_ID(2), relief: 0.2, texture: 0.9, radar: null }),
  comparison({ earth: EARTH_ID(3), relief: null, texture: 0.4, radar: 0.1 }),
  comparison({ earth: EARTH_ID(4), relief: 0.2, texture: null, radar: 0.2 }),
];

function reader(extra: Parameters<typeof memoryReader>[0] = {}) {
  return memoryReader({
    targets: [targetRecord()],
    comparisons: { [TARGET_ID]: RECORDS },
    ...extra,
  });
}

async function page(query: string, r = reader()): Promise<Page> {
  const response = await quietApi(r).api.request(`/api/v1/targets/${TARGET_ID}/matches?${query}`);
  expect(response.status).toBe(200);
  return (await response.json()) as Page;
}

const ids = (p: Page) => p.items.map((i) => i.earth_window_id);

describe('matches order and families', () => {
  it('returns the records in file order', async () => {
    expect(ids(await page('family=terrain'))).toEqual(RECORDS.map((r) => r.earth_window_id));
  });

  it('reorders by measure inside the terrain family only, stably, unavailable last', async () => {
    expect(ids(await page('family=terrain&measure=relief'))).toEqual([
      EARTH_ID(2),
      EARTH_ID(4),
      EARTH_ID(1),
      EARTH_ID(3),
    ]);
    expect(ids(await page('family=terrain&measure=texture'))).toEqual([
      EARTH_ID(1),
      EARTH_ID(3),
      EARTH_ID(2),
      EARTH_ID(4),
    ]);
  });

  it('is a 400 problem for measure with radar or model', async () => {
    for (const family of ['radar', 'model']) {
      const response = await quietApi(reader()).api.request(
        `/api/v1/targets/${TARGET_ID}/matches?family=${family}&measure=relief`,
      );
      await expectProblem(response, 400, 'Bad Request');
    }
  });

  it('carries terrain keys for terrain, radar_cpr for radar, and never mixes families', async () => {
    const terrain = (await page('family=terrain')).items[0] ?? {};
    expect(Object.keys(terrain)).toEqual(expect.arrayContaining(['terrain_relief', 'terrain_texture']));
    expect('radar_cpr' in terrain || 'model_embedding' in terrain).toBe(false);
    const radar = (await page('family=radar')).items[0] ?? {};
    expect('radar_cpr' in radar).toBe(true);
    expect('terrain_relief' in radar || 'model_embedding' in radar).toBe(false);
  });

  it('returns an unavailable distance as stored, with its reason and no value', async () => {
    const second = (await page('family=radar')).items[1];
    expect(second?.radar_cpr).toMatchObject({ value: null, status: 'unavailable', reason: 'test_radar_not_in_mode' });
  });

  it('refuses a Mode B record that carries a radar value', async () => {
    const bad = comparison({ earth: EARTH_ID(1), relief: 0.1, texture: 0.1, radar: 0.5, mode: 'B' });
    const r = reader({ comparisons: { [TARGET_ID]: [bad] } });
    const response = await quietApi(r).api.request(`/api/v1/targets/${TARGET_ID}/matches?family=radar`);
    await expectProblem(response, 500, 'Internal Server Error');
  });
});

describe('family=model', () => {
  const withModel = (g5: string | null, manifestG5 = 'PASS') =>
    reader({
      g5,
      manifest: { model: { id: 'test-model-alpha', status: 'EXPERIMENTAL', g5: manifestG5 } },
      comparisons: {
        [TARGET_ID]: [comparison({ earth: EARTH_ID(1), relief: 0.1, texture: 0.1, model: 0.7 })],
      },
    });

  it.each(['NOT_RUN', 'FAIL', 'PARTIAL', null])('is a 409 problem while G5 is %s', async (g5) => {
    const r = withModel(g5, g5 ?? 'NOT_RUN');
    const response = await quietApi(r).api.request(`/api/v1/targets/${TARGET_ID}/matches?family=model`);
    await expectProblem(response, 409, 'Conflict');
  });

  it('is a 409 problem when the bundle has no model, whatever it holds', async () => {
    const response = await quietApi(reader()).api.request(`/api/v1/targets/${TARGET_ID}/matches?family=model`);
    await expectProblem(response, 409, 'Conflict');
  });

  it('returns Predicted entries once G5 is PASS in both places', async () => {
    const result = await page('family=model', withModel('PASS'));
    expect(result.evidence_label).toBe('Predicted');
    expect(result.items[0]?.model_embedding).toMatchObject({ value: 0.7, evidence_label: 'Predicted' });
  });

  it('leaves terrain and radar untouched by the G5 state', async () => {
    expect((await page('family=terrain', withModel('FAIL', 'FAIL'))).items).toHaveLength(1);
  });
});

describe('paging', () => {
  const many = Array.from({ length: 10_000 }, (_, i) =>
    comparison({ earth: `earth-test-x${String(i)}-y0`, relief: i / 10_000, texture: 0 }),
  );
  const big = () => reader({ comparisons: { [TARGET_ID]: many } });

  it('pages 10,000 stored records to the first 500 in order, without repeat or gap', async () => {
    const r = big();
    const seen: string[] = [];
    let cursor: string | null = null;
    let pages = 0;
    do {
      const query: string = `family=terrain&limit=70${cursor === null ? '' : `&cursor=${cursor}`}`;
      const p = await page(query, r);
      expect(p.total).toBe(500);
      seen.push(...ids(p));
      cursor = p.next_cursor;
      pages += 1;
    } while (cursor !== null && pages < 20);
    expect(seen).toEqual(many.slice(0, 500).map((m) => m.earth_window_id));
  });

  it('defaults to 50 and rejects limits outside 1..500', async () => {
    expect((await page('family=terrain', big())).items).toHaveLength(50);
    for (const limit of ['0', '501', 'x']) {
      const response = await quietApi(big()).api.request(`/api/v1/targets/${TARGET_ID}/matches?family=terrain&limit=${limit}`);
      await expectProblem(response, 400, 'Bad Request');
    }
  });

  it('is a 400 problem for a malformed cursor', async () => {
    for (const cursor of ['!!', 'AAAA', Buffer.from('o9999').toString('base64url'), Buffer.from('x1').toString('base64url')]) {
      const response = await quietApi(reader()).api.request(
        `/api/v1/targets/${TARGET_ID}/matches?family=terrain&cursor=${cursor}`,
      );
      await expectProblem(response, 400, 'Bad Request');
    }
  });
});

describe('missing comparisons', () => {
  it('is a 404 problem for a target without a comparison file, and for an unknown one', async () => {
    const r = memoryReader({ targets: [targetRecord()] });
    await expectProblem(
      await quietApi(r).api.request(`/api/v1/targets/${TARGET_ID}/matches?family=terrain`),
      404,
      'Not Found',
    );
    await expectProblem(
      await quietApi(reader()).api.request('/api/v1/targets/moon-test-none/matches?family=terrain'),
      404,
      'Not Found',
    );
  });
});
