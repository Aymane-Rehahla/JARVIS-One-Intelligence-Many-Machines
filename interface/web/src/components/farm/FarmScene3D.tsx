import { useEffect, useRef, type ReactNode } from "react"
import { Canvas, useFrame } from "@react-three/fiber"
import { Billboard, Line, OrbitControls, Text } from "@react-three/drei"
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib"
import * as THREE from "three"

import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import type { Device, DeviceId, OperationalState } from "@/services/matrix"

type FarmScene3DProps = {
  devices: Device[]
  onDevice: (device: Device) => void
}

type DeviceNodeProps = {
  device: Device
  position: [number, number, number]
  children: ReactNode
  onDevice: (device: Device) => void
}

const palette = {
  steel: "#64748b",
  steelDark: "#334155",
  panel: "#dbe4ea",
  panelDark: "#aab8c2",
  roof: "#315c83",
  concrete: "#b8c0c5",
  floor: "#7c8589",
  warm: "#f7d37c",
  water: "#38bdf8",
  ethernet: "#2563eb",
  zigbee: "#a855f7",
}

function statusColor(state: OperationalState): string {
  if (state === "critical") return "#ef4444"
  if (state === "warning") return "#f59e0b"
  if (state === "normal") return "#22c55e"
  return "#94a3b8"
}

function deviceById(devices: Device[], id: DeviceId): Device {
  const device = devices.find((item) => item.id === id)
  if (!device) throw new Error(`Missing device ${id}`)
  return device
}

function Label({ children, color = "#e2e8f0" }: { children: string; color?: string }) {
  return (
    <Text
      position={[0, 0, 0]}
      color={color}
      fontSize={0.32}
      anchorX="center"
      anchorY="middle"
      outlineColor="#0f172a"
      outlineWidth={0.018}
    >
      {children}
    </Text>
  )
}

function Box({
  position,
  size,
  color,
  metalness = 0,
  roughness = 0.7,
}: {
  position: [number, number, number]
  size: [number, number, number]
  color: string
  metalness?: number
  roughness?: number
}) {
  return (
    <mesh position={position} castShadow receiveShadow>
      <boxGeometry args={size} />
      <meshStandardMaterial color={color} metalness={metalness} roughness={roughness} />
    </mesh>
  )
}

function Pipe({
  from,
  to,
  color,
  radius = 0.08,
}: {
  from: [number, number, number]
  to: [number, number, number]
  color: string
  radius?: number
}) {
  const start = new THREE.Vector3(...from)
  const end = new THREE.Vector3(...to)
  const direction = end.clone().sub(start)
  const midpoint = start.clone().add(end).multiplyScalar(0.5)
  return (
    <mesh position={midpoint} quaternion={new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.clone().normalize())} castShadow>
      <cylinderGeometry args={[radius, radius, direction.length(), 10]} />
      <meshStandardMaterial color={color} metalness={0.8} roughness={0.28} />
    </mesh>
  )
}

function StatusLight({ state, position }: { state: OperationalState; position: [number, number, number] }) {
  return (
    <mesh position={position}>
      <sphereGeometry args={[0.14, 16, 16]} />
      <meshStandardMaterial color={statusColor(state)} emissive={statusColor(state)} emissiveIntensity={state === "critical" ? 3 : 1.2} />
    </mesh>
  )
}

function DeviceNode({ device, position, children, onDevice }: DeviceNodeProps) {
  const selectedMaterial = device.operational === "critical" ? "#7f1d1d" : device.operational === "warning" ? "#78350f" : "#0f3554"
  return (
    <group
      position={position}
      onClick={(event) => { event.stopPropagation(); onDevice(device) }}
      onPointerOver={(event) => { event.stopPropagation(); document.body.style.cursor = "pointer" }}
      onPointerOut={() => { document.body.style.cursor = "" }}
    >
      <Box position={[0, 0.18, 0]} size={[3.1, 0.28, 2.25]} color="#0f172a" metalness={0.2} />
      <Box position={[0, 1.65, 0]} size={[3, 2.7, 2.1]} color={selectedMaterial} metalness={0.35} roughness={0.38} />
      <Box position={[0, 1.65, 1.08]} size={[2.4, 1.9, 0.05]} color="#1e293b" metalness={0.2} roughness={0.45} />
      <StatusLight state={device.operational} position={[1.15, 2.65, 1.16]} />
      {/* Native 3D text: drei's <Html> labels opened a React root each and errored when the page unmounted. */}
      <Billboard position={[0, 3.3, 0]}>
        <Text fontSize={0.36} color="#f1f5f9" anchorX="center" anchorY="middle" outlineColor="#020617" outlineWidth={0.03}>
          {device.name}
        </Text>
      </Billboard>
      {children}
    </group>
  )
}

