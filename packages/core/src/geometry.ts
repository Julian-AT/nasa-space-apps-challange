// D29 window resampler (PRD 7, 8.8): the TypeScript mirror of terraluna_pipeline.geometry.
//
// Only the geographic lookup form is implemented (Copernicus GLO-30 for the browser probe). The
// polar-stereographic and equirectangular lookups stay Python-only (PRD 7). Every numerical path
// is float64 (Float64Array and number); missing data is NaN beside an explicit mask, never 0.

/** D29 sphere radius per body in metres (geometry.BODY_RADII_M). */
export const MOON_RADIUS_M = 1737400.0;
export const MARS_RADIUS_M = 3396190.0;
export const EARTH_RADIUS_M = 6371008.8;

export type Body = 'moon' | 'mars' | 'earth';

export const BODY_RADII_M: Readonly<Record<Body, number>> = Object.freeze({
  moon: MOON_RADIUS_M,
  mars: MARS_RADIUS_M,
  earth: EARTH_RADIUS_M,
});

export class GeometryError extends Error {
  override name = 'GeometryError';
}

export function bodyRadiusM(body: string): number {
  if (body === 'moon' || body === 'mars' || body === 'earth') return BODY_RADII_M[body];
  throw new GeometryError(`no D29 sphere for body ${JSON.stringify(body)}; known bodies: moon, mars, earth`);
}

/** Pixel centres of a square window in metres from its centre (geometry.WindowGrid). */
export interface WindowGrid {
  readonly widthM: number;
  readonly spacingM: number;
  readonly size: number;
  /** Column centres, east positive: x_i = (i + 0.5) * spacing - width / 2. */
  readonly xM: Float64Array;
  /** Row centres, north positive, row 0 at the top: y_j = width / 2 - (j + 0.5) * spacing. */
  readonly yM: Float64Array;
}

/** geometry.window_grid: the largest n with n * spacing <= width, anchored at the top-left. */
export function windowGrid(widthM: number, spacingM: number): WindowGrid {
  for (const [name, value] of [
    ['width_m', widthM],
    ['spacing_m', spacingM],
  ] as const) {
    if (!(Number.isFinite(value) && value > 0)) {
      throw new GeometryError(`${name} must be finite and positive, got ${String(value)}`);
    }
  }
  const ratio = widthM / spacingM;
  if (!Number.isFinite(ratio)) throw new GeometryError('width_m / spacing_m is not finite');
  let size = Math.floor(ratio);
  while (size > 0 && size * spacingM > widthM) size -= 1;
  while ((size + 1) * spacingM <= widthM) size += 1;
  if (size < 1) throw new GeometryError(`no ${String(spacingM)} m cell fits in a ${String(widthM)} m window`);
  const xM = new Float64Array(size);
  const yM = new Float64Array(size);
  for (let i = 0; i < size; i += 1) {
    const centre = (i + 0.5) * spacingM;
    xM[i] = centre - widthM / 2;
    yM[i] = widthM / 2 - centre;
  }
  return { widthM, spacingM, size, xM, yM };
}

/** Row-major (x, y) of every pixel centre, indexed [row * size + column]. */
export function gridMesh(grid: WindowGrid): { x: Float64Array; y: Float64Array } {
  const n = grid.size;
  const x = new Float64Array(n * n);
  const y = new Float64Array(n * n);
  for (let r = 0; r < n; r += 1) {
    for (let c = 0; c < n; c += 1) {
      x[r * n + c] = grid.xM[c] as number;
      y[r * n + c] = grid.yM[r] as number;
    }
  }
  return { x, y };
}

const PI = Math.PI;
const HALF_PI = Math.PI / 2;
const TWO_PI = 2 * Math.PI;
// The largest float64 below Math.PI.
const PI_BELOW = 3.1415926535897927;
// 2 pi - 2 * Math.PI rounded to float64 (geometry._TWO_PI_LO).
const TWO_PI_LO = 2.4492935982947064e-16;

function twoSum(a: number, b: number): [number, number] {
  const s = a + b;
  const bPart = s - a;
  return [s, a - (s - bPart) + (b - bPart)];
}

/** geometry._wrap_longitude: the float64 in [-pi, pi) nearest a_hi + a_lo on the true circle. */
function wrapLongitude(aHi: number, aLo: number): number {
  const over = aHi > PI || (aHi === PI && aLo >= 0);
  const under = aHi < -PI || (aHi === -PI && aLo < 0);
  const turns = over ? -1 : under ? 1 : 0;
  const [bHi, bLo] = twoSum(aHi + turns * TWO_PI, aLo + turns * TWO_PI_LO);
  const toEastEnd = bHi - PI_BELOW + bLo;
  const toWestEnd = PI - bHi + (TWO_PI_LO - bLo);
  const east = toEastEnd < toWestEnd ? PI_BELOW : -PI;
  return bHi >= PI ? east : bHi < -PI ? -PI : bHi;
}

