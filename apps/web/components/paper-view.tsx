// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// /paper body (PRD 8.6, D35): one card with the paper's title, an abstract excerpt, the PDF link and
// a BibTeX entry built from the title, or an honest "Paper not yet released" panel. No placeholder
// text, no generated imagery.
import { PageHeader } from '@/components/page-header';
import { UnavailablePanel } from '@/components/model/unavailable-panel';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card';

export interface PaperInfo {
  readonly title?: string | null;
  /** Public URL of the PDF, or null when the paper build added none. */
  readonly pdfHref: string | null;
  readonly pdfBytes: number | null;
  /** The abstract or an excerpt of it, read from the paper sources. */
  readonly abstract: string | null;
  readonly excerpt?: boolean;
  readonly bibtex: string | null;
}

/** A BibTeX entry built from the paper's title only. */
export function paperBibtex(title: string): string {
  return ['@misc{terraluna2026,', `  title  = {${title}},`, '  author = {{Terraluna}},', '  year   = {2026},', '  note   = {NASA Space Apps Challenge 2026}', '}'].join('\n');
}

export function PaperView({ paper }: { readonly paper: PaperInfo }) {
  if (paper.pdfHref === null) {
    return (
      <>
        <PageHeader eyebrow="Paper" title="Paper" />
        <div className="mt-6">
          <UnavailablePanel title="Paper not yet released">
            <p>The compiled paper is not in this build, so there is nothing to download.</p>
          </UnavailablePanel>
        </div>
      </>
    );
  }
  const size = paper.pdfBytes === null ? '' : ` (${String(Math.round(paper.pdfBytes / 1024))} KiB)`;
  return (
    <>
      <PageHeader eyebrow="Paper" title="Paper" lead="The Terraluna paper, built from the same bundle artifacts as this site." />
      <Card className="mt-6 max-w-4xl">
        <CardHeader className="flex flex-col gap-2">
          <p className="text-xs font-medium text-muted-foreground">Terraluna · NASA Space Apps 2026</p>
          <h2 className="text-2xl leading-snug font-semibold tracking-tight text-balance">{paper.title ?? 'Terraluna'}</h2>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">{paper.excerpt === true ? 'Abstract (excerpt)' : 'Abstract'}</h3>
          {paper.abstract === null ? (
            <p className="text-sm text-muted-foreground">Abstract not provided in this build.</p>
          ) : (
            <p className="max-w-prose leading-relaxed whitespace-pre-line text-muted-foreground">
              {paper.abstract}
              {paper.excerpt === true ? ' …' : ''}
            </p>
          )}
        </CardContent>
        <CardFooter className="flex flex-col items-stretch gap-4 border-t">
          <div className="flex flex-wrap items-center gap-3">
            <Button asChild size="lg">
              <a href={paper.pdfHref} className="no-underline">
                Read the PDF
              </a>
            </Button>
            <Button asChild size="lg" variant="outline">
              <a href={paper.pdfHref} download className="no-underline">
                Download{size}
              </a>
            </Button>
          </div>
          {paper.bibtex === null ? (
            <p className="text-sm text-muted-foreground">BibTeX not provided in this build.</p>
          ) : (
            <details className="group rounded-lg bg-muted/50 px-4 ring-1 ring-foreground/10">
              <summary className="cursor-pointer py-3 text-sm font-medium">Cite this paper (BibTeX)</summary>
              <pre tabIndex={0} aria-label="BibTeX entry" className="overflow-x-auto pb-4 font-mono text-xs leading-relaxed">
                <code>{paper.bibtex}</code>
              </pre>
            </details>
          )}
        </CardFooter>
      </Card>
    </>
  );
}
