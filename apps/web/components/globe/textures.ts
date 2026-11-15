// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
import * as THREE from 'three';
import { useEffect, useState } from 'react';
import { useLoader } from '@react-three/fiber';

// NASA imagery (Blue Marble, LROC, Viking), 1k placeholder then 2k, in public/textures.
export type TextureKind = 'earth' | 'night' | 'moon' | 'mars';
type Lod = '1k' | '2k';

const NAME: Record<TextureKind, string> = {
  earth: 'earth_day',
  night: 'earth_night',
  moon: 'moon_lroc',
  mars: 'mars_viking',
};

export const textureUrl = (kind: TextureKind, lod: Lod): string => `/textures/${NAME[kind]}_${lod}.webp`;

function prep(t: THREE.Texture): THREE.Texture {
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}

const loader = new THREE.TextureLoader();

/** Suspends on the 1k texture, then swaps in the 2k one when it has streamed in. */
export function useProgressiveTexture(kind: TextureKind, target: Lod): THREE.Texture {
  const low = useLoader(THREE.TextureLoader, textureUrl(kind, '1k'));
  const [hi, setHi] = useState<THREE.Texture | null>(null);
  useEffect(() => {
    prep(low);
    if (target === '1k') return;
    let alive = true;
    void loader.loadAsync(textureUrl(kind, target)).then((t) => {
      if (alive) setHi(prep(t));
      else t.dispose();
    });
    return () => {
      alive = false;
    };
  }, [kind, target, low]);
  useEffect(() => () => hi?.dispose(), [hi]);
  return hi ?? low;
}
