// Client-only mount of the ported ShadowAstra Atlas: it reads WebGL support and the URL query at
// start-up, so it renders in the browser only, and its Leaflet, three.js and grid code load as a
// separate chunk on the /explore routes.
"use client";

import dynamic from "next/dynamic";

import type { Body } from "@/lib/shadowastra-atlas/data/sites";

import type { TerralunaAtlas } from "./terraluna-data";

const Atlas = dynamic(() => import("./Atlas"), {
  ssr: false,
  loading: () => (
    <div className="grid h-[calc(100dvh-98px)] min-h-[520px] place-items-center bg-void font-mono text-[11px] tracking-widest text-chrome uppercase" aria-busy="true">
      Loading atlas
    </div>
  ),
});

export function AtlasClient({ body, terraluna }: { body: Body; terraluna: TerralunaAtlas }) {
  return (
    <div className="sa-atlas" data-full-bleed>
      <Atlas initialBody={body} terraluna={terraluna} />
    </div>
  );
}
