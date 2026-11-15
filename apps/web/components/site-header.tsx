// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Primary navigation on every page (PRD 8.6, 8.10). The app's only shell client module: a root
// layout cannot read the pathname, so this reads it with usePathname to mark the current route with
// aria-current="page". Desktop uses a shadcn NavigationMenu; below the lg breakpoint the links open
// in a Sheet. The current route is a filled item with bold text, never colour alone.
'use client';

import { GithubIcon, Menu01Icon, EarthIcon, Search01Icon } from '@hugeicons/core-free-icons';
import { HugeiconsIcon } from '@hugeicons/react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { type ComponentProps, useState } from 'react';

import { openPalette } from '@/components/shadowastra/chrome/CommandPalette';
import { Button } from '@/components/ui/button';
import { NavigationMenu, NavigationMenuItem, NavigationMenuLink, NavigationMenuList } from '@/components/ui/navigation-menu';
import { ThemeToggle } from '@/components/theme-toggle';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
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
  { label: 'Paper', href: '/paper' },
] as const;

function isCurrentRoute(href: string, pathname: string | null): boolean {
  if (href === '/') return pathname === '/';
  const section = '/' + href.split('/')[1];
  return pathname === section || (pathname ?? '').startsWith(`${section}/`);
}

/** A route link; /api/docs is a plain anchor (served by the API, not the app router). Forwards Slot props. */
function NavAnchor({
  href,
  label,
  current,
  ...rest
}: Readonly<{ href: string; label: string; current: boolean } & Omit<ComponentProps<'a'>, 'href'>>) {
  return href === '/api/docs' ? (
    <a href={href} aria-current={current ? 'page' : undefined} {...rest}>
      {label}
    </a>
  ) : (
    <Link href={href} aria-current={current ? 'page' : undefined} {...(rest as Record<string, unknown>)}>
      {label}
    </Link>
  );
}

/** The navigation for a given pathname; a link is current only on its own route. */
export function SiteNav({ pathname, className }: Readonly<{ pathname: string | null; className?: string }>) {
  return (
    <NavigationMenu aria-label="Primary" className={cn('min-w-0', className)}>
      <NavigationMenuList className="gap-0.5">
        {NAV_ITEMS.map(({ label, href }) => {
          const current = isCurrentRoute(href, pathname);
          return (
            <NavigationMenuItem key={href}>
              <NavigationMenuLink asChild active={current}>
                <NavAnchor
                  href={href}
                  label={label}
                  current={current}
                  className={cn(
                    'rounded-full px-3 py-1 text-[13px] whitespace-nowrap no-underline transition-colors',
                    current
                      ? 'bg-muted font-medium text-foreground ring-1 ring-border data-[active=true]:bg-muted'
                      : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground',
                  )}
                />
              </NavigationMenuLink>
            </NavigationMenuItem>
          );
        })}
      </NavigationMenuList>
    </NavigationMenu>
  );
}

function MobileNav({ pathname }: Readonly<{ pathname: string | null }>) {
  const [open, setOpen] = useState(false);
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="outline" size="icon" aria-label={open ? 'Close menu' : 'Open menu'} className="lg:hidden">
          <HugeiconsIcon icon={Menu01Icon} strokeWidth={2} />
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-72">
        <SheetHeader>
          <SheetTitle>Terraluna</SheetTitle>
        </SheetHeader>
        <nav aria-label="Primary" className="px-4 pb-4">
          <ul className="flex flex-col gap-0.5">
            {NAV_ITEMS.map(({ label, href }) => {
              const current = isCurrentRoute(href, pathname);
              return (
                <li key={href}>
                  <NavAnchor
                    href={href}
                    label={label}
                    current={current}
                    onClick={() => {
                      setOpen(false);
                    }}
                    className={cn(
                      'block rounded-md px-3 py-2 text-sm no-underline',
                      current ? 'bg-muted font-medium text-foreground ring-1 ring-border' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground',
                    )}
                  />
                </li>
              );
            })}
          </ul>
        </nav>
      </SheetContent>
    </Sheet>
  );
}

/** The Terraluna mark: an Earth icon in a hairline square lit by the sunlight accent. Shared with the footer. */
export function LogoMark({ size = 'sm' }: Readonly<{ size?: 'sm' | 'xs' }>) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-md border bg-card text-primary',
        size === 'sm' ? 'size-7' : 'size-6',
      )}
    >
      <HugeiconsIcon icon={EarthIcon} strokeWidth={2} className={size === 'sm' ? 'size-4' : 'size-3.5'} />
    </span>
  );
}

function Wordmark() {
  return (
    <Link href="/" className="flex shrink-0 items-center gap-2 font-sans text-[15px] font-semibold tracking-tight text-foreground no-underline">
      <LogoMark />
      <span>Terraluna</span>
      <span className="hidden font-mono text-[10px] font-normal tracking-[0.18em] text-muted-foreground uppercase xl:inline">Gradient Descent</span>
    </Link>
  );
}

function HeaderActions() {
  return (
    <div className="flex shrink-0 items-center gap-1">
      <Button
        variant="outline"
        onClick={openPalette}
        aria-label="Search (Ctrl K)"
        aria-keyshortcuts="Control+K Meta+K /"
        className="text-muted-foreground"
      >
        <HugeiconsIcon icon={Search01Icon} strokeWidth={2} />
        <span className="hidden sm:inline">Search</span>
        <kbd className="hidden rounded-sm border bg-muted px-1.5 font-mono text-[10px] text-muted-foreground sm:inline">Ctrl K</kbd>
      </Button>
      <ThemeToggle />
      <Button variant="ghost" size="icon" asChild>
        <a href={REPO_URL} target="_blank" rel="noreferrer" aria-label="Terraluna on GitHub" title="GitHub">
          <HugeiconsIcon icon={GithubIcon} strokeWidth={2} />
        </a>
      </Button>
    </div>
  );
}

export function SiteHeader() {
  const pathname = usePathname();
  // A solid band (no blur or glass) so the page never shows through; the bottom hairline is the
  // layout header's border.
  return (
    <div className="w-full bg-background">
      <div className="mx-auto flex h-14 w-full max-w-[1400px] items-center justify-between gap-4 px-4 sm:px-6">
        <Wordmark />
        <SiteNav pathname={pathname} className="hidden lg:flex" />
        <div className="flex items-center gap-1">
          <HeaderActions />
          <MobileNav pathname={pathname} />
        </div>
      </div>
    </div>
  );
}
