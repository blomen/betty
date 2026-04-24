// NeuralNetworkSVG.tsx — real per-dim neuron visualization
// Input layer: always real (inputs[] always present when signal fired)
// Hidden layers: real when DQN activations available, dim grey otherwise
import { useMemo, useEffect, useState } from 'react'
import { DQN_SEGMENTS, HIDDEN_LAYERS, ACTION_NAMES, ACTION_COLORS } from './dqnConfig'
import type { DQNInferenceEvent } from '@/types/stocks'

interface Props {
  dqnInference: DQNInferenceEvent | null
  staleFactor: number  // 0 = fresh, 1 = fully stale
}

const W = 1000
const H = 260

// How many dots to show per segment (evenly sampled)
const MAX_DOTS_PER_SEG = 16
const DOT_R = 2.2
const SEG_COL_WIDTH = 28  // horizontal space per segment column
const INPUT_START_X = 36  // x of first segment column center

// Hidden layer x positions (after all 14 input columns)
const HIDDEN_XS = [460, 580, 680, 760]
const DOTS_PER_HIDDEN = 14  // sampled neurons shown per hidden layer
const LAYER_COLORS = ['#06b6d4', '#0891b2', '#8b5cf6', '#a78bfa']

const OUTPUT_X = 940

function sampleIndices(size: number, n: number): number[] {
  if (size <= n) return Array.from({ length: size }, (_, i) => i)
  const step = size / n
  return Array.from({ length: n }, (_, i) => Math.floor(i * step))
}

function hexToRgb(hex: string): [number, number, number] {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return [r, g, b]
}

function dotColor(segColor: string, value: number, opacity: number): string {
  if (value === 0) return `rgba(30,30,30,0.4)`
  if (value > 0) {
    const [r, g, b] = hexToRgb(segColor)
    return `rgba(${r},${g},${b},${opacity})`
  }
  // Negative: blend segment color toward red
  const [r, g, b] = hexToRgb(segColor)
  const red = Math.round(r * 0.3 + 239 * 0.7)
  const green = Math.round(g * 0.3)
  const blue = Math.round(b * 0.3)
  return `rgba(${red},${green},${blue},${opacity})`
}

