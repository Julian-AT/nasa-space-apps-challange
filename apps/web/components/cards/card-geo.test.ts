import { describe, expect, it } from 'vitest';

import { earthRegionName, pickTile, projectorFor, type TileLayerGeo } from './card-geo.ts';

const earth: TileLayerGeo = {
  layer: 'earth-holuhraun-hillshade',
  crs: 'EPSG:3857',
  body: 'earth',
  tile_px: 512,
  origin_x_m: -20037508.342789244,
  origin_y_m: 20037508.342789244,
  metres_per_pixel_by_zoom: [611.49622628141, 305.748113140705, 152.8740565703525, 76.43702828517625],
  zoom_min: 7,
  zoom_max: 10,
};

describe('card-geo', () => {
  it('projects lon/lat to Web Mercator', () => {
    const p = projectorFor('EPSG:3857')?.(0, 0);
    expect(p?.[0]).toBeCloseTo(0, 6);
    expect(p?.[1]).toBeCloseTo(0, 6);
  });

  it('projects the south pole to the polar stereographic origin', () => {
    const crs = 'PROJCS["u",GEOGCS["u",DATUM["u",SPHEROID["u",1737400,0]]],PROJECTION["Polar_Stereographic"],PARAMETER["latitude_of_origin",-90],PARAMETER["central_meridian",0]]';
    const p = projectorFor(crs)?.(10, -90);
    expect(Math.abs(p?.[0] ?? 1)).toBeLessThan(1e-6);
    const q = projectorFor(crs)?.(-136.97, -89.3234);
    expect(q?.[0]).toBeGreaterThan(-19000);
    expect(q?.[0]).toBeLessThan(-3000);
    expect(q?.[1]).toBeGreaterThan(-20000);
    expect(q?.[1]).toBeLessThan(-4000);
  });

  it('puts the window inside its square view and lists overlapping tiles', () => {
    const ring = [[-16.78, 64.86], [-16.67, 64.86], [-16.67, 64.9], [-16.78, 64.9]];
    const pick = pickTile(earth, ring, () => true);
    expect(pick).not.toBeNull();
    expect(pick?.box.left).toBeGreaterThan(0);
    expect((pick?.box.left ?? 0) + (pick?.box.width ?? 0)).toBeLessThan(1);
    expect(pick?.tiles.length).toBeGreaterThan(0);
    expect(pickTile(earth, ring, () => false)).toBeNull();
  });

  it('names Earth regions and falls back to the id', () => {
    expect(earthRegionName('holuhraun')).toBe('Holuhraun, Iceland');
    expect(earthRegionName('some-place')).toBe('Some place');
  });
});
