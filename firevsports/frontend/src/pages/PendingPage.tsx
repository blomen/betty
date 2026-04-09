import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../hooks/useApi'

export function PendingPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['pending-bets'],
    queryFn: () => apiFetch<any>('/api/opportunities/play/pending-bets'),
    refetchInterval: 15_000,
  })

  if (isLoading) return <div className="p-4 text-zinc-500">Loading...</div>

  const providers = data?.providers ?? []
  const totalBets = providers.reduce((s: number, p: any) => s + (p.bet_count ?? 0), 0)

  if (totalBets === 0) return <div className="p-4 text-zinc-600">No pending bets.</div>

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="px-3 py-2 text-xs text-zinc-400 border-b border-zinc-800">
        {totalBets} pending bets across {providers.length} providers
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
        </div>
      ))}
    </div>
  )
}
