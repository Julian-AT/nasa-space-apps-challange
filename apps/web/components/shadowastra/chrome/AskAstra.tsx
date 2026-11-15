'use client';
// @ts-nocheck
// Copied from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import clsx from "clsx";
import { ArrowRight, Send, X } from "lucide-react";
import { AiChat02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { astraAnswerAsync, pageGreeting, type AstraContext, type AstraReply } from "@/lib/shadowastra-chrome/astra";
import { logBadge } from "@/lib/shadowastra/fieldlog";

const EASE = [0.22, 1, 0.36, 1] as const;

interface Msg {
  from: "astra" | "you";
  reply?: AstraReply;
  text?: string;
}

/** Floating "Ask Luna" assistant, available on every page. */
export function AskAstra({ ctx }: { ctx: AstraContext }) {
  const pathname = usePathname() ?? "/";
  const router = useRouter();
  const navigate = (to: string) => router.push(to);
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [typing, setTyping] = useState(false);
  const [nudge, setNudge] = useState(false);
  const list = useRef<HTMLDivElement>(null);

  // A fresh, page-aware greeting each time the page changes.
  useEffect(() => {
    setMsgs([{ from: "astra", reply: pageGreeting(pathname, ctx) }]);
  }, [pathname]);

  // Gentle nudge on a first visit only, and never over the full-screen tools, so people notice Luna can talk.
  useEffect(() => {
    if (pathname.startsWith("/explore") || pathname.startsWith("/compare") || pathname.startsWith("/probe")) return;
    try {
      if (localStorage.getItem("sa-nudged")) return;
    } catch {
      /* storage blocked: still nudge once per load */
    }
    const t = setTimeout(() => {
      setNudge(true);
      try {
        localStorage.setItem("sa-nudged", "1");
      } catch {
        /* ignore */
      }
    }, 9000);
    const t2 = setTimeout(() => setNudge(false), 17000);
    return () => {
      clearTimeout(t);
      clearTimeout(t2);
      setNudge(false);
    };
  }, [pathname]);

  // Other parts of the site can open Luna with a question: askAstra("…")
  useEffect(() => {
    const on = (e: Event) => {
      const q = (e as CustomEvent<{ q: string }>).detail?.q ?? "";
      setOpen(true);
      setNudge(false);
      if (q) setTimeout(() => askRef.current(q), 250);
    };
    window.addEventListener("sa-ask", on);
    return () => window.removeEventListener("sa-ask", on);
  }, []);

  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight, behavior: "smooth" });
  }, [msgs, typing]);

  const ask = (q: string) => {
    const text = q.trim();
    if (!text) return;
    setMsgs((m) => [...m, { from: "you", text }]);
    setInput("");
    logBadge("ask");
    setTyping(true);
    const wait = new Promise((r) => setTimeout(r, 550 + Math.min(900, text.length * 12)));
    Promise.all([astraAnswerAsync(text, ctx), wait]).then(([reply]) => {
      setMsgs((m) => [...m, { from: "astra", reply }]);
      setTyping(false);
    });
  };

  const askRef = useRef(ask);
  askRef.current = ask;

  return (
    <>
      <AnimatePresence>
        {!open && (
          <motion.div
            key="fab"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 20 }}
            transition={{ duration: 0.5, ease: EASE, delay: 0.4 }}
            className="fixed right-4 bottom-4 z-[900] sm:right-6 sm:bottom-6"
          >
            <Button
              size="lg"
              className="relative rounded-full shadow-lg"
              onClick={() => {
                setOpen(true);
                setNudge(false);
              }}
              aria-label="Ask Luna"
            >
              <HugeiconsIcon icon={AiChat02Icon} strokeWidth={2} />
              Ask Luna
              <AnimatePresence>
                {nudge && (
                  <motion.span
                    initial={{ opacity: 0, x: 8 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0 }}
                    className="pointer-events-none absolute right-full mr-3 w-max max-w-[220px] rounded-md border bg-popover px-3 py-2 text-left text-xs font-normal text-popover-foreground"
                  >
                    Ask me what Terraluna is or where the cards are.
                  </motion.span>
                )}
              </AnimatePresence>
            </Button>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {open && (
          <motion.section
            key="panel"
            role="dialog"
            aria-label="Ask Luna"
            initial={{ opacity: 0, y: 24, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.97 }}
            transition={{ duration: 0.4, ease: EASE }}
            style={{ transformOrigin: "bottom right" }}
            className="fixed right-3 bottom-3 z-[900] h-[min(620px,calc(100dvh-24px))] w-[min(400px,calc(100vw-24px))] sm:right-6 sm:bottom-6"
          >
            <Card className="flex h-full flex-col gap-0 overflow-hidden py-0 shadow-2xl">
            <header className="flex items-center gap-3 border-b px-4 py-3">
              <span aria-hidden="true" className="inline-flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
                <HugeiconsIcon icon={AiChat02Icon} strokeWidth={2} className="size-4" />
              </span>
              <div className="flex-1">
                <div className="text-sm font-semibold">Luna</div>
                <div className="text-xs text-muted-foreground">Answers from the release bundle</div>
              </div>
              <Button variant="ghost" size="icon" onClick={() => setOpen(false)} aria-label="Close">
                <X size={14} />
              </Button>
            </header>

            <div ref={list} className="scrollbar-thin flex-1 space-y-3 overflow-y-auto px-4 py-4">
              {msgs.map((m, i) =>
                m.from === "you" ? (
                  <motion.div key={i} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-platinum px-3.5 py-2 text-[13px] text-void">
                    {m.text}
                  </motion.div>
                ) : (
                  <motion.div key={i} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: EASE }} className="max-w-[92%]">
                    <div className="rounded-2xl rounded-bl-sm border border-line bg-ink px-3.5 py-2.5 text-[13px] leading-relaxed text-platinum">{m.reply!.text}</div>
                    {m.reply!.links && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {m.reply!.links.map((l) => (
                          <button
                            key={l.to + l.label}
                            onClick={() => {
                              navigate(l.to);
                              setOpen(false);
                            }}
                            className="inline-flex items-center gap-1 rounded-full border border-flare/50 px-2.5 py-1 text-[11.5px] text-flare transition-colors hover:bg-flare/10"
                          >
                            {l.label} <ArrowRight size={11} />
                          </button>
                        ))}
                      </div>
                    )}
                    {i === msgs.length - 1 && m.reply!.suggestions && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {m.reply!.suggestions.map((s) => (
                          <button key={s} onClick={() => ask(s)} className="rounded-full border border-line-2 px-2.5 py-1 text-[11.5px] text-chrome transition-colors hover:border-chrome hover:text-platinum">
                            {s}
                          </button>
                        ))}
                      </div>
                    )}
                  </motion.div>
                ),
              )}
              {typing && (
                <div className="flex w-fit gap-1 rounded-2xl rounded-bl-sm border border-line bg-ink px-3.5 py-3">
                  {[0, 1, 2].map((k) => (
                    <motion.span key={k} className="size-1.5 rounded-full bg-chrome" animate={{ opacity: [0.3, 1, 0.3] }} transition={{ duration: 1, repeat: Infinity, delay: k * 0.15 }} />
                  ))}
                </div>
              )}
            </div>

            <form
              onSubmit={(e) => {
                e.preventDefault();
                ask(input);
              }}
              className="flex items-center gap-2 border-t border-line p-3"
            >
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask about Terraluna, the model or the cards…"
                className="min-w-0 flex-1 rounded-full border border-line-2 bg-ink px-4 py-2.5 text-[13px] text-platinum outline-none placeholder:text-dim focus:border-chrome"
                aria-label="Your question"
              />
              <button type="submit" disabled={!input.trim()} className={clsx("grid size-10 place-items-center rounded-full transition-colors", input.trim() ? "bg-platinum text-void" : "bg-line text-dim")} aria-label="Send">
                <Send size={15} />
              </button>
            </form>
            <p className="px-4 pb-3 text-[10.5px] text-muted-foreground">Terraluna answers quote the release bundle; other answers come from ShadowAstra's open catalogue (MIT).</p>
            </Card>
          </motion.section>
        )}
      </AnimatePresence>
    </>
  );
}
