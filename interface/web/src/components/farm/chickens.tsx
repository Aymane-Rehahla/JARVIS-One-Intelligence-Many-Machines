import { useMemo, useRef } from "react"
import { useFrame } from "@react-three/fiber"
import { useGLTF } from "@react-three/drei"
import * as THREE from "three"
import { useReducedMotion } from "@/hooks/use-reduced-motion"
import { CABINET, CHICKEN_STARTS, DRINKERS, FEEDER, HOPPER, LITTER_Y, type Rect } from "./layout"

export const CHICKEN_URL = "/assets/farm/chicken/source/Chicken.glb"

/**
 * The supplied Chicken.glb is 0.51 m tall with its body centred 0.26 m off the origin, so it is re-centred
 * (feet on y = 0, body over the pivot) and scaled to a market-age broiler: ~0.36 m tall, ~0.39 m long.
 */
export const CHICKEN_SCALE = 0.7
/** Yaw that turns the source model's head toward local +Z (the walking direction). */
const MODEL_YAW = -Math.PI / 2
const BODY_RADIUS = 0.14

/** Walkable litter, already inset by the bird's body radius from the walls and the water main. */
const WALKABLE: Rect = { x0: -5.65, x1: 5.75, z0: -7.25, z1: 1.9 }
const BLOCKERS: Rect[] = [
  { x0: HOPPER.x - HOPPER.size / 2, x1: HOPPER.x + HOPPER.size / 2, z0: HOPPER.z - HOPPER.size / 2, z1: HOPPER.z + HOPPER.size / 2 },
  { x0: FEEDER.startX - 0.35, x1: FEEDER.startX + 0.05, z0: FEEDER.z - 0.2, z1: FEEDER.z + 0.2 },
  CABINET,
  ...DRINKERS.zs.flatMap((z) => [...DRINKERS.standXs, DRINKERS.regulatorX].map((x) => ({ x0: x - 0.15, x1: x + 0.15, z0: z - 0.15, z1: z + 0.15 }))),
]

function blocked(x: number, z: number) {
  if (x < WALKABLE.x0 || x > WALKABLE.x1 || z < WALKABLE.z0 || z > WALKABLE.z1) return true
  if (Math.abs(z - FEEDER.z) < FEEDER.panRadius + BODY_RADIUS && FEEDER.panXs.some((px) => Math.hypot(x - px, z - FEEDER.z) < FEEDER.panRadius + BODY_RADIUS)) return true
  return BLOCKERS.some((r) => x > r.x0 - BODY_RADIUS && x < r.x1 + BODY_RADIUS && z > r.z0 - BODY_RADIUS && z < r.z1 + BODY_RADIUS)
}
function pathClear(x0: number, z0: number, x1: number, z1: number) {
  const steps = Math.ceil(Math.hypot(x1 - x0, z1 - z0) / 0.1)
  for (let i = 1; i <= steps; i += 1) if (blocked(x0 + ((x1 - x0) * i) / steps, z0 + ((z1 - z0) * i) / steps)) return false
  return true
}

