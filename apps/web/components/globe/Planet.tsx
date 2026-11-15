// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
'use client';
import { useMemo, useRef, useState } from 'react';
import { useFrame, type ThreeEvent } from '@react-three/fiber';
import * as THREE from 'three';

import { latLonToVec3 } from './geo';
import { useProgressiveTexture } from './textures';
import type { GlobeBody, GlobePin, PinTone } from './types';

export const SUN_DIR = new THREE.Vector3(-0.85, 0.32, 0.55).normalize();

const TONE: Record<PinTone, string> = { earth: '#8fb4ea', moon: '#c9c5bc', mars: '#e08a5f', flare: '#f2c879' };

const earthVertex = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vViewDir;
  void main() {
    vUv = uv;
    vec4 world = modelMatrix * vec4(position, 1.0);
    vNormalW = normalize(mat3(modelMatrix) * normal);
    vViewDir = normalize(cameraPosition - world.xyz);
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

// Day/night from NASA mosaics: city lights only on the night side, a thin limb brightening.
const earthFragment = /* glsl */ `
  uniform sampler2D dayMap;
  uniform sampler2D nightMap;
  uniform vec3 sunDir;
  varying vec2 vUv;
  varying vec3 vNormalW;
  varying vec3 vViewDir;
  void main() {
    vec3 n = normalize(vNormalW);
    float d = dot(n, normalize(sunDir));
    float dayMix = smoothstep(-0.08, 0.18, d);
    vec3 day = texture2D(dayMap, vUv).rgb;
    vec3 night = texture2D(nightMap, vUv).rgb;
    night = pow(night, vec3(1.8)) * vec3(1.0, 0.8, 0.55) * 2.2;
    vec3 lit = day * (0.16 + 1.2 * pow(max(d, 0.0), 0.75));
    vec3 col = mix(night, lit, dayMix);
    float fres = pow(1.0 - max(dot(n, vViewDir), 0.0), 3.4);
    col += vec3(0.55, 0.7, 0.95) * fres * 0.45 * smoothstep(-0.2, 0.4, d);
    gl_FragColor = vec4(col, 1.0);
    #include <colorspace_fragment>
  }
`;

const atmoVertex = /* glsl */ `
  varying vec3 vNormalV;
  varying vec3 vNormalW;
  void main() {
    vNormalV = normalize(normalMatrix * normal);
    vNormalW = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
const atmoFragment = /* glsl */ `
  uniform vec3 glow;
  uniform float strength;
  uniform vec3 sunDir;
  varying vec3 vNormalV;
  varying vec3 vNormalW;
  void main() {
    float rim = pow(max(0.66 - dot(vNormalV, vec3(0.0, 0.0, 1.0)), 0.0), 4.0);
    float sun = smoothstep(-0.35, 0.5, dot(normalize(vNormalW), normalize(sunDir)));
    gl_FragColor = vec4(glow, 1.0) * rim * strength * (0.15 + 0.85 * sun);
  }
`;

const ATMO: Partial<Record<GlobeBody, { color: string; strength: number }>> = {
  earth: { color: '#9cc0ff', strength: 1.9 },
  mars: { color: '#e5a27c', strength: 0.9 },
};

export interface PlanetProps {
  readonly body: GlobeBody;
  readonly radius?: number;
  readonly pins?: readonly GlobePin[];
  readonly activeId?: string | null | undefined;
  readonly onSelect?: ((id: string) => void) | undefined;
  /** Rotate so this point faces the camera. */
  readonly focus?: { lat: number; lon: number } | null;
  readonly autoRotate?: boolean;
  readonly spin?: number;
  readonly tilt?: number;
  readonly lod?: '1k' | '2k';
}

function EarthSurface({ radius, lod }: { radius: number; lod: '1k' | '2k' }) {
  const day = useProgressiveTexture('earth', lod);
  const night = useProgressiveTexture('night', '1k');
  const mat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        uniforms: { dayMap: { value: day }, nightMap: { value: night }, sunDir: { value: SUN_DIR } },
        vertexShader: earthVertex,
        fragmentShader: earthFragment,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  mat.uniforms['dayMap']!.value = day;
  mat.uniforms['nightMap']!.value = night;
  return (
    <mesh material={mat}>
      <sphereGeometry args={[radius, 96, 72]} />
    </mesh>
  );
}

function RockySurface({ body, radius, lod }: { body: 'moon' | 'mars'; radius: number; lod: '1k' | '2k' }) {
  const map = useProgressiveTexture(body, lod);
  return (
    <mesh>
      <sphereGeometry args={[radius, 96, 72]} />
      <meshStandardMaterial map={map} roughness={1} metalness={0} />
    </mesh>
  );
}

export function Planet({
  body,
  radius = 1,
  pins = [],
  activeId,
  onSelect,
  focus,
  autoRotate = true,
  spin = 0.05,
  tilt = 0,
  lod = '2k',
}: PlanetProps) {
  const tiltRef = useRef<THREE.Group>(null);
  const spinRef = useRef<THREE.Group>(null);
  const atmo = ATMO[body];
  const atmoMat = useMemo(
    () =>
      atmo &&
      new THREE.ShaderMaterial({
        uniforms: { glow: { value: new THREE.Color(atmo.color) }, strength: { value: atmo.strength }, sunDir: { value: SUN_DIR } },
        vertexShader: atmoVertex,
        fragmentShader: atmoFragment,
        side: THREE.BackSide,
        blending: THREE.AdditiveBlending,
        transparent: true,
        depthWrite: false,
      }),
    [atmo],
  );

  useFrame((_, dt) => {
    const s = spinRef.current;
    const t = tiltRef.current;
    if (!s || !t) return;
    const k = Math.min(1, dt * 2.4);
    if (focus) {
      const targetY = (-(focus.lon + 90) * Math.PI) / 180;
      const diff = Math.atan2(Math.sin(targetY - s.rotation.y), Math.cos(targetY - s.rotation.y));
      s.rotation.y += diff * k;
      t.rotation.x += ((Math.max(-60, Math.min(60, focus.lat)) * Math.PI) / 180 - t.rotation.x) * k;
    } else {
      if (autoRotate) s.rotation.y += dt * spin;
      t.rotation.x += (tilt - t.rotation.x) * Math.min(1, dt * 1.5);
    }
  });

  return (
    <group ref={tiltRef}>
      <group ref={spinRef}>
        {body === 'earth' ? <EarthSurface radius={radius} lod={lod} /> : <RockySurface body={body} radius={radius} lod={lod} />}
        {pins.map((p) => (
          <Pin key={p.id} pin={p} radius={radius} active={p.id === activeId} onSelect={onSelect} />
        ))}
      </group>
      {atmoMat ? (
        <mesh material={atmoMat} scale={1.055}>
          <sphereGeometry args={[radius, 48, 36]} />
        </mesh>
      ) : null}
    </group>
  );
}

function Pin({ pin, radius, active, onSelect }: { pin: GlobePin; radius: number; active: boolean; onSelect?: ((id: string) => void) | undefined }) {
  const ring = useRef<THREE.Mesh>(null);
  const [hover, setHover] = useState(false);
  const pos = useMemo(() => new THREE.Vector3(...latLonToVec3(pin.lat, pin.lon, radius * 1.003)), [pin.lat, pin.lon, radius]);
  const quat = useMemo(() => new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, 1), pos.clone().normalize()), [pos]);
  const color = TONE[pin.tone];

  // Only the selected pin breathes.
  useFrame(({ clock }) => {
    const m = ring.current;
    if (!m) return;
    const mat = m.material as THREE.MeshBasicMaterial;
    if (!active) {
      m.scale.setScalar(1);
      mat.opacity = 0.6;
      return;
    }
    const t = (clock.elapsedTime % 2.8) / 2.8;
    const e = 1 - Math.pow(1 - t, 3);
    m.scale.setScalar(1 + e * 2.4);
    mat.opacity = (1 - e) * 0.9;
  });

  const size = radius * (active ? 0.02 : hover ? 0.017 : 0.0135);
  const click = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    onSelect?.(pin.id);
  };
  return (
    <group position={pos} quaternion={quat}>
      <mesh
        onClick={click}
        onPointerOver={(e) => {
          e.stopPropagation();
          setHover(true);
          document.body.style.cursor = 'pointer';
        }}
        onPointerOut={() => {
          setHover(false);
          document.body.style.cursor = '';
        }}
      >
        <circleGeometry args={[radius * 0.05, 16]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      <mesh>
        <circleGeometry args={[size, 24]} />
        <meshBasicMaterial color={active ? '#ffffff' : color} toneMapped={false} />
      </mesh>
      <mesh ref={ring}>
        <ringGeometry args={[size * 1.5, size * 1.75, 48]} />
        <meshBasicMaterial color={color} transparent depthWrite={false} toneMapped={false} />
      </mesh>
    </group>
  );
}
