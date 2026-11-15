'use client';

// Photoreal Earth for the pitch title slide: NASA Blue Marble day texture and Black Marble city
// lights (public domain), one directional sun from the upper left, a soft terminator, an ocean
// glint and a faint white limb. Transparent canvas, so the slide colour shows through. No
// atmosphere glow. One turn in ~90 s, paused under prefers-reduced-motion. Optional `focus` eases a
// point to face the camera and stops the spin; optional `pin` marks a surface point; optional
// `scan` sweeps one light band across the disc and `zoom` dollies the camera towards the focus.
import { Canvas, useFrame, useLoader, useThree } from '@react-three/fiber';
import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import {
  ACESFilmicToneMapping,
  Color,
  type Group,
  type Mesh,
  type MeshBasicMaterial,
  LinearMipmapLinearFilter,
  Quaternion,
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
const SUN = new Vector3(-0.95, 0.5, 0.78).normalize();

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
  uniform float uScan;
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
    // Soft terminator: wrapped Lambert, eased, so the day side rolls off into shadow.
    float w = clamp((ndl + 0.18) / 1.18, 0.0, 1.0);
    float diffuse = w * w * (3.0 - 2.0 * w) * 0.55 + w * 0.6;
    float dayMix = smoothstep(-0.18, 0.15, ndl);

    // Ocean mask from the day texture: blue-dominant and dark.
    // Read the mask from a blurred mip so its edges stay soft and texture blocks do not show.
    vec3 dayBlur = texture2D(uDay, vUv, 3.0).rgb;
    float lum = dot(dayBlur, vec3(0.2126, 0.7152, 0.0722));
    float blueness = dayBlur.b - max(dayBlur.r, dayBlur.g * 0.92);
    float ocean = smoothstep(-0.005, 0.02, blueness) * (1.0 - smoothstep(0.20, 0.38, lum));

    // Lift the very dark Blue Marble ocean to a calm deep blue so it reads in daylight.
    // Mostly a uniform deep blue so the bathymetry streaks in the texture do not show.
    vec3 sea = mix(vec3(0.010, 0.030, 0.080), dayBlur * 0.9, 0.12);
    vec3 albedo = mix(day, sea, ocean);

    float ndv = max(dot(N, V), 0.0);
    float fres = pow(1.0 - ndv, 5.0);

    // Ocean glint: a soft core and a broad sheen, lit side only, Schlick-boosted.
    float ndh = max(dot(N, H), 0.0);
    float spec = (pow(ndh, 48.0) * 0.14 + pow(ndh, 8.0) * 0.015) * ocean * smoothstep(0.0, 0.3, ndl);

    // Slight neutral fill in the shadow so it reads as soft, never pure black.
    vec3 ambient = albedo * vec3(0.045, 0.046, 0.05);
    vec3 col = albedo * diffuse * vec3(1.0, 0.985, 0.96) * 1.2 + ambient;
    col += vec3(1.0, 0.97, 0.92) * spec;

    // Limb haze on the day side: lift towards a pale neutral, not blue.
    col = mix(col, vec3(0.78, 0.80, 0.82) * diffuse, pow(1.0 - ndv, 3.0) * 0.25 * dayMix);

    // City lights only in the dark hemisphere, warm and subdued.
    float nightMask = 1.0 - smoothstep(-0.25, 0.02, ndl);
    float lights = pow(dot(night, vec3(0.333)), 1.4);
    col += vec3(1.0, 0.72, 0.42) * lights * nightMask * 1.1;

    // Thin white rim for edge definition (alpha-limited).
    float rim = pow(1.0 - ndv, 6.0) * 0.12;
    col += vec3(rim);

    // Scan sweep (finder slide): a soft bright band crossing the disc left to right in view space,
    // with a faint lat/lon dot grid lighting up on land just behind it. Off when uScan is 0 or 1.
    if (uScan > 0.0 && uScan < 1.0) {
      vec3 vp = (viewMatrix * vec4(vP, 1.0)).xyz;
      float x0 = mix(-1.35, 1.35, uScan);
      float d = vp.x - x0;
      float band = exp(-d * d / 0.010);
      float trail = exp(-max(-d, 0.0) * 5.0) * step(d, 0.0) * 0.5;
      float fade = smoothstep(0.0, 0.12, uScan) * (1.0 - smoothstep(0.85, 1.0, uScan));
      vec2 cell = fract(vUv * vec2(180.0, 90.0)) - 0.5;
      float dots = smoothstep(0.22, 0.08, length(cell)) * (1.0 - ocean);
      col += vec3(1.0, 0.95, 0.88) * band * 0.34 * fade * smoothstep(0.0, 0.25, ndv);
      col += vec3(1.0, 0.62, 0.25) * dots * (band + trail) * 0.55 * fade;
    }

    gl_FragColor = vec4(col, 1.0);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

export type LatLon = { lat: number; lon: number };

const D2R = Math.PI / 180;
const FOCUS_S = 1.2;
/** Scan sweep and camera dolly durations, seconds. */
const SCAN_S = 1.3;
const ZOOM_S = 1.2;
/** Camera distance at rest and when zoomed (~1,900 km of surface across the view). */
const CAM_Z = 4.6;
const ZOOM_Z = 1.6;
/** Default spin (rotation.y): Africa and Europe facing the viewer. */
const AFRICA_Y = -1.75;
const ACCENT = new Color('#CC5800');

/** Unit-sphere point for a latitude/longitude in three's SphereGeometry UV layout (u = 0 at -180°). */
function surface(lat: number, lon: number, r = 1): Vector3 {
  const a = lat * D2R;
  const o = lon * D2R;
  return new Vector3(Math.cos(a) * Math.cos(o), Math.sin(a), -Math.cos(a) * Math.sin(o)).multiplyScalar(r);
}

/** Spin angle (rotation.y) that brings longitude `lon` to face the camera (+z). */
const spinFor = (lon: number) => -Math.PI / 2 - lon * D2R;

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

/** White dot with a thin accent ring and a soft pulse, lying flat on the surface. */
function Pin({ at }: { at: LatLon }) {
  const pulse = useRef<Mesh>(null);
  const pulseMat = useRef<MeshBasicMaterial>(null);
  const pos = useMemo(() => surface(at.lat, at.lon, 1.004), [at.lat, at.lon]);
  // Orient the disc (+z) along the local surface normal; lookAt would use world space.
  const quat = useMemo(() => new Quaternion().setFromUnitVectors(new Vector3(0, 0, 1), pos.clone().normalize()), [pos]);
  useFrame(({ clock }) => {
    const t = (clock.elapsedTime % 1.8) / 1.8;
    pulse.current?.scale.setScalar(1 + t * 2.4);
    if (pulseMat.current) pulseMat.current.opacity = 0.55 * (1 - t) ** 2;
  });
  return (
    <group position={pos} quaternion={quat}>
      <mesh ref={pulse} renderOrder={2}>
        <ringGeometry args={[0.036, 0.042, 64]} />
        <meshBasicMaterial ref={pulseMat} color={ACCENT} transparent depthWrite={false} toneMapped={false} />
      </mesh>
      <mesh renderOrder={3}>
        <ringGeometry args={[0.023, 0.03, 64]} />
        <meshBasicMaterial color={ACCENT} toneMapped={false} />
      </mesh>
      <mesh renderOrder={3}>
        <circleGeometry args={[0.023, 48]} />
        <meshBasicMaterial color="#ffffff" toneMapped={false} />
      </mesh>
    </group>
  );
}

function Planet({
  reduced,
  focus,
  pin,
  startLon,
  scan,
  zoom,
}: {
  reduced: boolean;
  focus: LatLon | null;
  pin: LatLon | null;
  startLon: number | null;
  scan: boolean;
  zoom: boolean;
}) {
  const day = useLoader(TextureLoader, DAY);
  const night = useLoader(TextureLoader, NIGHT);
  const gl = useThree((s) => s.gl);
  const tilt = useRef<Group>(null);
  const spin = useRef<Group>(null);
  // A running focus animation: start pose, target pose and elapsed time.
  const anim = useRef<{ from: [number, number, number]; to: [number, number, number]; t: number } | null>(null);

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
      uniforms: { uDay: { value: day }, uNight: { value: night }, uSun: { value: SUN }, uScan: { value: 0 } },
      vertexShader: vertex,
      fragmentShader: fragment,
    });
  }, [day, night, gl]);

  useEffect(() => () => material.dispose(), [material]);

  const fLat = focus?.lat;
  const fLon = focus?.lon;
  useEffect(() => {
    const tg = tilt.current;
    const sp = spin.current;
    if (!tg || !sp) return;
    if (fLat === undefined || fLon === undefined) {
      // Back to the free tilted spin; with a start longitude, also back to the opening view.
      const home = startLon === null ? sp.rotation.y : spinFor(startLon);
      const y = home + Math.round((sp.rotation.y - home) / (Math.PI * 2)) * Math.PI * 2;
      anim.current = { from: [tg.rotation.x, tg.rotation.z, sp.rotation.y], to: [0, TILT, y], t: reduced ? FOCUS_S : 0 };
      return;
    }
    // Nearest equivalent spin angle, so the globe takes the short way round.
    const raw = spinFor(fLon);
    const y = raw + Math.round((sp.rotation.y - raw) / (Math.PI * 2)) * Math.PI * 2;
    anim.current = { from: [tg.rotation.x, tg.rotation.z, sp.rotation.y], to: [fLat * D2R, 0, y], t: reduced ? FOCUS_S : 0 };
    // startLon is a mount-time choice
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fLat, fLon, reduced]);

  // Scan sweep progress (0..1, seconds-driven) and camera dolly progress (0 rest, 1 zoomed).
  const scanT = useRef(1);
  const zoomK = useRef(0);
  useEffect(() => {
    scanT.current = scan && !reduced ? 0 : 1;
  }, [scan, reduced]);
  useEffect(() => {
    if (reduced) zoomK.current = zoom ? 1 : 0;
  }, [zoom, reduced]);

  useFrame(({ camera }, delta) => {
    const dt = Math.min(delta, 0.1);
    if (scanT.current < 1) scanT.current = Math.min(1, scanT.current + dt / SCAN_S);
    const u = material.uniforms.uScan;
    if (u) u.value = scanT.current < 1 ? scanT.current : 0;
    const goal = zoom ? 1 : 0;
    if (zoomK.current !== goal) {
      const stepK = dt / ZOOM_S;
      zoomK.current = goal > zoomK.current ? Math.min(goal, zoomK.current + stepK) : Math.max(goal, zoomK.current - stepK);
    }
    const z = CAM_Z + (ZOOM_Z - CAM_Z) * ease(zoomK.current);
    if (camera.position.z !== z) {
      camera.position.set(0, 0, z);
      camera.lookAt(0, 0, 0);
    }
    const tg = tilt.current;
    const sp = spin.current;
    if (!tg || !sp) return;
    const a = anim.current;
    if (a) {
      a.t = Math.min(a.t + dt, FOCUS_S);
      const k = ease(a.t / FOCUS_S);
      tg.rotation.x = a.from[0] + (a.to[0] - a.from[0]) * k;
      tg.rotation.z = a.from[1] + (a.to[1] - a.from[1]) * k;
      sp.rotation.y = a.from[2] + (a.to[2] - a.from[2]) * k;
      if (a.t >= FOCUS_S) anim.current = null;
    }
    // Auto-rotation only while nothing is focused.
    if (fLat === undefined && !reduced) sp.rotation.y += (Math.PI * 2 * dt) / TURN_S;
  });

  return (
    <group ref={tilt} rotation={[0, 0, TILT]}>
      <group ref={spin} rotation={[0, startLon === null ? AFRICA_Y : spinFor(startLon), 0]}>
        <mesh material={material}>
          <sphereGeometry args={[1, 128, 128]} />
        </mesh>
        {pin ? <Pin at={pin} /> : null}
      </group>
    </group>
  );
}

