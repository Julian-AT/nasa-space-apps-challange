'use client';

// Copied from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
import { useEffect, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import { Menu, Moon, Pause, Play, Search, Sun, X } from "lucide-react";
import { setMotionPaused, useMotionPaused, useMotionReduced } from "@/lib/shadowastra/motionPref";
import clsx from "clsx";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Fit } from "@/lib/shadowastra/data/sites";
import { FIT_LABEL } from "@/lib/shadowastra/data/sites";

export function Github({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M12 .5a11.5 11.5 0 0 0-3.64 22.41c.58.1.79-.25.79-.56v-2c-3.2.7-3.88-1.37-3.88-1.37-.52-1.33-1.28-1.69-1.28-1.69-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.68 0-1.25.45-2.28 1.19-3.08-.12-.29-.52-1.46.11-3.04 0 0 .97-.31 3.17 1.18a11 11 0 0 1 5.77 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.58.23 2.75.11 3.04.74.8 1.19 1.83 1.19 3.08 0 4.41-2.69 5.38-5.26 5.67.41.36.78 1.06.78 2.14v3.17c0 .31.21.67.8.56A11.5 11.5 0 0 0 12 .5Z" />
    </svg>
  );
}

export const REPO_URL = "https://github.com/ABRUBAB/ShadowAstra_Atlas";
/** Every score as open tables (pipeline/s21_open_dataset.py). */
export const DATASET_URL = "https://huggingface.co/datasets/ABRUBAB/shadowastra-atlas";

export function Logo({ size = 34, withWord = true }: { size?: number; withWord?: boolean }) {
  return (
    <Link href="/" className="group flex items-center gap-3" aria-label="ShadowAstra Atlas home">
      <img
        src="/brand/shadowastra-logo.webp"
        alt=""
        width={size}
        height={size}
        className="logo-mark rounded-full mix-blend-screen transition-transform duration-500 ease-out group-hover:rotate-[20deg]"
        style={{ width: size, height: size }}
      />
      {withWord && (
        <span className="font-wide text-[10.5px] tracking-[0.14em] whitespace-nowrap text-platinum min-[420px]:text-[12px] min-[420px]:tracking-[0.18em]">
          SHADOWASTRA<span className="hidden text-earth min-[400px]:inline"> ATLAS</span>
        </span>
      )}
    </Link>
  );
}

/* Theme: one implementation for the whole app (components/theme-toggle.tsx). */
export { ThemeToggle, useTheme } from "@/components/theme-toggle";

export const LINKS = [
  { to: "/", label: "Home" },
  { to: "/discover", label: "Find new sites" },
  { to: "/lab", label: "Compare" },
  { to: "/atlas", label: "Maps" },
  { to: "/methods", label: "How it works" },
  { to: "/learn", label: "Learn" },
  { to: "/about", label: "Team" },
];

// Nav and Footer omitted: Terraluna renders its own site header and footer.

const FIT_STYLE: Record<Fit, string> = {
  strong: "text-strong border-strong/40 bg-strong/10",
  partial: "text-partial border-partial/40 bg-partial/10",
  weak: "text-weak border-weak/40 bg-weak/10",
  unknown: "text-muted-foreground border-dashed border-border bg-transparent",
};
const FIT_GLYPH: Record<Fit, string> = { strong: "●", partial: "◐", weak: "○", unknown: "?" };

export function FitBadge({ fit }: { fit: Fit }) {
  return (
    <Badge variant="outline" className={clsx("h-5 gap-1.5 px-2 text-[11px] font-medium", FIT_STYLE[fit])}>
      <span aria-hidden>{FIT_GLYPH[fit]}</span>
      {FIT_LABEL[fit]}
    </Badge>
  );
}

export function Eyebrow({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={clsx("flex items-center gap-3 text-xs font-medium text-muted-foreground", className)}>
      <span className="h-px w-8 bg-border" />
      {children}
    </div>
  );
}

export const WORLD_COLOR = { earth: "#8fb4ea", moon: "#c9c5bc", mars: "#e08a5f" } as const;

export function WorldDot({ world, className }: { world: keyof typeof WORLD_COLOR; className?: string }) {
  return (
    <span
      className={clsx("inline-block size-2 shrink-0 rounded-full ring-2 ring-background", className)}
      style={{ background: `var(--color-${world})` }}
    />
  );
}
