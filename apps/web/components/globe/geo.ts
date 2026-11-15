// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// Lat/lon helpers for the globe. Pin positions come from bundle polygons, never typed in.

/** Unit-sphere position for a planetocentric lat/lon in degrees (three.js axes, +y north). */
export function latLonToVec3(lat: number, lon: number, r = 1): [number, number, number] {
  const phi = ((90 - lat) * Math.PI) / 180;
  const theta = ((lon + 180) * Math.PI) / 180;
  return [-r * Math.sin(phi) * Math.cos(theta), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(theta)];
}

/**
 * Mean of a closed polygon ring's vertices (the repeated closing vertex is dropped). The windows
 * are a few km across, so the vertex mean is the polygon centre at globe scale. A ring that
 * straddles the antimeridian is averaged on the unit circle.
 */
export function ringCentroid(ring: readonly (readonly number[])[]): { lat: number; lon: number } | null {
  const pts = ring.length > 1 && ring[0]?.[0] === ring.at(-1)?.[0] && ring[0]?.[1] === ring.at(-1)?.[1] ? ring.slice(0, -1) : ring;
  if (pts.length === 0) return null;
  let x = 0;
  let y = 0;
  let lat = 0;
  for (const pt of pts) {
    const lon = pt[0] ?? 0;
    const la = pt[1] ?? 0;
    const r = (lon * Math.PI) / 180;
    x += Math.cos(r);
    y += Math.sin(r);
    lat += la;
  }
  return { lat: lat / pts.length, lon: (Math.atan2(y, x) * 180) / Math.PI };
}
