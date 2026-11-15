// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Astra's Terraluna answers come first; anything else falls through to ShadowAstra's own engine
// (astra-shadowastra.ts), whose answers are about ShadowAstra's site catalogue. Every value quoted
// here comes from the release bundle through AstraContext (passed from the server layout); no
// result is typed in. Missing values are left out, never filled.
import { astraAnswerAsync as shadowAstraAnswer, pageGreeting as shadowGreeting, type AstraReply } from "./astra-shadowastra";

export type { AstraReply, AstraLink } from "./astra-shadowastra";

/** Bundle facts Astra may quote. All come from the pulled release bundle. */
export interface AstraContext {
  readonly bundleVersion: string;
  readonly shippingMode: string | null;
  readonly g5Status: string | null;
  readonly cardCount: number;
}

const norm = (s: string) =>
  ` ${s
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9 ]/g, " ")} `;

const has = (q: string, words: readonly string[]) => words.some((w) => q.includes(w));

export const TERRALUNA_STARTERS = ["What is Terraluna?", "Is the model used for ranking?", "Where are the cards?", "Where is the paper?"];

function modeText(ctx: AstraContext) {
  return ctx.shippingMode
    ? `This release (bundle ${ctx.bundleVersion}) ships in Mode ${ctx.shippingMode}`
    : `This release (bundle ${ctx.bundleVersion}) records no shipping mode`;
}

/** Terraluna-specific answers, or null to fall through to ShadowAstra's engine. */
export function terralunaAnswer(raw: string, ctx: AstraContext): AstraReply | null {
  const q = norm(raw);
  if (has(q, [" terraluna ", " what is this ", " what does this site ", " mode c ", " mode ", " radar ", " fingerprint ", " nu l ", " what do you do "])) {
    return {
      text: `Terraluna searches Earth for terrain analogs of Moon and Mars target windows. ${modeText(ctx)}${ctx.shippingMode === "C" ? ": terrain only" : ""}. Windows are compared and ranked by a physics fingerprint, ν(L) over length scales L, with terrain, radar and model distances kept separate.${ctx.shippingMode === "C" ? " The radar comparison was not completed, so no card claims a radar match." : ""}`,
      mood: "happy",
      links: [
        { label: "Cards", to: "/site" },
        { label: "Methods", to: "/methods" },
      ],
      suggestions: ["Is the model used for ranking?", "Where are the cards?", "Where is the paper?"],
    };
  }
  if (has(q, [" model ", " encoder ", " g5 ", " ranking ", " ranked ", " rank ", " ai ", " neural ", " onnx "])) {
    const g5 = ctx.g5Status ? `Gate G5 is ${ctx.g5Status}` : "Gate G5 has no recorded status in this bundle";
    return {
      text: `The learned encoder is EXPERIMENTAL. ${g5}, so the encoder is never used to rank sites; it is shown on the Model page for inspection only. Rankings come from the physics fingerprint ν(L).`,
      mood: "neutral",
      links: [
        { label: "Model", to: "/model" },
        { label: "Methods", to: "/methods" },
      ],
      suggestions: ["What is Terraluna?", "Where are the cards?"],
    };
  }
  if (has(q, [" card ", " cards ", " evidence ", " result ", " results ", " site ", " sites ", " candidate "])) {
    const n = ctx.cardCount > 0 ? `This bundle has ${ctx.cardCount} evidence cards. ` : "";
    return {
      text: `${n}Each card answers the same questions: what was compared, what was measured, how it differs and how stable it is, with evidence labels and claim limits. Open Cards for the list, or Compare to put two windows side by side.`,
      mood: "happy",
      links: [
        { label: "Cards", to: "/site" },
        { label: "Compare", to: "/compare" },
        { label: "Find", to: "/find" },
      ],
      suggestions: ["Where is the paper?", "What is Terraluna?"],
    };
  }
  if (has(q, [" paper ", " pdf ", " report ", " cite ", " citation ", " api ", " download "])) {
    return {
      text: "The paper explains the method and every measurement; its numbers come from the same release bundle as the cards. You can read it on the site or as a PDF, and the API docs list the bundle endpoints.",
      mood: "happy",
      links: [
        { label: "Paper", to: "/paper" },
        { label: "Paper PDF", to: "/paper/terraluna-paper.pdf" },
        { label: "API docs", to: "/api/docs" },
      ],
      suggestions: ["Where are the cards?", "Is the model used for ranking?"],
    };
  }
  return null;
}

export async function astraAnswerAsync(raw: string, ctx: AstraContext): Promise<AstraReply> {
  return terralunaAnswer(raw, ctx) ?? shadowAstraAnswer(raw);
}

export function pageGreeting(path: string, ctx: AstraContext): AstraReply {
  if (path === "/" || path.startsWith("/site") || path.startsWith("/model") || path.startsWith("/paper")) {
    return {
      text: `Hi, I'm Astra. ${modeText(ctx)}${ctx.shippingMode === "C" ? ": terrain-only analogs" : ""}, ranked by the physics fingerprint ν(L). Ask me what Terraluna is, about the model, or where to find the cards and paper.`,
      mood: "happy",
      suggestions: TERRALUNA_STARTERS,
    };
  }
  const base = shadowGreeting(path);
  return { ...base, suggestions: [...TERRALUNA_STARTERS.slice(0, 2), ...(base.suggestions ?? []).slice(0, 2)] };
}
