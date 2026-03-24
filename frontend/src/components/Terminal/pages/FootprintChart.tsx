import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '@/services/api';

interface PriceLevel {
  price: number;
  buy_vol: number;
  sell_vol: number;
}

interface DiagImbalance {
  price: number;
  direction: 'buy' | 'sell';
  ratio: number;
}

interface StackedImbalance {
  direction: 'buy' | 'sell';
  price_low: number;
  price_high: number;
  count: number;
}

interface FootprintCandle {
  ts: string;
  o: number;
  h: number;
  l: number;
  c: number;
  volume: number;
  buy_volume: number;
  sell_volume: number;
  delta: number;
  delta_pct: number;
  price_levels: PriceLevel[];
  diagonal_imbalances: DiagImbalance[];
  stacked_imbalances: StackedImbalance[];
}

interface Props {
  /** Auto-refresh interval in ms (0 = manual only) */
  refreshMs?: number;
  /** Candle period in seconds */
  period?: number;
  /** Number of candles to show */
  limit?: number;
}

// Build a set of imbalance prices for fast lookup
function imbalanceSet(imbalances: DiagImbalance[]): Map<number, DiagImbalance> {
  const m = new Map<number, DiagImbalance>();
  for (const d of imbalances) m.set(d.price, d);
  return m;
}

// Build stacked imbalance ranges for highlight
function isInStacked(price: number, stacked: StackedImbalance[]): StackedImbalance | null {
  for (const s of stacked) {
    if (price >= s.price_low && price <= s.price_high) return s;
  }
  return null;
}

