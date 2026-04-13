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
  const latest = signals.length > 0 ? signals[signals.length - 1] : null
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
    : (replayData?.active_levels ?? []).map(l => ({
        price: l.price,
        members: 1,
        name: l.name,
      }))

  // Use inference data for specialist panel, fall back to signal data
  const inf = dqnInference
  const contP = inf?.cont_p ?? latest?.cont_p
  const revP = inf?.rev_p ?? latest?.rev_p
  const action = inf?.action ?? latest?.action
  const confidence = inf?.confidence ?? latest?.confidence
  const stopTicks = inf?.stop_ticks ?? latest?.stop_ticks
  const modelType = inf?.model_type ?? latest?.model_type

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-2 overflow-y-auto">
      {/* ── Neural Network Visualization ── */}
      <div className="border border-zinc-800 bg-zinc-900/80 p-2" style={{ minHeight: 300 }}>
        <NeuralNetworkSVG dqnInference={dqnInference} />
      </div>

      {/* ── Specialist Ensemble Decision Panel ── */}
      <div className="grid grid-cols-6 gap-2">
        <DecisionCard
          label="Decision"
          value={action ?? '---'}
          color={action === 'CONTINUATION' ? '#10b981' : action === 'REVERSAL' ? '#ef4444' : '#52525b'}
          large
        />
        <DecisionCard
          label="Model"
          value={modelType ?? '---'}
          color="#8b5cf6"
        />
        <ProbBar label="P(cont)" value={contP} color="#10b981" />
        <ProbBar label="P(rev)" value={revP} color="#ef4444" />
        <DecisionCard
          label="Confidence"
          value={confidence != null ? `${(confidence * 100).toFixed(1)}%` : '---'}
          color="#f59e0b"
        />
        <DecisionCard
          label="Stop"
          value={stopTicks != null ? `${stopTicks} ticks` : '---'}
          color="#f59e0b"
        />
      </div>

      {/* ── Feature Segment Heatmap ── */}
      {dqnInference?.inputs && (
        <div className="border border-zinc-800 bg-zinc-900 p-3">
          <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-2">
            Feature Segments ({dqnInference.inputs.length}-dim)
          </h3>
          <SegmentHeatmap inputs={dqnInference.inputs} />
        </div>
      )}

      <div className="grid grid-cols-2 gap-2 flex-1 min-h-0">
        {/* ── Zone Status ── */}
        <div className="border border-zinc-800 bg-zinc-900 p-3">
          <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-2">
            {liveZones.length > 0 ? 'Active' : 'Last Session'} Zones ({zones.length})
            {replayLoading && <span className="text-zinc-600 ml-2">loading...</span>}
          </h3>
          {zones.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {zones.map((z, i) => {
                const dist = lastPrice ? Math.abs(lastPrice - z.price) : null
                return (
                  <span key={i} className="text-xs font-mono px-2 py-0.5 border border-zinc-700 bg-zinc-950" title={z.name}>
                    <span className="text-purple-400">{z.price.toFixed(2)}</span>
                    <span className="text-zinc-600 ml-1">x{z.members}</span>
                    {dist != null && <span className="text-zinc-600 ml-1">({dist.toFixed(1)})</span>}
                  </span>
                )
              })}
            </div>
          ) : (
            <div className="text-xs font-mono text-zinc-600">
              {replayLoading ? 'Loading zones...' : 'No zones available'}
            </div>
          )}
        </div>

        {/* ── Signal History ── */}
        <div className="border border-zinc-800 bg-zinc-900 flex flex-col min-h-[200px]">
          <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider p-3 pb-1">
            Signal History ({signals.length})
          </h3>
          <div ref={historyRef} className="flex-1 overflow-y-auto">
            <table className="sq w-full">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Action</th>
                  <th>Conf</th>
                  <th>Zone</th>
                  <th>Price</th>
                </tr>
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
                      <td className={sig.action?.includes('long') || sig.action === 'CONTINUATION' ? 'text-emerald-400' : sig.action?.includes('short') || sig.action === 'REVERSAL' ? 'text-red-400' : ''}>
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
    </div>
  )
}

function DecisionCard({ label, value, color, large }: { label: string; value: string; color: string; large?: boolean }) {
  return (
    <div className="border border-zinc-800 bg-zinc-900 p-2">
      <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className={`font-mono font-bold mt-0.5 ${large ? 'text-base' : 'text-sm'}`} style={{ color }}>
        {value}
      </div>
    </div>
  )
}

function ProbBar({ label, value, color }: { label: string; value?: number; color: string }) {
  const pct = value != null ? value * 100 : 0
  return (
    <div className="border border-zinc-800 bg-zinc-900 p-2">
      <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className="text-sm font-mono font-bold mt-0.5" style={{ color }}>
        {value != null ? `${pct.toFixed(1)}%` : '---'}
      </div>
      {value != null && (
        <div className="mt-1 h-1.5 bg-zinc-800 rounded-sm">
          <div className="h-full rounded-sm transition-all duration-300" style={{ width: `${pct}%`, backgroundColor: color }} />
        </div>
      )}
    </div>
  )
}

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
            {/* Collapsed row: segment name + heatmap bar */}
            <div
              className="flex items-center gap-2 cursor-pointer hover:bg-zinc-800/40 px-1 rounded-sm"
              onClick={() => toggle(seg.name)}
            >
              <span className="text-[9px] font-mono text-zinc-600 w-3">{isOpen ? '▾' : '▸'}</span>
              <span
                className="text-[10px] font-mono w-20 text-right shrink-0"
                style={{ color: intensity > 0.3 ? seg.color : '#555' }}
              >
                {seg.name}
              </span>
              <div className="flex-1 flex gap-px h-3">
                {slice.map((val, i) => {
                  const norm = val / maxAbs
                  const bg = val === 0
                    ? '#1a1a1a'
                    : val > 0
                      ? `rgba(74, 222, 128, ${Math.abs(norm) * 0.9})`
                      : `rgba(248, 113, 113, ${Math.abs(norm) * 0.9})`
                  return (
                    <div
                      key={i}
                      className="flex-1 min-w-0"
                      style={{ backgroundColor: bg }}
                      title={`${seg.features[i] ?? `[${i}]`}: ${val.toFixed(4)}`}
                    />
                  )
                })}
              </div>
              <span className="text-[9px] font-mono text-zinc-600 w-8 text-right">
                {slice.length}d
              </span>
            </div>
            {/* Expanded: individual named features */}
            {isOpen && (
              <SegmentDetail seg={seg} slice={slice} maxAbs={maxAbs} />
            )}
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
            <span className="text-[9px] font-mono w-[90px] text-right shrink-0 truncate" style={{ color: seg.color }}>
              {name}
            </span>
            <div className="flex-1 h-2 bg-zinc-900 relative overflow-hidden rounded-sm">
              <div
                className="absolute top-0 h-full rounded-sm"
                style={{
                  width: `${barWidth}%`,
                  left: isPositive ? '50%' : `${50 - barWidth}%`,
                  backgroundColor: isPositive ? 'rgba(74, 222, 128, 0.7)' : 'rgba(248, 113, 113, 0.7)',
                }}
              />
              <div className="absolute top-0 left-1/2 w-px h-full bg-zinc-700" />
            </div>
            <span className={`text-[9px] font-mono w-12 text-right shrink-0 ${val === 0 ? 'text-zinc-700' : isPositive ? 'text-emerald-400/70' : 'text-red-400/70'}`}>
              {val === 0 ? '0' : val.toFixed(3)}
            </span>
          </div>
        )
      })}
    </div>
  )
}
