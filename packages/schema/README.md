# @terraluna/schema

Release bundle contracts for the web (PRD 8.4, 8.12, D23).

- `json/*.schema.json`: JSON Schema 2020-12, generated from the pydantic records in `pipeline/src/terraluna_pipeline/schema.py`.
- `src/generated/*.ts`: zod 4 schemas and inferred types, generated from that JSON Schema by `scripts/generate-zod.ts`. Do not edit them.
- `src/index.ts` exports `BundleManifestSchema`, `GateRecordSchema`, `ValidationSchema` (the array in `validation.json`), `ProductRecordSchema`, `SourcesSchema` (the array in `sources.json`), `WindowRecordSchema`, `FingerprintRecordSchema`, `WindowsSchema` (the array in `windows/{body}.json`), `ComparisonRecordSchema` and `ComparisonsSchema` (the array in `comparisons/{target_id}.json`), each with its type of the same name without `Schema`.
- The science records keep cross-field rules as refinements, which do not narrow the inferred types. `distances_by_family` is an object with exactly the four families; the other maps keyed by family (`by_family`, `rank_frequency`, `values_by_family`, `rank_by_family`) hold any subset of them. Four rules compare values inside one document, so only pydantic checks them: a Distribution's `values` are sorted and counted by `n`, a WindowPolygon's rings are closed, a WindowEntry's `terrain` and `radar` slots hold fingerprints of that kind, and an IncidenceRange's `min` does not exceed its `max`. Python writes every bundle file through those models.
- The generated modules inline every `$defs` reference, so `windows.ts` is about 150 kB of source. Import only the schemas a route needs.

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

The Vitest suite runs every case in `fixtures/schema-cases/cases.json`, the same cases the Python tests run, and parses `fixtures/mock-bundle/`. It also translates the synthetic schemas in `fixtures/schema-cases/generator-cases.json` and checks each document's verdict, and it evaluates each `if` condition the generator emits on that file's condition cases; the Python tests check the same verdicts against Draft 2020-12. `tests/types.test-d.ts` checks the inferred types and runs under `tsc`.

The tests read `pipeline/src/terraluna_pipeline/schema.py` and `fixtures/`, outside the package. Turborepo hashes both for the test task: the root `turbo.json` declares the fixtures, and `turbo.json` here adds `schema.py` with `$TURBO_EXTENDS$`, so a cached run never replays after either changes.

## Generator

`scripts/generate-zod.ts` inlines local `$defs` references, runs [json-schema-to-zod](https://github.com/StefanTerdell/json-schema-to-zod) (zod 4 output), and formats the result with prettier using explicit options. The converter turns any schema with `not` into `z.any()`, ignores `if`/`then` without `else` and an object's `allOf` of conditionals, and drops `propertyNames` from a map, so the script translates those keywords itself through the converter's `parserOverride` option. Both tools are pinned exactly in `package.json`, and each module header names their versions.

json-schema-to-zod 2.8.1 is the project's final release: active maintenance stopped in March 2026. The generated code is committed and checked for drift, so replacing the tool later changes only `src/generated/`.

The script refuses any JSON Schema it cannot translate with the same verdicts as pydantic and JSON Schema. A new keyword in `schema.py` therefore fails `make schema` instead of being dropped. It accepts:

- `type` `object` with `properties`, every property `required` and `additionalProperties: false`;
- a map: `type` `object` whose `additionalProperties` is a supported schema, with no `properties`, and optionally `propertyNames` of `{"minLength": 1}` or `{"enum": [<strings>]}`. It becomes `z.record(z.string(), V)`, `z.record(z.string().min(1), V)` or `z.partialRecord(z.enum([...]), V)`; the last accepts any subset of the keys and refuses others;
- `string` with `pattern`, `minLength` 0 or 1, `not`, `enum` or `const`; `integer` with safe-integer `minimum` and `maximum`; `number` with finite `minimum`, `maximum` and `exclusiveMinimum`; `boolean`, optionally with a boolean `const`; `null`; `array` with `items` and optional non-negative safe-integer `minItems` and `maxItems`;
- `anyOf`, `title`, `description`, and `$schema` at the root;
- `not` holding only a `pattern`, with or without `type: string`: a string in which the pattern finds no match;
- on a record object, a conditional at the top level and/or a non-empty `allOf` of conditionals. A conditional is `if` and `then`, with an optional `else`: `if` holds only `properties`, each exactly `{"const": <string>}`, `{"const": <safe integer>}` or `{"type": "null"}`, and `required` naming those properties; `then` and `else` hold only a non-empty `properties`, each a supported schema. All of them name properties of the same object. The zod schema refines the object once for each conditional: when every `if` property is present and passes its test, each `then` property must match its schema, and otherwise each `else` property. A failing property gives one issue for each issue of its schema, at the path below the property, with the message `Invalid input: must match the "then" schema when the "if" schema holds: ` (or `... the "else" schema when the "if" schema fails: `) followed by the nested message. ProductRecord uses a top-level conditional for `verification_status: VERIFIED` (PRD 8.12); the science records put theirs in `allOf`, for example DistanceEntry's "status available ⇔ value set ⇔ reason null".

Patterns must be printable ASCII without `.`, class escapes such as `\d`, negated or empty classes, or `(?` groups. These constructs match differently in pydantic's Rust regex engine and in a JavaScript `RegExp`.

JavaScript cannot tell `1.0` from `1`, and it rounds integers above 2^53. zod therefore accepts `1.0` where pydantic's strict mode refuses it. The shared cases avoid such numbers, and `validate_bundle` in Python remains the authoritative bundle check.
