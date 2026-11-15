// Which layers an explorer view expects per body (PRD 10), matched on the bundle's layer names.
// A role with no layer in TargetRecord.tile_layers is listed as Unavailable with a reason; it is
// never drawn as an empty map. The permanent-shadow product is not named until G0-10 (PRD 6.1).
import type { TileLayerRecord } from './tile-frame.ts';

export interface LayerRole {
  readonly id: string;
  readonly title: string;
  readonly pattern: RegExp;
  /** Why the role has no layer when the bundle holds none. */
  readonly absentReason: string;
}

const MOON_ROLES: readonly LayerRole[] = [
  {
    id: 'lola',
    title: 'LOLA hillshade / PGDA terrain',
    pattern: /lola|pgda|hillshade|dem|slope/iu,
    absentReason: 'no terrain tile layer in this target’s tile_layers',
  },
  {
    id: 'cpr',
    title: 'Mini-RF CPR with validity mask',
    pattern: /cpr|mini[-_]?rf|minirf|radar/iu,
    absentReason: 'no Mini-RF CPR tile layer in this target’s tile_layers',
  },
  {
    id: 'shadow',
    title: 'Permanent shadow',
    pattern: /shadow|psr/iu,
    absentReason:
      'no permanent-shadow tile layer in this target’s tile_layers; the product is not named until G0-10 decides it',
  },
];

const MARS_ROLES: readonly LayerRole[] = [
  {
    id: 'terrain',
    title: 'CTX hillshade / terrain',
    pattern: /ctx|hillshade|dem|mola|hirise|slope|terrain/iu,
    absentReason: 'no terrain tile layer in this target’s tile_layers',
  },
];

export function rolesFor(body: 'moon' | 'mars'): readonly LayerRole[] {
  return body === 'moon' ? MOON_ROLES : MARS_ROLES;
}

export interface RoleStatus {
  readonly role: LayerRole;
  readonly layers: readonly TileLayerRecord[];
}

export function roleStatuses(body: 'moon' | 'mars', layers: readonly TileLayerRecord[]): RoleStatus[] {
  return rolesFor(body).map((role) => ({ role, layers: layers.filter((l) => role.pattern.test(l.layer)) }));
}

/** Layers matching no role are still the bundle's own and are shown under "Other layers". */
export function unmatchedLayers(body: 'moon' | 'mars', layers: readonly TileLayerRecord[]): TileLayerRecord[] {
  const roles = rolesFor(body);
  return layers.filter((l) => !roles.some((r) => r.pattern.test(l.layer)));
}
