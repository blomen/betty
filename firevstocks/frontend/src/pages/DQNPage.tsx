import { useRef, useEffect, useState } from 'react'
import { api } from '@/hooks/useApi'
import type { Signal, Zone, LevelsReplayResponse } from '@/types'

interface Props {
  signals: Signal[]
  zones: Zone[]
  lastPrice: number | null
}

export function DQNPage({ signals, zones: liveZones, lastPrice }: Props) {
  const latest = signals.length > 0 ? signals[signals.length - 1] : null
  const historyRef = useRef<HTMLDivElement>(null)
  const [replayData, setReplayData] = useState<LevelsReplayResponse | null>(null)
  const [replayLoading, setReplayLoading] = useState(false)

  useEffect(() => {
    historyRef.current?.scrollTo({ top: historyRef.current.scrollHeight, behavior: 'smooth' })
  }, [signals.length])

  // Load last session's levels/zones if no live data
  useEffect(() => {
    if (liveZones.length > 0) return // live data available, skip
    setReplayLoading(true)
    api.getLevelsReplay()
      .then(d => { if (!d.error) setReplayData(d) })
      .catch(() => {})
      .finally(() => setReplayLoading(false))
  }, [liveZones.length])

  // Use live zones if available, otherwise build from replay active_levels
  const zones: Zone[] = liveZones.length > 0
    ? liveZones
    : (replayData?.active_levels ?? []).map(l => ({
        price: l.price,
        members: 1,
        name: l.name,
      }))

  const sessionLevels = replayData?.session_levels
  const vpData = replayData?.volume_profile
  const vwapData = replayData?.vwap?.vwap != null
    ? {
        vwap: replayData.vwap.vwap,
        sd1_u: replayData.vwap.sd1_upper!,
        sd1_l: replayData.vwap.sd1_lower!,
        sd2_u: replayData.vwap.sd2_upper!,
        sd2_l: replayData.vwap.sd2_lower!,
      }
    : null

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-3 overflow-y-auto">
      {/* Signal Panel */}
      <div className="grid grid-cols-3 gap-2">
        <SignalCard
          label="Action"
          value={latest?.action ?? '—'}
          color={latest?.action?.includes('long') || latest?.action === 'CONT' ? '#4ade80'
            : latest?.action?.includes('short') || latest?.action === 'REV' ? '#ef4444'
            : '#a1a1aa'}
        />
        <SignalCard
          label="Confidence"
          value={latest ? `${(latest.confidence * 100).toFixed(1)}%` : '—'}
          color="#f59e0b"
          bar={latest?.confidence}
        />
        <SignalCard
          label="Specialist"
          value={latest?.specialist ?? '—'}
          color="#8b5cf6"
        />
        <SignalCard
          label="cont_p"
          value={latest?.cont_p != null ? latest.cont_p.toFixed(3) : '—'}
          color="#4ade80"
        />
        <SignalCard
          label="rev_p"
          value={latest?.rev_p != null ? latest.rev_p.toFixed(3) : '—'}
          color="#ef4444"
        />
        <SignalCard
          label="stop_ticks"
          value={latest?.stop_ticks != null ? String(latest.stop_ticks) : '—'}
          color="#f59e0b"
        />
      </div>

      {/* Feature Heatmap */}
      <div className="border border-zinc-800 bg-zinc-900 p-3">
        <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-2">Feature Activation (276-dim)</h3>
        {latest?.features ? (
          <FeatureHeatmap features={latest.features} />
        ) : (
          <div className="text-xs font-mono text-zinc-600 py-4 text-center">
            No feature data — waiting for signal with features array
          </div>
        )}
      </div>

      {/* Session Reference Levels */}
      {(sessionLevels || vpData || vwapData) && (
        <div className="border border-zinc-800 bg-zinc-900 p-3">
          <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-2">
            Session Levels {replayData?.date && <span className="text-zinc-600 normal-case">({replayData.date})</span>}
          </h3>
          <div className="grid grid-cols-4 gap-x-4 gap-y-1 text-xs font-mono">
            {vpData?.poc != null && <LevelRow label="dPOC" value={vpData.poc} color="#a855f7" />}
            {vpData?.vah != null && <LevelRow label="dVAH" value={vpData.vah} color="#a855f7" />}
            {vpData?.val != null && <LevelRow label="dVAL" value={vpData.val} color="#a855f7" />}
            {vwapData && (
              <>
                <LevelRow label="VWAP" value={vwapData.vwap} color="#eab308" />
                <LevelRow label="+1σ" value={vwapData.sd1_u} color="#eab308" />
                <LevelRow label="-1σ" value={vwapData.sd1_l} color="#eab308" />
              </>
            )}
            {sessionLevels && (
              <>
                {sessionLevels.pdh != null && <LevelRow label="PDH" value={sessionLevels.pdh} color="#fb923c" />}
                {sessionLevels.pdl != null && <LevelRow label="PDL" value={sessionLevels.pdl} color="#fb923c" />}
                {sessionLevels.ib_high != null && <LevelRow label="IBH" value={sessionLevels.ib_high} color="#f59e0b" />}
                {sessionLevels.ib_low != null && <LevelRow label="IBL" value={sessionLevels.ib_low} color="#f59e0b" />}
              </>
            )}
          </div>
        </div>
      )}

      {/* Zone Status */}
      <div className="border border-zinc-800 bg-zinc-900 p-3">
        <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-2">
          {liveZones.length > 0 ? 'Active' : 'Last Session'} Zones ({zones.length})
          {replayLoading && <span className="text-zinc-600 ml-2">loading...</span>}
        </h3>
        {zones.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {zones.map((z, i) => {
              const dist = lastPrice ? Math.abs(lastPrice - z.price) : null
              return (
                <span key={i} className="text-xs font-mono px-2 py-1 border border-zinc-700 bg-zinc-950" title={z.name}>
                  <span className="text-purple-400">{z.price.toFixed(2)}</span>
                  <span className="text-zinc-600 ml-1">×{z.members}</span>
                  {dist != null && <span className="text-zinc-600 ml-1">({dist.toFixed(2)})</span>}
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

      {/* Replay summary */}
      {replayData && !replayData.error && (
        <div className="border border-zinc-800 bg-zinc-900 p-3">
          <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-2">
            Last Session Replay
          </h3>
          <div className="grid grid-cols-3 gap-2 text-xs font-mono">
            <div><span className="text-zinc-500">Date: </span><span className="text-zinc-300">{replayData.date}</span></div>
            <div><span className="text-zinc-500">Ticks: </span><span className="text-zinc-300">{replayData.ticks_count?.toLocaleString()}</span></div>
            <div><span className="text-zinc-500">Episodes: </span><span className="text-zinc-300">{replayData.episodes_count}</span></div>
            <div><span className="text-zinc-500">Levels: </span><span className="text-zinc-300">{replayData.active_levels?.length}</span></div>
            <div><span className="text-zinc-500">FVGs: </span><span className="text-zinc-300">{replayData.fvgs?.length ?? 0}</span></div>
          </div>
        </div>
      )}

      {/* Signal History */}
      <div className="border border-zinc-800 bg-zinc-900 flex-1 min-h-[200px] flex flex-col">
        <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider p-3 pb-1">
          Signal History ({signals.length})
        </h3>
        <div ref={historyRef} className="flex-1 overflow-y-auto">
          <table className="sq w-full">
            <thead>
              <tr>
                <th>Time</th>
                <th>Action</th>
                <th>Confidence</th>
                <th>Zone</th>
                <th>Specialist</th>
                <th>Price</th>
              </tr>
            </thead>
            <tbody>
              {signals.length === 0 ? (
                <tr><td colSpan={6} className="text-center text-zinc-600">No signals yet</td></tr>
              ) : (
                signals.map((sig, i) => (
                  <tr key={i}>
                    <td className="text-zinc-500">
                      {sig.ts ? new Date(sig.ts * 1000).toLocaleTimeString() : '—'}
                    </td>
                    <td className={sig.action?.includes('long') || sig.action === 'CONT' ? 'text-emerald-400' : sig.action?.includes('short') || sig.action === 'REV' ? 'text-red-400' : ''}>
                      {sig.action}
                    </td>
                    <td>{(sig.confidence * 100).toFixed(1)}%</td>
                    <td className="text-purple-400">{sig.zone ?? '—'}</td>
                    <td className="text-zinc-400">{sig.specialist ?? '—'}</td>
                    <td>{sig.price?.toFixed(2) ?? '—'}</td>
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

function SignalCard({ label, value, color, bar }: { label: string; value: string; color: string; bar?: number }) {
  return (
    <div className="border border-zinc-800 bg-zinc-900 p-3">
      <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className="text-lg font-mono font-bold mt-1" style={{ color }}>{value}</div>
      {bar != null && (
        <div className="mt-1 h-1 bg-zinc-800">
          <div className="h-full" style={{ width: `${bar * 100}%`, backgroundColor: color }} />
        </div>
      )}
    </div>
  )
}

function LevelRow({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex justify-between">
      <span style={{ color }}>{label}</span>
      <span className="text-zinc-300">{value.toFixed(2)}</span>
    </div>
  )
}

function FeatureHeatmap({ features }: { features: number[] }) {
  const cols = 12
  const maxAbs = Math.max(...features.map(Math.abs), 0.001)

  return (
    <div className="grid gap-px" style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
      {features.map((val, i) => {
        const norm = val / maxAbs
        const bg = val === 0
          ? '#2a2a2a'
          : val > 0
            ? `rgba(74, 222, 128, ${Math.abs(norm) * 0.8})`
            : `rgba(248, 113, 113, ${Math.abs(norm) * 0.8})`
        return (
          <div
            key={i}
            className="aspect-square"
            style={{ backgroundColor: bg }}
            title={`dim ${i}: ${val.toFixed(4)}`}
          />
        )
      })}
    </div>
  )
}
