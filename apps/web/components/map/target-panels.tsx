// The side panel of the Moon and Mars views (PRD 8.6, 10): target and window-grid facts, the
// nu(L) summary of the target window, layer list with legends and evidence labels, products and
// frames, and the window eligibility tally with reasons. Every figure is read from the serialised
// bundle records; a missing record renders an Unavailable panel that names the missing file.
import type { ReactNode } from 'react';

import { EvidenceLabel } from '@/components/evidence-label';
import { LabelledValue } from '@/components/labelled-value';

import { ELIGIBILITY_TONES, summariseWindows } from './eligibility.ts';
import type { MapTarget, NuSummary } from './explore-data.ts';
import { roleStatuses, unmatchedLayers } from './layer-roles.ts';
import { LayerLegend } from './legend.tsx';
import type { TileLayerRecord } from './tile-frame.ts';
import { UnavailablePanel } from './unavailable-panel.tsx';

function PanelSection({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2 border-t border-border pt-3 first:border-t-0 first:pt-0">
      <h3 className="text-sm font-semibold tracking-wide text-foreground uppercase">{title}</h3>
      {children}
    </section>
  );
}

/** The name inside a WKT (PROJCRS["name", ...]) or the first 48 characters of the CRS text. */
export function crsShortName(crs: string): string {
  const named = /^[A-Za-z_]*\[\s*"([^"]+)"/u.exec(crs);
  if (named?.[1] !== undefined) return named[1];
  return crs.length > 48 ? `${crs.slice(0, 48)}…` : crs;
}

export function TargetInfo({ target }: { readonly target: MapTarget }) {
  const grid = target.windowGrid;
  return (
    <PanelSection title="Target">
      <p className="text-base font-semibold">{target.name}</p>
      <p className="text-sm text-muted-foreground">{target.description}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Target id</dt>
        <dd>
          <code>{target.id}</code>
        </dd>
        <dt className="text-muted-foreground">Frame</dt>
        <dd>
          {target.projectionKind.replaceAll('_', ' ')}; {crsShortName(target.crs)}
          <details className="mt-1">
            <summary className="cursor-pointer text-xs text-muted-foreground">Full CRS</summary>
            <code className="block max-h-40 overflow-auto text-xs break-all whitespace-pre-wrap">{target.crs}</code>
          </details>
        </dd>
        <dt className="text-muted-foreground">Window</dt>
        <dd>
          {String(grid.window_m)} m window, {String(grid.stride_m)} m stride
        </dd>
        <dt className="text-muted-foreground">Primary window</dt>
        <dd>
          {target.primaryWindowId === null ? (
            <LabelledValue label="Unavailable" reason="primary_window_id is null until G0 freezes the window" source="targets.json" />
          ) : (
            <code>{target.primaryWindowId}</code>
          )}
        </dd>
        <dt className="text-muted-foreground">Windows generated</dt>
        <dd>
          {grid.n_generated === null ? (
            <LabelledValue label="Unavailable" reason="window_grid.n_generated is null: the grid is not built" source="targets.json" />
          ) : (
            <LabelledValue label="Derived" value={grid.n_generated} unit="windows" source="targets.json window_grid" />
          )}
        </dd>
        <dt className="text-muted-foreground">Windows eligible</dt>
        <dd>
          {grid.n_eligible === null ? (
            <LabelledValue label="Unavailable" reason="window_grid.n_eligible is null: the grid is not built" source="targets.json" />
          ) : (
            <LabelledValue label="Derived" value={grid.n_eligible} unit="windows" source="targets.json window_grid" />
          )}
        </dd>
      </dl>
      {target.mock ? (
        <p role="status" className="rounded border border-mock bg-mock/10 px-2 py-1 text-sm font-semibold">
          Mock target record: not a result.
        </p>
      ) : null}
    </PanelSection>
  );
}

