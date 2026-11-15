// GET /api/v1/dem/cop30/{tile} (PRD 8.5, decision G0-03): a bounded byte-range proxy to the public
// Copernicus GLO-30 COG bucket, which sends no CORS headers, so the browser probe worker reads DEM
// windows through here. It reads no bundle and holds no state.
//
// What it forwards: one `Range: bytes=a-b` of at most DEM_PROXY_MAX_RANGE_BYTES, for a tile name
// that matches DEM_TILE_PATTERN, to one URL built from DEM_UPSTREAM_BASE (never from client input
// beyond the validated tile name). Redirects are not followed. Only a 206 whose Content-Range
// matches the request is passed on; a 200 (Range ignored) is never streamed. The body is read
// whole (at most 4 MB) under one timeout, so a stalled upstream cannot hold the response open.
//
// Errors are problem+json (no-store): 400 for a bad tile or Range (a missing, open, suffix, multi,
// reversed or larger-than-4-MB range is a malformed request for this proxy, not a 416, because no
// resource length is known before the upstream answers), 404 for a tile not in GLO-30, 502 for a
// bad upstream answer or network failure, 504 for a timeout. Upstream detail is never echoed.
import { createRoute, type RouteHandler, z } from '@hono/zod-openapi';

import {
  DEM_PROXY_MAX_RANGE_BYTES,
  DEM_TILE_PATTERN,
  demUpstreamUrl,
  parseDemRange,
} from './dem-contract.ts';
import { PROBLEM_MEDIA_TYPE, problemResponse, ProblemSchema } from './problem.ts';

/** One day (PRD 8.5, Cache column): GLO-30 tiles do not change. */
export const DEM_CACHE_CONTROL = 'public, max-age=86400';

/** How long one upstream request may take, headers and body together. */
export const DEM_UPSTREAM_TIMEOUT_MS = 15_000;

/** The upstream headers passed on; nothing else (no x-amz-*, no cookies). */
const FORWARDED = [
  'Content-Range',
  'Content-Length',
  'Content-Type',
  'Accept-Ranges',
  'ETag',
  'Last-Modified',
] as const;

export const demRoute = createRoute({
  method: 'get',
  path: '/dem/cop30/{tile}',
  summary: 'Copernicus GLO-30 DEM byte range',
  description:
    'A byte-range proxy to the public Copernicus GLO-30 COG of one 1 degree tile. Send exactly one ' +
    '`Range: bytes=first-last` header of at most 4 MB; the 206 body is that range of the GeoTIFF. ' +
    'A request without such a Range is a 400 problem, never the whole file. A tile that is not in ' +
    'GLO-30 (ocean or not covered) is a 404 problem. Cached for one day.',
  request: {
    params: z.object({
      tile: z
        .string()
        .regex(DEM_TILE_PATTERN)
        .openapi({
          param: { name: 'tile', in: 'path' },
          description: 'The tile name, Copernicus_DSM_COG_10_<N|S>dd_00_<E|W>ddd_00_DEM.',
          example: 'Copernicus_DSM_COG_10_N64_00_W017_00_DEM',
        }),
    }),
    headers: z.object({
      range: z
        .string()
        .optional()
        .openapi({
          param: { name: 'range', in: 'header' },
          description: 'bytes=first-last, both given, at most 4 MB (4194304 bytes) long.',
          example: 'bytes=0-16383',
        }),
    }),
  },
  responses: {
    206: {
      description: 'The requested byte range of the GeoTIFF.',
      headers: {
        'Content-Range': { schema: { type: 'string' }, description: 'The range and total length.' },
        'Cache-Control': { schema: { type: 'string' }, description: DEM_CACHE_CONTROL },
      },
      content: { 'image/tiff': { schema: { type: 'string', format: 'binary' } } },
    },
    400: problemDoc('The tile name or the Range header is not accepted.'),
    404: problemDoc('The tile does not exist in GLO-30 (ocean or not covered).'),
    500: problemDoc('An unexpected error occurred.'),
    502: problemDoc('The upstream bucket failed or answered something other than the range.'),
    504: problemDoc('The upstream bucket did not answer in time.'),
  },
});

function problemDoc(description: string) {
  return { description, content: { [PROBLEM_MEDIA_TYPE]: { schema: ProblemSchema } } };
}

type Upstream = typeof fetch;

/** The proxy handler over an upstream fetch (global fetch by default; tests inject a stub). */
export function demHandler(upstream: Upstream = (...args) => fetch(...args)): RouteHandler<
  typeof demRoute
> {
  return async (c) => {
    const request = c.req.raw;
    const { tile } = c.req.valid('param');
    const range = parseDemRange(request.headers.get('range'));
    if (!range) {
      return problemResponse(
        request,
        400,
        'Send exactly one Range header of the form bytes=first-last (both ends, first not after last); open, suffix and multiple ranges are not served.',
      );
    }
    if (range.last - range.first + 1 > DEM_PROXY_MAX_RANGE_BYTES) {
      return problemResponse(
        request,
        400,
        `The range is longer than the ${String(DEM_PROXY_MAX_RANGE_BYTES)} bytes (4 MB) this proxy serves.`,
      );
    }
    let response: Response;
    let body: ArrayBuffer;
    try {
      response = await upstream(demUpstreamUrl(tile), {
        method: 'GET',
        headers: { Range: `bytes=${String(range.first)}-${String(range.last)}` },
        redirect: 'manual',
        signal: AbortSignal.timeout(DEM_UPSTREAM_TIMEOUT_MS),
      });
      if (response.status === 404) {
        return problemResponse(
          request,
          404,
          'This tile does not exist in GLO-30 (ocean or not covered).',
        );
      }
      if (response.status !== 206) {
        void response.body?.cancel().catch(() => undefined);
        return problemResponse(
          request,
          502,
          'The DEM bucket did not answer the byte range as a partial response.',
        );
      }
      body = await response.arrayBuffer();
    } catch (error) {
      const timedOut = error instanceof DOMException && error.name === 'TimeoutError';
      return problemResponse(
        request,
        timedOut ? 504 : 502,
        timedOut
          ? 'The DEM bucket did not answer in time.'
          : 'The DEM bucket could not be reached.',
      );
    }
    const match = /^bytes (\d+)-(\d+)\/(\d+|\*)$/.exec(response.headers.get('content-range') ?? '');
    const first = match ? Number(match[1]) : Number.NaN;
    const last = match ? Number(match[2]) : Number.NaN;
    if (
      first !== range.first ||
      last > range.last ||
      last < first ||
      body.byteLength !== last - first + 1
    ) {
      return problemResponse(
        request,
        502,
        'The DEM bucket answered a different range than requested.',
      );
    }
    const headers = new Headers();
    for (const name of FORWARDED) {
      const value = response.headers.get(name);
      if (value !== null) headers.set(name, value);
    }
    headers.set('Content-Length', String(body.byteLength));
    headers.set('Cache-Control', DEM_CACHE_CONTROL);
    headers.set('Vary', 'Range');
    return new Response(body, { status: 206, headers });
  };
}
