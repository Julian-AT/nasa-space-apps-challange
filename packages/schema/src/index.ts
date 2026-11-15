// @terraluna/schema: zod 4 schemas and inferred types for the release bundle (PRD 8.4, 8.12, D23).
// The modules in ./generated are generated from ../json/*.schema.json; run `make schema`.
export { BundleManifestSchema, type BundleManifest } from './generated/bundle-manifest.ts';
export { GateRecordSchema, type GateRecord } from './generated/gate-record.ts';
export { ValidationSchema, type Validation } from './generated/validation.ts';
export { ProductRecordSchema, type ProductRecord } from './generated/product-record.ts';
export { SourcesSchema, type Sources } from './generated/sources.ts';
export { WindowRecordSchema, type WindowRecord } from './generated/window-record.ts';
export { FingerprintRecordSchema, type FingerprintRecord } from './generated/fingerprint-record.ts';
export { WindowsSchema, type Windows } from './generated/windows.ts';
export { ComparisonRecordSchema, type ComparisonRecord } from './generated/comparison-record.ts';
export { ComparisonsSchema, type Comparisons } from './generated/comparisons.ts';
