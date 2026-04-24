import { useRef, useEffect, useState, useMemo, useCallback, useReducer } from 'react'
import { api } from '@/hooks/useStocksApi'
import type { Signal, Zone, DQNInferenceEvent, LevelsReplayResponse } from '@/types/stocks'
import { NeuralNetworkSVG } from './NeuralNetworkSVG'
import { DQN_SEGMENTS } from './dqnConfig'
import type { DQNSegment } from './dqnConfig'

interface Props {
  signals: Signal[]
  zones: Zone[]
  lastPrice: number | null
  dqnInference: DQNInferenceEvent | null
  dqnInferenceAt: number | null
}

export function DQNPage({ signals, zones: liveZones, lastPrice, dqnInference, dqnInferenceAt }: Props) {
  const historyRef = useRef<HTMLDivElement>(null)
  const [replayData, setReplayData] = useState<LevelsReplayResponse | null>(null)
  const [replayLoading, setReplayLoading] = useState(false)

  // Staleness: tick every second to recompute age
  const [, tick] = useReducer(n => n + 1, 0)
  useEffect(() => {
    if (!dqnInferenceAt) return
    const id = setInterval(() => tick(), 1000)
    return () => clearInterval(id)
  }, [dqnInferenceAt])

  const ageSeconds = dqnInferenceAt ? (Date.now() - dqnInferenceAt) / 1000 : null
  // Start fading at 30s, fully stale at 90s
  const staleFactor = ageSeconds == null ? 1 : Math.min(1, Math.max(0, (ageSeconds - 30) / 60))

  function formatAge(s: number): string {
    if (s < 60) return `${Math.round(s)}s ago`
    return `${Math.floor(s / 60)}m ago`
  }

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
  const action     = inf?.action     ?? latest?.action     ?? '---'
  const modelType  = inf?.model_type ?? latest?.model_type ?? '---'
  const confidence = inf?.confidence ?? latest?.confidence
  const contP      = inf?.cont_p     ?? latest?.cont_p
  const revP       = inf?.rev_p      ?? latest?.rev_p
  const contEv     = inf?.cont_ev
  const revEv      = inf?.rev_ev
  const stopTicks  = inf?.stop_ticks ?? latest?.stop_ticks
  const sizing     = inf?.sizing_signal
  const triggerLevel = inf?.level ?? null
  const triggerPrice = inf?.level_price ?? null
  const trigger    = inf?.trigger ?? null

  const actionColor = action === 'CONTINUATION' ? '#22c55e'
    : action === 'REVERSAL' ? '#ef4444'
    : '#52525b'

  const actionArrow = action === 'CONTINUATION' ? '↑'
    : action === 'REVERSAL' ? '↓'
    : '—'

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-2 p-2 overflow-y-auto font-mono">

      {/* ── DECISION STRIP ── */}
      <div className="border border-zinc-800 bg-zinc-900 px-3 py-2 flex items-baseline gap-5 flex-wrap text-xs">
        <span className="text-base font-bold tracking-widest" style={{ color: actionColor }}>
          {actionArrow} {action}
        </span>

        <span className="text-zinc-600 border border-zinc-700 px-1.5 py-0.5 text-[10px] tracking-wider">
          {modelType}
        </span>

        {hasSignal
          ? <span className="text-emerald-500 text-[10px] tracking-wider">● LIVE</span>
          : <span className="text-zinc-600 text-[10px] tracking-wider">○ NO SIGNAL</span>
        }
        {ageSeconds != null && (
          <span className="text-[10px]" style={{ color: staleFactor > 0.5 ? '#52525b' : '#78716c' }}>
            {formatAge(ageSeconds)}
          </span>
        )}

        <Stat label="conf"    value={confidence != null ? `${(confidence * 100).toFixed(0)}%` : '---'} color="#f59e0b" />
        <Stat label="P(cont)" value={contP != null ? contP.toFixed(2) : '---'} color="#22c55e" />
        <Stat label="P(rev)"  value={revP  != null ? revP.toFixed(2)  : '---'} color="#ef4444" />
        <Stat label="EV cont" value={contEv != null ? `${contEv > 0 ? '+' : ''}${contEv.toFixed(1)}R` : '---'} color="#a78bfa" />
        <Stat label="EV rev"  value={revEv  != null ? `${revEv  > 0 ? '+' : ''}${revEv.toFixed(1)}R`  : '---'} color={revEv != null && revEv > 0 ? '#a78bfa' : '#555'} />
        <Stat label="stop"    value={stopTicks != null ? `${stopTicks}t` : '---'} color="#f59e0b" />
        <Stat label="size"    value={sizing != null ? sizing.toFixed(2) : '---'} color="#06b6d4" />

        {(triggerLevel || trigger) && (
          <span className="ml-auto text-right text-[10px] text-zinc-600 leading-relaxed">
            {triggerLevel && <span className="text-zinc-400">{triggerLevel} </span>}
            {triggerPrice != null && <span>{triggerPrice.toFixed(2)} </span>}
            {trigger && <span>{trigger}</span>}
          </span>
        )}
      </div>

      {/* ── MIDDLE ROW: DQN | SPECIALISTS | GBT ── */}
      <div className="flex gap-2 min-h-0" style={{ height: 240 }}>

        {/* DQN Network */}
        <div className="border border-zinc-800 bg-zinc-900 flex-[3] min-w-0 p-1">
          <div className="text-[9px] text-zinc-600 tracking-widest px-1 mb-0.5">
            DQN — 276→256→256→128→64→Q(3)
          </div>
          <div style={{ height: 218 }}>
            <NeuralNetworkSVG dqnInference={dqnInference} staleFactor={staleFactor} />
          </div>
        </div>

        {/* Specialists */}
        <div className="border border-zinc-800 bg-zinc-900 w-44 shrink-0 p-2 flex flex-col gap-2">
          <div className="text-[9px] text-zinc-600 tracking-widest">SPECIALISTS</div>
          <SpecBar
            label="CONT p(win)"
            value={contP}
            color="#22c55e"
            suffix={contEv != null ? `${contEv > 0 ? '+' : ''}${contEv.toFixed(1)}R` : contP != null ? contP.toFixed(2) : undefined}
          />
          <SpecBar
            label="REV  p(win)"
            value={revP}
            color="#ef4444"
            suffix={revEv != null ? `${revEv > 0 ? '+' : ''}${revEv.toFixed(1)}R` : revP != null ? revP.toFixed(2) : undefined}
          />
          <SpecBar
            label="STOP (40t max)"
            value={stopTicks != null ? stopTicks / 40 : null}
            color="#f59e0b"
            suffix={stopTicks != null ? `${stopTicks}t` : undefined}
          />
          <SpecBar
            label="SIZE"
            value={sizing}
            color="#06b6d4"
            suffix={sizing != null ? sizing.toFixed(2) : undefined}
          />
        </div>

        {/* GBT */}
        <div className="border border-zinc-800 bg-zinc-900 w-44 shrink-0 p-2 flex flex-col gap-2">
          <div className="text-[9px] text-zinc-600 tracking-widest">GBT</div>
          <SpecBar label="cont"     value={contP} color="#22c55e" suffix={contP != null ? contP.toFixed(2) : undefined} />
          <SpecBar label="reversal" value={revP}  color="#ef4444" suffix={revP  != null ? revP.toFixed(2)  : undefined} />
          <div className="border-t border-zinc-800 pt-2 flex flex-col gap-2">
            <div className="text-[9px] text-zinc-600">forecast</div>
            <SpecBar
              label="exp return"
              value={contEv != null ? Math.max(0, contEv) / 5 : null}
              color="#a78bfa"
              suffix={contEv != null ? `${contEv > 0 ? '+' : ''}${contEv.toFixed(1)}R` : undefined}
            />
          </div>
          <div className="mt-auto">
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
                    <td style={{ color: (sig.action === 'CONTINUATION' || sig.action === 'enter_long') ? '#22c55e' : (sig.action === 'REVERSAL' || sig.action === 'enter_short') ? '#ef4444' : '#888' }}>
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

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <span className="flex flex-col leading-none gap-0.5">
      <span className="text-[8px] text-zinc-600 uppercase tracking-wider">{label}</span>
      <span className="text-xs font-bold" style={{ color }}>{value}</span>
    </span>
  )
}

