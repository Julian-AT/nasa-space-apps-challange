// The text and background colours that a component's class list can show in each interaction
// state, for the WCAG 2.2 SC 1.4.3 contrast test of components/ui (PRD 8.10). Colours come from
// the classes themselves, so a new hover or expanded style is checked without a hand-kept list.
//
// The model: a class without a state prefix applies at rest; a class under a prefix such as
// `hover:`, `[a]:hover:` or `aria-expanded:` applies in that state. Several states can hold at
// once (hovering an expanded button), and their order in the CSS decides which colour wins, so
// every combination of states is checked with every colour any of them sets. Text that sets no
// colour inherits the page text colour (--foreground); an element without a background shows
// the surface it sits on. A class whose effect on contrast the model cannot judge (opacity,
// arbitrary colours, gradients, unknown tokens) throws rather than being skipped.
import { composite } from './contrast.ts';

export interface Paint {
  /** A colour token of app/globals.css, without the leading `--`. */
  readonly token: string;
  /** Opacity from the Tailwind `/<percent>` modifier; 1 when there is none. */
  readonly alpha: number;
}

export interface TextCase {
  /** `rest`, or the active states joined with `+`, for example `hover+aria-expanded`. */
  readonly state: string;
  readonly text: Paint;
  /** null when no background is set, so the surface behind the element shows through. */
  readonly background: Paint | null;
}

/** WCAG 2.2 SC 1.4.3 sets no contrast requirement for an inactive user interface component. */
const EXEMPT_STATE = 'disabled';

const INHERITED_TEXT: Paint = { token: 'foreground', alpha: 1 };

const TEXT_NOT_COLOUR =
  /^text-(xs|sm|base|lg|[2-9]?xl|\[\d*\.?\d+(rem|em|px)\]|left|center|right|justify|start|end|wrap|nowrap|balance|pretty|ellipsis|clip)$/;
const BACKGROUND_NOT_COLOUR = /^bg-clip-(border|padding|content|text)$/;
const COLOUR = /^(text|bg)-([a-z][a-z0-9-]*?)(?:\/(\d{1,3}))?$/;

/** Splits `[a]:hover:bg-primary/80` into its state prefixes and the utility, outside brackets. */
function splitClass(name: string): { prefixes: string[]; utility: string } {
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < name.length; i += 1) {
    const char = name[i];
    if (char === '[') depth += 1;
    else if (char === ']') depth -= 1;
    else if (char === ':' && depth === 0) {
      parts.push(name.slice(start, i));
      start = i + 1;
    }
  }
  return { prefixes: parts, utility: name.slice(start) };
}

interface StateColours {
  readonly text: Paint[];
  readonly background: Paint[];
}

function paint(name: string, utility: string, tokens: ReadonlySet<string>): Paint {
  const match = COLOUR.exec(utility);
  const colour = match?.[2];
  const percent = match?.[3];
  if (colour === undefined || !tokens.has(colour)) {
    throw new Error(`${name}: not a colour token of app/globals.css; the contrast model cannot judge it`);
  }
  const alpha = percent === undefined ? 1 : Number(percent) / 100;
  if (alpha <= 0 || alpha > 1) throw new Error(`${name}: opacity modifier out of range`);
  return { token: colour, alpha };
}

/** Every text and background pairing the class list can show, for each combination of states. */
export function textCases(classes: string, tokens: ReadonlySet<string>): TextCase[] {
  const byState = new Map<string, StateColours>();
  const colours = (state: string): StateColours => {
    let found = byState.get(state);
    if (found === undefined) {
      found = { text: [], background: [] };
      byState.set(state, found);
    }
    return found;
  };
  colours('');

  for (const name of classes.split(/\s+/).filter((c) => c !== '')) {
    const { prefixes, utility } = splitClass(name);
    if (prefixes.includes(EXEMPT_STATE)) continue;
    if (utility.startsWith('opacity-')) {
      throw new Error(`${name}: opacity changes text contrast; the contrast model cannot judge it`);
    }
    const property = utility.startsWith('text-') ? 'text' : utility.startsWith('bg-') ? 'background' : null;
    if (property === null) continue;
    if (TEXT_NOT_COLOUR.test(utility) || BACKGROUND_NOT_COLOUR.test(utility)) continue;
    colours(prefixes.join(':'))[property].push(paint(name, utility, tokens));
  }

  const rest = colours('');
  const states = [...byState.keys()].filter((state) => state !== '');
  const cases: TextCase[] = [];
  for (let mask = 0; mask < 2 ** states.length; mask += 1) {
    const active = states.filter((_, i) => (mask & (2 ** i)) !== 0);
    const pick = (property: keyof StateColours): Paint[] => {
      const set = active.flatMap((state) => colours(state)[property]);
      return set.length > 0 ? set : rest[property];
    };
    const texts = pick('text');
    const backgrounds = pick('background');
    for (const text of texts.length > 0 ? texts : [INHERITED_TEXT]) {
      for (const background of backgrounds.length > 0 ? backgrounds : [null]) {
        cases.push({ state: active.length > 0 ? active.join('+') : 'rest', text, background });
      }
    }
  }
  return cases;
}

/** The opaque text and background colours of a case over a surface colour. */
export function resolveCase(
  textCase: TextCase,
  surface: string,
  tokenHex: (name: string) => string,
): { text: string; background: string } {
  const { text, background } = textCase;
  const behind =
    background === null ? surface : composite(tokenHex(background.token), background.alpha, surface);
  return { text: composite(tokenHex(text.token), text.alpha, behind), background: behind };
}

function describePaint(prefix: string, { token, alpha }: Paint): string {
  return alpha === 1 ? `${prefix}-${token}` : `${prefix}-${token}/${String(Math.round(alpha * 100))}`;
}

export function describeCase({ state, text, background }: TextCase): string {
  const fill = background === null ? 'no background' : describePaint('bg', background);
  return `${state}: ${describePaint('text', text)} on ${fill}`;
}
