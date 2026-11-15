'use client';
import dynamic from 'next/dynamic';

import { textureUrl } from './textures';
import type { GlobeBody, GlobeProps } from './types';

// three and react-three-fiber load only when a globe is on the page (never in the landing JS).
const GlobeScene = dynamic(() => import('./GlobeScene'), { ssr: false, loading: () => null });

const STILL: Record<GlobeBody, string> = {
  earth: textureUrl('earth', '1k'),
  moon: textureUrl('moon', '1k'),
  mars: textureUrl('mars', '1k'),
};

/** The static stand-in shown first (and kept behind the canvas): the NASA mosaic as a lit disc. */
function StaticDisc({ body }: { body: GlobeBody }) {
  return (
    <div aria-hidden="true" className="absolute inset-0 flex items-center justify-center">
      <div
        className="aspect-square h-[78%] max-h-full max-w-[78%] rounded-full"
        style={{
          backgroundImage: `url(${STILL[body]})`,
          backgroundSize: '200% 100%',
          backgroundPosition: '-40% 0',
          boxShadow: 'inset -2.2rem -0.5rem 2.4rem rgba(7,8,10,0.94), inset 0.3rem 0.1rem 0.9rem rgba(255,255,255,0.08)',
        }}
      />
    </div>
  );
}

const BODY_NAME: Record<GlobeBody, string> = { earth: 'Earth', moon: 'Moon', mars: 'Mars' };

/**
 * A textured 3D globe with real pins. The pins list is also rendered as links, so the globe is
 * reachable by keyboard and screen readers: the canvas itself is decorative.
 */
export function Globe(props: GlobeProps) {
  const { body, pins, className, variant = 'single' } = props;
  return (
    <div className={`relative ${variant === 'hero' ? 'h-full w-full' : 'aspect-square w-full'} ${className ?? ''}`}>
      {variant === 'hero' ? null : <StaticDisc body={body} />}
      <div aria-hidden="true" className="absolute inset-0">
        <GlobeScene {...props} />
      </div>
      <nav aria-label={`${BODY_NAME[body]} pins`} className="sr-only focus-within:not-sr-only focus-within:absolute focus-within:inset-x-2 focus-within:bottom-2 focus-within:z-20 focus-within:max-h-40 focus-within:overflow-auto focus-within:rounded-xl focus-within:bg-panel focus-within:p-3">
        <ul className="flex flex-col gap-1 text-sm">
          {pins.map((p) => (
            <li key={p.id}>
              <a href={p.href} className="underline">
                {p.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}
