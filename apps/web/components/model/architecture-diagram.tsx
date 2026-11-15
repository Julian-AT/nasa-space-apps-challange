// The encoder architecture figure of the paper (Figure 2), shown as the same vector drawing. The
// SVG is compiled from paper/figures/encoder-architecture.tex through
// paper/figures/standalone/encoder-architecture.tex (tectonic, then pdftocairo -svg), so the web
// and the paper share one source; its numbers come from paper/generated/numbers.tex and the model
// code. The model facts stay in the props so the caller keeps one interface.
import type { ModelFacts } from '@/components/home/model-facts';

export function ArchitectureDiagram({ facts }: { readonly facts: ModelFacts }) {
  const dim = facts.embeddingDim.ok ? facts.embeddingDim.value : null;
  return (
    <figure className="flex flex-col gap-3">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm font-semibold">Encoder architecture</span>
        <span className="text-xs text-muted-foreground">paper Figure 2, drawn from the model code</span>
      </figcaption>
      <div className="rounded-xl bg-white p-6 ring-1 ring-foreground/10 sm:p-10">
        <img
          src="/figures/encoder-architecture.svg"
          alt={`Encoder architecture from input window at the bottom to the training loss at the top: a stride 2 convolution stem, a repeated BasicBlock (two per stage, four stages, widths 32 to 256) with a residual skip, global average pooling to a ${dim === null ? '' : `${String(dim)}-value `}embedding that is L2 normalised for retrieval and exported to ONNX, and three training heads: a projector with the VICReg loss, a physics head with a masked loss and a body head behind gradient reversal.`}
          className="mx-auto block h-auto w-full max-w-[560px]"
          width={406}
          height={455}
        />
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        Inputs at the bottom, losses at the top; only the dashed backbone ships to the browser as ONNX.
      </p>
    </figure>
  );
}