function checkCentre(lat0: number, lon0: number, radius: number): void {
  if (!(Number.isFinite(radius) && radius > 0)) throw new GeometryError('radius_m must be finite and positive');
  if (!(Number.isFinite(lat0) && lat0 >= -HALF_PI && lat0 <= HALF_PI)) {
    throw new GeometryError('lat0_rad must lie in [-pi/2, pi/2]');
  }
  if (!(Number.isFinite(lon0) && lon0 >= -PI && lon0 < PI)) throw new GeometryError('lon0_rad must lie in [-pi, pi)');
}

export interface AeqdCentre {
  readonly lat0Rad: number;
  readonly lon0Rad: number;
  readonly radiusM: number;
}

/**
 * Inverse spherical AEQD (geometry.aeqd_inverse), evaluated in the same increment form: latitude
 * as phi0 + atan2 of small increments, clipped to [-pi/2, pi/2]; longitude as an exact two-sum
 * wrapped on the true 2 pi period; rho = 0 returns the centre exactly.
 */
export function aeqdInverse(
  xM: ArrayLike<number>,
  yM: ArrayLike<number>,
  centre: AeqdCentre,
): { lat: Float64Array; lon: Float64Array } {
  const { lat0Rad: lat0, lon0Rad: lon0, radiusM: radius } = centre;
  checkCentre(lat0, lon0, radius);
  if (xM.length !== yM.length) throw new GeometryError('x_m and y_m must have the same length');
  const n = xM.length;
  const lat = new Float64Array(n);
  const lon = new Float64Array(n);
  const sinLat0 = Math.sin(lat0);
  const cosLat0 = Math.cos(lat0);
  for (let i = 0; i < n; i += 1) {
    const x = xM[i] as number;
    const y = yM[i] as number;
    if (!(Number.isFinite(x) && Number.isFinite(y))) throw new GeometryError('window offsets must be finite');
    const rho = Math.hypot(x, y);
    if (rho > PI * radius) throw new GeometryError('offsets beyond the antipode');
    if (rho === 0) {
      lat[i] = lat0;
      lon[i] = lon0;
      continue;
    }
    const east = x / rho;
    const north = y / rho;
    const c = rho / radius;
    const sinC = Math.sin(c);
    const half = Math.sin(c / 2);
    const k = 2 * (half * half);
    const s = north * sinC * cosLat0 - k * sinLat0;
    const m = -k * cosLat0 - north * sinC * sinLat0;
    const lonNum = east * sinC;
    const lonDen = cosLat0 + m;
    const h = (lonNum * lonNum + m * (2 * cosLat0 + m)) / (Math.hypot(lonNum, lonDen) + cosLat0);
    const sinDlat = s * cosLat0 - h * sinLat0;
    const cosDlat = 1 + h * cosLat0 + s * sinLat0;
    const phi = lat0 + Math.atan2(sinDlat, cosDlat);
    lat[i] = Math.min(Math.max(phi, -HALF_PI), HALF_PI);
    const [lonHi, lonLo] = twoSum(lon0, Math.atan2(lonNum, lonDen));
    lon[i] = wrapLongitude(lonHi, lonLo);
  }
  return { lat, lon };
}

/**
 * Geographic latitude-longitude raster lookup (geometry.GeographicLookup). origin_* is the outer
 * corner of pixel (0, 0) as GDAL reports it; pixel sizes are signed (height negative north-up).
 */
export interface GeographicLookup {
  readonly form: 'geographic';
  readonly originLonDeg: number;
  readonly originLatDeg: number;
  readonly pixelWidthDeg: number;
  readonly pixelHeightDeg: number;
}

export function geographicLookup(params: Omit<GeographicLookup, 'form'>): GeographicLookup {
  const { originLonDeg, originLatDeg, pixelWidthDeg, pixelHeightDeg } = params;
  if (!(Number.isFinite(originLonDeg) && originLonDeg >= -360 && originLonDeg <= 360)) {
    throw new GeometryError('origin_lon_deg must lie in [-360, 360]');
  }
  if (!(Number.isFinite(originLatDeg) && originLatDeg >= -90 && originLatDeg <= 90)) {
    throw new GeometryError('origin_lat_deg must lie in [-90, 90]');
  }
  if (!(Number.isFinite(pixelWidthDeg) && pixelWidthDeg > 0)) {
    throw new GeometryError('pixel_width_deg must be positive: columns run east');
  }
  if (!(Number.isFinite(pixelHeightDeg) && pixelHeightDeg !== 0)) throw new GeometryError('pixel_height_deg must not be zero');
  return { form: 'geographic', originLonDeg, originLatDeg, pixelWidthDeg, pixelHeightDeg };
}

const RAD_TO_DEG = 180 / Math.PI;

