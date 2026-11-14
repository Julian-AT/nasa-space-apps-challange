// The first focusable element of every page (WCAG 2.2 SC 2.4.1, PRD 8.10): it moves keyboard
// focus past the banners and navigation to the main landmark. It stays in the accessibility tree
// but is drawn only while focused, over the page and with the global focus indicator.
export const SKIP_LINK_TEXT = 'Skip to content';

/** The id of the layout's main landmark, which the skip link targets. */
export const MAIN_ID = 'main';

export function SkipLink() {
  return (
    <a
      href={`#${MAIN_ID}`}
      className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:bg-background focus:px-3 focus:py-2 focus:text-foreground"
    >
      {SKIP_LINK_TEXT}
    </a>
  );
}
