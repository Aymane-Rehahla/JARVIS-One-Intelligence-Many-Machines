/**
 * Single source of truth for the farm's world-space layout.
 *
 * Every number here is read off the primary reference, `smart poultry farm - layout reference (top view)`
 * (`assest to use/ea5d4595-….png`). That drawing is 80 px per metre with the building's outer walls at
 * px x 380…1380 and y 260…1500, so a plan pixel (px, py) maps to world metres as
 *
 *   x = (px - 880) / 80   (+X = right-hand side of the plan)
 *   z = (py - 880) / 80   (+Z = toward the FRONT, the bottom of the plan; the barn is at -Z)
 *   y = height above the outside apron.
 *
 * The scene is never mirrored: what is on the right of the plan is on +X in the world.
 */

export type Vec3 = [number, number, number]
export type Rect = { x0: number; x1: number; z0: number; z1: number }

/** Outer faces of the 12.5 × 15.5 m building and the two partition centre lines. */
export const BUILDING = { left: -6.25, right: 6.25, rear: -7.75, front: 7.75, partitionZ: 2.25, splitX: -0.25 } as const
export const WALL = { exterior: 0.2, interior: 0.15, top: 2.7, doorHeight: 2.1 } as const
/** Finished concrete floor inside the building, and the top of the barn's litter bed. */
export const FLOOR_Y = 0.1
export const LITTER_Y = 0.14

/** Usable floor of each room, inside the wall faces. */
export const ROOMS = {
  barn: { x0: -6.05, x1: 6.05, z0: -7.55, z1: 2.175 },
  incubation: { x0: -6.05, x1: -0.325, z0: 2.325, z1: 7.55 },
  service: { x0: -0.175, x1: 6.05, z0: 2.325, z1: 7.55 },
} satisfies Record<string, Rect>

/** Closed roll-up gate in the rear wall (plan px 980…1220). Kept below the rear duct; the roll box is outside. */
export const GATE = { x0: 1.25, x1: 4.25, height: 2.0 } as const

/**
 * Doors, with the hinge and the side each leaf swings into as drawn on the plan.
 * `along` is the axis the opening runs along; `swing` is the sign of the axis the leaf opens toward.
 */
export const DOORS = {
  barn: { along: "x", at: BUILDING.partitionZ, from: 3.35, to: 4.35, hinge: 3.35, swing: -1 },
  cleanRoom: { along: "z", at: BUILDING.splitX, from: 5.75, to: 6.75, hinge: 5.75, swing: 1 },
  front: { along: "x", at: BUILDING.front - WALL.exterior / 2, from: 1.75, to: 2.75, hinge: 1.75, swing: -1 },
} as const

/* ------------------------------------------------------------------ barn */

export const FEEDER = { z: -2.75, startX: -4.75, panXs: Array.from({ length: 13 }, (_, i) => -4.45 + i * 0.75), panRadius: 0.165, tubeY: 0.52 }
export const HOPPER = { x: 5.1, z: -2.75, size: 0.7 }
export const DRINKERS = { zs: [-5.25, -0.25], regulatorX: -5.25, endX: 4.75, nippleXs: Array.from({ length: 19 }, (_, i) => -4.75 + i * 0.5), lineY: 0.46, standXs: [-4.95, -1.5, 1.75, 4.6] }
/** Water main inside the barn, fixed low along the left wall (plan px x 408). */
export const WATER_MAIN_X = -5.92
export const WATER_ENTRY_Z = 1.25
/** Animal-protection cabinet against the right wall, and its edge PC just to the rear of it. */
export const CABINET: Rect & { height: number } = { x0: 5.7, x1: 6.05, z0: 0.15, z1: 1.35, height: 1.7 }
export const PROTECTION_NODE: Vec3 = [5.98, 1.45, -0.1]

/** Initial broiler positions, one per circle on the plan (24). */
export const CHICKEN_STARTS: ReadonlyArray<readonly [number, number]> = [
  [-2.55, -6.05], [0.25, -5.95], [3.15, -5.95],
  [-3.95, -4.55], [-1.05, -4.45], [1.75, -4.55], [4.35, -4.65],
  [-3.35, -3.55], [-1.35, -3.45], [0.55, -3.65], [2.35, -3.45], [4.15, -3.55],
  [-4.35, -1.95], [-2.45, -2.05], [-0.45, -1.85], [1.65, -2.05], [3.35, -1.95],
  [-2.85, -0.85], [-0.15, -0.95], [2.65, -0.85],
  [-4.25, 0.25], [-1.65, 0.35], [1.25, 0.15], [3.95, -0.05],
]

/* ------------------------------------------------------------ front rooms */

/** Incubators stand on the floor with their backs to the partition and their doors facing the room (+Z). */
export const INCUBATORS = [{ id: "incubator-1", x: -4.65, z: 3.15 }, { id: "incubator-2", x: -3.05, z: 3.15 }] as const
export const INCUBATOR_FOOTPRINT = 0.8
export const BENCH: Rect = { x0: -1.0, x1: -0.4, z0: 3.35, z1: 5.15 }
export const SHELVING: Rect = { x0: 0.15, x1: 2.15, z0: 2.35, z1: 2.77 }
export const FEED_BIN = { x: 2.7, z: 3.15, radius: 0.31 }
export const FOOTBATH: Rect = { x0: 3.4, x1: 4.3, z0: 2.4, z1: 3.05 }
export const BOOT_BENCH: Rect = { x0: 4.55, x1: 5.55, z0: 2.38, z1: 2.76 }
export const ELECTRICAL: Rect = { x0: 5.8, x1: 6.05, z0: 4.15, z1: 5.15 }

/* --------------------------------------------------------------- HVAC */

/** Duct centre lines, high on the walls. */
export const DUCT = {
  y: 2.25,
  size: 0.3,
  rearZ: -7.38,
  rearFromX: -5.25,
  trunkX: 5.88,
  /** Outside the front wall: where the trunk turns down toward the outdoor unit. */
  outsideZ: 8.2,
  frontRoomZ: 2.5,
  frontRoomFromX: -5.65,
  serviceBranchZ: 6.35,
  serviceBranchToX: 4.9,
  rearDiffusers: [-3.75, -0.5, 2.75],
  trunkDiffusers: [-3.75, -0.75],
  incubationDiffusers: [-4.65, -1.65],
  serviceDiffuserX: 5.05,
  dampers: { barn: [5.88, 1.875] as const, incubation: [-0.65, 2.5] as const, service: [5.45, 6.35] as const },
}
export const HVAC_PAD: Rect = { x0: 4.15, x1: 6.35, z0: 8.25, z1: 10.25 }
export const HVAC_UNIT = { x: 5.25, z: 9.25, width: 1.8, depth: 1.4 }
/** Edge PC fixed to the left-hand face of the unit's cabinet (x = 4.475). */
export const HVAC_NODE: Vec3 = [4.44, 0.75, 9.25]

/* ------------------------------------------------------------- outside */

export const APRON: Rect = { x0: -7.05, x1: 7.05, z0: -8.55, z1: 8.55 }
export const TANK_PAD: Rect = { x0: -8.55, x1: -6.55, z0: 0.05, z1: 1.65 }
export const TANK = { x: -7.85, z: 0.85, radius: 0.55, height: 1.2 }
export const PUMP = { x: -6.85, z: 1.25 }
/** Incubator water feed: leaves the pump, runs along the outside of the left wall and in behind the incubators. */
export const INCUBATOR_WATER_Z = 2.675
export const INCUBATOR_WATER_END_X = -2.65
export const WEATHER_STATION = { x: -7.55, z: -8.85, mast: 6 }