function SpecBar({ label, value, color, suffix }: {
  label: string
  value: number | null | undefined
  color: string
  suffix?: string
}) {
  const pct = value != null ? Math.min(Math.max(value, 0), 1) * 100 : 0
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex justify-between text-[9px]">
        <span className="text-zinc-600">{label}</span>
        <span style={{ color: value != null ? color : '#444' }}>{suffix ?? '---'}</span>
      </div>
      <div className="h-1.5 bg-zinc-800">
        <div
          className="h-full transition-all duration-300"
          style={{ width: `${pct}%`, backgroundColor: color, opacity: value != null ? 1 : 0 }}
        />
      </div>
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
            <div
              className="flex items-center gap-2 cursor-pointer hover:bg-zinc-800/40 px-1 rounded-sm"
              onClick={() => toggle(seg.name)}
            >
              <span className="text-[9px] text-zinc-600 w-3">{isOpen ? '▾' : '▸'}</span>
              <span
                className="text-[10px] w-20 text-right shrink-0"
                style={{ color: intensity > 0.3 ? seg.color : '#555' }}
              >
                {seg.name}
              </span>
              <div className="flex-1 flex gap-px h-3">
                {slice.map((val, i) => {
                  const norm = val / maxAbs
                  const bg = val === 0 ? '#1a1a1a'
                    : val > 0 ? `rgba(74,222,128,${Math.abs(norm) * 0.9})`
                    : `rgba(248,113,113,${Math.abs(norm) * 0.9})`
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
            <span className="text-[9px] w-[90px] text-right shrink-0 truncate" style={{ color: seg.color }}>
              {name}
            </span>
            <div className="flex-1 h-2 bg-zinc-900 relative overflow-hidden rounded-sm">
              <div
                className="absolute top-0 h-full rounded-sm"
                style={{
                  width: `${barWidth}%`,
                  left: isPositive ? '50%' : `${50 - barWidth}%`,
                  backgroundColor: isPositive ? 'rgba(74,222,128,0.7)' : 'rgba(248,113,113,0.7)',
                }}
              />
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
