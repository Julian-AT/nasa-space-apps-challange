'use client';

// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The /find result card in ShadowAstra's site-report style, fed only by the live probe's
// ProbeResult (lib/probe/result.ts). Every number carries its unit and evidence label; an
// Unavailable value shows its reason and is never drawn as zero. Families stay separate and the
// targets are listed in bundle order, never ranked. The encoder row is Predicted and unranked.
import { ArrowRight, FileText, Ruler, ShieldAlert, Waves } from 'lucide-react';
import Link from 'next/link';
import type { ReactNode } from 'react';

import { ProbeCurves } from '../../app/probe/probe-curves.tsx';
import type { DistanceValue, ProbeResult, ProbeSuccess, TargetDistances } from '../../lib/probe/result.ts';
import { Chip, type ChipTone, Meter, Ring, WorldDot } from './kit.tsx';

export interface FindTarget {
  target_id: string;
  name: string;
  body: string;
}

export interface CardLink {
  card_id: string;
  target_id: string;
  earth_name: string;
}

const fmt = (v: number, digits = 3): string => (Math.abs(v) >= 1000 || (Math.abs(v) < 0.001 && v !== 0) ? v.toExponential(digits) : v.toFixed(digits));
const ms = (v: number): string => `${String(Math.round(v))} ms`;

function labelTone(label: string): ChipTone {
  if (label.startsWith('Predicted')) return 'predicted';
  if (label.startsWith('Derived')) return 'derived';
  return 'unavailable';
}

const UNIT_TEXT: Record<string, string> = { 'RMS of log nu': 'RMS of log ν' };

function DistanceBlock({ title, icon, d, note }: Readonly<{ title: string; icon: ReactNode; d: DistanceValue; note?: ReactNode }>) {
  const unit = UNIT_TEXT[d.unit] ?? d.unit;
  return (
    <div className="min-w-0 rounded-2xl border border-line bg-ink/60 p-4" data-family={title}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-[13px] text-platinum">
          {icon} {title}
        </span>
        <Chip tone={labelTone(d.label)}>{d.label}</Chip>
      </div>
      {d.status === 'available' ? (
        <p className="mt-3 flex flex-wrap items-baseline gap-x-2">
          <span className="font-mono text-[30px] leading-none font-light text-platinum tabular-nums" data-distance={d.value}>
            {fmt(d.value)}
          </span>
          <span className="text-[12.5px] text-chrome">{unit}</span>
        </p>
      ) : (
        <p className="mt-3 text-[13px] text-chrome">
          <span className="font-medium text-platinum">Unavailable</span>: {d.reason}
        </p>
      )}
      {d.shared_lags_m.length > 0 ? <p className="mt-2 font-mono text-[10.5px] break-words text-dim">lags {d.shared_lags_m.join(', ')} m</p> : null}
      <div className="mt-3">
        {d.background_percentile === undefined ? (
          <p className="text-[11.5px] text-dim">Background percentile: Unavailable: the live probe carries no background histogram for this family.</p>
        ) : d.background_percentile === null ? (
          <p className="text-[11.5px] text-dim">Background percentile: Unavailable: {d.background_percentile_reason ?? 'no reason recorded'}.</p>
        ) : (
          <Meter
            value={d.background_percentile / 100}
            label="Background percentile"
            right={`${fmt(d.background_percentile, 1)}`}
            tone="var(--color-evidence-predicted)"
            hint={`${fmt(d.background_percentile, 1)}% of this target's background windows are closer (smaller is closer).`}
          />
        )}
      </div>
      {note === undefined ? null : <div className="mt-2 text-[11.5px] text-dim">{note}</div>}
    </div>
  );
}

