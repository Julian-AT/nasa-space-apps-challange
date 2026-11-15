// Paths shared by the tests. The fixtures live at the repository root because the Python tests
// read the same files (PRD 8.8: the same fixtures drive both languages).
import { fileURLToPath } from 'node:url';

export const PACKAGE_DIR = fileURLToPath(new URL('..', import.meta.url));
export const JSON_DIR = fileURLToPath(new URL('../json', import.meta.url));
export const GENERATED_DIR = fileURLToPath(new URL('../src/generated', import.meta.url));
export const GENERATOR_SCRIPT = fileURLToPath(new URL('../scripts/generate-zod.ts', import.meta.url));
export const CASES_FILE = fileURLToPath(
  new URL('../../../fixtures/schema-cases/cases.json', import.meta.url),
);
export const MOCK_BUNDLE_DIR = fileURLToPath(
  new URL('../../../fixtures/mock-bundle', import.meta.url),
);
export const GENERATOR_CASES_FILE = fileURLToPath(
  new URL('../../../fixtures/schema-cases/generator-cases.json', import.meta.url),
);
// The pydantic records whose CONTRACTS list the generator's CONTRACTS mirror.
export const SCHEMA_PY = fileURLToPath(
  new URL('../../../pipeline/src/terraluna_pipeline/schema.py', import.meta.url),
);
