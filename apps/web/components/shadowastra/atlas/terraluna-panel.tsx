// Terraluna's own sections inside the ported ShadowAstra Atlas (PRD 3, 8.6, 10): layer toggles,
// the evidence-card list and a pin detail. Every value comes from TerralunaAtlas, which
// terraluna-data.ts reads from the release bundle; relief values keep their own evidence labels.
// The ShadowAstra site ratings, scores and twins around it come from their pipeline, which the
// attribution line says wherever they are shown.
"use client";

import clsx from "clsx";
import { ArrowRight, MapPin, X } from "lucide-react";
import Link from "next/link";

import { EvidenceLabel, isEvidenceLabel } from "@/components/evidence-label";
import { buttonVariants } from "@/components/ui/button";
import type { WindowRelief } from "@/components/map/explore-data";
import type { Body } from "@/lib/shadowastra-atlas/data/sites";

import type { TerralunaAtlas, TlCard } from "./terraluna-data";

export const SA_SITE = "https://shadowastra-atlas.vercel.app";
export const TERRALUNA_COLOR = "#f2c879";
export const MODE_C_NOTE = "Terrain evidence";
export const SA_ATTRIBUTION = "ShadowAstra Atlas (MIT)";

const BODY_NAME: Record<Body, string> = { earth: "Earth", moon: "Moon", mars: "Mars" };

export function TerralunaAttribution({ className }: { className?: string }) {
  return <p className={clsx("text-[11px] leading-relaxed text-dim", className)}>{SA_ATTRIBUTION}</p>;
}

function Relief({ r, what }: { r: WindowRelief | null; what: string }) {
  const ok = r !== null && r.evidenceLabel !== "Unavailable";
  return (
    <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
      <span className="truncate" title={ok ? `${what}, relief ν at ${r.lagM} m lag` : undefined}>
        {what.replace(" window", "")}
        {ok ? <span> · relief at {Math.round(Number(r.lagM))} m</span> : null}
      </span>
      <span className="flex shrink-0 items-center gap-2 tabular-nums">
        <span className="font-medium text-foreground">{ok ? `${r.valueM.toFixed(2)} m` : "—"}</span>
        {ok ? <EvidenceLabel label={isEvidenceLabel(r.evidenceLabel) ? r.evidenceLabel : "Unavailable"} /> : null}
      </span>
    </div>
  );
}

