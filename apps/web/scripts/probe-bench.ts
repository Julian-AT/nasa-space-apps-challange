// pnpm --filter @terraluna/web probe:bench (T13-06): runs the live-probe pipeline in Node against
// the real Copernicus GLO-30 bucket (direct mode: Node applies no CORS) at each --point, twice
// (first and repeat), and writes the stage timings and DEM bytes to --out. With --windows it
// also compares the probe's nu values with the pipeline's FingerprintRecord of the named window,
// and with --target it compares terrain distances with the pipeline's comparison.
//
// These are Node timings on this machine, not a mid-range laptop on 50 Mbit/s in a browser; the
// record says so. Run by hand, not part of make check.
//
//   node scripts/probe-bench.ts --out ../../outputs/probe/bench-node.json \
//     --point 64.88510551859456,-16.721614960145644,earth-utm27n-e280-n2880 \
//     --windows <earth windows.json> --target <moon windows.json>:moon-sps-x2-y2
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { cpus, platform, release, totalmem } from 'node:os';
import { dirname } from 'node:path';
import { parseArgs } from 'node:util';

import { PRD_DEM_BUDGET_BYTES } from '../lib/probe/reader.ts';
import { runProbe } from '../lib/probe/pipeline.ts';
import type { ProbeTargetFingerprint } from '../lib/probe/result.ts';

interface Entry {
  window: { window_id: string };
  terrain: { terrain: { relief: { supported_lags_m: number[]; nu_m: Record<string, number | null> }; texture: { supported_lags_m: number[]; nu_m: Record<string, number | null> } | null } | null } | null;
}

function entries(path: string): Map<string, Entry> {
  const list = JSON.parse(readFileSync(path, 'utf8')) as Entry[];
  return new Map(list.map((e) => [e.window.window_id, e]));
}

function maxRelDiff(a: Record<string, number | null>, b: Record<string, number | null>): number | null {
  let max = 0;
  for (const [lag, va] of Object.entries(a)) {
    const vb = Object.entries(b).find(([k]) => Number(k) === Number(lag))?.[1] ?? null;
    if (va === null || vb === null) return null;
    max = Math.max(max, Math.abs(va - vb) / Math.abs(vb));
  }
  return max;
}

const { values } = parseArgs({
  options: {
    out: { type: 'string' },
    point: { type: 'string', multiple: true },
    windows: { type: 'string' },
    target: { type: 'string' },
  },
});
if (values.out === undefined || values.point === undefined) {
  console.error('probe:bench needs --out <file> and at least one --point lat,lon[,window_id]');
  process.exit(2);
}

let target: ProbeTargetFingerprint | null = null;
if (values.target !== undefined) {
  const [path, id] = values.target.split(':');
  const fp = path && id ? entries(path).get(id)?.terrain?.terrain : undefined;
  if (!fp || !id) throw new Error(`--target ${values.target}: no terrain fingerprint for that window`);
  target = { target_id: id, window_id: id, body: 'moon', isotropic_rule: 'direction_mean', relief: fp.relief, texture: fp.texture };
}
const pipelineWindows = values.windows === undefined ? null : entries(values.windows);

const points = [];
for (const spec of values.point) {
  const [lat, lon, windowId] = spec.split(',');
  const runs = [];
  for (const attempt of ['first', 'repeat'] as const) {
    const result = await runProbe(
      { id: runs.length + 1, latDeg: Number(lat), lonDeg: Number(lon) },
      { mode: 'direct', targets: target ? { fingerprints: [target], reason: null } : { fingerprints: [], reason: 'no --target' }, bundleVersion: null },
    );
    if (result.kind === 'failure') {
      runs.push({ attempt, kind: result.kind, reason: result.reason, detail: result.detail });
      continue;
    }
    runs.push({ attempt, kind: result.kind, timings: result.timings, dem: result.dem, ...(result.kind === 'result' ? { relief_nu_m: result.terrain.relief.nu_m, texture_nu_m: result.terrain.texture?.nu_m ?? null, distances: result.targets } : {}) });
  }
  const first = runs[0];
  const pipeline = windowId && pipelineWindows ? pipelineWindows.get(windowId)?.terrain?.terrain : undefined;
  const comparison =
    pipeline && first && 'relief_nu_m' in first
      ? {
          window_id: windowId,
          relief_max_rel_diff: maxRelDiff(first.relief_nu_m, pipeline.relief.nu_m),
          texture_max_rel_diff: first.texture_nu_m && pipeline.texture ? maxRelDiff(first.texture_nu_m, pipeline.texture.nu_m) : null,
        }
      : null;
  points.push({ lat_deg: Number(lat), lon_deg: Number(lon), window_id: windowId ?? null, runs, pipeline_comparison: comparison });
}

const record = {
  record: 'probe_bench_node',
  statement:
    'Node timings of the live-probe pipeline (lib/probe/pipeline.ts) on this machine against the public Copernicus bucket in direct mode. Not a browser, not the proxy, and not a mid-range laptop on 50 Mbit/s; the PRD 8.7 browser budgets (first probe <= 6 s, repeat <= 1.5 s) are not assessed by this record.',
  generated_at: new Date().toISOString(),
  machine: { platform: platform(), release: release(), cpu: cpus()[0]?.model ?? null, cpus: cpus().length, memory_gb: Math.round(totalmem() / 2 ** 30), node: process.version },
  prd_dem_budget_bytes: PRD_DEM_BUDGET_BYTES,
  encoder: 'not run (no exported model yet; encoder distances are Unavailable, encoder_not_evaluated)',
  points,
};
mkdirSync(dirname(values.out), { recursive: true });
writeFileSync(values.out, `${JSON.stringify(record, null, 2)}\n`);
console.log(`probe:bench: wrote ${values.out}`);