/** GeographicLookup.to_pixel: fractional (column, row) of pixel centres for points in radians. */
export function geographicToPixel(
  lookup: GeographicLookup,
  latRad: ArrayLike<number>,
  lonRad: ArrayLike<number>,
): { column: Float64Array; row: Float64Array } {
  const n = latRad.length;
  const column = new Float64Array(n);
  const row = new Float64Array(n);
  for (let i = 0; i < n; i += 1) {
    const latR = latRad[i] as number;
    const lonR = lonRad[i] as number;
    if (!(Number.isFinite(latR) && Number.isFinite(lonR)) || Math.abs(latR) > HALF_PI || Math.abs(lonR) > PI) {
      throw new GeometryError('latitudes and longitudes must be finite and in range');
    }
    // numpy.degrees: x * (180 / pi), rounded once.
    const lonDeg = lonR * RAD_TO_DEG;
    const latDeg = latR * RAD_TO_DEG;
    const [eastHi, eastLo] = twoSum(lonDeg, -lookup.originLonDeg);
    let turns = Math.floor(eastHi / 360);
    let [shiftedHi, shiftedLo] = twoSum(eastHi, -360 * turns);
    shiftedLo = shiftedLo + eastLo;
    const below = shiftedHi < 0 || (shiftedHi === 0 && shiftedLo < 0);
    const above = shiftedHi > 360 || (shiftedHi === 360 && shiftedLo >= 0);
    turns = turns - (below ? 1 : 0) + (above ? 1 : 0);
    [shiftedHi, shiftedLo] = twoSum(eastHi, -360 * turns);
    const east = shiftedHi + (shiftedLo + eastLo);
    const [northHi, northLo] = twoSum(latDeg, -lookup.originLatDeg);
    column[i] = east / lookup.pixelWidthDeg - 0.5;
    row[i] = (northHi + northLo) / lookup.pixelHeightDeg - 0.5;
  }
  return { column, row };
}

/** A source raster, row-major float64, indexed [row * columns + column]. */
export interface SourceRaster {
  readonly values: Float64Array;
  readonly rows: number;
  readonly columns: number;
  /**
   * The declared sentinel in the raster's own type (for a float32 band pass Math.fround of the
   * declared value), or null if none is declared. NaN and infinities are always missing.
   */
  readonly nodata: number | null;
}

/** A resampled grid: NaN where not valid, with explicit masks (geometry.ResampledGrid). */
export interface ResampledGrid {
  readonly values: Float64Array;
  readonly valid: Uint8Array;
  readonly inside: Uint8Array;
  readonly validCells: number;
}

/**
 * geometry.bilinear_resample: bilinear on the four neighbouring source pixels; the output is
 * missing if the point is outside the rectangle of pixel centres or any neighbour is missing.
 */
export function bilinearResample(
  source: SourceRaster,
  column: ArrayLike<number>,
  row: ArrayLike<number>,
): ResampledGrid {
  const { rows, columns: cols, values: src, nodata } = source;
  if (rows < 2 || cols < 2) throw new GeometryError('source must have at least 2 rows and 2 columns');
  if (src.length !== rows * cols) throw new GeometryError('source values do not match rows x columns');
  // A declared zero masks both signed zeros (=== treats them as equal); a NaN or infinite
  // sentinel adds no missing pixels beyond the non-finite ones.
  const good = (index: number): boolean => {
    const z = src[index] as number;
    return Number.isFinite(z) && (nodata === null || !Number.isFinite(nodata) || z !== nodata);
  };
  const n = column.length;
  const values = new Float64Array(n);
  const valid = new Uint8Array(n);
  const inside = new Uint8Array(n);
  let validCells = 0;
  for (let k = 0; k < n; k += 1) {
    const u = column[k] as number;
    const v = row[k] as number;
    if (!(Number.isFinite(u) && Number.isFinite(v))) throw new GeometryError('pixel coordinates must be finite');
    const isInside = u >= 0 && u <= cols - 1 && v >= 0 && v <= rows - 1;
    if (!isInside) {
      values[k] = Number.NaN;
      continue;
    }
    inside[k] = 1;
    const i = Math.min(Math.floor(u), cols - 2);
    const j = Math.min(Math.floor(v), rows - 2);
    const p = u - i;
    const q = v - j;
    const a = j * cols + i;
    const b = (j + 1) * cols + i;
    if (!(good(a) && good(a + 1) && good(b) && good(b + 1))) {
      values[k] = Number.NaN;
      continue;
    }
    const top = (1 - p) * (src[a] as number) + p * (src[a + 1] as number);
    const bottom = (1 - p) * (src[b] as number) + p * (src[b + 1] as number);
    values[k] = (1 - q) * top + q * bottom;
    valid[k] = 1;
    validCells += 1;
  }
  return { values, valid, inside, validCells };
}

/**
 * geometry.resample_window for the geographic form: each pixel centre of grid through the inverse
 * AEQD about (lat0, lon0) on the body sphere, into source pixels, then bilinear. The 90 m analysis
 * grid and the 64 x 64 encoder grid at 78.125 m both come from this one function.
 */
export function resampleWindow(
  source: SourceRaster,
  lookup: GeographicLookup,
  options: { grid: WindowGrid; body: Body; lat0Rad: number; lon0Rad: number },
): ResampledGrid {
  const radiusM = bodyRadiusM(options.body);
  const { x, y } = gridMesh(options.grid);
  const { lat, lon } = aeqdInverse(x, y, { lat0Rad: options.lat0Rad, lon0Rad: options.lon0Rad, radiusM });
  const { column, row } = geographicToPixel(lookup, lat, lon);
  return bilinearResample(source, column, row);
}
