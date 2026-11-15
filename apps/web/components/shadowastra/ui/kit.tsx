// @ts-nocheck -- vendored verbatim; type-checks clean under upstream strict:true, not under Terraluna's extra noUncheckedIndexedAccess/exactOptionalPropertyTypes/noImplicitOverride.
'use client';

// Copied from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
/**
 * Shared interface kit. Calm, physical motion: things draw in when they enter the
 * view, numbers count, surfaces catch a soft light that follows the pointer.
 * Every effect respects prefers-reduced-motion.
 */
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, animate, motion, useInView, useMotionValue, useSpring } from "motion/react";
import { useMotionReduced } from "@/lib/shadowastra/motionPref";
import clsx from "clsx";
import { ArrowUp, Check, Info, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

export const EASE = [0.22, 1, 0.36, 1] as const;

/* ------------------------------------------------------------- spotlight */

/**
 * One listener for the whole app: any element with the `surface` class gets a
 * light that follows the mouse (CSS reads --mx / --my).
 */
export function useSpotlight() {
  useEffect(() => {
    let raf = 0;
    let last: { el: HTMLElement; x: number; y: number } | null = null;
    const on = (e: PointerEvent) => {
      if (e.pointerType !== "mouse") return;
      const el = (e.target as Element | null)?.closest?.(".surface") as HTMLElement | null;
      if (!el) return;
      last = { el, x: e.clientX, y: e.clientY };
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (!last) return;
        const r = last.el.getBoundingClientRect();
        last.el.style.setProperty("--mx", `${last.x - r.left}px`);
        last.el.style.setProperty("--my", `${last.y - r.top}px`);
      });
    };
    document.addEventListener("pointermove", on, { passive: true });
    return () => {
      document.removeEventListener("pointermove", on);
      cancelAnimationFrame(raf);
    };
  }, []);
}

/**
 * Marks the page as scrolling (html.is-scrolling) until 150 ms after the last scroll, so hover
 * effects pause while the page moves under a still mouse. The moment the mouse really moves (the
 * visitor is going to point at something) the flag clears, so a click right after scrolling, or
 * during trackpad momentum, is never swallowed.
 */
export function useScrollingFlag() {
  useEffect(() => {
    const root = document.documentElement;
    let t = 0;
    let x = -1;
    let y = -1;
    const stop = () => {
      clearTimeout(t);
      root.classList.remove("is-scrolling");
    };
    const on = () => {
      if (!root.classList.contains("is-scrolling")) root.classList.add("is-scrolling");
      clearTimeout(t);
      t = window.setTimeout(stop, 150);
    };
    // browsers may send a pointer event without movement after a scroll; only real movement counts
    const move = (e: PointerEvent) => {
      if (Math.abs(e.screenX - x) + Math.abs(e.screenY - y) > 2 && root.classList.contains("is-scrolling")) stop();
      x = e.screenX;
      y = e.screenY;
    };
    window.addEventListener("scroll", on, { passive: true });
    window.addEventListener("pointermove", move, { passive: true });
    return () => {
      window.removeEventListener("scroll", on);
      window.removeEventListener("pointermove", move);
      stop();
    };
  }, []);
}

/* ------------------------------------------------------------- numbers */

/** Counts from the previous value to the new one; starts when scrolled into view. */
export function Counter({ value, decimals = 0, prefix = "", suffix = "", duration = 1.4, className }: { value: number; decimals?: number; prefix?: string; suffix?: string; duration?: number; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "0px 0px -10% 0px" });
  const prev = useRef(0);
  const reduce = useMotionReduced();
  const fmt = (v: number) => `${prefix}${v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}${suffix}`;
  useEffect(() => {
    const el = ref.current;
    if (!el || !inView) return;
    if (reduce) {
      el.textContent = fmt(value);
      return;
    }
    const c = animate(prev.current, value, { duration, ease: EASE, onUpdate: (v) => (el.textContent = fmt(v)) });
    prev.current = value;
    return () => c.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, inView, reduce]);
  return (
    <span ref={ref} className={clsx("tabular", className)}>
      {fmt(reduce ? value : 0)}
    </span>
  );
}

