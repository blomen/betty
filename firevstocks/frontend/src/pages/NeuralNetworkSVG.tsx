// NeuralNetworkSVG.tsx — real-time Dueling DQN visualization
// Continuously animates with simulated activity; real inference overrides when available
import { useMemo, useRef, useEffect, useState } from 'react'
import { DQN_SEGMENTS, HIDDEN_LAYERS, ACTION_NAMES, ACTION_COLORS } from './dqnConfig'
import type { DQNInferenceEvent } from '@/types'

interface Props {
  dqnInference: DQNInferenceEvent | null
}

const W = 960
const H = 420
const INPUT_X = 80
const LAYER_XS = [200, 340, 480, 600]
const OUTPUT_X = 760
const DOTS_PER_LAYER = [16, 16, 12, 8]
const LAYER_COLORS = ['#06b6d4', '#0891b2', '#8b5cf6', '#a78bfa']
const SIM_INTERVAL = 120 // ms between simulation ticks

export function NeuralNetworkSVG({ dqnInference }: Props) {
  const frameRef = useRef(0)
  const [simTick, setSimTick] = useState(0)

  // Continuous simulation timer
  useEffect(() => {
    const id = setInterval(() => setSimTick(t => t + 1), SIM_INTERVAL)
    return () => clearInterval(id)
  }, [])

  // Stable pseudo-random from tick + index (no Math.random in render)
  const hash = (a: number, b: number) => {
    const x = Math.sin(a * 127.1 + b * 311.7) * 43758.5453
    return x - Math.floor(x)
  }

  // ── Segment positions ──
  const segmentNodes = useMemo(() => {
    const spacing = (H - 60) / (DQN_SEGMENTS.length - 1)
    return DQN_SEGMENTS.map((seg, i) => ({
      ...seg,
      x: INPUT_X,
      y: 30 + i * spacing,
    }))
  }, [])

  // ── Hidden layer dot positions ──
  const hiddenDots = useMemo(() =>
    HIDDEN_LAYERS.map((size, li) => {
      const n = DOTS_PER_LAYER[li]
      const step = Math.max(1, Math.floor(size / n))
      const dots: { srcIdx: number; y: number }[] = []
      for (let i = 0; i < size && dots.length < n; i += step)
        dots.push({ srcIdx: i, y: 0 })
      const spacing = (H - 60) / Math.max(dots.length - 1, 1)
      dots.forEach((d, j) => { d.y = 30 + j * spacing })
      return dots
    }), [])

  // ── Output positions ──
  const outputNodes = useMemo(() => {
    const spacing = 90
    const center = H / 2
    return ACTION_NAMES.map((name, i) => ({
      name,
      color: ACTION_COLORS[i],
      x: OUTPUT_X,
      y: center + (i - (ACTION_NAMES.length - 1) / 2) * spacing,
    }))
  }, [])

  // ── Activations: real data or simulated ──
  const hasReal = !!dqnInference?.activations

  const segActs = useMemo(() =>
    DQN_SEGMENTS.map((seg, i) => {
      if (hasReal && dqnInference?.inputs) {
        let sum = 0, count = 0
        for (let j = seg.start; j < seg.end; j++) { sum += Math.abs(dqnInference.inputs[j] ?? 0); count++ }
        return count > 0 ? Math.min(1, sum / count) : 0
      }
      // Simulated: gentle waves
      return 0.15 + 0.35 * hash(simTick, i * 7)
    }), [hasReal, dqnInference, simTick])

  const hiddenActs = useMemo(() =>
    HIDDEN_LAYERS.map((_, li) => {
      if (hasReal) {
        const key = (['layer1', 'layer2', 'layer3', 'layer4'] as const)[li]
        const acts = dqnInference?.activations?.[key] ?? []
        return hiddenDots[li].map(d => Math.min(1, Math.abs(acts[d.srcIdx] ?? 0)))
      }
      // Simulated: cascading waves across layers
      return hiddenDots[li].map((_, di) =>
        0.1 + 0.4 * hash(simTick - li * 2, di + li * 100)
      )
    }), [hasReal, dqnInference, simTick, hiddenDots])

  const qValues = dqnInference?.q_values ?? [0, 0, 0]
  const winnerIdx = hasReal ? qValues.indexOf(Math.max(...qValues)) : -1

  // Simulated output pulses
  const outActs = useMemo(() =>
    ACTION_NAMES.map((_, i) =>
      hasReal
        ? (i === winnerIdx ? 1 : 0.15)
        : 0.15 + 0.3 * hash(simTick, i * 53 + 999)
    ), [hasReal, winnerIdx, simTick])

  // ── Connections: real or simulated ──
  const connections = useMemo(() => {
    const lines: { d: string; color: string; width: number; opacity: number }[] = []
    const curve = (x1: number, y1: number, x2: number, y2: number) => {
      const mx = (x1 + x2) / 2
      return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`
    }

    if (hasReal && dqnInference?.connections) {
      // Real connections (same logic as before, simplified)
      const addReal = (
        conns: { from_idx: number; to_idx: number; strength: number; sign: number }[],
        fromNodes: { x: number; y: number }[],
        toNodes: { x: number; y: number }[],
        fromLookup: (idx: number) => number,
        toLookup: (idx: number) => number,
        defaultColor: string,
      ) => {
        for (const c of conns) {
          const fi = fromLookup(c.from_idx)
          const ti = toLookup(c.to_idx)
          if (fi === -1 || ti === -1) continue
          const fn = fromNodes[fi], tn = toNodes[ti]
          if (!fn || !tn) continue
          lines.push({
            d: curve(fn.x + 6, fn.y, tn.x - 6, tn.y),
            color: c.sign === 1 ? defaultColor : '#ef4444',
            width: Math.max(0.5, c.strength * 2.5),
            opacity: Math.max(0.04, Math.min(0.6, c.strength)),
          })
        }
      }

      // Input → L1
      const segConns = new Map<string, { strength: number; sign: number; si: number; di: number }>()
      for (const c of dqnInference.connections.input_l1 ?? []) {
        const si = DQN_SEGMENTS.findIndex(s => c.from_idx >= s.start && c.from_idx < s.end)
        const di = hiddenDots[0].findIndex(d => d.srcIdx === c.to_idx)
        if (si === -1 || di === -1) continue
        const key = `${si}-${di}`
        const ex = segConns.get(key)
        if (!ex || c.strength > ex.strength) segConns.set(key, { ...c, si, di })
      }
      for (const val of segConns.values()) {
        const sn = segmentNodes[val.si], dn = hiddenDots[0][val.di]
        if (!sn || !dn) continue
        lines.push({
          d: curve(sn.x + 6, sn.y, LAYER_XS[0] - 5, dn.y),
          color: val.sign === 1 ? sn.color : '#ef4444',
          width: Math.max(0.5, val.strength * 2.5),
          opacity: Math.max(0.04, Math.min(0.6, val.strength)),
        })
      }

      // Hidden → hidden
      const pairs = [
        { conns: dqnInference.connections.l1_l2, fromLi: 0, toLi: 1 },
        { conns: dqnInference.connections.l2_l3, fromLi: 1, toLi: 2 },
        { conns: dqnInference.connections.l3_l4, fromLi: 2, toLi: 3 },
      ]
      for (const { conns, fromLi, toLi } of pairs) {
        for (const c of conns ?? []) {
          const fi = hiddenDots[fromLi].findIndex(d => d.srcIdx === c.from_idx)
          const ti = hiddenDots[toLi].findIndex(d => d.srcIdx === c.to_idx)
          if (fi === -1 || ti === -1) continue
          lines.push({
            d: curve(LAYER_XS[fromLi] + 5, hiddenDots[fromLi][fi].y,
                     LAYER_XS[toLi] - 5, hiddenDots[toLi][ti].y),
            color: c.sign === 1 ? LAYER_COLORS[toLi] : '#ef4444',
            width: Math.max(0.5, c.strength * 2.5),
            opacity: Math.max(0.04, Math.min(0.6, c.strength)),
          })
        }
      }

      // L4 → output
      for (const c of dqnInference.connections.l4_output ?? []) {
        const fi = hiddenDots[3].findIndex(d => d.srcIdx === c.from_idx)
        const out = outputNodes[c.to_idx]
        if (fi === -1 || !out) continue
        lines.push({
          d: curve(LAYER_XS[3] + 5, hiddenDots[3][fi].y, out.x - 14, out.y),
          color: c.sign === 1 ? out.color : '#ef4444',
          width: Math.max(0.5, c.strength * 2.5),
          opacity: Math.max(0.04, Math.min(0.6, c.strength)),
        })
      }
    } else {
      // Simulated sparse connections — just enough to look alive
      // Input → L1
      for (let si = 0; si < segmentNodes.length; si++) {
        for (let di = 0; di < hiddenDots[0].length; di++) {
          const v = hash(si * 3 + simTick, di * 7)
          if (v > 0.7) {
            const sn = segmentNodes[si], dn = hiddenDots[0][di]
            const str = segActs[si] * v
            lines.push({
              d: curve(sn.x + 6, sn.y, LAYER_XS[0] - 5, dn.y),
              color: sn.color,
              width: 0.5 + str * 1.5,
              opacity: str * 0.4,
            })
          }
        }
      }
      // Hidden → hidden
      for (let li = 0; li < 3; li++) {
        for (let fi = 0; fi < hiddenDots[li].length; fi++) {
          for (let ti = 0; ti < hiddenDots[li + 1].length; ti++) {
            const v = hash(fi * 11 + simTick - li, ti * 13 + li * 100)
            if (v > 0.75) {
              const str = hiddenActs[li][fi] * v
              lines.push({
                d: curve(LAYER_XS[li] + 5, hiddenDots[li][fi].y,
                         LAYER_XS[li + 1] - 5, hiddenDots[li + 1][ti].y),
                color: v > 0.9 ? '#ef4444' : LAYER_COLORS[li + 1],
                width: 0.5 + str * 1.5,
                opacity: str * 0.35,
              })
            }
          }
        }
      }
      // L4 → output
      for (let fi = 0; fi < hiddenDots[3].length; fi++) {
        for (let oi = 0; oi < outputNodes.length; oi++) {
          const v = hash(fi * 17 + simTick, oi * 31 + 500)
          if (v > 0.6) {
            const str = hiddenActs[3][fi] * v
            lines.push({
              d: curve(LAYER_XS[3] + 5, hiddenDots[3][fi].y, outputNodes[oi].x - 14, outputNodes[oi].y),
              color: outputNodes[oi].color,
              width: 0.5 + str * 1.5,
              opacity: str * 0.35,
            })
          }
        }
      }
    }

    return lines
  }, [hasReal, dqnInference, simTick, segmentNodes, hiddenDots, outputNodes, segActs, hiddenActs])

  const statusLabel = useMemo(() => {
    if (!dqnInference) return 'SIMULATING'
    const action = dqnInference.action
    const model = dqnInference.model_type ?? 'dqn'
    if (dqnInference.trigger === 'zone_entry')
      return `ZONE ${dqnInference.zone_center?.toFixed(2) ?? ''} → ${action} [${model}]`
    return `${dqnInference.trigger?.toUpperCase() ?? ''} → ${action} [${model}]`
  }, [dqnInference])

  const duelingMidX = (LAYER_XS[3] + OUTPUT_X) / 2

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-full" preserveAspectRatio="xMidYMid meet">
      <defs>
        <filter id="nn-glow" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>

      <style>{`
        @keyframes nn-flow {
          0% { stroke-dashoffset: 16; }
          100% { stroke-dashoffset: 0; }
        }
        .nn-conn-active {
          stroke-dasharray: 8 8;
          animation: nn-flow 0.6s linear infinite;
        }
      `}</style>

      {/* Connections */}
      {connections.map((c, i) => (
        <path
          key={i}
          d={c.d}
          fill="none"
          stroke={c.color}
          strokeWidth={c.width}
          opacity={c.opacity}
          className={c.opacity > 0.2 ? 'nn-conn-active' : undefined}
        />
      ))}

      {/* Input segment nodes */}
      {segmentNodes.map((seg, i) => {
        const act = segActs[i]
        const r = 3.5 + act * 3.5
        return (
          <g key={seg.name}>
            {act > 0.4 && (
              <circle cx={seg.x} cy={seg.y} r={r + 4}
                fill={seg.color} opacity={act * 0.2} filter="url(#nn-glow)" />
            )}
            <circle cx={seg.x} cy={seg.y} r={r}
              fill={seg.color} opacity={Math.max(0.15, act)} />
            <text x={seg.x - 10} y={seg.y + 3}
              fill={act > 0.3 ? seg.color : '#444'}
              fontSize="7" fontFamily="monospace" fontWeight="bold"
              textAnchor="end" opacity={Math.max(0.35, act)}>
              {seg.name}
            </text>
          </g>
        )
      })}

      {/* Hidden layers */}
      {HIDDEN_LAYERS.map((size, li) => {
        const lx = LAYER_XS[li]
        const color = LAYER_COLORS[li]
        const dots = hiddenDots[li]
        const acts = hiddenActs[li]
        return (
          <g key={`layer-${li}`}>
            <text x={lx} y={14} fill={color} fontSize="8" fontFamily="monospace"
              textAnchor="middle" fontWeight="bold" opacity={0.7}>
              {size}
            </text>
            <text x={lx} y={H - 12} fill={color} fontSize="6" fontFamily="monospace"
              textAnchor="middle" opacity={0.3}>
              {li < 3 ? 'LN+ReLU' : 'ReLU'}
            </text>
            {dots.map((dot, di) => {
              const act = acts[di] ?? 0
              const r = 2.5 + act * 2.5
              return (
                <g key={di}>
                  {act > 0.4 && (
                    <circle cx={lx} cy={dot.y} r={r + 3}
                      fill={color} opacity={act * 0.2} filter="url(#nn-glow)" />
                  )}
                  <circle cx={lx} cy={dot.y} r={r}
                    fill={color} opacity={Math.max(0.08, act)} />
                </g>
              )
            })}
          </g>
        )
      })}

      {/* Dueling annotation */}
      <text x={duelingMidX} y={outputNodes[0].y - 50}
        fill="#555" fontSize="7" fontFamily="monospace" textAnchor="middle">
        Q-VALUES
      </text>
      <text x={duelingMidX} y={outputNodes[outputNodes.length - 1].y + 30}
        fill="#444" fontSize="6" fontFamily="monospace" textAnchor="middle" opacity={0.5}>
        V + (A - mean(A))
      </text>

      {/* Output nodes */}
      {outputNodes.map((o, i) => {
        const act = outActs[i]
        const isWinner = i === winnerIdx
        const r = isWinner ? 14 : 8 + act * 4
        return (
          <g key={o.name}>
            {act > 0.3 && (
              <circle cx={o.x} cy={o.y} r={r + 5}
                fill={o.color} opacity={act * 0.15} filter="url(#nn-glow)" />
            )}
            <circle cx={o.x} cy={o.y} r={r}
              fill={o.color} opacity={Math.max(0.15, act)}
              filter={isWinner ? 'url(#nn-glow)' : undefined} />
            <text x={o.x + r + 8} y={o.y - 3}
              fill={o.color} fontSize="10" fontFamily="monospace" fontWeight="bold"
              opacity={Math.max(0.3, act)}>
              {o.name}
            </text>
            {hasReal && (
              <text x={o.x + r + 8} y={o.y + 10}
                fill={isWinner ? o.color : '#444'}
                fontSize="9" fontFamily="monospace">
                {qValues[i].toFixed(3)}
              </text>
            )}
          </g>
        )
      })}

      {/* Status */}
      <text x={8} y={H - 8} fill={hasReal ? '#666' : '#444'} fontSize="8" fontFamily="monospace">
        {statusLabel}
      </text>
      <text x={W / 2} y={H - 8} fill="#333" fontSize="7" fontFamily="monospace" textAnchor="middle">
        Dueling DQN: 276 → 256 → 256 → 128 → 64 → V+A → Q(3)
      </text>
    </svg>
  )
}
