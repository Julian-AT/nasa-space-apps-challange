// Shared pieces of the record routes (PRD 8.5, 8.12, D23): reading the validated manifest and
// validation.json, and the problem responses of the OpenAPI document.
//
// A record the bundle does not hold is a 404 problem that names what is missing in bundle
// <bundle_version>; nothing is served in its place, and missing evidence is never zero. A record
// the API cannot produce yet (a comparison computed on request, which waits for @terraluna/core)
// is a 501 problem. Expected problems are ProblemError values, never logged and never cached
// (problem.ts). A bundle file that fails its schema is a 500 problem, never a 404.
import type { BundleManifest, Validation } from '@terraluna/schema';

import { MANIFEST, type PulledBundle, VALIDATION } from '../../lib/bundle.ts';
import {
  MOCK_REFUSED_STATUS,
  mockRefusedResponse,
  PROBLEM_MEDIA_TYPE,
  ProblemError,
  ProblemSchema,
  type ProblemStatus,
  VALIDATION_STATUS,
} from './problem.ts';
import { BundleManifestRecord, parseBundleFile, ValidationRecord } from './records.ts';

export const NOT_IN_BUNDLE_STATUS = 404;
export const NOT_SERVED_STATUS = 501;

/** The answer when the bundle does not hold the record or file: the detail says what is not in it. */
export function notInBundle(detail: string): ProblemError {
  return new ProblemError(NOT_IN_BUNDLE_STATUS, detail);
}

/** The answer when the API cannot produce the record yet. */
export function notServedYet(detail: string): ProblemError {
  return new ProblemError(NOT_SERVED_STATUS, detail);
}

/** manifest.json as the reader returned it, checked again with its generated schema. */
export function readManifest(bundle: PulledBundle): BundleManifest {
  return parseBundleFile(BundleManifestRecord, bundle.manifest, MANIFEST);
}

/** validation.json as the reader returned it, checked again with its generated schema. */
export function readValidation(bundle: PulledBundle): Validation {
  return parseBundleFile(ValidationRecord, bundle.validation, VALIDATION);
}

/** The status of the single G5 record in validation.json, or null when it has none or several. */
export function g5Status(validation: Validation): Validation[number]['status'] | null {
  const g5 = validation.filter((record) => record.gate_id === 'G5');
  return g5.length === 1 && g5[0] !== undefined ? g5[0].status : null;
}

const problemContent = { [PROBLEM_MEDIA_TYPE]: { schema: ProblemSchema } };

/**
 * The problem responses of a record route for the OpenAPI document: 500 and 503 always, 400 when
 * `invalid` is given, and each further status with its description.
 */
export function problemResponses(
  descriptions: Readonly<Partial<Record<ProblemStatus, string>>> & { readonly invalid?: string },
) {
  const { invalid, ...others } = descriptions;
  const responses: Record<number, { description: string; content: typeof problemContent }> = {
    500: {
      description: 'A bundle file is missing or invalid, or an unexpected error occurred.',
      content: problemContent,
    },
  };
  for (const [status, description] of Object.entries(others)) {
    responses[Number(status)] = { description, content: problemContent };
  }
  if (invalid !== undefined) {
    responses[VALIDATION_STATUS] = { description: invalid, content: problemContent };
  }
  return { ...responses, [MOCK_REFUSED_STATUS]: mockRefusedResponse };
}
