// Exact float64 helpers: math.fsum (Shewchuk), Python's round-half-even, float64 <-> BigInt
// rationals and a correctly rounded BigInt quotient, so the TypeScript mirror rounds where
// Python rounds and nowhere else.

/** Python math.fsum: the correctly rounded sum of the values (Shewchuk / msum). */
export function fsum(values: Iterable<number>): number {
  const partials: number[] = [];
  for (let x of values) {
    let i = 0;
    for (let y of partials) {
      if (Math.abs(x) < Math.abs(y)) {
        const t = x;
        x = y;
        y = t;
      }
      const hi = x + y;
      const lo = y - (hi - x);
      if (lo !== 0) {
        partials[i] = lo;
        i += 1;
      }
      x = hi;
    }
    partials.length = i;
    partials.push(x);
  }
  let n = partials.length;
  if (n === 0) return 0;
  n -= 1;
  let hi = partials[n] as number;
  let lo = 0;
  while (n > 0) {
    const x = hi;
    n -= 1;
    const y = partials[n] as number;
    hi = x + y;
    const yr = hi - x;
    lo = y - yr;
    if (lo !== 0) break;
  }
  if (n > 0 && ((lo < 0 && (partials[n - 1] as number) < 0) || (lo > 0 && (partials[n - 1] as number) > 0))) {
    const y = lo * 2;
    const x = hi + y;
    const yr = x - hi;
    if (y === yr) hi = x;
  }
  return hi;
}

/** Python's round() on a float: nearest integer, ties to even. */
export function roundHalfEven(x: number): number {
  const f = Math.floor(x);
  const diff = x - f;
  if (diff < 0.5) return f;
  if (diff > 0.5) return f + 1;
  return f % 2 === 0 ? f : f + 1;
}

/** frexp: (m, e) with x = m * 2^e and 0.5 <= |m| < 1, for finite nonzero x. */
export function frexpExponent(x: number): number {
  const a = Math.abs(x);
  let e = Math.floor(Math.log2(a)) + 1;
  // Settle the estimate exactly.
  while (a / pow2(e) >= 1) e += 1;
  while (a / pow2(e) < 0.5) e -= 1;
  return e;
}

/** 2^e as a float64, exact for -1074 <= e <= 1023. */
export function pow2(e: number): number {
  if (e > 1023) return Number.POSITIVE_INFINITY;
  if (e >= -1022) return 2 ** e;
  return 2 ** (e + 1022) * 2 ** -1022;
}

/** x * 2^e without an intermediate overflow or underflow of the power (math.ldexp). */
export function ldexp(x: number, e: number): number {
  let result = x;
  let rest = e;
  while (rest > 1000) {
    result *= pow2(1000);
    rest -= 1000;
  }
  while (rest < -1000) {
    result *= pow2(-1000);
    rest += 1000;
  }
  return result * pow2(rest);
}

/** A finite float64 as m * 2^e with integer m (float.as_integer_ratio up to reduction). */
export function decompose(x: number): { mantissa: bigint; exponent: number } {
  if (!Number.isFinite(x)) throw new RangeError('decompose needs a finite value');
  const view = new DataView(new ArrayBuffer(8));
  view.setFloat64(0, x);
  const hi = view.getUint32(0);
  const lo = view.getUint32(4);
  const negative = hi >>> 31 === 1;
  const exponentField = (hi >>> 20) & 0x7ff;
  let fraction = (BigInt(hi & 0xfffff) << 32n) | BigInt(lo);
  let exponent: number;
  if (exponentField === 0) {
    exponent = -1074;
  } else {
    fraction |= 1n << 52n;
    exponent = exponentField - 1075;
  }
  return { mantissa: negative ? -fraction : fraction, exponent };
}

function bitLength(n: bigint): number {
  return n === 0n ? 0 : n.toString(2).length;
}

/** The float64 nearest n / d (ties to even), as Python's int / int rounds it. */
export function divideToFloat(numerator: bigint, denominator: bigint): number {
  if (denominator === 0n) throw new RangeError('division by zero');
  if (numerator === 0n) return 0;
  const negative = numerator < 0n !== denominator < 0n;
  const n = numerator < 0n ? -numerator : numerator;
  const d = denominator < 0n ? -denominator : denominator;
  // Quotient with at least 65 significant bits plus a sticky bit, so Number() rounds it once.
  const shift = bitLength(n) - bitLength(d) - 66;
  let q: bigint;
  let r: bigint;
  if (shift >= 0) {
    const scaled = d << BigInt(shift);
    q = n / scaled;
    r = n % scaled;
  } else {
    const scaled = n << BigInt(-shift);
    q = scaled / d;
    r = scaled % d;
  }
  if (r !== 0n) q |= 1n;
  const value = ldexp(Number(q), shift);
  return negative ? -value : value;
}

/** An unreduced rational with a positive denominator. */
export interface Rational {
  readonly n: bigint;
  readonly d: bigint;
}

export function rationalOf(x: number): Rational {
  const { mantissa, exponent } = decompose(x);
  return exponent >= 0 ? { n: mantissa << BigInt(exponent), d: 1n } : { n: mantissa, d: 1n << BigInt(-exponent) };
}

export const rational = {
  add: (a: Rational, b: Rational): Rational => ({ n: a.n * b.d + b.n * a.d, d: a.d * b.d }),
  sub: (a: Rational, b: Rational): Rational => ({ n: a.n * b.d - b.n * a.d, d: a.d * b.d }),
  mul: (a: Rational, b: Rational): Rational => ({ n: a.n * b.n, d: a.d * b.d }),
  div: (a: Rational, b: Rational): Rational => {
    if (b.n === 0n) throw new RangeError('division by zero');
    return b.n < 0n ? { n: -a.n * b.d, d: a.d * -b.n } : { n: a.n * b.d, d: a.d * b.n };
  },
  toFloat: (a: Rational): number => divideToFloat(a.n, a.d),
};
