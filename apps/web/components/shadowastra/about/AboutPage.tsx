// @ts-nocheck -- layout vendored from upstream src/pages/About.tsx; upstream strict:true, not Terraluna's extra flags.
'use client';

// Copied from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Layout and styling follow upstream src/pages/About.tsx; every line of content is Terraluna's own.
// No ShadowAstra team members, names or photos are shown.
import { useRef } from "react";
import Link from "next/link";
import { motion } from "motion/react";
import { ArrowRight, ArrowUpRight, Bot, FileText, Fingerprint, Radar, Sparkles } from "lucide-react";
import { Github } from "../atlas/ui/Chrome";
import { Astronaut } from "../chrome/Astronaut";
import { useSectionProgress } from "../../../lib/shadowastra-learn/useSectionProgress";
import "../shadowastra.css";

const EASE = [0.22, 1, 0.36, 1] as const;
const REPO_URL = "https://github.com/Julian-AT/nasa-space-apps-challange";
const SHADOWASTRA_URL = "https://github.com/ABRUBAB/ShadowAstra_Atlas";

// Placeholders until the member names are added. Initial avatars only, no photos.
const TEAM = [1, 2, 3, 4].map((n) => ({ id: `member-${n}`, name: "Team member — name to be added", role: "Placeholder" }));

const BUILT = [
  { icon: Fingerprint, t: "Physics terrain fingerprint", d: "Terrain roughness fingerprints ν(L) computed from public elevation data, comparing Earth windows with Moon and Mars targets at the same supported scales." },
  { icon: Radar, t: "Live probe", d: "Draw a probe on the map and the fingerprint is recomputed in a browser worker, with tested Python/TypeScript numerical parity." },
  { icon: Sparkles, t: "Experimental encoder", d: "A learned terrain encoder shown as a second opinion only. It stays experimental and outside every ranking until its promotion gate passes." },
  { icon: FileText, t: "The paper", d: "The write-up is generated from the same release bundle as this app, so its measurements match the pages." },
];

const CREDITS = [
  { a: "UI and factor model", f: "ShadowAstra Atlas (MIT, Team ShadowAstra), used with permission. Its pages, Astra and factor model are ported and labelled as theirs.", href: SHADOWASTRA_URL },
  { a: "NASA PGDA / LOLA", f: "Product 78 (LOLA-derived Site01 DEM) and LOLA polar products." },
  { a: "NASA PDS Mini-RF", f: "LRO Mini-RF radar products." },
  { a: "HiRISE / CTX via USGS", f: "USGS/ASU CTX and HiRISE digital terrain models." },
  { a: "Copernicus GLO-30 DEM (ESA)", f: "© DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the European Union and ESA." },
  { a: "ESA WorldCover", f: "10 m 2021 v200 (CC BY 4.0)." },
  { a: "NASA GIBS", f: "Global Imagery Browse Services basemaps." },
  { a: "NASA Moon / Mars Trek", f: "Moon and Mars Trek map services." },
  { a: "JPL UAVSAR", f: "NASA/JPL UAVSAR (Iceland scene Icelnd_00208_15083_004_150530_L090_CX_01)." },
  { a: "Voigt & Hamilton 2021", f: "Holuhraun facies map (CC BY-NC), used for the G3 reproduction; not redistributed here." },
];

function Words({ text, className, delay = 0 }: { text: string; className?: string; delay?: number }) {
  return (
    <span className={className}>
      {text.split(" ").map((w, i) => (
        <span key={i} className="inline-block overflow-hidden pb-[0.08em] align-bottom">
          <motion.span className="inline-block" initial={{ y: "105%" }} animate={{ y: 0 }} transition={{ duration: 1.2, ease: EASE, delay: delay + i * 0.06 }}>
            {w}&nbsp;
          </motion.span>
        </span>
      ))}
    </span>
  );
}

function Rule() {
  return <motion.span className="block h-px w-10 origin-left bg-line-2" initial={{ scaleX: 0 }} whileInView={{ scaleX: 1 }} viewport={{ once: true }} transition={{ duration: 1.2, ease: EASE }} />;
}

function Kicker({ n, children }: { n: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4">
      <span className="font-mono text-[11px] text-flare">{n}</span>
      <Rule />
      <span className="font-mono text-[11px] tracking-[0.24em] text-chrome uppercase">{children}</span>
    </div>
  );
}

