'use client';

// Copied from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The per-factor breakdown of ShadowAstra's Analog Fit for one Earth point and one of their
// targets, assembled from their own pieces: PARAMS and TASK_WEIGHTS (lib/scoring.ts), scoreCell on
// their 0.5 degree Earth grid (lib/grid.ts), and their Meter and KindBadge (ui/kit.tsx). Nothing
// here is a Terraluna measurement; the provenance label says so on every render.
import clsx from "clsx";

import { KindBadge, Meter } from "@/components/shadowastra/ui/kit";
import { scoreCell, shapeOptions, useGrid } from "@/lib/shadowastra/grid";
import { modelLine } from "@/lib/shadowastra/gaps";
import { PARAMS, TASK_WEIGHTS } from "@/lib/shadowastra/scoring";
import { TARGET_BY_ID, TASKS, type TaskId } from "@/lib/shadowastra/data/sites";

export const FACTOR_PROVENANCE = "ShadowAstra Atlas (MIT)";

export function FactorProvenance({ className }: { className?: string }) {
  return (
    <p className={clsx("font-mono text-[10px] leading-snug tracking-wide text-dim", className)} data-provenance="shadowastra">
      {FACTOR_PROVENANCE}
    </p>
  );
}

export interface FactorBreakdownProps {
  /** Earth latitude in degrees. */
  lat: number;
  /** Earth longitude in degrees. */
  lon: number;
  /** A ShadowAstra target id (data/sites.ts TARGETS), e.g. "jezero", "arcadia", "shackleton". */
  targetId: string;
  /** Mission job; defaults to the target's first listed task. */
  task?: TaskId;
  className?: string;
}

/** ShadowAstra's six factors for one Earth point against one of their targets, with weights and badges. */
export function FactorBreakdown({ lat, lon, targetId, task, className }: FactorBreakdownProps) {
  const { grid, progress, error, retry } = useGrid();
  const T = TARGET_BY_ID[targetId];
  const job: TaskId | undefined = task ?? T?.tasks[0];

  const shell = (body: React.ReactNode) => (
    <section className={clsx("surface rounded-2xl p-6", className)} aria-label="ShadowAstra factor breakdown" data-factor-breakdown>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-balance text-sm font-semibold text-platinum">Factor breakdown</h3>
        <div className="flex flex-wrap gap-1">
          <KindBadge kind="computed" />
          <KindBadge kind="model" />
          <KindBadge kind="demo" />
          <KindBadge kind="gap" />
        </div>
      </div>
      <FactorProvenance className="mt-1.5" />
      {body}
    </section>
  );

  if (!T || !job) return shell(<p className="mt-3 text-[12.5px] text-chrome">No factor scores for this place.</p>);
  if (error)
    return shell(
      <p role="alert" className="mt-3 text-[12.5px] text-chrome">
        Factor scores are loading slowly.{" "}
        <button type="button" onClick={retry} className="text-flare underline">
          Try again
        </button>
      </p>,
    );
  if (!grid) return shell(<p className="mt-3 text-[12.5px] text-chrome" role="status">Loading the ShadowAstra grid… {Math.round(progress * 100)}%</p>);

  const k = grid.cellAt(lat, lon, 3);
  const t = grid.targets[targetId];
  if (k < 0 || !t) return shell(<p className="mt-3 text-[12.5px] text-chrome">No factor scores near this point.</p>);

  const weights = TASK_WEIGHTS[job];
  const cs = scoreCell(grid, k, { target: t, task: job, weights, ...shapeOptions("formula", targetId) });
  const wsum = Object.values(weights).reduce((a, b) => a + b, 0) || 1;
  const jobLabel = TASKS.find((x) => x.id === job)?.label ?? job;

  return shell(
    <>
      <p className="mt-2 truncate text-[12px] text-dim">
        {T.name} · {jobLabel}
      </p>
      <div className="mt-4 space-y-3 tabular-nums">
        {PARAMS.map((p) =>
          p.id === "radiation" ? null : (
            <Meter
              key={p.id}
              value={cs.dims[p.id]}
              label={p.label}
              kind="computed"
              right={cs.dims[p.id].toFixed(2)}
              hint={`Weight ${Math.round((weights[p.id] / wsum) * 100)}%`}
              thin
            />
          ),
        )}
        <Meter value={cs.model} label="Learned model" kind="model" tone="var(--color-earth)" right={cs.model.toFixed(2)} hint={modelLine(cs.model, T.body)} thin />
      </div>
      <div className="mt-3 flex items-center justify-between border-t border-line pt-3 text-[12.5px]">
        <span className="text-chrome">Match</span>
        <span className="font-mono text-platinum tabular-nums">{cs.total.toFixed(2)}</span>
      </div>
    </>,
  );
}
