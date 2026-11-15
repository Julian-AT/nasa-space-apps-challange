'use client';

// Adapted from ShadowAstra_Atlas (MIT, (c) 2026 Team ShadowAstra), https://github.com/ABRUBAB/ShadowAstra_Atlas
// Primary navigation on every page (PRD 8.6, 8.10). The app's only client module: a root layout
// cannot read the pathname, so this reads it with usePathname to mark the current route with
// aria-current="page". It holds the links only: no bundle data and no provider. Links are plain
// anchors in DOM order and keep the global focus indicator. The current route is shown by
// aria-current, bold text and an underline bar, never colour alone. On narrow screens the row
// scrolls sideways instead of hiding behind a toggle, so every link stays reachable by keyboard.
import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

export const NAV_ITEMS = [
  { label: 'Home', href: '/' },
  { label: 'Moon', href: '/explore/moon' },
  { label: 'Mars', href: '/explore/mars' },
  { label: 'Earth', href: '/explore/earth' },
  { label: 'Model', href: '/model' },
  { label: 'Methods', href: '/methods' },
  { label: 'Data', href: '/data' },
  { label: 'Paper', href: '/paper' },
  { label: 'API docs', href: '/api/docs' },
] as const;

const LINK = 'inline-block rounded-md px-3 py-1.5 text-sm whitespace-nowrap no-underline hover:bg-accent';

/** The navigation for a given pathname; a link is current only on its own route. */
export function SiteNav({ pathname }: Readonly<{ pathname: string | null }>) {
  return (
    <nav aria-label="Primary" className="-mx-1 min-w-0 overflow-x-auto">
      <ul className="flex items-center gap-1 px-1 py-1">
        {NAV_ITEMS.map(({ label, href }) => {
          const isCurrent = pathname === href;
          const className = cn(
            LINK,
            isCurrent ? 'bg-accent font-bold text-foreground underline decoration-primary decoration-2' : 'text-muted-foreground hover:text-foreground',
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

export function SiteHeader() {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-1 px-4 py-2 sm:px-6 md:flex-row md:items-center md:gap-6">
      <Link href="/" className="flex shrink-0 items-center gap-2 text-lg font-semibold tracking-tight no-underline">
        <svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22" className="text-primary">
          <circle cx="12" cy="12" r="6" fill="currentColor" />
          <ellipse cx="12" cy="12" rx="11" ry="4" fill="none" stroke="currentColor" strokeWidth="1.5" transform="rotate(-25 12 12)" />
        </svg>
        <span>Terraluna</span>
      </Link>
      <SiteNav pathname={usePathname()} />
    </div>
  );
}
