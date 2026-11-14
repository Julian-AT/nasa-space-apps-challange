// Generates the zod 4 schemas in src/generated/ from the committed JSON Schemas in json/, which
// pipeline/src/terraluna_pipeline/schema.py generates from the pydantic records (PRD 8.3, 8.9, D23).
//
//   node scripts/generate-zod.ts write <json-dir> <out-dir>   regenerate the modules in place
//   node scripts/generate-zod.ts check <json-dir> <out-dir>   fail, with a diff, on any drift
//
// json-schema-to-zod translates each schema after local $defs references are inlined. Before
// that, assertSupported refuses every keyword outside the small set that the generator
// translates with the same verdicts as pydantic and JSON Schema, so a new keyword in schema.py
// fails here instead of being dropped from the zod output. The converter mistranslates `not`
// and ignores `if`/`then` without `else`, so translateOverride translates those two itself.
import { lstatSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import { diff } from 'node:util';

import { jsonSchemaToZod, parseSchema } from 'json-schema-to-zod';
import type { JsonSchemaObject, Refs } from 'json-schema-to-zod';
import { format, version as prettierVersion } from 'prettier';
import type { Options as PrettierOptions } from 'prettier';

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type JsonObject = { [key: string]: Json };

/** One JSON Schema file and the zod module generated from it. */
export interface Contract {
  readonly json: string;
  readonly module: string;
  readonly schemaName: string;
  readonly typeName: string;
}

/** Mirrors CONTRACTS in schema.py: one module per committed JSON Schema. */
export const CONTRACTS: readonly Contract[] = [
  {
    json: 'bundle-manifest.schema.json',
    module: 'bundle-manifest.ts',
    schemaName: 'BundleManifestSchema',
    typeName: 'BundleManifest',
  },
  {
    json: 'gate-record.schema.json',
    module: 'gate-record.ts',
    schemaName: 'GateRecordSchema',
    typeName: 'GateRecord',
  },
  {
    json: 'validation.schema.json',
    module: 'validation.ts',
    schemaName: 'ValidationSchema',
    typeName: 'Validation',
  },
  {
    json: 'product-record.schema.json',
    module: 'product-record.ts',
    schemaName: 'ProductRecordSchema',
    typeName: 'ProductRecord',
  },
  {
    json: 'sources.schema.json',
    module: 'sources.ts',
    schemaName: 'SourcesSchema',
    typeName: 'Sources',
  },
];

/** The JSON Schema could not be translated faithfully, or is missing or malformed. */
export class SchemaError extends Error {
  override name = 'SchemaError';
}

/** A write target is not a plain file or directory, so nothing was written. */
export class WriteRefused extends Error {
  override name = 'WriteRefused';
}

function installedVersion(specifier: string): string {
  let dir = dirname(createRequire(import.meta.url).resolve(specifier));
  for (;;) {
    try {
      const pkg = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8')) as {
        name?: unknown;
        version?: unknown;
      };
      if (pkg.name === specifier && typeof pkg.version === 'string') {
        return pkg.version;
      }
    } catch {
      // No package.json at this level; keep walking up.
    }
    const parent = dirname(dir);
    if (parent === dir) {
      throw new Error(`cannot find the package.json of ${specifier}`);
    }
    dir = parent;
  }
}

/** The tool versions that shape the output; both are named in every module header. */
export const GENERATOR_VERSIONS = {
  jsonSchemaToZod: installedVersion('json-schema-to-zod'),
  prettier: prettierVersion,
} as const;

// Every option is explicit, and format() reads no configuration file, so the output depends
// only on the input and the pinned versions.
const PRETTIER_OPTIONS: PrettierOptions = {
  parser: 'typescript',
  printWidth: 100,
  tabWidth: 2,
  useTabs: false,
  semi: true,
  singleQuote: true,
  quoteProps: 'as-needed',
  trailingComma: 'all',
  bracketSpacing: true,
  arrowParens: 'always',
  endOfLine: 'lf',
};

const DRAFT_2020_12 = 'https://json-schema.org/draft/2020-12/schema';

