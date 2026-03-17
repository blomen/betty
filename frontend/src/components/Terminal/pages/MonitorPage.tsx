import { TabIcon, TAB_COLORS } from '../TabBar';
import { ContextSidebar } from './ContextSidebar';
import type { ExpandedSession, MonitoredLevel, PricePosition } from '@/types/market';

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
  session: ExpandedSession | null;
  levels: MonitoredLevel[];
  currentPrice: number | null;
  connected: boolean;
  pricePos?: PricePosition;
  onLevelClick: (levelName: string) => void;
}

export function MonitorPage({ session, levels, currentPrice, connected, pricePos, onLevelClick }: Props) {
  const cp = currentPrice ?? 0;
  const aboveLevels = levels.filter(l => l.price > cp).sort((a, b) => a.price - b.price);
  const belowLevels = levels.filter(l => l.price <= cp).sort((a, b) => b.price - a.price);

  const renderLevelRow = (level: MonitoredLevel) => {
    const badge = STATUS_BADGES[level.status] || STATUS_BADGES.watching;
    const catColor = CATEGORY_COLORS[level.category] || 'text-zinc-400';
    const displayDist = -level.distance_ticks;
    const rowCls = level.status === 'at_level' ? 'border-l-2 border-cyan-400' :
                   level.status === 'approaching' ? 'animate-pulse' : '';

    return (
      <tr
        key={level.name}
        className={`${rowCls} cursor-pointer`}
        onClick={() => onLevelClick(level.name)}
      >
        <td className="text-right tabular-nums text-zinc-300">
          {level.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
        </td>
        <td>
          {level.name}
          {level.cluster.length > 0 && (
            <span className="text-amber-500 ml-1" title={level.cluster.join(', ')}>
              +{level.cluster.length}
            </span>
          )}
        </td>
        <td className={catColor}>{level.category}</td>
        <td className="text-right tabular-nums">
          {displayDist > 0 ? '+' : ''}{displayDist.toFixed(0)}
        </td>
        <td className="text-center">
          <span className={`px-1.5 py-0.5 text-[10px] ${badge.cls}`}>
            {badge.text}
          </span>
        </td>
      </tr>
    );
  };

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-2">
      {/* Header — same pattern as sports pages */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text flex items-center gap-2">
          <TabIcon name="tradingMonitor" color={TAB_COLORS.tradingMonitor} size={16} />
          Monitor
        </h2>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1 text-[10px]">
            <span className={connected ? 'text-green-400' : 'text-red-400'}>●</span>
            <span className="text-zinc-500">{connected ? 'Live' : 'Offline'}</span>
          </div>
          {currentPrice != null && (
            <span className="text-xs font-mono text-zinc-300">
              NQ {currentPrice.toLocaleString(undefined, { minimumFractionDigits: 2 })}
            </span>
          )}
        </div>
      </div>

      {/* Content: level table + context sidebar */}
      <div className="grid grid-cols-[7fr_3fr] gap-2 flex-1 min-h-0">
        {/* Left: Level table */}
        <div className="overflow-y-auto border-l-2 border-tabTradingScanner">
          {levels.length > 0 ? (
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
                {aboveLevels.map(renderLevelRow)}

                {/* Center price divider */}
                <tr className="!bg-zinc-800/50 border-y border-tabTradingScanner/40">
                  <td colSpan={5} className="!py-2">
                    <div className="flex items-center gap-3 px-1">
                      <span className={connected ? 'text-green-400' : 'text-red-400'}>●</span>
                      <span className="font-mono text-sm text-tabTradingScanner font-bold">
                        NQ {cp.toFixed(2)}
                      </span>
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

                {belowLevels.map(renderLevelRow)}
              </tbody>
            </table>
          ) : (
            <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm h-full">
              No levels loaded. Click Refresh to compute session.
            </div>
          )}
        </div>

        {/* Right: Context sidebar */}
        <ContextSidebar session={session} />
      </div>
    </div>
  );
}
