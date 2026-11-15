'use client';
// Copied from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The floating site chrome mounted once in the root layout, as ShadowAstra's App.tsx does: Ask Luna
// and the Ctrl-K command palette. Cards and the
// Astra context come from the release bundle via the server layout.
import { usePathname } from "next/navigation";

import type { AstraContext } from "@/lib/shadowastra-chrome/astra";

import { AskAstra } from "./AskAstra";
import { CommandPalette, type PaletteCard } from "./CommandPalette";

export function SiteChrome({ cards, ctx }: Readonly<{ cards: readonly PaletteCard[]; ctx: AstraContext }>) {
  const pathname = usePathname() ?? "/";
  // The printable report and paper stay clean: no floating buttons over their text.
  const clean = pathname.startsWith("/report") || pathname.startsWith("/paper") || pathname.startsWith("/pitch");
  return (
    <>
      {!clean && <AskAstra ctx={ctx} />}
      <CommandPalette cards={cards} />
    </>
  );
}