export default function AboutPage() {
  return (
    <div className="sa-ui bg-void">
      <Hero />
      <Crew />
      <Built />
      <Credits />
      <Disclosure />
      <section className="relative overflow-hidden border-t border-line px-4 py-32 sm:px-6">
        <div className="relative mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-8">
          <motion.h2 initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ duration: 1, ease: EASE }} className="text-3xl sm:text-4xl leading-[1.02] font-medium tracking-[-0.04em]">
            Read the method <span className="font-serif font-normal text-flare italic">in full.</span>
          </motion.h2>
          <div className="flex flex-wrap gap-3">
            <Link href="/paper" className="btn-primary inline-flex items-center gap-2 rounded-full px-6 py-3.5 text-sm font-medium no-underline">
              Read the paper <ArrowRight size={16} />
            </Link>
            <a href="/paper/terraluna-paper.pdf" className="inline-flex items-center gap-2 rounded-full border border-line-2 px-6 py-3.5 text-sm no-underline hover:border-chrome">
              <FileText size={16} /> PDF
            </a>
            <a href={REPO_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 rounded-full border border-line-2 px-6 py-3.5 text-sm no-underline hover:border-chrome">
              <Github size={16} /> GitHub <ArrowUpRight size={14} />
            </a>
          </div>
        </div>
      </section>
    </div>
  );
}

/* --------------------------------------------------------------------- hero */

function Hero() {
  const ref = useRef<HTMLElement>(null);
  return (
    <section ref={ref} className="relative overflow-hidden border-b border-line">
      <div className="relative mx-auto max-w-[1400px] px-4 pt-28 pb-24 sm:px-6 lg:pt-36 lg:pb-32">
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1 }} className="flex items-center gap-4 font-mono text-[11px] tracking-[0.24em] text-chrome uppercase">
          <span className="h-px w-8 bg-chrome/50" /> About Terraluna
        </motion.div>
        <h1 className="mt-8 max-w-[900px] text-4xl sm:text-5xl leading-[0.98] font-medium tracking-[-0.045em]">
          <Words text="Team Terraluna." />
          <br />
          <Words text="Earth terrain, measured against the Moon and Mars." className="text-muted-foreground" delay={0.3} />
        </h1>
        <motion.p initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 1.1, ease: EASE, delay: 0.7 }} className="mt-8 max-w-[600px] text-[17px] leading-relaxed text-chrome">
          Terraluna is a NASA Space Apps 2026 project. It compares Earth terrain windows with Moon and Mars targets using roughness fingerprints from public elevation data, and labels every value with its evidence and claim limits.
        </motion.p>
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1, delay: 1.1 }} className="mt-12">
          <Astronaut size={86} mood="happy" action="wave" say="Astra is on loan from ShadowAstra Atlas." sayAlign="side" />
        </motion.div>
      </div>
      <style>{`@keyframes sweep{to{transform:rotate(360deg)}}`}</style>
    </section>
  );
}

/* --------------------------------------------------------------------- crew */

function Crew() {
  return (
    <section className="border-b border-line px-4 py-28 sm:px-6">
      <div className="mx-auto max-w-[1400px]">
        <Kicker n="01">Team Terraluna</Kicker>
        <motion.h2 initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ duration: 1, ease: EASE }} className="mt-6 text-3xl sm:text-4xl leading-[1.03] font-medium tracking-[-0.038em]">
          The crew, <span className="font-serif font-normal text-chrome italic">names to be added.</span>
        </motion.h2>
        <p className="mt-5 max-w-xl text-[15px] leading-relaxed text-chrome">These cards are placeholders. The member names and roles will be filled in by the team.</p>
        <div className="mt-14 grid gap-x-5 gap-y-12 sm:grid-cols-2 lg:grid-cols-4">
          {TEAM.map((m, k) => (
            <motion.article key={m.id} initial="hidden" whileInView="show" viewport={{ once: true, margin: "-60px" }} className="group" transition={{ staggerChildren: 0.1, delayChildren: (k % 4) * 0.1 }}>
              <div className="relative aspect-[4/5] overflow-hidden rounded-2xl border border-dashed border-line-2 bg-panel">
                <motion.div className="absolute inset-0 grid place-items-center" variants={{ hidden: { clipPath: "inset(100% 0 0 0)" }, show: { clipPath: "inset(0% 0 0 0)" } }} transition={{ duration: 1.3, ease: EASE }}>
                  <span aria-hidden="true" className="grid size-28 place-items-center rounded-full border border-line-2 bg-ink font-serif text-[56px] text-dim italic transition-colors duration-700 group-hover:text-flare">
                    T
                  </span>
                </motion.div>
                <span className="absolute top-4 left-4 font-mono text-[11px] text-dim">{String(k + 1).padStart(2, "0")}</span>
                <span className="absolute right-4 bottom-4 rounded-full border border-line-2 px-2.5 py-1 font-mono text-[10px] tracking-[0.18em] text-chrome uppercase">Placeholder</span>
              </div>
              <motion.div variants={{ hidden: { opacity: 0, y: 12 }, show: { opacity: 1, y: 0 } }} transition={{ duration: 0.9, ease: EASE }} className="mt-4">
                <h3 className="text-[18px] font-medium tracking-tight">{m.name}</h3>
                <span className="mt-3 block h-px w-full origin-left scale-x-[0.15] bg-line-2 transition-transform duration-700 ease-[cubic-bezier(.22,1,.36,1)] group-hover:scale-x-100 group-hover:bg-flare/60" />
              </motion.div>
            </motion.article>
          ))}
        </div>
      </div>
    </section>
  );
}

