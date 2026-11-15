// The live-probe result card (PRD 8.7 step 7). Pure presentation of a ProbeResult: every number
// carries its label, Unavailable values show their reason and never 0, and each distance family
// has its own row. Nothing here combines families or ranks targets.
import { ProbeCurves } from './probe-curves.tsx';
import type { DistanceValue, ProbeResult, ProbeSuccess, ProbeVariant } from '../../lib/probe/result.ts';

const ms = (v: number): string => `${String(Math.round(v))} ms`;

const fmt = (v: number, digits = 3): string => (Math.abs(v) >= 1000 || (Math.abs(v) < 0.001 && v !== 0) ? v.toExponential(digits) : v.toFixed(digits));

function Distance({ d, label }: Readonly<{ d: DistanceValue; label?: string }>) {
  if (d.status === 'unavailable') {
    return (
      <span>
        <span className="font-medium">Unavailable</span>
        <span className="text-muted-foreground">: {d.reason.replaceAll('_', ' ')}</span>
      </span>
    );
  }
  return (
    <span>
      <span className="font-mono font-medium tabular-nums">{fmt(d.value)}</span> <span className="text-muted-foreground">{d.unit}</span>
      <span className="text-muted-foreground"> · {label ?? d.label}</span>
      {d.shared_lags_m.length > 0 ? <span className="text-muted-foreground"> · lags {d.shared_lags_m.join(', ')} m</span> : null}
      {d.background_percentile === undefined ? null : (
        <span className="text-muted-foreground">
          {' '}
          · background percentile{' '}
          {d.background_percentile === null ? `Unavailable: ${d.background_percentile_reason ?? ''}` : fmt(d.background_percentile, 1)}
        </span>
      )}
    </span>
  );
}

function Curve({ variant, title }: Readonly<{ variant: ProbeVariant; title: string }>) {
  return (
    <tr>
      <th scope="row" className="pr-4 text-left font-medium">
        {title}
      </th>
      {Object.entries(variant.nu_m).map(([lag, nu]) => (
        <td key={lag} className="pr-4 tabular-nums">
          {nu === null ? 'Unavailable' : fmt(nu)}
        </td>
      ))}
      <td className="tabular-nums">{variant.hurst === null ? `Unavailable: ${variant.hurst_reason ?? ''}` : fmt(variant.hurst)}</td>
    </tr>
  );
}

