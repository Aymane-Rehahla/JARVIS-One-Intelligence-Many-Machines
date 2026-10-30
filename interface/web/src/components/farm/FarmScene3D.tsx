import { Component, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { Canvas, useFrame, useThree } from "@react-three/fiber"
import { OrbitControls, OrthographicCamera, PerspectiveCamera, useGLTF, useProgress } from "@react-three/drei"
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib"
import * as THREE from "three"
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js"
import { Button } from "@/components/ui/button"
import { useReducedMotion } from "@/hooks/use-reduced-motion"
import type { Device, DeviceId } from "@/services/matrix"
import { Chickens } from "./chickens"
import {
  APRON, BENCH, BOOT_BENCH, BUILDING, CABINET, DOORS, DRINKERS, DUCT, ELECTRICAL, FEED_BIN, FEEDER, FLOOR_Y, FOOTBATH, GATE, HOPPER,
  HVAC_NODE, HVAC_PAD, HVAC_UNIT, INCUBATOR_FOOTPRINT, INCUBATOR_WATER_END_X, INCUBATOR_WATER_Z, INCUBATORS, LITTER_Y, PROTECTION_NODE,
  PUMP, ROOMS, SHELVING, TANK, TANK_PAD, WALL, WATER_ENTRY_Z, WATER_MAIN_X, WEATHER_STATION, type Rect, type Vec3,
} from "./layout"

type Props = { devices: Device[]; onDevice: (device: Device) => void }

const C = { wall: "#eef2f4", trim: "#1c1c1c", apron: "#a3abb0", ground: "#d6d6d6", floor: "#c3c8cb", galvanised: "#aab4ba", water: "#2f7fd8", red: "#d33b32", duct: "#c3ccd2", steel: "#9aa5ad", dark: "#2f3a44" }

const INCUBATOR_URL = "/assets/farm/incubatormodel.glb"
const HVAC_URL = "/assets/farm/hvac.glb"

function deviceState(device: Device): "OFF" | "ALIVE" | "ALARM" {
  if (!device.running || device.operational === "unknown") return "OFF"
  return device.operational === "normal" ? "ALIVE" : "ALARM"
}
function lightColor(device: Device) { return deviceState(device) === "ALIVE" ? "#22c55e" : deviceState(device) === "ALARM" ? "#ef4444" : "#64748b" }
function byId(devices: Device[], id: DeviceId) { const device = devices.find((item) => item.id === id); if (!device) throw new Error(`Missing ${id}`); return device }

/* ------------------------------------------------------------------ primitives */

function Box({ position, size, color, rotation = [0, 0, 0], metalness = 0, roughness = 0.7, map }: { position: Vec3; size: Vec3; color: string; rotation?: Vec3; metalness?: number; roughness?: number; map?: THREE.Texture }) {
  return <mesh position={position} rotation={rotation} castShadow receiveShadow><boxGeometry args={size} /><meshStandardMaterial color={color} metalness={metalness} roughness={roughness} map={map} /></mesh>
}
/** A box given by its plan rectangle and its bottom/top heights. */
function Block({ rect, y0, y1, ...rest }: { rect: Rect; y0: number; y1: number } & Omit<Parameters<typeof Box>[0], "position" | "size">) {
  return <Box position={[(rect.x0 + rect.x1) / 2, (y0 + y1) / 2, (rect.z0 + rect.z1) / 2]} size={[rect.x1 - rect.x0, y1 - y0, rect.z1 - rect.z0]} {...rest} />
}
function Cylinder({ position, radius, height, color, top = radius, segments = 28, metalness = 0.15, roughness = 0.55 }: { position: Vec3; radius: number; height: number; color: string; top?: number; segments?: number; metalness?: number; roughness?: number }) {
  return <mesh position={position} castShadow receiveShadow><cylinderGeometry args={[top, radius, height, segments]} /><meshStandardMaterial color={color} roughness={roughness} metalness={metalness} /></mesh>
}
function Pipe({ from, to, color, radius = 0.025, metalness = 0.3 }: { from: Vec3; to: Vec3; color: string; radius?: number; metalness?: number }) {
  const a = new THREE.Vector3(...from), b = new THREE.Vector3(...to), delta = b.clone().sub(a)
  return <mesh position={a.clone().add(b).multiplyScalar(0.5)} quaternion={new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), delta.clone().normalize())} castShadow><cylinderGeometry args={[radius, radius, delta.length() + radius, 12]} /><meshStandardMaterial color={color} metalness={metalness} roughness={0.4} /></mesh>
}
/** Pipe through a list of points, with a joint at each bend. */
function PipeRun({ points, color, radius = 0.025 }: { points: Vec3[]; color: string; radius?: number }) {
  return <group>{points.slice(1).map((point, index) => <Pipe key={index} from={points[index]} to={point} color={color} radius={radius} />)}{points.slice(1, -1).map((point, index) => <mesh key={`j${index}`} position={point}><sphereGeometry args={[radius * 1.25, 10, 10]} /><meshStandardMaterial color={color} metalness={0.3} roughness={0.4} /></mesh>)}</group>
}

