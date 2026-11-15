'use client';
// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Two stacked maps; the top (target) one is clipped by a draggable, keyboard-accessible divider.
import { type ReactNode, useRef } from 'react';

import { WorldDot } from './kit.tsx';

export function SwipeStage({
  value,
  onChange,
  left,
  right,
  leftLabel,
  rightLabel,
  leftWorld,
  className = '',
}: {
  readonly value: number;
  readonly onChange: (v: number) => void;
  readonly left: ReactNode;
  readonly right: ReactNode;
  readonly leftLabel: string;
  readonly rightLabel: string;
  readonly leftWorld: 'moon' | 'mars';
  readonly className?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef(false);
  const move = (clientX: number): void => {
    const r = box.current?.getBoundingClientRect();
    if (r === undefined || r.width === 0) return;
    onChange(Math.min(0.97, Math.max(0.03, (clientX - r.left) / r.width)));
  };
  const stop = (): void => {
    drag.current = false;
  };
  return (
    <div
      ref={box}
      className={`relative overflow-hidden ${className}`}
      onPointerMove={(e) => {
        if (drag.current) move(e.clientX);
      }}
      onPointerUp={stop}
      onPointerLeave={stop}
    >
      <div className="absolute inset-0">{right}</div>
      <div className="absolute inset-0 z-[400]" style={{ clipPath: `inset(0 ${String((1 - value) * 100)}% 0 0)` }}>
        {left}
      </div>
      <div className="absolute inset-y-0 z-[700] w-px bg-foreground/70" style={{ left: `${String(value * 100)}%` }}>
        <button
          type="button"
          aria-label="Swipe divider between the target and the Earth hillshade"
          role="slider"
          aria-valuenow={Math.round(value * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuetext={`${String(Math.round(value * 100))}% target shown`}
          onPointerDown={(e) => {
            drag.current = true;
            (e.target as HTMLElement).setPointerCapture(e.pointerId);
          }}
          onPointerMove={(e) => {
            if (drag.current) move(e.clientX);
          }}
          onPointerUp={stop}
          onKeyDown={(e) => {
            if (e.key === 'ArrowLeft') onChange(Math.max(0.03, value - 0.05));
            if (e.key === 'ArrowRight') onChange(Math.min(0.97, value + 0.05));
            if (e.key === 'Home') onChange(0.03);
            if (e.key === 'End') onChange(0.97);
          }}
          className="absolute top-1/2 left-1/2 grid size-11 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize touch-none place-items-center rounded-md border border-platinum/70 bg-void/85 text-[13px] text-platinum backdrop-blur"
        >
          <span aria-hidden="true">⟷</span>
        </button>
      </div>
      <div className="pointer-events-none absolute top-3 left-3 z-[700] flex max-w-[45%] items-center gap-2 truncate rounded-md border bg-popover px-3 py-1.5 text-xs shadow-sm">
        <WorldDot world={leftWorld} /> <span className="truncate">{leftLabel}</span>
      </div>
      <div className="pointer-events-none absolute top-3 right-3 z-[700] flex max-w-[45%] items-center gap-2 truncate rounded-md border bg-popover px-3 py-1.5 text-xs shadow-sm">
        <WorldDot world="earth" /> <span className="truncate">{rightLabel}</span>
      </div>
    </div>
  );
}
