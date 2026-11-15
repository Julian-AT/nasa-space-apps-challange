// Window eligibility (PRD 6.1, 10): the colour class of a window comes from the WindowEntry
// `status` the pipeline wrote, never from a score. Rejected and unknown windows keep their reason
// codes visible and are drawn differently from eligible ones, so an exclusion is never mistaken for
// a poor match. Each status also has its own outline pattern, so colour is not the only cue.
export type EligibilityStatus = 'eligible' | 'rejected' | 'unknown';

export interface EligibilityTone {
  readonly status: EligibilityStatus;
  readonly label: string;
  /** Tailwind classes for swatches and chips (token colours only). */
  readonly className: string;
  /** deck.gl fill and line colours as RGBA. */
  readonly fill: readonly [number, number, number, number];
  readonly line: readonly [number, number, number, number];
  readonly dash: boolean;
}

export const ELIGIBILITY_TONES: Readonly<Record<EligibilityStatus, EligibilityTone>> = {
  eligible: {
    status: 'eligible',
    label: 'Eligible',
    className: 'border-emerald-400 bg-emerald-400/30 text-foreground',
    // Windows overlap 4x (5 km window, 2.5 km stride), so a low per-window alpha stacks to about 15-25%.
    fill: [52, 211, 153, 16],
    line: [52, 211, 153, 210],
    dash: false,
  },
  rejected: {
    status: 'rejected',
    label: 'Rejected (excluded)',
    className: 'border-amber-400 border-dashed bg-amber-400/20 text-foreground',
    fill: [251, 191, 36, 16],
    line: [251, 191, 36, 210],
    dash: true,
  },
  unknown: {
    status: 'unknown',
    label: 'Unknown',
    className: 'border-slate-400 border-dotted bg-slate-400/20 text-foreground',
    fill: [148, 163, 184, 14],
    line: [148, 163, 184, 210],
    dash: true,
  },
};

export function eligibilityTone(status: EligibilityStatus): EligibilityTone {
  return ELIGIBILITY_TONES[status];
}

/** Window counts by status and, for the not-eligible ones, by reason code, as serialized. */
export function summariseWindows(
  windows: readonly { readonly status: EligibilityStatus; readonly reasons: readonly string[] }[],
): {
  readonly total: number;
  readonly byStatus: Readonly<Record<EligibilityStatus, number>>;
  readonly reasons: readonly (readonly [string, number])[];
} {
  const byStatus: Record<EligibilityStatus, number> = { eligible: 0, rejected: 0, unknown: 0 };
  const reasons = new Map<string, number>();
  for (const window of windows) {
    byStatus[window.status] += 1;
    for (const reason of window.reasons) reasons.set(reason, (reasons.get(reason) ?? 0) + 1);
  }
  return {
    total: windows.length,
    byStatus,
    reasons: [...reasons].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
  };
}
