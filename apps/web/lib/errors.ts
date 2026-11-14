/** A bundle that bundle:pull or next build refuses. The message says why and what to do. */
export class BundleError extends Error {
  override name = 'BundleError';
}
