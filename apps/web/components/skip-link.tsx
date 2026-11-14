// The first focusable element of every page (WCAG 2.2 SC 2.4.1, PRD 8.10): it moves keyboard
// focus past the banners and navigation to the main landmark. It stays in the accessibility tree
// but is drawn only while focused. Then it takes its own row above the banners, on the page
// background, so the global focus indicator never sits on the red mock banner.
export const SKIP_LINK_TEXT = 'Skip to content';

/** The id of the layout's main landmark, which the skip link targets. */
export const MAIN_ID = 'main';

export function SkipLink() {
  return (
    <a
      href={`#${MAIN_ID}`}
      className="sr-only focus:not-sr-only focus:m-2 focus:inline-block focus:bg-background focus:px-3 focus:py-2 focus:text-foreground"
    >
      {SKIP_LINK_TEXT}
    </a>
  );
}
