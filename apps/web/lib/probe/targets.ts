// Target terrain fingerprints for the live probe, read from the release bundle (server side).
// targets.json names each target's primary window; windows/<body>.json carries that window's
// FingerprintRecord (CONTRACTS WindowEntry). Only the fields the terrain distance reads are
// parsed, and a missing file or window becomes a stated reason, never an empty default.

import { z } from 'zod';

import { type PulledBundle, targetsFile, windowsTopNFile } from '../bundle.ts';
import { decodeJson } from '../json.ts';
import type { ProbeTargetFingerprint } from './result.ts';

const Variant = z.object({
  supported_lags_m: z.array(z.number().positive()),
  nu_m: z.record(z.string(), z.number().nonnegative().nullable()),
});

const WindowEntry = z.object({
  window: z.object({ window_id: z.string() }),
  terrain: z
    .object({
      terrain: z.object({ relief: Variant, texture: Variant.nullable() }).nullable(),
    })
    .nullable(),
});

const Target = z.object({ target_id: z.string(), name: z.string(), body: z.string(), primary_window_id: z.string() });

export interface ProbeTargets {
  fingerprints: (ProbeTargetFingerprint & { name: string })[];
  reason: string | null;
}

/**
 * The pipeline's isotropic summary for real windows (realcard.ISOTROPIC_RULE = direction_mean,
 * stated in each comparison's claim limits); the FingerprintRecord does not carry it.
 */
const BUNDLE_ISOTROPIC_RULE = 'direction_mean' as const;

export function probeTargets(bundle: PulledBundle): ProbeTargets {
  const targets = targetsFile(bundle);
  if (targets.status === 'not_present') return { fingerprints: [], reason: 'the bundle lists no targets.json' };
  const parsed = z.array(Target).safeParse(decodeJson(targets.bytes, targets.path));
  if (!parsed.success) return { fingerprints: [], reason: 'targets.json does not hold target records the probe can read' };
  const windowsByBody = new Map<string, Map<string, z.infer<typeof WindowEntry>>>();
  const fingerprints: ProbeTargets['fingerprints'] = [];
  const missing: string[] = [];
  for (const target of parsed.data) {
    let windows = windowsByBody.get(target.body);
    if (windows === undefined) {
      windows = new Map();
      const file = windowsTopNFile(bundle, target.body);
      if (file.status === 'present') {
        const entries = z.array(WindowEntry).safeParse(decodeJson(file.bytes, file.path));
        if (entries.success) for (const e of entries.data) windows.set(e.window.window_id, e);
      }
      windowsByBody.set(target.body, windows);
    }
    const fp = windows.get(target.primary_window_id)?.terrain?.terrain;
    if (fp === undefined || fp === null) {
      missing.push(target.target_id);
      continue;
    }
    fingerprints.push({
      target_id: target.target_id,
      name: target.name,
      window_id: target.primary_window_id,
      body: target.body,
      isotropic_rule: BUNDLE_ISOTROPIC_RULE,
      relief: fp.relief,
      texture: fp.texture,
    });
  }
  if (fingerprints.length === 0) {
    return { fingerprints, reason: `no target window fingerprint in the bundle (targets: ${missing.join(', ') || 'none'})` };
  }
  return { fingerprints, reason: null };
}
