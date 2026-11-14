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
