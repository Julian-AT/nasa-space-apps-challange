'use client';
// @ts-nocheck
// Copied from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { ArrowRight, Compass, CornerDownLeft, FileText, MapPin, MessageCircle, Moon, Search, Sun } from "lucide-react";
import { WorldDot } from "@/components/shadowastra/ui/Chrome";
import { loadGrid, searchGrid, type Grid } from "@/lib/shadowastra/grid";
import { EASE } from "@/components/shadowastra/ui/kit";
import { logBadge } from "@/lib/shadowastra/fieldlog";

/** One evidence card from the release bundle (server-passed). */
export interface PaletteCard {
  id: string;
  title: string;
  body: string;
}

/** Terraluna routes. */
export const ROUTES = [
  { to: "/", label: "Home" },
  { to: "/site", label: "Cards" },
  { to: "/compare", label: "Compare" },
  { to: "/find", label: "Find" },
  { to: "/explore/earth", label: "Maps: Earth" },
  { to: "/explore/moon", label: "Maps: Moon" },
  { to: "/explore/mars", label: "Maps: Mars" },
  { to: "/lab", label: "Lab" },
  { to: "/report", label: "Report" },
  { to: "/learn", label: "Learn" },
  { to: "/model", label: "Model" },
  { to: "/methods", label: "Methods" },
  { to: "/paper", label: "Paper" },
  { to: "/paper/terraluna-paper.pdf", label: "Paper PDF", hard: true },
  { to: "/api/docs", label: "API docs", hard: true },
] as const;

interface Item {
  id: string;
  group: string;
  label: string;
  sub?: string;
  icon: React.ReactNode;
  run: () => void;
  keywords?: string;
}

export function openPalette() {
  window.dispatchEvent(new Event("sa-palette"));
}

export function askAstra(q: string) {
  window.dispatchEvent(new CustomEvent("sa-ask", { detail: { q } }));
}

const norm = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();