function TargetBlock({ r, t, target }: Readonly<{ r: ProbeSuccess; t: TargetDistances; target: FindTarget }>) {
  return (
    <section aria-label={`Distances to ${target.name}`} className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <DistanceBlock title="Terrain relief" icon={<Ruler size={14} className="text-flare" aria-hidden="true" />} d={t.terrain_relief} />
        <DistanceBlock title="Terrain texture" icon={<Waves size={14} className="text-flare" aria-hidden="true" />} d={t.terrain_texture} />
      </div>
      <DistanceBlock
        title="Encoder (EXPERIMENTAL)"
        icon={<ShieldAlert size={14} className="text-evidence-predicted" aria-hidden="true" />}
        d={t.encoder}
        note={
          <span className="flex flex-wrap items-center gap-1.5">
            <Chip tone="fail">G5 {r.g5_status ?? 'not recorded'}</Chip>
            <Chip>unranked</Chip>
            Predicted values never rank or order anything.
          </span>
        }
      />
      <p className="text-[12px] text-chrome">
        Radar: <span className="text-platinum">Unavailable</span>: {t.radar.reason}.
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        <ProbeCurves title={`Relief nu(L): this window over ${target.name}`} window={r.terrain.relief} target={{ nu_m: t.target_relief_nu_m }} targetName={target.name} />
        {r.terrain.texture === null || t.target_texture_nu_m === null ? null : (
          <ProbeCurves title={`Texture nu(L): this window over ${target.name}`} window={r.terrain.texture} target={{ nu_m: t.target_texture_nu_m }} targetName={target.name} />
        )}
      </div>
    </section>
  );
}

