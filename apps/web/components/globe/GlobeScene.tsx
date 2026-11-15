// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
'use client';
import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

import { Planet, SUN_DIR } from './Planet';
import { Starfield } from './Starfield';
import type { GlobeProps } from './types';
import { useReducedMotion } from './use-reduced-motion';

/** The Moon on a thin orbit ring around the hero Earth. Scene dressing: not to scale, not data. */
function MoonOrbit({ reduced }: { reduced: boolean }) {
  const moon = useRef<THREE.Group>(null);
  const ring = useMemo(() => {
    const pts = new THREE.EllipseCurve(0, 0, 2.35, 2.35, 0, Math.PI * 2).getPoints(192);
    return new THREE.BufferGeometry().setFromPoints(pts.map((p) => new THREE.Vector3(p.x, 0, p.y)));
  }, []);
  useFrame(({ clock }) => {
    if (!moon.current) return;
    const t = reduced ? 2.2 : clock.elapsedTime * 0.05 + 2.2;
    moon.current.position.set(Math.cos(t) * 2.35, 0, Math.sin(t) * 2.35);
  });
  return (
    <group rotation={[0.3, 0, -0.36]}>
      <lineLoop geometry={ring}>
        <lineBasicMaterial color="#cfd6e4" transparent opacity={0.16} />
      </lineLoop>
      <group ref={moon}>
        <Planet body="moon" radius={0.27} autoRotate={!reduced} spin={0.02} lod="1k" />
      </group>
    </group>
  );
}

/** Slight camera parallax with the pointer. */
function Rig({ pointer }: { pointer: React.RefObject<{ x: number; y: number }> }) {
  const { camera } = useThree();
  useFrame((_, dt) => {
    const p = pointer.current;
    const k = Math.min(1, dt * 1.2);
    camera.position.x += (p.x * 0.28 - camera.position.x) * k;
    camera.position.y += (p.y * 0.16 - camera.position.y) * k;
    camera.lookAt(0, 0, 0);
  });
  return null;
}

/** Drag-to-rotate only: no pan, no zoom, so the page keeps its scroll wheel. */
function Drag() {
  const { camera, gl } = useThree();
  const ctl = useRef<OrbitControls | null>(null);
  useEffect(() => {
    const c = new OrbitControls(camera, gl.domElement);
    c.enablePan = false;
    c.enableZoom = false;
    c.enableDamping = true;
    c.rotateSpeed = 0.5;
    ctl.current = c;
    return () => {
      c.dispose();
      ctl.current = null;
    };
  }, [camera, gl]);
  useFrame(() => ctl.current?.update());
  return null;
}

/** Back the camera off so a sphere of this radius fits whatever the canvas shape. */
function FitSphere({ radius }: { radius: number }) {
  const { camera, size } = useThree();
  useEffect(() => {
    const cam = camera as THREE.PerspectiveCamera;
    if (!size.width || !size.height) return;
    const vHalf = (cam.fov * Math.PI) / 360;
    const hHalf = Math.atan(Math.tan(vHalf) * (size.width / size.height));
    cam.position.setLength(radius / Math.sin(Math.min(vHalf, hHalf) * 0.92));
    cam.updateProjectionMatrix();
  }, [camera, size.width, size.height, radius]);
  return null;
}

/** Earth placed right of centre on wide screens and in the upper half on phones. */
function HeroEarth({ props, reduced }: { props: GlobeProps; reduced: boolean }) {
  const { viewport } = useThree();
  const wide = viewport.width / viewport.height > 1.05;
  const pos: [number, number, number] = wide ? [viewport.width * 0.22, -0.1, 0] : [0, viewport.height * 0.2, 0];
  const scale = wide ? Math.min(1, viewport.height / 3.3) : Math.min(0.62, viewport.width / 4.4);
  const focus = useMemo(() => {
    const p = props.pins.find((x) => x.id === props.selectedId);
    return p ? { lat: p.lat, lon: p.lon } : null;
  }, [props.pins, props.selectedId]);
  return (
    <>
      <group position={pos} scale={scale}>
        <Planet
          body="earth"
          radius={1.25}
          pins={props.pins}
          activeId={props.selectedId}
          onSelect={props.onSelect}
          focus={focus}
          autoRotate={!reduced}
          spin={0.035}
          tilt={0.18}
        />
        <MoonOrbit reduced={reduced} />
      </group>
      <group position={[wide ? -viewport.width * 0.05 : -viewport.width * 0.32, wide ? viewport.height * 0.34 : -viewport.height * 0.05, -7]}>
        <Planet body="mars" radius={0.42} autoRotate={!reduced} spin={0.02} lod="1k" />
      </group>
    </>
  );
}

export default function GlobeScene(props: GlobeProps) {
  const { body, pins, selectedId, onSelect, variant = 'single', autoRotate = true } = props;
  const wrap = useRef<HTMLDivElement>(null);
  const pointer = useRef({ x: 0, y: 0 });
  const [visible, setVisible] = useState(true);
  const systemReduced = useReducedMotion();
  const reduced = systemReduced || !autoRotate;
  const hero = variant === 'hero';

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => setVisible(e?.isIntersecting ?? true), { threshold: 0 });
    io.observe(el);
    const move = (e: PointerEvent) => {
      pointer.current = { x: (e.clientX / window.innerWidth) * 2 - 1, y: -((e.clientY / window.innerHeight) * 2 - 1) };
    };
    if (hero) window.addEventListener('pointermove', move, { passive: true });
    return () => {
      io.disconnect();
      window.removeEventListener('pointermove', move);
    };
  }, [hero]);

  const focus = useMemo(() => {
    const p = pins.find((x) => x.id === selectedId);
    return p ? { lat: p.lat, lon: p.lon } : null;
  }, [pins, selectedId]);

  return (
    <div ref={wrap} className="absolute inset-0">
      <Canvas
        dpr={[1, 1.5]}
        frameloop={visible ? 'always' : 'never'}
        camera={{ position: [0, 0, hero ? 5.2 : 3.4], fov: 38 }}
        gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
      >
        <ambientLight intensity={0.14} />
        <directionalLight position={SUN_DIR.clone().multiplyScalar(10)} intensity={2.4} />
        {hero ? <Starfield drift={reduced ? 0 : 0.003} /> : null}
        <Suspense fallback={null}>
          {hero ? (
            <HeroEarth props={props} reduced={reduced} />
          ) : (
            <Planet
              body={body}
              radius={1}
              pins={pins}
              activeId={selectedId}
              onSelect={onSelect}
              focus={focus}
              autoRotate={!reduced}
              spin={0.05}
              tilt={body === 'earth' ? 0.18 : 0}
            />
          )}
        </Suspense>
        {hero ? (reduced ? null : <Rig pointer={pointer} />) : <Drag />}
        {hero ? null : <FitSphere radius={1.12} />}
      </Canvas>
    </div>
  );
}