function useCanvasTexture(draw: (context: CanvasRenderingContext2D, size: number) => void, repeat: [number, number], size = 512) {
  return useMemo(() => {
    const canvas = document.createElement("canvas"); canvas.width = canvas.height = size
    const context = canvas.getContext("2d"); if (context) draw(context, size)
    const texture = new THREE.CanvasTexture(canvas); texture.wrapS = texture.wrapT = THREE.RepeatWrapping; texture.repeat.set(...repeat); texture.colorSpace = THREE.SRGBColorSpace; texture.anisotropy = 8
    return texture
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}

/* ------------------------------------------------------------------ devices */

function StatusLight({ device, position }: { device: Device; position: Vec3 }) {
  const lamp = useRef<THREE.Mesh>(null), alarm = deviceState(device) === "ALARM", color = lightColor(device), reduced = useReducedMotion()
  // Under reduced motion the alarm lamp stays lit instead of blinking.
  useFrame(({ clock }) => { if (lamp.current) lamp.current.visible = !alarm || reduced || Math.sin(clock.elapsedTime * 7) > -0.2 })
  return <group position={position}>
    <mesh><cylinderGeometry args={[0.05, 0.05, 0.03, 16]} /><meshStandardMaterial color="#1f2937" /></mesh>
    <mesh ref={lamp} position={[0, 0.05, 0]}><sphereGeometry args={[0.06, 16, 16]} /><meshStandardMaterial color={color} emissive={color} emissiveIntensity={alarm ? 3.5 : 1.4} toneMapped={false} /></mesh>
    {alarm && <pointLight position={[0, 0.1, 0]} color="#ef4444" intensity={4} distance={3} />}
  </group>
}

/** A JARVIS edge PC: small enclosure fixed to the device or wall it serves, with its status beacon on top. Faces local +Z. */
function EdgeNode({ device, position, rotationY = 0, onDevice }: { device: Device; position: Vec3; rotationY?: number; onDevice: (device: Device) => void }) {
  return <group position={position} rotation={[0, rotationY, 0]} onClick={(event) => { event.stopPropagation(); onDevice(device) }} onPointerOver={() => { document.body.style.cursor = "pointer" }} onPointerOut={() => { document.body.style.cursor = "" }}>
    <Box position={[0, 0, 0]} size={[0.2, 0.26, 0.07]} color="#151b22" metalness={0.4} roughness={0.5} />
    <Box position={[0, 0.06, 0.036]} size={[0.12, 0.06, 0.004]} color="#0b3b52" />
    <StatusLight device={device} position={[0, 0.145, 0]} />
  </group>
}

/** A GLB re-based so its footprint is centred on the origin and its lowest point sits on y = 0. */
function useGroundedModel(url: string) {
  const gltf = useGLTF(url)
  return useMemo(() => {
    const clone = gltf.scene.clone(true)
    clone.traverse((item) => { if (item instanceof THREE.Mesh) { item.castShadow = true; item.receiveShadow = true } })
    const box = new THREE.Box3().setFromObject(clone), centre = box.getCenter(new THREE.Vector3())
    clone.position.x -= centre.x; clone.position.y -= box.min.y; clone.position.z -= centre.z
    return clone
  }, [gltf.scene])
}

/** Supplied equipment model, standing on `position` (its support surface). Geometry, materials and textures are kept as supplied. */
function Asset({ url, position, scale, rotationY = 0, device, onDevice, children }: { url: string; position: Vec3; scale: number; rotationY?: number; device?: Device; onDevice?: (device: Device) => void; children?: ReactNode }) {
  const model = useGroundedModel(url)
  return <group position={position} rotation={[0, rotationY, 0]} onClick={(event) => { if (device && onDevice) { event.stopPropagation(); onDevice(device) } }} onPointerOver={() => { if (device) document.body.style.cursor = "pointer" }} onPointerOut={() => { document.body.style.cursor = "" }}>
    <group scale={scale}><primitive object={model} /></group>{children}
  </group>
}

/* ------------------------------------------------------------------ building */

type Opening = { from: number; to: number; height: number }
/** A straight wall from `from` to `to` along one axis, centred on `at`, with openings cut to their head height. */
function WallRun({ along, at, from, to, thickness, base = 0, openings = [] }: { along: "x" | "z"; at: number; from: number; to: number; thickness: number; base?: number; openings?: Opening[] }) {
  const piece = (a: number, b: number, y0: number, y1: number, color: string, key: string) => {
    const mid = (a + b) / 2, length = b - a, height = y1 - y0
    return along === "x"
      ? <Box key={key} position={[mid, (y0 + y1) / 2, at]} size={[length, height, thickness]} color={color} roughness={0.85} metalness={color === C.trim ? 0.35 : 0} />
      : <Box key={key} position={[at, (y0 + y1) / 2, mid]} size={[thickness, height, length]} color={color} roughness={0.85} metalness={color === C.trim ? 0.35 : 0} />
  }
  const sorted = [...openings].sort((a, b) => a.from - b.from), solids: ReactNode[] = []
  let cursor = from
  sorted.forEach((opening, index) => {
    if (opening.from > cursor) solids.push(piece(cursor, opening.from, base, WALL.top, C.wall, `s${index}`))
    solids.push(piece(opening.from, opening.to, opening.height, WALL.top, C.wall, `l${index}`))
    cursor = opening.to
  })
  if (to > cursor) solids.push(piece(cursor, to, base, WALL.top, C.wall, "end"))
  return <group>{solids}{piece(from, to, WALL.top, WALL.top + 0.06, C.trim, "cap")}</group>
}

/** A closed door leaf in its opening, hinged where the plan draws it, with a handle on both faces at the free edge. */
function Door({ door, thickness }: { door: (typeof DOORS)[keyof typeof DOORS]; thickness: number }) {
  const width = door.to - door.from, free = door.hinge === door.from ? door.to - 0.12 : door.from + 0.12, mid = (door.from + door.to) / 2
  const along = (u: number, y: number, n: number): Vec3 => door.along === "x" ? [u, y, door.at + n] : [door.at + n, y, u]
  const leaf: Vec3 = door.along === "x" ? [width - 0.02, WALL.doorHeight - 0.02, 0.05] : [0.05, WALL.doorHeight - 0.02, width - 0.02]
  const frameSide: Vec3 = door.along === "x" ? [0.05, WALL.doorHeight, thickness + 0.03] : [thickness + 0.03, WALL.doorHeight, 0.05]
  const handle: Vec3 = door.along === "x" ? [0.14, 0.03, 0.03] : [0.03, 0.03, 0.14]
  return <group>
    <Box position={along(mid, WALL.doorHeight / 2, 0)} size={leaf} color="#dfe6ea" metalness={0.15} roughness={0.55} />
    <Box position={along(door.from + 0.025, WALL.doorHeight / 2, 0)} size={frameSide} color="#8e9aa3" metalness={0.4} />
    <Box position={along(door.to - 0.025, WALL.doorHeight / 2, 0)} size={frameSide} color="#8e9aa3" metalness={0.4} />
    {[-1, 1].map((side) => <Box key={side} position={along(free, 1.02, side * 0.05)} size={handle} color="#3c4852" metalness={0.7} />)}
  </group>
}

function Architecture() {
  const litter = useCanvasTexture((context, size) => {
    context.fillStyle = "#c9a26a"; context.fillRect(0, 0, size, size)
    for (let i = 0; i < 2600; i += 1) {
      const x = Math.random() * size, y = Math.random() * size, length = 3 + Math.random() * 9, angle = Math.random() * Math.PI, tone = Math.random()
      context.strokeStyle = tone < 0.45 ? `rgba(234,205,150,${0.5 + Math.random() * 0.4})` : tone < 0.8 ? `rgba(160,118,66,${0.45 + Math.random() * 0.4})` : `rgba(120,86,48,0.5)`
      context.lineWidth = 1 + Math.random() * 2; context.beginPath(); context.moveTo(x, y); context.lineTo(x + Math.cos(angle) * length, y + Math.sin(angle) * length); context.stroke()
    }
  }, [6, 5])
  const shutter = useCanvasTexture((context, size) => {
    context.fillStyle = "#9aa5ad"; context.fillRect(0, 0, size, size)
    for (let y = 0; y < size; y += 16) { context.fillStyle = "#7d8891"; context.fillRect(0, y, size, 3); context.fillStyle = "#b9c2c8"; context.fillRect(0, y + 3, size, 2) }
  }, [1, 1.4], 256)
  const ext = WALL.exterior, int = WALL.interior, { left, right, rear, front, partitionZ, splitX } = BUILDING
  return <group name="architecture">
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.03, 0]} receiveShadow><planeGeometry args={[80, 80]} /><meshStandardMaterial color={C.ground} roughness={1} /></mesh>
    <Block rect={APRON} y0={-0.15} y1={0} color={C.apron} roughness={0.95} />
    <Block rect={{ x0: left, x1: right, z0: rear, z1: front }} y0={-0.1} y1={FLOOR_Y} color={C.floor} roughness={0.9} />
    <Block rect={ROOMS.barn} y0={FLOOR_Y} y1={LITTER_Y} color="#ffffff" roughness={1} map={litter} />
    <Block rect={ROOMS.incubation} y0={FLOOR_Y} y1={FLOOR_Y + 0.004} color="#dfe5e8" roughness={0.55} />
    <Block rect={ROOMS.service} y0={FLOOR_Y} y1={FLOOR_Y + 0.004} color="#cdd2d5" roughness={0.75} />

    <WallRun along="x" at={rear + ext / 2} from={left} to={right} thickness={ext} openings={[{ from: GATE.x0, to: GATE.x1, height: GATE.height }]} />
    <WallRun along="x" at={front - ext / 2} from={left} to={right} thickness={ext} openings={[{ from: DOORS.front.from, to: DOORS.front.to, height: WALL.doorHeight }]} />
    <WallRun along="z" at={left + ext / 2} from={rear + ext} to={front - ext} thickness={ext} />
    <WallRun along="z" at={right - ext / 2} from={rear + ext} to={front - ext} thickness={ext} />
    {/* Barn | front rooms. The only opening is the service-room door: there is no door between the clean incubation room and the barn. */}
    <WallRun along="x" at={partitionZ} from={left + ext} to={right - ext} thickness={int} base={FLOOR_Y} openings={[{ from: DOORS.barn.from, to: DOORS.barn.to, height: WALL.doorHeight }]} />
    <WallRun along="z" at={splitX} from={partitionZ + int / 2} to={front - ext} thickness={int} base={FLOOR_Y} openings={[{ from: DOORS.cleanRoom.from, to: DOORS.cleanRoom.to, height: WALL.doorHeight }]} />
    <Door door={DOORS.barn} thickness={int} /><Door door={DOORS.cleanRoom} thickness={int} /><Door door={DOORS.front} thickness={ext} />

    {/* Closed roll-up gate, roll box on the outside face so it clears the rear duct. */}
    <Box position={[(GATE.x0 + GATE.x1) / 2, GATE.height / 2, rear + ext / 2]} size={[GATE.x1 - GATE.x0, GATE.height, 0.06]} color="#ffffff" metalness={0.5} roughness={0.45} map={shutter} />
    <Box position={[(GATE.x0 + GATE.x1) / 2, GATE.height + 0.17, rear - 0.17]} size={[GATE.x1 - GATE.x0 + 0.2, 0.34, 0.34]} color="#8a959d" metalness={0.55} roughness={0.4} />
    {[GATE.x0 - 0.04, GATE.x1 + 0.04].map((x) => <Box key={x} position={[x, GATE.height / 2, rear - 0.03]} size={[0.08, GATE.height, 0.08]} color="#7a868f" metalness={0.55} />)}
  </group>
}

