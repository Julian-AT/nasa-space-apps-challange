'use client';
// Copied from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The floating site chrome mounted once in the root layout, as ShadowAstra's App.tsx does: the
// first-visit splash, Ask Astra, the Ctrl-K command palette and the badge toasts. Cards and the
// Astra context come from the release bundle via the server layout.
import { usePathname } from "next/navigation";
import { useEffect } from "react";

import type { AstraContext } from "@/lib/shadowastra-chrome/astra";

import { AskAstra } from "./AskAstra";
import { BadgeToast } from "./BadgeToast";
import { CommandPalette, type PaletteCard } from "./CommandPalette";
import { Splash } from "./Splash";

export function SiteChrome({ cards, ctx }: Readonly<{ cards: readonly PaletteCard[]; ctx: AstraContext }>) {
  const pathname = usePathname() ?? "/";
  // Re-apply the remembered theme after hydration, which can reset attributes on <html>.
  useEffect(() => {
    try {
      const saved = new URLSearchParams(location.search).get("theme") ?? localStorage.getItem("sa-theme");
      if ((saved === "light" || saved === "dark") && document.documentElement.dataset.theme !== saved) {
        document.documentElement.dataset.theme = saved;
        window.dispatchEvent(new Event("sa-theme"));
      }
    } catch {
      /* storage blocked: dark default */
    }
  }, []);
  // The printable report and paper stay clean: no floating buttons over their text.
  const clean = pathname.startsWith("/report") || pathname.startsWith("/paper");
  return (
    <>
      <Splash />
      {!clean && <AskAstra ctx={ctx} />}
      <CommandPalette cards={cards} />
      {!clean && <BadgeToast />}
    </>
  );
}

/** Applies the remembered theme before first paint (dark is the default). */
export const THEME_SCRIPT = `try{var t=localStorage.getItem("sa-theme");if(t==="light")document.documentElement.dataset.theme="light"}catch(e){}`;
