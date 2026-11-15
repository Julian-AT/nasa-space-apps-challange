// /paper (PRD 8.6, D35): static page; reads apps/web/public/paper/ at build time.
import type { Metadata } from 'next';
import { join } from 'node:path';

import { PaperView } from '../../components/paper-view.tsx';
import { readPaper } from '../../components/paper-data.ts';
import { SITE_TITLE } from '../../lib/shell.ts';

export const dynamic = 'error';

export const metadata: Metadata = { title: `Paper · ${SITE_TITLE}` };

export default function PaperPage() {
  return <PaperView paper={readPaper(join(process.cwd(), 'public'))} />;
}
