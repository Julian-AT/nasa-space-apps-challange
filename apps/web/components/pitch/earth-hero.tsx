'use client';

// Photoreal Earth for the pitch title slide: NASA Blue Marble day texture and Black Marble city
// lights (public domain), one directional sun from the upper left, a soft terminator, an ocean
// glint and a faint white limb. Transparent canvas, so the slide colour shows through. No
// atmosphere glow. One turn in ~90 s, paused under prefers-reduced-motion.
import { Canvas, useFrame, useLoader, useThree } from '@react-three/fiber';
import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import {
  ACESFilmicToneMapping,
  type Group,
  LinearMipmapLinearFilter,
  ShaderMaterial,
  SRGBColorSpace,
  TextureLoader,
  Vector3,
} from 'three';

const DAY = '/textures/earth_day_4k.webp';
const NIGHT = '/textures/earth_night_2k.webp';
const TILT = (23.4 * Math.PI) / 180;
const TURN_S = 90;
/** World-space direction towards the sun: upper left, a little in front. */
const SUN = new Vector3(-1.0, 0.55, 0.62).normalize();

const vertex = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vN;
  varying vec3 vP;
  void main() {
    vUv = uv;
    vec4 wp = modelMatrix * vec4(position, 1.0);
    vP = wp.xyz;
    vN = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * viewMatrix * wp;
  }
`;

const fragment = /* glsl */ `
  uniform sampler2D uDay;
  uniform sampler2D uNight;
  uniform vec3 uSun;
  varying vec2 vUv;
  varying vec3 vN;
  varying vec3 vP;

  void main() {
    vec3 N = normalize(vN);
    vec3 V = normalize(cameraPosition - vP);
    vec3 L = normalize(uSun);
    vec3 H = normalize(L + V);

    vec3 day = texture2D(uDay, vUv).rgb;      // linear (sRGB texture decoded by the GPU)
    vec3 night = texture2D(uNight, vUv).rgb;

    float ndl = dot(N, L);
    // Soft terminator: a gentle wrap so the day side rolls off instead of cutting.
    float diffuse = smoothstep(-0.12, 0.55, ndl) * 0.85 + max(ndl, 0.0) * 0.35;
    float dayMix = smoothstep(-0.18, 0.12, ndl);

    // Ocean mask from the day texture: blue-dominant and dark.
    float lum = dot(day, vec3(0.2126, 0.7152, 0.0722));
    float blueness = day.b - max(day.r, day.g * 0.92);
    float ocean = smoothstep(0.004, 0.03, blueness) * (1.0 - smoothstep(0.05, 0.16, lum));

    float ndv = max(dot(N, V), 0.0);
    float fres = pow(1.0 - ndv, 5.0);

    // Ocean glint: a tight core and a broad sheen, lit side only, Schlick-boosted.
    float ndh = max(dot(N, H), 0.0);
    float spec = (pow(ndh, 90.0) * 0.55 + pow(ndh, 14.0) * 0.06) * ocean * smoothstep(0.0, 0.25, ndl);
    spec *= 0.6 + 0.4 * (0.02 + 0.98 * fres);

    // Very slight cool-neutral fill in the shadow so it reads as soft, never black.
    vec3 ambient = day * vec3(0.030, 0.031, 0.034);
    vec3 col = day * diffuse * vec3(1.0, 0.985, 0.96) * 1.25 + ambient;
    col += vec3(1.0, 0.97, 0.92) * spec;

    // Limb haze on the day side: lift towards a pale neutral, not blue.
    col = mix(col, vec3(0.80, 0.82, 0.84) * diffuse, pow(1.0 - ndv, 3.0) * 0.22 * dayMix);

    // City lights only in the dark hemisphere, warm and subdued.
    float nightMask = 1.0 - smoothstep(-0.25, 0.02, ndl);
    float lights = pow(dot(night, vec3(0.333)), 1.4);
    col += vec3(1.0, 0.72, 0.42) * lights * nightMask * 1.6;

    // Thin white rim for edge definition (alpha-limited).
    float rim = pow(1.0 - ndv, 6.0) * 0.12;
    col += vec3(rim);

    gl_FragColor = vec4(col, 1.0);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

function Planet({ reduced }: { reduced: boolean }) {
  const day = useLoader(TextureLoader, DAY);
  const night = useLoader(TextureLoader, NIGHT);
  const gl = useThree((s) => s.gl);
  const spin = useRef<Group>(null);

  const material = useMemo(() => {
    const aniso = gl.capabilities.getMaxAnisotropy();
    for (const t of [day, night]) {
      t.colorSpace = SRGBColorSpace;
      t.anisotropy = aniso;
      t.generateMipmaps = true;
      t.minFilter = LinearMipmapLinearFilter;
      t.needsUpdate = true;
    }
    return new ShaderMaterial({
      uniforms: { uDay: { value: day }, uNight: { value: night }, uSun: { value: SUN } },
      vertexShader: vertex,
      fragmentShader: fragment,
    });
  }, [day, night, gl]);

  useEffect(() => () => material.dispose(), [material]);

  useFrame((_, dt) => {
    if (!reduced && spin.current) spin.current.rotation.y += (Math.PI * 2 * Math.min(dt, 0.1)) / TURN_S;
  });

  return (
    <group rotation={[0, 0, TILT]}>
      {/* Start with Africa and Europe facing the viewer. */}
      <group ref={spin} rotation={[0, -1.75, 0]}>
        <mesh material={material}>
          <sphereGeometry args={[1, 128, 128]} />
        </mesh>
      </group>
    </group>
  );
}

export function EarthHero({ className }: { className?: string }) {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const on = () => setReduced(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);

  return (
    <div className={className} role="img" aria-label="Rendered Earth, NASA Blue Marble imagery">
      {/* Soft contact shadow under the planet. */}
      <div
        aria-hidden
        style={{
          position: 'absolute',
          left: '22%',
          right: '22%',
          bottom: '3%',
          height: '6%',
          borderRadius: '50%',
          background: 'radial-gradient(closest-side, rgba(40,32,20,0.16), rgba(40,32,20,0))',
          filter: 'blur(6px)',
        }}
      />
      <Canvas
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
        dpr={[1, 2]}
        camera={{ fov: 28, position: [0, 0, 4.6], near: 0.1, far: 20 }}
        gl={{ alpha: true, antialias: true, toneMapping: ACESFilmicToneMapping, outputColorSpace: SRGBColorSpace, toneMappingExposure: 1.05 }}
        onCreated={({ gl }) => gl.setClearColor(0x000000, 0)}
      >
        <Suspense fallback={null}>
          <Planet reduced={reduced} />
        </Suspense>
      </Canvas>
    </div>
  );
}

export default EarthHero;