function Diffuser({ position, vertical = false }: { position: Vec3; vertical?: boolean }) {
  return <group position={position}><Box position={[0, 0, 0]} size={vertical ? [0.03, 0.24, 0.24] : [0.26, 0.03, 0.26]} color="#eef3f6" metalness={0.2} />{[-0.07, 0, 0.07].map((offset) => <Box key={offset} position={vertical ? [-0.018, offset, 0] : [offset, -0.018, 0]} size={vertical ? [0.006, 0.012, 0.2] : [0.012, 0.006, 0.2]} color="#9aa7b0" />)}</group>
}
function Damper({ position, along }: { position: Vec3; along: "x" | "z" }) {
  const collar: Vec3 = along === "x" ? [0.1, DUCT.size + 0.06, DUCT.size + 0.06] : [DUCT.size + 0.06, DUCT.size + 0.06, 0.1]
  return <group position={position}><Box position={[0, 0, 0]} size={collar} color="#56636d" metalness={0.5} /><Box position={[0, DUCT.size / 2 + 0.07, 0]} size={[0.12, 0.08, 0.12]} color="#1d252c" metalness={0.3} /></group>
}

/** Supply ducting high on the walls: rear barn run, right-hand trunk to the outdoor unit, front-room run and service branch. */
function Ducting() {
  const s = DUCT.size, y = DUCT.y, half = s / 2, duct = (key: string, rect: Rect, y0 = y - half, y1 = y + half) => <Block key={key} rect={rect} y0={y0} y1={y1} color={C.duct} metalness={0.55} roughness={0.35} />
  const below = y - half - 0.016
  return <group name="hvac-ducting">
    {duct("rear", { x0: DUCT.rearFromX, x1: DUCT.trunkX - half, z0: DUCT.rearZ - half, z1: DUCT.rearZ + half })}
    {duct("trunk", { x0: DUCT.trunkX - half, x1: DUCT.trunkX + half, z0: DUCT.rearZ - half, z1: DUCT.outsideZ + half })}
    {duct("front-rooms", { x0: DUCT.frontRoomFromX, x1: DUCT.trunkX - half, z0: DUCT.frontRoomZ - half, z1: DUCT.frontRoomZ + half })}
    {duct("service", { x0: DUCT.serviceBranchToX, x1: DUCT.trunkX - half, z0: DUCT.serviceBranchZ - half, z1: DUCT.serviceBranchZ + half })}
    {/* Outside: down the front wall, then a round flexible duct along the back of the unit into its own inlet collar. */}
    {duct("drop", { x0: DUCT.trunkX - half, x1: DUCT.trunkX + half, z0: DUCT.outsideZ - half, z1: DUCT.outsideZ + half }, 1.45, y - half)}
    <PipeRun points={[[DUCT.trunkX, 1.5, DUCT.outsideZ], [DUCT.trunkX, 1.3, DUCT.outsideZ], [HVAC_COLLAR[0], 1.3, DUCT.outsideZ], [HVAC_COLLAR[0], 1.3, HVAC_COLLAR[2]], HVAC_COLLAR]} color="#b9c2c8" radius={0.1} />
    {DUCT.rearDiffusers.map((x) => <Diffuser key={`r${x}`} position={[x, below, DUCT.rearZ]} />)}
    {DUCT.trunkDiffusers.map((z) => <Diffuser key={`t${z}`} position={[DUCT.trunkX, below, z]} />)}
    {DUCT.incubationDiffusers.map((x) => <Diffuser key={`i${x}`} position={[x, below, DUCT.frontRoomZ]} />)}
    <Diffuser position={[DUCT.serviceDiffuserX, below, DUCT.serviceBranchZ]} />
    <Damper position={[DUCT.dampers.barn[0], y, DUCT.dampers.barn[1]]} along="z" />
    <Damper position={[DUCT.dampers.incubation[0], y, DUCT.dampers.incubation[1]]} along="x" />
    <Damper position={[DUCT.dampers.service[0], y, DUCT.dampers.service[1]]} along="x" />
    {/* Wall brackets. */}
    {[-4.5, -1.5, 1.5, 4.5].map((x) => <Box key={`b${x}`} position={[x, y - half - 0.02, DUCT.rearZ - 0.02]} size={[0.04, 0.04, s + 0.04]} color="#56636d" metalness={0.5} />)}
    {[-6, -3, 0, 3, 5.2, 7].map((z) => <Box key={`bt${z}`} position={[DUCT.trunkX + 0.02, y - half - 0.02, z]} size={[s + 0.04, 0.04, 0.04]} color="#56636d" metalness={0.5} />)}
  </group>
}

