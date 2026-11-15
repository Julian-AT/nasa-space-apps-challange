// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Primary navigation on every page (PRD 8.6, 8.10). The app's only shell client module: a root
// layout cannot read the pathname, so this reads it with usePathname to mark the current route with
// aria-current="page", and holds the open state of the compact menu. It holds the links only: no
// bundle data and no provider. Links are plain anchors in DOM order and keep the global focus
// indicator. The ten links are visually grouped in one hairline pill (home | worlds | tools |
// reference); the current route is a filled pill with bold text, never colour alone. Below the lg
// breakpoint the pill collapses behind a menu button (aria-expanded) and opens as a panel.
'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';

import { openPalette } from '@/components/shadowastra/chrome/CommandPalette';
import { Github, ThemeToggle } from '@/components/shadowastra/ui/Chrome';
import { cn } from '@/lib/utils';

export const REPO_URL = 'https://github.com/Julian-AT/nasa-space-apps-challange';

export const NAV_ITEMS = [
  { label: 'Home', href: '/' },
  { label: 'Cards', href: '/site' },
  { label: 'Compare', href: '/compare' },
  { label: 'Lab', href: '/lab' },
  { label: 'Find', href: '/find' },
  { label: 'Maps', href: '/explore/earth' },
  { label: 'Model', href: '/model' },
  { label: 'Methods', href: '/methods' },
  { label: 'Paper', href: '/paper' },
  { label: 'API docs', href: '/api/docs' },
] as const;

/** Index in NAV_ITEMS after which a group divider is drawn: Home | Cards Compare Lab Find Maps | Model Methods Paper | API docs. */
const GROUP_ENDS: ReadonlySet<number> = new Set([0, 5, 8]);

const LINK =
  'block rounded-full px-3.5 py-2 text-[13px] whitespace-nowrap no-underline transition-colors duration-200 lg:inline-block lg:py-1.5';

/** The navigation for a given pathname; a link is current only on its own route. */
export function SiteNav({ pathname, className }: Readonly<{ pathname: string | null; className?: string }>) {
  return (
    <nav aria-label="Primary" className={cn('min-w-0', className)}>
      <ul className="flex flex-col gap-0.5 lg:flex-row lg:items-center lg:gap-0.5 lg:rounded-full lg:border lg:border-line-2 lg:bg-panel lg:p-1">
        {NAV_ITEMS.map(({ label, href }, index) => {
          const section = href === '/' ? '/' : '/' + href.split('/')[1];
          const isCurrent = href === '/' ? pathname === '/' : pathname === section || (pathname ?? "").startsWith(`${section}/`);
          const className = cn(
            LINK,
            isCurrent
              ? 'bg-platinum/10 font-bold text-foreground lg:bg-platinum lg:text-void'
              : 'text-muted-foreground hover:bg-platinum/5 hover:text-foreground',
          );
          return (
            <li key={href} className="contents">
              {href === '/api/docs' ? (
                <a href={href} aria-current={isCurrent ? 'page' : undefined} className={className}>
                  {label}
                </a>
              ) : (
                <Link href={href} aria-current={isCurrent ? 'page' : undefined} className={className}>
                  {label}
                </Link>
              )}
              {GROUP_ENDS.has(index) ? <span aria-hidden="true" className="mx-1 hidden h-4 w-px bg-line-2 lg:block" /> : null}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function Wordmark() {
  return (
    <Link
      href="/"
      className="flex shrink-0 items-center gap-2.5 font-wide text-[13px] tracking-[0.18em] text-platinum uppercase no-underline"
    >
      <svg aria-hidden="true" viewBox="0 0 24 24" width="24" height="24" className="text-flare">
        <circle cx="12" cy="12" r="5.5" fill="currentColor" />
        <ellipse cx="12" cy="12" rx="11" ry="4" fill="none" stroke="currentColor" strokeWidth="1.2" transform="rotate(-25 12 12)" />
      </svg>
      <span>
        Terra<span className="text-earth">luna</span>
      </span>
    </Link>
  );
}

/** ShadowAstra's search pill: opens the command palette (also Ctrl K or /). */
function SearchPill() {
  return (
    <button
      type="button"
      onClick={openPalette}
      aria-label="Search (Ctrl K)"
      aria-keyshortcuts="Control+K Meta+K /"
      className="inline-flex h-9 items-center gap-2 rounded-full border border-line-2 bg-panel pr-1.5 pl-3 text-[12.5px] text-chrome transition-colors hover:border-chrome hover:text-platinum"
    >
      <svg aria-hidden="true" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-3.5-3.5" />
      </svg>
      <span className="hidden sm:inline">Search</span>
      <kbd className="hidden rounded-full border border-line-2 px-2 py-0.5 font-mono text-[10px] text-dim sm:inline">Ctrl K</kbd>
    </button>
  );
}

function HeaderActions() {
  return (
    <div className="flex shrink-0 items-center gap-2">
      <SearchPill />
      <a
        href={REPO_URL}
        target="_blank"
        rel="noreferrer"
        aria-label="Terraluna on GitHub"
        title="GitHub"
        className="grid size-9 place-items-center rounded-full border border-line-2 text-chrome no-underline transition-colors hover:border-chrome hover:text-platinum"
      >
        <Github size={15} />
      </a>
      <ThemeToggle />
    </div>
  );
}

export function SiteHeader() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  return (
    <div className="mx-auto flex w-full max-w-[1400px] items-center justify-between gap-4 px-4 py-3 sm:px-6">
      <Wordmark />
      <SiteNav pathname={pathname} className="hidden lg:block" />
      <div className="flex items-center gap-2">
      <HeaderActions />
      <button
        type="button"
        aria-label={open ? 'Close menu' : 'Open menu'}
        aria-expanded={open}
        aria-controls="site-menu"
        onClick={() => {
          setOpen((value) => !value);
        }}
        className="inline-flex size-10 items-center justify-center rounded-full border border-line-2 text-chrome transition-colors hover:text-foreground lg:hidden"
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          {open ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 8h16M4 16h16" />}
        </svg>
      </button>
      </div>
      {open ? (
        <div
          id="site-menu"
          onClick={() => {
            setOpen(false);
          }}
          className="absolute inset-x-0 top-full border-b border-line bg-void px-4 pt-2 pb-4 sm:px-6 lg:hidden"
        >
          <SiteNav pathname={pathname} />
        </div>
      ) : null}
    </div>
  );
}
