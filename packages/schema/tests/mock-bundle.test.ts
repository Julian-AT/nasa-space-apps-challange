// The committed mock bundle (fixtures/mock-bundle, PRD 8.4) must parse with the generated zod
// schemas, because web work starts on it (D23).
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { BundleManifestSchema, GateRecordSchema, ValidationSchema } from '../src/index.ts';
import { MOCK_BUNDLE_DIR } from './paths.ts';

function readMockFile(name: string): unknown {
  return JSON.parse(readFileSync(`${MOCK_BUNDLE_DIR}/${name}`, 'utf8'));
}

describe('mock bundle', () => {
  it('manifest.json parses as a BundleManifest labelled mock', () => {
    const manifest = BundleManifestSchema.parse(readMockFile('manifest.json'));
    expect(manifest.mock).toBe(true);
    expect(manifest.files.map((file) => file.path)).toContain('validation.json');
  });

  it('validation.json parses as the validation file and each entry as a GateRecord', () => {
    const raw = readMockFile('validation.json');
    const validation = ValidationSchema.parse(raw);
    expect(validation.length).toBeGreaterThan(0);
    for (const record of validation) {
      expect(GateRecordSchema.parse(record)).toStrictEqual(record);
    }
    expect(validation).toStrictEqual(raw);
  });
});
