// Facts about Terraluna's two models for `/` and `/model`, read on the server at build time.
// Every value comes from a shipped artifact: the bundle (manifest.model, model/model_card.json,
// report/metrics.json, validation.json, targets.json, windows/) and the tracked probe records
// (public/probe-model/export_record.json, outputs/probe/wasm-parity.json). A missing artifact
// gives a null with a reason; nothing is typed in.
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import type { PulledBundle } from '../../lib/bundle.ts';

export type Fact<T> = { readonly ok: true; readonly value: T; readonly source: string } | { readonly ok: false; readonly reason: string };

export interface G5Condition {
  readonly condition: number;
  readonly testId: string;
  readonly statistic: string;
  readonly threshold: number | null;
  readonly observed: number | null;
  readonly passed: boolean | null;
  readonly reason: string | null;
}

export interface ModelFacts {
  readonly modelId: string | null;
  readonly modelStatus: string | null;
  readonly parameters: Fact<number>;
  readonly trainingWindows: Fact<{ readonly total: number; readonly byBody: readonly (readonly [string, number])[] }>;
  readonly embeddingDim: Fact<number>;
  readonly inputShape: Fact<string>;
  readonly wasmParity: Fact<{ readonly minCosine: number; readonly threshold: number; readonly windows: number; readonly runtime: string; readonly ms: number | null }>;
  readonly pythonParity: Fact<{ readonly minCosine: number; readonly threshold: number }>;
  readonly g5: { readonly status: string; readonly result: string | null; readonly consequence: string | null } | null;
  readonly conditions: readonly G5Condition[];
  readonly stageWidths: readonly number[] | null;
  readonly blocksPerStage: readonly number[] | null;
  readonly architecture: string | null;
  readonly preprocessing: string | null;
  readonly lambdaMax: number | null;
  readonly bodies: readonly string[];
  readonly lagsM: readonly number[] | null;
  readonly windowKm: number | null;
  readonly targets: readonly { readonly name: string; readonly body: string }[];
  readonly firstProbeBudgetS: number | null;
}

function readJson(path: string): unknown {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as unknown;
  } catch {
    return null;
  }
}

const isRec = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

const EXPORT_RECORD = 'public/probe-model/export_record.json';
const WASM_PARITY = 'outputs/probe/wasm-parity.json';
const BENCH = 'outputs/probe/bench-browser.json';