/* ------------------------------------------------------------------ barn */

function feederPanGeometry() {
  return new THREE.LatheGeometry([new THREE.Vector2(0, 0), new THREE.Vector2(0.12, 0), new THREE.Vector2(FEEDER.panRadius, 0.055), new THREE.Vector2(FEEDER.panRadius - 0.012, 0.062), new THREE.Vector2(0.11, 0.012), new THREE.Vector2(0.05, 0.03), new THREE.Vector2(0.03, 0.2), new THREE.Vector2(0, 0.2)], 28)
}
function Feeder() {
  const pan = useMemo(feederPanGeometry, []), z = FEEDER.z, y = FEEDER.tubeY
  return <group name="feeder-line">
    <Pipe from={[FEEDER.startX, y, z]} to={[HOPPER.x - HOPPER.size / 2, y, z]} color={C.galvanised} radius={0.03} metalness={0.6} />
    {/* End stand at the far end of the line. */}
    <Pipe from={[FEEDER.startX - 0.05, FLOOR_Y, z]} to={[FEEDER.startX - 0.05, y, z]} color={C.galvanised} radius={0.018} metalness={0.6} />
    <Box position={[FEEDER.startX - 0.05, LITTER_Y + 0.005, z]} size={[0.3, 0.01, 0.3]} color="#7f8a91" metalness={0.5} />
    {FEEDER.panXs.map((x) => <group key={x} position={[x, LITTER_Y, z]}>
      <mesh geometry={pan} castShadow receiveShadow><meshStandardMaterial color={C.red} roughness={0.5} side={THREE.DoubleSide} /></mesh>
      <Pipe from={[0, 0.2, 0]} to={[0, y - LITTER_Y, 0]} color="#8f9aa1" radius={0.022} metalness={0.5} />
      {[0, 1, 2, 3].map((index) => <Pipe key={index} from={[0, 0.2, 0]} to={[Math.cos(index * Math.PI / 2) * 0.14, 0.06, Math.sin(index * Math.PI / 2) * 0.14]} color="#b52c26" radius={0.006} />)}
    </group>)}
    {/* Hopper on four legs at the end of the line. */}
    <group position={[HOPPER.x, 0, HOPPER.z]}>
      {[-1, 1].flatMap((sx) => [-1, 1].map((sz) => <Pipe key={`${sx}${sz}`} from={[sx * 0.3, FLOOR_Y, sz * 0.3]} to={[sx * 0.3, 0.8, sz * 0.3]} color="#7f8a91" radius={0.025} metalness={0.6} />))}
      <mesh position={[0, 0.6, 0]} rotation={[Math.PI, Math.PI / 4, 0]} castShadow><cylinderGeometry args={[HOPPER.size * 0.707, 0.08, 0.4, 4]} /><meshStandardMaterial color={C.galvanised} metalness={0.6} roughness={0.35} /></mesh>
      <Box position={[0, 1.08, 0]} size={[HOPPER.size, 0.56, HOPPER.size]} color={C.galvanised} metalness={0.6} roughness={0.35} />
      <Box position={[0, 1.38, 0]} size={[HOPPER.size + 0.04, 0.04, HOPPER.size + 0.04]} color="#7f8a91" metalness={0.6} />
      <Box position={[-0.38, 0.52, 0]} size={[0.08, 0.14, 0.14]} color="#7f8a91" metalness={0.6} />
    </group>
  </group>
}

function DrinkerLine({ z }: { z: number }) {
  const y = DRINKERS.lineY
  return <group name="drinker-line">
    <Pipe from={[DRINKERS.regulatorX, y, z]} to={[DRINKERS.endX, y, z]} color={C.water} radius={0.02} />
    <Pipe from={[WATER_MAIN_X, y, z]} to={[DRINKERS.regulatorX, y, z]} color={C.water} radius={0.016} />
    <Box position={[DRINKERS.regulatorX, y + 0.02, z]} size={[0.12, 0.18, 0.1]} color="#1c4f9c" metalness={0.2} />
    <Pipe from={[DRINKERS.regulatorX, y + 0.11, z]} to={[DRINKERS.regulatorX, y + 0.5, z]} color="#cfe6f5" radius={0.008} />
    <Box position={[DRINKERS.endX + 0.02, y, z]} size={[0.04, 0.06, 0.06]} color="#1c4f9c" />
    {DRINKERS.nippleXs.map((x) => <group key={x} position={[x, y, z]}>
      <mesh position={[0, -0.035, 0]}><cylinderGeometry args={[0.01, 0.012, 0.05, 8]} /><meshStandardMaterial color="#d8dde0" metalness={0.6} roughness={0.3} /></mesh>
      <mesh position={[0, -0.07, 0.02]}><cylinderGeometry args={[0.026, 0.02, 0.016, 12]} /><meshStandardMaterial color="#f2f5f7" /></mesh>
    </group>)}
    {DRINKERS.standXs.map((x) => <group key={x}><Pipe from={[x, FLOOR_Y, z]} to={[x, y, z]} color="#7f8a91" radius={0.014} metalness={0.6} /><Box position={[x, LITTER_Y + 0.005, z]} size={[0.26, 0.01, 0.26]} color="#7f8a91" metalness={0.5} /></group>)}
  </group>
}