function OtherTargets({ r, targets, selected }: Readonly<{ r: ProbeSuccess; targets: readonly FindTarget[]; selected: string }>) {
  const rest = r.targets.filter((t) => t.target_id !== selected);
  if (rest.length === 0) return null;
  const name = (id: string): FindTarget | undefined => targets.find((x) => x.target_id === id);
  const show = (d: DistanceValue): string => (d.status === 'available' ? `${fmt(d.value)} ${UNIT_TEXT[d.unit] ?? d.unit}` : `Unavailable: ${d.reason}`);
  return (
    <div className="rounded-2xl border border-line p-4">
      <h4 className="font-mono text-[10px] tracking-[0.22em] text-dim uppercase">Same window, other targets (listed, not ranked)</h4>
      <ul className="mt-2 flex flex-col gap-2">
        {rest.map((t) => (
          <li key={t.target_id} className="min-w-0 text-[12.5px] text-chrome">
            <span className="flex items-center gap-2 text-platinum">
              <WorldDot world={name(t.target_id)?.body ?? ''} /> {name(t.target_id)?.name ?? t.target_id}
            </span>
            <span className="block font-mono text-[11.5px] break-words">
              relief {show(t.terrain_relief)} · texture {show(t.terrain_texture)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ResultCard({
  result,
  target,
  targets,
  cards,
}: Readonly<{ result: ProbeResult; target: FindTarget; targets: readonly FindTarget[]; cards: readonly CardLink[] }>) {
  const cardLinks = cards.filter((c) => c.target_id === target.target_id);
  if (result.kind === 'failure') {
    const coverage = result.reason === 'tile_missing';
    return (
      <div role="status" className="rounded-2xl border border-status-fail/40 bg-status-fail/[0.06] p-4" data-probe-outcome="failure">
        <Chip tone="fail">{coverage ? 'Out of coverage' : 'Probe failed'}</Chip>
        <p className="mt-3 text-[13.5px] text-platinum">
          {coverage
            ? 'No Copernicus GLO-30 elevation tile covers this window (open ocean or outside the DEM coverage), so nothing was measured.'
            : `The probe stopped with reason ${result.reason}.`}
        </p>
        <p className="mt-1 font-mono text-[11.5px] break-words text-dim">{result.detail}</p>
      </div>
    );
  }
  if (result.kind === 'stop') {
    return (
      <div role="status" className="flex flex-col gap-4 rounded-2xl border border-status-partial/40 bg-status-partial/[0.06] p-4 sm:flex-row sm:items-center" data-probe-outcome="stop">
        <Ring
          value={result.valid_fraction}
          size={104}
          label="valid"
          display={`${fmt(result.valid_fraction * 100, 0)}%`}
          sub={`need ${fmt(result.min_valid_fraction * 100, 0)}%`}
          ariaLabel={`Valid fraction ${fmt(result.valid_fraction * 100, 1)} percent, at least ${fmt(result.min_valid_fraction * 100, 0)} percent required`}
          color="var(--color-status-partial)"
        />
        <div className="min-w-0">
          <Chip tone="unavailable">Probe stopped · {result.reason}</Chip>
          <p className="mt-2 text-[13.5px] text-platinum">
            Only {fmt(result.valid_fraction * 100, 1)}% of the 5 km window has elevation data; at least {fmt(result.min_valid_fraction * 100, 0)}% is required, so no distance is reported.
          </p>
          <p className="mt-1 text-[11.5px] break-words text-dim">Missing tiles: {result.dem.tiles_missing.join(', ') || 'none'}.</p>
        </div>
      </div>
    );
  }
  const t = result.targets.find((x) => x.target_id === target.target_id);
  return (
    <div className="flex flex-col gap-4" data-probe-outcome="result">
      <div className="surface flex flex-col gap-4 rounded-2xl p-4 sm:flex-row sm:items-center">
        <Ring
          value={result.terrain.valid_fraction}
          size={104}
          label="valid"
          display={`${fmt(result.terrain.valid_fraction * 100, 0)}%`}
          sub="of the window"
          ariaLabel={`Valid fraction ${fmt(result.terrain.valid_fraction * 100, 1)} percent of the 5 km window`}
          color="var(--color-evidence-derived)"
        />
        <div className="min-w-0">
          <div className="flex flex-wrap gap-1.5">
            <Chip tone="derived">{result.terrain.label}</Chip>
            <Chip>{result.source_product}</Chip>
          </div>
          <p className="mt-2 text-[13.5px] font-medium text-platinum">{result.sentence}</p>
          <p className="mt-1 text-[12px] text-chrome">{result.surface_notice}</p>
          <p className="mt-1 font-mono text-[11px] break-words text-dim">
            {fmt(result.centre.lat_deg, 5)}°, {fmt(result.centre.lon_deg, 5)}° · {result.terrain.analysis_grid_m} m grid · {result.terrain.isotropic_rule} · tiles {result.dem.tiles_read.join(', ')}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2 text-[13px] text-platinum">
        <WorldDot world={target.body} /> Distances to {target.name}
        <span className="text-dim">· smaller is closer within one family</span>
      </div>
      {t === undefined ? (
        <p className="text-[13px] text-chrome">Unavailable: {result.targets_reason ?? 'this target has no fingerprint in the bundle'}.</p>
      ) : (
        <TargetBlock r={result} t={t} target={target} />
      )}
      <OtherTargets r={result} targets={targets} selected={target.target_id} />
      <p className="text-[11.5px] text-dim">
        Families are never added together and targets are never ranked. Land cover: Unavailable: {result.worldcover.reason}.
      </p>
      <div className="rounded-2xl border border-line p-4">
        <h4 className="flex items-center gap-2 text-[13px] text-platinum">
          <FileText size={14} className="text-flare" aria-hidden="true" /> Evidence cards for {target.name}
        </h4>
        <ul className="mt-2 flex flex-col gap-1.5">
          {cardLinks.map((c) => (
            <li key={c.card_id}>
              <Link href={`/site/${c.card_id}`} className="inline-flex max-w-full items-center gap-1.5 text-[12.5px] break-all text-flare hover:underline">
                {target.name} ↔ {c.earth_name} <ArrowRight size={12} aria-hidden="true" />
              </Link>
            </li>
          ))}
          <li>
            <Link href="/site" className="text-[12.5px] text-chrome hover:text-platinum hover:underline">
              All evidence cards
            </Link>
          </li>
        </ul>
        <p className="mt-2 text-[11px] text-dim">Cards are pipeline results from the release bundle; this probe is a live estimate and is not one of them.</p>
      </div>
      <p className="font-mono text-[10.5px] break-words text-dim tabular-nums">
        Measured in this browser: DEM read {ms(result.timings.dem_read_ms)} ({fmt(result.dem.bytes / 1e6, 2)} MB, {result.dem.requests} requests), resample{' '}
        {ms(result.timings.resample_ms)}, fingerprint {ms(result.timings.fingerprint_ms)}
        {result.timings.inference_ms === null ? '' : `, encoder ${ms(result.timings.inference_ms)}`}, total {ms(result.timings.total_ms)} · bundle {result.bundle_version ?? 'not recorded'}
      </p>
    </div>
  );
}
