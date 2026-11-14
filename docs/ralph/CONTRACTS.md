# Terraluna integration contracts for the Ralph batches

Version 1, 14 November 2026. This file is the single source of truth for every batch that has not launched yet: **t0b-contracts** (new), **t4-logic** and **t4-real** (the split of t4-comparison), **t5-search**, **t6-uncertainty**, **t7b-explorer**, **t9-release**, **t10-core**, **t11-dataset**, **t12a-encoder**, **t12b-evaluate**, **t13-probe**, **t14-report** and **t15b-api** (new).

The running batches (t1-pilot, t2-polarimetry, t3-terrain, t7-shell, t15-api, t16-paper) keep their frozen story lists. What they produce is listed here as fixed input. Anything asked of them is an orchestrator note under `running-<batch>` in fix-plan.json.

**Precedence.** PRD.md and CLAUDE.md come first, then this file, then a batch's prd.json. A story that finds this file in conflict with the PRD stops and records the conflict; it does not choose. Human decisions are cited by id from HUMAN-DECISIONS.md (for example PRD-01).

**Notation.** `X | None` is a required key whose value may be `null`. This is the T0 rule: every field is required, has no default, rejects unknown fields and validates strictly. `list[...]` keys are required and may be empty only where stated. Reason codes are `snake_case` strings. `C` marks a committed path and `I` a git-ignored one.

---

## 0. Batch map