function Success({ r, names }: Readonly<{ r: ProbeSuccess; names: Readonly<Record<string, string>> }>) {
  const lags = Object.keys(r.terrain.relief.nu_m);
  return (
    <>
      <p className="text-lg font-semibold tracking-tight">{r.sentence}</p>
      <p className="mt-1 text-sm text-muted-foreground">{r.surface_notice}</p>
      <h3 className="mt-8 text-base font-semibold">
        Terrain fingerprint <span className="text-sm font-normal text-muted-foreground">· {r.terrain.label}</span>
      </h3>
      <p className="mt-1 text-sm text-muted-foreground">
        5 km window at {fmt(r.centre.lat_deg, 5)}°, {fmt(r.centre.lon_deg, 5)}° on a {r.terrain.analysis_grid_m} m D29 grid; valid
        fraction {fmt(r.terrain.valid_fraction, 3)}; isotropic rule {r.terrain.isotropic_rule}. Source: {r.source_product} (
        {r.dem.tiles_read.join(', ')}).
      </p>
      <div className="mt-3 overflow-x-auto rounded-lg ring-1 ring-foreground/10">
        <table className="w-full text-sm [&_td]:px-3 [&_td]:py-2 [&_th]:px-3 [&_th]:py-2 [&_thead]:bg-muted/50 [&_tr]:border-b [&_tbody_tr:last-child]:border-b-0">
          <caption className="caption-bottom px-3 py-2 text-left text-xs text-muted-foreground">ν(L) in metres by lag, and the Hurst exponent H</caption>
          <thead>
            <tr>
              <th scope="col" className="pr-4 text-left">
                Variant
              </th>
              {lags.map((lag) => (
                <th key={lag} scope="col" className="pr-4 text-left">
                  {Number(lag)} m
                </th>
              ))}
              <th scope="col" className="text-left">
                H
              </th>
            </tr>
          </thead>
          <tbody>
            <Curve variant={r.terrain.relief} title="Relief" />
            {r.terrain.texture === null ? (
              <tr>
                <th scope="row" className="pr-4 text-left font-medium">
                  Texture
                </th>
                <td colSpan={lags.length + 1}>Unavailable: {r.terrain.texture_reason}</td>
              </tr>
            ) : (
              <Curve variant={r.terrain.texture} title="Texture" />
            )}
          </tbody>
        </table>
      </div>
      <h3 className="mt-10 text-base font-semibold">Distances to the targets</h3>
      {r.targets.length === 0 ? (
        <p>Unavailable: {r.targets_reason ?? 'no targets'}</p>
      ) : (
        r.targets.map((t) => (
          <section key={t.target_id} aria-label={`Distances to ${names[t.target_id] ?? t.target_id}`} className="mt-4 rounded-xl bg-muted/40 p-4 ring-1 ring-foreground/10 sm:p-5">
            <h4 className="font-semibold">
              {names[t.target_id] ?? t.target_id} <span className="font-mono text-xs font-normal text-muted-foreground">{t.target_window_id}</span>
            </h4>
            <div className="grid gap-4 md:grid-cols-2">
              <ProbeCurves
                title={`Relief nu(L): this window over ${names[t.target_id] ?? t.target_id}`}
                window={r.terrain.relief}
                target={{ nu_m: t.target_relief_nu_m }}
                targetName={names[t.target_id] ?? t.target_id}
              />
              {r.terrain.texture === null || t.target_texture_nu_m === null ? null : (
                <ProbeCurves
                  title={`Texture nu(L): this window over ${names[t.target_id] ?? t.target_id}`}
                  window={r.terrain.texture}
                  target={{ nu_m: t.target_texture_nu_m }}
                  targetName={names[t.target_id] ?? t.target_id}
                />
              )}
            </div>
            <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-1.5 border-t pt-4 text-sm sm:grid-cols-[max-content_1fr] [&_dd]:mb-2 sm:[&_dd]:mb-0 [&_dt]:text-muted-foreground">
              <dt>Terrain relief</dt>
              <dd>
                <Distance d={t.terrain_relief} />
              </dd>
              <dt>Terrain texture</dt>
              <dd>
                <Distance d={t.terrain_texture} />
              </dd>
              <dt>Radar</dt>
              <dd>
                <Distance d={t.radar} />
              </dd>
              <dt>Learned encoder (experimental)</dt>
              <dd>
                <Distance d={t.encoder} label="Predicted, experimental" />
              </dd>
            </dl>
          </section>
        ))
      )}
      <p className="mt-4 text-sm text-muted-foreground">Smaller is closer within one family. Families are never added together, and targets are listed, not ranked.</p>
      <p className="mt-1 text-sm text-muted-foreground">Land cover: Unavailable: {r.worldcover.reason}.</p>
      <h3 className="mt-10 text-base font-semibold">Measured in this browser</h3>
      <p className="mt-1 text-sm text-muted-foreground tabular-nums">
        DEM read {ms(r.timings.dem_read_ms)} ({fmt(r.dem.bytes / 1e6, 2)} MB in {r.dem.requests} requests,{' '}
        {r.dem.within_prd_budget ? 'within' : 'over'} the 4 MB budget), resample {ms(r.timings.resample_ms)}, fingerprint{' '}
        {ms(r.timings.fingerprint_ms)}, total {ms(r.timings.total_ms)}.
      </p>
    </>
  );
}

export function ProbeCard({ result, names }: Readonly<{ result: ProbeResult; names: Readonly<Record<string, string>> }>) {
  if (result.kind === 'failure') {
    return (
      <p role="status">
        <span className="font-medium">Probe failed</span> ({result.reason}): {result.detail}
      </p>
    );
  }
  if (result.kind === 'stop') {
    return (
      <p role="status">
        <span className="font-medium">Probe stopped</span>: only {fmt(result.valid_fraction * 100, 1)}% of the 5 km window has
        elevation data (at least {fmt(result.min_valid_fraction * 100, 0)}% is required, reason {result.reason}). Missing tiles:{' '}
        {result.dem.tiles_missing.join(', ') || 'none'}.
      </p>
    );
  }
  return (
    <>
      <Success r={result} names={names} />
      <details className="mt-4 text-sm">
        <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Result record (JSON)</summary>
        <pre id="probe-result-json" className="mt-2 max-h-96 overflow-auto rounded-lg bg-muted/50 p-3 font-mono text-xs whitespace-pre-wrap">
          {JSON.stringify(result, null, 2)}
        </pre>
      </details>
    </>
  );
}
