// The bundle records the API serves, as @terraluna/schema generates them from the Python models
// (PRD 8.5, D23). Each is the generated schema itself under a component name for the OpenAPI
// document, so no field is redeclared and the routes serve exactly the generated contract.
//
// zod 4 copies prototype methods onto a schema when the schema is constructed, so a generated
// schema built before @hono/zod-openapi extended zod has no .openapi method; which module loads
// first depends on the importer. clone() constructs a new instance over the same definition after
// the extension (this module imports @hono/zod-openapi), and .openapi(name) does the same again.
import { z } from '@hono/zod-openapi';
import {
  ComparisonsSchema,
  MetricsSchema,
  ModelRecordSchema,
  TargetsSchema,
  ValidationSchema,
  WindowsSchema,
  BundleManifestSchema,
} from '@terraluna/schema';

import { BundleFileError } from '../../lib/bundle.ts';

/** manifest.json (PRD 8.4). */
export const BundleManifestRecord = BundleManifestSchema.clone().openapi('BundleManifest');

/** validation.json: the GateRecords (PRD 8.12). */
export const ValidationRecord = ValidationSchema.clone().openapi('Validation');

/** One entry of targets.json (PRD 8.4, 8.12). */
export const TargetRecordSchema = TargetsSchema.element.clone().openapi('TargetRecord');

/** One row of windows/<body>.json: a window, its fingerprints and its eligibility. */
export const WindowEntrySchema = WindowsSchema.element.clone().openapi('WindowEntry');

/** The four stored fingerprints of a window row. */
export const FingerprintRecordSchema = WindowsSchema.element.shape.radar.element
  .clone()
  .openapi('FingerprintRecord');

/** One record of comparisons/<target_id>.json. */
export const ComparisonRecordSchema = ComparisonsSchema.element.clone().openapi('ComparisonRecord');

/** One family's stored distance. */
export const DistanceEntrySchema = ComparisonsSchema.element.shape.distances_by_family.shape.terrain_relief
  .clone()
  .openapi('DistanceEntry');

/** The model's stored distance (Predicted). */
export const ModelDistanceEntrySchema = ComparisonsSchema.element.shape.distances_by_family.shape.model_embedding
  .clone()
  .openapi('ModelEmbeddingDistanceEntry');

/** model/model_card.json. */
export const ModelRecordResponseSchema = ModelRecordSchema.clone().openapi('ModelRecord');

/** The G5 summary of report/metrics.json. */
export const G5SummarySchema = MetricsSchema.shape.g5.clone().openapi('G5Summary');

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
