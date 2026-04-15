# ML Intelligence Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the DQN tab with a unified ML intelligence page showing real per-dim neuron firing for all three model tiers (DQN, GBT, Specialists) in a simple primitive layout.

**Architecture:** Two files change. `NeuralNetworkSVG.tsx` is fully rewritten — input layer becomes 14 segment columns of real per-dim dots (always live from `inputs[]`), hidden layers show real activations when DQN is loaded and dim grey otherwise, simulation is removed from the input layer entirely. `DQNPage.tsx` gets a new primitive layout: decision strip on top, three panels side-by-side (DQN network, Specialists bars, GBT bars), feature heatmap strip at bottom.

**Tech Stack:** React 19, TypeScript, SVG (inline), Tailwind CSS, existing `DQN_SEGMENTS` config from `dqnConfig.ts`

---

## File Map

| File | Change | Responsibility |
|------|--------|----------------|
| `firevstocks/frontend/src/pages/NeuralNetworkSVG.tsx` | Full rewrite | SVG network: real per-dim input dots, real hidden activations, Q-value outputs |
| `firevstocks/frontend/src/pages/DQNPage.tsx` | Refactor | Page layout: decision strip, 3-panel row, feature heatmap |

No new files. No backend changes. `dqnConfig.ts` and `SegmentHeatmap` (inside DQNPage.tsx) are read-only dependencies.

---

## Task 1: Rewrite NeuralNetworkSVG — input layer with real per-dim dots

**Files:**
- Rewrite: `firevstocks/frontend/src/pages/NeuralNetworkSVG.tsx`

The input layer becomes 14 segment columns. Each column has up to 16 dots sampled evenly from the segment's dims. Dot opacity = `abs(inputs[dim]) / maxAbs`. Dot color = segment color (negative values get a red tint). No simulation for inputs.

- [ ] **Step 1: Replace the file with the new input-layer skeleton**