function isObject(value: Json | undefined): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function pointerToken(key: string): string {
  return key.replaceAll('~', '~0').replaceAll('/', '~1');
}

function at(pointer: string): string {
  return pointer === '' ? '/' : pointer;
}

// --- $ref resolution ------------------------------------------------------------------------

/**
 * Inline every `{"$ref": "#/$defs/<Name>"}` and drop the root `$defs`.
 *
 * References are read only where a subschema is expected: under `properties`, `items` and
 * `anyOf`, the keywords assertSupported accepts. Elsewhere, such as in a `properties` map or an
 * `enum`, `$ref` is plain data. pydantic emits only local references with no sibling keywords,
 * so anything else is refused, as is a recursive reference, which inlining cannot express.
 */
export function dereference(root: JsonObject): JsonObject {
  const { $defs: defsValue, ...rest } = root;
  let defs: JsonObject = {};
  if (defsValue !== undefined) {
    if (!isObject(defsValue)) {
      throw new SchemaError('/$defs: must be an object');
    }
    defs = defsValue;
  }

  const inlineKeyword = (key: string, value: Json, pointer: string, stack: readonly string[]): Json => {
    if (key === 'items') {
      return inline(value, pointer, stack);
    }
    if (key === 'anyOf' && Array.isArray(value)) {
      return value.map((branch, index) => inline(branch, `${pointer}/${String(index)}`, stack));
    }
    if (key === 'properties' && isObject(value)) {
      return Object.fromEntries(
        Object.entries(value).map(([name, schema]) => [
          name,
          inline(schema, `${pointer}/${pointerToken(name)}`, stack),
        ]),
      );
    }
    // Any other keyword holds data, or is refused by assertSupported.
    return value;
  };

  const inline = (node: Json, pointer: string, stack: readonly string[]): Json => {
    if (!isObject(node)) {
      return node;
    }
    const ref = node['$ref'];
    if (ref === undefined) {
      return Object.fromEntries(
        Object.entries(node).map(([key, value]) => [
          key,
          inlineKeyword(key, value, `${pointer}/${pointerToken(key)}`, stack),
        ]),
      );
    }
    if (Object.keys(node).length !== 1) {
      throw new SchemaError(`${at(pointer)}: keywords next to $ref are not supported`);
    }
    const name = typeof ref === 'string' ? /^#\/\$defs\/([A-Za-z0-9_.-]+)$/.exec(ref)?.[1] : undefined;
    if (name === undefined) {
      throw new SchemaError(
        `${at(pointer)}: only local "#/$defs/<Name>" references are supported, not ${JSON.stringify(ref)}`,
      );
    }
    if (stack.includes(name)) {
      throw new SchemaError(`${at(pointer)}: recursive reference to #/$defs/${name}`);
    }
    const target = Object.hasOwn(defs, name) ? defs[name] : undefined;
    if (target === undefined) {
      throw new SchemaError(`${at(pointer)}: #/$defs/${name} is not defined`);
    }
    return inline(target, `/$defs/${pointerToken(name)}`, [...stack, name]);
  };

  const resolved = inline(rest, '', []);
  if (!isObject(resolved)) {
    throw new SchemaError('/: the root schema must be an object');
  }
  return resolved;
}

// --- Supported subset -----------------------------------------------------------------------

const ANNOTATIONS = ['title', 'description'];

// Keywords allowed next to each `type`. Each one is translated by json-schema-to-zod into a zod
// check with the same verdict as pydantic (strict, extra=forbid) and JSON Schema 2020-12.
const KEYWORDS_BY_TYPE: ReadonlyMap<string, ReadonlySet<string>> = new Map(
  Object.entries({
    string: ['type', 'pattern', 'minLength', 'enum', 'const', 'not'],
    integer: ['type', 'minimum', 'maximum'],
    boolean: ['type'],
    null: ['type'],
    array: ['type', 'items'],
    object: ['type', 'properties', 'required', 'additionalProperties', 'if', 'then'],
  }).map(([type, keywords]) => [type, new Set([...keywords, ...ANNOTATIONS])]),
);

// A schema without `type` may only be a union or a negated pattern.
const UNTYPED_KEYWORDS: ReadonlySet<string> = new Set(['anyOf', 'not', ...ANNOTATIONS]);

