// The live-probe result card (PRD 8.7 step 7). Pure presentation of a ProbeResult: every number
// carries its label, Unavailable values show their reason and never 0, and each distance family
// has its own row. Nothing here combines families or ranks targets.
import { ProbeCurves } from './probe-curves.tsx';
import type { DistanceValue, ProbeResult, ProbeSuccess, ProbeVariant } from '../../lib/probe/result.ts';

const ms = (v: number): string => `${String(Math.round(v))} ms`;

const fmt = (v: number, digits = 3): string => (Math.abs(v) >= 1000 || (Math.abs(v) < 0.001 && v !== 0) ? v.toExponential(digits) : v.toFixed(digits));

function Distance({ d }: Readonly<{ d: DistanceValue }>) {
  if (d.status === 'unavailable') {
    return (
      <span>
        <span className="font-medium">Unavailable</span>: {d.reason}
      </span>
    );
  }
  return (
    <span>
      {fmt(d.value)} <span className="text-sm">{d.unit}</span> · <span className="text-sm">{d.label}</span> ·{' '}
      {d.shared_lags_m.length > 0 ? <span className="text-sm">lags {d.shared_lags_m.join(', ')} m</span> : null}
      {d.background_percentile === undefined ? null : (
        <span className="text-sm">
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
      <p className="font-medium">{r.sentence}</p>
      <p className="text-sm">{r.surface_notice}</p>
      <h3 className="mt-8 text-base font-semibold">
        Terrain fingerprint · <span className="text-sm">{r.terrain.label}</span>
      </h3>
      <p className="text-sm">
        5 km window at {fmt(r.centre.lat_deg, 5)}°, {fmt(r.centre.lon_deg, 5)}° on a {r.terrain.analysis_grid_m} m D29 grid; valid
        fraction {fmt(r.terrain.valid_fraction, 3)}; isotropic rule {r.terrain.isotropic_rule}. Source: {r.source_product} (
        {r.dem.tiles_read.join(', ')}).
      </p>
      <div className="overflow-x-auto">
        <table className="mt-2 text-sm [&_td]:py-1 [&_th]:py-1">
          <caption className="text-left">nu(L) in metres by lag, and H</caption>
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
          <section key={t.target_id} aria-label={`Distances to ${names[t.target_id] ?? t.target_id}`} className="mt-4 rounded-md bg-muted p-4">
            <h4 className="font-medium">
              {names[t.target_id] ?? t.target_id} <span className="text-sm">({t.target_window_id})</span>
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
            <dl className="mt-3 grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_1fr] [&_dd]:mb-2 sm:[&_dd]:mb-0 [&_dt]:text-muted-foreground">
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
              <dt>EXPERIMENTAL encoder (never ranked)</dt>
              <dd>
                <Distance d={t.encoder} />
              </dd>
            </dl>
          </section>
        ))
      )}
      <p className="mt-2 text-sm">Smaller is closer within one family. Families are never added together, and targets are listed, not ranked.</p>
      <p className="mt-2 text-sm">Land cover: Unavailable: {r.worldcover.reason}.</p>
      <h3 className="mt-10 text-base font-semibold">Measured in this browser</h3>
      <p className="text-sm tabular-nums">
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
        <summary>Result record (JSON)</summary>
        <pre id="probe-result-json" className="overflow-x-auto whitespace-pre-wrap">
          {JSON.stringify(result, null, 2)}
        </pre>
      </details>
    </>
  );
}