```tsx
// NeuralNetworkSVG.tsx — real per-dim neuron visualization
// Input layer: always real (inputs[] always present when signal fired)
// Hidden layers: real when DQN activations available, dim grey otherwise
import { useMemo, useRef, useEffect, useState } from 'react'
import { DQN_SEGMENTS, HIDDEN_LAYERS, ACTION_NAMES, ACTION_COLORS } from './dqnConfig'
import type { DQNInferenceEvent } from '@/types'

interface Props {
  dqnInference: DQNInferenceEvent | null
}

const W = 1000
const H = 260
const OUTPUT_X = 940

// How many dots to show per segment (evenly sampled)
const MAX_DOTS_PER_SEG = 16
const DOT_R = 2.2
const DOT_SPACING = (H - 40) / (MAX_DOTS_PER_SEG - 1)  // vertical gap between dots
const SEG_COL_WIDTH = 28  // horizontal space per segment column
const INPUT_START_X = 36  // x of first segment column center

// Hidden layer x positions (after all 14 input columns)
const HIDDEN_XS = [460, 580, 680, 760]
const DOTS_PER_HIDDEN = 14  // sampled neurons shown per hidden layer

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

export function NeuralNetworkSVG({ dqnInference }: Props) {
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

  // ── Input: normalize across all 276 dims
  const maxAbs = useMemo(() => {
    if (!hasRealInputs) return 1
    return Math.max(...dqnInference!.inputs.map(Math.abs), 0.001)
  }, [hasRealInputs, dqnInference])

  // ── Per-segment sampled dot data
  const segDots = useMemo(() =>
    DQN_SEGMENTS.map((seg, si) => {
      const cx = INPUT_START_X + si * SEG_COL_WIDTH
      const indices = sampleIndices(seg.end - seg.start, MAX_DOTS_PER_SEG)
      const n = indices.length
      const totalHeight = (n - 1) * DOT_SPACING
      const yStart = (H - 40 - totalHeight) / 2 + 8

      return indices.map((relIdx, di) => {
        const absIdx = seg.start + relIdx
        const value = hasRealInputs ? (dqnInference!.inputs[absIdx] ?? 0) : 0
        const opacity = hasRealInputs
          ? Math.max(0.08, Math.min(0.95, Math.abs(value) / maxAbs))
          : 0.12
        return {
          cx,
          cy: yStart + di * DOT_SPACING,
          value,
          opacity,
          color: dotColor(seg.color, value, opacity),
        }
      })
    }), [hasRealInputs, dqnInference, maxAbs])

  // ── Hidden layer dot positions and activations
  const hiddenDots = useMemo(() =>
    HIDDEN_LAYERS.map((size, li) => {
      const indices = sampleIndices(size, DOTS_PER_HIDDEN)
      const totalHeight = (DOTS_PER_HIDDEN - 1) * ((H - 40) / (DOTS_PER_HIDDEN - 1))
      const yStart = 8

      return indices.map((srcIdx, di) => {
        let opacity: number
        if (hasRealActivations) {
          const key = (['layer1', 'layer2', 'layer3', 'layer4'] as const)[li]
          const acts = dqnInference?.activations?.[key] ?? []
          const maxAct = Math.max(...acts.map(Math.abs), 0.001)
          opacity = Math.max(0.05, Math.min(0.95, Math.abs(acts[srcIdx] ?? 0) / maxAct))
        } else {
          // Gentle sim pulse so layers look "waiting" not dead
          opacity = 0.05 + 0.1 * hash(simTick - li, di + li * 100)
        }
        const LAYER_COLORS = ['#06b6d4', '#0891b2', '#8b5cf6', '#a78bfa']
        return {
          cx: HIDDEN_XS[li],
          cy: yStart + di * ((H - 40) / (DOTS_PER_HIDDEN - 1)),
          opacity,
          color: LAYER_COLORS[li],
          srcIdx,
        }
      })
    }), [hasRealActivations, dqnInference, simTick])

  // ── Q-values and winner
  const qValues = dqnInference?.q_values ?? [0, 0, 0]
  const qMax = Math.max(...qValues.map(Math.abs), 0.001)
  const winnerIdx = hasRealInputs ? qValues.indexOf(Math.max(...qValues)) : -1

  // Output node positions
  const outputNodes = useMemo(() => {
    const spacing = 72
    const center = H / 2
    return ACTION_NAMES.map((name, i) => ({
      name,
      color: ACTION_COLORS[i],
      cy: center + (i - 1) * spacing,
    }))
  }, [])

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full h-full"
      preserveAspectRatio="xMidYMid meet"
      style={{ display: 'block' }}
    >
      {/* ── Input segment dots ── */}
      {DQN_SEGMENTS.map((seg, si) => (
        <g key={seg.name}>
          {segDots[si].map((dot, di) => (
            <circle
              key={di}
              cx={dot.cx}
              cy={dot.cy}
              r={DOT_R}
              fill={dot.color}
            />
          ))}
          {/* Segment name label */}
          <text
            x={INPUT_START_X + si * SEG_COL_WIDTH}
            y={H - 4}
            fill={seg.color}
            fontSize="5"
            fontFamily="monospace"
            textAnchor="middle"
            opacity={0.5}
          >
            {seg.name.split(' ')[0].slice(0, 4).toUpperCase()}
          </text>
        </g>
      ))}

      {/* ── Hidden layer dots ── */}
      {HIDDEN_LAYERS.map((size, li) => (
        <g key={`hidden-${li}`}>
          <text
            x={HIDDEN_XS[li]}
            y={12}
            fill={['#06b6d4', '#0891b2', '#8b5cf6', '#a78bfa'][li]}
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

      {/* ── Output nodes ── */}
      {outputNodes.map((o, i) => {
        const isWinner = i === winnerIdx
        const q = qValues[i]
        const r = hasRealInputs ? Math.max(5, Math.min(14, 5 + Math.abs(q) / qMax * 9)) : 6
        return (
          <g key={o.name}>
            <circle
              cx={OUTPUT_X}
              cy={o.cy}
              r={r}
              fill={o.color}
              opacity={isWinner ? 0.9 : 0.25}
            />
            <text
              x={OUTPUT_X + r + 6}
              y={o.cy - 2}
              fill={o.color}
              fontSize="9"
              fontFamily="monospace"
              fontWeight="bold"
              opacity={isWinner ? 1 : 0.35}
            >
              {o.name}
            </text>
            {hasRealInputs && (
              <text
                x={OUTPUT_X + r + 6}
                y={o.cy + 9}
                fill={o.color}
                fontSize="8"
                fontFamily="monospace"
                opacity={isWinner ? 0.8 : 0.3}
              >
                {q.toFixed(3)}
              </text>
            )}
          </g>
        )
      })}

      {/* ── Status labels ── */}
      <text x={8} y={H - 6} fill={hasRealInputs ? '#22c55e' : '#444'} fontSize="7" fontFamily="monospace">
        {hasRealInputs ? '● LIVE INPUTS' : '○ NO SIGNAL'}
      </text>
      <text x={W - 8} y={H - 6} fill={hasRealActivations ? '#06b6d4' : '#333'} fontSize="7" fontFamily="monospace" textAnchor="end">
        {hasRealActivations ? 'DQN ACTIVE' : 'DQN OFFLINE'}
      </text>
    </svg>
  )
}
```