function rng(seed: number) {
  let s = seed >>> 0
  return () => { s = (s + 0x6d2b79f5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296 }
}

type Mode = "idle" | "turn" | "walk"
type Bird = { x: number; z: number; heading: number; mode: Mode; timer: number; tx: number; tz: number; speed: number; peck: boolean; phase: number; random: () => number }

/** Points a bird is drawn to: beside a feeder pan or under a drinker line. */
function attraction(random: () => number): [number, number] {
  if (random() < 0.5) {
    const pan = FEEDER.panXs[Math.floor(random() * FEEDER.panXs.length)]
    const angle = random() * Math.PI * 2
    return [pan + Math.cos(angle) * 0.36, FEEDER.z + Math.sin(angle) * 0.36]
  }
  const z = DRINKERS.zs[Math.floor(random() * DRINKERS.zs.length)]
  return [DRINKERS.nippleXs[Math.floor(random() * DRINKERS.nippleXs.length)], z + (random() - 0.5) * 0.12]
}

function chooseTarget(bird: Bird, birds: Bird[]) {
  for (let attempt = 0; attempt < 16; attempt += 1) {
    let x: number, z: number
    if (bird.random() < 0.3) [x, z] = attraction(bird.random)
    else {
      const angle = bird.heading + (bird.random() - 0.5) * Math.PI * 1.6, distance = 0.4 + bird.random() * 1.8
      x = bird.x + Math.sin(angle) * distance; z = bird.z + Math.cos(angle) * distance
    }
    if (Math.hypot(x - bird.x, z - bird.z) > 4 || blocked(x, z) || !pathClear(bird.x, bird.z, x, z)) continue
    if (birds.some((other) => other !== bird && (Math.hypot(other.tx - x, other.tz - z) < 0.45 || Math.hypot(other.x - x, other.z - z) < 0.45))) continue
    bird.tx = x; bird.tz = z; bird.mode = "turn"
    return
  }
  bird.mode = "idle"; bird.timer = 0.5 + bird.random()
}

function angleDelta(from: number, to: number) { return Math.atan2(Math.sin(to - from), Math.cos(to - from)) }

/** Recolours the supplied (brown hen) texture to a white broiler: feathers white, comb red, legs and beak yellow. */
function broilerMaterial(source: THREE.MeshStandardMaterial) {
  const material = source.clone()
  const image = source.map?.image as (CanvasImageSource & { width: number; height: number }) | undefined
  if (source.map && image?.width) {
    const size = Math.min(1024, image.width), canvas = document.createElement("canvas")
    canvas.width = canvas.height = size
    const context = canvas.getContext("2d", { willReadFrequently: true })
    if (context) {
      context.drawImage(image, 0, 0, size, size)
      const pixels = context.getImageData(0, 0, size, size), data = pixels.data
      for (let i = 0; i < data.length; i += 4) {
        const r = data[i] / 255, g = data[i + 1] / 255, b = data[i + 2] / 255
        const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2, d = max - min
        const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1))
        let hue = d === 0 ? 0 : max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4
        hue = (hue * 60 + 360) % 360
        if ((hue < 14 || hue > 330) && s > 0.35 && r - g > 0.35 && r > 0.47) { data[i] = Math.min(255, data[i] * 1.05); data[i + 1] *= 0.75; data[i + 2] *= 0.8; continue }
        if (hue >= 36 && hue <= 68 && l > 0.45) { const shade = 0.75 + 0.3 * l; data[i] = 236 * shade; data[i + 1] = 196 * shade; data[i + 2] = 104 * shade; continue }
        const shade = 0.8 + 0.2 * THREE.MathUtils.clamp((l - 0.2) / 0.55, 0, 1)
        data[i] = 252 * shade; data[i + 1] = 250 * shade; data[i + 2] = 244 * shade
      }
      context.putImageData(pixels, 0, 0)
      const texture = new THREE.CanvasTexture(canvas)
      texture.flipY = source.map.flipY; texture.colorSpace = THREE.SRGBColorSpace; texture.wrapS = source.map.wrapS; texture.wrapT = source.map.wrapT; texture.anisotropy = 4
      material.map = texture
    }
  }
  material.color.set("#ffffff"); material.metalness = 0; material.metalnessMap = null; material.roughness = 0.9; material.roughnessMap = null
  return material
}

function useBroilerModel() {
  const gltf = useGLTF(CHICKEN_URL)
  return useMemo(() => {
    const template = gltf.scene.clone(true), materials = new Map<THREE.Material, THREE.Material>()
    template.traverse((item) => {
      if (!(item instanceof THREE.Mesh)) return
      item.castShadow = true; item.receiveShadow = true
      if (!materials.has(item.material)) materials.set(item.material, broilerMaterial(item.material as THREE.MeshStandardMaterial))
      item.material = materials.get(item.material)
    })
    // Feet on y = 0 and the body centred over the pivot, so turning happens in place.
    const box = new THREE.Box3().setFromObject(template), centre = box.getCenter(new THREE.Vector3())
    template.position.x -= centre.x; template.position.y -= box.min.y; template.position.z -= centre.z
    return template
  }, [gltf.scene])
}