function sameKeys(keys: readonly string[], expected: readonly string[]): boolean {
  return keys.length === expected.length && expected.every((key) => keys.includes(key));
}

/**
 * Refuse a `not` other than `{"pattern": <portable pattern>}`.
 *
 * JSON Schema searches for the pattern and applies it only to strings, so such a `not` holds
 * exactly for a string that contains no match, whether or not its schema has a `type`.
 */
function assertNegatedPattern(negated: Json, pointer: string): void {
  const where = at(pointer);
  if (!isObject(negated) || !sameKeys(Object.keys(negated), ['pattern'])) {
    throw new SchemaError(`${where}: "not" must hold only a "pattern"`);
  }
  const pattern = negated['pattern'];
  if (typeof pattern !== 'string') {
    throw new SchemaError(`${where}/pattern: pattern must be a string`);
  }
  assertPortablePattern(pattern);
}

/**
 * Refuse an `if`/`then` other than the one form translateOverride translates.
 *
 * `if` must hold only `properties` and `required`, with each property one `{"const": <string>}`
 * and `required` naming exactly those properties, so it holds when each of them is present and
 * equal to its constant. `then` must hold only `properties`, each a supported schema. Every
 * property either names must be a property of the enclosing object, and `else` is refused by
 * the keyword list.
 */
function assertConditional(
  condition: Json | undefined,
  consequence: Json | undefined,
  objectKeys: readonly string[],
  pointer: string,
): void {
  if (condition === undefined || consequence === undefined) {
    throw new SchemaError(`${at(pointer)}: "if" and "then" must appear together`);
  }
  const ifPointer = `${pointer}/if`;
  if (!isObject(condition) || !sameKeys(Object.keys(condition), ['properties', 'required'])) {
    throw new SchemaError(`${at(ifPointer)}: "if" must hold only "properties" and "required"`);
  }
  const { properties: tested, required } = condition;
  if (!isObject(tested) || Object.keys(tested).length === 0) {
    throw new SchemaError(`${ifPointer}/properties: must name at least one property`);
  }
  const testedKeys = Object.keys(tested);
  const isString = (key: Json): key is string => typeof key === 'string';
  if (!Array.isArray(required) || !required.every(isString) || !sameKeys(required, testedKeys)) {
    throw new SchemaError(`${ifPointer}/required: must list every "if" property exactly once`);
  }
  for (const key of testedKeys) {
    const where = `${ifPointer}/properties/${pointerToken(key)}`;
    if (!objectKeys.includes(key)) {
      throw new SchemaError(`${where}: not a property of the object`);
    }
    const test = tested[key];
    if (!isObject(test) || !sameKeys(Object.keys(test), ['const']) || typeof test['const'] !== 'string') {
      throw new SchemaError(`${where}: an "if" property must be exactly {"const": <string>}`);
    }
  }

  const thenPointer = `${pointer}/then`;
  if (!isObject(consequence) || !sameKeys(Object.keys(consequence), ['properties'])) {
    throw new SchemaError(`${at(thenPointer)}: "then" must hold only "properties"`);
  }
  const constrained = consequence['properties'];
  if (!isObject(constrained) || Object.keys(constrained).length === 0) {
    throw new SchemaError(`${thenPointer}/properties: must name at least one property`);
  }
  for (const [key, schema] of Object.entries(constrained)) {
    const where = `${thenPointer}/properties/${pointerToken(key)}`;
    if (!objectKeys.includes(key)) {
      throw new SchemaError(`${where}: not a property of the object`);
    }
    assertSupported(schema, where);
  }
}

/**
 * Refuse any schema the generator would not translate with the same verdicts.
 *
 * Every object must forbid additional properties and require all of its properties, as the
 * pydantic records do. `minLength` may only be 0 or 1: JSON Schema counts code points and zod
 * counts UTF-16 code units, which agree only on whether a string is empty. A string `enum` or
 * `const` stands alone next to `type` and annotations, because the generator would drop a
 * `pattern`, `minLength`, `not` or second `enum`/`const` beside it. `not` may only negate a
 * pattern, and an object's `if`/`then` must have the form assertConditional describes.
 */