export function readModelFacts(bundle: PulledBundle, cwd: string = process.cwd()): ModelFacts {
  const { modelCard, metrics, targets, windows } = bundle.records;
  const repo = join(cwd, '..', '..');

  const parameters: Fact<number> =
    modelCard === null
      ? { ok: false, reason: 'model/model_card.json is not in this bundle' }
      : { ok: true, value: modelCard.parameter_count, source: 'model/model_card.json' };

  let trainingWindows: ModelFacts['trainingWindows'] = { ok: false, reason: 'model/model_card.json is not in this bundle' };
  if (modelCard !== null) {
    const byBody: [string, number][] = [];
    for (const [body, splits] of Object.entries(modelCard.training_data.window_counts)) {
      const n = (splits as Record<string, number | null>).train;
      if (typeof n === 'number') byBody.push([body, n]);
    }
    trainingWindows =
      byBody.length === 0
        ? { ok: false, reason: 'the model card counts no training windows' }
        : { ok: true, value: { total: byBody.reduce((s, [, n]) => s + n, 0), byBody }, source: 'model/model_card.json (train split)' };
  }

  const exportRecord = readJson(join(cwd, EXPORT_RECORD));
  let embeddingDim: Fact<number> = { ok: false, reason: `${EXPORT_RECORD} is not present` };
  let inputShape: Fact<string> = { ok: false, reason: `${EXPORT_RECORD} is not present` };
  let pythonParity: ModelFacts['pythonParity'] = { ok: false, reason: `${EXPORT_RECORD} is not present` };
  if (isRec(exportRecord)) {
    const out = exportRecord.output_shape;
    const dim = Array.isArray(out) ? num(out[out.length - 1]) : null;
    embeddingDim = dim === null ? { ok: false, reason: 'the export record has no output shape' } : { ok: true, value: dim, source: EXPORT_RECORD };
    const inp = exportRecord.input_shape;
    inputShape = Array.isArray(inp)
      ? { ok: true, value: inp.map((d) => String(d)).join(' × '), source: EXPORT_RECORD }
      : { ok: false, reason: 'the export record has no input shape' };
    const pp = exportRecord.python_parity;
    if (isRec(pp) && num(pp.fp16_min_cosine) !== null && num(pp.fp16_threshold) !== null) {
      pythonParity = { ok: true, value: { minCosine: num(pp.fp16_min_cosine) ?? 0, threshold: num(pp.fp16_threshold) ?? 0 }, source: EXPORT_RECORD };
    }
  }

  const wasm = readJson(join(repo, WASM_PARITY));
  let wasmParity: ModelFacts['wasmParity'] = { ok: false, reason: `${WASM_PARITY} is not present in this build` };
  if (isRec(wasm)) {
    const min = num(wasm.min_cosine_vs_python_fp32);
    const thr = num(wasm.threshold);
    const sameModel = modelCard?.onnx_sha256 == null || wasm.onnx_sha256 === modelCard.onnx_sha256;
    if (!sameModel) wasmParity = { ok: false, reason: 'the WASM parity record is for a different ONNX export than the bundle model' };
    else if (min !== null && thr !== null)
      wasmParity = {
        ok: true,
        value: {
          minCosine: min,
          threshold: thr,
          windows: num(wasm.windows) ?? 0,
          runtime: `onnxruntime-web ${String(wasm.onnxruntime_web)}, ${String(wasm.runtime)}`,
          ms: num(wasm.per_window_ms),
        },
        source: WASM_PARITY,
      };
  }

  const gate = bundle.validation.find((g) => g.gate_id === 'G5') ?? null;
  const conditions: G5Condition[] =
    metrics === null
      ? []
      : metrics.g5.conditions.map((c) => ({
          condition: c.condition,
          testId: c.test_id,
          statistic: c.statistic,
          threshold: c.threshold,
          observed: c.observed,
          passed: c.passed,
          reason: c.reason ?? null,
        }));

  // Stage widths as the model card's architecture field states them, e.g. "(32, 64, 128, 256)".
  let stageWidths: number[] | null = null;
  if (modelCard !== null) {
    const m = /width \(([\d,\s]+)\)/.exec(modelCard.architecture);
    if (m?.[1] !== undefined) stageWidths = m[1].split(',').map((s) => Number(s.trim())).filter((n) => Number.isFinite(n));
  }

  let blocksPerStage: number[] | null = null;
  if (modelCard !== null) {
    const m = /\[([\d,\s]+)\]/.exec(modelCard.architecture);
    if (m?.[1] !== undefined) blocksPerStage = m[1].split(',').map((s) => Number(s.trim())).filter((n) => Number.isFinite(n));
  }
  const preprocessing = isRec(exportRecord) && typeof exportRecord.preprocessing === 'string' ? exportRecord.preprocessing : null;
  const lambdaMax = modelCard === null ? null : num((modelCard.objectives_and_weights as Record<string, unknown>).lambda_max);

  // The lags every target window supports (relief), read from the target window records.
  let lagsM: number[] | null = null;
  const primary = new Set((targets ?? []).map((t) => t.primary_window_id));
  for (const list of windows.values()) {
    for (const w of list) {
      const t = w.terrain as unknown;
      if (!isRec(t) || !primary.has(String(t.window_id))) continue;
      const relief = isRec(t.terrain) && isRec(t.terrain.relief) ? t.terrain.relief : null;
      const lags = relief !== null && Array.isArray(relief.supported_lags_m) ? relief.supported_lags_m.filter((x): x is number => typeof x === 'number') : null;
      if (lags === null) continue;
      lagsM = lagsM === null ? lags : lagsM.filter((l) => lags.includes(l));
    }
  }

  const windowM = targets?.[0]?.window_grid.window_m ?? null;
  const bench = readJson(join(repo, BENCH));
  const budgetMs = isRec(bench) && isRec(bench.prd_budgets_ms) ? num(bench.prd_budgets_ms.first_probe) : null;

  return {
    modelId: bundle.manifest.model?.id ?? modelCard?.model_id ?? null,
    modelStatus: bundle.manifest.model?.status ?? null,
    parameters,
    trainingWindows,
    embeddingDim,
    inputShape,
    wasmParity,
    pythonParity,
    g5: gate === null ? null : { status: gate.status, result: gate.result ?? null, consequence: gate.consequence ?? null },
    conditions,
    stageWidths,
    blocksPerStage,
    architecture: modelCard?.architecture ?? null,
    preprocessing,
    lambdaMax,
    bodies: modelCard?.training_data.bodies ?? [],
    lagsM,
    windowKm: windowM === null ? null : windowM / 1000,
    targets: (targets ?? []).map((t) => ({ name: t.name, body: t.body })),
    firstProbeBudgetS: budgetMs === null ? null : budgetMs / 1000,
  };
}

/** Thousands separators for a count, e.g. 2795040 -> "2,795,040". */
export const grouped = (n: number): string => n.toLocaleString('en-US');

/** Cosine shown to the digits that matter: floor to 6 decimals so the display never rounds up. */
export const cosine = (v: number): string => (Math.floor(v * 1e6) / 1e6).toFixed(6);
