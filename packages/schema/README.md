# @terraluna/schema

Release bundle contracts for the web (PRD 8.4, 8.12, D23).

- `json/*.schema.json`: JSON Schema 2020-12, generated from the pydantic records in `pipeline/src/terraluna_pipeline/schema.py`.
- `src/generated/*.ts`: zod 4 schemas and inferred types, generated from that JSON Schema by `scripts/generate-zod.ts`. Do not edit them.
- `src/index.ts` exports `BundleManifestSchema`, `GateRecordSchema`, `ValidationSchema` (the array in `validation.json`), `ProductRecordSchema` and `SourcesSchema` (the array in `sources.json`), with the types `BundleManifest`, `GateRecord`, `Validation`, `ProductRecord` and `Sources`.

```ts
import { BundleManifestSchema } from '@terraluna/schema';

const manifest = BundleManifestSchema.parse(await response.json());
```

## Commands (from the repository root)

| Command | Does |
| --- | --- |
| `make schema` | Regenerates the JSON Schema, then the zod modules, in place |
| `make schema-check` | Fails, with a diff, if either generated output differs from the committed copy |
| `make check-ts` / `make test-ts` | ESLint and `tsc --noEmit`, and Vitest, through Turborepo |

The Vitest suite runs every case in `fixtures/schema-cases/cases.json`, the same cases the Python tests run, and parses `fixtures/mock-bundle/`. It also translates the synthetic schemas in `fixtures/schema-cases/generator-cases.json` and checks each document's verdict; the Python tests check the same verdicts against Draft 2020-12. `tests/types.test-d.ts` checks the inferred types and runs under `tsc`.

## Generator

`scripts/generate-zod.ts` inlines local `$defs` references, runs [json-schema-to-zod](https://github.com/StefanTerdell/json-schema-to-zod) (zod 4 output), and formats the result with prettier using explicit options. The converter turns any schema with `not` into `z.any()` and ignores `if`/`then` without `else`, so the script translates those two keywords itself through the converter's `parserOverride` option. Both tools are pinned exactly in `package.json`, and each module header names their versions.

json-schema-to-zod 2.8.1 is the project's final release: active maintenance stopped in March 2026. The generated code is committed and checked for drift, so replacing the tool later changes only `src/generated/`.

The script refuses any JSON Schema it cannot translate with the same verdicts as pydantic and JSON Schema. A new keyword in `schema.py` therefore fails `make schema` instead of being dropped. It accepts:

- `type` `object` with `properties`, every property `required` and `additionalProperties: false`;
- `string` with `pattern`, `minLength` 0 or 1, `not`, `enum` or `const`; `integer` with safe-integer `minimum` and `maximum`; `boolean`; `null`; `array` with `items`;
- `anyOf`, `title`, `description`, and `$schema` at the root;
- `not` holding only a `pattern`, with or without `type: string`: a string in which the pattern finds no match;
- on an object, `if` and `then` together, no `else`: `if` holds only `properties`, each exactly `{"const": <string>}`, and `required` naming those properties; `then` holds only `properties`, each a supported schema. Both name properties of the same object. The zod schema refines the object: when every `if` property equals its constant, each `then` property must match its schema. ProductRecord uses this for `verification_status: VERIFIED` (PRD 8.12).

Patterns must be printable ASCII without `.`, class escapes such as `\d`, negated or empty classes, or `(?` groups. These constructs match differently in pydantic's Rust regex engine and in a JavaScript `RegExp`.

JavaScript cannot tell `1.0` from `1`, and it rounds integers above 2^53. zod therefore accepts `1.0` where pydantic's strict mode refuses it. The shared cases avoid such numbers, and `validate_bundle` in Python remains the authoritative bundle check.
