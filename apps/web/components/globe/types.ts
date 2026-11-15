export type GlobeBody = 'earth' | 'moon' | 'mars';
export type PinTone = 'earth' | 'moon' | 'mars' | 'flare';

/** One pin: real coordinates read from a bundle polygon by the server component that builds it. */
export interface GlobePin {
  readonly id: string;
  readonly lat: number;
  readonly lon: number;
  readonly label: string;
  readonly href: string;
  readonly tone: PinTone;
}

export interface GlobeProps {
  readonly body: GlobeBody;
  readonly pins: readonly GlobePin[];
  readonly selectedId?: string | null | undefined;
  /** Spin slowly; ignored when the visitor prefers reduced motion. Default true. */
  readonly autoRotate?: boolean | undefined;
  readonly onSelect?: ((id: string) => void) | undefined;
  /** 'hero' fills its container as a full-bleed scene with a small Moon, Mars and stars; default 'single'. */
  readonly variant?: 'single' | 'hero' | undefined;
  readonly className?: string | undefined;
}
