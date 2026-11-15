// Parity assertions (PRD 8.8). NaN, null and Unavailable are distinct states: a missing value
// never equals zero, and NaN equals NaN only where the fixture marks nodata (null).

/** PRD 8.8 tolerances, exported once. */
export const RESAMPLED_GRID_RTOL = 1e-9;
export const LOG_NU_ATOL = 1e-9;
export const H_ATOL = 1e-9;
export const DISTANCE_ATOL_M = 1e-9;

export class ParityMismatch extends Error {
  override name = 'ParityMismatch';
}

/** |actual - expected| <= rtol * |expected|; null expected only matches NaN or null. */
export function closeRel(actual: number | null, expected: number | null, rtol: number, where = 'value'): void {
  check(actual, expected, where, (a, e) => Math.abs(a - e) <= rtol * Math.abs(e), `rtol ${String(rtol)}`);
}

/** |actual - expected| <= atol; null expected only matches NaN or null. */
export function closeAbs(actual: number | null, expected: number | null, atol: number, where = 'value'): void {
  check(actual, expected, where, (a, e) => Math.abs(a - e) <= atol, `atol ${String(atol)}`);
}

function check(
  actual: number | null,
  expected: number | null,
  where: string,
  ok: (a: number, e: number) => boolean,
  tol: string,
): void {
  const aMissing = actual === null || Number.isNaN(actual);
  const eMissing = expected === null || Number.isNaN(expected);
  if (aMissing || eMissing) {
    if (aMissing && eMissing) return;
    throw new ParityMismatch(`${where}: expected ${String(expected)}, got ${String(actual)} (missing is never a number)`);
  }
  if (!ok(actual, expected)) throw new ParityMismatch(`${where}: expected ${String(expected)}, got ${String(actual)} (${tol})`);
}

/** Validity reasons must be identical strings (or both null). */
export function reasonsEqual(actual: string | null | undefined, expected: string | null | undefined, where = 'reason'): void {
  if ((actual ?? null) !== (expected ?? null)) {
    throw new ParityMismatch(
      `${where}: expected reason ${JSON.stringify(expected ?? null)}, got ${JSON.stringify(actual ?? null)}`,
    );
  }
}

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

const EXACT_INT_PATH = /count|cells|grid_size|min_lags_for_H|offset_cells/;

/**
 * Walk an expected Python record and the TypeScript record together. Floats on a log_nu, H or
 * log_intercept path use the absolute tolerance, other floats the relative one; counts, strings,
 * booleans and nulls are compared exactly. Returns every mismatch, not only the first.
 */
export function compareRecord(actual: unknown, expected: Json, path: string, out: string[] = []): string[] {
  if (expected === null) {
    if (actual !== null) out.push(`${path}: expected null, got ${JSON.stringify(actual)}`);
    return out;
  }
  if (Array.isArray(expected)) {
    if (!Array.isArray(actual) || actual.length !== expected.length) {
      out.push(`${path}: expected an array of ${String(expected.length)}`);
      return out;
    }
    expected.forEach((e, i) => compareRecord(actual[i], e, `${path}[${String(i)}]`, out));
    return out;
  }
  if (typeof expected === 'object') {
    if (actual === null || typeof actual !== 'object' || Array.isArray(actual)) {
      out.push(`${path}: expected an object`);
      return out;
    }
    const a = actual as Record<string, unknown>;
    for (const [k, e] of Object.entries(expected)) {
      if (!(k in a)) out.push(`${path}.${k}: missing in the TypeScript record`);
      else compareRecord(a[k], e, `${path}.${k}`, out);
    }
    return out;
  }
  if (typeof expected === 'number') {
    if (typeof actual !== 'number') {
      out.push(`${path}: expected ${String(expected)}, got ${JSON.stringify(actual)}`);
      return out;
    }
    const last = path.split('.').pop() ?? '';
    const isLog = /log_nu|log_intercept/.test(path) || /^H(\[|$)/.test(last);
    try {
      if (Number.isInteger(expected) && EXACT_INT_PATH.test(last)) {
        if (actual !== expected) out.push(`${path}: expected exactly ${String(expected)}, got ${String(actual)}`);
      } else if (isLog) {
        closeAbs(actual, expected, LOG_NU_ATOL, path);
      } else {
        closeRel(actual, expected, RESAMPLED_GRID_RTOL, path);
      }
    } catch (error) {
      out.push((error as Error).message);
    }
    return out;
  }
  if (actual !== expected) out.push(`${path}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  return out;
}