/** A 0 to 1 score as a ring that draws itself in, with the number counting up. */
export function Ring({ value, size = 112, stroke = 6, label, sub, color, className }: { value: number | null; size?: number; stroke?: number; label?: ReactNode; sub?: ReactNode; color?: string; className?: string }) {
  const id = useId().replace(/:/g, "");
  const R = 50 - stroke;
  const C = 2 * Math.PI * R;
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true });
  const v = value ?? 0;
  return (
    <div ref={ref} className={clsx("relative shrink-0", className)} style={{ width: size, height: size }} role="img" aria-label={value === null ? "Measuring" : `Score ${v.toFixed(2)} out of 1`}>
      <svg viewBox="0 0 100 100" className="size-full -rotate-90" aria-hidden>
        <defs>
          <linearGradient id={`rg-${id}`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" style={{ stopColor: color ?? "var(--primary)" }} />
            <stop offset="1" style={{ stopColor: color ?? "var(--primary)" }} />
          </linearGradient>
        </defs>
        <circle cx="50" cy="50" r={R} fill="none" style={{ stroke: "var(--muted)" }} strokeWidth={stroke} />
        <motion.circle
          cx="50"
          cy="50"
          r={R}
          fill="none"
          stroke={`url(#rg-${id})`}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={C}
          initial={{ strokeDashoffset: C }}
          animate={{ strokeDashoffset: inView && value !== null ? C * (1 - v) : C }}
          transition={{ duration: 1.3, ease: EASE }}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-center">
        <div>
          {label && <div className="text-xs text-muted-foreground">{label}</div>}
          <div className="leading-none font-semibold tracking-tight text-foreground tabular" style={{ fontSize: size * 0.24 }}>
            {value === null ? "··" : <Counter value={v} decimals={2} duration={1.1} />}
          </div>
          {sub && <div className="mt-1 text-[10.5px] text-muted-foreground">{sub}</div>}
        </div>
      </div>
    </div>
  );
}

/** Horizontal bar that fills when it enters the view. */
export function Meter({ value, label, right, hint, tone = "var(--primary)", kind, className, thin }: { value: number | null; label?: ReactNode; right?: ReactNode; hint?: ReactNode; tone?: string; kind?: Kind; className?: string; thin?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true });
  return (
    <div ref={ref} className={className}>
      {(label || right) && (
        <div className="flex items-center justify-between gap-3 text-[13px]">
          <span className="flex items-center gap-2 text-foreground">
            {label}
            {kind && <KindBadge kind={kind} />}
          </span>
          <span className="font-mono text-foreground tabular">{right ?? (value === null ? "n/a" : value.toFixed(2))}</span>
        </div>
      )}
      <div data-slot="progress" className={clsx("relative overflow-hidden rounded-md bg-muted", thin ? "mt-1.5 h-1" : "mt-2 h-1.5")}>
        <motion.div
          className="absolute inset-y-0 left-0 rounded-md"
          style={{ background: tone }}
          initial={{ width: 0 }}
          animate={{ width: inView && value !== null ? `${Math.max(0, Math.min(1, value)) * 100}%` : 0 }}
          transition={{ duration: 1.1, ease: EASE }}
        />
      </div>
      {hint && <div className="mt-1.5 text-[11.5px] leading-snug text-muted-foreground">{hint}</div>}
    </div>
  );
}

/* ------------------------------------------------------------- labels */

export type Kind = "computed" | "model" | "demo" | "gap";

const KIND: Record<Kind, { label: string; cls: string; tip: string }> = {
  computed: { label: "Computed", cls: "border-strong/40 bg-strong/10 text-strong", tip: "Measured from real NASA and open data." },
  model: { label: "Learned", cls: "border-earth/40 bg-earth/10 text-earth", tip: "From our model trained on 38 known test sites." },
  demo: { label: "Demo", cls: "border-dashed border-border text-muted-foreground", tip: "A placeholder from published studies, not measured." },
  gap: { label: "Earth can't copy", cls: "border-weak/40 bg-weak/10 text-weak", tip: "Earth cannot reproduce this. Shown, never scored." },
};

export function KindBadge({ kind }: { kind: Kind }) {
  return (
    <Badge variant="outline" title={KIND[kind].tip} className={clsx("h-5 shrink-0 px-1.5 text-[10px] font-medium tracking-wide uppercase", KIND[kind].cls)}>
      {KIND[kind].label}
    </Badge>
  );
}

const CHIP = {
  default: "border-border bg-secondary text-secondary-foreground",
  flare: "border-primary/40 bg-primary/15 text-foreground",
  strong: "border-strong/35 bg-strong/10 text-strong",
  moon: "border-moon/35 bg-moon/10 text-moon",
  mars: "border-mars/35 bg-mars/10 text-mars",
  earth: "border-earth/35 bg-earth/10 text-earth",
  partial: "border-partial/35 bg-partial/10 text-partial",
  weak: "border-weak/35 bg-weak/10 text-weak",
};

export function Chip({ children, tone = "default", className }: { children: ReactNode; tone?: keyof typeof CHIP; className?: string }) {
  return (
    <Badge variant="outline" className={clsx("h-auto gap-1.5 px-2.5 py-1 text-[11.5px] leading-none font-medium", CHIP[tone], className)}>
      {children}
    </Badge>
  );
}