/** The 24 broilers: one frame loop moves them all so they can steer around each other. */
export function Chickens() {
  const template = useBroilerModel()
  const models = useMemo(() => CHICKEN_STARTS.map(() => template.clone(true)), [template])
  const groups = useRef<(THREE.Group | null)[]>([])
  const motion = useRef<(THREE.Group | null)[]>([])
  const birds = useMemo<Bird[]>(() => CHICKEN_STARTS.map(([x, z], index) => {
    const random = rng(index * 7919 + 17)
    return { x, z, heading: random() * Math.PI * 2, mode: "idle", timer: random() * 3, tx: x, tz: z, speed: 0.17 + random() * 0.13, peck: random() < 0.5, phase: random() * 10, random }
  }), [])

  const reduced = useReducedMotion()

  // Under reduced motion the flock stands where the plan places it.
  useFrame((_, rawDelta) => {
    if (reduced) return
    const delta = Math.min(rawDelta, 0.05)
    birds.forEach((bird, index) => {
      let pitch = 0, roll = 0
      if (bird.mode === "idle") {
        bird.timer -= delta
        if (bird.peck) { bird.phase += delta * 7; pitch = Math.max(0, Math.sin(bird.phase)) ** 3 * 0.32 }
        if (bird.timer <= 0) chooseTarget(bird, birds)
      } else {
        const want = Math.atan2(bird.tx - bird.x, bird.tz - bird.z), off = angleDelta(bird.heading, want)
        if (bird.mode === "turn") {
          bird.heading += Math.sign(off) * Math.min(Math.abs(off), 2.6 * delta)
          if (Math.abs(off) < 0.2) bird.mode = "walk"
        } else {
          bird.heading += off * Math.min(1, 5 * delta)
          const distance = Math.hypot(bird.tx - bird.x, bird.tz - bird.z), step = Math.min(distance, bird.speed * delta)
          const nx = bird.x + Math.sin(bird.heading) * step, nz = bird.z + Math.cos(bird.heading) * step
          const crowded = birds.some((other) => other !== bird && Math.hypot(other.x - nx, other.z - nz) < 0.38 && Math.hypot(other.x - bird.x, other.z - bird.z) > Math.hypot(other.x - nx, other.z - nz))
          if (distance < 0.05) { bird.mode = "idle"; bird.timer = 1.2 + bird.random() * 4; bird.peck = bird.random() < 0.65 }
          else if (blocked(nx, nz) || crowded) { bird.mode = "idle"; bird.timer = 0.3 + bird.random() * 0.9; bird.peck = false }
          else { bird.x = nx; bird.z = nz; bird.phase += delta * bird.speed * 38; roll = Math.sin(bird.phase) * 0.07 }
        }
      }
      const group = groups.current[index], body = motion.current[index]
      if (group) { group.position.set(bird.x, LITTER_Y, bird.z); group.rotation.y = bird.heading }
      if (body) { body.rotation.x = THREE.MathUtils.damp(body.rotation.x, pitch, 14, delta); body.rotation.z = THREE.MathUtils.damp(body.rotation.z, roll, 14, delta) }
    })
  })

  return <group name="broilers">
    {models.map((model, index) => <group key={index} name="broiler" ref={(node) => { groups.current[index] = node }} position={[CHICKEN_STARTS[index][0], LITTER_Y, CHICKEN_STARTS[index][1]]}>
      <group ref={(node) => { motion.current[index] = node }}>
        <group rotation={[0, MODEL_YAW, 0]} scale={CHICKEN_SCALE}><primitive object={model} /></group>
      </group>
    </group>)}
  </group>
}

useGLTF.preload(CHICKEN_URL)
