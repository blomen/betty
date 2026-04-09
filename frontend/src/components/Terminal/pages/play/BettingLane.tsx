import { useBettingLane, type BetDetails, type BettingLaneStatus } from '../../../../hooks/useBettingLane';
import { usePriceStream } from '../../../../hooks/usePriceStream';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface BettingLaneProps {
  providerId: string | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString('sv-SE', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function edgeClass(edge: number | null | undefined): string {
  if (edge == null) return 'text-zinc-400';
  if (edge >= 3) return 'text-green-400';
  if (edge >= 0) return 'text-amber-400';
  return 'text-red-400';
}

function StatusBadge({ status }: { status: BettingLaneStatus }) {
  const labels: Record<BettingLaneStatus, string> = {
    idle: 'Idle',
    navigating: 'Navigating...',
    filling: 'Filling stake...',
    ready: 'Ready',
    placing: 'Placing...',
  };

  const colors: Record<BettingLaneStatus, string> = {
    idle: 'text-zinc-500',
    navigating: 'text-blue-400',
    filling: 'text-amber-400',
    ready: 'text-green-400',
    placing: 'text-green-400 animate-pulse',
  };

  return (
    <span className={`text-xs font-medium ${colors[status]}`}>
      {labels[status]}
    </span>
  );
}

function UpNextRow({ bet }: { bet: BetDetails }) {
  const outcome =
    bet.outcome === 'home'
      ? bet.display_home
      : bet.outcome === 'away'
        ? bet.display_away
        : bet.outcome;

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-b border-zinc-800 last:border-0">
      <div className="flex-1 min-w-0">
        <span className="text-xs text-zinc-300 truncate">
          {bet.display_home} <span className="text-zinc-500">vs</span> {bet.display_away}
        </span>
      </div>
      <span className="text-xs text-amber-400 w-28 truncate text-right">{outcome}</span>
      <span className="text-xs text-zinc-300 w-10 text-right">{bet.odds.toFixed(2)}</span>
      <span className={`text-xs w-14 text-right font-medium ${edgeClass(bet.edge_pct)}`}>
        {bet.edge_pct >= 0 ? '+' : ''}{bet.edge_pct.toFixed(1)}%
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// BettingLane
// ---------------------------------------------------------------------------

export function BettingLane({ providerId }: BettingLaneProps) {
  const { currentBet, upNext, status, placeBet, skipBet } = useBettingLane(providerId);
  const { domOdds, apiOdds, fairOdds, edge: liveEdge, priceMatch, lastUpdate } = usePriceStream(
    providerId,
    currentBet?.bet_id ?? null,
  );

  const isPlacing = status === 'placing';
  const canPlace = status === 'ready' && !isPlacing;

  // ---------------------------------------------------------------------------
  // Empty state
  // ---------------------------------------------------------------------------

  if (!providerId) {
    return (
      <div
        className="flex flex-col items-center justify-center h-full text-zinc-500 text-sm"
        style={{ flex: '1.2' }}
      >
        No provider selected
      </div>
    );
  }

  if (!currentBet) {
    const msg = status === 'idle' ? 'No bets remaining' : 'Loading...';
    return (
      <div
        className="flex flex-col items-center justify-center h-full text-zinc-500 text-sm"
        style={{ flex: '1.2' }}
      >
        <span className={status !== 'idle' ? 'animate-pulse' : ''}>{msg}</span>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Derived display values
  // ---------------------------------------------------------------------------

  const bet = currentBet;
  const displayEdge = liveEdge ?? bet.edge_pct;
  const displayFair = fairOdds ?? bet.fair_odds;

  const outcomeLabel =
    bet.outcome === 'home'
      ? bet.display_home
      : bet.outcome === 'away'
        ? bet.display_away
        : bet.outcome;

  const marketLabel = bet.market.toUpperCase();
  const isSpreadOrTotal = bet.market === 'spread' || bet.market === 'total';

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="flex flex-col gap-3 p-3 overflow-y-auto" style={{ flex: '1.2' }}>

      {/* 1. Current Bet */}
      <div className="border border-zinc-700 bg-zinc-900">
        <div className="px-3 py-2 border-b border-zinc-800">
          <div className="text-sm text-zinc-200 font-medium">
            {bet.display_home} <span className="text-zinc-500">vs</span> {bet.display_away}
          </div>
          <div className="text-xs text-zinc-500 mt-0.5">
            {bet.sport}
            {bet.league && <span> · {bet.league}</span>}
            {bet.start_time && <span> · {formatDate(bet.start_time)}</span>}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 px-3 py-2 text-xs">
          <div>
            <span className="text-blue-400">Market</span>
            <span className="text-zinc-300 ml-2">{marketLabel}</span>
          </div>
          <div>
            <span className="text-blue-400">Outcome</span>
            <span className="text-amber-400 ml-2">{outcomeLabel}</span>
          </div>

          <div>
            <span className="text-blue-400">Odds</span>
            <span className="text-zinc-200 ml-2 font-medium">{bet.odds.toFixed(2)}</span>
          </div>
          <div>
            <span className="text-blue-400">Fair</span>
            <span className="text-zinc-400 ml-2">{displayFair != null ? displayFair.toFixed(2) : '—'}</span>
          </div>

          <div>
            <span className="text-blue-400">Edge</span>
            <span className={`ml-2 font-semibold ${edgeClass(displayEdge)}`}>
              {displayEdge != null
                ? `${displayEdge >= 0 ? '+' : ''}${displayEdge.toFixed(1)}%`
                : '—'}
            </span>
          </div>
          <div>
            <span className="text-blue-400">Stake</span>
            <span className="text-zinc-200 ml-2 font-medium">{Math.round(bet.stake)} kr</span>
          </div>

          {bet.kelly_pct != null && (
            <div>
              <span className="text-blue-400">Kelly</span>
              <span className="text-zinc-400 ml-2">{bet.kelly_pct.toFixed(2)}%</span>
            </div>
          )}

          {isSpreadOrTotal && bet.point != null && (
            <div>
              <span className="text-blue-400">Line</span>
              <span className="text-zinc-300 ml-2">
                {bet.point > 0 ? '+' : ''}{bet.point}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* 2. Live Price Stream */}
      <div className="border border-purple-800/50 bg-purple-950/20 px-3 py-2">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs font-medium text-purple-400 uppercase tracking-wider">Live Prices</span>
          {lastUpdate && (
            <span className="text-[10px] text-zinc-600">
              {lastUpdate.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
        </div>

        <div className="grid grid-cols-3 gap-2 text-xs">
          <div>
            <div className="text-purple-500 mb-0.5">DOM</div>
            <div className="text-zinc-200 font-medium">
              {domOdds != null ? domOdds.toFixed(2) : <span className="text-zinc-600">—</span>}
            </div>
          </div>
          <div>
            <div className="text-purple-500 mb-0.5">API</div>
            <div className="text-zinc-200 font-medium">
              {apiOdds != null ? apiOdds.toFixed(2) : <span className="text-zinc-600">—</span>}
            </div>
          </div>
          <div>
            <div className="text-purple-500 mb-0.5">Fair</div>
            <div className="text-zinc-400 font-medium">
              {fairOdds != null ? fairOdds.toFixed(2) : <span className="text-zinc-600">—</span>}
            </div>
          </div>
        </div>

        {priceMatch && (
          <div className="flex items-center gap-1 mt-1.5 text-green-400 text-xs">
            <span>✓</span>
            <span>Prices match</span>
          </div>
        )}
      </div>

      {/* 3. Status */}
      <div className="flex items-center gap-2 px-1">
        <span className="text-xs text-zinc-600 uppercase tracking-wider">Status</span>
        <StatusBadge status={status} />
      </div>

      {/* 4. Action Buttons */}
      <div className="flex items-center gap-2">
        <button
          onClick={placeBet}
          disabled={!canPlace}
          className="px-4 py-1.5 text-xs bg-green-700 text-white font-medium hover:bg-green-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {isPlacing ? 'Placing...' : 'Place Bet'}
        </button>
        <button
          onClick={skipBet}
          disabled={isPlacing}
          className="px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Skip
        </button>
      </div>

      {/* 5. Up Next */}
      {upNext.length > 0 && (
        <div className="border border-zinc-800">
          <div className="px-3 py-1.5 border-b border-zinc-800 bg-zinc-900/50">
            <span className="text-[10px] font-medium text-zinc-500 uppercase tracking-wider">
              Up Next ({upNext.length})
            </span>
          </div>
          <div>
            {upNext.slice(0, 5).map(b => (
              <UpNextRow key={b.bet_id} bet={b} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
