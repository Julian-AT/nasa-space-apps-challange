// The bundle records the API serves, as @terraluna/schema generates them from the Python models
// (PRD 8.5, D23). Each is the generated schema itself under a component name for the OpenAPI
// document, so no field is redeclared and the routes serve exactly the generated contract.
//
// zod 4 copies prototype methods onto a schema when the schema is constructed, so a generated
// schema built before @hono/zod-openapi extended zod has no .openapi method; which module loads
// first depends on the importer. clone() constructs a new instance over the same definition after
// the extension (this module imports @hono/zod-openapi), and .openapi(name) does the same again.
import { z } from '@hono/zod-openapi';
import { BundleManifestSchema, ValidationSchema } from '@terraluna/schema';

import { BundleFileError } from '../../lib/bundle.ts';

/** manifest.json (PRD 8.4). */
export const BundleManifestRecord = BundleManifestSchema.clone().openapi('BundleManifest');

/** validation.json: the GateRecords (PRD 8.12). */
export const ValidationRecord = ValidationSchema.clone().openapi('Validation');

/**
 * A bundle file's record, checked again with its schema before it is served, so a reader that
 * returns anything else cannot pass it through. A mismatch is the file being invalid: the API
 * answers with a 500 problem naming the bundle path, and the schema issues go to the log only.
 */
export function parseBundleFile<S extends z.ZodType>(
  schema: S,
  value: unknown,
  path: string,
): z.output<S> {
  const result = schema.safeParse(value);
  if (!result.success) {
    throw new BundleFileError(
      path,
      'invalid',
      `${path} as the reader returned it does not match its schema:\n${z.prettifyError(result.error)}`,
    );
  }
  return result.data;
}
