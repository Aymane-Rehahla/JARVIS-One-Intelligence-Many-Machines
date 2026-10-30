/**
 * A force layout for the 256 neurons, driven only by the exported links.
 *
 * The CTM has no geometry: every neuron is the same kind and all 256 feed all 256 through the shared synapses.
 * Any placement is a layout choice. This one pulls together neurons joined by one of the exported strongest
 * influences and pushes every pair apart, so clusters on screen are clusters in the real links. It is
 * deterministic (no randomness), so the same brain always draws the same way.
 */
export type LayoutPoint = { x: number; y: number }

const cache = new Map<string, LayoutPoint[]>()

export function layoutNeurons(key: string, count: number, edges: [number, number][], width = 1000, height = 640): LayoutPoint[] {
  const cached = cache.get(key)
  if (cached) return cached
  // Start on a golden-angle spiral, then relax (Fruchterman–Reingold with linear cooling).
  const golden = Math.PI * (3 - Math.sqrt(5))
  const xs = new Float64Array(count), ys = new Float64Array(count)
  for (let i = 0; i < count; i += 1) {
    const r = Math.sqrt((i + 0.5) / count) * Math.min(width, height) * 0.45
    xs[i] = width / 2 + Math.cos(i * golden) * r * (width / height)
    ys[i] = height / 2 + Math.sin(i * golden) * r
  }
  const k = Math.sqrt((width * height) / count) * 0.62
  const dx = new Float64Array(count), dy = new Float64Array(count)
  const iterations = 260
  for (let step = 0; step < iterations; step += 1) {
    dx.fill(0)
    dy.fill(0)
    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        let ox = xs[i] - xs[j], oy = ys[i] - ys[j]
        let d2 = ox * ox + oy * oy
        if (d2 < 0.01) { ox = 0.1 * ((i % 7) - 3); oy = 0.1 * ((j % 5) - 2); d2 = ox * ox + oy * oy + 0.01 }
        const force = (k * k) / d2
        dx[i] += ox * force; dy[i] += oy * force
        dx[j] -= ox * force; dy[j] -= oy * force
      }
    }
    for (const [a, b] of edges) {
      const ox = xs[a] - xs[b], oy = ys[a] - ys[b], d = Math.sqrt(ox * ox + oy * oy) + 0.01
      const force = d / k
      dx[a] -= ox * force; dy[a] -= oy * force
      dx[b] += ox * force; dy[b] += oy * force
    }
    const temperature = (width / 12) * (1 - step / iterations) + 0.5
    for (let i = 0; i < count; i += 1) {
      // A weak pull to the centre keeps loosely linked neurons on the canvas.
      dx[i] += (width / 2 - xs[i]) * 0.02
      dy[i] += (height / 2 - ys[i]) * 0.02
      const d = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]) + 0.01
      xs[i] += (dx[i] / d) * Math.min(d, temperature)
      ys[i] += (dy[i] / d) * Math.min(d, temperature)
    }
  }
  // Fit to the frame with a margin.
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
  for (let i = 0; i < count; i += 1) {
    minX = Math.min(minX, xs[i]); maxX = Math.max(maxX, xs[i]); minY = Math.min(minY, ys[i]); maxY = Math.max(maxY, ys[i])
  }
  const margin = 28, scale = Math.min((width - margin * 2) / (maxX - minX || 1), (height - margin * 2) / (maxY - minY || 1))
  const offsetX = (width - (maxX - minX) * scale) / 2, offsetY = (height - (maxY - minY) * scale) / 2
  const points = Array.from({ length: count }, (_, i) => ({ x: offsetX + (xs[i] - minX) * scale, y: offsetY + (ys[i] - minY) * scale }))
  cache.set(key, points)
  return points
}
