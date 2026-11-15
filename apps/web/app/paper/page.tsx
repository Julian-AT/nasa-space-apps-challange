// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// /paper (PRD 8.6, D35): static page; reads apps/web/public/paper/ at build time.
import type { Metadata } from 'next';
import { join } from 'node:path';

import { Button } from '../../components/ui/button.tsx';
import { PaperView } from '../../components/paper-view.tsx';
import { readPaper } from '../../components/paper-data.ts';
import { SITE_TITLE } from '../../lib/shell.ts';

export const dynamic = 'error';

/** The compiled paper, served from public/paper/ (added by the paper build). */
const PAPER_PDF = '/paper/terraluna-paper.pdf';

export const metadata: Metadata = { title: `Paper · ${SITE_TITLE}` };

export default function PaperPage() {
  return (
    <>
      <PaperView paper={readPaper(join(process.cwd(), 'public'))} />
      <p className="mt-10 flex flex-wrap items-center gap-3 border-t pt-6 text-sm text-muted-foreground">
        <Button asChild variant="outline">
          <a href={PAPER_PDF} className="no-underline">
            Download the paper (PDF)
          </a>
        </Button>
        <span>The compiled paper, built from the same bundle artifacts as this site.</span>
      </p>
    </>
  );
}