function Incubator({ device, position, onDevice }: { device: Device; position: [number, number, number]; onDevice: (device: Device) => void }) {
  return (
    <DeviceNode device={device} position={position} onDevice={onDevice}>
      <Box position={[-0.65, 1.45, 1.16]} size={[0.95, 1.5, 0.08]} color="#cbd5e1" metalness={0.55} roughness={0.25} />
      <Box position={[0.65, 1.45, 1.16]} size={[0.95, 1.5, 0.08]} color="#cbd5e1" metalness={0.55} roughness={0.25} />
      {[-0.7, -0.25, 0.2, 0.65].map((y) => (
        <group key={y}>
          <Box position={[-0.65, 1.45 + y, 1.22]} size={[0.7, 0.05, 0.02]} color={palette.warm} />
          <Box position={[0.65, 1.45 + y, 1.22]} size={[0.7, 0.05, 0.02]} color={palette.warm} />
        </group>
      ))}
      <Box position={[1.2, 2.05, 1.16]} size={[0.28, 0.38, 0.04]} color="#0ea5e9" metalness={0.35} />
      <Label>{device.id === "incubator-1" ? "INCUBATOR 1" : "INCUBATOR 2"}</Label>
    </DeviceNode>
  )
}

function Fan({ active }: { active: boolean }) {
  const ref = useRef<THREE.Group>(null)
  useFrame((_, delta) => {
    if (ref.current) ref.current.rotation.z += delta * (active ? 4 : 0.45)
  })
  return (
    <group ref={ref}>
      <mesh>
        <cylinderGeometry args={[0.65, 0.65, 0.18, 24]} />
        <meshStandardMaterial color="#1e293b" metalness={0.7} roughness={0.3} />
      </mesh>
      {[0, 1, 2, 3].map((index) => (
        <mesh key={index} rotation={[0, 0, (Math.PI / 2) * index]} position={[0.38, 0, 0]}>
          <boxGeometry args={[0.72, 0.08, 0.12]} />
          <meshStandardMaterial color={active ? "#93c5fd" : "#475569"} metalness={0.6} />
        </mesh>
      ))}
    </group>
  )
}

function HVAC({ device, position, onDevice }: { device: Device; position: [number, number, number]; onDevice: (device: Device) => void }) {
  const active = device.running
  return (
    <DeviceNode device={device} position={position} onDevice={onDevice}>
      <Box position={[-0.85, 1.55, 1.18]} size={[0.72, 1.5, 0.08]} color="#94a3b8" metalness={0.75} roughness={0.25} />
      <Box position={[0, 1.55, 1.18]} size={[0.72, 1.5, 0.08]} color="#94a3b8" metalness={0.75} roughness={0.25} />
      <Box position={[0.85, 1.55, 1.18]} size={[0.72, 1.5, 0.08]} color="#94a3b8" metalness={0.75} roughness={0.25} />
      <group position={[-0.85, 1.55, 1.28]} rotation={[Math.PI / 2, 0, 0]}><Fan active={active} /></group>
      <group position={[0, 1.55, 1.28]} rotation={[Math.PI / 2, 0, 0]}><Fan active={active} /></group>
      <group position={[0.85, 1.55, 1.28]} rotation={[Math.PI / 2, 0, 0]}><Fan active={active} /></group>
      <Label>AHU / CENTRAL HVAC</Label>
    </DeviceNode>
  )
}

