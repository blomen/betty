import type { PositionRow } from '@/types/market';

interface Props {
  positions: PositionRow[];
  onScale: (tradeId: number, pct: number) => void;
  onClose: (tradeId: number) => void;
  onHold: (tradeId: number) => void;
  onUpdateStop: (tradeId: number, newStop: number) => void;
}

export function PositionManager({ positions, onScale, onClose, onHold }: Props) {
  if (positions.length === 0) return null;

  return (
    <div className="font-mono text-xs">
      <div className="text-zinc-500 text-[10px] uppercase tracking-wider mb-1">Open Positions</div>
      <table className="w-full">
        <thead>
          <tr className="text-zinc-500 text-[10px] uppercase">
            <th className="text-right pr-2 py-1">Entry</th>
            <th className="text-center py-1">Dir</th>
            <th className="text-right pr-2 py-1">Size</th>
            <th className="text-right pr-2 py-1">Current</th>
            <th className="text-right pr-2 py-1">P&L</th>
            <th className="text-right pr-2 py-1">Stop</th>
            <th className="text-left py-1">Next Level</th>
            <th className="text-center py-1">Status</th>
            <th className="text-center py-1">Actions</th>
          </tr>
        </thead>
        <tbody>
          {positions.map(pos => {
            const pnlColor = pos.pnl_points >= 0 ? 'text-emerald-400' : 'text-red-400';
            const dirBadge = pos.direction === 'long'
              ? 'bg-emerald-900/50 text-emerald-400'
              : 'bg-red-900/50 text-red-400';
            const statusBadge = pos.status === 'at_target'
              ? 'bg-cyan-900/50 text-cyan-300'
              : pos.status === 'running'
                ? 'bg-zinc-800 text-zinc-400'
                : pos.status === 'stopped'
                  ? 'bg-red-900/50 text-red-400'
                  : 'bg-zinc-800 text-zinc-500';

            return (
              <tr key={pos.trade_id} className="hover:bg-zinc-800/50">
                <td className="text-right pr-2 py-0.5 text-zinc-300 tabular-nums">
                  {pos.entry_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                </td>
                <td className="text-center py-0.5">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${dirBadge}`}>
                    {pos.direction.toUpperCase()}
                  </span>
                </td>
                <td className="text-right pr-2 py-0.5 tabular-nums">
                  {pos.current_size}/{pos.original_size}
                </td>
                <td className="text-right pr-2 py-0.5 text-zinc-300 tabular-nums">
                  {pos.current_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                </td>
                <td className={`text-right pr-2 py-0.5 tabular-nums ${pnlColor}`}>
                  {pos.pnl_points >= 0 ? '+' : ''}{pos.pnl_points.toFixed(2)} ({pos.pnl_dollars >= 0 ? '+' : ''}${pos.pnl_dollars.toFixed(0)})
                </td>
                <td className="text-right pr-2 py-0.5 text-red-400 tabular-nums">
                  {pos.stop_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                </td>
                <td className="text-left py-0.5">
                  {pos.next_target ? (
                    <span>
                      <span className="text-zinc-400">{pos.next_target.name}</span>
                      <span className="text-zinc-600 ml-1">{pos.next_target.price.toLocaleString()}</span>
                    </span>
                  ) : '--'}
                </td>
                <td className="text-center py-0.5">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${statusBadge}`}>
                    {pos.status.toUpperCase().replace('_', ' ')}
                  </span>
                </td>
                <td className="text-center py-0.5">
                  {pos.status === 'at_target' && (
                    <div className="flex gap-1 justify-center">
                      <button
                        onClick={() => onScale(pos.trade_id, 50)}
                        className="px-1.5 py-0.5 bg-amber-900/50 hover:bg-amber-800/50 text-amber-300 rounded text-[10px]"
                      >
                        SCALE 50%
                      </button>
                      <button
                        onClick={() => onClose(pos.trade_id)}
                        className="px-1.5 py-0.5 bg-red-900/50 hover:bg-red-800/50 text-red-300 rounded text-[10px]"
                      >
                        CLOSE
                      </button>
                      <button
                        onClick={() => onHold(pos.trade_id)}
                        className="px-1.5 py-0.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded text-[10px]"
                      >
                        HOLD
                      </button>
                    </div>
                  )}
                  {pos.status === 'running' && (
                    <button
                      onClick={() => onClose(pos.trade_id)}
                      className="px-1.5 py-0.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-400 rounded text-[10px]"
                    >
                      CLOSE
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
