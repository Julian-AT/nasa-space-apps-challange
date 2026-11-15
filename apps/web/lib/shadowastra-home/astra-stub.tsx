// Copied from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// The Astra mascot is ported by another worker; until then the home sections render nothing in its place.
// climateReaction and the Mood/Action/Reaction types are copied verbatim from src/components/ui/Astronaut.tsx.
export type Mood = "neutral" | "happy" | "sad" | "focused" | "thinking" | "wow" | "cold" | "hot";
export type Action = "idle" | "wave" | "scan" | "drill" | "flag" | "sample" | "celebrate" | "walk" | "slump" | "shiver" | "fan" | "point" | "binoculars";
export interface Reaction {
  mood: Mood;
  action: Action;
  say: string;
}

export function Astronaut(_props: Record<string, unknown>): null {
  return null;
}

export function climateReaction(nightMinC: number | null, dayMaxC: number | null): Reaction | null {
  if (nightMinC !== null && nightMinC <= -25) return { mood: "cold", action: "shiver", say: `Brr! Nights here drop to ${String(Math.round(nightMinC))} °C. Good practice for the cold.` };
  if (dayMaxC !== null && dayMaxC >= 48) return { mood: "hot", action: "fan", say: `Phew, the ground hits ${String(Math.round(dayMaxC))} °C. Great for testing gear in heat.` };
  return null;
}