function Protection({ device, position, onDevice }: { device: Device; position: [number, number, number]; onDevice: (device: Device) => void }) {
  return (
    <DeviceNode device={device} position={position} onDevice={onDevice}>
      <Box position={[0, 1.75, 1.18]} size={[1.4, 0.92, 0.08]} color="#0ea5e9" metalness={0.25} />
      <Box position={[-0.9, 1.75, 1.18]} size={[0.1, 0.5, 0.04]} color="#f59e0b" />
      <Label>ANIMAL PROTECTION</Label>
    </DeviceNode>
  )
}

// The real barn holds one flock of 24 chickens with one feeder line and one drinker line.
const CHICKENS: [number, number][] = Array.from({ length: 24 }, (_, index) => {
  const column = index % 6
  const row = Math.floor(index / 6)
  return [-7 + column * 1.75 + (row % 2) * 0.8, -2.6 + row * 2.1]
})

function PoultryHouse({ animalState }: { animalState: OperationalState }) {
  const sensors: [number, number, number][] = [[-6, 2.2, 1], [-3, 2.2, 1], [0, 2.2, 1], [2.6, 2.2, 1]]
  return (
    <group>
      <Box position={[0, 0, 0]} size={[20, 0.35, 12]} color={palette.floor} roughness={0.9} />
      <Box position={[-2.3, 0.7, 0.7]} size={[11.3, 0.12, 9]} color="#e4c39a" roughness={1} />
      <Box position={[-2.3, 0.82, -3.7]} size={[11.3, 0.7, 0.12]} color={palette.steelDark} metalness={0.4} />
      <Box position={[-2.3, 0.82, 5.0]} size={[11.3, 0.7, 0.12]} color={palette.steelDark} metalness={0.4} />
      <Pipe from={[-7.5, 1.9, -1.4]} to={[3, 1.9, -1.4]} color="#dc2626" radius={0.05} />
      <Pipe from={[-7.5, 1.9, 2.6]} to={[3, 1.9, 2.6]} color="#38bdf8" radius={0.05} />
      {[-6, -3.5, -1, 1.5].map((x) => (
        <group key={x} position={[x, 1.1, -1.4]}>
          <mesh><coneGeometry args={[0.24, 0.3, 16]} /><meshStandardMaterial color="#dc2626" /></mesh>
        </group>
      ))}
      {CHICKENS.map(([x, z], index) => (
        <mesh key={index} position={[x, 0.92, z]}><sphereGeometry args={[0.16, 10, 10]} /><meshStandardMaterial color="#f5f1df" roughness={1} /></mesh>
      ))}
      {sensors.map((position, index) => (
        <group key={index} position={position}>
          <mesh><cylinderGeometry args={[0.07, 0.07, 0.65, 10]} /><meshStandardMaterial color="#0f172a" metalness={0.6} /></mesh>
          <StatusLight state={animalState} position={[0, 0.42, 0]} />
        </group>
      ))}
      <Text position={[-2.3, 0.25, 5.65]} rotation={[-Math.PI / 2, 0, 0]} color="#334155" fontSize={0.3} anchorX="center">BARN · 24 CHICKENS · FEEDER (RED) · DRINKERS (BLUE)</Text>
    </group>
  )
}

function BuildingShell() {
  return (
    <group>
      <Box position={[0, 4.1, -6.1]} size={[22, 7.8, 0.32]} color={palette.panelDark} roughness={0.7} />
      <Box position={[-10.8, 4.1, 0]} size={[0.32, 7.8, 12]} color={palette.panel} roughness={0.72} />
      <Box position={[10.8, 4.1, 0]} size={[0.32, 7.8, 12]} color={palette.panel} roughness={0.72} />
      <Box position={[-5.7, 5.1, -3.2]} size={[0.18, 6, 0.18]} color={palette.steelDark} metalness={0.85} />
      <Box position={[-1.9, 5.1, -3.2]} size={[0.18, 6, 0.18]} color={palette.steelDark} metalness={0.85} />
      <Box position={[1.9, 5.1, -3.2]} size={[0.18, 6, 0.18]} color={palette.steelDark} metalness={0.85} />
      <Box position={[5.7, 5.1, -3.2]} size={[0.18, 6, 0.18]} color={palette.steelDark} metalness={0.85} />
      <Box position={[0, 8.05, -3.2]} size={[21.8, 0.24, 6.3]} color={palette.roof} metalness={0.35} roughness={0.4} />
      <Box position={[-7.8, 7.1, 2.8]} size={[6, 0.18, 5]} color="#203f62" metalness={0.3} roughness={0.42} />
      {[-9.5, -4.7, 0, 4.7, 9.5].map((x) => (
        <Box key={x} position={[x, 4.2, 5.55]} size={[0.14, 7.5, 0.14]} color={palette.steelDark} metalness={0.8} />
      ))}
    </group>
  )
}