/** Search everything: pages, actions, Moon and Mars targets, known sites and any place on Earth. */
export function CommandPalette({ cards }: { cards: readonly PaletteCard[] }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const [grid, setGrid] = useState<Grid | null>(null);
  const router = useRouter();
  const navigate = (to: string) => router.push(to);
  const input = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = /input|textarea|select/i.test((e.target as HTMLElement)?.tagName ?? "") || (e.target as HTMLElement)?.isContentEditable;
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((o) => !o);
        logBadge("search");
      } else if (e.key === "/" && !typing) {
        e.preventDefault();
        setOpen(true);
        logBadge("search");
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("sa-palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("sa-palette", onOpen);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    setQ("");
    setI(0);
    setTimeout(() => input.current?.focus(), 30);
    loadGrid().then(setGrid).catch(() => {});
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  const go = (to: string) => () => navigate(to);
  const hard = (to: string) => () => window.location.assign(to);
  const base: Item[] = useMemo(() => {
    const theme = typeof document !== "undefined" && document.documentElement.dataset.theme === "light" ? "dark" : "light";
    return [
      ...ROUTES.map((l) => ({ id: `p${l.to}`, group: "Pages", label: l.label, sub: l.to, icon: <Compass size={15} />, run: "hard" in l ? hard(l.to) : go(l.to) })),
      {
        id: "a-theme",
        group: "Actions",
        label: `Switch to ${theme} mode`,
        icon: theme === "light" ? <Sun size={15} /> : <Moon size={15} />,
        keywords: "theme dark light colour",
        run: () => {
          document.documentElement.dataset.theme = theme;
          try {
            localStorage.setItem("sa-theme", theme);
          } catch {
            /* private mode */
          }
          window.dispatchEvent(new Event("sa-theme"));
        },
      },
      { id: "a-ask", group: "Actions", label: "Ask Astra a question", icon: <MessageCircle size={15} />, keywords: "help chat assistant", run: () => askAstra("") },
      ...cards.map((c) => ({
        id: `c${c.id}`,
        group: "Evidence cards",
        label: c.title,
        sub: c.id,
        icon: c.body === "moon" || c.body === "mars" ? <WorldDot world={c.body} /> : <FileText size={15} />,
        keywords: `${c.body} card`,
        run: go(`/site/${encodeURIComponent(c.id)}`),
      })),
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const items = useMemo(() => {
    const n = norm(q.trim());
    const matched = n ? base.filter((it) => norm(`${it.label} ${it.sub ?? ""} ${it.keywords ?? ""}`).includes(n)) : base.filter((it) => it.group === "Pages" || it.group === "Actions" || it.group === "Evidence cards");
    const places: Item[] =
      grid && n.length >= 2
        ? searchGrid(grid, q, 6)
            .filter((h) => h.kind !== "known")
            .map((h, k) => ({
              id: `g${k}${h.label}`,
              group: "Check a place on Earth",
              label: h.label,
              sub: h.sub,
              icon: <MapPin size={15} />,
              run: () => {
                if (h.lat !== undefined && h.lon !== undefined) navigate(`/find?target=jezero&task=traverse&at=${h.lat.toFixed(2)},${h.lon.toFixed(2)}`);
                else if (h.field !== undefined && h.value !== undefined) {
                  const k2 = bestIn(grid, h.field, h.value);
                  if (k2 >= 0) navigate(`/find?target=jezero&task=traverse&at=${grid.lat(k2).toFixed(2)},${grid.lon(k2).toFixed(2)}`);
                }
              },
            }))
        : [];
    return [...places, ...matched].slice(0, 40);
  }, [q, base, grid, navigate]);

  useEffect(() => {
    listRef.current?.querySelector(`[data-i="${i}"]`)?.scrollIntoView({ block: "nearest" });
  }, [i]);

  const run = (it: Item) => {
    setOpen(false);
    it.run();
  };

  let lastGroup = "";
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="fixed inset-0 z-[1100] flex items-start justify-center bg-void/60 px-4 pt-[12vh] backdrop-blur-sm" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={() => setOpen(false)}>
          <motion.div
            role="dialog"
            aria-label="Search everything"
            className="glass w-full max-w-[620px] overflow-hidden rounded-2xl"
            initial={{ opacity: 0, y: -12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.98 }}
            transition={{ duration: 0.28, ease: EASE }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3 border-b border-line px-4 py-3.5">
              <Search size={16} className="text-dim" />
              <input
                ref={input}
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setI(0);
                }}
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setI((v) => Math.min(items.length - 1, v + 1));
                  } else if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setI((v) => Math.max(0, v - 1));
                  } else if (e.key === "Enter" && items[i]) run(items[i]);
                  else if (e.key === "Escape") setOpen(false);
                }}
                placeholder="Search pages, evidence cards, or any town on Earth…"
                aria-label="Search"
                className="min-w-0 flex-1 bg-transparent text-[15px] text-platinum outline-none placeholder:text-dim"
              />
              <kbd className="rounded border border-line-2 px-1.5 py-0.5 font-mono text-[10px] text-dim">esc</kbd>
            </div>
            <div ref={listRef} className="scrollbar-thin max-h-[56vh] overflow-y-auto p-2">
              {items.length === 0 && <div className="px-3 py-8 text-center text-[13px] text-dim">Nothing found. Try “Jezero”, “paper”, “Iceland” or “model”.</div>}
              {items.map((it, k) => {
                const head = it.group !== lastGroup;
                lastGroup = it.group;
                return (
                  <div key={it.id}>
                    {head && <div className="px-3 pt-3 pb-1.5 font-mono text-[10px] tracking-[0.2em] text-dim uppercase">{it.group}</div>}
                    <button
                      data-i={k}
                      onClick={() => run(it)}
                      onMouseMove={() => setI(k)}
                      className={clsx("flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors", k === i ? "bg-platinum/[0.08]" : "")}
                    >
                      <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-line bg-ink text-chrome">{it.icon}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[13.5px] text-platinum">{it.label}</span>
                        {it.sub && <span className="block truncate text-[11.5px] text-dim">{it.sub}</span>}
                      </span>
                      {k === i ? <CornerDownLeft size={14} className="text-flare" /> : <ArrowRight size={13} className="text-dim" />}
                    </button>
                  </div>
                );
              })}
            </div>
            <div className="flex items-center gap-4 border-t border-line px-4 py-2.5 font-mono text-[10px] text-dim">
              <span>↑ ↓ move</span>
              <span>↵ open</span>
              <span className="ml-auto">Ctrl K or / to open anywhere</span>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function bestIn(grid: Grid, field: "n_country" | "n_region", value: number) {
  // without a mission in mind, pick the most Mars-like spot inside the area
  const a = grid.arr(field);
  let best = -1;
  let bv = -1;
  for (let k = 0; k < grid.n; k++) {
    if (a[k] !== value) continue;
    const v = grid.model("mars", k);
    if (v > bv) {
      bv = v;
      best = k;
    }
  }
  return best;
}
