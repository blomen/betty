import type { MonitoredLevel, PricePosition } from '@/types/market';

const STATUS_BADGES: Record<string, { text: string; cls: string }> = {
  watching: { text: 'WATCH', cls: 'bg-zinc-800 text-zinc-500' },
  approaching: { text: 'NEAR', cls: 'bg-amber-900/50 text-amber-400' },
  at_level: { text: 'AT LVL', cls: 'bg-cyan-900/50 text-cyan-300' },
  triggered: { text: 'TRIG', cls: 'bg-emerald-900/50 text-emerald-400' },
  rejected: { text: 'REJ', cls: 'bg-zinc-800 text-zinc-600' },
};

const CATEGORY_COLORS: Record<string, string> = {
  session: 'text-blue-400',
  band: 'text-purple-400',
  prior: 'text-amber-400',
  structure: 'text-cyan-400',
  overnight: 'text-zinc-400',
};

interface Props {
  levels: MonitoredLevel[];
  currentPrice: number | null;
  connected: boolean;
  pricePos?: PricePosition;
  onLevelClick: (levelName: string) => void;
}

export function LevelTable({ levels, currentPrice, connected, pricePos, onLevelClick }: Props) {
  const cp = currentPrice ?? 0;

  // Dedup levels by price (keep first occurrence, prefer non-band over band)
  const seen = new Map<number, MonitoredLevel>();
  for (const l of levels) {
    const key = Math.round(l.price * 4); // snap to tick
    if (!seen.has(key) || (seen.get(key)!.category === 'band' && l.category !== 'band')) {
      seen.set(key, l);
    }
  }
  const dedupedLevels = Array.from(seen.values());

  const aboveLevels = dedupedLevels.filter(l => l.price > cp).sort((a, b) => a.price - b.price);
  const belowLevels = dedupedLevels.filter(l => l.price <= cp).sort((a, b) => b.price - a.price);

  const TICK = 0.25;

  const renderRow = (level: MonitoredLevel) => {
    const badge = STATUS_BADGES[level.status] || STATUS_BADGES.watching;
    const catColor = CATEGORY_COLORS[level.category] || 'text-zinc-400';
    const displayDist = cp ? (level.price - cp) / TICK : 0;
    const rowCls = level.status === 'at_level' ? 'border-l-2 border-cyan-400' :
                   level.status === 'approaching' ? 'animate-pulse' : '';

    return (
      <tr key={level.name} className={`${rowCls} cursor-pointer`} onClick={() => onLevelClick(level.name)}>
        <td className="text-right tabular-nums text-zinc-300">
          {level.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
        </td>
        <td>
          {level.name}
          {level.cluster.length > 0 && (
            <span className="text-amber-500 ml-1" title={level.cluster.join(', ')}>+{level.cluster.length}</span>
          )}
        </td>
        <td className={catColor}>{level.category}</td>
        <td className="text-right tabular-nums">{displayDist > 0 ? '+' : ''}{displayDist.toFixed(0)}</td>
        <td className="text-center">
          <span className={`px-1.5 py-0.5 text-[10px] ${badge.cls}`}>{badge.text}</span>
        </td>
      </tr>
    );
  };

  if (levels.length === 0) {
    return <div className="flex items-center justify-center text-zinc-600 text-sm h-full">No levels loaded</div>;
  }

  return (
    <table className="sq w-full table-fixed">
      <colgroup>
        <col style={{ width: '20%' }} />
        <col style={{ width: '30%' }} />
        <col style={{ width: '15%' }} />
        <col style={{ width: '15%' }} />
        <col style={{ width: '20%' }} />
      </colgroup>
      <thead className="sticky top-0 z-10 bg-panel">
        <tr>
          <th className="text-right">Price</th>
          <th>Level</th>
          <th>Type</th>
          <th className="text-right">Dist</th>
          <th className="text-center">Status</th>
        </tr>
      </thead>
      <tbody>
        {aboveLevels.map(renderRow)}

        {/* Center price divider */}
        <tr className="!bg-zinc-800/50 border-y border-amber-500/40">
          <td colSpan={5} className="!py-2">
            <div className="flex items-center gap-3 px-1">
              <span className={connected ? 'text-green-400' : 'text-red-400'}>●</span>
              <span className="font-mono text-sm text-amber-400 font-bold">NQ {cp.toFixed(2)}</span>
              <span className="text-zinc-500 text-[10px]">{connected ? 'Live' : 'Offline'}</span>
              {pricePos?.vwap_deviation_sd != null && (
                <span className={`text-[10px] font-mono ${
                  Math.abs(pricePos.vwap_deviation_sd) > 2 ? 'text-red-400' :
                  Math.abs(pricePos.vwap_deviation_sd) > 1 ? 'text-yellow-400' : 'text-zinc-400'
                }`}>
                  {pricePos.vwap_deviation_sd > 0 ? '+' : ''}{pricePos.vwap_deviation_sd.toFixed(2)} SD
                </span>
              )}
            </div>
          </td>
        </tr>

        {belowLevels.map(renderRow)}
      </tbody>
    </table>
  );
}
