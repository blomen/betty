import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch, api } from '../hooks/useApi'
import { useMirrorStream } from '../hooks/useMirrorStream'

export function PendingPage() {
  const queryClient = useQueryClient()
  const mirror = useMirrorStream()
  const [syncing, setSyncing] = useState(false)
  const [detectedSettlements, setDetectedSettlements] = useState<Record<string, any[]>>({})

  const { data, isLoading } = useQuery({
    queryKey: ['pending-bets'],
    queryFn: () => apiFetch<any>('/api/opportunities/play/pending-bets'),
    refetchInterval: 15_000,
  })

  useEffect(() => {
    if (!mirror.lastEvent) return
    const { type, data } = mirror.lastEvent
    if (type === 'settlements_detected') {
      setDetectedSettlements(prev => ({ ...prev, [data.provider_id]: data.settlements }))
    }
    if (type === 'settlements_confirmed') {
      setDetectedSettlements(prev => {
        const next = { ...prev }
        delete next[data.provider_id]
        return next
      })
      queryClient.invalidateQueries({ queryKey: ['pending-bets'] })
    }
    if (type === 'pending_stopped') setSyncing(false)
  }, [mirror.lastEvent])

  const handleSyncAll = async () => { setSyncing(true); await api.startPendingLoop() }
  const handleStopSync = () => { api.stopPendingLoop(); setSyncing(false) }
  const handleConfirm = (pid: string) => api.confirmSettlement(pid)

  if (isLoading) return <div className="p-4 text-zinc-500">Loading...</div>

  const providers = data?.providers ?? []
  const totalBets = providers.reduce((s: number, p: any) => s + (p.bet_count ?? 0), 0)

  if (totalBets === 0) return <div className="p-4 text-zinc-600">No pending bets.</div>

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="flex items-center gap-3 px-3 py-2 text-xs text-zinc-400 border-b border-zinc-800">
        <span>{totalBets} pending bets across {providers.length} providers</span>
        <div className="ml-auto flex items-center gap-2">
          {mirror.connected && <span className="w-1.5 h-1.5 rounded-full bg-green-500" />}
          {!syncing ? (
            <button onClick={handleSyncAll}
              className="px-2 py-0.5 text-xs bg-amber-600 hover:bg-amber-500 text-white rounded">
              Sync All
            </button>
          ) : (
            <button onClick={handleStopSync}
              className="px-2 py-0.5 text-xs bg-red-700 hover:bg-red-600 text-white rounded">
              Stop
            </button>
          )}
        </div>
      </div>
      {providers.map((p: any) => (
        <div key={p.provider_id}>
          <div className="flex items-center gap-3 px-3 py-2 bg-zinc-900/50 border-b border-zinc-800">
            <span className="text-xs font-medium text-zinc-300 uppercase">{p.provider_id}</span>
            <span className="text-xs text-zinc-500">{p.bet_count} bets</span>
            <span className="text-xs text-zinc-500">{(p.total_stake ?? 0).toFixed(0)} {p.currency ?? 'SEK'}</span>
          </div>
          {p.bets && p.bets.length > 0 && (
            <table className="w-full text-xs">
              <thead><tr className="text-zinc-600 text-[10px]">
                <th className="text-left pl-6 py-1">Event</th>
                <th className="text-left">Market</th>
                <th className="text-left">Outcome</th>
                <th className="text-right">Odds</th>
                <th className="text-right">Stake</th>
                <th className="text-right pr-3">Placed</th>
              </tr></thead>
              <tbody>
                {p.bets.map((b: any) => (
                  <tr key={b.id} className="border-b border-zinc-800/30">
                    <td className="pl-6 py-1 text-zinc-400 truncate max-w-[200px]">
                      {b.event_id?.split(':').slice(1,3).join(' v ') ?? b.event_id}
                    </td>
                    <td className="text-zinc-500">{b.market}</td>
                    <td className="text-zinc-400">{b.outcome}</td>
                    <td className="text-right text-zinc-300">{(b.odds ?? 0).toFixed(2)}</td>
                    <td className="text-right text-zinc-300">{(b.stake ?? 0).toFixed(0)}</td>
                    <td className="text-right pr-3 text-zinc-600">
                      {b.placed_at ? new Date(b.placed_at).toLocaleDateString('sv-SE') : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {detectedSettlements[p.provider_id] && (
            <div className="px-6 py-2 bg-amber-900/20 border-b border-amber-700/30">
              <div className="flex items-center gap-3">
                <span className="text-xs text-amber-400 font-medium">
                  {detectedSettlements[p.provider_id].length} settlements detected
                </span>
                <button onClick={() => handleConfirm(p.provider_id)}
                  className="px-2 py-0.5 text-xs bg-green-700 hover:bg-green-600 text-white rounded">
                  Confirm
                </button>
              </div>
              {detectedSettlements[p.provider_id].map((s: any, i: number) => (
                <div key={i} className="flex gap-3 text-xs mt-1">
                  <span className="text-zinc-400">Bet #{s.bet_id}</span>
                  <span className={s.result === 'won' ? 'text-green-400' : s.result === 'lost' ? 'text-red-400' : 'text-zinc-400'}>
                    {s.result}
                  </span>
                  <span className="text-zinc-300">{s.payout > 0 ? `+${s.payout.toFixed(0)} kr` : ''}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
