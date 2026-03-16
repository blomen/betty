import type { MonitoredLevel } from '@/types/market';

interface Props {
  levels: MonitoredLevel[];
  currentPrice: number | null;
  onLevelClick: (levelName: string) => void;
  compact?: boolean;
}

const STATUS_STYLES: Record<string, string> = {
  watching: 'text-zinc-600',
  approaching: 'text-amber-400 animate-pulse',
  at_level: 'text-cyan-400 font-bold border-l-2 border-cyan-400',
  triggered: 'text-emerald-600',
  rejected: 'text-zinc-600',
};

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

export function LevelMonitorTable({ levels, currentPrice, onLevelClick, compact }: Props) {
  const displayLevels = compact ? levels.slice(0, 5) : levels;

  return (
    <div className="font-mono text-xs">
      <table className="w-full">
        <thead>
          <tr className="text-zinc-500 text-[10px] uppercase">
            <th className="text-right pr-2 py-1">Price</th>
            <th className="text-left py-1">Level</th>
            <th className="text-left py-1">Type</th>
            <th className="text-right pr-2 py-1">Dist</th>
            <th className="text-center py-1">Status</th>
          </tr>
        </thead>
        <tbody>
          {displayLevels.map(level => {
            const rowStyle = STATUS_STYLES[level.status] || '';
            const badge = STATUS_BADGES[level.status] || STATUS_BADGES.watching;
            const catColor = CATEGORY_COLORS[level.category] || 'text-zinc-400';

            return (
              <tr
                key={level.name}
                className={`${rowStyle} hover:bg-zinc-800/50 cursor-pointer`}
                onClick={() => onLevelClick(level.name)}
              >
                <td className="text-right pr-2 py-0.5 text-zinc-300 tabular-nums">
                  {level.price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                </td>
                <td className="text-left py-0.5">
                  {level.name}
                  {level.cluster.length > 0 && (
                    <span className="text-amber-500 ml-1" title={level.cluster.join(', ')}>
                      +{level.cluster.length}
                    </span>
                  )}
                </td>
                <td className={`text-left py-0.5 ${catColor}`}>{level.category}</td>
                <td className="text-right pr-2 py-0.5 tabular-nums">
                  {level.distance_ticks > 0 ? '+' : ''}{level.distance_ticks.toFixed(0)}
                </td>
                <td className="text-center py-0.5">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${badge.cls}`}>
                    {badge.text}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {compact && levels.length > 5 && (
        <div className="text-zinc-600 text-center text-[10px] py-1">
          +{levels.length - 5} more levels
        </div>
      )}
    </div>
  );
}