// The drinker tank is 1,000 L (about 1.1 m wide, 1.2 m tall), refilled from the farm water main.
function WaterInfrastructure() {
  return (
    <group>
      <Box position={[12.6, 0.12, 2.5]} size={[2.4, 0.24, 2]} color="#334155" metalness={0.5} />
      <mesh position={[13.1, 0.84, 2.5]} castShadow>
        <cylinderGeometry args={[0.55, 0.55, 1.2, 32]} />
        <meshStandardMaterial color="#64748b" metalness={0.6} roughness={0.35} />
      </mesh>
      <Box position={[11.9, 0.5, 2.5]} size={[0.5, 0.5, 0.5]} color="#0f172a" metalness={0.4} />
      <Pipe from={[13.1, -0.2, 3.6]} to={[13.1, 0.4, 3.6]} color={palette.water} radius={0.06} />
      <Pipe from={[11.9, 0.5, 2.5]} to={[8.2, 0.5, 2.5]} color={palette.water} radius={0.06} />
      <Pipe from={[8.2, 0.5, 2.5]} to={[3, 1.9, 2.6]} color={palette.water} radius={0.05} />
      <Text position={[12.6, 2.05, 2.5]} color="#dbeafe" fontSize={0.3} anchorX="center" outlineColor="#0f172a" outlineWidth={0.02}>DRINKER TANK · 1,000 L</Text>
    </group>
  )
}

function ExternalServices() {
  return (
    <group>
      <Box position={[8.5, 1.35, 4.5]} size={[2.5, 2.1, 1.5]} color="#f1f5f9" metalness={0.35} roughness={0.38} />
      <Box position={[8.5, 1.35, 5.28]} size={[1.2, 0.75, 0.03]} color="#334155" metalness={0.4} />
      <Text position={[8.5, 2.65, 4.5]} color="#e2e8f0" fontSize={0.3} anchorX="center" outlineColor="#0f172a" outlineWidth={0.02}>ELECTRICAL DISTRIBUTION</Text>
      <Pipe from={[8.5, 2.2, 3.75]} to={[5, 2.2, 2]} color="#f59e0b" radius={0.08} />
      <Pipe from={[8.5, 2.0, 3.75]} to={[2, 2.0, -3]} color="#f59e0b" radius={0.06} />
    </group>
  )
}

function CommunicationLinks({ devices }: { devices: Device[] }) {
  const status = deviceById(devices, "incubator-1").operational
  const end = deviceById(devices, "animal-protection").operational
  return (
    <group>
      <Line points={[[-7, 4.4, -3.9], [-7, 7, -3.9], [0, 7, -3.9]]} color={palette.ethernet} lineWidth={2} dashed dashSize={0.25} gapSize={0.16} />
      <Text position={[-3.5, 7.2, -3.8]} color="#bfdbfe" fontSize={0.28} anchorX="center">ETHERNET · JARVIS</Text>
      <Line points={[[5, 4, 1], [2, 4.8, 0], [-2, 4.5, 0]]} color={palette.zigbee} lineWidth={1.5} dashed dashSize={0.18} gapSize={0.18} />
      <Line points={[[5, 4, 1], [6, 4.8, -3]]} color={palette.zigbee} lineWidth={1.5} dashed dashSize={0.18} gapSize={0.18} />
      <Line points={[[5, 4, 1], [8, 4.8, 2]]} color={palette.zigbee} lineWidth={1.5} dashed dashSize={0.18} gapSize={0.18} />
      <StatusLight state={status} position={[-7, 4.4, -3.9]} />
      <StatusLight state={end} position={[5, 4, 1]} />
    </group>
  )
}