export function EarthHero({
  className,
  focus = null,
  pin = null,
  startLon = null,
  shadow = true,
  scan = false,
  zoom = false,
}: {
  className?: string;
  /** When set, the globe eases (~1.2 s) to face this point and stops auto-rotating. */
  focus?: LatLon | null;
  /** A small surface marker. */
  pin?: LatLon | null;
  /** Longitude facing the viewer at mount; default shows Africa and Europe. */
  startLon?: number | null;
  /** Soft contact shadow under the planet. */
  shadow?: boolean;
  /** When it turns true, one bright scan band sweeps across the disc (~1.3 s). */
  scan?: boolean;
  /** Dolly the camera in (~1.2 s) towards the point facing the viewer. */
  zoom?: boolean;
}) {
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
      {shadow ? (
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
      ) : null}
      <Canvas
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
        dpr={[1, 2]}
        camera={{ fov: 28, position: [0, 0, CAM_Z], near: 0.01, far: 20 }}
        gl={{ alpha: true, antialias: true, toneMapping: ACESFilmicToneMapping, outputColorSpace: SRGBColorSpace, toneMappingExposure: 1.05 }}
        onCreated={({ gl }) => gl.setClearColor(0x000000, 0)}
      >
        <Suspense fallback={null}>
          <Planet reduced={reduced} focus={focus} pin={pin} startLon={startLon} scan={scan} zoom={zoom} />
        </Suspense>
      </Canvas>
    </div>
  );
}

export default EarthHero;