function Barn({ protection, onDevice }: { protection: Device; onDevice: (device: Device) => void }) {
  const y = DRINKERS.lineY
  return <group name="barn">
    <Feeder />
    {DRINKERS.zs.map((z) => <DrinkerLine key={z} z={z} />)}
    {/* Water main fixed low along the left wall, fed through the wall from the pump. */}
    <Pipe from={[WATER_MAIN_X, y, WATER_ENTRY_Z]} to={[WATER_MAIN_X, y, Math.min(...DRINKERS.zs)]} color={C.water} radius={0.022} />
    {[-4, -2, 0].map((z) => <Box key={z} position={[WATER_MAIN_X - 0.06, y, z]} size={[0.1, 0.05, 0.04]} color="#56636d" metalness={0.5} />)}
    <group onClick={(event) => { event.stopPropagation(); onDevice(protection) }} onPointerOver={() => { document.body.style.cursor = "pointer" }} onPointerOut={() => { document.body.style.cursor = "" }}>
      <Block rect={CABINET} y0={FLOOR_Y} y1={FLOOR_Y + CABINET.height} color="#3b4855" metalness={0.45} roughness={0.45} />
      <Box position={[CABINET.x0 - 0.003, FLOOR_Y + CABINET.height / 2, (CABINET.z0 + CABINET.z1) / 2]} size={[0.006, CABINET.height - 0.08, 0.008]} color="#1c252d" />
      {[-1, 1].map((side) => <Box key={side} position={[CABINET.x0 - 0.015, FLOOR_Y + 1.0, (CABINET.z0 + CABINET.z1) / 2 + side * 0.08]} size={[0.025, 0.14, 0.025]} color="#c9d1d6" metalness={0.7} />)}
    </group>
    <EdgeNode device={protection} position={PROTECTION_NODE} rotationY={-Math.PI / 2} onDevice={onDevice} />
  </group>
}

/* ------------------------------------------------------------------ front rooms */

function Table({ rect, top, color = "#c6ced3" }: { rect: Rect; top: number; color?: string }) {
  const legs: Array<[number, number]> = [[rect.x0 + 0.04, rect.z0 + 0.04], [rect.x1 - 0.04, rect.z0 + 0.04], [rect.x0 + 0.04, rect.z1 - 0.04], [rect.x1 - 0.04, rect.z1 - 0.04]]
  return <group>
    <Block rect={rect} y0={top - 0.03} y1={top} color={color} metalness={0.7} roughness={0.3} />
    <Block rect={{ x0: rect.x0 + 0.04, x1: rect.x1 - 0.04, z0: rect.z0 + 0.04, z1: rect.z1 - 0.04 }} y0={FLOOR_Y + 0.17} y1={FLOOR_Y + 0.19} color={color} metalness={0.7} roughness={0.3} />
    {legs.map(([x, z]) => <Box key={`${x}${z}`} position={[x, (FLOOR_Y + top) / 2, z]} size={[0.035, top - FLOOR_Y, 0.035]} color="#9aa5ad" metalness={0.7} />)}
  </group>
}

function Incubation({ one, two, onDevice }: { one: Device; two: Device; onDevice: (device: Device) => void }) {
  const devices = { "incubator-1": one, "incubator-2": two } as const, half = INCUBATOR_FOOTPRINT / 2
  return <group name="incubation-room">
    {INCUBATORS.map((spot) => <group key={spot.id}>
      <Asset url={INCUBATOR_URL} position={[spot.x, FLOOR_Y, spot.z]} scale={INCUBATOR_SCALE} rotationY={INCUBATOR_YAW} device={devices[spot.id]} onDevice={onDevice} />
      {/* Edge PC on the incubator's right-hand side, as on the plan. */}
      <EdgeNode device={devices[spot.id]} position={[spot.x + half + 0.035, FLOOR_Y + 1.05, spot.z]} rotationY={Math.PI / 2} onDevice={onDevice} />
      <Pipe from={[spot.x + 0.2, 0.3, INCUBATOR_WATER_Z]} to={[spot.x + 0.2, 0.3, spot.z - half + 0.02]} color={C.water} radius={0.012} />
    </group>)}
    <Table rect={BENCH} top={FLOOR_Y + 0.9} />
  </group>
}