function Airflow({ active }: { active: boolean }) {
  const ref = useRef<THREE.Mesh>(null)
  useFrame((_, delta) => {
    if (!ref.current) return
    ref.current.position.x += delta * (active ? 1.8 : 0.35)
    if (ref.current.position.x > 6) ref.current.position.x = -6
  })
  return (
    <mesh ref={ref} position={[-6, 5.1, -5.2]}>
      <sphereGeometry args={[0.1, 8, 8]} />
      <meshBasicMaterial color={active ? "#7dd3fc" : "#64748b"} />
    </mesh>
  )
}

function SceneContents({ devices, onDevice }: FarmScene3DProps) {
  const incubator1 = deviceById(devices, "incubator-1")
  const incubator2 = deviceById(devices, "incubator-2")
  const hvac = deviceById(devices, "hvac")
  const protection = deviceById(devices, "animal-protection")
  return (
    <>
      <color attach="background" args={["#08111d"]} />
      <ambientLight intensity={1.2} />
      <directionalLight position={[8, 16, 12]} intensity={2.5} castShadow shadow-mapSize={[2048, 2048]} />
      <pointLight position={[-5, 6, 1]} color="#fbbf75" intensity={18} distance={18} />
      <pointLight position={[6, 6, -4]} color="#93c5fd" intensity={12} distance={16} />
      <PoultryHouse animalState={protection.operational} />
      <BuildingShell />
      <Incubator device={incubator1} position={[-7.2, 0, -4]} onDevice={onDevice} />
      <Incubator device={incubator2} position={[-3.4, 0, -4]} onDevice={onDevice} />
      <HVAC device={hvac} position={[5.2, 0, -4]} onDevice={onDevice} />
      <Protection device={protection} position={[5.2, 0, 1]} onDevice={onDevice} />
      <ExternalServices />
      <WaterInfrastructure />
      <CommunicationLinks devices={devices} />
      <Line points={[[5.2, 3.2, -2.8], [5.2, 5, -4], [0, 5, -4]]} color="#38bdf8" lineWidth={3} transparent opacity={0.75} />
      <Line points={[[5.2, 3.1, 1], [8, 5.1, 1], [8, 5.1, -4]]} color="#38bdf8" lineWidth={3} transparent opacity={0.7} />
      <Airflow active={hvac.running} />
      <gridHelper args={[40, 40, "#1e3a4d", "#102536"]} position={[0, -0.2, 0]} />
    </>
  )
}

export function FarmScene3D({ devices, onDevice }: FarmScene3DProps) {
  const controls = useRef<OrbitControlsImpl>(null)
  // Leaving the page while hovering a machine must not leave a pointer cursor behind.
  useEffect(() => () => {
    document.body.style.cursor = ""
  }, [])
  const resetCamera = () => {
    if (!controls.current) return
    controls.current.reset()
  }
  return (
    <Card className="overflow-hidden p-5 shadow-none">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">Farm facility · 3D cutaway</h3>
          <p className="mt-1 text-xs text-muted-foreground">Stylised diagram, not to scale. Select a system to inspect its demo telemetry.</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
            {["normal", "warning", "critical", "unknown"].map((state) => (
              <span key={state} className="flex items-center gap-1.5 capitalize"><span className="size-2 rounded-full" style={{ backgroundColor: statusColor(state as OperationalState) }} />{state}</span>
            ))}
          </div>
          <Separator orientation="vertical" className="h-5" />
          <Button size="sm" variant="outline" onClick={resetCamera}>Reset view</Button>
        </div>
      </div>
      <div className="mt-4 h-[620px] overflow-hidden rounded-md border border-slate-800 bg-[#08111d]">
        {/* "percentage" = PCF shadows; three r186 dropped the soft variant that plain `shadows` asks for. */}
        <Canvas shadows="percentage" camera={{ position: [20, 17, 22], fov: 42 }} dpr={[1, 1.6]}>
          <SceneContents devices={devices} onDevice={onDevice} />
          <OrbitControls ref={controls} makeDefault enablePan={false} minDistance={15} maxDistance={37} minPolarAngle={0.65} maxPolarAngle={1.25} target={[0, 3.2, 0]} />
        </Canvas>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <span>Drag to orbit · scroll to zoom · click a labeled system to inspect</span>
        <span>Blue lines: physical services · dashed links: simulated telemetry</span>
      </div>
    </Card>
  )
}
