// Pure explorer logic (PRD 8.6, 8.10, 10): URL state, keyboard reducer, eligibility, tile frame.
import { describe, expect, it } from 'vitest';

import { eligibilityTone, summariseWindows } from '../components/map/eligibility.ts';
import { parseEarthView, serializeEarthView } from '../components/map/earth-state.ts';
import { buildBodyData } from '../components/map/explore-data.ts';
import { scaleGradient } from '../components/map/colour-scales.ts';
import { fromWorld, resolveLayers, tileBounds, tileUrl, toWorld } from '../components/map/tile-frame.ts';
import { applyKey, DEFAULT_VIEW, parseViewState, serializeViewState } from '../components/map/view-state.ts';
import { testBundle, testLayer, testTarget, testWindow } from './explore-fixtures.ts';

describe('view state in the URL', () => {
  it('round-trips centre, zoom, layers and target', () => {
    const view = { x: -123.5, y: 4567.5, zoom: 3.5, layers: ['a', 'b-c'], target: 'moon-test-target' };
    expect(parseViewState(serializeViewState(view))).toEqual(view);
  });
  it('falls back without throwing on an invalid query', () => {
    for (const q of ['x=abc&y=1&z=2', 'x=1', 'z=Infinity', 'layers=../etc', 'target=Bad', '%%%', 'z=1e99']) {
      expect(() => parseViewState(q)).not.toThrow();
    }
    expect(parseViewState('x=abc&y=1&z=2')).toEqual({ ...DEFAULT_VIEW, zoom: 2 });
    expect(parseViewState('layers=../etc').layers).toBeNull();
    expect(parseViewState('z=1e99').zoom).toBeNull();
  });
  it('round-trips the Earth family and rejects an unknown one', () => {
    expect(parseEarthView(serializeEarthView({ family: 'terrain_texture', target: 'mars-test' }))).toEqual({
      family: 'terrain_texture',
      target: 'mars-test',
    });
    expect(parseEarthView('family=score').family).toBe('terrain_relief');
  });
});

describe('keyboard controls', () => {
  const camera = { x: 0, y: 0, zoom: 1 };
  const range = { min: 0, max: 5 };
  it('pans with arrows, a fixed number of pixels', () => {
    expect(applyKey(camera, 'ArrowRight', 4, range)).toMatchObject({ x: 160, y: 0 });
    expect(applyKey(camera, 'ArrowLeft', 4, range)).toMatchObject({ x: -160 });
    expect(applyKey(camera, 'ArrowUp', 4, range)).toMatchObject({ y: 160 });
    expect(applyKey(camera, 'ArrowDown', 4, range)).toMatchObject({ y: -160 });
  });
  it('zooms with plus and minus within the range and resets with Home', () => {
    expect(applyKey(camera, '+', 4, range)?.zoom).toBe(1.5);
    expect(applyKey({ ...camera, zoom: 5 }, '+', 4, range)?.zoom).toBe(5);
    expect(applyKey({ ...camera, zoom: 0 }, '-', 4, range)?.zoom).toBe(0);
    expect(applyKey(camera, 'Home', 4, range)).toBeNull();
  });
});

describe('eligibility', () => {
  it('derives the colour class from status and keeps reasons', () => {
    expect(eligibilityTone('eligible').className).not.toBe(eligibilityTone('rejected').className);
    expect(eligibilityTone('rejected').dash).toBe(true);
    const s = summariseWindows([
      { status: 'rejected', reasons: ['too_steep'] },
      { status: 'rejected', reasons: ['too_steep', 'no_data'] },
      { status: 'eligible', reasons: [] },
    ]);
    expect(s.total).toBe(3);
    expect(s.byStatus).toEqual({ eligible: 1, rejected: 2, unknown: 0 });
    expect(s.reasons[0]).toEqual(['too_steep', 2]);
  });
});

describe('tile frame', () => {
  it('addresses tiles from the layer record and round-trips coordinates', () => {
    const { frame, drawable } = resolveLayers([testLayer()]);
    expect(drawable).toHaveLength(1);
    expect(frame).not.toBeNull();
    if (frame === null) return;
    expect(frame.metresPerUnit).toBe(4);
    const w = toWorld(frame, [-1000, 1000]);
    expect(w).toEqual([0, 0]);
    expect(fromWorld(frame, toWorld(frame, [123, -456]))).toEqual([123, -456]);
    expect(tileUrl('https://assets.test/', 'v1', 'test-hillshade', { z: 1, x: 2, y: 3 })).toBe(
      'https://assets.test/v1/tiles/test-hillshade/1/2/3.webp',
    );
    expect(tileBounds(frame, { left: 0, top: 1, right: 2, bottom: 3 })).toEqual([0, 3, 2, 1]);
  });
  it('reports a non-dyadic pyramid and a disagreeing frame as unavailable', () => {
    const bad = testLayer({ layer: 'bad', metres_per_pixel_by_zoom: [4, 3, 1] });
    const shifted = testLayer({ layer: 'shifted', origin_x_m: 5 });
    const r = resolveLayers([testLayer(), bad, shifted]);
    expect(r.drawable.map((l) => l.layer)).toEqual(['test-hillshade']);
    expect(r.problems.map((p) => p.layer)).toEqual(['bad', 'shifted']);
  });
  it('draws a numeric legend on viridis or cividis only', () => {
    expect(scaleGradient('viridis')).toContain('rgb(68 1 84)');
    expect(scaleGradient('cividis')).toContain('rgb(0 32 77)');
  });
});

describe('body data from records', () => {
  it('reports a missing targets.json', () => {
    expect(buildBodyData(testBundle(), 'moon').targetsMissing).toBe(true);
  });
  it('counts and numbers only the selected target\'s windows of a bodywide file', () => {
    const windows = [
      testWindow('moon-test-a-x0-y0', 'eligible', []),
      testWindow('moon-test-a-x1-y0', 'eligible', [], 10),
      testWindow('moon-test-b-x0-y0', 'eligible', []),
    ];
    const target = testTarget({ primary_window_id: 'moon-test-a-x0-y0' });
    const data = buildBodyData(testBundle({ targets: target, windows: new Map([['moon', windows]]) }), 'moon');
    expect(data.targets[0]?.windows.map((w) => w.id)).toEqual(['moon-test-a-x0-y0', 'moon-test-a-x1-y0']);
  });

  it('keeps windows with their status and reasons, and counts the actual windows', () => {
    const windows = [
      testWindow('moon-test-window-a', 'eligible', []),
      testWindow('moon-test-window-b', 'rejected', ['too_steep'], 200),
      testWindow('moon-test-window-far', 'eligible', [], 99999),
    ];
    const data = buildBodyData(testBundle({ targets: testTarget(), windows: new Map([['moon', windows]]) }), 'moon');
    const t = data.targets[0];
    expect(t?.windows.map((w) => w.id)).toEqual(['moon-test-window-a', 'moon-test-window-b']);
    expect(t?.windows[1]).toMatchObject({ status: 'rejected', reasons: ['too_steep'] });
    expect(t?.nu.kind).toBe('unavailable');
  });
});