function ShelfUnit({ x0, x1 }: { x0: number; x1: number }) {
  const { z0, z1 } = SHELVING, levels = [0.15, 0.6, 1.05, 1.5, 1.78]
  return <group>
    {levels.map((level) => <Block key={level} rect={{ x0, x1, z0, z1 }} y0={FLOOR_Y + level - 0.02} y1={FLOOR_Y + level} color="#8d989f" metalness={0.6} roughness={0.4} />)}
    {[x0 + 0.02, x1 - 0.02].flatMap((x) => [z0 + 0.02, z1 - 0.02].map((z) => <Box key={`${x}${z}`} position={[x, FLOOR_Y + 0.9, z]} size={[0.03, 1.8, 0.03]} color="#6f7b83" metalness={0.6} />))}
  </group>
}
function Service() {
  const middle = (SHELVING.x0 + SHELVING.x1) / 2, depth = SHELVING.z1 - SHELVING.z0
  return <group name="service-room">
    <ShelfUnit x0={SHELVING.x0} x1={middle} /><ShelfUnit x0={middle} x1={SHELVING.x1} />
    {/* Stock on the shelves: sacks and crates. */}
    {[[0.45, 0.6], [0.95, 0.6], [1.45, 0.15], [1.9, 0.15], [0.55, 1.05], [1.7, 1.05]].map(([x, level]) => <Box key={`${x}${level}`} position={[SHELVING.x0 + x - 0.15, FLOOR_Y + level + 0.13, SHELVING.z0 + depth / 2]} size={[0.38, 0.26, depth - 0.08]} color={level === 0.15 ? "#d9cba6" : "#8fa3b0"} roughness={0.9} />)}
    <Cylinder position={[FEED_BIN.x, FLOOR_Y + 0.44, FEED_BIN.z]} radius={FEED_BIN.radius} height={0.88} color="#e9b52c" metalness={0.1} roughness={0.5} />
    <Cylinder position={[FEED_BIN.x, FLOOR_Y + 0.9, FEED_BIN.z]} radius={FEED_BIN.radius + 0.015} height={0.04} color="#d39d17" metalness={0.1} />
    {[0.2, 0.62].map((h) => <Cylinder key={h} position={[FEED_BIN.x, FLOOR_Y + h, FEED_BIN.z]} radius={FEED_BIN.radius + 0.008} height={0.025} color="#d39d17" />)}
    {/* Footbath in front of the barn door, then the boot-change bench. */}
    <Block rect={FOOTBATH} y0={FLOOR_Y} y1={FLOOR_Y + 0.08} color="#4a5661" metalness={0.2} roughness={0.6} />
    <Block rect={{ x0: FOOTBATH.x0 + 0.04, x1: FOOTBATH.x1 - 0.04, z0: FOOTBATH.z0 + 0.04, z1: FOOTBATH.z1 - 0.04 }} y0={FLOOR_Y + 0.07} y1={FLOOR_Y + 0.082} color="#5cc3e8" metalness={0.1} roughness={0.15} />
    <Table rect={BOOT_BENCH} top={FLOOR_Y + 0.45} />
    <Block rect={{ x0: 1.5, x1: 3.0, z0: 6.9, z1: 7.45 }} y0={FLOOR_Y} y1={FLOOR_Y + 0.012} color="#3d454c" roughness={1} />
    {/* Electrical distribution board on the right-hand wall. */}
    <Block rect={ELECTRICAL} y0={FLOOR_Y + 0.9} y1={FLOOR_Y + 1.9} color="#dfe5e9" metalness={0.3} roughness={0.5} />
    <Box position={[ELECTRICAL.x0 - 0.003, FLOOR_Y + 1.4, (ELECTRICAL.z0 + ELECTRICAL.z1) / 2]} size={[0.006, 0.9, 0.006]} color="#8b979f" />
    <Box position={[ELECTRICAL.x0 - 0.01, FLOOR_Y + 1.45, ELECTRICAL.z0 + 0.12]} size={[0.02, 0.1, 0.03]} color="#3c4852" />
  </group>
}

/* ------------------------------------------------------------------ outside */

/** Rotor that spins under the unit's top grille, only while the HVAC is ALIVE. */
function Fan({ active }: { active: boolean }) {
  const fan = useRef<THREE.Group>(null), reduced = useReducedMotion()
  useFrame((_, delta) => { if (fan.current && active && !reduced) fan.current.rotation.y += delta * 9 })
  return <group ref={fan}>
    {[0, 1, 2, 3, 4].map((index) => <group key={index} rotation={[0, (Math.PI * 2 * index) / 5, 0]}><mesh position={[0, 0, 0.22]} rotation={[0, 0, 0.35]}><boxGeometry args={[0.15, 0.01, 0.34]} /><meshStandardMaterial color="#2c3742" metalness={0.6} roughness={0.4} /></mesh></group>)}
    <mesh><cylinderGeometry args={[0.07, 0.07, 0.06, 16]} /><meshStandardMaterial color="#1d252c" metalness={0.6} /></mesh>
  </group>
}

function Outside({ hvac, onDevice }: { hvac: Device; onDevice: (device: Device) => void }) {
  const y = DRINKERS.lineY, ws = WEATHER_STATION
  return <group name="outside">
    {/* Drinker tank and pump on their pad, with the barn feed and the incubator feed. */}
    <Block rect={TANK_PAD} y0={-0.03} y1={0.1} color="#9aa2a7" roughness={0.95} />
    <Cylinder position={[TANK.x, 0.1 + TANK.height / 2, TANK.z]} radius={TANK.radius} height={TANK.height} color="#eef1f2" roughness={0.6} metalness={0} />
    {/* Shallow domed top (0.14 m) sitting on the tank wall, with the filler cap on it. */}
    <mesh position={[TANK.x, 0.1 + TANK.height, TANK.z]} scale={[1, 0.25, 1]} castShadow><sphereGeometry args={[TANK.radius, 28, 12, 0, Math.PI * 2, 0, Math.PI / 2]} /><meshStandardMaterial color="#eef1f2" roughness={0.6} /></mesh>
    <Cylinder position={[TANK.x, 0.1 + TANK.height + 0.15, TANK.z]} radius={0.16} height={0.06} color="#d6dbde" />
    <Box position={[PUMP.x, 0.27, PUMP.z]} size={[0.3, 0.34, 0.3]} color="#5a6670" metalness={0.5} roughness={0.4} />
    <PipeRun points={[[TANK.x + TANK.radius - 0.02, 0.22, PUMP.z], [PUMP.x - 0.15, 0.22, PUMP.z]]} color={C.water} radius={0.022} />
    <PipeRun points={[[PUMP.x + 0.15, 0.3, PUMP.z], [PUMP.x + 0.4, 0.3, PUMP.z], [PUMP.x + 0.4, y, PUMP.z], [BUILDING.left, y, PUMP.z], [WATER_MAIN_X, y, WATER_ENTRY_Z]]} color={C.water} radius={0.022} />
    <PipeRun points={[[PUMP.x, 0.3, PUMP.z + 0.15], [PUMP.x, 0.3, INCUBATOR_WATER_Z], [INCUBATOR_WATER_END_X, 0.3, INCUBATOR_WATER_Z]]} color={C.water} radius={0.018} />
    {[1.95, INCUBATOR_WATER_Z].map((z) => <Pipe key={z} from={[PUMP.x, 0, z]} to={[PUMP.x, 0.28, z]} color="#7f8a91" radius={0.012} />)}

    {/* Packaged HVAC unit on its own pad, fan on top. */}
    <Block rect={HVAC_PAD} y0={-0.03} y1={0.12} color="#9aa2a7" roughness={0.95} />
    <Asset url={HVAC_URL} position={[HVAC_UNIT.x - HVAC_COLLAR_OFFSET, 0.12, HVAC_UNIT.z]} scale={HVAC_SCALE} rotationY={HVAC_YAW} device={hvac} onDevice={onDevice}>
      <group position={HVAC_FAN}><Fan active={deviceState(hvac) === "ALIVE"} /></group>
    </Asset>
    <EdgeNode device={hvac} position={HVAC_NODE} rotationY={-Math.PI / 2} onDevice={onDevice} />

    {/* Weather station: 6 m mast on its own footing. */}
    <group position={[ws.x, 0, ws.z]}>
      <Box position={[0, 0.05, 0]} size={[0.5, 0.16, 0.5]} color="#9aa2a7" roughness={0.95} />
      <Cylinder position={[0, ws.mast / 2, 0]} radius={0.04} height={ws.mast} color="#8d989f" metalness={0.6} />
      <Box position={[0, ws.mast - 0.1, 0]} size={[0.9, 0.04, 0.04]} color="#56636d" metalness={0.6} />
      <group position={[-0.42, ws.mast - 0.02, 0]}>{[0, 1, 2].map((index) => <mesh key={index} position={[Math.cos(index * 2.09) * 0.09, 0.06, Math.sin(index * 2.09) * 0.09]}><sphereGeometry args={[0.035, 10, 8]} /><meshStandardMaterial color="#e5e9ec" /></mesh>)}<Cylinder position={[0, 0.03, 0]} radius={0.012} height={0.08} color="#56636d" /></group>
      <group position={[0.42, ws.mast - 0.02, 0]}><Box position={[0.06, 0.06, 0]} size={[0.24, 0.02, 0.01]} color="#56636d" /><Box position={[0.17, 0.06, 0]} size={[0.02, 0.09, 0.08]} color="#56636d" /><Cylinder position={[0, 0.03, 0]} radius={0.012} height={0.08} color="#56636d" /></group>
      {[0, 1, 2, 3, 4].map((index) => <Cylinder key={index} position={[0.16, 2.1 + index * 0.045, 0]} radius={0.08} height={0.025} color="#f2f4f5" segments={20} />)}
      <Box position={[0.08, 2.06, 0]} size={[0.12, 0.02, 0.02]} color="#56636d" />
      <Box position={[0, 1.5, 0.06]} size={[0.24, 0.3, 0.1]} color="#e3e8eb" metalness={0.2} />
      <Box position={[0, 3.2, -0.12]} rotation={[-0.6, 0, 0]} size={[0.45, 0.02, 0.35]} color="#1f3248" metalness={0.5} roughness={0.25} />
    </group>
  </group>
}

