// The mock banner (PRD 8.4, 8.6), shown on every page while the bundle has mock=true and on
// none otherwise. Its text says that it is a mock, so the warning does not rest on colour alone;
// role="status" exposes it to assistive technology as an advisory message. White on the mock token is 6.5:1.
import type { Bundle } from '../lib/bundle.ts';
import { shellText } from '../lib/shell.ts';

export function MockBanner({ bundle }: Readonly<{ bundle: Bundle }>) {
  const text = shellText(bundle).mockBanner;
  if (text === null) return null;
  return (
    <div className="bg-mock">
      <div className="mx-auto w-full max-w-[1400px] px-4 py-1.5 sm:px-6">
        <p role="status" className="text-xs font-bold text-mock-foreground">
          {text}
        </p>
      </div>
    </div>
  );
}