function CardRow({ c, onPin }: { c: TlCard; onPin: (id: string) => void }) {
  return (
    <li className="border-b py-4 last:border-b-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-foreground">{c.targetName}</div>
          <div className="truncate text-[11px] text-muted-foreground" title={c.earthWindowId}>↔ {c.earthWindowId}</div>
          <div className="mt-2 space-y-1.5">
            <Relief r={c.targetRelief} what={`${BODY_NAME[c.targetBody]} window`} />
            <Relief r={c.earthRelief} what="Earth window" />
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <Link href={`/site/${c.cardId}`} className={clsx(buttonVariants({ variant: "outline", size: "sm" }), "no-underline")}>
            Card <ArrowRight size={11} />
          </Link>
          {(c.earthPinId ?? c.targetPinId) && (
            <button onClick={() => onPin((c.earthPinId ?? c.targetPinId) as string)} className={clsx(buttonVariants({ variant: "ghost", size: "sm" }), "text-muted-foreground")} aria-label={`Show ${c.cardId} on the map`}>
              <MapPin size={11} /> Show
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

export function TerralunaEvidence({ data, onPin }: { data: TerralunaAtlas; onPin: (id: string) => void }) {
  return (
    <section aria-labelledby="tl-evidence" className="border-b border-line p-5">
      <div className="flex items-center gap-2 text-xs font-medium text-primary">
        <span className="size-2 rounded-full" style={{ background: TERRALUNA_COLOR }} aria-hidden />
        Terraluna
      </div>
      <h2 id="tl-evidence" className="mt-1.5 text-xl leading-tight font-medium tracking-tight">
        Terraluna evidence
      </h2>
      <p className="mt-1 font-mono text-[10.5px] text-dim">Bundle {data.bundleVersion}{data.mock ? " · MOCK, not production" : ""}</p>
      <div className="mt-4 text-xs font-medium text-muted-foreground">Evidence cards · {data.cards.length}</div>
      {data.cards.length === 0 ? (
        <p className="mt-2 text-[12.5px] text-chrome">No evidence cards yet.</p>
      ) : (
        <ul className="mt-1 border-t border-line">
          {data.cards.map((c) => (
            <CardRow key={c.cardId} c={c} onPin={onPin} />
          ))}
        </ul>
      )}
      <p className="mt-3 text-[11px] leading-relaxed text-dim">Relief: terrain relief from the windows file terrain fingerprint, at the shortest lag the bundle holds.</p>
    </section>
  );
}

export function TerralunaDetail({ id, data, onClose, onPin }: { id: string; data: TerralunaAtlas; onClose: () => void; onPin: (id: string) => void }) {
  const pin = data.pins.find((p) => p.id === id);
  if (!pin) return null;
  const cards = data.cards.filter((c) => pin.cardIds.includes(c.cardId));
  return (
    <div className="space-y-4 p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs font-medium text-primary">Terraluna · {pin.kind === "target" ? `${BODY_NAME[pin.body]} target` : "Earth card window"}</div>
          <h2 className="mt-1.5 text-xl leading-tight font-medium tracking-tight">{pin.label}</h2>
          <p className="mt-1 font-mono text-[11px] text-dim">
            {pin.lat.toFixed(3)}°, {pin.lon.toFixed(3)}° · bundle {data.bundleVersion}
          </p>
        </div>
        <button onClick={onClose} aria-label="Close" className="grid size-8 shrink-0 place-items-center rounded-full border border-line-2 text-chrome hover:text-platinum">
          <X size={14} />
        </button>
      </div>
      {cards.length > 0 ? (
        <ul className="border-t border-line">
          {cards.map((c) => (
            <CardRow key={c.cardId} c={c} onPin={onPin} />
          ))}
        </ul>
      ) : (
        <p className="text-[12.5px] text-chrome">
          No evidence card for this place yet.
        </p>
      )}
      {pin.href && (
        <Link href={pin.href} className="btn-primary inline-flex items-center gap-2 rounded-full px-4 py-2 text-[12.5px]">
          Open the evidence card <ArrowRight size={14} />
        </Link>
      )}
    </div>
  );
}

export function TerralunaLayers({
  body,
  data,
  showPins,
  onPins,
  hillshade,
  onHillshade,
}: {
  body: Body;
  data: TerralunaAtlas;
  showPins: boolean;
  onPins: (on: boolean) => void;
  hillshade: boolean;
  onHillshade: (on: boolean) => void;
}) {
  const count = data.pins.filter((p) => p.body === body).length;
  const projected = data.projectedHillshade.filter((l) => l.body === body);
  return (
    <div className="space-y-2">
      <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-line p-3">
        <input type="checkbox" checked={showPins} onChange={(e) => onPins(e.target.checked)} className="mt-0.5 size-4 accent-[#f2c879]" />
        <span>
          <span className="block text-[13px] text-platinum">Terraluna pins · {count}</span>
          <span className="block text-[11px] leading-relaxed text-dim">{body === "earth" ? "Evidence card windows" : "Bundle targets"}</span>
        </span>
      </label>
      {body === "earth" ? (
        <label className={clsx("flex items-start gap-3 rounded-xl border border-line p-3", data.earthHillshade.length > 0 ? "cursor-pointer" : "opacity-60")}>
          <input type="checkbox" checked={hillshade} disabled={data.earthHillshade.length === 0} onChange={(e) => onHillshade(e.target.checked)} className="mt-0.5 size-4 accent-[#f2c879]" />
          <span>
            <span className="block text-[13px] text-platinum">Bundle hillshade · {data.earthHillshade.length}</span>
            <span className="block text-[11px] leading-relaxed text-dim">
              {data.earthHillshade.length > 0
                ? `${data.earthHillshade.map((l) => l.evidenceLabel).filter((v, i, a) => a.indexOf(v) === i).join(", ")} · tiles from the release bundle · 2D only`
                : "No map tiles for this place"}
            </span>
          </span>
        </label>
      ) : (
        <p className="rounded-xl border border-dashed border-line-2 px-3 py-2.5 text-[11px] leading-relaxed text-dim">
          {projected.length > 0
            ? `${projected.length} bundle hillshade pyramid${projected.length > 1 ? "s" : ""} for ${BODY_NAME[body]}, in each target's projected metre frame; it opens on the evidence card, not on this global map.`
            : `No bundle hillshade for ${BODY_NAME[body]} in this bundle.`}
        </p>
      )}
    </div>
  );
}
