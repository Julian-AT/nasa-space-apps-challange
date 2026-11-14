# Shared schema cases

`cases.json` holds accept and reject cases for the release bundle records (PRD 8.4, 8.12). The Python tests (`pipeline/tests/test_json_schema.py`) and the TypeScript tests in `@terraluna/schema` run the same cases. Each case must get its stated verdict from pydantic, from the generated JSON Schema and from the generated zod.

Each case is `{"name", "schema", "valid", "value"}`:

- `name` is unique, lower-case kebab-case.
- `schema` is `BundleManifest`, `GateRecord`, `Validation` (the array of GateRecords in `validation.json`), `ProductRecord` or `Sources` (the array of ProductRecords in `sources.json`). These map to `packages/schema/json/bundle-manifest.schema.json`, `gate-record.schema.json`, `validation.schema.json`, `product-record.schema.json` and `sources.schema.json`.
- `valid` is the verdict, taken from the PRD contract. It is never copied from what a validator returns.
- `value` is the JSON value to validate. Values are schema test inputs, not observations, results or gate outcomes.

Rules for new cases:

- Write every number so that JavaScript reads the same value. JavaScript cannot tell `1.0` from `1`, and it rounds integers above 2^53. A float must have a fractional part, and an integer must stay within ±2^53.
- Evaluate `pattern` as ECMA-262 does: `$` matches only at the end of the input. Python's `re.search`, which the jsonschema package uses, also matches `$` before a trailing newline. The Python tests correct for this.
- The cases cover schema rules only. Bundle rules belong to `validate_bundle` in `export.py`: listings, checksums, budgets, duplicate gates, and refusing a mock or an unset shipping mode in production.
- `manifest-mock` and `validation-mock` must equal `fixtures/mock-bundle/manifest.json` and `validation.json`. `manifest-real-shaped` must equal the PRD 8.4 example, with each `…` replaced by 64 hex characters.
- Each `product-*` case differs from `product-verified` only in what its name says. A VERIFIED ProductRecord needs a `local_path`, a `checksum` and `units` other than `unknown` in any letter case; `unknown` followed by a newline is not that value, and all three evaluators accept it.

## Generator cases

`generator-cases.json` holds synthetic schemas, not bundle records, for the zod generator's `if`/`then` and `not` translation. Each entry is `{"name", "schema", "cases"}`, and each case is `{"name", "valid", "value"}` with a verdict authored from Draft 2020-12. The Python tests check every verdict with the Draft 2020-12 validator (ECMA-262 `$`), and the `@terraluna/schema` tests check that the generated zod gives the same one.