/* ------------------------------------------------------------------ scene */

/**
 * Supplied model transforms, from their measured glTF bounds:
 *  - incubatormodel.glb is 0.98 × 1.90 × 1.03 m; × 0.8 gives the plan's 0.8 m square footprint and a 1.52 m cabinet.
 *  - hvac.glb is 1.90 × 1.03 × 1.58 m; × 0.92 gives a 1.75 × 0.94 × 1.46 m packaged unit on the plan's 1.8 × 1.4 m spot.
 */
const INCUBATOR_SCALE = 0.8
const INCUBATOR_YAW = 0
const HVAC_SCALE = 0.92
const HVAC_YAW = 0
/** hvac.glb's bounds include the 0.2 m inlet collar on its left side; the cabinet body itself is centred 0.1 m to the right of them. */
const HVAC_COLLAR_OFFSET = 0.1
/** Top of that inlet collar in world space (measured from the placed model), where the supply duct lands. */
const HVAC_COLLAR: Vec3 = [4.376, 1.08, 8.66]
/** Rotor centre in the unit's local frame: over the cabinet body, just under the top grille. */
const HVAC_FAN: Vec3 = [HVAC_COLLAR_OFFSET, 0.86, 0.02]

function SceneEnvironment() {
  const { gl, scene } = useThree()
  useEffect(() => {
    const generator = new THREE.PMREMGenerator(gl), environment = generator.fromScene(new RoomEnvironment(), 0.04).texture
    scene.environment = environment; scene.environmentIntensity = 0.55
    return () => { scene.environment = null; environment.dispose(); generator.dispose() }
  }, [gl, scene])
  return null
}

/** The .glb models are carried outside git (assets-manifest.json); a missing or broken one must not take the page down. */
class ModelBoundary extends Component<{ onMissing: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch() { this.props.onMissing() }
  render() { return this.state.failed ? null : this.props.children }
}

function Scene({ devices, onDevice, onMissing }: Props & { onMissing: () => void }) {
  const one = byId(devices, "incubator-1"), two = byId(devices, "incubator-2"), hvac = byId(devices, "hvac"), protection = byId(devices, "animal-protection")
  return <>
    <color attach="background" args={["#e6e6e6"]} />
    <SceneEnvironment />
    <hemisphereLight color="#f4f8ff" groundColor="#8a8f86" intensity={0.9} />
    <ambientLight intensity={0.25} />
    <directionalLight position={[-7, 20, 9]} intensity={2.4} castShadow shadow-mapSize={[4096, 4096]} shadow-bias={-0.0004} shadow-normalBias={0.02} shadow-camera-left={-15} shadow-camera-right={15} shadow-camera-top={15} shadow-camera-bottom={-15} shadow-camera-near={1} shadow-camera-far={60} />
    <Architecture /><Ducting /><Barn protection={protection} onDevice={onDevice} /><Service />
    <ModelBoundary onMissing={onMissing}><Suspense fallback={null}><Incubation one={one} two={two} onDevice={onDevice} /><Outside hvac={hvac} onDevice={onDevice} /><Chickens /></Suspense></ModelBoundary>
  </>
}

type View = "TOP" | "ISO_FRONT_LEFT" | "ISO_FRONT_RIGHT" | "BARN" | "BROILERS" | "INCUBATION" | "SERVICE"
type Preset = { label: string; orthographic: boolean; position: Vec3; target: Vec3 }
/** Orthographic presets are placed along a direction and then fitted to the whole site; perspective presets are interior cameras. */
const views: Record<View, Preset> = {
  TOP: { label: "Top", orthographic: true, position: [0, 40, 0.001], target: [-1.1, 0, 0.55] },
  ISO_FRONT_LEFT: { label: "Front-left", orthographic: true, position: [-1, 1.15, 1], target: [-1.1, 0.8, 0.55] },
  ISO_FRONT_RIGHT: { label: "Front-right", orthographic: true, position: [1, 1.15, 1], target: [-1.1, 0.8, 0.55] },
  BARN: { label: "Barn", orthographic: false, position: [-5.4, 1.55, 1.75], target: [0.6, 0.2, -3.6] },
  BROILERS: { label: "Broilers close-up", orthographic: false, position: [-1.6, 0.85, -0.6], target: [0.2, 0.2, -2.9] },
  INCUBATION: { label: "Incubation", orthographic: false, position: [-1.0, 1.6, 7.25], target: [-3.85, 0.75, 3.2] },
  SERVICE: { label: "Service", orthographic: false, position: [0.3, 1.65, 7.25], target: [3.5, 0.75, 3.0] },
}
/** Everything on site except the top of the weather mast. */
const SITE = new THREE.Box3(new THREE.Vector3(-8.7, 0, -9.3), new THREE.Vector3(6.6, 3.2, 10.4))

