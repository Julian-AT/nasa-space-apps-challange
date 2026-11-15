# Shared schema cases

`cases.json` holds accept and reject cases for the release bundle records (PRD 8.4, 8.12). The Python tests (`pipeline/tests/test_json_schema.py`) and the TypeScript tests in `@terraluna/schema` run the same cases. Each case must get its stated verdict from pydantic, from the generated JSON Schema and from the generated zod.

Each case is `{"name", "schema", "valid", "value"}`:

- `name` is unique, lower-case kebab-case.
- `schema` is `BundleManifest`, `GateRecord`, `Validation` (the array of GateRecords in `validation.json`), `ProductRecord`, `Sources` (the array of ProductRecords in `sources.json`), `WindowRecord`, `FingerprintRecord`, `Windows` (the array of WindowEntries in `windows/{body}.json`), `ComparisonRecord` or `Comparisons` (the array in `comparisons/{target_id}.json`). Each maps to the file of the same name in kebab case in `packages/schema/json/`, for example `window-record.schema.json`.
- `valid` is the verdict, taken from the PRD contract. It is never copied from what a validator returns.
- `value` is the JSON value to validate. Values are schema test inputs, not observations, results or gate outcomes.

Rules for new cases:

- Write every number so that JavaScript reads the same value. JavaScript cannot tell `1.0` from `1`, and it rounds integers above 2^53. A float must have a fractional part, and an integer must stay within ±2^53.
- Evaluate `pattern` as ECMA-262 does: `$` matches only at the end of the input. Python's `re.search`, which the jsonschema package uses, also matches `$` before a trailing newline. The Python tests correct for this.
- The cases cover schema rules only. Bundle rules belong to `validate_bundle` in `export.py`: listings, checksums, budgets, duplicate gates, and refusing a mock or an unset shipping mode in production.
- `manifest-mock` and `validation-mock` must equal `fixtures/mock-bundle/manifest.json` and `validation.json`. `manifest-real-shaped` must equal the PRD 8.4 example, with each `…` replaced by 64 hex characters.
- Each `window-record-*`, `fingerprint-*` and `comparison-*` case differs from its base (`window-record-moon` or `window-record-earth-utm`, `fingerprint-terrain` or `fingerprint-radar`, `comparison-terrain-only`) only at the paths that `SCIENCE_CASE_CHANGES` in `pipeline/tests/test_json_schema.py` lists. The `windows-*` and `comparisons-*` arrays are built from those records. A real zero distance is written `0`, because JavaScript reads `0.0` as an integer.
- A rule that compares values inside one document cannot be stated in JSON Schema, so no shared case tests it: a Distribution's sorted values and `n == len(values)`, a WindowPolygon's closed rings, a WindowEntry's slot kinds, and an IncidenceRange's `min <= max`. `pipeline/tests/test_schema_science.py` tests them in pydantic, and `test_json_schema.py` shows that the JSON Schema accepts such documents.
- Each `product-*` case differs from `product-verified` only in what its name says. A VERIFIED ProductRecord needs a `local_path`, a `checksum` and `units` other than `unknown` in any letter case; `unknown` followed by a newline is not that value, and all three evaluators accept it.

## Generator cases

`generator-cases.json` holds synthetic schemas, not bundle records, for the forms the zod generator translates itself or checks closely: `not`, conditionals (`if`/`then`/`else`, also in `allOf`, with string, integer and null tests), maps with `propertyNames`, number bounds, a boolean `const` and array lengths. Each entry is `{"name", "schema", "cases"}`, and each case is `{"name", "valid", "value"}` with a verdict authored from Draft 2020-12. The Python tests check every verdict with the Draft 2020-12 validator (ECMA-262 `$`), and the `@terraluna/schema` tests check that the generated zod gives the same one.

Every object there requires all of its properties, so a document without an `if` property is rejected whatever the condition says. An entry whose schema has a top-level `if` therefore also has `conditionCases`: each is `{"name", "holds", "value"}`, where `value` is an object and `holds` is the Draft 2020-12 verdict of the `if` schema alone. An absent `if` property makes it false, because `if` lists that property in `required`. The Python tests check each verdict against the `if` schema, and the `@terraluna/schema` tests evaluate the condition the generated refinement uses. Each `if` property is absent in at least one condition case.