export function assertSupported(node: Json, pointer = ''): void {
  const where = at(pointer);
  if (!isObject(node)) {
    throw new SchemaError(`${where}: a schema must be a JSON object`);
  }
  const type = node['type'];
  if (type !== undefined && typeof type !== 'string') {
    throw new SchemaError(`${where}: unsupported type ${JSON.stringify(type)}`);
  }
  const allowed = type === undefined ? UNTYPED_KEYWORDS : KEYWORDS_BY_TYPE.get(type);
  if (allowed === undefined) {
    throw new SchemaError(`${where}: unsupported type ${JSON.stringify(type)}`);
  }
  for (const key of Object.keys(node)) {
    const isRootDialect = key === '$schema' && pointer === '';
    if (!allowed.has(key) && !isRootDialect) {
      const context = type === undefined ? 'a schema without "type"' : `type "${type}"`;
      throw new SchemaError(`${where}: unsupported keyword ${JSON.stringify(key)} for ${context}`);
    }
  }
  if (node['$schema'] !== undefined && node['$schema'] !== DRAFT_2020_12) {
    throw new SchemaError(`${where}: $schema must be ${DRAFT_2020_12}`);
  }
  for (const key of ANNOTATIONS) {
    if (node[key] !== undefined && typeof node[key] !== 'string') {
      throw new SchemaError(`${where}: ${key} must be a string`);
    }
  }

  switch (type) {
    case undefined: {
      const { anyOf: branches, not: negated } = node;
      if (negated !== undefined) {
        if (branches !== undefined) {
          throw new SchemaError(`${where}: "anyOf" and "not" cannot be combined`);
        }
        assertNegatedPattern(negated, `${pointer}/not`);
        return;
      }
      if (!Array.isArray(branches) || branches.length === 0) {
        throw new SchemaError(`${where}: a schema needs "type", a non-empty "anyOf" or "not"`);
      }
      branches.forEach((branch, index) => {
        assertSupported(branch, `${pointer}/anyOf/${String(index)}`);
      });
      return;
    }
    case 'string': {
      const { pattern, minLength, enum: members, const: constant, not: negated } = node;
      if (negated !== undefined) {
        assertNegatedPattern(negated, `${pointer}/not`);
      }
      if (pattern !== undefined) {
        if (typeof pattern !== 'string') {
          throw new SchemaError(`${where}: pattern must be a string`);
        }
        assertPortablePattern(pattern);
      }
      if (minLength !== undefined && minLength !== 0 && minLength !== 1) {
        throw new SchemaError(`${where}: minLength must be 0 or 1, not ${JSON.stringify(minLength)}`);
      }
      if (
        members !== undefined &&
        (!Array.isArray(members) ||
          members.length === 0 ||
          !members.every((member) => typeof member === 'string'))
      ) {
        throw new SchemaError(`${where}: enum must be a non-empty array of strings`);
      }
      if (constant !== undefined && typeof constant !== 'string') {
        throw new SchemaError(`${where}: const must be a string`);
      }
      // json-schema-to-zod emits only z.enum or z.literal for an enum or const and drops every
      // other string keyword, so such a combination would be weakened silently.
      if (members !== undefined || constant !== undefined) {
        const others = ['pattern', 'minLength', 'enum', 'const', 'not'].filter(
          (key) => node[key] !== undefined,
        );
        if (others.length > 1) {
          throw new SchemaError(
            `${where}: ${others.map((key) => JSON.stringify(key)).join(' and ')} cannot be combined, because only the enum or const would be translated`,
          );
        }
      }
      return;
    }
    case 'integer': {
      for (const key of ['minimum', 'maximum']) {
        const bound = node[key];
        if (bound !== undefined && !(typeof bound === 'number' && Number.isSafeInteger(bound))) {
          throw new SchemaError(`${where}: ${key} must be a safe integer`);
        }
      }
      return;
    }
    case 'array': {
      const items = node['items'];
      if (items === undefined) {
        throw new SchemaError(`${where}: an array needs "items"`);
      }
      assertSupported(items, `${pointer}/items`);
      return;
    }
    case 'object': {
      const { properties, required, additionalProperties } = node;
      if (!isObject(properties)) {
        throw new SchemaError(`${where}: an object needs "properties"`);
      }
      if (additionalProperties !== false) {
        throw new SchemaError(`${where}: an object must set "additionalProperties": false`);
      }
      const keys = Object.keys(properties);
      const requiredKeys = Array.isArray(required) ? required : [];
      if (
        requiredKeys.length !== keys.length ||
        !keys.every((key) => requiredKeys.includes(key))
      ) {
        throw new SchemaError(`${where}: "required" must list every property exactly once`);
      }
      for (const key of keys) {
        assertSupported(properties[key] ?? null, `${pointer}/properties/${pointerToken(key)}`);
      }
      if (node['if'] !== undefined || node['then'] !== undefined) {
        assertConditional(node['if'], node['then'], keys, pointer);
      }
      return;
    }
    default:
      // boolean and null take no further keywords.
      return;
  }
}