/** Small explanation bubble for any term that needs one. */
export function Tip({ text, children, className }: { text: string; children?: ReactNode; className?: string }) {
  return (
    <TooltipProvider delayDuration={120}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button type="button" aria-label={text} className={clsx("relative inline-flex items-center text-muted-foreground transition-colors hover:text-foreground", className)}>
            {children ?? <Info size={12} />}
          </button>
        </TooltipTrigger>
        <TooltipContent sideOffset={6} className="z-[900] max-w-[240px] text-left text-[11.5px] leading-snug font-normal tracking-normal normal-case">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/* ------------------------------------------------------------- controls */

export function Seg<T extends string>({ value, onChange, options, size = "md", className }: { value: T; onChange: (v: T) => void; options: { id: T; label: ReactNode; title?: string }[]; size?: "sm" | "md"; className?: string }) {
  return (
    <ToggleGroup type="single" variant="outline" size="sm" spacing={0} value={value} onValueChange={(v) => v && onChange(v as T)} className={className}>
      {options.map((o) => (
        <ToggleGroupItem key={o.id} value={o.id} title={o.title} className={clsx("gap-1.5", size === "sm" ? "h-7 px-2.5 text-[11.5px]" : "text-[12px]")}>
          {o.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

/** Nudges its child toward the pointer, a small springy pull. */
export function Magnetic({ children, strength = 0.25, className }: { children: ReactNode; strength?: number; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const sx = useSpring(x, { stiffness: 260, damping: 20, mass: 0.35 });
  const sy = useSpring(y, { stiffness: 260, damping: 20, mass: 0.35 });
  return (
    <motion.div
      ref={ref}
      style={{ x: sx, y: sy }}
      className={clsx("inline-block", className)}
      onPointerMove={(e) => {
        if (e.pointerType !== "mouse" || !ref.current) return;
        const r = ref.current.getBoundingClientRect();
        x.set((e.clientX - (r.left + r.width / 2)) * strength);
        y.set((e.clientY - (r.top + r.height / 2)) * strength);
      }}
      onPointerLeave={() => {
        x.set(0);
        y.set(0);
      }}
    >
      {children}
    </motion.div>
  );
}

/* ------------------------------------------------------------- text motion */

/** Cycles through words with a vertical mask, used in headings. */
export function WordCycler({ words, interval = 2600, className }: { words: string[]; interval?: number; className?: string }) {
  const [i, setI] = useState(0);
  const reduce = useMotionReduced();
  useEffect(() => {
    if (reduce) return;
    const t = setInterval(() => setI((n) => (n + 1) % words.length), interval);
    return () => clearInterval(t);
  }, [words.length, interval, reduce]);
  return (
    <span className={clsx("relative inline-flex overflow-hidden align-bottom", className)}>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span key={words[i]} initial={{ y: "100%", opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: "-100%", opacity: 0 }} transition={{ duration: 0.6, ease: EASE }} className="inline-block whitespace-nowrap">
          {words[i]}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}

/** Words rise out of a mask when the heading scrolls into view. */
export function RevealWords({ text, className, delay = 0 }: { text: string; className?: string; delay?: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "0px 0px -8% 0px" });
  const reduce = useMotionReduced();
  const words = text.split(" ");
  return (
    <span ref={ref} className={clsx("inline", className)}>
      <span className="sr-only">{text}</span>
      {words.map((w, i) => (
        <span key={i} className="inline-block overflow-hidden pb-[0.08em] align-bottom" aria-hidden>
          <motion.span className="inline-block" initial={reduce ? false : { y: "105%" }} animate={inView ? { y: 0 } : undefined} transition={{ duration: 0.9, ease: EASE, delay: delay + i * 0.035 }}>
            {w}
            {i < words.length - 1 ? " " : ""}
          </motion.span>
        </span>
      ))}
    </span>
  );
}

export function Reveal({ children, className, delay = 0, y = 22 }: { children: ReactNode; className?: string; delay?: number; y?: number }) {
  return (
    <motion.div className={className} initial={{ opacity: 0, y }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-60px" }} transition={{ duration: 0.9, ease: EASE, delay }}>
      {children}
    </motion.div>
  );
}

/** Big outlined chapter number with a kicker, used at the top of every section. */
export function Kicker({ n, children, className }: { n?: string; children: ReactNode; className?: string }) {
  return (
    <div className={clsx("flex items-center gap-4", className)}>
      {n && <Badge variant="outline" className="h-6 px-2 font-mono text-[11px] text-muted-foreground tabular">{n}</Badge>}
      <motion.span className="h-px w-10 origin-left bg-border" initial={{ scaleX: 0 }} whileInView={{ scaleX: 1 }} viewport={{ once: true }} transition={{ duration: 1.1, ease: EASE }} />
      <span className="text-xs font-medium text-muted-foreground">{children}</span>
    </div>
  );
}

/* ------------------------------------------------------------- toasts */

interface ToastMsg {
  id: number;
  text: string;
  tone: "ok" | "info";
}

export function toast(text: string, tone: "ok" | "info" = "ok") {
  window.dispatchEvent(new CustomEvent("sa-toast", { detail: { text, tone } }));
}

export function Toaster() {
  const [list, setList] = useState<ToastMsg[]>([]);
  useEffect(() => {
    let n = 0;
    const on = (e: Event) => {
      const d = (e as CustomEvent<{ text: string; tone: "ok" | "info" }>).detail;
      const id = ++n;
      setList((l) => [...l.slice(-2), { id, ...d }]);
      setTimeout(() => setList((l) => l.filter((t) => t.id !== id)), 3200);
    };
    window.addEventListener("sa-toast", on);
    return () => window.removeEventListener("sa-toast", on);
  }, []);
  return (
    <div className="pointer-events-none fixed bottom-6 left-1/2 z-[1200] flex -translate-x-1/2 flex-col items-center gap-2" aria-live="polite">
      <AnimatePresence>
        {list.map((t) => (
          <motion.div
            key={t.id}
            layout
            initial={{ opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ duration: 0.35, ease: EASE }}
            className="pointer-events-auto flex items-center gap-2 rounded-lg border border-border bg-popover px-4 py-2.5 text-[13px] text-popover-foreground shadow-md"
          >
            {t.tone === "ok" ? <Check size={14} className="text-strong" /> : <Info size={14} className="text-primary" />}
            {t.text}
            <Button variant="ghost" size="icon-xs" onClick={() => setList((l) => l.filter((x) => x.id !== t.id))} aria-label="Dismiss" className="ml-1 text-muted-foreground">
              <X size={12} />
            </Button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

/* ------------------------------------------------------------- back to top */

export function BackToTop() {
  const [p, setP] = useState(0);
  useEffect(() => {
    const on = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      setP(max > 0 ? window.scrollY / max : 0);
    };
    on();
    window.addEventListener("scroll", on, { passive: true });
    window.addEventListener("resize", on);
    return () => {
      window.removeEventListener("scroll", on);
      window.removeEventListener("resize", on);
    };
  }, []);
  const C = 2 * Math.PI * 19;
  return (
    <AnimatePresence>
      {p > 0.12 && (
        <motion.button
          initial={{ opacity: 0, scale: 0.8 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.8 }}
          transition={{ duration: 0.3, ease: EASE }}
          onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
          aria-label="Back to top"
          className="fixed right-5 bottom-24 z-[800] grid size-10 place-items-center rounded-full border border-border bg-popover text-popover-foreground shadow-md max-md:bottom-24"
        >
          <svg viewBox="0 0 44 44" className="absolute inset-0 -rotate-90" aria-hidden>
            <circle cx="22" cy="22" r="19" fill="none" style={{ stroke: "var(--primary)" }} strokeWidth="1.5" strokeDasharray={C} strokeDashoffset={C * (1 - p)} strokeLinecap="round" />
          </svg>
          <ArrowUp size={16} />
        </motion.button>
      )}
    </AnimatePresence>
  );
}

/* ------------------------------------------------------------- glyph tile */

/** Icon on a softly rotating orbit, used to give text cards a visual anchor. */
export function GlyphTile({ children, tone = "var(--color-flare)", className }: { children: ReactNode; tone?: string; className?: string }) {
  return (
    <span className={clsx("glyph-tile relative grid size-10 shrink-0 place-items-center rounded-lg border border-border bg-muted", className)} style={{ color: tone }}>
      <span className="relative">{children}</span>
    </span>
  );
}

/** How sure we are about one place: real (not gap-filled) measurements and model agreement. */
export function ConfidenceChip({ level, cover, spread, className }: { level: "high" | "medium" | "low"; cover: number; spread: number; className?: string }) {
  const text = level === "high" ? "High confidence" : level === "medium" ? "Medium confidence" : "Not enough evidence";
  const tone = level === "high" ? "strong" : level === "medium" ? "partial" : "weak";
  const why = `${Math.round(cover * 100)}% of the measurements here are real, not filled in. The learned model's 24 runs (3 models × 8 bags) agree to within ${Math.round(spread * 100)} points.`;
  return (
    <span title={why} className={className}>
      <Chip tone={tone}>
        <span className={clsx("size-1.5 rounded-full", level === "high" ? "bg-strong" : level === "medium" ? "bg-partial" : "bg-weak")} />
        {text}
      </Chip>
    </span>
  );
}
