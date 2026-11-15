// GET /api/v1/targets and /targets/{id} (PRD 8.5, T15B-02) over test-local, schema-valid records.
import { describe, expect, it } from 'vitest';

import { BundleFileError } from '../../lib/bundle.ts';
import type { TargetDetail } from './targets.ts';
import {
  EARTH_ID,
  memoryReader,
  radarFingerprint,
  TARGET_ID,
  targetRecord,
  windowEntry,
} from './test-records.ts';
import { expectProblem, mockReader, quietApi } from './test-support.ts';

const TARGET_WINDOW = 'moon-test-x0-y0-w0';

function get(reader: ReturnType<typeof memoryReader>, path: string) {
  return quietApi(reader).api.request(`/api/v1${path}`);
}

describe('GET /api/v1/targets', () => {
  it('lists id, body, footprint and shipping_mode of each record and nothing else', async () => {
    const reader = memoryReader({
      targets: [targetRecord(), targetRecord({ target_id: 'mars-test-x9-y9', body: 'mars', shipping_mode: 'B' })],
    });
    const response = await get(reader, '/targets');
    expect(response.status).toBe(200);
    const body = (await response.json()) as { items: Record<string, unknown>[] };
    expect(body.items).toEqual([
      { target_id: TARGET_ID, body: 'moon', footprint: null, shipping_mode: null },
      { target_id: 'mars-test-x9-y9', body: 'mars', footprint: null, shipping_mode: 'B' },
    ]);
    for (const item of body.items) expect(Object.keys(item).sort()).toEqual(['body', 'footprint', 'shipping_mode', 'target_id']);
  });

  it('keeps a null footprint null, never derived from the id', async () => {
    const response = await get(memoryReader({ targets: [targetRecord()] }), '/targets');
    const body = (await response.json()) as { items: { footprint: unknown }[] };
    expect(body.items[0]?.footprint).toBeNull();
  });

  it('is empty on a bundle without targets.json', async () => {
    const response = await get(mockReader, '/targets');
    expect(response.status).toBe(200);
    expect(((await response.json()) as { items: unknown[] }).items).toEqual([]);
  });

  it('is cached per bundle', async () => {
    const response = await get(memoryReader({ targets: [targetRecord()] }), '/targets');
    expect(response.headers.get('etag')).toBeTruthy();
    expect(response.headers.get('cache-control')).toContain('max-age');
  });
});

describe('GET /api/v1/targets/{id}', () => {
  const windows = {
    moon: [windowEntry(TARGET_WINDOW, { radar: [radarFingerprint(TARGET_WINDOW)] })],
    earth: [windowEntry(EARTH_ID(1))],
  };

  it('returns the record, its target and reference windows and the radar context', async () => {
    const reader = memoryReader({
      targets: [
        targetRecord({
          primary_window_id: TARGET_WINDOW,
          reference_window_ids: [EARTH_ID(1)],
        }),
      ],
      windows,
    });
    const response = await get(reader, `/targets/${TARGET_ID}`);
    expect(response.status).toBe(200);
    const body = (await response.json()) as TargetDetail;
    expect(body.target.target_id).toBe(TARGET_ID);
    expect(body.target_window?.window.window_id).toBe(TARGET_WINDOW);
    expect(body.reference_windows.map((w) => w.window.window_id)).toEqual([EARTH_ID(1)]);
    expect(body.radar_context).toHaveLength(1);
    expect(body.radar_context[0]).toMatchObject({ window_id: TARGET_WINDOW, status: 'available', reason: null });
  });

  it('omits a window the files lack instead of defaulting it', async () => {
    const reader = memoryReader({
      targets: [targetRecord({ primary_window_id: 'moon-test-gone', reference_window_ids: [EARTH_ID(7)] })],
      windows,
    });
    const body = (await (await get(reader, `/targets/${TARGET_ID}`)).json()) as Record<string, unknown>;
    expect('target_window' in body).toBe(false);
    expect(body.reference_windows).toEqual([]);
    expect(body.radar_context).toEqual([]);
  });

  it('gives a radar fingerprint without data as Unavailable with its stored reason, never zero', async () => {
    const reader = memoryReader({
      targets: [targetRecord({ primary_window_id: TARGET_WINDOW })],
      windows: {
        moon: [
          windowEntry(TARGET_WINDOW, {
            radar: [radarFingerprint(TARGET_WINDOW, { reason: 'test_radar_not_covered' })],
          }),
        ],
      },
    });
    const body = (await (await get(reader, `/targets/${TARGET_ID}`)).json()) as TargetDetail;
    expect(body.radar_context[0]).toMatchObject({ status: 'unavailable', reason: 'test_radar_not_covered' });
    expect(body.radar_context[0]?.fingerprint.radar?.cpr_ratio_of_mean_powers).toBeNull();
  });

  it('is a 404 problem for an unknown id and a 400 problem for an id outside the pattern', async () => {
    const reader = memoryReader({ targets: [targetRecord()] });
    await expectProblem(await get(reader, '/targets/moon-test-none'), 404, 'Not Found');
    await expectProblem(await get(reader, '/targets/.hidden'), 400, 'Bad Request');
    await expectProblem(await get(mockReader, `/targets/${TARGET_ID}`), 404, 'Not Found');
  });

  it('is a 500 problem naming the file when the loader refuses targets.json', async () => {
    const refusing = () => {
      throw new BundleFileError('targets.json', 'invalid', 'targets.json fails its schema');
    };
    const problem = await expectProblem(await get(refusing, '/targets'), 500, 'Internal Server Error');
    expect(problem.detail).toContain('targets.json');
  });
});
