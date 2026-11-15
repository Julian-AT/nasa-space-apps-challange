// The request contract of the Copernicus GLO-30 DEM range proxy (PRD 8.5, decision G0-03). No
// server-only imports: the browser probe worker imports it to build and check its own requests.

/** The path prefix of the proxy; a tile is appended: `${DEM_PROXY_PATH}/${tile}`. */
export const DEM_PROXY_PATH = '/api/v1/dem/cop30';

/** The only tile names the proxy accepts (PRD 8.5). */
export const DEM_TILE_PATTERN = /^Copernicus_DSM_COG_10_[NS]\d{2}_00_[EW]\d{3}_00_DEM$/;

/** The most bytes one proxied Range request may ask for: 4 MB. */
export const DEM_PROXY_MAX_RANGE_BYTES = 4 * 1024 * 1024;

/** The public bucket the proxy reads; it sends no CORS headers (G0-03), hence the proxy. */
export const DEM_UPSTREAM_BASE = 'https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com';

/** The upstream COG of a tile name (not validated here; the proxy validates first). */
export function demUpstreamUrl(tile: string): string {
  return `${DEM_UPSTREAM_BASE}/${tile}/${tile}.tif`;
}

/** The proxy URL path of a tile. */
export function demProxyPath(tile: string): string {
  return `${DEM_PROXY_PATH}/${tile}`;
}

/** The Range header value for an inclusive byte range. */
export function demRangeHeader(first: number, last: number): string {
  return `bytes=${String(first)}-${String(last)}`;
}

/** Parses `bytes=a-b` (both ends, a <= b); null for anything else. */
export function parseDemRange(header: string | null | undefined): { first: number; last: number } | null {
  const match = /^bytes=(\d{1,15})-(\d{1,15})$/.exec(header ?? '');
  if (!match) return null;
  const first = Number(match[1]);
  const last = Number(match[2]);
  return first <= last ? { first, last } : null;
}
