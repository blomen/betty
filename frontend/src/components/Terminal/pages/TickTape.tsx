import { useRef, useEffect, useState } from 'react';
import type { StreamTickEvent } from '@/types/market';

const MAX_TICKS = 200;

interface Props {
  lastTick: StreamTickEvent | null;
}

interface TapeEntry {
  ts: string;
  price: number;
  size: number;
  side: 'A' | 'B';
}

export function TickTape({ lastTick }: Props) {
  const [ticks, setTicks] = useState<TapeEntry[]>([]);
  const containerRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);

  useEffect(() => {
    if (!lastTick) return;
    setTicks(prev => {
      const next = [...prev, { ts: lastTick.ts, price: lastTick.price, size: lastTick.size, side: lastTick.side }];
      return next.length > MAX_TICKS ? next.slice(-MAX_TICKS) : next;
    });
  }, [lastTick]);

  // Auto-scroll to bottom when new ticks arrive
  useEffect(() => {
    if (autoScrollRef.current && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [ticks]);

  const handleScroll = () => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    autoScrollRef.current = scrollHeight - scrollTop - clientHeight < 40;
  };

  const formatTime = (ts: string) => {
    try {
      const d = new Date(ts);
      return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return ts.slice(11, 19);
    }
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header with CVD + Delta */}
      <div className="px-2 py-1.5 border-b border-border flex items-center justify-between text-[10px] font-mono">
        <span className="text-muted uppercase tracking-wider">Time &amp; Sales</span>
        {lastTick && (
          <div className="flex gap-3">
            <span className={lastTick.cvd >= 0 ? 'text-emerald-400' : 'text-red-400'}>
              CVD {lastTick.cvd > 0 ? '+' : ''}{lastTick.cvd}
            </span>
            <span className={lastTick.delta_1m >= 0 ? 'text-emerald-400' : 'text-red-400'}>
              Δ1m {lastTick.delta_1m > 0 ? '+' : ''}{lastTick.delta_1m}
            </span>
          </div>
        )}
      </div>

      {/* Column headers */}
      <div className="grid grid-cols-[3fr_3fr_2fr_1.5fr] px-2 py-1 text-[10px] font-mono text-muted2 border-b border-border">
        <span>Time</span>
        <span className="text-right">Price</span>
        <span className="text-right">Size</span>
        <span className="text-right">Side</span>
      </div>

      {/* Scrollable tick list */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto overflow-x-hidden"
      >
        {ticks.length === 0 ? (
          <div className="text-muted2 text-[10px] font-mono p-4 text-center">Waiting for ticks...</div>
        ) : (
          ticks.map((t, i) => (
            <div
              key={i}
              className="grid grid-cols-[3fr_3fr_2fr_1.5fr] px-2 py-[2px] text-[10px] font-mono hover:bg-white/[0.02]"
            >
              <span className="text-muted2">{formatTime(t.ts)}</span>
              <span className="text-right text-text">{t.price.toFixed(2)}</span>
              <span className="text-right text-muted">{t.size}</span>
              <span className={`text-right font-bold ${t.side === 'A' ? 'text-emerald-400' : 'text-red-400'}`}>
                {t.side === 'A' ? 'BUY' : 'SELL'}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
