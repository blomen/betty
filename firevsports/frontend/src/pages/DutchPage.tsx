import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../hooks/useApi'

export function DutchPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['dutch'],
    queryFn: () => apiFetch<any>('/api/opportunities/dutch-workflow?providers=all'),
    refetchInterval: 30_000,
    retry: false,
  })

  if (isLoading) return <div className="p-4 text-zinc-500">Loading dutch opportunities...</div>
  if (error) return <div className="p-4 text-zinc-600">Dutch: {(error as Error).message}</div>

  // Response could be array or object with opportunities key
  const opps = Array.isArray(data) ? data : (data?.opportunities ?? data?.events ?? [])

  if (!Array.isArray(opps) || opps.length === 0) return <div className="p-4 text-zinc-600">No dutch opportunities.</div>

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="px-3 py-2 text-xs text-zinc-400 border-b border-zinc-800">{opps.length} dutch opportunities</div>
      <table className="w-full text-xs">
        <thead><tr className="text-zinc-600 text-[10px]">
          <th className="text-left pl-3 py-1">Event</th>
          <th className="text-right">Profit %</th>
          <th className="text-right">Stake</th>
          <th className="text-right pr-3">Providers</th>
        </tr></thead>
        <tbody>
          {opps.slice(0, 50).map((o: any, i: number) => (
            <tr key={i} className="border-b border-zinc-800/50 hover:bg-zinc-800/30">
              <td className="pl-3 py-1.5 text-zinc-300 truncate max-w-[250px]">
                {o.event || o.display_home && o.display_away ? `${o.display_home} v ${o.display_away}` : o.event_id}
              </td>
              <td className="text-right text-green-400">+{(o.profit_pct ?? o.edge_pct ?? o.margin ?? 0).toFixed(1)}%</td>
              <td className="text-right text-zinc-300">{Math.round(o.total_stake ?? o.stake ?? 0)} kr</td>
              <td className="text-right pr-3 text-zinc-500">{o.provider_count ?? o.providers?.length ?? 2}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