// Escaped punctuation that means the literal character in ECMA-262 (zod) and in the Rust regex
// engine (pydantic).
const LITERAL_ESCAPES: ReadonlySet<string> = new Set(
  ['\\', '^', '$', '.', '|', '?', '*', '+', '(', ')', '[', ']', '{', '}', '/', '-'],
);

/**
 * Refuse a pattern that may match differently in pydantic's Rust regex engine and in a JS
 * RegExp without the `u` flag, which json-schema-to-zod emits.
 *
 * Allowed: printable ASCII literals, non-negated character classes, quantifiers, alternation,
 * plain groups, anchors and escaped punctuation. Refused: `.`, class escapes such as \d, \w, \s
 * and \b, \p and \u escapes, negated or empty classes, `(?` group syntax and non-ASCII text,
 * all of which depend on the engine or on Unicode mode.
 */
export function assertPortablePattern(pattern: string): void {
  const refuse = (reason: string): never => {
    throw new SchemaError(`pattern ${JSON.stringify(pattern)}: ${reason}`);
  };
  if (!/^[\x20-\x7e]*$/.test(pattern)) {
    refuse('only printable ASCII is portable between regex engines');
  }
  let inClass = false;
  let classStart = false;
  for (let index = 0; index < pattern.length; index += 1) {
    const char = pattern.charAt(index);
    const next = pattern.charAt(index + 1);
    if (char === '\\') {
      if (next === '') {
        refuse('ends with a lone backslash');
      }
      if (!LITERAL_ESCAPES.has(next)) {
        refuse(`the escape \\${next} differs between regex engines`);
      }
      index += 1;
      classStart = false;
      continue;
    }
    if (inClass) {
      if (char === ']') {
        if (classStart) {
          refuse('an empty character class differs between regex engines');
        }
        inClass = false;
      } else if (char === '[') {
        refuse('"[" inside a character class differs between regex engines');
      }
      classStart = false;
      continue;
    }
    if (char === '[') {
      if (next === '^') {
        refuse('a negated character class differs between regex engines');
      }
      inClass = true;
      classStart = true;
    } else if (char === '.') {
      refuse('"." differs between regex engines; use a character class');
    } else if (char === '(' && next === '?') {
      refuse('"(?" group syntax differs between regex engines');
    }
  }
  if (inClass) {
    refuse('unterminated character class');
  }
}

// --- Translation ----------------------------------------------------------------------------
// json-schema-to-zod 2.8.1 turns any schema with `not` into z.any().refine(...), dropping its
// type and pattern, and translates `if`/`then` only next to `else` and never on an object. The
// override below translates both forms that assertSupported admits; the converter translates
// every other node. Generated code is plain JavaScript over `z`, so zodExpression can be
// evaluated in tests.

/** A JavaScript string literal in generated code. */
function literal(value: string): string {
  return JSON.stringify(value);
}

function withDescription(source: string, description: Json | undefined): string {
  return typeof description === 'string' ? `${source}.describe(${literal(description)})` : source;
}

