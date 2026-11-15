'use client';

// Adapted from ShadowAstra_Atlas (MIT, (c) 2026 Team ShadowAstra), https://github.com/ABRUBAB/ShadowAstra_Atlas
// Primary navigation on every page (PRD 8.6, 8.10). The app's only shell client module: a root
// layout cannot read the pathname, so this reads it with usePathname to mark the current route with
// aria-current="page". It holds the links only: no bundle data and no provider. Links are plain
// anchors in DOM order and keep the global focus indicator. The current route is a filled pill
// with bold text, never colour alone. On narrow screens the row wraps onto a second line instead of hiding
// behind a toggle, so every link stays visible and reachable by keyboard. The wordmark and nav are flex items
// of the header row in app/layout.tsx; the mode pill (components/mode-banner.tsx) wraps to its own row.
import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

export const NAV_ITEMS = [
  { label: 'Home', href: '/' },
  { label: 'Moon', href: '/explore/moon' },
  { label: 'Mars', href: '/explore/mars' },
  { label: 'Earth', href: '/explore/earth' },
  { label: 'Probe', href: '/probe' },
  { label: 'Model', href: '/model' },
  { label: 'Methods', href: '/methods' },
  { label: 'Data', href: '/data' },
  { label: 'Paper', href: '/paper' },
  { label: 'API docs', href: '/api/docs' },
] as const;

const LINK = 'inline-block rounded-full px-3.5 py-1.5 text-[13px] whitespace-nowrap no-underline transition-colors';

/** The navigation for a given pathname; a link is current only on its own route. */
export function SiteNav({ pathname }: Readonly<{ pathname: string | null }>) {
  return (
    <nav aria-label="Primary" className="order-2 -mx-1 w-full min-w-0 xl:w-auto xl:flex-1">
      <ul className="flex flex-wrap items-center gap-0.5 px-1 py-1 xl:justify-center">
        {NAV_ITEMS.map(({ label, href }) => {
          const isCurrent = pathname === href;
          const className = cn(
            LINK,
            isCurrent
              ? 'bg-platinum/10 font-bold text-foreground'
              : 'text-muted-foreground hover:bg-platinum/5 hover:text-foreground',
          );
          return (
            <li key={href}>
              {href === '/api/docs' ? (
                <a href={href} aria-current={isCurrent ? 'page' : undefined} className={className}>
                  {label}
                </a>
              ) : (
                <Link href={href} aria-current={isCurrent ? 'page' : undefined} className={className}>
                  {label}
                </Link>
              )}
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
      className="order-1 flex shrink-0 items-center gap-2.5 font-wide text-[13px] tracking-[0.18em] text-platinum uppercase no-underline"
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

export function SiteHeader() {
  return (
    <>
      <Wordmark />
      <SiteNav pathname={usePathname()} />
    </>
  );
}
