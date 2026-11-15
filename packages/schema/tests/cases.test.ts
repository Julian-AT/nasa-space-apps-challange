// Every shared schema case (fixtures/schema-cases/cases.json) must get its stated verdict from the
// generated zod schemas, as it does from pydantic and from the JSON Schema in the Python tests
// (PRD 8.4, 8.8, 8.12).
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';
import type { ZodType } from 'zod';

import {
  BundleManifestSchema,
  ComparisonRecordSchema,
  ComparisonsSchema,
  FingerprintRecordSchema,
  GateRecordSchema,
  ProductRecordSchema,
  SourcesSchema,
  ValidationSchema,
  WindowRecordSchema,
  WindowsSchema,
} from '../src/index.ts';
import { CONTRACTS } from '../scripts/generate-zod.ts';
import { CASES_FILE, MOCK_BUNDLE_DIR } from './paths.ts';

interface SchemaCase {
  readonly name: string;
  readonly schema: string;
  readonly valid: boolean;
  readonly value: unknown;
}

interface NumberLiteral {
  readonly source: string;
  readonly value: number;
}

const SCHEMAS: ReadonlyMap<string, ZodType> = new Map<string, ZodType>([
  ['BundleManifest', BundleManifestSchema],
  ['GateRecord', GateRecordSchema],
  ['Validation', ValidationSchema],
  ['ProductRecord', ProductRecordSchema],
  ['Sources', SourcesSchema],
  ['WindowRecord', WindowRecordSchema],
  ['FingerprintRecord', FingerprintRecordSchema],
  ['Windows', WindowsSchema],
  ['ComparisonRecord', ComparisonRecordSchema],
  ['Comparisons', ComparisonsSchema],
]);

const CASE_KEYS = ['name', 'schema', 'valid', 'value'];

// JSON.parse passes each primitive's source text to the reviver (V8, Node 22). TypeScript's lib
// does not declare that argument yet.
type SourceReviver = (key: string, value: unknown, context?: { source?: string }) => unknown;
const parseWithReviver = JSON.parse as (text: string, reviver: SourceReviver) => unknown;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readCases(): { cases: SchemaCase[]; numbers: NumberLiteral[] } {
  const numbers: NumberLiteral[] = [];
  const parsed = parseWithReviver(readFileSync(CASES_FILE, 'utf8'), (_key, value, context) => {
    if (typeof value === 'number') {
      numbers.push({ source: context?.source ?? '', value });
    }
    return value;
  });
  if (!Array.isArray(parsed)) {
    throw new Error(`${CASES_FILE} is not a JSON array`);
  }
  const cases = parsed.map((entry: unknown, index): SchemaCase => {
    if (
      !isRecord(entry) ||
      Object.keys(entry).sort().join() !== CASE_KEYS.join() ||
      typeof entry['name'] !== 'string' ||
      typeof entry['schema'] !== 'string' ||
      typeof entry['valid'] !== 'boolean'
    ) {
      throw new Error(`case ${String(index)} is not {name, schema, valid, value}`);
    }
    return {
      name: entry['name'],
      schema: entry['schema'],
      valid: entry['valid'],
      value: entry['value'],
    };
  });
  return { cases, numbers };
}

const { cases, numbers } = readCases();

function readMockFile(name: string): unknown {
  return JSON.parse(readFileSync(`${MOCK_BUNDLE_DIR}/${name}`, 'utf8'));
}

describe('shared schema cases', () => {
  it('run every generated contract', () => {
    expect([...SCHEMAS.keys()].sort()).toEqual(CONTRACTS.map((c) => c.typeName).sort());
  });

  it('give each schema accepted and rejected values', () => {
    for (const schema of SCHEMAS.keys()) {
      const verdicts = new Set(cases.filter((c) => c.schema === schema).map((c) => c.valid));
      expect([schema, [...verdicts].sort()]).toEqual([schema, [false, true]]);
    }
    expect(cases.filter((c) => !SCHEMAS.has(c.schema)).map((c) => c.name)).toEqual([]);
  });

  it('have unique lower-case kebab-case names', () => {
    const names = cases.map((c) => c.name);
    expect(new Set(names).size).toBe(names.length);
    expect(names.filter((name) => !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(name))).toEqual([]);
  });

  it('write every number so that JavaScript reads the value that was written', () => {
    expect(numbers.length).toBeGreaterThan(0);
    const misread = numbers.filter(({ source, value }) => {
      if (/[.eE]/.test(source)) {
        // A float must have a fractional part: JavaScript cannot tell 1.0 from 1.
        return Number.isInteger(value);
      }
      // An integer must stay within ±2^53 and be read exactly.
      return Math.abs(value) > 2 ** 53 || BigInt(source) !== BigInt(value);
    });
    expect(misread).toEqual([]);
  });

  it('use the committed mock bundle for manifest-mock and validation-mock', () => {
    const byName = new Map(cases.map((c) => [c.name, c.value]));
    expect(byName.get('manifest-mock')).toEqual(readMockFile('manifest.json'));
    expect(byName.get('validation-mock')).toEqual(readMockFile('validation.json'));
  });

  it.each(cases.map((c) => [c.name, c] as const))('%s gets its verdict from zod', (_name, c) => {
    const schema = SCHEMAS.get(c.schema);
    if (schema === undefined) {
      throw new Error(`unknown schema ${c.schema}`);
    }
    const result = schema.safeParse(c.value);
    expect(result.success, JSON.stringify(result.error?.issues)).toBe(c.valid);
    if (result.success) {
      // Accepting a value must not change it: no defaults, coercion or stripped keys.
      expect(result.data).toStrictEqual(c.value);
    }
  });
});