function notChecked(refs: Refs, keyword: string): never {
  throw new SchemaError(`/${refs.path.join('/')}: "${keyword}" was not checked by assertSupported`);
}

/**
 * `not: {"pattern": P}`: a string in which P finds no match.
 *
 * JSON Schema searches for a pattern, as RegExp.prototype.test does without flags, and P is
 * portable (assertPortablePattern). `{"pattern": P}` holds for every non-string, so its negation
 * needs a string even where the schema has no `type`.
 */
function translateNegatedPattern(node: JsonObject, refs: Refs): string {
  const { not: negated, description, ...rest } = node;
  const pattern = isObject(negated) ? negated['pattern'] : undefined;
  if (typeof pattern !== 'string') {
    return notChecked(refs, 'not');
  }
  const base = rest['type'] === undefined ? 'z.string()' : parseSchema(rest, refs, true);
  const error = literal('Invalid input: must not match the "not" pattern');
  return withDescription(
    `${base}.refine((text) => !new RegExp(${literal(pattern)}).test(text), { error: ${error} })`,
    description,
  );
}

/**
 * The JavaScript test, over the parsed object `value`, of an `if` that assertConditional accepts:
 * every `if` property is present, as `required` demands, and equal to its constant. An absent
 * property makes it false, as in Draft 2020-12.
 */
export function conditionSource(condition: Json | undefined, pointer = ''): string {
  const tested = isObject(condition) ? condition['properties'] : undefined;
  const refused = new SchemaError(`${at(pointer)}: "if" was not checked by assertSupported`);
  if (!isObject(tested) || Object.keys(tested).length === 0) {
    throw refused;
  }
  const terms = Object.entries(tested).map(([key, test]) => {
    const constant = isObject(test) ? test['const'] : undefined;
    if (typeof constant !== 'string') {
      throw refused;
    }
    return `Object.hasOwn(value, ${literal(key)}) && value[${literal(key)}] === ${literal(constant)}`;
  });
  return terms.join(' && ');
}

/**
 * An object with `if`/`then`, as Draft 2020-12 evaluates it: when every `if` property is present
 * and equal to its constant, every `then` property that is present must match its schema.
 *
 * The object's own keywords are the converter's; the condition is a superRefine on top, which
 * adds one issue per `then` property that fails.
 */
function translateConditional(node: JsonObject, refs: Refs): string {
  const { if: condition, then: consequence, description, ...rest } = node;
  const constrained = isObject(consequence) ? consequence['properties'] : undefined;
  if (!isObject(constrained)) {
    return notChecked(refs, 'then');
  }
  const holds = conditionSource(condition, `/${refs.path.join('/')}`);
  const message = literal('Invalid input: must match the "then" schema when the "if" schema holds');
  const checks = Object.entries(constrained).map(([key, schema]) => {
    if (!isObject(schema)) {
      return notChecked(refs, 'then');
    }
    const property = literal(key);
    const zod = parseSchema(schema, { ...refs, path: [...refs.path, 'then', 'properties', key] });
    return [
      `if (Object.hasOwn(value, ${property}) && !${zod}.safeParse(value[${property}]).success) {`,
      `ctx.addIssue({ code: 'custom', path: [${property}], input: value[${property}], message: ${message} });`,
      '}',
    ].join('\n');
  });
  const base = parseSchema(rest, refs, true);
  return withDescription(
    `${base}.superRefine((value, ctx) => {\nif (${holds}) {\n${checks.join('\n')}\n}\n})`,
    description,
  );
}

function translateOverride(schema: JsonSchemaObject, refs: Refs): string | undefined {
  const node = schema as JsonObject;
  if (node['not'] !== undefined) {
    return translateNegatedPattern(node, refs);
  }
  if (node['if'] !== undefined) {
    return translateConditional(node, refs);
  }
  return undefined;
}

const CONVERTER_OPTIONS = { zodVersion: 4, parserOverride: translateOverride } as const;

/** Inline the references, then refuse anything outside the supported subset. */
function checkedSchema(root: JsonObject): JsonObject {
  const schema = dereference(root);
  assertSupported(schema);
  return schema;
}

