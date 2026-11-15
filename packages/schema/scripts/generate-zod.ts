// Generates the zod 4 schemas in src/generated/ from the committed JSON Schemas in json/, which
// pipeline/src/terraluna_pipeline/schema.py generates from the pydantic records (PRD 8.3, 8.9, D23).
//
//   node scripts/generate-zod.ts write <json-dir> <out-dir>   regenerate the modules in place
//   node scripts/generate-zod.ts check <json-dir> <out-dir>   fail, with a diff, on any drift
//
// json-schema-to-zod translates each schema after local $defs references are inlined. Before
// that, assertSupported refuses every keyword outside the small set that the generator
// translates with the same verdicts as pydantic and JSON Schema, so a new keyword in schema.py
// fails here instead of being dropped from the zod output. The converter mistranslates `not`,
// ignores `if`/`then` without `else`, and drops `propertyNames` from a map, so translateOverride
// translates those itself: `not` as a negated pattern, a record object's `if`/`then`/`else` and
// `allOf` of such conditionals as refinements, and a map object as a zod record.
//
// Two more forms need no override, only checks that make the converter's output faithful: an
// integer `const` (z.literal), and pydantic's discriminated union, an OpenAPI `discriminator`
// next to a `oneOf` of `$ref`s to record objects, which the converter turns into
// z.discriminatedUnion once dereference and assertSupported have verified the branches and the
// mapping. Any other `oneOf` is refused, because the converter would emit z.any().superRefine.
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
  {
    json: 'window-record.schema.json',
    module: 'window-record.ts',
    schemaName: 'WindowRecordSchema',
    typeName: 'WindowRecord',
  },
  {
    json: 'fingerprint-record.schema.json',
    module: 'fingerprint-record.ts',
    schemaName: 'FingerprintRecordSchema',
    typeName: 'FingerprintRecord',
  },
  {
    json: 'windows.schema.json',
    module: 'windows.ts',
    schemaName: 'WindowsSchema',
    typeName: 'Windows',
  },
  {
    json: 'comparison-record.schema.json',
    module: 'comparison-record.ts',
    schemaName: 'ComparisonRecordSchema',
    typeName: 'ComparisonRecord',
  },
  {
    json: 'comparisons.schema.json',
    module: 'comparisons.ts',
    schemaName: 'ComparisonsSchema',
    typeName: 'Comparisons',
  },
  {
    json: 'targets.schema.json',
    module: 'targets.ts',
    schemaName: 'TargetsSchema',
    typeName: 'Targets',
  },
  {
    json: 'card-record.schema.json',
    module: 'card-record.ts',
    schemaName: 'CardRecordSchema',
    typeName: 'CardRecord',
  },
  {
    json: 'model-record.schema.json',
    module: 'model-record.ts',
    schemaName: 'ModelRecordSchema',
    typeName: 'ModelRecord',
  },
  {
    json: 'metrics.schema.json',
    module: 'metrics.ts',
    schemaName: 'MetricsSchema',
    typeName: 'Metrics',
  },
  {
    json: 'embedding-record.schema.json',
    module: 'embedding-record.ts',
    schemaName: 'EmbeddingRecordSchema',
    typeName: 'EmbeddingRecord',
  },
  {
    json: 'embeddings-index.schema.json',
    module: 'embeddings-index.ts',
    schemaName: 'EmbeddingsIndexSchema',
    typeName: 'EmbeddingsIndex',
  },
  {
    json: 'curves.schema.json',
    module: 'curves.ts',
    schemaName: 'CurvesSchema',
    typeName: 'Curves',
  },
  {
    json: 'umap-header.schema.json',
    module: 'umap-header.ts',
    schemaName: 'UmapHeaderSchema',
    typeName: 'UmapHeader',
  },
  {
    json: 'examples.schema.json',
    module: 'examples.ts',
    schemaName: 'ExamplesSchema',
    typeName: 'Examples',
  },
  {
    json: 'background.schema.json',
    module: 'background.ts',
    schemaName: 'BackgroundSchema',
    typeName: 'Background',
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
 * For a `discriminator` next to a `oneOf` of `$ref`s, refuse a mapping that sends a branch's
 * discriminator value to another definition than the one that branch references. Inlining loses
 * the definition names, so this is checked here, on the node before and after inlining. Shapes
 * this cannot read, and values several branches share, are left to assertUnion, which refuses
 * them with their own messages.
 */
function checkMappingTargets(node: JsonObject, inlined: JsonObject, pointer: string): void {
  const { oneOf, discriminator } = node;
  const resolved = inlined['oneOf'];
  if (!Array.isArray(oneOf) || !Array.isArray(resolved) || !isObject(discriminator)) {
    return;
  }
  const { propertyName, mapping } = discriminator;
  if (typeof propertyName !== 'string' || !isObject(mapping)) {
    return;
  }
  const branches: { name: string; value: string; index: number }[] = [];
  oneOf.forEach((element, index) => {
    const ref = isObject(element) ? element['$ref'] : undefined;
    const name = typeof ref === 'string' ? /^#\/\$defs\/([A-Za-z0-9_.-]+)$/.exec(ref)?.[1] : undefined;
    const branch = resolved[index];
    const properties = isObject(branch) ? branch['properties'] : undefined;
    const property = isObject(properties) ? properties[propertyName] : undefined;
    const value = isObject(property) ? property['const'] : undefined;
    if (name !== undefined && typeof value === 'string') {
      branches.push({ name, value, index });
    }
  });
  for (const { name, value, index } of branches) {
    if (branches.filter((other) => other.value === value).length > 1 || !Object.hasOwn(mapping, value)) {
      continue;
    }
    if (mapping[value] !== `#/$defs/${name}`) {
      throw new SchemaError(
        `${at(pointer)}: discriminator mapping sends ${JSON.stringify(value)} to ${JSON.stringify(mapping[value])}, not to #/$defs/${name}, the definition of oneOf/${String(index)}`,
      );
    }
  }
}

/**
 * Inline every `{"$ref": "#/$defs/<Name>"}` and drop the root `$defs`.
 *
 * References are read only where a subschema is expected: under `properties`, `items`, `anyOf`,
 * `oneOf` and a schema-valued `additionalProperties`, the keywords assertSupported accepts. Elsewhere,
 * such as in a `properties` map or an `enum`, `$ref` is plain data. pydantic emits only local
 * references with no sibling keywords, so anything else is refused, as is a recursive
 * reference, which inlining cannot express.
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
    if (key === 'additionalProperties' && isObject(value)) {
      return inline(value, pointer, stack);
    }
    if ((key === 'anyOf' || key === 'oneOf') && Array.isArray(value)) {
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
      const inlined = Object.fromEntries(
        Object.entries(node).map(([key, value]) => [
          key,
          inlineKeyword(key, value, `${pointer}/${pointerToken(key)}`, stack),
        ]),
      );
      checkMappingTargets(node, inlined, pointer);
      return inlined;
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
    integer: ['type', 'minimum', 'maximum', 'const'],
    number: ['type', 'minimum', 'maximum', 'exclusiveMinimum'],
    boolean: ['type', 'const'],
    null: ['type'],
    array: ['type', 'items', 'minItems', 'maxItems'],
    object: [
      'type',
      'properties',
      'required',
      'additionalProperties',
      'propertyNames',
      'if',
      'then',
      'else',
      'allOf',
    ],
  }).map(([type, keywords]) => [type, new Set([...keywords, ...ANNOTATIONS])]),
);

// A schema without `type` may only be a union or a negated pattern.
const UNTYPED_KEYWORDS: ReadonlySet<string> = new Set(['anyOf', 'not', ...ANNOTATIONS]);
const UNION_KEYWORDS: ReadonlySet<string> = new Set(['oneOf', 'discriminator', ...ANNOTATIONS]);

function sameKeys(keys: readonly string[], expected: readonly string[]): boolean {
  return keys.length === expected.length && expected.every((key) => keys.includes(key));
}

/**
 * Refuse a union other than pydantic's discriminated union: a non-empty `oneOf` of record objects
 * next to a `discriminator` of exactly `propertyName` and `mapping`, and nothing else but
 * annotations (and `$schema` at the root).
 *
 * Every branch is an object assertSupported accepts whose `propertyName` property is required and
 * is exactly a non-empty string `const`; no two branches share a value, and `mapping` has one
 * entry for each value. (dereference has already checked that each entry names its branch's
 * definition.) The converter then emits z.discriminatedUnion, with every branch check intact.
 */
function assertUnion(node: JsonObject, pointer: string): void {
  const where = at(pointer);
  for (const key of Object.keys(node)) {
    const isRootDialect = key === '$schema' && pointer === '';
    if (!UNION_KEYWORDS.has(key) && !isRootDialect) {
      throw new SchemaError(`${where}: "oneOf" cannot be combined with ${JSON.stringify(key)}`);
    }
  }
  const { oneOf: branches, discriminator } = node;
  if (branches === undefined || discriminator === undefined) {
    throw new SchemaError(
      `${where}: a "oneOf" is supported only with a "discriminator", and a "discriminator" only with a "oneOf"`,
    );
  }
  if (!Array.isArray(branches) || branches.length === 0) {
    throw new SchemaError(`${where}: "oneOf" must be a non-empty array of branches`);
  }
  const mapping = isObject(discriminator) ? discriminator['mapping'] : undefined;
  const propertyName = isObject(discriminator) ? discriminator['propertyName'] : undefined;
  if (
    !isObject(discriminator) ||
    !sameKeys(Object.keys(discriminator), ['propertyName', 'mapping']) ||
    typeof propertyName !== 'string' ||
    propertyName === '' ||
    !isObject(mapping) ||
    !Object.values(mapping).every((target) => typeof target === 'string')
  ) {
    throw new SchemaError(
      `${where}: "discriminator" must be exactly a non-empty string "propertyName" and a "mapping" of strings`,
    );
  }
  const seen = new Map<string, number>();
  branches.forEach((branch, index) => {
    const branchPointer = `${pointer}/oneOf/${String(index)}`;
    const property = isObject(branch) ? branch['properties'] : undefined;
    const tag = isObject(property) ? property[propertyName] : undefined;
    if (
      !isObject(branch) ||
      branch['type'] !== 'object' ||
      !isObject(property) ||
      branch['additionalProperties'] !== false
    ) {
      throw new SchemaError(
        `${branchPointer}: a "oneOf" branch must be a record object with "additionalProperties": false`,
      );
    }
    const required = branch['required'];
    if (tag === undefined || !Array.isArray(required) || !required.includes(propertyName)) {
      throw new SchemaError(
        `${branchPointer}: a "oneOf" branch must have the required discriminator property ${JSON.stringify(propertyName)}`,
      );
    }
    const tagPointer = `${branchPointer}/properties/${pointerToken(propertyName)}`;
    const value = isObject(tag) ? tag['const'] : undefined;
    if (
      !isObject(tag) ||
      tag['type'] !== 'string' ||
      typeof value !== 'string' ||
      value === '' ||
      !Object.keys(tag).every((key) => ['type', 'const', ...ANNOTATIONS].includes(key))
    ) {
      throw new SchemaError(
        `${tagPointer}: the discriminator property must be a string "const" of a non-empty value, with only annotations besides`,
      );
    }
    const first = seen.get(value);
    if (first !== undefined) {
      throw new SchemaError(
        `${branchPointer}: discriminator value ${JSON.stringify(value)} is also the value of oneOf/${String(first)}`,
      );
    }
    seen.set(value, index);
    assertSupported(branch, branchPointer);
  });
  for (const value of seen.keys()) {
    if (!Object.hasOwn(mapping, value)) {
      throw new SchemaError(
        `${where}: discriminator mapping has no entry for ${JSON.stringify(value)}`,
      );
    }
  }
  for (const key of Object.keys(mapping)) {
    if (!seen.has(key)) {
      throw new SchemaError(
        `${where}: discriminator mapping names ${JSON.stringify(key)}, which no branch has`,
      );
    }
  }
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
 * Refuse an `if`/`then`/`else` other than the one form translateOverride translates.
 *
 * `if` must hold only `properties` and `required`, with each property one test, either
 * `{"const": <string>}`, `{"const": <safe integer>}` or `{"type": "null"}`, and `required`
 * naming exactly those properties, so it holds when each of them is present and passes its test.
 * `then` and the optional `else` must each hold only a non-empty `properties`, each a supported
 * schema; `else` needs `then`. Every property any of them names must be a property of the
 * enclosing object. `$ref` is not inlined inside a conditional, so assertSupported refuses it.
 */
function assertConditional(
  condition: Json | undefined,
  consequence: Json | undefined,
  alternative: Json | undefined,
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
    const constant = isObject(test) ? test['const'] : undefined;
    const isConstTest =
      isObject(test) &&
      sameKeys(Object.keys(test), ['const']) &&
      (typeof constant === 'string' || (typeof constant === 'number' && Number.isSafeInteger(constant)));
    const isNullTest =
      isObject(test) && sameKeys(Object.keys(test), ['type']) && test['type'] === 'null';
    if (!isConstTest && !isNullTest) {
      throw new SchemaError(
        `${where}: an "if" property must be exactly {"const": <string>}, {"const": <safe integer>} or {"type": "null"}`,
      );
    }
  }

  const branches: [string, Json][] = [['then', consequence]];
  if (alternative !== undefined) {
    branches.push(['else', alternative]);
  }
  for (const [name, branch] of branches) {
    const branchPointer = `${pointer}/${name}`;
    if (!isObject(branch) || !sameKeys(Object.keys(branch), ['properties'])) {
      throw new SchemaError(`${at(branchPointer)}: "${name}" must hold only "properties"`);
    }
    const constrained = branch['properties'];
    if (!isObject(constrained) || Object.keys(constrained).length === 0) {
      throw new SchemaError(`${branchPointer}/properties: must name at least one property`);
    }
    for (const [key, schema] of Object.entries(constrained)) {
      const where = `${branchPointer}/properties/${pointerToken(key)}`;
      if (!objectKeys.includes(key)) {
        throw new SchemaError(`${where}: not a property of the object`);
      }
      assertSupported(schema, where);
    }
  }
}

/**
 * Refuse an `allOf` that is not a non-empty array of conditionals, each exactly an `if`/`then`
 * with an optional `else` that assertConditional accepts.
 */
function assertConditionalAllOf(
  elements: Json,
  objectKeys: readonly string[],
  pointer: string,
): void {
  const where = `${pointer}/allOf`;
  if (!Array.isArray(elements) || elements.length === 0) {
    throw new SchemaError(`${where}: must be a non-empty array of conditionals`);
  }
  elements.forEach((element, index) => {
    const elementPointer = `${where}/${String(index)}`;
    if (
      !isObject(element) ||
      !Object.keys(element).every((key) => ['if', 'then', 'else'].includes(key))
    ) {
      throw new SchemaError(
        `${elementPointer}: an "allOf" element must be only an "if", "then" and optional "else"`,
      );
    }
    assertConditional(element['if'], element['then'], element['else'], objectKeys, elementPointer);
  });
}

/**
 * Refuse a map object other than `{"additionalProperties": <schema>}` with optional
 * `propertyNames` of `{"minLength": 1}` or `{"enum": [<non-empty list of strings>]}`.
 */
function assertMap(node: JsonObject, pointer: string): void {
  const where = at(pointer);
  for (const key of ['properties', 'required', 'if', 'then', 'else', 'allOf']) {
    if (node[key] !== undefined) {
      throw new SchemaError(`${where}: a map with a schema for "additionalProperties" cannot have "${key}"`);
    }
  }
  const names = node['propertyNames'];
  if (names !== undefined) {
    const members = isObject(names) ? names['enum'] : undefined;
    const isMinLength =
      isObject(names) && sameKeys(Object.keys(names), ['minLength']) && names['minLength'] === 1;
    const isEnum =
      isObject(names) &&
      sameKeys(Object.keys(names), ['enum']) &&
      Array.isArray(members) &&
      members.length > 0 &&
      members.every((member) => typeof member === 'string');
    if (!isMinLength && !isEnum) {
      throw new SchemaError(
        `${where}/propertyNames: must be {"minLength": 1} or {"enum": [<non-empty list of strings>]}`,
      );
    }
  }
  assertSupported(node['additionalProperties'] ?? null, `${pointer}/additionalProperties`);
}

/**
 * Refuse any schema the generator would not translate with the same verdicts.
 *
 * Every object must forbid additional properties and require all of its properties, as the
 * pydantic records do. `minLength` may only be 0 or 1: JSON Schema counts code points and zod
 * counts UTF-16 code units, which agree only on whether a string is empty. A string `enum` or
 * `const` stands alone next to `type` and annotations, because the generator would drop a
 * `pattern`, `minLength`, `not` or second `enum`/`const` beside it. `not` may only negate a
 * pattern. An object either is a map (assertMap) or sets `additionalProperties: false`, and then
 * its `if`/`then`/`else` and its `allOf` of conditionals must have the forms assertConditional
 * and assertConditionalAllOf describe. A `number` takes finite `minimum`, `maximum` and
 * `exclusiveMinimum`, a `boolean` may be a boolean `const`, and an array's `minItems` and
 * `maxItems` must be non-negative safe integers.
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
  if (node['oneOf'] !== undefined || node['discriminator'] !== undefined) {
    if (type !== undefined) {
      throw new SchemaError(`${where}: "oneOf" cannot be combined with "type"`);
    }
    for (const key of ANNOTATIONS) {
      if (node[key] !== undefined && typeof node[key] !== 'string') {
        throw new SchemaError(`${where}: ${key} must be a string`);
      }
    }
    if (node['$schema'] !== undefined && node['$schema'] !== DRAFT_2020_12) {
      throw new SchemaError(`${where}: $schema must be ${DRAFT_2020_12}`);
    }
    assertUnion(node, pointer);
    return;
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
      const constant = node['const'];
      if (constant !== undefined) {
        if (!(typeof constant === 'number' && Number.isSafeInteger(constant))) {
          throw new SchemaError(`${where}: const must be a safe integer`);
        }
        // json-schema-to-zod emits only z.literal for a const and drops the bounds beside it.
        if (node['minimum'] !== undefined || node['maximum'] !== undefined) {
          throw new SchemaError(
            `${where}: "const" cannot be combined with "minimum" or "maximum", because only the const would be translated`,
          );
        }
      }
      for (const key of ['minimum', 'maximum']) {
        const bound = node[key];
        if (bound !== undefined && !(typeof bound === 'number' && Number.isSafeInteger(bound))) {
          throw new SchemaError(`${where}: ${key} must be a safe integer`);
        }
      }
      return;
    }
    case 'number': {
      for (const key of ['minimum', 'maximum', 'exclusiveMinimum']) {
        const bound = node[key];
        if (bound !== undefined && !(typeof bound === 'number' && Number.isFinite(bound))) {
          throw new SchemaError(`${where}: ${key} must be a finite number`);
        }
      }
      return;
    }
    case 'boolean': {
      const constant = node['const'];
      if (constant !== undefined && typeof constant !== 'boolean') {
        throw new SchemaError(`${where}: const must be a boolean`);
      }
      return;
    }
    case 'array': {
      const items = node['items'];
      if (items === undefined) {
        throw new SchemaError(`${where}: an array needs "items"`);
      }
      for (const key of ['minItems', 'maxItems']) {
        const length = node[key];
        if (
          length !== undefined &&
          !(typeof length === 'number' && Number.isSafeInteger(length) && length >= 0)
        ) {
          throw new SchemaError(`${where}: ${key} must be a non-negative safe integer`);
        }
      }
      assertSupported(items, `${pointer}/items`);
      return;
    }
    case 'object': {
      const { properties, required, additionalProperties } = node;
      if (isObject(additionalProperties)) {
        assertMap(node, pointer);
        return;
      }
      if (node['propertyNames'] !== undefined) {
        throw new SchemaError(`${where}: "propertyNames" is only supported on a map`);
      }
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
      if (node['if'] !== undefined || node['then'] !== undefined || node['else'] !== undefined) {
        assertConditional(node['if'], node['then'], node['else'], keys, pointer);
      }
      if (node['allOf'] !== undefined) {
        assertConditionalAllOf(node['allOf'], keys, pointer);
      }
      return;
    }
    default:
      // null takes no further keywords.
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
// type and pattern, translates `if`/`then` only next to `else` and never on an object, has no
// `allOf` of conditionals, and drops `propertyNames` from a map. The override below translates
// the forms that assertSupported admits; the converter translates every other node. Generated
// code is plain JavaScript over `z`, so zodExpression can be evaluated in tests.

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
 * every `if` property is present, as `required` demands, and equal to its string or integer
 * constant or, for `{"type": "null"}`, null. An absent property makes it false, as in Draft
 * 2020-12.
 */
export function conditionSource(condition: Json | undefined, pointer = ''): string {
  const tested = isObject(condition) ? condition['properties'] : undefined;
  const refused = new SchemaError(`${at(pointer)}: "if" was not checked by assertSupported`);
  if (!isObject(tested) || Object.keys(tested).length === 0) {
    throw refused;
  }
  const terms = Object.entries(tested).map(([key, test]) => {
    if (!isObject(test)) {
      throw refused;
    }
    const constant = test['const'];
    let expected: string;
    if (typeof constant === 'string') {
      expected = literal(constant);
    } else if (typeof constant === 'number' && Number.isSafeInteger(constant)) {
      expected = String(constant);
    } else if (test['type'] === 'null' && constant === undefined) {
      expected = 'null';
    } else {
      throw refused;
    }
    return `Object.hasOwn(value, ${literal(key)}) && value[${literal(key)}] === ${expected}`;
  });
  return terms.join(' && ');
}

/** One `if`/`then`/`else` of a record object, and where it sits in the schema. */
interface Conditional {
  readonly condition: Json | undefined;
  readonly consequence: Json | undefined;
  readonly alternative: Json | undefined;
  readonly path: readonly (string | number)[];
}

/** The statements that check each property in `branch` (a `then` or `else`) when it applies. */
function branchChecks(
  branch: Json | undefined,
  name: 'then' | 'else',
  prefix: string,
  refs: Refs,
  path: readonly (string | number)[],
): string {
  const constrained = isObject(branch) ? branch['properties'] : undefined;
  if (!isObject(constrained)) {
    return notChecked(refs, name);
  }
  return Object.entries(constrained)
    .map(([key, schema]) => {
      if (!isObject(schema)) {
        return notChecked(refs, name);
      }
      const property = literal(key);
      const zod = parseSchema(schema, { ...refs, path: [...path, name, 'properties', key] });
      return [
        `if (Object.hasOwn(value, ${property})) {`,
        `for (const issue of ${zod}.safeParse(value[${property}]).error?.issues ?? []) {`,
        `ctx.addIssue({ code: 'custom', path: [${property}, ...issue.path], input: value[${property}], message: ${prefix} + issue.message });`,
        '}',
        '}',
      ].join('\n');
    })
    .join('\n');
}

/**
 * One conditional as a superRefine on the base schema, as Draft 2020-12 evaluates it: when every
 * `if` property is present and passes its test, every `then` property that is present must match
 * its schema; otherwise every `else` property that is present must match its schema.
 *
 * For each issue of such a property it adds one issue at the path below that property, with the
 * nested message after a fixed prefix naming the branch.
 */
function refinement(conditional: Conditional, refs: Refs): string {
  const { condition, consequence, alternative, path } = conditional;
  const holds = conditionSource(condition, `/${path.join('/')}`);
  const thenPrefix = literal('Invalid input: must match the "then" schema when the "if" schema holds: ');
  const elsePrefix = literal('Invalid input: must match the "else" schema when the "if" schema fails: ');
  const otherwise =
    alternative === undefined
      ? ''
      : ` else {\n${branchChecks(alternative, 'else', elsePrefix, refs, path)}\n}`;
  return `.superRefine((value, ctx) => {\nif (${holds}) {\n${branchChecks(consequence, 'then', thenPrefix, refs, path)}\n}${otherwise}\n})`;
}

/**
 * An object with a top-level `if`/`then`/`else` and/or an `allOf` of such conditionals: the
 * object's own keywords are the converter's and each conditional is a superRefine on top, the
 * top-level one first, then the `allOf` elements in order.
 */
function translateConditionals(node: JsonObject, refs: Refs): string {
  const { if: condition, then: consequence, else: alternative, allOf, description, ...rest } = node;
  const conditionals: Conditional[] = [];
  if (condition !== undefined) {
    conditionals.push({ condition, consequence, alternative, path: refs.path });
  }
  if (allOf !== undefined) {
    if (!Array.isArray(allOf)) {
      return notChecked(refs, 'allOf');
    }
    for (const [index, element] of allOf.entries()) {
      if (!isObject(element)) {
        return notChecked(refs, 'allOf');
      }
      conditionals.push({
        condition: element['if'],
        consequence: element['then'],
        alternative: element['else'],
        path: [...refs.path, 'allOf', index],
      });
    }
  }
  const base = parseSchema(rest, refs, true);
  const refinements = conditionals.map((conditional) => refinement(conditional, refs));
  return withDescription(`${base}${refinements.join('')}`, description);
}

/**
 * A map object: `z.record(z.string(), V)`, `z.record(z.string().min(1), V)` for non-empty keys,
 * or `z.partialRecord(z.enum([...]), V)` for enumerated keys, which allows any subset of them.
 */
function translateMap(node: JsonObject, refs: Refs): string {
  const { additionalProperties: values, propertyNames: names, description } = node;
  if (!isObject(values)) {
    return notChecked(refs, 'additionalProperties');
  }
  const valueZod = parseSchema(values, { ...refs, path: [...refs.path, 'additionalProperties'] });
  const members = isObject(names) ? names['enum'] : undefined;
  let source: string;
  if (names === undefined) {
    source = `z.record(z.string(), ${valueZod})`;
  } else if (isObject(names) && names['minLength'] === 1) {
    source = `z.record(z.string().min(1), ${valueZod})`;
  } else if (Array.isArray(members)) {
    source = `z.partialRecord(z.enum(${JSON.stringify(members)}), ${valueZod})`;
  } else {
    return notChecked(refs, 'propertyNames');
  }
  return withDescription(source, description);
}

function translateOverride(schema: JsonSchemaObject, refs: Refs): string | undefined {
  const node = schema as JsonObject;
  if (node['not'] !== undefined) {
    return translateNegatedPattern(node, refs);
  }
  if (node['type'] === 'object' && isObject(node['additionalProperties'])) {
    return translateMap(node, refs);
  }
  if (node['if'] !== undefined || node['allOf'] !== undefined) {
    return translateConditionals(node, refs);
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