| Before | After | Why |
| --- | --- | --- |
| t4-comparison T4-01 (records) | **t0b-contracts** T0B-01, T0B-02 | One batch edits schema.py after t1-pilot (review-science #4, review-ml #47) |
| t14-report T14-01 (report records) | **t0b-contracts** T0B-03, T0B-04 | Same |
| t6-uncertainty T6-03 (gate recorder) and the merge part of t9 T9-03 | **t0b-contracts** T0B-06 (`gates.py`) | One writer for outputs/validation.json (review-science #43, review-release #4) |
| t4-comparison T4-02 to T4-06 | **t4-logic** T4L-01 to T4L-07 | Synthetic logic, no G0 dependency |
| t9-release T9-02 (bundle) and T4-06 build_bundle | **t4-logic** T4L-08 | PRD 8.4 names export.py as the manifest producer, and the first real card (T4) needs a real bundle before T9 runs |
| t4-comparison T4-07, T4-08 | **t4-real** T4R-03, T4R-04 | Need the human G0 decisions and real data |
| (gap) | **t4-real** T4R-01 UAVSAR reader, T4R-02 card figures A to C | No producer existed |
| t5-search T5-01 (masks) and t11-dataset T11-03 (mask hook) | **t11-dataset** T11-03 (`masks.py`) | One mask implementation, needed first by T11 (review-ml #7, review-science #22) |
| t7b-explorer T7B-07 (3D twins) | removed until PRD-09; slot used by **T7B-09** `/paper` | No heightmap producer or bundle path |
| t15-api endpoints left as problem+json (running) | **t15b-api** (new) | Remaining 8.5 endpoints, CSP and isolation headers, DEM proxy (review-release #33, #34) |
| (gap) | t10-core **T10-07**, **T10-08** | TypeScript encoder channels and terrain distances (review-ml #11, #38) |
| (gap) | t13-probe **T13-07** `/probe`, **T13-08** target embeddings and model background | review-ml #10, #37 |
| (gap) | t11-dataset **T11-07**, **T11-08** | Real DEM sources and the real 600,000-window build (review-release #34) |
| (gap) | t6-uncertainty **T6-09** | Uncertainty written back into comparisons; shortlist cards (review-science #32, #41) |
| (gap) | t9-release **T9-09** | Licence and dependency audit (review-release #22) |

---

## (a) Shared records

### a.1 Owner and rules

schema.py and `packages/schema/*` change only in **t0b-contracts**. t1-pilot has already added ProductRecord and Sources (T1-01). No other batch edits schema.py. A batch that needs a new field stops and asks the orchestrator for a t0b follow-up story.

Every record inherits the T0 `Record` base (`extra="forbid"`, `strict=True`, `frozen=True`, rust-regex). Every new record is appended to `CONTRACTS` with its own schema file. `make schema` regenerates the JSON Schema and zod, and `make schema-check` passes. Each t0b story also updates `pipeline/tests/test_json_schema.py` (the pinned contract list), `fixtures/schema-cases/cases.json` and `packages/schema` tests (review-science #0). Generated files are never merged by hand; after any merge the orchestrator runs `make schema`.

`SCHEMA_VERSION` stays `1.0.0`. No bundle has been published, and `test_schema_version_is_the_prd_example_version` pins it to the PRD example. Changing it needs a PRD amendment (part of PRD-01).

### a.2 Common types (T0B-01)

```
Body          = "moon" | "mars" | "earth"                         # T1 already defines it
EvidenceLabel = "Observed" | "Derived" | "Predicted" | "Assumed" | "Unavailable"   # PRD 3
Family        = "terrain_relief" | "terrain_texture" | "radar_cpr" | "model_embedding"
ReasonCode    = str matching ^[a-z][a-z0-9_]*$                    # vocabulary in a.5
RunId         = str matching ^sha256:[0-9a-f]{64}$                # PRD 8.4 example form
RepoPath      = relative POSIX path, same segment rules as BundlePath (T0)
WindowId      = str matching ^(moon|mars|earth)-[a-z0-9][a-z0-9-]*$, deterministic from body, frame or zone, and grid indices
Distribution  = {n: int, values: list[float], min, median, p05, p95, max: float | None}
                # block values sorted ascending; the order statistics are null exactly when n == 0
```

API family mapping (review-science #5): `family=terrain` returns `terrain_relief` and `terrain_texture` together. The optional `measure=relief|texture` selects which key orders the list. `family=radar` returns `radar_cpr` and `family=model` returns `model_embedding`.

### a.3 Record table

| Record (contract name, schema file) | Story | Bundle or output path |
| --- | --- | --- |
| WindowPolygon, EnvironmentFlags, WindowRecord (`window-record.schema.json`) | T0B-01 | inside windows/{body}.json |
| FingerprintRecord (`fingerprint-record.schema.json`) | T0B-01 | inside windows/{body}.json |
| WindowEntry, Windows = list[WindowEntry] (`windows.schema.json`) | T0B-01 | windows/{body}.json |
| DistanceEntry, UncertaintyComponents, ComparisonRecord (`comparison-record.schema.json`) | T0B-01 | — |
| Comparisons = list[ComparisonRecord] (`comparisons.schema.json`) | T0B-01 | comparisons/{target_id}.json |
| TileLayer, TargetRecord, Targets = list[TargetRecord] (`targets.schema.json`) | T0B-02 | targets.json |
| CardRecord, TableDRow, CampaignEntry, FigureRef (`card-record.schema.json`) | T0B-02 | cards/{card_id}.json |
| ModelRecord (`model-record.schema.json`) | T0B-03 | model/model_card.json |
| MetricRecord, G5ConditionRecord, MetricsFile (`metrics.schema.json`) | T0B-03 | report/metrics.json |
| EmbeddingRecord (`embedding-record.schema.json`), EmbeddingsIndex (`embeddings-index.schema.json`) | T0B-03 | model/embeddings.index.json; inside reference/background_* |
| CurvesFile (`curves.schema.json`), UmapHeader (`umap-header.schema.json`), ExamplesFile (`examples.schema.json`) | T0B-04 | report/curves.json, report/umap.bin header, report/examples.json |
| BackgroundFamily, ModelBackground, Background (`background.schema.json`) | T0B-04 | reference/background_{target_id}.json |
| `BUNDLE_PATHS` and generated `packages/schema/json/bundle-paths.json` | T0B-04 | path template to contract name, read by export.py and the web loader |
| `provenance.py` (run_id, config_version, file hashes, command line) | T0B-01 | used by every record writer (c.4) |
| GateRecord with the provenance fields, InputHash | T0B-05 (needs PRD-01) | validation.json, outputs/gates/*.json |
| ProductRecord, Sources (existing, t1-pilot T1-01) | — | sources.json |
| BundleManifest, BundleFile, ModelSummary (existing, T0) | — | manifest.json |

`BUNDLE_PATHS` (T0B-04). There is exactly one map. `export.validate_bundle` (T4L-08) and `bundle:pull` (T15B-01) read it from the generated JSON rather than keeping copies.

```
manifest.json                         -> BundleManifest
validation.json                       -> Validation
sources.json                          -> Sources
targets.json                          -> Targets
windows/{body}.json                   -> Windows
comparisons/{target_id}.json          -> Comparisons
cards/{card_id}.json                  -> CardRecord
reference/background_{target_id}.json -> Background
model/model_card.json                 -> ModelRecord
model/embeddings.index.json           -> EmbeddingsIndex
report/metrics.json                   -> Metrics
report/curves.json                    -> Curves
report/examples.json                  -> Examples
report/umap.bin                       -> UmapHeader (length-prefixed header; layout checked by report.read_umap)
figures/{name}.svg                    -> svg_allowlist (no <script>, on* attributes, foreignObject or external href)
tiles/{layer}/{z}/{x}/{y}.webp, windows/{body}.parquet, model/*.onnx, model/embeddings.f16.bin -> no JSON schema; budget and sha256 only
```

### a.4 Field definitions

**WindowPolygon** (T0B-01)
```
frame: "utm" | "polar_stereographic" | "equirectangular" | "aeqd_sphere"
crs: str                              # as read from the product (WKT or PROJ string), never assumed
epsg: int | None                      # Earth UTM zones; null where the product has none
utm_zone: str | None                  # e.g. "27N"; Earth only
ring_m: list[[float, float]]          # closed ring in the frame's metres
ring_lonlat: list[[float, float]]     # closed ring in degrees: WGS84 for Earth, planetocentric for Moon and Mars
```

**EnvironmentFlags** (T0B-01). This is the WindowRecord `environment_flags` value (PRD 5 environment records, PRD 6.1 permanent shadow).
```
search_region_id: str | None          # declared region from config earth_search.regions; null for targets
scene_ids: list[str]                  # radar scenes declared for the region (UAVSAR); [] when none
acquisition_dates: list[str]          # ISO dates of the radar acquisitions used; [] when none
snow_or_freeze: str | None            # "unknown" if applicable but unchecked; null on Moon and Mars
moisture_state: str | None            # same rule
surface_change_since_acquisition: str | None
permanent_shadow_fraction: float | None   # Moon area fraction 0..1 (6.1); null with a reason in evidence_status_by_field
surface_model_note: str | None        # Copernicus: surface model including vegetation and buildings, heights on EGM2008 (PRD 5)
```

**WindowRecord** (T0B-01). The field names are exactly those of PRD 8.12, with no extras. Zone and EPSG live in `polygon`. Region, scene and environment live in `environment_flags`. Scene product ids also appear in `product_ids`. This answers review-science #23 and #26 without a PRD amendment.
```
window_id: WindowId
body: Body
polygon: WindowPolygon
area_m2: float                        # 25,000,000 for a 5 km window; Earth sphere approximation noted (PRD 7 D29)
analysis_grid_m: float                # 90, or the coarsened grid with the factor in the fingerprint
product_ids: list[str]                # DEM tiles, WorldCover tile, radar products and scenes used
valid_fraction_by_observable: dict[str, float | None]
observed_interpolated_quality: {observed_fraction: float | None, interpolated_fraction: float | None,
                                source: str | None, reason: ReasonCode | None}   # PGDA78 count map (PRD 5)
environment_flags: EnvironmentFlags
evidence_status_by_field: dict[str, EvidenceLabel]
```

**FingerprintRecord** (T0B-01). Values are copied from T3 `terrain_fingerprint` and from T2 block statistics, never derived again (review-science #6).
```
window_id: WindowId
body: Body
kind: "terrain" | "radar"
product_ids: list[str]
evidence_label: EvidenceLabel         # Derived from Observed products; Unavailable when nothing is supported
input_hash: Sha256                    # arrays plus settings, so a changed polygon or setting changes it
run_id: RunId
terrain: TerrainFingerprint | None    # set iff kind == terrain
radar: RadarFingerprint | None        # set iff kind == radar

TerrainFingerprint: analysis_grid_m, coarsening_factor: float | None, resampler: "aeqd_sphere",
  valid_fraction: float, relief: TerrainVariant, texture: TerrainVariant
TerrainVariant: detrend: "none" | "plane", supported_lags_m: list[float],
  unsupported_lags: list[{lag_m: float, reason: ReasonCode}],
  nu_m: dict[str, float | None]                        # key = lag in metres as written by T3, isotropic summary
  nu_by_direction_m: dict[str, dict[str, float | None]],  # direction -> lag -> value, as T3 names them
  hurst: float | None, hurst_reason: ReasonCode | None,   # exactly one is null
  tilt: list[float] | None                             # fitted plane coefficients (texture only)
RadarFingerprint: product_id, band: str, frequency_or_wavelength: str, convention: str | None,
  support_m: float, aggregation: "ratio_of_mean_powers", blocks_independent: false (Literal),
  n_blocks: int, n_valid: int, valid_fraction: float,
  cpr_ratio_of_mean_powers: Distribution | None, cpr_mean_of_pixelwise_ratios: Distribution | None,
  sc: Distribution | None, oc: Distribution | None, total_power: Distribution | None,
  incidence_deg: {min: float, max: float} | None, handedness_ratio: Distribution | None,
  reason: ReasonCode | None
```

**WindowEntry** (T0B-01). One row of windows/{body}.json, which is what `/windows/{id}` serves.
```
window: WindowRecord
terrain: FingerprintRecord | None
radar: list[FingerprintRecord]
status: "eligible" | "rejected" | "unknown"
reasons: list[ReasonCode]             # ordered; [] iff eligible
grid_index: [int, int] | None
card_id: str | None
```
The windows/{body}.json "top-N subset" (PRD 8.4) is defined as every window referenced by a comparisons file of that body, plus every target and reference-set window. The limit is 5 MB per body.

**DistanceEntry** (T0B-01; review-science #1)
```
value: float | None
unit: str                             # e.g. "RMS of log nu", "CPR Wasserstein-1", "cosine distance"
evidence_label: EvidenceLabel         # Derived for terrain and radar; Predicted for model_embedding when value is set
status: "available" | "unavailable" | "not_comparable"
reason: ReasonCode | None             # null iff status == "available"
reference_spread: {min: float, median: float, max: float, n: int} | None
reference_spread_reason: ReasonCode | None   # exactly one of the pair is null
background_percentile: float | None
background_percentile_reason: ReasonCode | None
tied: bool | None                     # null when no spread was supplied
tie_group: str | None
tie_reason: ReasonCode | None         # e.g. spread_unavailable
```
Validators: a value of 0 is only a real zero (identical fingerprints) and never stands for missing. `model_embedding` with a value must be labelled Predicted. Because extra fields are forbidden, no `score` or combined field can exist (D07).

**UncertaintyComponents** (T0B-01; PRD 6.6; review-science #41). The keys are exactly `heterogeneity`, `measurement_error`, `statistical_prediction_error` and `scenario_sensitivity`, each a Component:
```
status: "available" | "unavailable"
evidence_label: EvidenceLabel
reason: ReasonCode | None
note: str | None                      # e.g. the PGDA78 clone-limits statement (6.6)
by_family: dict[Family, {spread_min, spread_median, spread_max: float | None, n: int | None}] | None
n_requested: int | None, n_used: int | None      # clones
scenarios: list[{name: str, change: str, values_by_family: dict[Family, float | None],
                 rank_by_family: dict[Family, int | None]}] | None   # scenario_sensitivity only; no probability or weight
rank_frequency: dict[Family, {frequency: float, count: int, n: int}] | None
```
No field combines components or families.

**ComparisonRecord** (T0B-01). These are exactly the PRD 8.12 field names.
```
target_window_id: WindowId
earth_window_id: WindowId
shipping_mode: "A" | "B" | "C" | None
comparable_observables: list[str]
excluded_observables_and_reasons: list[{observable: str, reasons: list[ReasonCode]}]
distances_by_family: dict[Family, DistanceEntry]   # all four keys always present
uncertainty_components: UncertaintyComponents
sort_rule: str | None                 # e.g. "pareto layers over terrain_relief, terrain_texture; then window_id"
claim_limits: list[str]
source_ids: list[str]                 # product ids; GLO-30 and WorldCover with version and year (review-science #26)
run_id: RunId                         # scoped run_id over source_ids (see c.4)
```
Shortlist membership is held on TargetRecord, not on ComparisonRecord.

**TileLayer** (T0B-02; review-science #33). This replaces a separate tile metadata path; it lives inside TargetRecord.
```
layer: str, body: Body, crs: str, units: str | None, evidence_label: EvidenceLabel
source_product_ids: list[str]
origin_x_m: float, origin_y_m: float, y_direction: "down" | "up", tile_px: 512 (Literal)
metres_per_pixel_by_zoom: list[float], zoom_min: int, zoom_max: int
extent_m: [float, float, float, float]
omitted_tiles: list[[int, int, int]]  # all-nodata tiles (z, x, y), never written as images
legend: {kind: "classes", classes: list[{code: int, label: str, rgba: [int, int, int, int]}]}
      | {kind: "numeric", min: float, max: float, scale: "viridis" | "cividis", unit: str | None}
hillshade: {azimuth_deg: float, altitude_deg: float} | None
encoding: {format: "webp", lossless: bool, quality: int | None}
bytes_total: int
mock: bool
```

**TargetRecord**, Targets = list[TargetRecord] (T0B-02; review-science #2)
```
target_id: str                        # e.g. moon-cr-site01-w000 (PRD 8.4 example ids)
mock: bool
run_id: RunId | None
body: Body
name: str
description: str                      # PRD 3 and 14 wording, e.g. "Candidate Artemis region at Connecting Ridge"
product_ids: list[str]
crs: str                              # read from the product
projection: {kind: "polar_stereographic" | "equirectangular" | "geographic", params: dict[str, float | str]}
footprint: WindowPolygon | None       # Site01 ROI union or the frozen CTX window; null until G0-02
primary_window_id: WindowId | None    # null until frozen at G0 (config targets.*.primary_window_id)
window_grid: {window_m: float, stride_m: float, n_generated: int | None, n_eligible: int | None}
reference_window_ids: list[WindowId]
shortlist: {earth_window_ids: list[WindowId], rule: str, n_compared: int | None} | None
shortlist_reason: ReasonCode | None   # e.g. search_not_run; exactly one of the pair is null
shipping_mode: "A" | "B" | "C" | None
tile_layers: list[TileLayer]
```

**CardRecord** (T0B-02; PRD 10; review-science #2, #3). A card does not store `bundle_version`, which only exists once the bundle is assembled. The page shows `manifest.bundle_version` inside the card body (D08, PRD 8.6). The gate states shown on the page come from validation.json. The card stores only the `g5_status` it was built under, for the encoder guard.
```
card_id: str                          # "<target_window_id>__<earth_window_id>"
mock: bool
run_id: RunId
shipping_mode: "A" | "B" | "C"        # never null; export refuses a null mode
title: str                            # PRD 4 title for the mode; Mars wording recorded in T4L-06 notes
wording: "candidate window identified by this search" (Literal)
target: {target_id: str, target_window_id: WindowId, body: Body, polygon: WindowPolygon, product_ids: list[str]}
earth: {earth_window_id: WindowId, polygon: WindowPolygon, product_ids: list[str], search_region_id: str | None}
comparison_ref: {path: BundlePath, target_window_id: WindowId, earth_window_id: WindowId}
q1_compared: str
q2_interpretation: {text: str, evidence_label: "Assumed"}
q3_measurements: list[TableDRow]      # PRD 10 table D
q4_differences: {limits: list[str],   # exactly the seven Earth limits, or the Mars list, of PRD 10
                 differing: list[str], ambiguous: list[str], not_observed: list[str]}
q5_stability: {uncertainty_components: UncertaintyComponents,
               tied_by_family: dict[Family, bool | None], tie_group_by_family: dict[Family, str | None]}
q6_campaign: {access: CampaignEntry, permits: CampaignEntry, season: CampaignEntry,
              land_status: CampaignEntry, logistics: CampaignEntry}
q7_field_experiment: {text: str, evidence_label: "Assumed", status: "proposed" (Literal)}
distances_by_family: dict[Family, DistanceEntry]    # copied unchanged from the comparison
radar_context: list[{product_id: str, band: str, observable: str, statistic: str, value: float | None,
                     unit: str | None, evidence_label: EvidenceLabel, support_m: float | None, reason: ReasonCode | None}]
figures: list[FigureRef]
g5_status: GateStatus
claim_limits: list[str]

TableDRow: {observable: str, value: float | None, unit: str | None, evidence_label: EvidenceLabel,
            support: str, product_id: str | None, band: str | None, geometry: str | None, reason: ReasonCode | None}
CampaignEntry: {value: str, source: str | None}     # value "unknown" iff source is null
FigureRef: {figure: "A" | "B" | "C", path: BundlePath | None, alt: str, reason: ReasonCode | None}
```

**ModelRecord** (T0B-03). These are exactly the PRD 8.12 fields; `mock` lives in the manifest and the export record.
```
model_id, version: str; architecture: str; parameter_count: int
training_data: {bodies: list[Body], product_ids: list[str],
                window_counts: dict[Body, dict["train" | "val" | "test", int | None]], split_groups: dict[Body, str]}
objectives_and_weights: {vicreg: {invariance: float, variance: float, covariance: float} | None,
                         alpha_phys: float, lambda_max: float, schedule: str}
seeds: list[int]; metrics_with_ci: list[MetricRecord]; g5_status: GateStatus
intended_use: str; limitations: str; licence: str; onnx_sha256: Sha256 | None
```

**MetricRecord** (T0B-03; review-ml #40)
```
test_id: "physics_probe" | "morphology_probe" | "body_invariance" | "physics_agreement" | "target_retrieval"
       | "seed_stability" | "held_out_body" | "out_of_distribution"        # PRD 11.6
measure: str; target: str | None; body: Body | "pooled" | None
value: float | None; ci_low: float | None; ci_high: float | None
n_blocks: int | None; n_bootstrap: int | None          # null exactly when value is null; otherwise n_bootstrap >= 1
unavailable_reason: str | None                         # non-empty iff value is null
evidence_label: "Predicted" (Literal)
baselines: list[{name: str, value: float | None, ci_low: float | None, ci_high: float | None,
                 paired_diff_ci_low: float | None, paired_diff_ci_high: float | None, unavailable_reason: str | None}]
```
Only `ci_low <= ci_high` is enforced. A percentile interval may exclude the point estimate.

**G5ConditionRecord** (T0B-03; review-ml #41)
```
condition: 1..6; test_id: str; statistic: "lower_95" | "point" | "paired_interval"
threshold: float | None               # copied from config ml.g5; null only for condition 6
observed: float | None; passed: bool | None; reason: str | None   # passed null requires reason
components: list[{component: str, observed: float | None, ci_low: float | None, ci_high: float | None,
                  passed: bool | None, reason: str | None}]
                  # e.g. "relief_lag_270", "dino:morph_gain"; passed is the 11.7 conjunction of these
```

**MetricsFile** (T0B-03). This is report/metrics.json.
```
mock: bool; run_id: RunId | None; model_id: str; evaluated_split: "val" | "test"
g5: {status: GateStatus, conditions: list[G5ConditionRecord], failed_tests: list[str]}
    # copied unchanged from the G5 GateRecord and outputs/ml/evaluation/test/g5_conditions.json (T12B-06)
tests: list[MetricRecord]
ablations: list[{run_name: str, status: "available" | "unavailable", reason: str | None, metrics: list[MetricRecord]}]
run_record: {config_hash: Sha256, data_manifest_sha256: Sha256 | None, split_map_sha256: Sha256 | None,
             checkpoint_sha256s: list[Sha256], git_sha: str | None, uv_lock_sha256: Sha256,
             hardware: str, gpu_hours_total: float | None, gpu_hours_partial: bool}
```

**EmbeddingRecord** (T0B-03). These are exactly the PRD 8.12 fields: `window_id`, `body`, `model_id`, `embedding_f16` (list[float] of length 256, each exactly representable in float16), `input_hash: Sha256`, `valid_fraction: float` and `ood_score: float | None`.

**EmbeddingsIndex** (T0B-03). This is model/embeddings.index.json.
```
model_id: str; onnx_sha256: Sha256 | None; mock: bool; dim: 256; dtype: "float16"; byte_order: "little"
n_rows: int; rows: list[{row: int, window_id: WindowId, body: Body, split: "test", input_hash: Sha256,
                         valid_fraction: float, ood_score: float | None, ood_flag: bool | None}]
```

**CurvesFile**, **UmapHeader**, **ExamplesFile** (T0B-04). These are the T14-01 draft definitions with the review-ml fixes. CurvesFile holds `loss_terms`, `gpu_hours` (per run, with null plus reason, and a `partial` flag), `tradeoff` points and `validation_probe_metrics` (which may be Unavailable). Ablations live in MetricsFile, not CurvesFile (review-ml #44). Each file has `mock` and `run_id`. ExamplesFile entries carry `evidence_label` Predicted for the encoder and Derived for the deterministic fingerprint.

**BackgroundFamily**, **ModelBackground**, **Background** (T0B-04; review-science #30, review-ml #9 and #10). Background is reference/background_{target_id}.json and also carries the target embedding.
```
Background:
  target_id: str; mock: bool; run_id: RunId
  statement: "empirical background comparison, not a physical truth test" (Literal)
  seed: int; requested_n: int; actual_n: int; sample_window_ids: list[WindowId]; source_ids: list[str]
  families: {terrain_relief: BackgroundFamily | None, terrain_texture: BackgroundFamily | None,
             radar_cpr: BackgroundFamily | None, model_embedding: ModelBackground | None}
  family_reasons: dict[Family, ReasonCode | None]      # exactly one of family/reason is null per key
BackgroundFamily: unit: str; evidence_label: EvidenceLabel; bin_edges: list[float]; counts: list[int]; n: int
  percentile_definition: "linear_within_bin_v1" (Literal); direction: "smaller_is_closer" (Literal)
ModelBackground: BackgroundFamily fields, plus model_id: str; onnx_sha256: Sha256; g5_status: GateStatus
  evidence_label: "Predicted" (Literal); target_embedding: EmbeddingRecord; sample_window_ids_sha256: Sha256
```
`linear_within_bin_v1` is defined as follows. For a distance d, p = 100 × (sum of the counts of bins entirely below d, plus counts[k] × (d − lo_k)/(hi_k − lo_k) for the bin k that contains d) / n. A d below the first edge gives 0 and a d above the last edge gives 100. The Python reference is `matching.background_percentile` (T4L-05). TypeScript mirrors it in T13-03 and checks parity against cases in `fixtures/parity/matching_synthetic.json`. The probe computes a percentile only against the histogram of the same family, and for the model family also requires the same `model_id` and `onnx_sha256` (review-ml #9).

**GateRecord** (T0B-05; PRD 8.12 and PRD 9; review-science #16 and #42, review-release #5)

Option A, recommended, requires **PRD-01**: a human approves amending the PRD 8.12 GateRecord line. The T0 test `test_gate_record_fields_and_statuses_are_exactly_prd_8_12` reads the field list from PRD.md, so the field list cannot change without the amendment.
```
gate_id: GateId; status: GateStatus
evidence_path: RepoPath | None        # committed evidence file
run_id: RunId | None                  # required for PASS (review-release #6)
scope: NonEmptyStr | None             # G6 scope starts with "bundle <bundle_version>"
result: NonEmptyStr | None            # one line; details live in the evidence file
consequence: NonEmptyStr | None       # PRD 9 failure-action text for FAIL, PARTIAL and NOT_RUN
command: NonEmptyStr | None           # NEW: shlex-joined argv of the command that produced the record
config_version: Sha256 | None         # NEW: sha256 of config/run.yaml as read
input_hashes: list[InputHash]         # NEW: every file read, sorted by path
InputHash: {path: RepoPath, sha256: Sha256}
```
Validators: PASS, FAIL, PARTIAL and NOT_APPLICABLE require a non-null command, config_version, evidence_path and scope, and non-empty input_hashes. PASS also requires run_id. A NOT_RUN entry that gates.py writes for a missing source has every nullable field null and `input_hashes == []`. There are no wall-clock times anywhere, so re-recording is byte-identical.

T0B-05 also updates every construction site in the same story, because the extension changes required fields: `export.mock_gate_records` (and the regenerated mock bundle), the G0 block builder in `pilot.py` (T1-09) and `record-g1` in `polarimetry.py` (T2-06). Each site gets the three arguments from provenance.py. The committed records are regenerated with the owners' own commands. This is the only planned edit of a running batch's files after it merges.

Option B, if PRD-01 is declined: GateRecord is unchanged. The command, config_version and input_hashes go into the evidence file at `evidence_path`. `gates verify` reads them from there, and the construction sites are not edited.

### a.5 Reason-code vocabulary

Each owning module defines its codes as a `StrEnum` and the records hold them as `ReasonCode` strings, so no schema change is needed when a code is added. Consumers import the codes; they never retype them.

| Module (story) | Codes |
| --- | --- |
| terrain.py (T3, running) | below_min_valid_fraction, insufficient_lags, insufficient_lag_span, nu_zero (as merged; read the code) |
| polarimetry.py (T2, running) | the InvalidReason values as merged |
| masks.py (T11-03) | land_cover_below_min_fraction, land_cover_nodata, unknown_class, worldcover_missing, water_rule_unset, not_applicable_body, ctx_valid_fraction_below_min, ctx_qa_missing, ctx_qa_threshold_unset |
| fingerprints.py (T4L-01) | convention_unknown, missing_channel, no_valid_blocks |
| matching.py (T4L-02 to T4L-05) | band_mismatch, band_unknown, convention_unknown, geometry_incompatible, incidence_unit_unknown, incidence_bins_unset, calibration_unresolved, support_mismatch, coverage_below_threshold, qa_missing, cross_band_calibration_unresolved, radar_not_completed, shipping_mode_unset, radar_unavailable_for_body, no_shared_lags, nu_zero, reference_set_too_small, background_sample_size_unset, spread_unavailable, g5_not_pass, encoder_not_evaluated |
| search.py (T5) | regions_unset, arcadia_bbox_unset, target_window_unfrozen, cache_missing, earth_radar_fingerprint_not_built, search_not_run, permanent_shadow_product_unset, dem_valid_fraction_below_min |
| export.py (T4L-06 to T4L-08) | figure_not_rendered, stale_run_id, not_present |

---

## (b) Artifact producers

Every bundle path in PRD 8.4, followed by the non-bundle outputs. "Canonical location" is where the producer writes. `export stage` (T4L-08) copies from there. A writer for a JSON bundle file always goes through the export.py writer functions (T4L-06), which validate against `BUNDLE_PATHS` and write canonical JSON.

| Bundle path | Producer (batch, story) | Command | Canonical location | Consumers | Staging |
| --- | --- | --- | --- | --- | --- |
| manifest.json | t4-logic T4L-08 | `make bundle` → `python -m terraluna_pipeline.export assemble` | release/<v>/ (I) | bundle:pull and loader (t15-api, T15B-01); API /health and /manifest; CI bundle job (T9-07); make validate and publish (T9-03, T9-05); `make -C paper numbers-check BUNDLE_VERSION=<v>` (t16) | written at assembly |
| sources.json | t1-pilot T1-03 to T1-08 → `outputs/sources.json`; t4-real T4R-01 → `outputs/uavsar/sources.json`; t5 T5-07 → `outputs/search/sources.json`; t6 T6-01 → `outputs/uncertainty/sources.json`; t11 T11-07 and T11-08 → `outputs/ml/sources.json` | owners' fetch or record commands | C | /data (t7-shell T7-05); API (T15B-02); README attributions (T9-08); provenance.run_id | stage merges by product_id; identical duplicates are kept once and differing duplicates fail |
| targets.json | t5 T5-03 (records), T5-06 (tile_layers), T5-07 (counts, shortlist) | `search targets`, `search tiles`, `search compute` | `outputs/search/targets.json` (C, < 50 kB) | /targets (T15B-02); maps (T7B-03 to T7B-05); probe target list (T13-05, T13-07); cards (T4L-06); G6 | copy |
| windows/{body}.parquet | t5 T5-07 | `search compute` | `data/processed/windows/{body}.parquet` (I) | t6 T6-07 and T6-08; paper Appendix A | copy |
| windows/{body}.json | t4-real T4R-04 (slice: two windows), then t5 T5-07 (full set) via `export.write_windows` | `export build-real-card`; `search compute` | `data/processed/windows/{body}.json` (I); sha256 in `outputs/search/summary_<target_id>.json` | /windows/{id} and /targets/{id} (T15B-03, T15B-02); maps (T7B-04, T7B-05); /compare (T7B-06); fingerprint end-to-end check (T15B-06) | copy |
| comparisons/{target_id}.json | t4-real T4R-04 (slice), then t6 T6-09 (final, with uncertainty) via `export.write_comparisons`. The raw t5 input is `data/processed/search/<target_id>/comparisons.raw.json` (I) | `export build-real-card`; `search compute` (raw); `uncertainty annotate <target_id>` | `outputs/comparisons/<target_id>.json` (C, ≤ 2 MB, at most 500 records in T4L-04 trim order) | matches and compare (T15B-03, T15B-05); cards (T4L-06); /explore/earth (T7B-05); G4 (T6-04); G6 (T9-04) | copy |
| cards/{card_id}.json | T4L-06 `export cards`, run by t4-real T4R-04 (slice) and t6 T6-09 (shortlist) | `export cards` (and `export cards --check`) | `outputs/cards/<card_id>.json` (C) | /site/[cardId] and OG image (T7B-01, T7B-02); smoke (T9-06); G6 (T9-04) | copy; a card whose pair is absent from the current comparisons fails `export cards --check` |
| figures/*.svg | t4-real T4R-02 `figures cards`, run in T4R-04 and T6-09 | `python -m terraluna_pipeline.figures cards` | `data/processed/figures/<card_id>_{A,B,C}.svg` (I); index `outputs/figures/index.json` (C: sha256, bytes, card, command) | /site via the T15B-01 loader | copy; sha256 must match the index |
| validation.json | t0b T0B-06 `gates merge` (the only writer of outputs/validation.json) | `python -m terraluna_pipeline.gates merge` | `outputs/validation.json` (C) | /methods (t7-shell T7-04); /model G5 (T14-06); /gates (t15-api); cards (T4L-06); G6 and publish | `gates snapshot --bundle-version <v>` at assembly (see c.3) |
| tiles/{layer}/{z}/{x}/{y}.webp | t5 T5-06 | `search tiles` | `data/processed/tiles/{layer}/...` (I) | maps (T7B-03, T7B-04) from `NEXT_PUBLIC_ASSET_BASE/<v>/` | copy; ≤ 300 MB total |
| tile metadata | t5 T5-06 → `TargetRecord.tile_layers` | — | inside targets.json | T7B-03 | inside targets.json |
| model/encoder.onnx, model/encoder.fp16.onnx | t13 T13-01, T13-02 (real: the frozen checkpoint after G5-06) | `export_onnx export --checkpoint <frozen> --fp16 --windows 256 --out data/processed/model` | `data/processed/model/` (I) plus `export_record.json` | probe worker (T13-03, T13-05); model card onnx_sha256 (T14-02); G5 parity (T12B-06) | copy; refused if the export record says random-init or mock, or the checkpoint is not in the freeze record |
| model/model_card.json | t14 T14-02 | `make report` → `report build` | `data/processed/report/model/model_card.json` (I) | /model (T14-06); /model API (T15B-05); HF mirror (T9-05) | copy |
| model/embeddings.f16.bin, model/embeddings.index.json | t12b T12B-05b (needs an UnsealToken, G5-01) | `evaluate export-embeddings` | `data/processed/model/` (I) | UMAP (T14-04); paper Appendix B | copy; 20 MB total (PRD-03) |
| report/metrics.json, curves.json, umap.bin, examples.json | t14 T14-02 to T14-04 | `make report` | `data/processed/report/report/` (I) | /model (T14-06, T14-07); paper (t16b); G6 | copy; 15 MB total |
| reference/background_{target_id}.json | terrain part: t5 T5-05 → `outputs/search/reference/background_<target_id>.json` (C, model_embedding null with reason `encoder_not_evaluated`); model part: t13 T13-08 → `data/processed/model/background_model_<target_id>.json` (I) | `search compute`; `export_onnx reference` | — | probe percentiles (T13-03, T13-05); G6 | composed by `export stage` through `export.compose_background` (T4L-08); the model part must match the manifest model |

Non-bundle outputs:

| Path | Producer | Notes |
| --- | --- | --- |
| outputs/pilot/*.json, preview PNG | t1-pilot T1-03 to T1-09 | G0 evidence. gates.py imports the G0 block from `outputs/pilot/g0_evidence.json` |
| outputs/gates/G0.json | a human, with `gates attest-g0` | Never an agent (G0-01) |
| outputs/gates/G2.json, g2_evidence.json, g2_pairs.json | t4-real T4R-03 | pairs generated by `matching pairs` from config and sources, never chosen by hand |
| outputs/gates/G3.json, G4.json and their evidence | t6 T6-05, T6-06, T6-04 | evidence under analysis/ |
| outputs/gates/G5.json | t12b T12B-07 `evaluate g5` | evidence `outputs/ml/evaluation/test/g5_conditions.json` |
| outputs/gates/G6.json, g6_evidence_<v>.json | t9 T9-03 and T9-04 (`release validate`) | scope names the bundle |
| outputs/validation.json | t0b T0B-06 `gates merge` | see (d) |
| outputs/search/summary_<target_id>.json, blocked.json, targets.json, reference/* | t5 T5-05 to T5-07 | |
| outputs/uncertainty/*.json | t6 T6-01, T6-02, T6-09 | clone and scenario results with input hashes |
| outputs/uavsar/<scene_id>.json | t4-real T4R-01 | scene record after the human download (ACC-01) |
| outputs/ml/evaluation/<split>/evaluation_metrics.json, evaluation_curves.json, g5_conditions.json, freeze.json; outputs/ml/test_read_log.json | t12b T12B-05a, T12B-05b, T12B-06 | not named metrics.json (review-ml #49) |
| outputs/ml/dataset/manifest.json | t11 T11-08 | shards stay in ml/shards/ (I) |
| outputs/smoke/<v>.json | t9 T9-06, run by a human | G6 evidence |
| outputs/publish-plan.json | t9 T9-05 | no credentials |
| outputs/report.md | t9 T9-08 | written report (PRD 16), generated with a source path per number |
| analysis/*.json | t6 T6-04 to T6-08 | Appendix A inputs |
| fixtures/parity/terrain_synthetic.json, geometry_synthetic.json | t3-terrain T3-07 (running) | exact input arrays |
| fixtures/parity/matching_synthetic.json | t4-logic T4L-03 (`matching parity`) | distances and percentile cases for T10-08 and T13-03 |
| fixtures/parity/encoder_channels.json | t11 T11-06 (`dataset parity`) | for T10-07 |
| fixtures/parity/terrain_real_<body>.json | t5 T5-07 (real; LIC-05) | the 20 real windows per body of PRD 8.8 (review-release #3) |
| fixtures/onnx/parity_spec.json | t13 T13-02 | specification only |
| fixtures/mock-report/* | t14 T14-05 `report mock` | copied byte for byte into the mock bundle by export.py (review-ml #14) |
| fixtures/mock-bundle/* | export.py mock generator (T0, T0B-05, T4L-07, T14-05) | regenerate with `make mock-bundle`; never place files by hand |

---

## (c) Bundle staging and assembly

The order follows the PRD 8.9 runbook: `make bundle` → `make validate` → `make publish`, then the pull request bumping `BUNDLE_VERSION` → preview check → merge → smoke test. Nothing in it is circular because of three rules. The bundle reads outputs/ and never contains its own G6 result. Validate reads release/ and writes only outputs/. Publish reads both and writes neither.

### c.1 make bundle (t4-logic T4L-08)

1. `python -m terraluna_pipeline.export stage --out data/processed/bundle-staging`
   - Copies every path in the (b) table from its canonical location. A path with no producer yet is recorded as `not_present` in `STAGING.json` and is never replaced by a placeholder.
   - A real bundle requires validation.json, sources.json, targets.json, at least one comparisons file and at least one card (PRD 1, submission criterion 2). If any is missing, stage exits 3 naming it.
   - Validates each JSON file against its `BUNDLE_PATHS` contract and each SVG against the allowlist, and checks the PRD 8.4 budgets.
   - Merges the sources files, composes the background files, and refuses mock records, anything under fixtures/, random-init or mock model exports, and ONNX files whose sha256 differs from `model_card.onnx_sha256`.
   - Recomputes every record's scoped run_id (c.4). A stale record makes stage exit 1 with reason `stale_run_id`, which is the PRD 12 end-to-end check "a changed polygon or config invalidates cached results".
   - Writes `STAGING.json`: bundle path, source path, sha256, bytes and producer for each file.
2. `python -m terraluna_pipeline.export assemble --staging data/processed/bundle-staging --bundle-version <v> --release-dir release`
   - `<v>` comes from `export bundle-version --utc <YYYY-MM-DDTHHMMZ> --sha <short>`, in the PRD 8.4 form `2026-11-15T0900Z-3f9c2a1`. The short sha must be a prefix of HEAD.
   - The git tree must be clean, and an existing release/<v>/ is refused. Assembly writes to a temporary directory and renames it only after `validate_bundle(production=True)` passes.
   - validation.json is `gates snapshot` (c.3). The manifest fields are: run_id (scoped over all staged sources), git_sha HEAD, created_utc, shipping_mode from config (a null mode refuses a real bundle), targets from targets.json, model (EXPERIMENTAL; g5 from validation.json; null when no model is staged) and files.

### c.2 make validate (t9-release T9-03 and T9-04)

`python -m terraluna_pipeline.release validate --bundle-version <v>`:
1. Runs the owners' offline checks and fails on any drift: `pilot check g0`, `gates verify`, `matching check-g2`, `checks verify-records`, `uncertainty verify`, `evaluate verify-g5`, `export cards --check`, `figures cards --check`.
2. Requires the G0 to G5 entries of release/<v>/validation.json to equal those of outputs/validation.json byte for byte. A record changed after assembly fails, so the operator builds a new bundle.
3. Runs the G6 offline consistency checks (T9-04) against release/<v>/ and apps/web/data pulled from <v>.
4. Writes `outputs/gates/G6.json` with scope `bundle <v>`. The status is FAIL with the failed checks, or PARTIAL naming what remains human or online: the production smoke test, the browser probe check, link checks, the claims audit and the deadline. It is PASS only when `outputs/smoke/<v>.json` (schema-valid, mock false), a link-check record, a browser-probe record and a claims-auditor review for <v> all exist. Validate then runs `gates merge`.
5. Never writes release/. The operator commits outputs/gates/G6.json and outputs/validation.json.

### c.3 Snapshot rule (t0b T0B-06)

`gates snapshot --bundle-version <v> --out <path>` copies G0 to G5 from outputs/validation.json unchanged. It writes G6 as NOT_RUN with null fields and the scope `G6 for bundle <v> is evaluated after assembly by make validate and recorded in outputs/validation.json of the repository`. A G6 record for an earlier bundle is never copied into a later one. Whether the shipped /methods may show this NOT_RUN for G6 is decision PRD-02 (recommended: yes).

### c.4 Scoped run_id (t0b T0B-01, provenance.py)

`provenance.run_id(config_path, products)` returns `"sha256:" + sha256(canonical_json({"config_sha256": sha256(config bytes), "products": sorted([product_id, checksum or null])}))`. Each record computes it over the products it lists: `source_ids`, `product_ids` or the inputs of its evidence. The manifest computes it over the staged sources.json. There is a single implementation, used by export, search, uncertainty, gates, evaluate and report (review-science #38). Any edit to config/run.yaml changes every run_id. That is the PRD 8.13 rule, and config is frozen after G0.

### c.5 make publish (t9-release T9-05)

`make publish BUNDLE_VERSION=<v>` requires:
- a clean tree;
- `validate_bundle(production=True)` passing on release/<v>/;
- outputs/validation.json holding a G6 entry with scope `bundle <v>` and status PARTIAL or PASS. The bundle's own G6 is always NOT_RUN by c.3, so publish never reads it (review-release #8).

Without flags, publish prints the plan to outputs/publish-plan.json. Only `--i-am-a-human-and-approve-upload` reaches the upload path. It is a human step (REL-01).

---

## (d) outputs/validation.json

- **Owner and module:** `terraluna_pipeline.gates` (`pipeline/src/terraluna_pipeline/gates.py`), story **t0b T0B-06**. It is the only code that writes outputs/validation.json. Producers write only their own fragment `outputs/gates/<GATE>.json` (one GateRecord) and its evidence file, using `gates.write_fragment(record)`.
- **Commands:**
  - `gates merge` writes outputs/validation.json.
  - `gates verify` is offline: it re-hashes each entry's `input_hashes`, checks run_id freshness, requires the committed file to equal `merge` output byte for byte, and writes nothing.
  - `gates attest-g0 --mode A|B|C --operator NAME --confirm I-RECORD-THE-G0-DECISION` is for humans only. No Make target, check or CI step calls it, and a test greps for this.
  - `gates snapshot` is described in c.3.
  - `write_fragment` is a library function.
- **Sources per gate:**

| Gate | Source | Producer |
| --- | --- | --- |
| G0 | `outputs/gates/G0.json` (human attestation) if present, else the GateRecord block in `outputs/pilot/g0_evidence.json` (only NOT_RUN or PARTIAL) | human (G0-01) or t1-pilot T1-09 |
| G1 | `fixtures/polarimetry/g1-record.json` | t2-polarimetry T2-06 (`record-g1`) |
| G2 | `outputs/gates/G2.json` | t4-real T4R-03 |
| G3 | `outputs/gates/G3.json` | t6 T6-05 and T6-06 (one combined record) |
| G4 | `outputs/gates/G4.json` | t6 T6-04 |
| G5 | `outputs/gates/G5.json` | t12b T12B-07 (`evaluate g5`; computed by `g5.py`, T12B-06) |
| G6 | `outputs/gates/G6.json` | t9 T9-03 (`release validate`) |

- **Merge-by-gate_id semantics:**
  - The output is a JSON array of exactly seven GateRecords in G0 to G6 order.
  - Each entry is byte-identical, in canonical JSON, to its source.
  - A gate with no source is NOT_RUN with null fields and `input_hashes: []` (PRD 9: every gate starts NOT_RUN).
  - Merging never alters another gate's entry, never promotes a status, and never reads or writes a wall-clock time.
  - Merge fails, naming the file, when a fragment's gate_id differs from its file name, when a gate appears twice, when an entry fails GateRecord validation (for example PASS without run_id), when evidence lies under fixtures/mock-bundle or carries `mock: true`, or when G0 shows PASS without a human attestation.
  - An attestation must hash `outputs/pilot/g0_evidence.json` and config/run.yaml, and config `run.shipping_mode` must equal the attested mode.
- **Merge conflicts:** a story that writes a fragment also runs `gates merge` and commits both files. When two branches both changed outputs/validation.json, the orchestrator never hand-merges it. It keeps both fragments and reruns `gates merge`.

---

## (e) Make targets and the merge order

T0 owns, unchanged: setup, check, test, setup-py, check-py, test-py, setup-ts, check-ts, test-ts, schema, schema-check, mock-bundle, mock-bundle-check, web-build, web-smoke and web-dev.

| Target | Owner | Recipe (POSIX sh, GNU Make 3.81) |
| --- | --- | --- |
| bundle | t4-logic T4L-08 | `export stage`, then `export bundle-version` and `export assemble` |
| train, sweep | t12a T12A-05 | `python -m terraluna_ml.train run` for the sweep entry named RUN with SEED; `python -m terraluna_ml.train sweep` for ROUND=1\|2, one process per visible GPU; `DRY_RUN=1` passes `--dry-run`; fails on unset RUN, SEED or ROUND |
| evaluate, evaluate-freeze, evaluate-g5 | t12b T12B-07 | `evaluate run --split val`, `evaluate freeze`, `evaluate g5`; no target ever runs read-test-blocks |
| report | t14 T14-05 | `report build --evaluation outputs/ml/evaluation/test --out data/processed/report`. PRD 12 lists `make evaluate` as writing report/*.json; here that is split into evaluate plus report, recorded in T14-05 notes |
| pilot | t9 T9-01 | `release run pilot`: `pilot record <s>` and `pilot check <s>` for every T1 source; `FETCH=1` runs `pilot fetch <s>` first |
| build-data | t9 T9-01 | `release run build-data`: `search compute <t>` and `search tiles` for each configured target, then `uncertainty annotate <t>`, `export cards`, `figures cards` |
| dataset | t9 T9-01 | `release run dataset`: `terraluna_ml.dataset build` (T11-08) |
| export-model | t9 T9-01 | `release run export-model CHECKPOINT=<p> OUT=<dir>`; both required, with no default checkpoint |
| parity | t9 T9-01 | T3-07's parity subcommand (read from merged terrain.py), `matching parity`, `dataset parity`, then `pnpm --filter @terraluna/core test` and `pnpm --filter @terraluna/web test:parity` |
| validate | t9 T9-03 | `release validate --bundle-version $(BUNDLE_VERSION)` |
| publish | t9 T9-05 | `release publish --bundle-version $(BUNDLE_VERSION)`; never passes the upload flag |
| smoke | t9 T9-06 | `node scripts/release/smoke.mjs --url $(URL) --expect-bundle-version $(EXPECT) --probe-lat $(PROBE_LAT) --probe-lon $(PROBE_LON)` |
| licenses, audit | t9 T9-09 | pip-licenses and license-checker against the LIC-04 allowlist; pip-audit and pnpm audit |
| all | t9 T9-01 | pilot, build-data, parity, bundle, validate, in PRD 8.9 order. Excludes dataset, train, sweep, evaluate, report and every credential-gated download, with a comment saying so |

**Rules.** Each Makefile-editing story appends only its own targets, as one block at the end of the file headed `# <batch> <story>`, with its own `.PHONY:` line directly above each rule. No existing line is edited. Merge conflicts are then append-only, and the orchestrator keeps both blocks. T9-01 adds a test that fails when any target has more than one recipe.

**Makefile merge order.** T4L-08, T12A-05, T12B-07, T14-05, T9-01, then T9-03, T9-05, T9-06 and T9-09 in that order.

**CI.** Only t9 T9-07 edits `.github/workflows/ci.yml`, and it merges last, after t13-probe (onnx and parity), t7-shell T7-07 (e2e) and T9-09 (licences). Other batches put requests in their notes. The root pyproject.toml is edited only by the orchestrator (g), apart from dependency lines.

---

## (f) CLI convention

- Every command is `python -m terraluna_<pkg>.<module> <subcommand> [options]` with argparse subcommands (`required=True`, no default subcommand), as in T0 schema.py and export.py and T1 pilot.py. A module with a single action still uses one subcommand, for example `train run`.
- Every CLI module exposes `main(argv: Sequence[str] | None = None) -> int`, and `if __name__ == "__main__": raise SystemExit(main())`. Tests call `main([...])` and check exit codes.
- Exit codes: 0 done; 1 a check failed (drift, invalid or stale input); 2 usage (argparse); 3 blocked by a named missing human decision or input. Blocked commands print `blocked: <codes>` and write nothing except an explicit blocked record where the story says so.
- Only `fetch` subcommands touch the network. Their downloads go to data/raw/ (I). `record`, `compute` and `build` read only caches and committed files. Every command that writes committed outputs has an offline `check` or `verify` twin that recomputes in memory, compares byte for byte and writes nothing. Gate checks use only the twins.
- JSON outputs go through `export.canonical_json` (sorted keys, 2-space indent, LF, trailing newline, no NaN). No wall-clock time appears in committed records; the T1 practice of copying the retrieval time from fetch metadata applies.
- The T9-01 launcher (`python -m terraluna_pipeline.release run <target>`) holds one table from target to (module, fixed argv, owning story). It checks each module with `importlib.util.find_spec` and its subcommand with `main(["<sub>", "--help"])`, then calls `main(argv)`. A missing module, `main` or subcommand exits 2 naming the owning batch and story, so a no-op cannot pass. A test pins the table against the merged CLIs.

Known and planned subcommands, which T9-01 reads from the merged code:

| Module | Subcommands (story) |
| --- | --- |
| terraluna_pipeline.schema | write, check (T0) |
| terraluna_pipeline.export | validate, mock-bundle, mock-bundle-check (T0); cards (T4L-06); stage, bundle-version, assemble (T4L-08); build-real-card (T4R-04) |
| terraluna_pipeline.pilot | fetch, record, check <source> (T1) |
| terraluna_pipeline.polarimetry | record-g1 (T2-06) |
| terraluna_pipeline.terrain | parity writer and check (T3-07; read the merged name) |
| terraluna_pipeline.gates | merge, verify, attest-g0, snapshot (T0B-06) |
| terraluna_pipeline.matching | parity (T4L-03); pairs, record-g2, check-g2 (T4R-03) |
| terraluna_pipeline.uavsar | record <scene_dir>, check (T4R-01) |
| terraluna_pipeline.figures | cards, with `--check` (T4R-02) |
| terraluna_pipeline.search | fetch, targets, compute, tiles, verify (T5) |
| terraluna_pipeline.uncertainty | fetch-clones, run-clones (T6-01); annotate, verify (T6-09) |
| terraluna_pipeline.checks | g4, holuhraun, context, analysis, verify-records (T6) |
| terraluna_pipeline.release | run, validate, publish (T9) |
| terraluna_ml.dataset | plan, parity (T11-06); fetch, build, verify (T11-07, T11-08) |
| terraluna_ml.train | run, sweep (T12A-04, T12A-05) |
| terraluna_ml.baselines | fetch, extract (T12B-04) |
| terraluna_ml.evaluate | run, freeze, read-test-blocks, export-embeddings, verify-g5 (T12B-05a, T12B-05b); g5 (T12B-07) |
| terraluna_ml.export_onnx | export, reference, verify (T13-01, T13-02, T13-08) |
| terraluna_ml.report | build, mock, check (T14-05) |

---

## (g) Integration-branch fixes (the orchestrator does these itself)

1. **Root pyproject.toml, ruff and pytest** (review-ml #1, #15). Add `ml/tests` to `[tool.ruff] src`, `ml/tests/**/*.py` to `include`, and `testpaths = ["pipeline/tests", "ml/tests"]`. The integration worktree currently holds exactly this edit uncommitted (`pyproject.toml` modified, `ml/tests/` untracked). Commit it once ml/tests holds a real file.
2. **Root pyproject.toml, mypy** (review-ml #1, #17). Add `"ml/tests"` to `[tool.mypy] files` and to `mypy_path`, so bare-name helpers such as `helpers_encoder` resolve. Apply this in the merge commit of t11-dataset T11-01, the first ml/tests file, because mypy fails on a missing path. Until then, ML checks name paths explicitly (`uv run mypy ml/src ml/tests`).
3. **Unique test basenames** (review-ml #1, #56). A test file's basename must be unique across pipeline/tests and ml/tests, because the default import mode with no `__init__.py` raises "import file mismatch". Before each merge the orchestrator lists both trees and rejects duplicates. Reserved for pipeline/tests: test_config*, test_geometry*, test_terrain*, test_schema*, test_json_schema, test_export*, test_io*, test_quality*, test_pilot*, test_polarimetry*, test_masks*, test_fingerprints*, test_matching*, test_cards*, test_bundle*, test_figures*, test_uavsar*, test_search*, test_tiles*, test_uncertainty*, test_checks*, test_gates*, test_provenance*, test_release*. `ml/tests/conftest.py` belongs to t11-dataset only; later ML batches put shared fixtures in their own `helpers_<area>.py`.
4. **Generated files.** packages/schema/json/*, packages/schema/src/generated/*, fixtures/mock-bundle/* and outputs/validation.json are never merged by hand. Regenerate them with `make schema`, `make mock-bundle` and `gates merge`.
5. **CI time.** After t12a merges, measure the CI py job with torch installed. If it exceeds 20 minutes, apply the PyTorch CPU index that T12A-01 records in its notes.
6. **Early partial merges.** Merge t1-pilot through T1-02 now, so t0b and t11 can start. Merge t2-polarimetry through T2-06 when it passes; T2-07 waits for ACC-01.

---

## (h) Splitting t4-comparison

| New story | From | Depends on G0 | Note |
| --- | --- | --- | --- |
| t4-logic T4L-01 fingerprints.py | T4-02 | no | |
| T4L-02 qualify_pair with incidence binning in declared units | T4-03 | no | owns the PRD 12 angle-unit end-to-end check |
| T4L-03 compare_windows and `matching parity` | T4-04 | no | writes fixtures/parity/matching_synthetic.json |
| T4L-04 Pareto shortlist, sort rule, trim | T4-05 (part) | no | |
| T4L-05 ties, reference spread, background sample and percentile | T4-05 (part) | no | |
| T4L-06 export_card, record writers, `export cards` | T4-06 (part) | no | |
| T4L-07 labelled mock comparisons, cards, targets, windows and background in the mock bundle | T4-06 AC[8] | no | |
| T4L-08 stage, assemble, per-path schema validation, `make bundle` | T4-06 AC[7], T9-02 | no | needs t0b complete (gates snapshot) |
| t4-real T4R-01 UAVSAR reader and scene record | new | no for code; ACC-01 for the real record | |
| T4R-02 card figures A to C | new | no | |
| T4R-03 G2 record | T4-07 | yes (G0-01, G0-04, ANA-01) | |
| T4R-04 first real card in a real bundle | T4-08 | yes (G0-01, G0-02, G0-04, G0-05; ACC-01 for radar context) | |

Dependencies on each part:

| Batch | Needs |
| --- | --- |
| t5-search | t4-logic through T4L-07 |
| t7b-explorer | t4-logic through T4L-07 (mock records) |
| t15b-api | nothing from t4 (route tests use fixtures); T4L-07 waits for T15B-01 instead |
| t10-core T10-08 | t4-logic through T4L-03 |
| t14-report | t4-logic complete (T14-05 edits export.py after T4L-07 and T4L-08) |
| t9-release | t4-logic complete and t4-real through T4R-02 |
| t6-uncertainty | t4-logic complete and t4-real through T4R-01 (UAVSAR reader for T6-05). T6-09's real run also waits for t4-real complete, so the slice and the final comparisons are never written concurrently |

---

## (i) Gap owners

| Gap | Owner (batch, story) | Contract |
| --- | --- | --- |
| /probe route and result card (PRD 8.6, 8.7 step 7) | t13-probe **T13-07** (web-frontend) | `apps/web/app/probe/*` uses the T13-05 worker and the shell layout, adds the nav link in `site-header.tsx`, shows the line "Live estimate from public elevation data; not a site assessment.", keeps state in the URL, is hidden behind its flag when the worker is not built, and never stores or ranks a result |
| TypeScript terrain distances | t10-core **T10-08** (after T4L-03) | mirrors `matching.terrain_distance` and `background_percentile` with parity on fixtures/parity/matching_synthetic.json at 1e-9 absolute (8.8) |
| TypeScript encoder input channels | t10-core **T10-07** (after T11-06) | mirrors `terraluna_ml.dataset` channels (plane removal over valid pixels, asinh(z/50 m), slope/45°, valid mask, fill 0.0) on fixtures/parity/encoder_channels.json, compared in float64 at 1e-9 absolute |
| Encoder backgrounds and target embeddings | t13-probe **T13-08** (ml-engineer) | `export_onnx reference` embeds each target window and the T5 background sample windows with the exported fp16 model through onnxruntime and writes ModelBackground parts (b). Blocked until G5-06 (final model), t5 complete (sample ids) and t11 T11-07 (real DEM sources) |
| Strict CSP, COOP and COEP | t15b-api **T15B-04** | `next.config` `headers()` (t15 owns next.config): CSP with self, `NEXT_PUBLIC_ASSET_BASE`, the Copernicus bucket and the WorldCover bucket only if G0 shows it CORS-readable; COOP `same-origin` and COEP `require-corp` on /probe and /explore/earth only; tests assert the header values. Asset hosts need CORS and CORP (ACC-05). A third-party basemap needs LIC-02 |
| Remaining API endpoints | t15b-api **T15B-02** (/targets, /targets/{id}), **T15B-03** (/targets/{id}/matches, /windows/{id}), **T15B-05** (/compare, /model) | schemas from @terraluna/schema; `family=model` returns 409 problem+json while G5 is not PASS; /compare returns the precomputed record or computes terrain distances with @terraluna/core from stored fingerprints, labelled "Derived (on request)" |
| Card, report and figure loading for the web | t15b-api **T15B-01** | extends bundle:pull and the loader to copy and zod-validate cards/*.json, model/model_card.json and report/{metrics,curves,examples}.json, and to copy figures/*.svg through the SVG allowlist (fixes the T7B-01 blocker, review-science #50) |
| DEM proxy | t15b-api **T15B-07** | built only if G0-03 chooses proxy; same tile-name regex, Range ≤ 4 MB and caching rules as PRD 8.5 |
| /paper | t7b-explorer **T7B-09** | static page; shows Unavailable until a released `apps/web/public/paper/main.pdf` and its abstract exist (REL-04) |
| Licence and dependency audit | t9-release **T9-09** | `make licenses`, `make audit`; allowlist LIC-04; CI job in T9-07 |
| Angle-unit end-to-end check | t4-logic **T4L-02** | incidence enters a bin only through `polarimetry.angle_to_degrees` with the unit declared in the ProductRecord. An unknown unit gives `incidence_unit_unknown` and null bins give `incidence_bins_unset`. A test shows that the same number declared in radians and in degrees cannot land in the same bin silently |
| Pipeline–API–probe fingerprint end-to-end check | t15b-api **T15B-06** | one parity-fixture window: the Python fingerprint in a test bundle's windows/{body}.json, the `/windows/{id}` response and `@terraluna/core` on the same input agree within 8.8 tolerances |
| PGDA78 clone reader | t6-uncertainty **T6-01** | `uncertainty fetch-clones` (PGDA README file list, no login) and `run-clones` (cache only); clones are read with the T1 io windowed GeoTIFF reader |
| UAVSAR reader | t4-real **T4R-01** | `pipeline/src/terraluna_pipeline/uavsar.py`: .ann keys (grd_mag.set_rows, set_cols, row_mult, col_mult, row_addr, col_addr), HHHH/HVHV/VVVV 4-byte real and HHHV/HHVV/HVVV 8-byte complex, little-endian (PRD 5); synthetic tests; `uavsar record <TERRALUNA_UAVSAR_SCENE>` writes the ProductRecord and scene record after ACC-01 |
| Card figures A to C | t4-real **T4R-02** | `figures.py`; deterministic SVG from records and real DEM caches; Earth figure A uses a GLO-30 hillshade (no third-party basemap) |
| Cards for the 3 to 5 shortlisted windows; uncertainty in comparisons | t6-uncertainty **T6-09** | `uncertainty annotate` writes the final outputs/comparisons, then runs `export cards` and `figures cards` |
| Per-window Mini-RF statistics for Site01 windows | t5-search **T5-03** | radar fingerprints of target and reference windows from the T1-cached mosaic window (T6-06 input) |
| Mini-RF CPR and permanent-shadow tiles | t5-search **T5-06** | CPR overlay with validity mask; the shadow layer only once G0-10 names a product |
| Real DEM sources and the 600,000-window build | t11-dataset **T11-07**, **T11-08** | DemSource adapters over T1 io readers; `dataset build` blocked on G0-02, G0-06, G0-07, ANA-05, PRD-05, PRD-06 |
| 20 real parity windows per body | t5-search **T5-07** (real run) | fixtures/parity/terrain_real_<body>.json (LIC-05) |
| Written report outputs/report.md | t9-release **T9-08** | generated from bundle files, with a source path beside every number |
| Paper results sections | not drafted (t16b-paper-results) | after t6, t12b with G5, and t14 |

Module placement differs from PRD 8.3 in four places, recorded as engineering choices: masks.py instead of quality.py (t1 owns quality.py), uavsar.py instead of io.py (t1 owns io.py), figures.py instead of export.py (disjoint files), and the new gates.py and provenance.py.

---

## (j) WorldCover and CTX masks in one place

- **Module:** `pipeline/src/terraluna_pipeline/masks.py`, owned by **t11-dataset T11-03** (owner data-engineer; moved from T5-01). T5 search (T5-04), the T11 dataset (T11-02, T11-05, T11-08) and any later TypeScript mirror import it. Nothing reimplements it.
- **Code table:** the pinned WorldCover legend (G0-08): 10 tree, 20 shrubland, 30 grassland, 40 cropland, 50 built-up, 60 bare or sparse, 70 snow and ice, 80 water, 90 herbaceous wetland, 95 mangroves, 100 moss and lichen. The table maps them to the config names (`bare_sparse` 60, `snow_ice` 70, `grassland` 30, `shrubland` 20, and so on). The version, year and legend source are module constants, and a test pins them.
- **Sampling:** classes are never bilinearly resampled. Nearest-class lookup is taken at the D29 pixel centres of the 90 m analysis grid (`geometry.window_grid(window_m, analysis_grid_m)` as T3 defines it) through the same source-lookup functions as the DEM. A test shows that no code outside the legend can arise. The alternative, an areal count of native 10 m pixels, is decision ANA-05. The default stands until the human answers.
- **`window_land_cover_status(classes, allowed, min_allowed_fraction, water_rule)`** returns `eligible`, `rejected` or `unknown`, a reason, the allowed fraction and pixel counts by reason. The counts sum to the sample count (tested).
  - `allowed` and `min_allowed_fraction` come from config `earth_search.masks` (PRD 8.13; 0.9) and are never edited (PRD-04 covers the snow_ice, grassland and shrubland question). `snow_ice` pixels are also counted separately.
  - Unknown codes and nodata count as `unknown`, never as allowed. If they exceed 1 − min_allowed_fraction, the window is `unknown` (`land_cover_nodata` or `unknown_class`). Otherwise the allowed fraction is taken over all samples, with the boundary inclusive (exactly 0.9 is eligible).
  - A null `earth_search.masks.water` returns `unknown` with `water_rule_unset` (G0-09).
  - Moon and Mars windows return `not_applicable_body` and are never recorded as fully allowed.
  - Disallowed pixels inside an accepted window are not turned into nodata. The PRD does not state that rule (review-ml #7), so it is dropped.
- **`ctx_quality_status(valid_fraction, qa_metrics, ctx_mola_sd_threshold_m, *, is_target)`:**
  - Target windows need a valid fraction ≥ 0.9 (PRD 5); other windows need ≥ `terrain.min_valid_fraction` (0.8).
  - A missing `qa_metrics.txt` gives `unknown` with `ctx_qa_missing`.
  - A threshold of None gives `unknown` with `ctx_qa_threshold_unset` (G0-06; PRD 11.2 starts at 10 m). Tests pass explicit thresholds.
- **Valid-fraction filter:** the window filter is the 90 m analysis-grid valid fraction from `terraluna_pipeline.terrain` (`below_min_valid_fraction`). The 64 × 64 encoder window's valid fraction is recorded but is not a second filter (review-ml #21). The TypeScript probe (T10-05) uses the same 90 m rule.

---

## (k) Launch DAG

A node is a batch, or a batch through a named story. A node launches only when every predecessor has passed and been merged into ralph/integration. Where a batch pauses for a later node, the Ralph loop marks the next story blocked and stops, and the orchestrator merges the passed prefix.

| Node | Predecessors (passed and merged) | Human decisions it waits on |
| --- | --- | --- |
| integration fix (g1, g4, g6) | — | — |
| t1-pilot, t2-polarimetry, t3-terrain, t7-shell, t15-api, t16-paper | running | t2 T2-07 waits on ACC-01 |
| **t0b-contracts through T0B-04** (records) | t1-pilot through T1-02 | — |
| **t0b-contracts T0B-05, T0B-06** | t0b through T0B-04; t1-pilot complete (T1-09 G0 block); t2-polarimetry through T2-06 | PRD-01 |
| **t10-core through T10-06** | t3-terrain | — |
| **t11-dataset through T11-06** | t3-terrain; t1-pilot through T1-02; integration fix g2 in the T11-01 merge | — |
| **t4-logic through T4L-06** | t0b through T0B-04; t1-pilot through T1-02; t2 through T2-06; t3-terrain | — |
| **t4-logic T4L-07** (mock records in the mock bundle) | t4-logic through T4L-06; t15b-api through T15B-01, so `make web-build` keeps passing once the mock bundle lists cards | — |
| **t4-logic T4L-08** | t4-logic through T4L-07; t0b complete | — |
| **t10-core T10-07** | t10 through T10-06; t11 through T11-06 | — |
| **t10-core T10-08** | t10 T10-07; t4-logic through T4L-03 | — |
| **t15b-api T15B-01** | t15-api; t7-shell; t0b through T0B-04 | — |
| **t15b-api T15B-02 to T15B-04** | t15b through T15B-01 (route tests use test-local fixtures, not the mock bundle) | — |
| **t15b-api T15B-05, T15B-06** | t15b through T15B-04; t10-core complete | — |
| **t15b-api T15B-07** | t15b through T15B-06 | G0-03 = proxy (otherwise the story is closed as not needed) |
| **t7b-explorer** | t7-shell; t15-api; t15b through T15B-01; t4-logic through T4L-07; t0b through T0B-04 | T7B-05 basemap: LIC-02 |
| **t5-search through T5-06** | t4-logic through T4L-07; t11 through T11-03; t1-pilot complete; t3-terrain | G0-10 for the shadow layer only (an Unavailable reason otherwise) |
| **t5-search T5-07** | t5 through T5-06 | G0-01, G0-02, G0-04, G0-06, G0-08, G0-09, ANA-02, ANA-05 |
| **t4-real through T4R-02** | t4-logic complete; t0b complete; t1-pilot complete | — |
| **t4-real T4R-03** | t4-real through T4R-02 | G0-01, G0-04, ANA-01 |
| **t4-real T4R-04** | t4-real T4R-03 | G0-01, G0-02, G0-05 (and ACC-01 for radar context) |
| **t6-uncertainty through T6-02** | t5 through T5-06; t4-logic complete; t0b complete; t4-real through T4R-01 | — |
| **t6-uncertainty T6-09, T6-04, T6-06, T6-07, T6-08** | t6 through T6-02; t5-search complete; t4-real complete | G0-10 (T6-06 shadow part) |
| **t6-uncertainty T6-05** | t6 through T6-02; t4-real through T4R-01 | ACC-01, ANA-04 |
| **t12a-encoder** | t11 through T11-06; integration fix g2 | G5-05 before real round 1 only |
| **t13-probe through T13-02** | t12a; t11 through T11-06 | — |
| **t13-probe T13-03 to T13-07** | t13 through T13-02; t10-core complete; t7-shell; t15-api | G0-03 before the real browser run (the code takes the mode as a parameter) |
| **t12b-evaluate through T12B-05b** | t12a; t11 through T11-06; t0b through T0B-04 | real runs: ACC-03, LIC-01, G5-02, G5-03 |
| **t12b-evaluate T12B-06, T12B-07** | t12b through T12B-05b; t0b complete; t13 through T13-02 | — (G5-01 is the later human run, not a story) |
| **t11-dataset T11-07, T11-08** | t11 through T11-06; t1-pilot complete | G0-02, G0-06, G0-07, ANA-05, PRD-04, PRD-05, PRD-06, PRD-08 |
| **t14-report** | t12b complete; t7-shell; t0b complete; t4-logic complete; t13 through T13-02 | PRD-07 (T14-04 hillshades) |
| **t13-probe T13-08** | t13 through T13-07; t5-search complete; t11 complete | G5-01, G5-06 |
| **t9-release** | t4-logic complete; t4-real through T4R-02; t5 through T5-06; t6 through T6-02; t0b; t10; t12a; t12b; t13 through T13-07; t14; t15b through T15B-06; t7b; t16-paper | ACC-05, ACC-06, ACC-07, LIC-03, LIC-04 for their stories; REL-* are human runs |
| t16b-paper-results (not drafted) | t6 complete; t12b with G5 recorded; t14 | REL-04 |

The graph is acyclic. It flows from t1, t2 and t3 to t0b, from t0b to t4-logic and t11, and on to t5, t10, t15b, t7b, t12a, t13, t12b, t14, t4-real and t6, ending at t9. The only cross-links are the merge points named above: t10 T10-08 on T4L-03, T4L-07 on T15B-01, t12b T12B-06 on T13-02, t15b T15B-05 on t10, and t7b on T15B-01. None of them points back up the chain.