export function NeuralNetworkSVG({ dqnInference, staleFactor }: Props) {
  // Separate flags: inputs always real; activations need DQN loaded
  const hasRealInputs = !!dqnInference?.inputs?.length
  const hasRealActivations = !!dqnInference?.activations?.layer1?.length

  // Simulation tick — only used for hidden layers when DQN offline
  const [simTick, setSimTick] = useState(0)
  useEffect(() => {
    if (hasRealActivations) return
    const id = setInterval(() => setSimTick(t => t + 1), 200)
    return () => clearInterval(id)
  }, [hasRealActivations])

  const hash = (a: number, b: number) => {
    const x = Math.sin(a * 127.1 + b * 311.7) * 43758.5453
    return x - Math.floor(x)
  }

  // Normalize across all 276 input dims
  const maxAbs = useMemo(() => {
    if (!hasRealInputs) return 1
    return Math.max(...dqnInference!.inputs.map(Math.abs), 0.001)
  }, [hasRealInputs, dqnInference])

  // Per-segment sampled dot data
  const segDots = useMemo(() =>
    DQN_SEGMENTS.map((seg, si) => {
      const cx = INPUT_START_X + si * SEG_COL_WIDTH
      const indices = sampleIndices(seg.end - seg.start, MAX_DOTS_PER_SEG)
      const n = indices.length
      const totalHeight = (n - 1) * ((H - 40) / (MAX_DOTS_PER_SEG - 1))
      const yStart = (H - 40 - totalHeight) / 2 + 8

      return indices.map((relIdx, di) => {
        const absIdx = seg.start + relIdx
        const value = hasRealInputs ? (dqnInference!.inputs[absIdx] ?? 0) : 0
        const opacity = hasRealInputs
          ? Math.max(0.08, Math.min(0.95, Math.abs(value) / maxAbs))
          : 0.12
        return {
          cx,
          cy: yStart + di * ((H - 40) / (MAX_DOTS_PER_SEG - 1)),
          value,
          opacity,
          color: dotColor(seg.color, value, opacity),
        }
      })
    }), [hasRealInputs, dqnInference, maxAbs])

  // Hidden layer dot positions and activations
  const hiddenDots = useMemo(() =>
    HIDDEN_LAYERS.map((size, li) => {
      const indices = sampleIndices(size, DOTS_PER_HIDDEN)
      const yStep = (H - 40) / (DOTS_PER_HIDDEN - 1)

      return indices.map((srcIdx, di) => {
        let opacity: number
        if (hasRealActivations) {
          const key = (['layer1', 'layer2', 'layer3', 'layer4'] as const)[li]
          const acts = dqnInference?.activations?.[key] ?? []
          const maxAct = Math.max(...acts.map(Math.abs), 0.001)
          opacity = Math.max(0.05, Math.min(0.95, Math.abs(acts[srcIdx] ?? 0) / maxAct))
        } else {
          opacity = 0.05 + 0.1 * hash(simTick - li, di + li * 100)
        }
        return {
          cx: HIDDEN_XS[li],
          cy: 8 + di * yStep,
          opacity,
          color: LAYER_COLORS[li],
        }
      })
    }), [hasRealActivations, dqnInference, simTick])

  // Q-values and winner
  const qValues = dqnInference?.q_values ?? [0, 0, 0]
  const qMax = Math.max(...qValues.map(Math.abs), 0.001)
  const winnerIdx = hasRealInputs ? qValues.indexOf(Math.max(...qValues)) : -1

  // Output node y positions
  const outputYs = useMemo(() => {
    const spacing = 72
    const center = H / 2
    return ACTION_NAMES.map((_, i) => center + (i - 1) * spacing)
  }, [])

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full h-full"
      preserveAspectRatio="xMidYMid meet"
      style={{ display: 'block' }}
    >
      {/* Input segment dots */}
      {DQN_SEGMENTS.map((seg, si) => (
        <g key={seg.name} opacity={1 - staleFactor * 0.8}>
          {segDots[si].map((dot, di) => (
            <circle
              key={di}
              cx={dot.cx}
              cy={dot.cy}
              r={DOT_R}
              fill={dot.color}
            />
          ))}
          <text
            x={INPUT_START_X + si * SEG_COL_WIDTH}
            y={H - 4}
            fill={seg.color}
            fontSize="5"
            fontFamily="monospace"
            textAnchor="middle"
            opacity={0.45}
          >
            {seg.name.split(' ')[0].slice(0, 4).toUpperCase()}
          </text>
        </g>
      ))}

      {/* Hidden layer dots */}
      {HIDDEN_LAYERS.map((size, li) => (
        <g key={`hidden-${li}`} opacity={hasRealActivations ? 1 - staleFactor * 0.8 : 1}>
          <text
            x={HIDDEN_XS[li]}
            y={12}
            fill={LAYER_COLORS[li]}
            fontSize="7"
            fontFamily="monospace"
            textAnchor="middle"
            opacity={0.4}
          >
            {size}
          </text>
          {hiddenDots[li].map((dot, di) => (
            <circle
              key={di}
              cx={dot.cx}
              cy={dot.cy}
              r={3}
              fill={dot.color}
              opacity={dot.opacity}
            />
          ))}
        </g>
      ))}

      {/* Output nodes */}
      {ACTION_NAMES.map((name, i) => {
        const isWinner = i === winnerIdx
        const q = qValues[i]
        const r = hasRealInputs ? Math.max(5, Math.min(14, 5 + Math.abs(q) / qMax * 9)) : 6
        const color = ACTION_COLORS[i]
        return (
          <g key={name}>
            <circle
              cx={OUTPUT_X}
              cy={outputYs[i]}
              r={r}
              fill={color}
              opacity={isWinner ? 0.9 : 0.2}
            />
            <text
              x={OUTPUT_X + r + 6}
              y={outputYs[i] - 2}
              fill={color}
              fontSize="9"
              fontFamily="monospace"
              fontWeight="bold"
              opacity={isWinner ? 1 : 0.3}
            >
              {name}
            </text>
            {hasRealInputs && (
              <text
                x={OUTPUT_X + r + 6}
                y={outputYs[i] + 9}
                fill={color}
                fontSize="8"
                fontFamily="monospace"
                opacity={isWinner ? 0.75 : 0.25}
              >
                {q.toFixed(3)}
              </text>
            )}
          </g>
        )
      })}

      {/* Status labels */}
      <text x={8} y={H - 6} fill={hasRealInputs ? '#22c55e' : '#444'} fontSize="7" fontFamily="monospace">
        {hasRealInputs ? '● LIVE INPUTS' : '○ NO SIGNAL'}
      </text>
      <text x={W - 8} y={H - 6} fill={hasRealActivations ? '#06b6d4' : '#333'} fontSize="7" fontFamily="monospace" textAnchor="end">
        {hasRealActivations ? 'DQN ACTIVE' : 'DQN OFFLINE'}
      </text>
    </svg>
  )
}