export function NuPanel({ nu }: { readonly nu: NuSummary }) {
  if (nu.kind === 'unavailable') {
    return (
      <PanelSection title="nu(L) summary">
        <UnavailablePanel title="nu(L) of the target window" reason={nu.reason} missing={`windows/*.json terrain fingerprint`} />
      </PanelSection>
    );
  }
  const label = nu.evidenceLabel;
  const source = nu.productIds.join(', ');
  const cell = (value: number | null) =>
    value === null || label === 'Unavailable' ? (
      <LabelledValue label="Unavailable" reason="nu is null at this lag in the fingerprint" source={nu.windowId} />
    ) : (
      <LabelledValue label={label} value={value} unit="m" source={source} />
    );
  return (
    <PanelSection title="nu(L) summary">
      <p className="text-sm text-muted-foreground">
        Terrain variation of <code>{nu.windowId}</code> at each supported lag, on a {String(nu.analysisGridM)} m analysis
        grid with valid fraction {String(nu.validFraction)}. Relief and texture stay separate.
      </p>
      <div className="overflow-x-auto">
      <table className="w-full text-left text-sm [&_[data-slot=source]]:hidden">
        <caption className="sr-only">nu by lag for the target window</caption>
        <thead>
          <tr>
            <th scope="col" className="pr-2 font-medium">Lag (m)</th>
            <th scope="col" className="pr-2 font-medium">Relief</th>
            <th scope="col" className="font-medium">Texture</th>
          </tr>
        </thead>
        <tbody>
          {nu.rows.map((row) => (
            <tr key={row.lagM} className="align-top border-t border-border">
              <th scope="row" className="py-1 pr-2 font-normal">{row.lagM}</th>
              <td className="py-1 pr-2">{cell(row.relief)}</td>
              <td className="py-1">{cell(row.texture)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <p className="text-xs break-words text-muted-foreground" title={source}>
        Source: {source.split(/,\s+/u).map((p) => p.split('/').at(-1) ?? p).join(', ')}
      </p>
      {nu.hurstRelief === null ? null : (
        <p className="text-sm">
          Relief Hurst exponent: <LabelledValue label={label === 'Unavailable' ? 'Derived' : label} value={nu.hurstRelief} unit={null} source={source} />
        </p>
      )}
    </PanelSection>
  );
}

function LayerRow({
  layer,
  active,
  onToggle,
}: {
  readonly layer: TileLayerRecord;
  readonly active: boolean;
  readonly onToggle: ((layer: string, on: boolean) => void) | null;
}) {
  const id = `layer-${layer.layer}`;
  return (
    <li className="flex flex-col gap-1 rounded-md border border-border bg-background/40 p-2">
      <div className="flex flex-wrap items-center gap-2">
        {onToggle === null ? null : (
          <input
            id={id}
            type="checkbox"
            checked={active}
            onChange={(e) => { onToggle(layer.layer, e.target.checked); }}
            className="size-4 accent-primary"
          />
        )}
        <label htmlFor={id} className="text-sm font-medium">
          {layer.layer}
        </label>
        <EvidenceLabel label={layer.evidence_label} />
        {layer.mock ? <span className="text-xs font-semibold">mock layer</span> : null}
      </div>
      <p className="text-xs text-muted-foreground">
        {layer.units === null ? 'no unit' : `unit ${layer.units}`}; zoom {String(layer.zoom_min)} to {String(layer.zoom_max)};
        sources {layer.source_product_ids.join(', ') || 'none listed'}
        {layer.hillshade === null
          ? ''
          : `; hillshade azimuth ${String(layer.hillshade.azimuth_deg)} deg, altitude ${String(layer.hillshade.altitude_deg)} deg`}
      </p>
      <LayerLegend layer={layer} />
    </li>
  );
}

export function LayersPanel({
  body,
  target,
  active,
  onToggle,
}: {
  readonly body: 'moon' | 'mars';
  readonly target: MapTarget;
  readonly active: readonly string[];
  readonly onToggle: ((layer: string, on: boolean) => void) | null;
}) {
  const roles = roleStatuses(body, target.layers);
  const others = unmatchedLayers(body, target.layers);
  const problemFor = new Map(target.layerProblems.map((p) => [p.layer, p.reason]));
  return (
    <PanelSection title="Layers">
      {target.layers.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          TargetRecord.tile_layers is empty for this target, so no layer can be listed.
        </p>
      ) : null}
      {roles.map(({ role, layers }) => (
        <div key={role.id} className="flex flex-col gap-2">
          <h4 className="text-sm font-medium">{role.title}</h4>
          {layers.length === 0 ? (
            <UnavailablePanel title={role.title} reason={role.absentReason} missing={`${target.id} tile_layers entry`} />
          ) : (
            <ul className="flex flex-col gap-2">
              {layers.map((layer) => {
                const problem = problemFor.get(layer.layer);
                return problem === undefined ? (
                  <LayerRow key={layer.layer} layer={layer} active={active.includes(layer.layer)} onToggle={onToggle} />
                ) : (
                  <li key={layer.layer}>
                    <UnavailablePanel title={layer.layer} reason={problem} />
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ))}
      {others.length === 0 ? null : (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-medium">Other layers in the bundle</h4>
          <ul className="flex flex-col gap-2">
            {others.map((layer) => (
              <LayerRow key={layer.layer} layer={layer} active={active.includes(layer.layer)} onToggle={onToggle} />
            ))}
          </ul>
        </div>
      )}
      {body === 'mars' ? (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-medium">Radar</h4>
          <UnavailablePanel
            title="Radar on Mars"
            reason="Mars views are terrain only (PRD 10); no radar comparison is made."
          />
        </div>
      ) : null}
    </PanelSection>
  );
}

export function ProductsPanel({ target }: { readonly target: MapTarget }) {
  return (
    <PanelSection title="Products and frames">
      {target.products.length === 0 && target.productsMissing.length === 0 ? (
        <p className="text-sm text-muted-foreground">The target record lists no source products.</p>
      ) : null}
      <details className="text-sm">
      <summary className="cursor-pointer text-muted-foreground">
        {String(target.products.length + target.productsMissing.length)} products (click to expand)
      </summary>
      <ul className="mt-2 flex flex-col gap-2 text-xs break-words">
        {target.products.map((p) => (
          <li key={p.id}>
            <code>{p.id}</code> ({p.provider}, {p.version}); frame {p.frame}
            {p.band === null ? '' : `; band ${p.band}`}
            {p.qa === null ? '' : `; validity: ${p.qa}`}. {p.licence}
          </li>
        ))}
        {target.productsMissing.map((id) => (
          <li key={id}>
            <code>{id}</code>: <LabelledValue label="Unavailable" reason="ProductRecord not in sources.json" source="sources.json" />
          </li>
        ))}
      </ul>
      </details>
    </PanelSection>
  );
}

export function WindowsPanel({ target }: { readonly target: MapTarget }) {
  const summary = summariseWindows(target.windows);
  return (
    <PanelSection title="Window grid">
      {!target.windowsFilePresent ? (
        <UnavailablePanel
          title="Window records"
          reason="The window grid cannot be drawn without the target windows."
          missing={target.windowsFile}
        />
      ) : (
        <>
          <p className="text-sm">
            <span data-slot="window-count" className="font-semibold">
              {String(summary.total)}
            </span>{' '}
            windows in <code>{target.windowsFile}</code> fall in this target. This is the bundle&apos;s top-N subset, not the whole
            grid. <EvidenceLabel label="Derived" />
          </p>
          <ul className="flex flex-col gap-1 text-sm" aria-label="Eligibility key">
            {(['eligible', 'rejected', 'unknown'] as const).map((status) => {
              const tone = ELIGIBILITY_TONES[status];
              return (
                <li key={status} className="flex items-center gap-2">
                  <span aria-hidden="true" data-status={status} className={`inline-block size-4 rounded-sm border-2 ${tone.className}`} />
                  <span>
                    {tone.label}: <span data-slot="status-count">{String(summary.byStatus[status])}</span>
                  </span>
                </li>
              );
            })}
          </ul>
          {summary.reasons.length === 0 ? (
            <p className="text-sm text-muted-foreground">No window carries an exclusion reason.</p>
          ) : (
            <div>
              <h4 className="text-sm font-medium">Exclusion reasons (as serialized)</h4>
              <ul className="text-sm">
                {summary.reasons.map(([reason, count]) => (
                  <li key={reason}>
                    <code>{reason}</code>: {String(count)}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {target.windows.length === 0 ? null : (
            <details>
              <summary className="cursor-pointer text-sm font-medium">Numbered window list</summary>
              <ol className="mt-1 text-xs">
                {target.windows.map((w) => (
                  <li key={w.id}>
                    <code className="break-all">{w.id}</code> {ELIGIBILITY_TONES[w.status].label}
                    {w.reasons.length === 0 ? '' : `: ${w.reasons.join(', ')}`}
                    {w.isPrimary ? ' (target window)' : ''}
                  </li>
                ))}
              </ol>
            </details>
          )}
          <p className="text-xs text-muted-foreground">
            Excluded windows are shown with a dashed outline and keep their reasons; they are not poor matches.
          </p>
        </>
      )}
    </PanelSection>
  );
}
