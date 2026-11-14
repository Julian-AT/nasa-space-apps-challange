// The rule for data the API cannot serve yet (PRD 8.5, 8.12, D23). It covers matches, windows,
// compare and model (T15-05) and is the rule GET /targets/{id} (T15-04) already follows:
//
//   404 Not Found (NOT_IN_BUNDLE_STATUS): the bundle does not hold the requested record or file.
//       manifest.json decides: it does not list the id, lists no file that could hold the record
//       of that id, or names no model. The detail says what is not in bundle <bundle_version>.
//   501 Not Implemented (NOT_SERVED_STATUS): the bundle holds a file the record could come from,
//       but @terraluna/schema has no schema for its records, so the API serves nothing from it.
//       The detail says the file is in bundle <bundle_version> and which schema is missing.
//
// An id manifest.targets does not list is therefore a 404 only where manifest.json rules it out.
// When it lists windows/<body>.json, which could hold any window, a window or comparison id is a
// 501, since telling an unknown id from a known one would mean parsing that file (orchestrator
// decision on T15-05 AC0). t15b-api replaces these 501s with real 404 or 200 answers once
// @terraluna/schema has WindowRecord and ComparisonRecord.
//
// So the status follows from manifest.json alone, which is checked again with its generated schema
// first: a manifest that fails it is a 500 problem, never a 404 or 501. No file without a schema is
// parsed, nothing is passed through unvalidated, and nothing is computed in place of a missing
// record: missing evidence is never zero, and never a default (PRD 8.12). Both answers are expected
// problems (ProblemError), never logged and never cached (problem.ts).
import type { BundleManifest, Validation } from '@terraluna/schema';

import { MANIFEST, type PulledBundle, VALIDATION } from '../../lib/bundle.ts';
import {
  MOCK_REFUSED_STATUS,
  mockRefusedResponse,
  PROBLEM_MEDIA_TYPE,
  ProblemError,
  ProblemSchema,
  VALIDATION_STATUS,
} from './problem.ts';
import { BundleManifestRecord, parseBundleFile, ValidationRecord } from './records.ts';

export const NOT_IN_BUNDLE_STATUS = 404;
export const NOT_SERVED_STATUS = 501;

/** The answer when the bundle does not hold the record or file: the detail says what is not in it. */
export function notInBundle(detail: string): ProblemError {
  return new ProblemError(NOT_IN_BUNDLE_STATUS, detail);
}

/** The answer when the bundle holds the file, but its records have no schema to be served with. */
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

/** The bundle paths manifest.json lists that pass the test, in manifest order. */
export function listedPaths(manifest: BundleManifest, test: (path: string) => boolean): string[] {
  return manifest.files.map((file) => file.path).filter(test);
}

/** True when manifest.json lists the bundle path. */
export function isListed(manifest: BundleManifest, path: string): boolean {
  return manifest.files.some((file) => file.path === path);
}

const problemContent = { [PROBLEM_MEDIA_TYPE]: { schema: ProblemSchema } };

/** The problem responses of an unavailable-data route, for the OpenAPI document. */
export function unavailableResponses(descriptions: {
  /** Why a request fails validation; leave out for a route without parameters. */
  readonly invalid?: string;
  readonly notInBundle: string;
  readonly notServed: string;
}) {
  const responses = {
    [NOT_IN_BUNDLE_STATUS]: { description: descriptions.notInBundle, content: problemContent },
    500: {
      description: 'A bundle file is missing or invalid, or an unexpected error occurred.',
      content: problemContent,
    },
    [NOT_SERVED_STATUS]: { description: descriptions.notServed, content: problemContent },
    [MOCK_REFUSED_STATUS]: mockRefusedResponse,
  };
  if (descriptions.invalid === undefined) return responses;
  return {
    [VALIDATION_STATUS]: { description: descriptions.invalid, content: problemContent },
    ...responses,
  };
}
