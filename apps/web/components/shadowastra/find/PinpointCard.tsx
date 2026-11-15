'use client';

// Copied from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
import Link from "next/link";
import { Crosshair } from "lucide-react";
import { formatCoord } from "@/lib/shadowastra/geo";
import { usePinpoint } from "@/lib/shadowastra/pinpoint";
import { useValidation } from "@/lib/shadowastra/validation";
import type { TaskId } from "@/lib/shadowastra/data/sites";
import { KindBadge } from "../ui/kit";

/** What the 66-site test of the pinpoint found, in one plain sentence. */
export function pinpointVerdict(t: NonNullable<ReturnType<typeof useValidation>["v"]>["pinpoint"]) {
  if (!t) return null;
  const a = t.all;
  const pct = Math.round(a.median_rank * 100);
  return t.works
    ? `Tested on ${a.n} real field sites: the 10 km piece holding the site typically (median) ranks in the best ${pct}% of the place's pieces (chance is 50%).`
    : `Tested on ${a.n} real field sites, the piece holding the site typically (median) ranks at ${pct}% where chance is 50%: a slight lean, not a clear one. Treat it as an experimental ground-shape hint, not a prediction.`;
}

/** The best 10 km spot inside a 55 km place, for the chosen target. */
export function PinpointCard({ lat, lon, targetHist, targetId, targetName, task }: { lat: number; lon: number; targetHist: number[]; targetId: string; targetName: string; task: TaskId }) {
  const pin = usePinpoint(lat, lon, targetHist);
  const { v } = useValidation();
  const verdict = pinpointVerdict(v?.pinpoint);
  return (
    <div className="mt-4 rounded-xl border bg-card p-6 text-card-foreground shadow-xs" aria-live="polite">
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-2 text-[13px] text-foreground">
          <Crosshair size={14} className="text-primary" /> Best 10 km spot <KindBadge kind="computed" />
        </span>
        <span className="text-xs font-medium text-muted-foreground">{lat <= 60 && lat >= -56 ? "SRTM" : "Terrain Tiles"} · 1.2 km grid</span>
      </div>
      {pin === null && <div className="shimmer mt-3 h-[58px] rounded-xl" />}
      {pin === "failed" && <p className="mt-2 text-[12px] text-muted-foreground">Terrain tiles are still on their way.</p>}
      {pin && pin !== "failed" && (
        <>
          <p className="mt-2 text-[12.5px] leading-relaxed text-muted-foreground">
            Best of {pin.windows} tiles at <span className="font-medium tabular-nums text-foreground">{formatCoord(pin.best.lat, pin.best.lon)}</span> · average {Math.round(pin.mean * 100)}% alike to {targetName}.
          </p>
          <dl className="mt-3 grid grid-cols-3 gap-px overflow-hidden rounded-xl border bg-border text-center">
            {[
              ["Slopes alike", `${Math.round(pin.best.score * 100)}%`],
              ["Average slope", `${pin.best.meanSlope.toFixed(1)}°`],
              ["Height range", `${Math.round(pin.best.relief)} m`],
            ].map(([a, b]) => (
              <div key={a} className="bg-card px-2 py-2">
                <dt className="text-[10.5px] text-muted-foreground">{a}</dt>
                <dd className="text-[15px] font-semibold tabular-nums text-foreground">{b}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
            <Link href={`/probe?lat=${pin.best.lat.toFixed(3)}&lon=${pin.best.lon.toFixed(3)}`} data-target={targetId} data-task={task} className="text-[12px] text-primary hover:underline">
              Compare this spot in 3D
            </Link>
            {pin.missing > 0.05 && <span className="text-[11px] text-muted-foreground">{Math.round(pin.missing * 100)}% of heights missing here</span>}
          </div>
        </>
      )}
      {verdict && <p className="mt-3 border-t pt-3 text-[11px] leading-relaxed text-muted-foreground">{verdict}</p>}
    </div>
  );
}