/** The zod expression for a JSON Schema, after the same checks and translation as a module. */
export function zodExpression(root: JsonObject): string {
  return jsonSchemaToZod(checkedSchema(root), { module: 'none', ...CONVERTER_OPTIONS });
}

// --- Generation -----------------------------------------------------------------------------

function header(contract: Contract): string {
  return [
    `// Generated from json/${contract.json} by scripts/generate-zod.ts`,
    `// with json-schema-to-zod ${GENERATOR_VERSIONS.jsonSchemaToZod} and prettier ${GENERATOR_VERSIONS.prettier}.`,
    '// Do not edit this file: run `make schema` to regenerate it.',
  ].join('\n');
}

function parseSchemaText(text: string): JsonObject {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new SchemaError(`not valid JSON (${error instanceof Error ? error.message : String(error)})`);
  }
  if (!isObject(parsed as Json)) {
    throw new SchemaError('/: the root schema must be a JSON object');
  }
  return parsed as JsonObject;
}

/** The formatted TypeScript module for one contract, from the text of its JSON Schema. */
export async function generateModule(contract: Contract, schemaText: string): Promise<string> {
  let schema: JsonObject;
  try {
    schema = checkedSchema(parseSchemaText(schemaText));
  } catch (error) {
    if (error instanceof SchemaError) {
      throw new SchemaError(`${contract.json}: ${error.message}`);
    }
    throw error;
  }
  const source = jsonSchemaToZod(schema, {
    name: contract.schemaName,
    module: 'esm',
    type: contract.typeName,
    ...CONVERTER_OPTIONS,
  });
  return format(`${header(contract)}\n\n${source}`, PRETTIER_OPTIONS);
}

function lstat(path: string): ReturnType<typeof lstatSync> {
  return lstatSync(path, { throwIfNoEntry: false });
}

function isDirectory(path: string): boolean {
  return lstat(path)?.isDirectory() === true;
}

function isRegularFile(path: string): boolean {
  return lstat(path)?.isFile() === true;
}

/** Non-hidden entries, so a git-ignored .DS_Store is never reported. */
function visibleEntries(dir: string): string[] {
  return readdirSync(dir)
    .filter((name) => !name.startsWith('.'))
    .sort();
}

function uncontractedSchemas(jsonDir: string): string[] {
  const known = new Set(CONTRACTS.map((contract) => contract.json));
  return visibleEntries(jsonDir)
    .filter((name) => name.endsWith('.schema.json') && !known.has(name))
    .map((name) => `${name}: no zod contract in scripts/generate-zod.ts`);
}

function readSchemaText(jsonDir: string, contract: Contract): string {
  const path = join(jsonDir, contract.json);
  if (!isRegularFile(path)) {
    throw new SchemaError(`${contract.json}: missing from ${jsonDir} or not a regular file`);
  }
  return readFileSync(path, 'utf8');
}

/**
 * Generate every module and write it into `outDir`, creating the directory and overwriting the
 * modules in place. Nothing is deleted, and nothing is written if any schema fails or any
 * target is a symlink or not a regular file.
 */
export async function writeModules(jsonDir: string, outDir: string): Promise<string[]> {
  if (!isDirectory(jsonDir)) {
    throw new SchemaError(`${jsonDir} is not a directory`);
  }
  const [uncontracted] = uncontractedSchemas(jsonDir);
  if (uncontracted !== undefined) {
    throw new SchemaError(`${uncontracted}, so nothing was written`);
  }
  if (lstat(outDir) !== undefined && !isDirectory(outDir)) {
    throw new WriteRefused(`${outDir} is not a directory, so nothing was written`);
  }
  const modules: [string, string][] = [];
  for (const contract of CONTRACTS) {
    const text = await generateModule(contract, readSchemaText(jsonDir, contract));
    modules.push([join(outDir, contract.module), text]);
  }
  for (const [target] of modules) {
    if (lstat(target) !== undefined && !isRegularFile(target)) {
      throw new WriteRefused(`${target} is not a regular file, so nothing was written`);
    }
  }
  mkdirSync(outDir, { recursive: true });
  for (const [target, text] of modules) {
    writeFileSync(target, text);
  }
  return modules.map(([target]) => target);
}

