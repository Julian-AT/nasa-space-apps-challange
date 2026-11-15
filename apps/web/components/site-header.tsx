// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Primary navigation on every page (PRD 8.6, 8.10). The app's only shell client module: a root
// layout cannot read the pathname, so this reads it with usePathname to mark the current route with
// aria-current="page". Desktop uses a shadcn NavigationMenu; below the lg breakpoint the links open
// in a Sheet. The current route is a filled item with bold text, never colour alone.
'use client';

import { GithubIcon, Menu01Icon, Moon02Icon, Search01Icon, Sun03Icon } from '@hugeicons/core-free-icons';
import { HugeiconsIcon } from '@hugeicons/react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { type ComponentProps, useEffect, useState } from 'react';

import { openPalette } from '@/components/shadowastra/chrome/CommandPalette';
import { Button } from '@/components/ui/button';
import { NavigationMenu, NavigationMenuItem, NavigationMenuLink, NavigationMenuList } from '@/components/ui/navigation-menu';
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
  { label: 'Methods', href: '/methods' },
  { label: 'Learn', href: '/learn' },
  { label: 'Paper', href: '/paper' },
  { label: 'API docs', href: '/api/docs' },
  { label: 'About', href: '/about' },
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
                    'rounded-md px-2.5 py-1.5 text-[13px] whitespace-nowrap no-underline',
                    current ? 'bg-muted font-semibold text-foreground' : 'text-muted-foreground hover:text-foreground',
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
                      current ? 'bg-muted font-semibold text-foreground' : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground',
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

function Wordmark() {
  return (
    <Link href="/" className="flex shrink-0 items-center gap-2 text-sm font-semibold tracking-tight text-foreground no-underline">
      <svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22" className="text-primary">
        <circle cx="12" cy="12" r="5.5" fill="currentColor" />
        <ellipse cx="12" cy="12" rx="11" ry="4" fill="none" stroke="currentColor" strokeWidth="1.2" transform="rotate(-25 12 12)" />
      </svg>
      <span>Terraluna</span>
    </Link>
  );
}

type Theme = 'dark' | 'light';

function readTheme(): Theme {
  if (typeof document === 'undefined') return 'dark';
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

/** Toggles both data-theme (site tokens) and the `dark` class (shadcn variables). */
function ThemeButton() {
  const [theme, setTheme] = useState<Theme>('dark');
  useEffect(() => {
    setTheme(readTheme());
    const on = () => {
      setTheme(readTheme());
    };
    window.addEventListener('sa-theme', on);
    return () => {
      window.removeEventListener('sa-theme', on);
    };
  }, []);
  const toggle = () => {
    const next: Theme = readTheme() === 'dark' ? 'light' : 'dark';
    const root = document.documentElement;
    root.dataset.theme = next;
    root.classList.toggle('dark', next === 'dark');
    try {
      localStorage.setItem('sa-theme', next);
    } catch {
      /* private mode */
    }
    window.dispatchEvent(new Event('sa-theme'));
  };
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      title={theme === 'dark' ? 'Light mode' : 'Dark mode'}
    >
      <HugeiconsIcon icon={theme === 'dark' ? Sun03Icon : Moon02Icon} strokeWidth={2} />
    </Button>
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
        <kbd className="hidden rounded border bg-muted px-1.5 font-mono text-[10px] sm:inline">Ctrl K</kbd>
      </Button>
      <Button variant="ghost" size="icon" asChild>
        <a href={REPO_URL} target="_blank" rel="noreferrer" aria-label="Terraluna on GitHub" title="GitHub">
          <HugeiconsIcon icon={GithubIcon} strokeWidth={2} />
        </a>
      </Button>
      <ThemeButton />
    </div>
  );
}

export function SiteHeader() {
  const pathname = usePathname();
  return (
    <div className="mx-auto flex w-full max-w-[1400px] items-center justify-between gap-4 px-4 py-2.5 sm:px-6">
      <Wordmark />
      <SiteNav pathname={pathname} className="hidden lg:flex" />
      <div className="flex items-center gap-1">
        <HeaderActions />
        <MobileNav pathname={pathname} />
      </div>
    </div>
  );
}
