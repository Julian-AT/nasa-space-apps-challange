// Explorer view state (PRD 8.6, 8.10): the map centre in projected metres, the zoom and the active
// layers live in the URL query (`x`, `y`, `z`, `layers`, `target`), so a view can be shared. A
// missing or invalid parameter falls back to "unset", which the map resolves to its fitted view;
// parsing never throws. The keyboard reducer is pure so it can be tested without a browser.

export interface ViewState {
  /** Centre easting in the target frame's metres, or null for the fitted view. */
  readonly x: number | null;
  readonly y: number | null;
  /** Zoom as the pyramid level: one screen pixel is one tile pixel at that level. */
  readonly zoom: number | null;
  /** Active layer names; null means the default set. */
  readonly layers: readonly string[] | null;
  readonly target: string | null;
}

export const DEFAULT_VIEW: ViewState = { x: null, y: null, zoom: null, layers: null, target: null };

/** A concrete (resolved) camera, in metres and zoom. */
export interface Camera {
  readonly x: number;
  readonly y: number;
  readonly zoom: number;
}

const LAYER_NAME = /^[A-Za-z0-9_-][A-Za-z0-9._-]*$/u;
const TARGET_ID = /^(moon|mars|earth)-[a-z0-9][a-z0-9-]*$/u;
const ZOOM_LIMIT = 40;

type Query = URLSearchParams | string | Readonly<Record<string, string | readonly string[] | undefined>>;

function read(query: Query, key: string): string | null {
  if (typeof query === 'string') return new URLSearchParams(query).get(key);
  if (query instanceof URLSearchParams) return query.get(key);
  const value = query[key];
  if (Array.isArray(value)) return (value[0] as string | undefined) ?? null;
  return typeof value === 'string' ? value : null;
}

function finite(text: string | null): number | null {
  if (text === null || text.trim() === '') return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

/** Parses the query; a pair that is incomplete or invalid falls back to the fitted view. */
export function parseViewState(query: Query): ViewState {
  const x = finite(read(query, 'x'));
  const y = finite(read(query, 'y'));
  const z = finite(read(query, 'z'));
  const centred = x !== null && y !== null;
  const layersText = read(query, 'layers');
  const names = layersText === null ? null : layersText.split(',').filter((name) => name !== '');
  const layers = names !== null && names.every((name) => LAYER_NAME.test(name)) ? names : null;
  const target = read(query, 'target');
  return {
    x: centred ? x : null,
    y: centred ? y : null,
    zoom: z !== null && Math.abs(z) <= ZOOM_LIMIT ? z : null,
    layers,
    target: target !== null && TARGET_ID.test(target) ? target : null,
  };
}

function trim(value: number, digits: number): string {
  return String(Number(value.toFixed(digits)));
}

/** The query string (no leading "?") that parseViewState restores; unset fields are omitted. */
export function serializeViewState(view: ViewState): string {
  const params = new URLSearchParams();
  if (view.target !== null) params.set('target', view.target);
  if (view.x !== null && view.y !== null) {
    params.set('x', trim(view.x, 1));
    params.set('y', trim(view.y, 1));
  }
  if (view.zoom !== null) params.set('z', trim(view.zoom, 3));
  if (view.layers !== null) params.set('layers', view.layers.join(','));
  return params.toString().replaceAll('%2C', ',');
}

export const PAN_STEP_PX = 80;
export const ZOOM_STEP = 0.5;

export type MapKey = 'ArrowUp' | 'ArrowDown' | 'ArrowLeft' | 'ArrowRight' | '+' | '=' | '-' | '_' | 'Home';

const MAP_KEYS: ReadonlySet<string> = new Set([
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
  '+',
  '=',
  '-',
  '_',
  'Home',
]);

export function isMapKey(key: string): key is MapKey {
  return MAP_KEYS.has(key);
}

export interface ZoomRange {
  readonly min: number;
  readonly max: number;
}

/**
 * The camera after a key press, or null for Home (back to the fitted view). `metresPerPixelAtZoom0`
 * comes from the layer's own pyramid, so a pan step is a fixed number of screen pixels at any zoom.
 */
export function applyKey(
  camera: Camera,
  key: MapKey,
  metresPerPixelAtZoom0: number,
  range: ZoomRange,
): Camera | null {
  if (key === 'Home') return null;
  const clamp = (zoom: number) => Math.min(range.max, Math.max(range.min, zoom));
  const step = PAN_STEP_PX * (metresPerPixelAtZoom0 / 2 ** camera.zoom);
  switch (key) {
    case 'ArrowUp':
      return { ...camera, y: camera.y + step };
    case 'ArrowDown':
      return { ...camera, y: camera.y - step };
    case 'ArrowLeft':
      return { ...camera, x: camera.x - step };
    case 'ArrowRight':
      return { ...camera, x: camera.x + step };
    case '+':
    case '=':
      return { ...camera, zoom: clamp(camera.zoom + ZOOM_STEP) };
    case '-':
    case '_':
      return { ...camera, zoom: clamp(camera.zoom - ZOOM_STEP) };
  }
}
