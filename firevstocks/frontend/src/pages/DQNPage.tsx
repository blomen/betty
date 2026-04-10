import { useRef, useEffect } from 'react'
import type { Signal, Zone } from '@/types'

interface Props {
  signals: Signal[]
  zones: Zone[]
  lastPrice: number | null
}

export function DQNPage({ signals, zones, lastPrice }: Props) {
  const latest = signals.length > 0 ? signals[signals.length - 1] : null
  const historyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    historyRef.current?.scrollTo({ top: historyRef.current.scrollHeight, behavior: 'smooth' })
  }, [signals.length])

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

      {/* Zone Status */}
      <div className="border border-zinc-800 bg-zinc-900 p-3">
        <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-2">
          Active Zones ({zones.length})
        </h3>
        {zones.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {zones.map((z, i) => {
              const dist = lastPrice ? Math.abs(lastPrice - z.price) : null
              return (
                <span key={i} className="text-xs font-mono px-2 py-1 border border-zinc-700 bg-zinc-950">
                  <span className="text-purple-400">{z.price.toFixed(2)}</span>
                  <span className="text-zinc-600 ml-1">×{z.members}</span>
                  {dist != null && <span className="text-zinc-600 ml-1">({dist.toFixed(2)})</span>}
                </span>
              )
            })}
          </div>
        ) : (
          <div className="text-xs font-mono text-zinc-600">No zones loaded</div>
        )}
      </div>

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
