// The zod generator: committed output, refusal of JSON Schema it cannot translate faithfully, and
// the write/check command line used by make schema and make schema-check (PRD 8.9).
import { spawnSync } from 'node:child_process';
import {
  cpSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { jsonSchemaToZod } from 'json-schema-to-zod';
import { afterEach, describe, expect, it } from 'vitest';
import { z } from 'zod';

import {
  CONTRACTS,
  GENERATOR_VERSIONS,
  SchemaError,
  assertPortablePattern,
  assertSupported,
  checkModules,
  dereference,
  generateModule,
  writeModules,
  zodExpression,
} from '../scripts/generate-zod.ts';
import {
  GENERATED_DIR,
  GENERATOR_CASES_FILE,
  GENERATOR_SCRIPT,
  JSON_DIR,
  PACKAGE_DIR,
  SCHEMA_PY,
} from './paths.ts';

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type JsonObject = Record<string, Json>;

const scratchDirs: string[] = [];

function scratch(): string {
  const dir = mkdtempSync(join(tmpdir(), 'terraluna-schema-'));
  scratchDirs.push(dir);
  return dir;
}

afterEach(() => {
  for (const dir of scratchDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

function visible(dir: string): string[] {
  return readdirSync(dir)
    .filter((name) => !name.startsWith('.'))
    .sort();
}

function readSchema(file: string): JsonObject {
  return JSON.parse(readFileSync(join(JSON_DIR, file), 'utf8')) as JsonObject;
}

/** A copy of the committed JSON Schemas and generated modules in a scratch directory. */
function copyPackage(): { json: string; out: string } {
  const root = scratch();
  const json = join(root, 'json');
  const out = join(root, 'generated');
  cpSync(JSON_DIR, json, { recursive: true });
  cpSync(GENERATED_DIR, out, { recursive: true });
  return { json, out };
}

function run(...args: string[]): { status: number | null; stdout: string; stderr: string } {
  const result = spawnSync(process.execPath, [GENERATOR_SCRIPT, ...args], {
    cwd: PACKAGE_DIR,
    encoding: 'utf8',
  });
  return { status: result.status, stdout: result.stdout, stderr: result.stderr };
}

const STRING: JsonObject = { type: 'string' };

function object(properties: JsonObject, extra: JsonObject = {}): JsonObject {
  return {
    type: 'object',
    properties,
    required: Object.keys(properties),
    additionalProperties: false,
    ...extra,
  };
}

// An object whose `then` applies when `status` is DONE, as ProductRecord's does when
// verification_status is VERIFIED.
const IF_DONE: JsonObject = { properties: { status: { const: 'DONE' } }, required: ['status'] };
const THEN_LABEL: JsonObject = { properties: { label: STRING } };

function conditional(extra: JsonObject = {}): JsonObject {
  return object(
    {
      status: { type: 'string', enum: ['DONE', 'OPEN'] },
      label: { anyOf: [STRING, { type: 'null' }] },
    },
    { if: IF_DONE, then: THEN_LABEL, ...extra },
  );
}

interface GeneratorCase {
  readonly name: string;
  readonly valid: boolean;
  readonly value: Json;
}

interface GeneratorGroup {
  readonly name: string;
  readonly schema: JsonObject;
  readonly cases: readonly GeneratorCase[];
}

/** Synthetic schemas and documents whose verdicts the Python tests check with Draft 2020-12. */
function readGeneratorCases(): GeneratorGroup[] {
  return JSON.parse(readFileSync(GENERATOR_CASES_FILE, 'utf8')) as GeneratorGroup[];
}

/** Evaluate a generated zod expression, which is plain JavaScript over `z`. */
function compile(expression: string): z.ZodType {
  // The expression is the generator's output for a committed fixture, never outside input.
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const factory = new Function('z', `return ${expression};`) as (zod: typeof z) => z.ZodType;
  return factory(z);
}

/** The CONTRACTS of schema.py as [name, file name] pairs, read from its source. */
function pythonContracts(): string[][] {
  const text = readFileSync(SCHEMA_PY, 'utf8');
  const block = /^CONTRACTS: tuple\[Contract, \.\.\.\] = \(\n([\s\S]*?)^\)/m.exec(text)?.[1];
  if (block === undefined) {
    throw new Error(`no CONTRACTS tuple in ${SCHEMA_PY}`);
  }
  const entries = [...block.matchAll(/Contract\(\s*"([A-Za-z]+)",\s*"([a-z-]+\.schema\.json)"/g)];
  if (entries.length !== (block.match(/Contract\(/g) ?? []).length) {
    throw new Error(`a CONTRACTS entry in ${SCHEMA_PY} is not Contract("<Name>", "<file>", ...)`);
  }
  return entries.map((match) => [match[1] ?? '', match[2] ?? '']);
}

describe('contracts', () => {
  it('cover every committed JSON Schema', () => {
    expect(CONTRACTS.map((c) => c.json).sort()).toEqual(visible(JSON_DIR));
  });

  it('match the committed generated modules one to one', () => {
    expect(CONTRACTS.map((c) => c.module).sort()).toEqual(visible(GENERATED_DIR));
  });

  it('mirror the CONTRACTS of schema.py in names, file names and order', () => {
    expect(pythonContracts().length).toBeGreaterThanOrEqual(5);
    expect(CONTRACTS.map((c) => [c.typeName, c.json])).toEqual(pythonContracts());
  });

  it('name each module after its JSON Schema', () => {
    for (const contract of CONTRACTS) {
      expect(contract.module).toBe(contract.json.replace(/\.schema\.json$/, '.ts'));
    }
  });

  it('name the zod schema <Type>Schema and the inferred type <Type>', () => {
    for (const contract of CONTRACTS) {
      expect(contract.schemaName).toBe(`${contract.typeName}Schema`);
    }
    expect(CONTRACTS.map((c) => c.typeName)).toEqual([
      'BundleManifest',
      'GateRecord',
      'Validation',
      'ProductRecord',
      'Sources',
    ]);
  });
});

describe('generator versions', () => {
  it('are the exact versions pinned in package.json', () => {
    const pkg = JSON.parse(readFileSync(join(PACKAGE_DIR, 'package.json'), 'utf8')) as {
      devDependencies: Record<string, string>;
    };
    expect(GENERATOR_VERSIONS).toEqual({
      jsonSchemaToZod: pkg.devDependencies['json-schema-to-zod'],
      prettier: pkg.devDependencies['prettier'],
    });
    expect(GENERATOR_VERSIONS.jsonSchemaToZod).toMatch(/^\d+\.\d+\.\d+$/);
  });
});

describe('committed modules', () => {
  it('match the generator output', async () => {
    expect(await checkModules(JSON_DIR, GENERATED_DIR)).toEqual([]);
  });

  it.each(CONTRACTS.map((c) => [c.module, c] as const))(
    '%s starts with a do-not-edit header naming its source and generator',
    (_module, contract) => {
      const text = readFileSync(join(GENERATED_DIR, contract.module), 'utf8');
      const header = text.split('\n').slice(0, 3);
      expect(header).toEqual([
        `// Generated from json/${contract.json} by scripts/generate-zod.ts`,
        `// with json-schema-to-zod ${GENERATOR_VERSIONS.jsonSchemaToZod} and prettier ${GENERATOR_VERSIONS.prettier}.`,
        '// Do not edit this file: run `make schema` to regenerate it.',
      ]);
      expect(text).toContain(`export const ${contract.schemaName} = `);
      expect(text).toContain(
        `export type ${contract.typeName} = z.infer<typeof ${contract.schemaName}>;`,
      );
      expect(text.endsWith(';\n')).toBe(true);
    },
  );

  it('are generated deterministically', async () => {
    for (const contract of CONTRACTS) {
      const text = readFileSync(join(JSON_DIR, contract.json), 'utf8');
      expect(await generateModule(contract, text)).toBe(await generateModule(contract, text));
    }
  });

  // z.any() is the converter's fallback for a schema it does not understand; it accepts anything.
  it.each(CONTRACTS.map((c) => [c.module] as const))('%s never falls back to z.any()', (module) => {
    expect(readFileSync(join(GENERATED_DIR, module), 'utf8')).not.toContain('z.any(');
  });
});

describe('if/then and not translation', () => {
  const groups = readGeneratorCases();

  it('reads the synthetic schemas', () => {
    expect(groups.map((g) => g.name)).toEqual(['conditional', 'conditional-list']);
    expect(groups.flatMap((g) => g.cases).length).toBeGreaterThanOrEqual(20);
  });

  describe.each(groups.map((g) => [g.name, g] as const))('%s', (_group, group) => {
    it.each(group.cases.map((c) => [c.name, c] as const))(
      '%s gets its Draft 2020-12 verdict from zod',
      (_name, c) => {
        const result = compile(zodExpression(group.schema)).safeParse(c.value);
        expect(result.success, JSON.stringify(result.error?.issues)).toBe(c.valid);
        if (result.success) {
          expect(result.data).toStrictEqual(c.value);
        }
      },
    );
  });

  it('translates the conditional without the converter fallback z.any()', () => {
    for (const group of groups) {
      expect(zodExpression(group.schema)).not.toContain('z.any(');
    }
  });

  it.each([
    ['else', conditional({ else: THEN_LABEL })],
    ['oneOf', conditional({ then: { properties: { label: { oneOf: [STRING] } } } })],
    ['a format in the then branch', conditional({ then: { properties: { label: { type: 'string', format: 'uri' } } } })],
    ['allOf next to not', { type: 'string', not: { pattern: 'a' }, allOf: [STRING] }],
  ])('throws on %s instead of ignoring it', async (_label, schema) => {
    expect(() => zodExpression(schema)).toThrow(SchemaError);
    const contract = CONTRACTS[0];
    if (contract === undefined) {
      throw new Error('no contracts');
    }
    await expect(generateModule(contract, JSON.stringify(schema))).rejects.toThrow(SchemaError);
  });

  it('names the keyword it refuses', () => {
    expect(() => zodExpression(conditional({ else: THEN_LABEL }))).toThrow(
      '/: unsupported keyword "else" for type "object"',
    );
  });

  // Why the generator translates these keywords itself: the pinned converter would weaken both.
  it('documents that the pinned converter drops if/then without else', () => {
    const source = jsonSchemaToZod(
      conditional({ then: { properties: { label: { not: { pattern: '^[Xx]+$' } } } } }),
      { module: 'none', zodVersion: 4 },
    );
    expect(source).not.toContain('refine');
    expect(source).not.toContain('[Xx]');
  });

  it('documents that the pinned converter turns a schema with not into z.any()', () => {
    const source = jsonSchemaToZod(
      { type: 'string', pattern: '^[a-z]+$', not: { pattern: 'bad' } },
      { module: 'none', zodVersion: 4 },
    );
    expect(source).toContain('z.any()');
    expect(source).not.toContain('^[a-z]+$');
  });
});

describe('dereference', () => {
  it('inlines local $defs references and drops $defs', () => {
    const inner = object({ id: STRING });
    const resolved = dereference({
      $defs: { Inner: inner },
      type: 'array',
      items: { $ref: '#/$defs/Inner' },
    });
    expect(resolved).toEqual({ type: 'array', items: inner });
  });

  it('resolves references inside definitions', () => {
    const leaf = object({ id: STRING });
    const resolved = dereference({
      $defs: { Leaf: leaf, Branch: object({ leaf: { $ref: '#/$defs/Leaf' } }) },
      ...object({ branch: { $ref: '#/$defs/Branch' } }),
    });
    expect(resolved).toEqual(object({ branch: object({ leaf }) }));
  });

  it.each([
    ['a remote reference', { $ref: 'https://example.org/schema.json' }],
    ['a JSON pointer outside $defs', { $ref: '#/properties/a' }],
    ['a nested definition path', { $ref: '#/$defs/Inner/properties' }],
    ['a missing definition', { $ref: '#/$defs/Missing' }],
    ['keywords next to $ref', { $ref: '#/$defs/Inner', description: 'x' }],
  ])('refuses %s', (_label, ref) => {
    expect(() =>
      dereference({ $defs: { Inner: object({ id: STRING }) }, type: 'array', items: ref }),
    ).toThrow(SchemaError);
  });

  it('reads $ref only where a schema is expected, so a property may be named $ref', () => {
    const schema = object({ $ref: STRING, values: { type: 'string', enum: ['$ref'] } });
    expect(dereference(schema)).toEqual(schema);
    expect(() => {
      assertSupported(dereference(schema));
    }).not.toThrow();
  });

  it('refuses a recursive reference', () => {
    expect(() =>
      dereference({
        $defs: { Node: object({ next: { anyOf: [{ $ref: '#/$defs/Node' }, { type: 'null' }] } }) },
        $ref: '#/$defs/Node',
      }),
    ).toThrow(SchemaError);
  });
});

describe('assertSupported', () => {
  it.each(CONTRACTS.map((c) => [c.json] as const))('accepts %s', (file) => {
    expect(() => {
      assertSupported(dereference(readSchema(file)));
    }).not.toThrow();
  });

  it.each([
    ['a format', { type: 'string', format: 'date-time' }],
    ['oneOf', { oneOf: [STRING, { type: 'null' }] }],
    ['allOf', { allOf: [STRING] }],
    ['a default', { type: 'string', default: 'x' }],
    ['maxLength', { type: 'string', maxLength: 3 }],
    ['minLength above 1, which zod counts in UTF-16 code units', { type: 'string', minLength: 2 }],
    ['exclusiveMinimum', { type: 'integer', exclusiveMinimum: 0 }],
    ['the number type', { type: 'number' }],
    ['a list of types', { type: ['string', 'null'] }],
    ['an unknown type', { type: 'date' }],
    ['a non-string enum', { enum: ['A', 1] }],
    ['a non-string const', { const: 1 }],
    ['an object without additionalProperties', { type: 'object', properties: {}, required: [] }],
    ['an object that allows additional properties', object({ a: STRING }, { additionalProperties: true })],
    ['an object with a schema for additional properties', object({ a: STRING }, { additionalProperties: STRING })],
    ['an optional property', object({ a: STRING }, { required: [] })],
    ['a required key with no property', object({ a: STRING }, { required: ['a', 'b'] })],
    ['an object without properties', { type: 'object', additionalProperties: false }],
    ['an array without items', { type: 'array' }],
    ['a nested unsupported keyword', object({ a: { type: 'string', format: 'uri' } })],
    ['an unresolved $ref', { $ref: '#/$defs/Inner' }],
    ['a non-portable pattern', { type: 'string', pattern: '^\\d+$' }],
    ['an enum with minLength', { type: 'string', enum: ['', 'ok'], minLength: 1 }],
    ['an enum with a pattern', { type: 'string', enum: ['a', 'B'], pattern: '^[a-z]$' }],
    ['a const with minLength', { type: 'string', const: '', minLength: 1 }],
    ['a const with a pattern', { type: 'string', const: 'A', pattern: '^[a-z]$' }],
    ['an enum with a const', { type: 'string', enum: ['A', 'B'], const: 'C' }],
    ['a schema with neither type, anyOf nor not', { title: 'Nothing' }],
    // if/then: only a string-const condition on required properties of the same object, and a
    // then branch that constrains properties of that object.
    ['else next to if and then', conditional({ else: THEN_LABEL })],
    ['if without then', object({ status: STRING }, { if: IF_DONE })],
    ['then without if', object({ label: STRING }, { then: THEN_LABEL })],
    ['if and then on an array', { type: 'array', items: STRING, if: IF_DONE, then: THEN_LABEL }],
    ['if and then on a string', { type: 'string', if: IF_DONE, then: THEN_LABEL }],
    ['an if that is not an object', conditional({ if: true })],
    ['an if with a type', conditional({ if: { ...IF_DONE, type: 'object' } })],
    ['an if without required', conditional({ if: { properties: { status: { const: 'DONE' } } } })],
    ['an if with no condition', conditional({ if: { properties: {}, required: [] } })],
    [
      'an if whose required differs from its properties',
      conditional({ if: { ...IF_DONE, required: ['status', 'label'] } }),
    ],
    [
      'an if condition that is not a const',
      conditional({ if: { properties: { status: { enum: ['DONE'] } }, required: ['status'] } }),
    ],
    [
      'an if const that is not a string',
      conditional({ if: { properties: { status: { const: 1 } }, required: ['status'] } }),
    ],
    [
      'an if const next to another keyword',
      conditional({
        if: { properties: { status: { const: 'DONE', type: 'string' } }, required: ['status'] },
      }),
    ],
    [
      'an if on a property the object does not have',
      conditional({ if: { properties: { missing: { const: 'DONE' } }, required: ['missing'] } }),
    ],
    ['a then that is not an object', conditional({ then: true })],
    ['a then without properties', conditional({ then: {} })],
    ['a then with required', conditional({ then: { ...THEN_LABEL, required: ['label'] } })],
    ['a then with a type', conditional({ then: { ...THEN_LABEL, type: 'object' } })],
    ['a then with an empty properties map', conditional({ then: { properties: {} } })],
    [
      'a then property the object does not have',
      conditional({ then: { properties: { missing: STRING } } }),
    ],
    [
      'a then property with an unsupported keyword',
      conditional({ then: { properties: { label: { type: 'string', format: 'uri' } } } }),
    ],
    ['oneOf in a then property', conditional({ then: { properties: { label: { oneOf: [STRING] } } } })],
    // not: only a negated portable pattern, which JSON Schema searches for.
    ['not that is not an object', { type: 'string', not: true }],
    ['not without a pattern', { not: { type: 'string' } }],
    ['not with a keyword besides pattern', { type: 'string', not: { pattern: 'a', minLength: 1 } }],
    ['not with a non-portable pattern', { not: { pattern: '^\\d$' } }],
    ['not with a pattern that is not a string', { not: { pattern: 1 } }],
    ['not next to an enum', { type: 'string', enum: ['a', 'b'], not: { pattern: 'a' } }],
    ['not next to a const', { type: 'string', const: 'a', not: { pattern: 'a' } }],
    ['not next to anyOf', { anyOf: [STRING], not: { pattern: 'a' } }],
    ['not under an integer', { type: 'integer', not: { pattern: 'a' } }],
    ['not under an object', object({ a: STRING }, { not: { pattern: 'a' } })],
  ])('refuses %s', (_label, schema) => {
    expect(() => {
      assertSupported(schema);
    }).toThrow(SchemaError);
  });

  // The reason for the refusals above: json-schema-to-zod emits only the enum or const and drops
  // the other string keywords, so {"enum": ["", "ok"], "minLength": 1} would accept "".
  it.each([
    ['minLength', { type: 'string', enum: ['', 'ok'], minLength: 1 }, '.min('],
    ['pattern', { type: 'string', const: 'A', pattern: '^[a-z]$' }, '.regex('],
    ['const', { type: 'string', enum: ['A', 'B'], const: 'C' }, '"C"'],
  ])('documents that the pinned converter drops %s next to an enum or const', (_label, schema, kept) => {
    const source = jsonSchemaToZod(schema, { module: 'none', zodVersion: 4 });
    expect(source).not.toContain(kept);
  });

  it('accepts an enum or a const with only type and annotations', () => {
    expect(() => {
      assertSupported({ type: 'string', enum: ['A', 'B'], title: 'Mode' });
      assertSupported({ type: 'string', const: 'A', description: 'Fixed' });
    }).not.toThrow();
  });

  it.each([
    ['if/then with a string-const condition', conditional()],
    [
      'a then branch with a negated pattern',
      conditional({ then: { properties: { label: { not: { pattern: '^[Xx]+$' } } } } }),
    ],
    [
      'an if on two properties',
      conditional({
        if: {
          properties: { status: { const: 'DONE' }, label: { const: 'x' } },
          required: ['status', 'label'],
        },
      }),
    ],
    ['a string with a pattern and a negated pattern', { type: 'string', pattern: '^[a-z]+$', not: { pattern: 'bad' } }],
    ['a negated pattern alone', { not: { pattern: '^[Uu][Nn]$' } }],
    ['a negated pattern with annotations', { not: { pattern: 'bad' }, title: 'Not bad' }],
    ['a nullable negated pattern', { anyOf: [{ type: 'string', not: { pattern: 'bad' } }, { type: 'null' }] }],
  ])('accepts %s', (_label, schema) => {
    expect(() => {
      assertSupported(schema);
    }).not.toThrow();
  });

  it('checks the then branch with the same rules, naming its pointer', () => {
    expect(() => {
      assertSupported(conditional({ then: { properties: { label: { type: 'string', format: 'uri' } } } }));
    }).toThrow('/then/properties/label: unsupported keyword "format"');
  });

  it('names the JSON pointer of the refused keyword', () => {
    expect(() => {
      assertSupported(object({ files: { type: 'array', items: { type: 'string', format: 'uri' } } }));
    }).toThrow('/properties/files/items: unsupported keyword "format"');
  });
});

describe('assertPortablePattern', () => {
  const committed = CONTRACTS.flatMap((c) =>
    [...JSON.stringify(readSchema(c.json)).matchAll(/"pattern":("(?:[^"\\]|\\.)*")/g)].map(
      (match) => JSON.parse(match[1] ?? '""') as string,
    ),
  );

  it('finds the committed patterns', () => {
    expect(committed.length).toBeGreaterThanOrEqual(5);
  });

  it.each(committed.map((p) => [p] as const))('accepts %s', (pattern) => {
    expect(() => {
      assertPortablePattern(pattern);
    }).not.toThrow();
  });

  it.each([
    ['^\\d+$'],
    ['^\\w+$'],
    ['^\\s$'],
    ['^a\\b'],
    ['^\\p{L}$'],
    ['^\\u0041$'],
    ['^a.b$'],
    ['^[^/]+$'],
    ['^[]$'],
    ['^(?:a)$'],
    ['^(?i)a$'],
    ['^é$'],
    ['^a$\n'],
    ['^a\\'],
    ['^[a-z$'],
  ])('refuses %j', (pattern) => {
    expect(() => {
      assertPortablePattern(pattern);
    }).toThrow(SchemaError);
  });
});

describe('command line', () => {
  it('check passes on the committed modules', () => {
    const result = run('check', 'json', 'src/generated');
    expect(result.stderr).toBe('');
    expect(result.stdout).toBe('zod schemas match json: src/generated\n');
    expect(result.status).toBe(0);
  });

  it('check fails and shows the difference when a module was edited', () => {
    const { json, out } = copyPackage();
    const target = join(out, 'gate-record.ts');
    const original = readFileSync(target, 'utf8');
    const edited = original.replace("'NOT_APPLICABLE'", "'NOT_APPLICABLE_X'");
    expect(edited).not.toBe(original);
    writeFileSync(target, edited);
    const result = run('check', json, out);
    expect(result.status).toBe(1);
    expect(result.stdout).toContain('gate-record.ts: differs from the generated module');
    expect(result.stdout).toContain('NOT_APPLICABLE_X');
    expect(result.stderr).toContain('run make schema to regenerate');
  });

  it('check fails when the JSON Schema changed but the module was not regenerated', () => {
    const { json, out } = copyPackage();
    const target = join(json, 'gate-record.schema.json');
    const original = readFileSync(target, 'utf8');
    const edited = original.replace('"G6"', '"G7"');
    expect(edited).not.toBe(original);
    writeFileSync(target, edited);
    const result = run('check', json, out);
    expect(result.status).toBe(1);
    expect(result.stdout).toContain('gate-record.ts: differs from the generated module');
  });

  it('check fails on a missing module', () => {
    const { json, out } = copyPackage();
    rmSync(join(out, 'validation.ts'));
    const result = run('check', json, out);
    expect(result.status).toBe(1);
    expect(result.stdout).toContain('validation.ts: generated but missing from');
  });

  it('check fails on a module that is not generated', () => {
    const { json, out } = copyPackage();
    writeFileSync(join(out, 'extra.ts'), 'export {};\n');
    const result = run('check', json, out);
    expect(result.status).toBe(1);
    expect(result.stdout).toContain('extra.ts: in');
    expect(result.stdout).toContain('but not generated');
  });

  it('check fails on a JSON Schema that has no contract', () => {
    const { json, out } = copyPackage();
    writeFileSync(join(json, 'new-record.schema.json'), '{"type": "string"}\n');
    const result = run('check', json, out);
    expect(result.status).toBe(1);
    expect(result.stdout).toContain('new-record.schema.json: no zod contract');
  });

  it('check fails on a module that is not a regular file', () => {
    const { json, out } = copyPackage();
    rmSync(join(out, 'validation.ts'));
    mkdirSync(join(out, 'validation.ts'));
    const result = run('check', json, out);
    expect(result.status).toBe(1);
    expect(result.stdout).toContain('validation.ts: not a regular file');
  });

  it('check fails on a JSON Schema the generator cannot translate', () => {
    const { json, out } = copyPackage();
    const target = join(json, 'gate-record.schema.json');
    const schema = JSON.parse(readFileSync(target, 'utf8')) as JsonObject;
    writeFileSync(target, JSON.stringify({ ...schema, format: 'x' }));
    const result = run('check', json, out);
    expect(result.status).toBe(1);
    expect(result.stdout).toContain('gate-record.schema.json: /: unsupported keyword "format"');
  });

  it('check ignores hidden files such as .DS_Store', () => {
    const { json, out } = copyPackage();
    writeFileSync(join(out, '.DS_Store'), '');
    writeFileSync(join(json, '.DS_Store'), '');
    const result = run('check', json, out);
    expect(result.status).toBe(0);
  });

  it('check reports a missing directory', () => {
    const root = scratch();
    const result = run('check', JSON_DIR, join(root, 'missing'));
    expect(result.status).toBe(1);
    expect(result.stdout).toContain('not a directory');
  });

  it('write regenerates edited modules in place and deletes nothing', async () => {
    const { json, out } = copyPackage();
    writeFileSync(join(out, 'gate-record.ts'), 'edited\n');
    writeFileSync(join(out, 'notes.txt'), 'kept\n');
    const result = run('write', json, out);
    expect(result.status).toBe(0);
    expect(result.stdout).toBe(`wrote ${String(CONTRACTS.length)} zod modules to ${out}\n`);
    expect(readFileSync(join(out, 'notes.txt'), 'utf8')).toBe('kept\n');
    for (const contract of CONTRACTS) {
      expect(readFileSync(join(out, contract.module), 'utf8')).toBe(
        readFileSync(join(GENERATED_DIR, contract.module), 'utf8'),
      );
    }
    expect(await checkModules(json, out)).toEqual(['notes.txt: in ' + out + ' but not generated']);
  });

  it('write creates a missing output directory', () => {
    const root = scratch();
    const out = join(root, 'new', 'generated');
    expect(run('write', JSON_DIR, out).status).toBe(0);
    expect(visible(out)).toEqual(CONTRACTS.map((c) => c.module).sort());
  });

  it('write refuses a symlinked target and writes nothing', () => {
    const { json, out } = copyPackage();
    const elsewhere = join(scratch(), 'elsewhere.ts');
    writeFileSync(elsewhere, 'untouched\n');
    rmSync(join(out, 'validation.ts'));
    symlinkSync(elsewhere, join(out, 'validation.ts'));
    writeFileSync(join(out, 'gate-record.ts'), 'edited\n');
    const result = run('write', json, out);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain('validation.ts is not a regular file, so nothing was written');
    expect(readFileSync(elsewhere, 'utf8')).toBe('untouched\n');
    expect(readFileSync(join(out, 'gate-record.ts'), 'utf8')).toBe('edited\n');
  });

  it('write refuses a JSON Schema the generator cannot translate and writes nothing', () => {
    const { json, out } = copyPackage();
    const target = join(json, 'gate-record.schema.json');
    const schema = JSON.parse(readFileSync(target, 'utf8')) as JsonObject;
    writeFileSync(target, JSON.stringify({ ...schema, format: 'x' }));
    writeFileSync(join(out, 'validation.ts'), 'edited\n');
    const result = run('write', json, out);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain('unsupported keyword "format"');
    expect(readFileSync(join(out, 'validation.ts'), 'utf8')).toBe('edited\n');
  });

  it('write refuses a JSON Schema that has no contract and writes nothing', () => {
    const { json, out } = copyPackage();
    writeFileSync(join(json, 'new-record.schema.json'), '{"type": "string"}\n');
    writeFileSync(join(out, 'validation.ts'), 'edited\n');
    const result = run('write', json, out);
    expect(result.status).toBe(1);
    expect(result.stderr).toContain('new-record.schema.json: no zod contract');
    expect(readFileSync(join(out, 'validation.ts'), 'utf8')).toBe('edited\n');
  });

  it.each([[[]], [['check']], [['write', 'json']], [['lint', 'json', 'src/generated']]])(
    'rejects the arguments %j with exit code 2',
    (args) => {
      const result = run(...args);
      expect(result.status).toBe(2);
      expect(result.stderr).toContain('usage: node scripts/generate-zod.ts write|check');
    },
  );
});

describe('writeModules', () => {
  it('returns the written paths in contract order', async () => {
    const out = join(scratch(), 'generated');
    expect(await writeModules(JSON_DIR, out)).toEqual(CONTRACTS.map((c) => join(out, c.module)));
  });
});
