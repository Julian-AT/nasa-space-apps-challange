// Compile-time checks of the inferred types. `tsc --noEmit` (the typecheck task in make check)
// fails if a generated type drifts from the PRD 8.4 / 8.12 record shapes; Vitest does not run
// this file.
import { expectTypeOf } from 'vitest';
import type { z } from 'zod';

import type {
  BundleManifest,
  BundleManifestSchema,
  GateRecord,
  GateRecordSchema,
  ProductRecord,
  ProductRecordSchema,
  Sources,
  SourcesSchema,
  Validation,
  ValidationSchema,
} from '../src/index.ts';

type GateStatus = 'NOT_RUN' | 'PASS' | 'FAIL' | 'PARTIAL' | 'NOT_APPLICABLE';

expectTypeOf<BundleManifest>().toEqualTypeOf<{
  bundle_version: string;
  schema_version: string;
  mock: boolean;
  run_id: string | null;
  git_sha: string | null;
  created_utc: string;
  shipping_mode: 'A' | 'B' | 'C' | null;
  targets: string[];
  model: { id: string; status: 'EXPERIMENTAL'; g5: GateStatus } | null;
  files: { path: string; sha256: string; bytes: number }[];
}>();

expectTypeOf<GateRecord>().toEqualTypeOf<{
  gate_id: 'G0' | 'G1' | 'G2' | 'G3' | 'G4' | 'G5' | 'G6';
  status: GateStatus;
  evidence_path: string | null;
  run_id: string | null;
  scope: string | null;
  result: string | null;
  consequence: string | null;
}>();

expectTypeOf<Validation>().toEqualTypeOf<GateRecord[]>();

// The VERIFIED condition (PRD 8.12) is a refinement, so it does not narrow the inferred type.
expectTypeOf<ProductRecord>().toEqualTypeOf<{
  product_id: string;
  provider: string;
  version: string;
  source_url: string | null;
  local_path: string | null;
  checksum: string | null;
  body: 'moon' | 'mars' | 'earth';
  acquisition_time: string | null;
  frequency_or_wavelength: string | null;
  transmit_receive_convention: string | null;
  units: string;
  calibration_type: string | null;
  native_grid: string | null;
  effective_resolution: string | null;
  crs_and_frame: string;
  incidence_source: string | null;
  qa_definition: string | null;
  license_or_attribution: string;
  verification_status: 'NOT_CHECKED' | 'VERIFIED' | 'PARTIAL' | 'BLOCKED' | 'FAILED';
}>();

expectTypeOf<Sources>().toEqualTypeOf<ProductRecord[]>();

// Parsing transforms nothing, so a schema's input type is its output type.
expectTypeOf<z.input<typeof BundleManifestSchema>>().toEqualTypeOf<BundleManifest>();
expectTypeOf<z.input<typeof GateRecordSchema>>().toEqualTypeOf<GateRecord>();
expectTypeOf<z.input<typeof ValidationSchema>>().toEqualTypeOf<Validation>();
expectTypeOf<z.input<typeof ProductRecordSchema>>().toEqualTypeOf<ProductRecord>();
expectTypeOf<z.input<typeof SourcesSchema>>().toEqualTypeOf<Sources>();