/* -------------------------------------------------------------------- built */

function Built() {
  return (
    <section className="border-b border-line bg-ink px-4 py-28 sm:px-6">
      <div className="mx-auto max-w-[1400px]">
        <Kicker n="02">What we built</Kicker>
        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {BUILT.map((v, i) => (
            <motion.div key={v.t} initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ duration: 0.9, ease: EASE, delay: i * 0.08 }} whileHover={{ y: -4 }} className="surface lift rounded-2xl p-7">
              <v.icon size={20} strokeWidth={1.5} className="text-flare" />
              <h3 className="mt-10 text-[18px] font-medium tracking-tight">{v.t}</h3>
              <p className="mt-2 text-[14px] leading-relaxed text-chrome">{v.d}</p>
            </motion.div>
          ))}
        </div>
        <div className="mt-8 flex flex-wrap gap-x-6 gap-y-2 text-[14px]">
          <Link href="/paper" className="link-wipe text-chrome hover:text-platinum">Paper</Link>
          <a href="/paper/terraluna-paper.pdf" className="link-wipe text-chrome hover:text-platinum">Paper (PDF)</a>
          <a href={REPO_URL} target="_blank" rel="noreferrer" className="link-wipe text-chrome hover:text-platinum">GitHub repository</a>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ credits */

function Credits() {
  return (
    <section className="border-b border-line px-4 py-28 sm:px-6">
      <div className="mx-auto grid max-w-[1400px] gap-14 lg:grid-cols-[0.75fr_1.25fr]">
        <div className="lg:sticky lg:top-28 lg:self-start">
          <Kicker n="03">Credits</Kicker>
          <motion.h2 initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ duration: 1, ease: EASE }} className="mt-6 text-2xl sm:text-3xl leading-[1.04] font-medium tracking-[-0.035em]">
            Built on open work, <span className="font-serif font-normal text-chrome italic">credited by name.</span>
          </motion.h2>
          <p className="mt-5 max-w-md text-[15px] leading-relaxed text-chrome">
            Full source records with licences and checksums are on <Link href="/data" className="underline">/data</Link> and in each bundle&apos;s sources.json.
          </p>
          <div className="mt-10 hidden lg:block">
            <Astronaut size={110} mood="happy" action="flag" />
          </div>
        </div>
        <ul className="border-t border-line">
          {CREDITS.map((x, k) => (
            <motion.li key={x.a} initial={{ opacity: 0, y: 16 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-40px" }} transition={{ duration: 0.8, ease: EASE, delay: (k % 5) * 0.04 }} className="group relative grid gap-2 border-b border-line py-6 pl-6 transition-colors duration-500 hover:bg-panel sm:grid-cols-[260px_1fr]">
              <span className="absolute top-0 bottom-0 left-0 w-px origin-top scale-y-0 bg-flare transition-transform duration-500 ease-[cubic-bezier(.22,1,.36,1)] group-hover:scale-y-100" />
              <span className="flex items-baseline gap-3 text-[17px] text-platinum">
                <span className="font-mono text-[11px] text-dim transition-colors group-hover:text-flare">{String(k + 1).padStart(2, "0")}</span>
                {x.a}
              </span>
              <span className="pr-4 text-[14px] leading-relaxed text-chrome transition-colors group-hover:text-platinum">
                {x.f}
                {x.href ? (
                  <>
                    {" "}
                    <a href={x.href} target="_blank" rel="noreferrer" className="underline">
                      {x.href.replace("https://", "")}
                    </a>
                  </>
                ) : null}
              </span>
            </motion.li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/* --------------------------------------------------------------- disclosure */

function Disclosure() {
  return (
    <section className="bg-ink px-4 py-28 sm:px-6">
      <div className="mx-auto max-w-[1400px]">
        <Kicker n="04">AI disclosure</Kicker>
        <motion.div initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ duration: 0.9, ease: EASE }} className="surface mt-12 flex max-w-3xl gap-5 rounded-2xl p-7">
          <Bot size={22} strokeWidth={1.5} className="mt-1 shrink-0 text-flare" />
          <p className="text-[16px] leading-relaxed text-chrome">
            Terraluna was built with Claude Code (Anthropic) and Codex (OpenAI), used extensively for code, analysis scripts and writing under human direction. No AI-generated images are used; all figures are drawn from data.
          </p>
        </motion.div>
      </div>
    </section>
  );
}
