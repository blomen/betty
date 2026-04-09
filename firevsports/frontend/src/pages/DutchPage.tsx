import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../hooks/useApi'

export function DutchPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['dutch'],
    queryFn: () => apiFetch<any>('/api/dutch/opportunities'),
    refetchInterval: 30_000,
  })

  const opps = data?.opportunities ?? data ?? []

  if (isLoading) return <div className="p-4 text-zinc-500">Loading...</div>
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
              <td className="pl-3 py-1.5 text-zinc-300 truncate max-w-[250px]">{o.event || (o.home_team + ' v ' + o.away_team)}</td>
              <td className="text-right text-green-400">+{(o.profit_pct ?? o.edge_pct ?? 0).toFixed(1)}%</td>
              <td className="text-right text-zinc-300">{Math.round(o.total_stake ?? 0)} kr</td>
              <td className="text-right pr-3 text-zinc-500">{o.provider_count ?? 2}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
