import type { BattleScreenData } from '@/types/market';

interface Props {
  battle: BattleScreenData;
  onTrade: (direction: 'long' | 'short', entry: number, stop: number, targets: { name: string; price: number }[]) => void;
}

export function TradeActionBar({ battle, onTrade }: Props) {
  return (
    <div className="flex-shrink-0 border-t border-zinc-700 px-3 py-2 flex items-center justify-between gap-4 text-xs font-mono bg-panel">
      <div className="flex gap-4 text-zinc-400">
        <span>ENTRY: <span className="text-white">{battle.suggested_entry.toLocaleString()}</span></span>
        <span>STOP: <span className="text-red-400">{battle.suggested_stop.toLocaleString()}</span></span>
        {battle.targets.map((t, i) => (
          <span key={i}>T{i + 1}: <span className="text-emerald-400">{t.price.toLocaleString()}</span> <span className="text-zinc-500">({t.name})</span></span>
        ))}
      </div>
      <div className="flex gap-2">
        <button
          onClick={() => onTrade('long', battle.suggested_entry, battle.suggested_stop, battle.targets)}
          className="px-3 py-1 bg-emerald-800 hover:bg-emerald-700 text-emerald-200 rounded text-xs font-bold"
        >
          TRADE LONG
        </button>
        <button
          onClick={() => onTrade('short', battle.suggested_entry, battle.suggested_stop, battle.targets)}
          className="px-3 py-1 bg-red-800 hover:bg-red-700 text-red-200 rounded text-xs font-bold"
        >
          TRADE SHORT
        </button>
      </div>
    </div>
  );
}
