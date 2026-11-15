// GET /api/v1/dem/cop30/{tile} (PRD 8.5, G0-03): a bounded Range proxy to the public Copernicus
// GLO-30 bucket. Tests inject a stub upstream and count its requests: no network.
import { describe, expect, it } from 'vitest';

import { createApi } from './app.ts';
import {
  DEM_PROXY_MAX_RANGE_BYTES,
  DEM_PROXY_PATH,
  DEM_TILE_PATTERN,
  demUpstreamUrl,
  parseDemRange,
} from './dem-contract.ts';
import { DEM_CACHE_CONTROL, DEM_UPSTREAM_TIMEOUT_MS } from './dem.ts';
import { PROBLEM_MEDIA_TYPE } from './problem.ts';

const TILE = 'Copernicus_DSM_COG_10_N64_00_W017_00_DEM';
const URL_OK = `${DEM_PROXY_PATH}/${TILE}`;

interface Call {
  url: string;
  init: RequestInit | undefined;
}

function harness(answer: (call: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  const stub = ((input: string | URL | Request, init?: RequestInit) => {
    const call = { url: input instanceof Request ? input.url : String(input), init };
    calls.push(call);
    return Promise.resolve(answer(call));
  }) as typeof fetch;
  const api = createApi({ demFetch: stub, log: () => undefined });
  return { api, calls };
}

const partial = (body: Uint8Array, first = 0, total = 1000) =>
  new Response(body as BodyInit, {
    status: 206,
    headers: {
      'Content-Range': `bytes ${String(first)}-${String(first + body.length - 1)}/${String(total)}`,
      'Content-Length': String(body.length),
      'Content-Type': 'image/tiff',
      'Accept-Ranges': 'bytes',
      ETag: '"abc"',
      'Last-Modified': 'Mon, 09 May 2022 14:41:24 GMT',
      'x-amz-request-id': 'secret',
    },
  });

const get = (api: ReturnType<typeof harness>['api'], path: string, range?: string) =>
  api.request(path, { headers: range ? { Range: range } : {} });

async function expectProblem(response: Response, status: number) {
  expect(response.status).toBe(status);
  expect(response.headers.get('content-type')).toBe(PROBLEM_MEDIA_TYPE);
  expect(response.headers.get('cache-control')).toBe('no-store');
  const body = (await response.json()) as { status: number; detail: string };
  expect(body.status).toBe(status);
  return body;
}

describe('the DEM contract', () => {
  it('has a 4 MB limit and accepts only exact tile names', () => {
    expect(DEM_PROXY_MAX_RANGE_BYTES).toBe(4_194_304);
    expect(DEM_TILE_PATTERN.test(TILE)).toBe(true);
    for (const bad of [
      `${TILE}x`,
      `x${TILE}`,
      'Copernicus_DSM_COG_10_N64_00_W017_00_DEM.tif',
      'Copernicus_DSM_COG_10_X64_00_W017_00_DEM',
      'Copernicus_DSM_COG_10_N6_00_W017_00_DEM',
      'Copernicus_DSM_COG_10_N64_00_W17_00_DEM',
      'Copernicus_DSM_COG_30_N64_00_W017_00_DEM',
      '',
    ]) {
      expect(DEM_TILE_PATTERN.test(bad), bad).toBe(false);
    }
    expect(demUpstreamUrl(TILE)).toBe(
      `https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/${TILE}/${TILE}.tif`,
    );
  });

  it('parses only a single bounded range', () => {
    expect(parseDemRange('bytes=0-99')).toEqual({ first: 0, last: 99 });
    for (const bad of ['bytes=0-', 'bytes=-5', 'bytes=0-1,4-5', 'bytes=5-1', 'items=0-1', '', null])
      expect(parseDemRange(bad), String(bad)).toBeNull();
  });
});

describe('GET /api/v1/dem/cop30/{tile}', () => {
  it('forwards a bounded range and returns the 206 with the allowed headers and a one-day cache', async () => {
    const bytes = new Uint8Array([1, 2, 3, 4, 5]);
    const { api, calls } = harness(() => partial(bytes));
    const response = await get(api, URL_OK, 'bytes=0-4');
    expect(response.status).toBe(206);
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(bytes);
    expect(response.headers.get('content-range')).toBe('bytes 0-4/1000');
    expect(response.headers.get('content-length')).toBe('5');
    expect(response.headers.get('content-type')).toBe('image/tiff');
    expect(response.headers.get('accept-ranges')).toBe('bytes');
    expect(response.headers.get('etag')).toBe('"abc"');
    expect(response.headers.get('last-modified')).toBe('Mon, 09 May 2022 14:41:24 GMT');
    expect(response.headers.get('cache-control')).toBe('public, max-age=86400');
    expect(DEM_CACHE_CONTROL).toBe('public, max-age=86400');
    expect(response.headers.get('x-amz-request-id')).toBeNull();
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(demUpstreamUrl(TILE));
    expect(new Headers(calls[0]?.init?.headers).get('range')).toBe('bytes=0-4');
    expect(calls[0]?.init?.redirect).toBe('manual');
    expect(calls[0]?.init?.signal).toBeInstanceOf(AbortSignal);
    expect(DEM_UPSTREAM_TIMEOUT_MS).toBe(15_000);
  });

  it('accepts exactly 4 MB and refuses 4 MB plus one byte, without an upstream request', async () => {
    const ok = harness(() => partial(new Uint8Array(4)));
    expect((await get(ok.api, URL_OK, `bytes=0-${String(DEM_PROXY_MAX_RANGE_BYTES - 1)}`)).status).toBe(206);
    const big = harness(() => partial(new Uint8Array(4)));
    const body = await expectProblem(
      await get(big.api, URL_OK, `bytes=0-${String(DEM_PROXY_MAX_RANGE_BYTES)}`),
      400,
    );
    expect(body.detail).toMatch(/4194304/);
    expect(big.calls).toHaveLength(0);
  });

  it.each([
    ['no Range header', undefined],
    ['an open range', 'bytes=10-'],
    ['a suffix range', 'bytes=-100'],
    ['multiple ranges', 'bytes=0-1,5-6'],
    ['a reversed range', 'bytes=9-1'],
    ['another unit', 'items=0-1'],
  ])('answers 400 for %s, without an upstream request', async (_name, range) => {
    const { api, calls } = harness(() => partial(new Uint8Array(1)));
    await expectProblem(await get(api, URL_OK, range), 400);
    expect(calls).toHaveLength(0);
  });

  it.each([
    `${TILE}x`,
    'Copernicus_DSM_COG_10_N64_00_W017_00_DEM.tif',
    '..%2Fsecret',
    'Copernicus_DSM_COG_10_N6_00_W017_00_DEM',
  ])('answers 400 for the tile %s, without an upstream request', async (tile) => {
    const { api, calls } = harness(() => partial(new Uint8Array(1)));
    await expectProblem(await get(api, `${DEM_PROXY_PATH}/${tile}`, 'bytes=0-1'), 400);
    expect(calls).toHaveLength(0);
  });

  it('answers 404 when the tile does not exist upstream (ocean or not covered)', async () => {
    const { api } = harness(() => new Response('<Error/>', { status: 404 }));
    const body = await expectProblem(await get(api, URL_OK, 'bytes=0-1'), 404);
    expect(body.detail).toMatch(/does not exist in GLO-30/);
    expect(body.detail).toMatch(/ocean or not covered/);
  });

  it('answers 502 and never streams the file when upstream ignores the Range', async () => {
    const { api } = harness(() => new Response(new Uint8Array(100), { status: 200 }));
    const response = await get(api, URL_OK, 'bytes=0-1');
    await expectProblem(response, 502);
  });

  it('answers 502 for other upstream statuses and for a redirect', async () => {
    for (const status of [301, 302, 403, 500, 416]) {
      const { api } = harness(
        () => new Response('x', { status, headers: { Location: 'https://evil.example/x' } }),
      );
      const response = await get(api, URL_OK, 'bytes=0-1');
      expect(response.status, String(status)).toBe(502);
      expect(response.headers.get('location')).toBeNull();
    }
  });

  it('answers 502 when the 206 does not match the requested range', async () => {
    const wrongStart = harness(() => partial(new Uint8Array(2), 5));
    await expectProblem(await get(wrongStart.api, URL_OK, 'bytes=0-1'), 502);
    const tooLong = harness(() => partial(new Uint8Array(10), 0));
    await expectProblem(await get(tooLong.api, URL_OK, 'bytes=0-1'), 502);
    const noRange = harness(() => new Response(new Uint8Array(2), { status: 206 }));
    await expectProblem(await get(noRange.api, URL_OK, 'bytes=0-1'), 502);
  });

  it('answers 504 on an upstream timeout and 502 on a network failure', async () => {
    const slow = harness(() => {
      throw new DOMException('timed out', 'TimeoutError');
    });
    await expectProblem(await get(slow.api, URL_OK, 'bytes=0-1'), 504);
    const down = harness(() => {
      throw new TypeError('fetch failed');
    });
    const body = await expectProblem(await get(down.api, URL_OK, 'bytes=0-1'), 502);
    expect(body.detail).not.toMatch(/fetch failed/);
  });

  it('does not send credentials, cookies or the client headers upstream', async () => {
    const { api, calls } = harness(() => partial(new Uint8Array(2)));
    await api.request(URL_OK, { headers: { Range: 'bytes=0-1', Cookie: 'a=b', Authorization: 'x' } });
    expect([...new Headers(calls[0]?.init?.headers).keys()].sort()).toEqual(['range']);
  });

  it('is read-only: POST is a 405', async () => {
    const { api, calls } = harness(() => partial(new Uint8Array(2)));
    expect((await api.request(URL_OK, { method: 'POST' })).status).toBe(405);
    expect(calls).toHaveLength(0);
  });
});
