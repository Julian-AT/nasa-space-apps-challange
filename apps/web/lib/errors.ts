/** A bundle that bundle:pull, next build or the API refuses. The message says why and what to do. */
export class BundleError extends Error {
  override name = 'BundleError';
}

/**
 * Why one bundle file is refused. `invalid` covers bytes that are not strict JSON and records that
 * do not match their @terraluna/schema schema.
 */
export type BundleFileProblem =
  | 'missing'
  | 'not_regular_file'
  | 'not_listed'
  | 'listed_twice'
  | 'bytes_mismatch'
  | 'sha256_mismatch'
  | 'invalid';

/** A bundle file that is missing or invalid. `path` is its path inside the bundle. */
export class BundleFileError extends BundleError {
  override name = 'BundleFileError';
  readonly path: string;
  readonly reason: BundleFileProblem;

  constructor(path: string, reason: BundleFileProblem, message: string) {
    super(message);
    this.path = path;
    this.reason = reason;
  }
}

/** A mock bundle refused because VERCEL_ENV=production (PRD 8.4, 12 end-to-end checks). */
export class MockBundleError extends BundleError {
  override name = 'MockBundleError';
}
