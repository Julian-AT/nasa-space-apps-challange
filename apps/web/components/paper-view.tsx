// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// /paper body (PRD 8.6, D35): the released PDF with its abstract and BibTeX, or an honest
// "Paper not yet released" panel. No placeholder text, no generated imagery.
import { PageHeader } from '@/components/page-header';
import { Section } from '@/components/section';
import { UnavailablePanel } from '@/components/model/unavailable-panel';
import { Button } from '@/components/ui/button';

import type { PaperFiles } from './paper-data.ts';

export function PaperView({ paper }: { readonly paper: PaperFiles }) {
  if (paper.pdfHref === null) {
    return (
      <>
        <PageHeader eyebrow="Paper" title="Paper" />
        <div className="mt-6">
          <UnavailablePanel title="Paper not yet released">
            <p>
              The paper is published here only after a human releases it. <code>public/paper/main.pdf</code> does not exist in this
              build, so there is nothing to download.
            </p>
          </UnavailablePanel>
        </div>
      </>
    );
  }
  return (
    <>
      <PageHeader eyebrow="Paper" title="Paper" lead="The released paper, built from the same bundle artifacts as this site." />
      <p className="mt-6">
        <Button asChild>
          <a href={paper.pdfHref} download>
            Download the paper (PDF{paper.pdfBytes === null ? '' : `, ${String(Math.round(paper.pdfBytes / 1024))} KiB`})
          </a>
        </Button>
      </p>
      <Section title="Abstract">
        {paper.abstract === null ? (
          <UnavailablePanel title="Abstract not provided">
            <p>
              <code>public/paper/abstract.txt</code> is not in this build.
            </p>
          </UnavailablePanel>
        ) : (
          <p className="max-w-3xl whitespace-pre-line">{paper.abstract}</p>
        )}
      </Section>
      <Section title="BibTeX">
        {paper.bibtex === null ? (
          <UnavailablePanel title="BibTeX not provided">
            <p>
              <code>public/paper/paper.bib</code> is not in this build.
            </p>
          </UnavailablePanel>
        ) : (
          <pre tabIndex={0} aria-label="BibTeX entry" className="overflow-x-auto rounded-lg border border-line bg-card p-4 text-sm">
            <code>{paper.bibtex}</code>
          </pre>
        )}
      </Section>
    </>
  );
}
