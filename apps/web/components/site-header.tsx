'use client';

// Primary navigation on every page (PRD 8.6, 8.10). The app's only client module: a root layout
// cannot read the pathname, so this reads it with usePathname to mark the current route with
// aria-current="page" (orchestrator decision for T7-03). It holds the seven links only: no
// bundle data and no provider. Links are plain next/link anchors in DOM order, keep the global
// focus indicator, and show the current route in bold as well as through aria-current.
import Link from 'next/link';
import { usePathname } from 'next/navigation';

export const NAV_ITEMS = [
  { label: 'Home', href: '/' },
  { label: 'Moon', href: '/explore/moon' },
  { label: 'Mars', href: '/explore/mars' },
  { label: 'Earth', href: '/explore/earth' },
  { label: 'Probe', href: '/probe' },
  { label: 'Model', href: '/model' },
  { label: 'Methods', href: '/methods' },
  { label: 'Data', href: '/data' },
] as const;

/** The navigation for a given pathname; a link is current only on its own route. */
export function SiteNav({ pathname }: Readonly<{ pathname: string | null }>) {
  return (
    <nav aria-label="Primary" className="px-4 py-2">
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        {NAV_ITEMS.map(({ label, href }) => {
          const isCurrent = pathname === href;
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={isCurrent ? 'page' : undefined}
                className={isCurrent ? 'font-bold' : undefined}
              >
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function SiteHeader() {
  return <SiteNav pathname={usePathname()} />;
}