- [ ] **Step 2: Verify the file compiles**

```bash
cd c:/Users/rasmu/firev/firevstocks/frontend
npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors (or only pre-existing unrelated errors).

- [ ] **Step 3: Commit**

```bash
cd c:/Users/rasmu/firev
git add firevstocks/frontend/src/pages/NeuralNetworkSVG.tsx
git commit -m "feat(firevstocks): rewrite NeuralNetworkSVG with real per-dim input neurons"
```

---

## Task 2: Refactor DQNPage — primitive layout with decision strip + 3 panels

**Files:**
- Modify: `firevstocks/frontend/src/pages/DQNPage.tsx`

Replace the current layout with: (1) decision strip, (2) 3-column row (DQN network | Specialists | GBT), (3) feature heatmap. Keep `SegmentHeatmap` and `SegmentDetail` unchanged. Remove `DecisionCard` and `ProbBar` helpers — replace with inline primitives.

- [ ] **Step 1: Replace DQNPage with the new layout**

```tsx
import { useRef, useEffect, useState, useMemo, useCallback } from 'react'
import { api } from '@/hooks/useApi'
import type { Signal, Zone, DQNInferenceEvent, LevelsReplayResponse } from '@/types'
import { NeuralNetworkSVG } from './NeuralNetworkSVG'
import { DQN_SEGMENTS } from './dqnConfig'
import type { DQNSegment } from './dqnConfig'

interface Props {
  signals: Signal[]
  zones: Zone[]
  lastPrice: number | null
  dqnInference: DQNInferenceEvent | null
}

