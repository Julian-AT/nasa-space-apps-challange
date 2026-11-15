// The encoder architecture figure of the paper (Figure 2), shown as the same vector drawing. The
// SVG is compiled from paper/figures/encoder-architecture.tex through
// paper/figures/standalone/encoder-architecture.tex (tectonic, then pdftocairo -svg), so the web
// and the paper share one source; its numbers come from paper/generated/numbers.tex and the model
// code. The model facts stay in the props so the caller keeps one interface.
import type { ModelFacts } from '@/components/home/model-facts';

export function ArchitectureDiagram({ facts }: { readonly facts: ModelFacts }) {
  const dim = facts.embeddingDim.ok ? facts.embeddingDim.value : null;
  return (
    <figure className="surface flex flex-col gap-4 rounded-3xl p-5 sm:p-6">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-mono text-[11px] tracking-[0.18em] text-chrome uppercase">Encoder architecture</span>
        <span className="text-xs text-chrome">paper Figure 2, drawn from the model code</span>
      </figcaption>
      <div className="rounded-2xl border border-line-2 bg-white p-3 sm:p-5">
        <img
          src="/figures/encoder-architecture.svg"
          alt={`Encoder architecture from input window at the bottom to the training loss at the top: a stride 2 convolution stem, a repeated BasicBlock (two per stage, four stages, widths 32 to 256) with a residual skip, global average pooling to a ${dim === null ? '' : `${String(dim)}-value `}embedding that is L2 normalised for retrieval and exported to ONNX, and three training heads: a projector with the VICReg loss, a physics head with a masked loss and a body head behind gradient reversal.`}
          className="mx-auto block h-auto w-full max-w-[640px]"
          width={406}
          height={455}
        />
      </div>
      <p className="text-xs leading-relaxed text-chrome">
        Inputs at the bottom, losses at the top. The grey frame is the repeated residual block. Only
        the backbone with its L2 normalisation (dashed) is exported to ONNX; the three heads are used in training only, and the body head is trained through a
        gradient reversal layer so the embedding is pushed not to encode the body.
      </p>
    </figure>
  );
}
