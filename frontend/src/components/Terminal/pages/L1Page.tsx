import { useState, useEffect } from 'react';
import { BookSnapshot } from './BookSnapshot';
import { CandleChart } from './CandleChart';
import { usePersistedState } from '@/hooks/usePersistedState';
import { useMarketStatus } from '@/hooks/useMarketStatus';
import { api } from '@/services/api';
import type { StreamTickEvent, StreamBookEvent, CandleData, ExpandedSession, TPOLiveProfile } from '@/types/market';

interface Props {
  lastTick: StreamTickEvent | null;
  book: StreamBookEvent | null;
  lastCandle: CandleData | null;
  connected: boolean;
  session: ExpandedSession | null;
}

export function L1Page({ lastTick, book, lastCandle, connected, session }: Props) {
  const price = lastTick?.price ?? session?.price_position?.last_price ?? null;
  const [hiddenLevels, setHiddenLevels] = usePersistedState<Set<string>>('l1-hidden-levels', new Set());
  const [tpo, setTpo] = useState<TPOLiveProfile | null>(null);
  const market = useMarketStatus();

  // Fetch TPO data alongside session updates
  useEffect(() => {
    api.getTpoLive('NQ').then(setTpo).catch(() => {});
  }, [session]);

  const dotColor = market.state === 'open' && connected
    ? 'bg-emerald-500'
    : market.state === 'halt'
      ? 'bg-yellow-500'
      : 'bg-red-500';

  const statusLabel = !connected && market.state === 'open'
    ? 'DISCONNECTED'
    : market.label;

  return (
    <div className="flex flex-col flex-1 min-h-0 gap-3">
      {/* Header */}
      <div className="flex items-center gap-3 px-1">
        <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />
        <span className="text-xs text-muted font-mono">
          {statusLabel}
        </span>
        {market.opensIn && (
          <span className="text-xs text-muted font-mono">
            opens in {market.opensIn}
          </span>
        )}
        {price && (
          <span className="text-sm font-mono font-bold text-text">
            NQ {price.toFixed(2)}
          </span>
        )}
        <span className="text-xs text-muted font-mono ml-auto">LEVEL 1</span>
      </div>

      {/* 2-column grid: Chart | Book (top of book + OHLCV) */}
      <div className="flex-1 grid grid-cols-[5fr_3fr] gap-3 min-h-0">
        {/* Left — Candle Chart */}
        <div className="border border-border bg-panel min-h-0 overflow-hidden">
          <CandleChart lastCandle={lastCandle} session={session} hiddenLevels={hiddenLevels} tpo={tpo} />
        </div>

        {/* Right — Book Snapshot (best bid/ask + candle stats) */}
        <div className="border border-border bg-panel min-h-0">
          <BookSnapshot book={book} lastCandle={lastCandle} session={session} hiddenLevels={hiddenLevels} setHiddenLevels={setHiddenLevels} tpo={tpo} />
        </div>
      </div>
    </div>
  );
}