const DIFF_CONTEXT = 2;

/** A line diff of the committed module against the generated one, with a little context. */
function describeDifference(committed: string, generated: string): string {
  // util.diff marks lines only in its first argument with 1 and lines only in its second with -1.
  const entries = diff(committed.split('\n'), generated.split('\n'));
  const changed = entries.map(([operation]) => operation !== 0);
  const lines = ['--- committed', '+++ generated'];
  let skipping = false;
  entries.forEach(([operation, line], index) => {
    const nearChange = changed
      .slice(Math.max(0, index - DIFF_CONTEXT), index + DIFF_CONTEXT + 1)
      .some(Boolean);
    if (!nearChange) {
      if (!skipping) {
        lines.push('  ...');
      }
      skipping = true;
      return;
    }
    skipping = false;
    const marker = operation === 0 ? ' ' : operation === 1 ? '-' : '+';
    lines.push(`${marker} ${line}`);
  });
  return lines.join('\n');
}

/**
 * Regenerate every module from `jsonDir` in memory and list how `outDir` differs.
 *
 * Every visible file in `outDir` must be a generated module, and every JSON Schema in `jsonDir`
 * must have a contract.
 */
export async function checkModules(jsonDir: string, outDir: string): Promise<string[]> {
  for (const dir of [jsonDir, outDir]) {
    if (!isDirectory(dir)) {
      return [`${dir}: not a directory`];
    }
  }
  const problems = uncontractedSchemas(jsonDir);
  const generated = new Map<string, string>();
  for (const contract of CONTRACTS) {
    try {
      generated.set(contract.module, await generateModule(contract, readSchemaText(jsonDir, contract)));
    } catch (error) {
      if (!(error instanceof SchemaError)) {
        throw error;
      }
      problems.push(error.message);
    }
  }
  const expected = new Set(CONTRACTS.map((contract) => contract.module));
  const present = new Set(visibleEntries(outDir));
  for (const name of [...new Set([...expected, ...present])].sort()) {
    const path = join(outDir, name);
    if (!present.has(name)) {
      problems.push(`${name}: generated but missing from ${outDir}`);
    } else if (!isRegularFile(path)) {
      problems.push(`${name}: not a regular file`);
    } else if (!expected.has(name)) {
      problems.push(`${name}: in ${outDir} but not generated`);
    } else {
      const fresh = generated.get(name);
      const committed = readFileSync(path);
      if (fresh !== undefined && !committed.equals(Buffer.from(fresh, 'utf8'))) {
        const difference = describeDifference(committed.toString('utf8'), fresh);
        problems.push(`${name}: differs from the generated module\n${difference}`);
      }
    }
  }
  return problems;
}

// --- Command line ---------------------------------------------------------------------------

const USAGE = 'usage: node scripts/generate-zod.ts write|check <json-dir> <out-dir>';

export async function main(args: readonly string[]): Promise<number> {
  const [command, jsonDir, outDir, ...extra] = args;
  if (
    (command !== 'write' && command !== 'check') ||
    jsonDir === undefined ||
    outDir === undefined ||
    extra.length > 0
  ) {
    process.stderr.write(`${USAGE}\n`);
    return 2;
  }
  if (command === 'write') {
    try {
      const written = await writeModules(jsonDir, outDir);
      process.stdout.write(`wrote ${String(written.length)} zod modules to ${outDir}\n`);
      return 0;
    } catch (error) {
      if (error instanceof SchemaError || error instanceof WriteRefused) {
        process.stderr.write(`zod write: ${error.message}\n`);
        return 1;
      }
      throw error;
    }
  }
  const problems = await checkModules(jsonDir, outDir);
  for (const problem of problems) {
    process.stdout.write(`${problem}\n`);
  }
  if (problems.length > 0) {
    process.stderr.write(
      `${String(problems.length)} problem(s) in ${outDir}; run make schema to regenerate\n`,
    );
    return 1;
  }
  process.stdout.write(`zod schemas match ${jsonDir}: ${outDir}\n`);
  return 0;
}

if (process.argv[1] !== undefined && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main(process.argv.slice(2));
}
