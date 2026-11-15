// Light and dark theme. The theme follows the system preference until the user picks one with the
// header toggle; the choice is stored in localStorage ("terraluna-theme"). The inline script in
// app/layout.tsx (THEME_SCRIPT) applies it before first paint, so there is no flash. Both the shadcn
// `.dark` class and the legacy data-theme attribute are set on <html> and always agree.
'use client';

import { Moon02Icon, Sun03Icon } from '@hugeicons/core-free-icons';
import { HugeiconsIcon } from '@hugeicons/react';
import { useSyncExternalStore } from 'react';

import { Button } from '@/components/ui/button';

export type Theme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'terraluna-theme';
const THEME_EVENT = 'terraluna-theme';

/** Runs in <head> before the body renders: stored choice, else the system preference. */
export const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem('${THEME_STORAGE_KEY}');if(t!=='light'&&t!=='dark'){t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'}var d=document.documentElement;d.classList.toggle('dark',t==='dark');d.dataset.theme=t;d.style.colorScheme=t}catch(e){}})();`;

function readTheme(): Theme {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
}

export function applyTheme(theme: Theme, persist = true): void {
  const root = document.documentElement;
  root.classList.toggle('dark', theme === 'dark');
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  if (persist) {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      /* private mode: the choice lasts for this page only */
    }
  }
  window.dispatchEvent(new Event(THEME_EVENT));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(THEME_EVENT, onChange);
  // Follow the system while the user has not chosen a theme.
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const onSystem = (event: MediaQueryListEvent) => {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(THEME_STORAGE_KEY);
    } catch {
      /* ignore */
    }
    if (stored !== 'light' && stored !== 'dark') applyTheme(event.matches ? 'dark' : 'light', false);
  };
  media.addEventListener('change', onSystem);
  return () => {
    window.removeEventListener(THEME_EVENT, onChange);
    media.removeEventListener('change', onSystem);
  };
}

/** The current theme and a toggle; the server snapshot is dark until hydration reads <html>. */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const theme = useSyncExternalStore<Theme>(subscribe, readTheme, () => 'dark');
  const toggle = () => {
    applyTheme(readTheme() === 'dark' ? 'light' : 'dark');
  };
  return { theme, toggle };
}

export function ThemeToggle({ className }: Readonly<{ className?: string }>) {
  const { theme, toggle } = useTheme();
  const label = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
  return (
    <Button variant="ghost" size="icon" onClick={toggle} aria-label={label} title={label} className={className}>
      <HugeiconsIcon icon={theme === 'dark' ? Sun03Icon : Moon02Icon} strokeWidth={2} />
    </Button>
  );
}