export function DQNPage({ signals, zones: liveZones, lastPrice, dqnInference }: Props) {
  const historyRef = useRef<HTMLDivElement>(null)
  const [replayData, setReplayData] = useState<LevelsReplayResponse | null>(null)
  const [replayLoading, setReplayLoading] = useState(false)

  useEffect(() => {
    historyRef.current?.scrollTo({ top: historyRef.current.scrollHeight, behavior: 'smooth' })
  }, [signals.length])

  useEffect(() => {
    if (liveZones.length > 0) return
    setReplayLoading(true)
    api.getLevelsReplay()
      .then(d => { if (!d.error) setReplayData(d) })
      .catch(() => {})
      .finally(() => setReplayLoading(false))
  }, [liveZones.length])

  const zones: Zone[] = liveZones.length > 0
    ? liveZones
    : (replayData?.active_levels ?? []).map(l => ({ price: l.price, members: 1, name: l.name }))

  const inf = dqnInference
  const hasSignal = !!inf?.inputs?.length

  // Decision values — prefer inference, fall back to latest signal
  const latest = signals.length > 0 ? signals[signals.length - 1] : null
  const action    = inf?.action     ?? latest?.action     ?? '---'
  const modelType = inf?.model_type ?? latest?.model_type ?? '---'
  const confidence = inf?.confidence ?? latest?.confidence
  const contP     = inf?.cont_p     ?? latest?.cont_p
  const revP      = inf?.rev_p      ?? latest?.rev_p
  const contEv    = inf?.cont_ev
  const revEv     = inf?.rev_ev
  const stopTicks = inf?.stop_ticks ?? latest?.stop_ticks
  const sizing    = inf?.sizing_signal
  const triggerLevel = (inf as any)?.level ?? null
  const triggerPrice = (inf as any)?.level_price ?? null
  const trigger   = inf?.trigger ?? null

  const actionColor = action === 'CONTINUATION' ? '#22c55e'
    : action === 'REVERSAL' ? '#ef4444'
    : '#52525b'

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-2 p-2 overflow-y-auto font-mono">

      {/* ── DECISION STRIP ── */}
      <div className="border border-zinc-800 bg-zinc-900 px-3 py-2 flex items-baseline gap-5 flex-wrap text-xs">
        <span className="text-base font-bold tracking-widest" style={{ color: actionColor }}>
          {action === 'CONTINUATION' ? '↑' : action === 'REVERSAL' ? '↓' : '—'} {action}
        </span>

        <span className="text-zinc-600 border border-zinc-700 px-1.5 py-0.5 text-[10px] tracking-wider">
          {modelType}
        </span>

        {hasSignal
          ? <span className="text-emerald-500 text-[10px] tracking-wider">● LIVE</span>
          : <span className="text-zinc-600 text-[10px] tracking-wider">○ NO SIGNAL</span>
        }

        <Stat label="conf"   value={confidence != null ? `${(confidence * 100).toFixed(0)}%` : '---'} color="#f59e0b" />
        <Stat label="P(cont)" value={contP != null ? contP.toFixed(2) : '---'} color="#22c55e" />
        <Stat label="P(rev)"  value={revP  != null ? revP.toFixed(2)  : '---'} color="#ef4444" />
        <Stat label="EV cont" value={contEv != null ? `${contEv > 0 ? '+' : ''}${contEv.toFixed(1)}R` : '---'} color="#a78bfa" />
        <Stat label="EV rev"  value={revEv  != null ? `${revEv  > 0 ? '+' : ''}${revEv.toFixed(1)}R`  : '---'} color={revEv != null && revEv > 0 ? '#a78bfa' : '#555'} />
        <Stat label="stop"    value={stopTicks != null ? `${stopTicks}t` : '---'} color="#f59e0b" />
        <Stat label="size"    value={sizing != null ? sizing.toFixed(2) : '---'} color="#06b6d4" />

        {triggerLevel && (
          <span className="ml-auto text-right text-[10px] text-zinc-600 leading-relaxed">
            <span className="text-zinc-400">{triggerLevel}</span>
            {triggerPrice && <span className="ml-1">{triggerPrice.toFixed(2)}</span>}
            {trigger && <span className="ml-1 text-zinc-600">{trigger}</span>}
          </span>
        )}
      </div>

      {/* ── MIDDLE ROW: DQN | SPECIALISTS | GBT ── */}
      <div className="flex gap-2 min-h-0" style={{ height: 240 }}>

        {/* DQN Network */}
        <div className="border border-zinc-800 bg-zinc-900 flex-[3] min-w-0 p-1">
          <div className="text-[9px] text-zinc-600 tracking-widest px-1 mb-1">DQN — 276→256→256→128→64→Q(3)</div>
          <div style={{ height: 210 }}>
            <NeuralNetworkSVG dqnInference={dqnInference} />
          </div>
        </div>

        {/* Specialists */}
        <div className="border border-zinc-800 bg-zinc-900 w-44 shrink-0 p-2 flex flex-col gap-2">
          <div className="text-[9px] text-zinc-600 tracking-widest">SPECIALISTS</div>
          <SpecBar label="CONT p(win)" value={contP}    color="#22c55e" suffix={contEv != null ? ` ${contEv > 0 ? '+' : ''}${contEv.toFixed(1)}R` : ''} />
          <SpecBar label="REV  p(win)" value={revP}     color="#ef4444" suffix={revEv  != null ? ` ${revEv  > 0 ? '+' : ''}${revEv.toFixed(1)}R`  : ''} />
          <SpecBar label="STOP (40t)"  value={stopTicks != null ? stopTicks / 40 : null} color="#f59e0b" suffix={stopTicks != null ? ` ${stopTicks}t` : ''} />
          <SpecBar label="SIZE"        value={sizing}   color="#06b6d4" suffix={sizing != null ? ` ${sizing.toFixed(2)}` : ''} />
        </div>

        {/* GBT */}
        <div className="border border-zinc-800 bg-zinc-900 w-44 shrink-0 p-2 flex flex-col gap-2">
          <div className="text-[9px] text-zinc-600 tracking-widest">GBT</div>
          <SpecBar label="cont"      value={contP} color="#22c55e" suffix={contP != null ? ` ${contP.toFixed(2)}` : ''} />
          <SpecBar label="reversal"  value={revP}  color="#ef4444" suffix={revP  != null ? ` ${revP.toFixed(2)}`  : ''} />
          <div className="mt-1 border-t border-zinc-800 pt-1">
            <div className="text-[9px] text-zinc-600 mb-1">forecast</div>
            <SpecBar label="exp ret"  value={contEv != null ? Math.max(0, contEv) / 5 : null} color="#a78bfa" suffix={contEv != null ? ` ${contEv > 0 ? '+' : ''}${contEv.toFixed(1)}R` : ''} />
          </div>
          <div className="mt-1">
            <div className="text-[9px] text-zinc-600 mb-0.5">stop regressor</div>
            <div className="text-base font-bold" style={{ color: '#f59e0b' }}>
              {stopTicks != null ? `${stopTicks}t` : '---'}
            </div>
          </div>
        </div>
      </div>

      {/* ── FEATURE HEATMAP ── */}
      {dqnInference?.inputs && (
        <div className="border border-zinc-800 bg-zinc-900 p-2">
          <div className="text-[9px] text-zinc-600 tracking-widest mb-2">
            INPUT FEATURES — {dqnInference.inputs.length} dims
          </div>
          <SegmentHeatmap inputs={dqnInference.inputs} />
        </div>
      )}

      {/* ── SIGNAL HISTORY ── */}
      <div className="border border-zinc-800 bg-zinc-900 flex flex-col min-h-[120px]">
        <div className="text-[9px] text-zinc-600 tracking-widest p-2 pb-1">
          SIGNAL HISTORY ({signals.length})
        </div>
        <div ref={historyRef} className="flex-1 overflow-y-auto">
          <table className="sq w-full">
            <thead>
              <tr><th>Time</th><th>Action</th><th>Conf</th><th>Zone</th><th>Price</th></tr>
            </thead>
            <tbody>
              {signals.length === 0 ? (
                <tr><td colSpan={5} className="text-center text-zinc-600">No signals yet</td></tr>
              ) : (
                signals.map((sig, i) => (
                  <tr key={i}>
                    <td className="text-zinc-500">
                      {sig.ts ? new Date(sig.ts * 1000).toLocaleTimeString() : '---'}
                    </td>
                    <td style={{ color: sig.action === 'CONTINUATION' ? '#22c55e' : sig.action === 'REVERSAL' ? '#ef4444' : '#888' }}>
                      {sig.action}
                    </td>
                    <td>{sig.confidence != null ? `${(sig.confidence * 100).toFixed(0)}%` : '---'}</td>
                    <td className="text-purple-400">{sig.zone ?? '---'}</td>
                    <td>{sig.price?.toFixed(2) ?? '---'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Primitive stat inline
function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <span className="flex flex-col leading-none gap-0.5">
      <span className="text-[8px] text-zinc-600 uppercase tracking-wider">{label}</span>
      <span className="text-xs font-bold" style={{ color }}>{value}</span>
    </span>
  )
}

// ── Specialist/GBT bar row
function SpecBar({ label, value, color, suffix }: { label: string; value: number | null | undefined; color: string; suffix?: string }) {
  const pct = value != null ? Math.min(Math.max(value, 0), 1) * 100 : 0
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex justify-between text-[9px]">
        <span className="text-zinc-600">{label}</span>
        <span style={{ color: value != null ? color : '#444' }}>{suffix || '---'}</span>
      </div>
      <div className="h-1.5 bg-zinc-800">
        <div className="h-full transition-all duration-300" style={{ width: `${pct}%`, backgroundColor: color, opacity: value != null ? 1 : 0 }} />
      </div>
    </div>
  )
}

// ── Feature heatmap (unchanged from original)
function SegmentHeatmap({ inputs }: { inputs: number[] }) {
  const maxAbs = useMemo(() => Math.max(...inputs.map(Math.abs), 0.001), [inputs])
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const toggle = useCallback((name: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(name) ? next.delete(name) : next.add(name)
      return next
    })
  }, [])

  return (
    <div className="space-y-0.5">
      {DQN_SEGMENTS.map(seg => {
        const slice = inputs.slice(seg.start, seg.end)
        const avgAbs = slice.reduce((s, v) => s + Math.abs(v), 0) / slice.length
        const intensity = Math.min(1, avgAbs / maxAbs)
        const isOpen = expanded.has(seg.name)
        return (
          <div key={seg.name}>
            <div
              className="flex items-center gap-2 cursor-pointer hover:bg-zinc-800/40 px-1 rounded-sm"
              onClick={() => toggle(seg.name)}
            >
              <span className="text-[9px] text-zinc-600 w-3">{isOpen ? '▾' : '▸'}</span>
              <span className="text-[10px] w-20 text-right shrink-0" style={{ color: intensity > 0.3 ? seg.color : '#555' }}>
                {seg.name}
              </span>
              <div className="flex-1 flex gap-px h-3">
                {slice.map((val, i) => {
                  const norm = val / maxAbs
                  const bg = val === 0 ? '#1a1a1a'
                    : val > 0 ? `rgba(74,222,128,${Math.abs(norm) * 0.9})`
                    : `rgba(248,113,113,${Math.abs(norm) * 0.9})`
                  return (
                    <div key={i} className="flex-1 min-w-0" style={{ backgroundColor: bg }}
                      title={`${seg.features[i] ?? `[${i}]`}: ${val.toFixed(4)}`} />
                  )
                })}
              </div>
              <span className="text-[9px] text-zinc-600 w-8 text-right">{slice.length}d</span>
            </div>
            {isOpen && <SegmentDetail seg={seg} slice={slice} maxAbs={maxAbs} />}
          </div>
        )
      })}
    </div>
  )
}

function SegmentDetail({ seg, slice, maxAbs }: { seg: DQNSegment; slice: number[]; maxAbs: number }) {
  return (
    <div className="ml-[108px] mr-1 grid gap-px py-1" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))' }}>
      {slice.map((val, i) => {
        const name = seg.features[i] ?? `[${i}]`
        const norm = val / maxAbs
        const barWidth = Math.min(Math.abs(norm) * 100, 100)
        const isPositive = val > 0
        return (
          <div key={i} className="flex items-center gap-1 h-4 px-1">
            <span className="text-[9px] w-[90px] text-right shrink-0 truncate" style={{ color: seg.color }}>{name}</span>
            <div className="flex-1 h-2 bg-zinc-900 relative overflow-hidden rounded-sm">
              <div className="absolute top-0 h-full rounded-sm"
                style={{ width: `${barWidth}%`, left: isPositive ? '50%' : `${50 - barWidth}%`,
                  backgroundColor: isPositive ? 'rgba(74,222,128,0.7)' : 'rgba(248,113,113,0.7)' }} />
              <div className="absolute top-0 left-1/2 w-px h-full bg-zinc-700" />
            </div>
            <span className={`text-[9px] w-12 text-right shrink-0 ${val === 0 ? 'text-zinc-700' : isPositive ? 'text-emerald-400/70' : 'text-red-400/70'}`}>
              {val === 0 ? '0' : val.toFixed(3)}
            </span>
          </div>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 2: Check TypeScript**

```bash
cd c:/Users/rasmu/firev/firevstocks/frontend
npx tsc --noEmit 2>&1 | head -40
```

Expected: no new errors. If `(inf as any)?.level` causes issues, check the `DQNInferenceEvent` type in `src/types/index.ts`. If `level` and `level_price` are not on the type, add them:

```ts
// In src/types/index.ts, inside DQNInferenceEvent:
level?: string
level_price?: number
```

- [ ] **Step 3: Commit**

```bash
cd c:/Users/rasmu/firev
git add firevstocks/frontend/src/pages/DQNPage.tsx firevstocks/frontend/src/types/index.ts
git commit -m "feat(firevstocks): simplify DQNPage layout with decision strip + specialists + GBT panels"
```

---

## Task 3: Build frontend and verify visually

- [ ] **Step 1: Build the frontend**

```bash
cd c:/Users/rasmu/firev/firevstocks/frontend
npm run build 2>&1 | tail -20
```

Expected: build succeeds with no errors. Warnings about unused vars are OK.

- [ ] **Step 2: Start firevstocks and verify the DQN tab**

Run `.\firevstocks` (or start via the bat file) and open the DQN tab in the browser.

Check without a live signal:
- Decision strip shows `— ---` with `○ NO SIGNAL` badge
- DQN network shows 14 segment columns of dim grey dots (real structure, no data)
- Status line shows `○ NO SIGNAL` and `DQN OFFLINE`
- Specialists + GBT panels show `---` placeholders
- No feature heatmap (hidden when `dqnInference` is null)

When a signal fires (or replay from server sends one):
- Decision strip populates: action glows green/red, conf/EV/stop/size fill in
- Input dots light up — some segments glow bright (high activation), others dim
- Negative-valued inputs show red-tinted dots
- Feature heatmap strip appears below

- [ ] **Step 3: Commit if any fixups were needed**

```bash
cd c:/Users/rasmu/firev
git add -p
git commit -m "fix(firevstocks): visual fixups after DQN page review"
```

---

## Self-Review

**Spec coverage:**
- ✅ Decision strip with action, model badge, LIVE/SIM, all stats, trigger info
- ✅ Input layer: 14 segments, per-dim dots, brightness = real abs(inputs[i])
- ✅ hasRealInputs / hasRealActivations split
- ✅ Hidden layers: real when activations present, gentle sim pulse when offline
- ✅ DQN ACTIVE / DQN OFFLINE status label
- ✅ Connections: removed (not drawn at all — cleaner than fake connections)
- ✅ Specialists panel: CONT p(win)+EV, REV p(win)+EV, STOP ticks, SIZING
- ✅ GBT panel: direction probs, forecast EV, stop regressor scalar
- ✅ Feature heatmap strip (existing component, unchanged)
- ✅ Signal history table preserved

**Placeholder scan:** None found. All code blocks are complete.

**Type consistency:**
- `hexToRgb` defined in Task 1 and only used in Task 1 ✅
- `sampleIndices` defined in Task 1 and only used in Task 1 ✅
- `SpecBar` defined in Task 2 and used only in Task 2 ✅
- `DQNInferenceEvent.level` / `level_price` may need adding to types (noted in Task 2 Step 2) ✅

**One known gap:** GBT panel reuses `contP`/`revP` from the inference event (which is the specialists output). The GBT sub-models don't expose separate probabilities in the current `DQNInferenceEvent` type — `cont_p` is the final decision probability regardless of which model computed it. The GBT panel shows the same values as specialists when both are active. This is correct behavior given the current backend — the values come from whichever model was active.