function CandleColumn({ candle, allPrices }: { candle: FootprintCandle; allPrices: number[] }) {
  const isBullish = candle.c >= candle.o;
  const diagMap = imbalanceSet(candle.diagonal_imbalances);
  // Build price→level map
  const levelMap = new Map<number, PriceLevel>();
  for (const pl of candle.price_levels) levelMap.set(pl.price, pl);

  const time = new Date(candle.ts).toLocaleTimeString('en-US', {
    hour: '2-digit', minute: '2-digit', hour12: false,
  });

  return (
    <div className="flex flex-col items-center min-w-[140px]">
      {/* Time header */}
      <div className="text-[9px] text-zinc-500 font-mono mb-1">{time}</div>

      {/* Price levels — top to bottom (high to low) */}
      <div className="flex flex-col w-full">
        {allPrices.map(price => {
          const pl = levelMap.get(price);
          const diag = diagMap.get(price);
          const stacked = isInStacked(price, candle.stacked_imbalances);
          const inBody = price >= Math.min(candle.o, candle.c) && price <= Math.max(candle.o, candle.c);
          const inWick = price >= candle.l && price <= candle.h;

          if (!pl && !inWick) {
            return <div key={price} className="h-[18px]" />;
          }

          // Background: stacked > diagonal > body > wick > nothing
          let bgCls = '';
          if (stacked) {
            bgCls = stacked.direction === 'buy'
              ? 'bg-emerald-900/40 border-l-2 border-emerald-500'
              : 'bg-red-900/40 border-l-2 border-red-500';
          } else if (diag) {
            bgCls = diag.direction === 'buy'
              ? 'bg-emerald-900/25'
              : 'bg-red-900/25';
          } else if (inBody) {
            bgCls = isBullish ? 'bg-emerald-900/15' : 'bg-red-900/15';
          } else if (inWick) {
            bgCls = 'bg-zinc-800/30';
          }

          const buyVol = pl?.buy_vol ?? 0;
          const sellVol = pl?.sell_vol ?? 0;

          return (
            <div key={price} className={`h-[18px] flex items-center font-mono text-[10px] ${bgCls}`}>
              {(buyVol > 0 || sellVol > 0) ? (
                <div className="flex items-center w-full px-0.5">
                  {/* Sell (left) */}
                  <span className={`w-[35%] text-right pr-1 ${
                    diag?.direction === 'sell' ? 'text-red-300 font-bold' : 'text-red-500/70'
                  }`}>
                    {sellVol > 0 ? sellVol : ''}
                  </span>
                  {/* Divider */}
                  <span className="text-zinc-700 mx-px">×</span>
                  {/* Buy (right) */}
                  <span className={`w-[35%] text-left pl-1 ${
                    diag?.direction === 'buy' ? 'text-emerald-300 font-bold' : 'text-emerald-500/70'
                  }`}>
                    {buyVol > 0 ? buyVol : ''}
                  </span>
                  {/* Imbalance ratio badge */}
                  {diag && (
                    <span className={`ml-auto text-[8px] px-0.5 ${
                      diag.direction === 'buy' ? 'text-emerald-400' : 'text-red-400'
                    }`}>
                      {diag.ratio < 99 ? `${diag.ratio}:1` : '∞'}
                    </span>
                  )}
                </div>
              ) : (
                // Empty wick level
                <div className="w-full text-center text-zinc-800">·</div>
              )}
            </div>
          );
        })}
      </div>

      {/* Delta footer */}
      <div className={`text-[10px] font-mono font-bold mt-1 ${
        candle.delta > 0 ? 'text-emerald-400' : candle.delta < 0 ? 'text-red-400' : 'text-zinc-500'
      }`}>
        Δ {candle.delta > 0 ? '+' : ''}{candle.delta}
        <span className="text-zinc-600 font-normal ml-1">({candle.delta_pct > 0 ? '+' : ''}{candle.delta_pct}%)</span>
      </div>

      {/* Volume */}
      <div className="text-[9px] text-zinc-600 font-mono">
        {candle.volume.toLocaleString()}
      </div>

      {/* Stacked imbalance summary */}
      {candle.stacked_imbalances.length > 0 && (
        <div className="mt-0.5">
          {candle.stacked_imbalances.map((s, i) => (
            <div key={i} className={`text-[8px] font-bold uppercase ${
              s.direction === 'buy' ? 'text-emerald-400' : 'text-red-400'
            }`}>
              ▌ {s.count}× {s.direction}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function FootprintChart({ refreshMs = 10_000, period = 300, limit = 8 }: Props) {
  const [candles, setCandles] = useState<FootprintCandle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const fetchData = useCallback(async () => {
    try {
      const res = await api.getFootprint(period, limit);
      if (res.candles) {
        setCandles(res.candles);
        setError(null);
      } else if (res.error) {
        setError(res.error);
      }
    } catch {
      setError('Failed to fetch footprint');
    } finally {
      setLoading(false);
    }
  }, [period, limit]);

  useEffect(() => {
    fetchData();
    if (refreshMs > 0) {
      const id = setInterval(fetchData, refreshMs);
      return () => clearInterval(id);
    }
  }, [fetchData, refreshMs]);

  // Auto-scroll to right (most recent candle)
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollLeft = scrollRef.current.scrollWidth;
    }
  }, [candles]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-600 text-xs font-mono">
        Loading footprint...
      </div>
    );
  }

  if (error || candles.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-zinc-600 gap-1">
        <div className="text-sm font-mono">{error || 'No footprint data'}</div>
        <div className="text-[10px] text-zinc-700">Footprint populates when the live stream is active</div>
      </div>
    );
  }

  // Compute the unified price axis across all visible candles
  const allPricesSet = new Set<number>();
  for (const c of candles) {
    for (const pl of c.price_levels) allPricesSet.add(pl.price);
    // Also include wick range
    const tick = 0.25;
    for (let p = c.l; p <= c.h; p += tick) {
      allPricesSet.add(Math.round(p / tick) * tick);
    }
  }
  const allPrices = Array.from(allPricesSet).sort((a, b) => b - a); // High to low

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-2 py-1 border-b border-zinc-800 flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-zinc-500 uppercase tracking-wider">Footprint</span>
          <span className="text-[10px] text-zinc-600 font-mono">{period / 60}m</span>
        </div>
        <div className="flex items-center gap-3 text-[9px] text-zinc-600">
          <span><span className="text-emerald-500">●</span> Buy imbalance</span>
          <span><span className="text-red-500">●</span> Sell imbalance</span>
          <span><span className="text-amber-400">▌</span> Stacked</span>
        </div>
      </div>

      {/* Scrollable grid: price labels + candle columns */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Fixed price axis */}
        <div className="flex-shrink-0 flex flex-col pt-[18px]"> {/* offset for time header */}
          {allPrices.map(p => (
            <div key={p} className="h-[18px] flex items-center pr-1">
              <span className="text-[9px] text-zinc-600 font-mono tabular-nums">
                {p.toFixed(2)}
              </span>
            </div>
          ))}
        </div>

        {/* Scrollable candle columns */}
        <div ref={scrollRef} className="flex-1 overflow-x-auto overflow-y-hidden">
          <div className="flex gap-px">
            {candles.map(c => (
              <CandleColumn key={c.ts} candle={c} allPrices={allPrices} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
