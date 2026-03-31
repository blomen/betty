import { useState, useEffect, useMemo } from 'react';
import { BookSnapshot } from './BookSnapshot';
import { CandleChart } from './CandleChart';
import { usePersistedState } from '@/hooks/usePersistedState';
import { useMarketStatus } from '@/hooks/useMarketStatus';
import { useConnectionStatus } from '@/hooks/useConnectionStatus';
import { api } from '@/services/api';
import type { StreamTickEvent, StreamBookEvent, CandleData, ExpandedSession, TPOLiveProfile, SessionTPOResponse } from '@/types/market';

interface Props {
  lastTick: StreamTickEvent | null;
  book: StreamBookEvent | null;
  lastCandle: CandleData | null;
  connected: boolean;
  session: ExpandedSession | null;
}

export function ChartPage({ lastTick, book, lastCandle, connected, session }: Props) {
  const price = lastTick?.price ?? session?.price_position?.last_price ?? null;
  const [hiddenLevels, setHiddenLevels] = usePersistedState<Set<string>>('chart-hidden-levels', new Set());
  const [tpo, setTpo] = useState<TPOLiveProfile | null>(null);
  const [sessionTPO, setSessionTPO] = useState<SessionTPOResponse | null>(null);
  const [cotData, setCotData] = useState<{ cot_net_position: number | null; cot_change_1w: number | null } | null>(null);
  const market = useMarketStatus();
  // Stabilise: only update timestamp when lastTick identity changes (not every render)
  const health = useConnectionStatus();

  // Fetch TPO data alongside session updates
  useEffect(() => {
    api.getTpoLive('NQ').then(setTpo).catch(() => {});
    api.getSessionTPO('NQ').then(setSessionTPO).catch(() => {});
  }, [session]);

  // Fetch COT independently (doesn't depend on session pipeline)
  useEffect(() => {
    api.getCotSummary().then(setCotData).catch(() => {});
  }, []);

  // Enrich session with COT data
  const enrichedSession = useMemo(() => {
    if (!session) return null;
    if (!cotData || (cotData.cot_net_position == null && cotData.cot_change_1w == null)) return session;
    return {
      ...session,
      macro: { ...session.macro, ...cotData },
    };
  }, [session, cotData]);

  const dotColor = health.status === 'connecting'
    ? 'bg-orange-500 animate-pulse'
    : market.state === 'open' && connected && health.status !== 'down'
      ? 'bg-emerald-500'
      : market.state === 'halt'
        ? 'bg-yellow-500'
        : 'bg-red-500';

  const statusLabel = health.status === 'connecting'
    ? 'CONNECTING'
    : health.status === 'down'
      ? 'BACKEND DOWN'
      : health.status === 'slow'
        ? 'LOOP STALLED'
        : !connected && market.state === 'open'
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
        {/* Backend health indicator */}
        {health.status !== 'ok' && health.status !== 'checking' && (
          <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${
            health.status === 'connecting' ? 'bg-orange-500/20 text-orange-400'
            : health.status === 'down' ? 'bg-red-500/20 text-red-400'
            : 'bg-yellow-500/20 text-yellow-400'
          }`}>
            {health.message}
            {health.status === 'slow' && ` - restart backend`}
            {health.status === 'down' && ` - restart backend`}
          </span>
        )}
        {health.latencyMs != null && (
          <span className={`text-xs font-mono ${
            health.latencyMs > 2000 ? 'text-red-400' : health.latencyMs > 500 ? 'text-yellow-400' : 'text-zinc-600'
          }`}>
            {health.latencyMs}ms
          </span>
        )}
      </div>

      {/* 2-column grid: Chart | Book (top of book + OHLCV) */}
      <div className="flex-1 grid grid-cols-[1fr_220px] gap-2 min-h-0">
        {/* Left — Candle Chart */}
        <div className="border border-border bg-panel min-h-0 overflow-hidden">
          <CandleChart lastCandle={lastCandle} session={enrichedSession} hiddenLevels={hiddenLevels} />
        </div>

        {/* Right — Book Snapshot (best bid/ask + candle stats) */}
        <div className="border border-border bg-panel min-h-0">
          <BookSnapshot book={book} lastCandle={lastCandle} session={enrichedSession} hiddenLevels={hiddenLevels} setHiddenLevels={setHiddenLevels} tpo={tpo} sessionTPO={sessionTPO} />
        </div>
      </div>
    </div>
  );
}
