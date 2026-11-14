// The red mock banner (PRD 8.4, 8.6), shown on every page while the bundle has mock=true and on
// none otherwise. Its text says that it is a mock, so the warning does not rest on colour alone;
// role="status" exposes it to assistive technology as an advisory message. The red token is
// reserved for this banner, and white on it is 6.5:1.
import type { Bundle } from '../lib/bundle.ts';
import { shellText } from '../lib/shell.ts';

export function MockBanner({ bundle }: Readonly<{ bundle: Bundle }>) {
  const text = shellText(bundle).mockBanner;
  if (text === null) return null;
  return (
    <p role="status" className="bg-mock px-4 py-3 font-bold text-mock-foreground">
      {text}
    </p>
  );
}