function CameraRig({ view, nonce }: { view: View; nonce: number }) {
  const camera = useThree((state) => state.camera), controls = useThree((state) => state.controls) as OrbitControlsImpl | null, size = useThree((state) => state.size)
  useLayoutEffect(() => {
    const preset = views[view], target = new THREE.Vector3(...preset.target)
    if (camera instanceof THREE.OrthographicCamera) {
      camera.position.copy(target).add(new THREE.Vector3(...preset.position).normalize().multiplyScalar(45))
      camera.up.set(0, 1, 0); camera.lookAt(target); camera.updateMatrixWorld()
      // Fit the site's bounding box in view space, then re-centre on it.
      const bounds = new THREE.Box3(), corner = new THREE.Vector3()
      for (let i = 0; i < 8; i += 1) bounds.expandByPoint(corner.set(i & 1 ? SITE.max.x : SITE.min.x, i & 2 ? SITE.max.y : SITE.min.y, i & 4 ? SITE.max.z : SITE.min.z).applyMatrix4(camera.matrixWorldInverse))
      camera.zoom = Math.min(size.width / ((bounds.max.x - bounds.min.x) * 1.04), size.height / ((bounds.max.y - bounds.min.y) * 1.04))
      const shift = new THREE.Vector3((bounds.max.x + bounds.min.x) / 2, (bounds.max.y + bounds.min.y) / 2, 0).applyQuaternion(camera.quaternion)
      target.add(shift); camera.position.add(shift); camera.lookAt(target); camera.updateProjectionMatrix()
    } else {
      camera.position.set(...preset.position); camera.up.set(0, 1, 0); camera.lookAt(target)
    }
    if (controls) { controls.target.copy(target); controls.update() }
  }, [view, nonce, camera, controls, size.width, size.height])
  return null
}

/** Progress of the 3D models while they stream in; the canvas stays blank until they arrive. */
function LoadingOverlay() {
  const { active, progress } = useProgress()
  if (!active) return null
  return <div className="hatch absolute inset-0 grid place-items-center"><span role="status" className="label-mono border bg-background px-4 py-3 text-foreground">Loading 3D farm // {Math.round(progress)} %</span></div>
}

const STATE_LEGEND = [
  { state: "ALIVE", swatch: "bg-ok", meaning: "operating normally" },
  { state: "ALARM", swatch: "bg-danger", meaning: "a warning or fault on the machine" },
  { state: "OFF", swatch: "bg-input", meaning: "stopped or unavailable" },
] as const

export function FarmScene3D({ devices, onDevice }: Props) {
  const [view, setView] = useState<View>("ISO_FRONT_LEFT"), [nonce, setNonce] = useState(0), [missing, setMissing] = useState(false), orthographic = views[view].orthographic
  useEffect(() => () => { document.body.style.cursor = "" }, [])
  return <section aria-labelledby="farm-3d-title" className="border bg-background">
    <div className="grid gap-4 border-b px-5 py-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
      <div>
        <div className="flex items-center gap-3"><span aria-hidden className="marker" /><span className="label-mono text-muted-foreground">3D cutaway // 12.5 × 15.5 m plan</span></div>
        <h2 id="farm-3d-title" className="display-sm mt-3 text-[15px]">The poultry farm</h2>
        <p className="mt-2 max-w-2xl text-[14px] leading-6 text-muted-foreground">Barn 12.5 × 10 m, incubation room 6 × 5.5 m, service room 6.5 × 5.5 m. Every reading on it is simulation data, not live hardware. Click a machine to open it.</p>
      </div>
      <Button size="sm" variant="outline" onClick={() => setNonce((value) => value + 1)}>Reset view</Button>
    </div>
    <div role="group" aria-label="Camera view" className="flex flex-wrap border-b">{(Object.keys(views) as View[]).map((key) => <button key={key} type="button" aria-pressed={view === key} onClick={() => setView(key)} className={`label-mono h-11 border-r border-l-[3px] px-4 transition-colors duration-150 ${view === key ? "border-l-signal bg-foreground text-background" : "border-l-transparent text-foreground hover:bg-secondary"}`}>{views[key].label}</button>)}</div>
    <div className="relative h-[clamp(420px,62vh,640px)] overflow-hidden bg-[#e6e6e6]">
      <Canvas shadows="percentage" dpr={[1, 1.5]}>
        <OrthographicCamera makeDefault={orthographic} near={0.1} far={200} />
        <PerspectiveCamera makeDefault={!orthographic} fov={50} near={0.05} far={200} />
        <Scene devices={devices} onDevice={onDevice} onMissing={() => setMissing(true)} />
        <OrbitControls makeDefault enablePan={!orthographic} minDistance={0.6} maxDistance={40} minZoom={12} maxZoom={400} minPolarAngle={0} maxPolarAngle={1.52} />
        <CameraRig view={view} nonce={nonce} />
      </Canvas>
      {missing ? <div role="status" className="absolute inset-x-0 top-0 border-b bg-background/95 px-5 py-3 text-[14px] leading-6"><span className="font-semibold">The machine 3D models could not be loaded (missing or unreadable).</span> They are carried separately from the code (<span className="label-mono">public/assets/farm</span>, listed in <span className="label-mono">assets-manifest.json</span>). The rooms are drawn without them; every reading on this page is unaffected.</div> : <LoadingOverlay />}
    </div>
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-t px-5 py-3">
      <ul className="flex flex-wrap gap-x-5 gap-y-2">{STATE_LEGEND.map((item) => <li key={item.state} className="label-mono flex items-center gap-2 text-muted-foreground"><span aria-hidden className={`size-2 ${item.swatch}`} /><span className="font-semibold text-foreground">{item.state}</span> {item.meaning}</li>)}</ul>
      <span className="label-mono text-muted-foreground">Top and front views are orthographic // room views are eye level</span>
    </div>
  </section>
}

useGLTF.preload(INCUBATOR_URL)
useGLTF.preload(HVAC_URL)
