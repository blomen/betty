import { useRef, useEffect, useState, useCallback } from 'react';
import {
  createChart,
  HistogramSeries,
  LineSeries,
  AreaSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type HistogramData,
  type LineData,
  type Time,
  ColorType,
} from 'lightweight-charts';
import { api } from '@/services/api';
import type { CandleData, ExpandedSession } from '@/types/market';

const INTERVAL = '1m';
const INITIAL_DAYS = 3;
const SCROLL_DAYS = 1;

// VP overlay config: which timeframes to show, with colors
// Only daily VP overlay for now — verified
const VP_OVERLAYS = [
  { tf: 'session', color: [168, 85, 247],  label: 'D' },   // purple
] as const;

// Session box definitions (ET times as hour*60+minute)
// Tokyo: 20:00 ET (prior day) → 02:00 ET (current day)
// London: 03:00 → 08:30 ET
// New York (RTH): 09:30 → 16:00 ET
const SESSION_DEFS = [
  { name: 'Tokyo',    startMin: 20 * 60,  endMin: 26 * 60,      color: 'rgba(6, 182, 212, 0.12)',  border: 'rgba(6, 182, 212, 0.35)',  label: '#06B6D4' },  // cyan
  { name: 'London',   startMin: 3 * 60,   endMin: 8 * 60 + 30,  color: 'rgba(16, 185, 129, 0.12)', border: 'rgba(16, 185, 129, 0.35)', label: '#10B981' },  // green
  { name: 'New York', startMin: 9 * 60 + 30, endMin: 16 * 60,   color: 'rgba(239, 68, 68, 0.10)',  border: 'rgba(239, 68, 68, 0.30)',  label: '#EF4444' },  // red
] as const;

// ET offset from UTC: -5 (EST) or -4 (EDT). Approximate with -4 for March-Nov.
function getETOffset(epoch: number): number {
  const d = new Date(epoch * 1000);
  const month = d.getUTCMonth(); // 0-indexed
  // EDT: March (2) second Sunday → November (10) first Sunday
  return (month >= 2 && month < 10) ? -4 : -5;
}

function epochToETMinute(epoch: number): number {
  const offset = getETOffset(epoch);
  const d = new Date((epoch + offset * 3600) * 1000);
  return d.getUTCHours() * 60 + d.getUTCMinutes();
}

function epochToETDate(epoch: number): string {
  const offset = getETOffset(epoch);
  const d = new Date((epoch + offset * 3600) * 1000);
  return d.toISOString().slice(0, 10);
}

interface SessionBox {
  name: string;
  high: number;
  low: number;
  startEpoch: number;
  endEpoch: number;
  color: string;
  border: string;
  labelColor: string;
}

type VPData = { levels: Array<{ price: number; volume: number }>; poc: number; vah: number; val: number };

interface Props {
  lastCandle: CandleData | null;
  session: ExpandedSession | null;
}

function toLine(c: CandleData): LineData<Time> {
  return { time: c.t as Time, value: c.c };
}

function toVolume(c: CandleData): HistogramData<Time> {
  const color = c.c >= c.o ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)';
  return { time: c.t as Time, value: c.v, color };
}

function epochToDateStr(epoch: number): string {
  return new Date(epoch * 1000).toISOString().slice(0, 10);
}

function detectSessionBoxes(candles: CandleData[]): SessionBox[] {
  if (candles.length < 2) return [];

  const boxes: SessionBox[] = [];

  // Group candles by ET date
  const dateGroups = new Map<string, CandleData[]>();
  for (const c of candles) {
    const etDate = epochToETDate(c.t);
    if (!dateGroups.has(etDate)) dateGroups.set(etDate, []);
    dateGroups.get(etDate)!.push(c);
  }

  for (const [etDate, dayCandles] of dateGroups) {
    for (const def of SESSION_DEFS) {
      // Tokyo wraps midnight: startMin=20:00 (1200), endMin=26:00 (1560 = 02:00 next day)
      const sessionCandles = dayCandles.filter(c => {
        let etMin = epochToETMinute(c.t);
        if (def.name === 'Tokyo') {
          // For Tokyo, also include candles from the previous ET date that are >= 20:00
          // This group contains candles for this ET date, so we look at 00:00-02:00
          return etMin < (def.endMin - 24 * 60); // 0 to 120 (02:00)
        }
        return etMin >= def.startMin && etMin < def.endMin;
      });

      // For Tokyo evening part, need to look at the previous date's candles >= 20:00
      if (def.name === 'Tokyo') {
        // Find previous date
        const prevDate = new Date(new Date(etDate).getTime() - 86400000).toISOString().slice(0, 10);
        const prevCandles = dateGroups.get(prevDate) || [];
        const eveningCandles = prevCandles.filter(c => epochToETMinute(c.t) >= def.startMin);
        sessionCandles.push(...eveningCandles);
      }

      if (sessionCandles.length < 2) continue;

      const high = Math.max(...sessionCandles.map(c => c.h));
      const low = Math.min(...sessionCandles.map(c => c.l));
      const startEpoch = Math.min(...sessionCandles.map(c => c.t));
      const endEpoch = Math.max(...sessionCandles.map(c => c.t));

      boxes.push({
        name: def.name,
        high,
        low,
        startEpoch,
        endEpoch,
        color: def.color,
        border: def.border,
        labelColor: def.label,
      });
    }
  }

  return boxes;
}

export function CandleChart({ lastCandle, session }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const priceSeriesRef = useRef<ISeriesApi<'Area'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const [noData, setNoData] = useState(false);
  const priceLineRefs = useRef<Record<string, any>>({});
  const anchorSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const vwapSeriesRefs = useRef<ISeriesApi<'Line'>[]>([]);

  // Scroll-back state
  const candlesRef = useRef<CandleData[]>([]);
  const fetchingRef = useRef(false);
  const exhaustedRef = useRef(false);

  // VP overlay data
  const vpDataRef = useRef<Map<string, VPData>>(new Map());
  const [vpLoaded, setVpLoaded] = useState(0); // trigger redraws

  // Draw VP histograms + session boxes on canvas
  const drawOverlays = useCallback(() => {
    const canvas = canvasRef.current;
    const chart = chartRef.current;
    const pSeries = priceSeriesRef.current;
    if (!canvas || !chart || !pSeries) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, rect.width, rect.height);

    const timeScale = chart.timeScale();

    // --- Session boxes ---
    const candles = candlesRef.current;
    if (candles.length > 0) {
      const boxes = detectSessionBoxes(candles);
      for (const box of boxes) {
        const x1 = timeScale.timeToCoordinate(box.startEpoch as Time);
        const x2 = timeScale.timeToCoordinate(box.endEpoch as Time);
        const y1 = pSeries.priceToCoordinate(box.high);
        const y2 = pSeries.priceToCoordinate(box.low);

        if (x1 === null || x2 === null || y1 === null || y2 === null) continue;
        if (x2 < 0 || x1 > rect.width) continue; // off-screen

        const bx = Math.min(x1, x2);
        const bw = Math.abs(x2 - x1);
        const by = Math.min(y1, y2);
        const bh = Math.abs(y2 - y1);

        // Fill
        ctx.fillStyle = box.color;
        ctx.fillRect(bx, by, bw, bh);

        // Border
        ctx.strokeStyle = box.border;
        ctx.lineWidth = 1;
        ctx.strokeRect(bx, by, bw, bh);

        // Label at top-right of box
        ctx.font = '10px monospace';
        ctx.fillStyle = box.labelColor;
        ctx.textAlign = 'right';
        ctx.fillText(box.name, bx + bw - 3, by + 11);
      }
    }

    // --- VP histogram on right edge ---
    const vpMap = vpDataRef.current;
    const vp = vpMap.get('session');
    if (vp && vp.levels.length) {
      const maxVol = Math.max(...vp.levels.map(l => l.volume));
      if (maxVol > 0) {
        const maxBarWidth = 80;
        const priceScaleWidth = 65;
        const xRight = rect.width - priceScaleWidth;

        for (const level of vp.levels) {
          const y = pSeries.priceToCoordinate(level.price);
          if (y === null || y < 0 || y > rect.height) continue;

          const barW = (level.volume / maxVol) * maxBarWidth;
          const inVA = level.price >= vp.val && level.price <= vp.vah;
          const isPOC = level.price === vp.poc;

          ctx.fillStyle = isPOC
            ? 'rgba(168, 85, 247, 0.6)'
            : inVA
              ? 'rgba(168, 85, 247, 0.2)'
              : 'rgba(168, 85, 247, 0.06)';

          ctx.fillRect(xRight - barW, y - 1, barW, 2);
        }
      }
    }
  }, []);

  // Initialize chart
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#9AA0A6',
        fontSize: 10,
        fontFamily: 'monospace',
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.03)' },
        horzLines: { color: 'rgba(255,255,255,0.03)' },
      },
      crosshair: {
        vertLine: { color: 'rgba(255,255,255,0.15)', labelBackgroundColor: '#1a1e1a' },
        horzLine: { color: 'rgba(255,255,255,0.15)', labelBackgroundColor: '#1a1e1a' },
      },
      rightPriceScale: {
        borderColor: 'rgba(255,255,255,0.08)',
        scaleMargins: { top: 0.05, bottom: 0.25 },
      },
      timeScale: {
        borderColor: 'rgba(255,255,255,0.08)',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 5,
        barSpacing: 7,
        minBarSpacing: 3,
      },
      handleScroll: { vertTouchDrag: false },
    });

    const priceSeries = chart.addSeries(AreaSeries, {
      lineColor: '#E0E0E0',
      lineWidth: 1,
      topColor: 'rgba(255, 255, 255, 0.04)',
      bottomColor: 'rgba(255, 255, 255, 0.0)',
      lastValueVisible: true,
      priceLineVisible: true,
      crosshairMarkerVisible: true,
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });

    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    const anchorSeries = chart.addSeries(LineSeries, {
      color: 'transparent',
      lineWidth: 1,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });

    chartRef.current = chart;
    priceSeriesRef.current = priceSeries;
    volumeSeriesRef.current = volumeSeries;
    anchorSeriesRef.current = anchorSeries;

    // Load candles immediately after chart is created
    (async () => {
      try {
        const res = await api.getCandles('NQ', INTERVAL, undefined, INITIAL_DAYS);
        if (res.candles?.length) {
          candlesRef.current = res.candles;
          priceSeries.setData(res.candles.map(toLine));
          volumeSeries.setData(res.candles.map(toVolume));
          chart.timeScale().scrollToRealTime();
          setNoData(false);
        } else {
          setNoData(true);
        }
      } catch (err) {
        console.warn('Failed to load candles:', err);
        setNoData(true);
      }
    })();

    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        chart.applyOptions({ width, height });
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      priceSeriesRef.current = null as any;
      volumeSeriesRef.current = null;
      anchorSeriesRef.current = null;
    };
  }, []);

  // Subscribe VP overlay redraws to chart events (separate from init)
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const redraw = () => drawOverlays();
    chart.timeScale().subscribeVisibleLogicalRangeChange(redraw);

    const observer = new ResizeObserver(() => requestAnimationFrame(redraw));
    if (containerRef.current) observer.observe(containerRef.current);

    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(redraw);
      observer.disconnect();
    };
  }, [drawOverlays]);

  // Fetch VP curve data for all overlays
  // Fetch VP profiles in parallel (these are slow — 1-3s each)
  useEffect(() => {
    let cancelled = false;
    const fetches = VP_OVERLAYS.map(async (overlay) => {
      try {
        const data = await api.getVolumeProfile(overlay.tf);
        if (!cancelled && data.levels?.length) {
          vpDataRef.current.set(overlay.tf, data);
        }
      } catch { /* skip if not available */ }
    });
    Promise.all(fetches).then(() => {
      if (!cancelled) {
        setVpLoaded(n => n + 1);
        drawOverlays();
      }
    });
    return () => { cancelled = true; };
  }, [session, drawOverlays]); // refetch when session updates

  // Redraw when VP data loads
  useEffect(() => { drawOverlays(); }, [vpLoaded, drawOverlays]);

  // Infinite scroll
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const onVisibleRangeChange = () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (!range || fetchingRef.current || exhaustedRef.current) return;
      if (range.from > 10) return;

      const candles = candlesRef.current;
      if (candles.length === 0) return;

      const oldestTs = candles[0].t;
      const endDate = epochToDateStr(oldestTs);

      fetchingRef.current = true;

      api.getCandles('NQ', INTERVAL, endDate, SCROLL_DAYS)
        .then(res => {
          if (!res.candles?.length) { exhaustedRef.current = true; return; }
          const existing = new Set(candlesRef.current.map(c => c.t));
          const newCandles = res.candles.filter(c => !existing.has(c.t));
          if (newCandles.length === 0) { exhaustedRef.current = true; return; }

          candlesRef.current = [...newCandles, ...candlesRef.current];
          priceSeriesRef.current?.setData(candlesRef.current.map(toLine));
          volumeSeriesRef.current?.setData(candlesRef.current.map(toVolume));
        })
        .catch(err => console.warn('Failed to load older candles:', err))
        .finally(() => { fetchingRef.current = false; });
    };

    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRangeChange);
    return () => { chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleRangeChange); };
  }, []);

  // (candle loading is done inside chart init effect above)

  // Live candle updates
  useEffect(() => {
    if (!lastCandle || !priceSeriesRef.current || !volumeSeriesRef.current) return;
    priceSeriesRef.current.update(toLine(lastCandle));
    volumeSeriesRef.current.update(toVolume(lastCandle));

    const existing = candlesRef.current;
    if (existing.length && existing[existing.length - 1].t === lastCandle.t) {
      existing[existing.length - 1] = lastCandle;
    } else {
      existing.push(lastCandle);
    }
  }, [lastCandle]);

  // Anchor series for no-data state
  useEffect(() => {
    if (!noData || !session || !anchorSeriesRef.current) return;
    const s = session.session;
    const anchor = s.vwap ?? session.price_position?.last_price;
    if (!anchor) return;

    const pad = s.ib_high && s.ib_low ? (s.ib_high - s.ib_low) * 1.5 : 200;
    const now = Math.floor(Date.now() / 1000);

    anchorSeriesRef.current.setData([
      { time: (now - 7200) as Time, value: anchor + pad },
      { time: now as Time,          value: anchor - pad },
    ] as LineData<Time>[]);
    chartRef.current?.timeScale().scrollToRealTime();
  }, [noData, session]);

  // Developing VWAP + SD bands from backend tick data (single source of truth)
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    // Remove old VWAP series
    vwapSeriesRefs.current.forEach(s => {
      try { chart.removeSeries(s); } catch {}
    });
    vwapSeriesRefs.current = [];

    // Fetch tick-level VWAP from backend
    let cancelled = false;
    api.getDevelopingVwap('NQ', '1m').then(res => {
      if (cancelled || !res.vwap?.length || !chartRef.current) return;

      const toLD = (arr: typeof res.vwap, key: keyof typeof arr[0]): LineData<Time>[] =>
        arr.map(p => ({ time: p.t as Time, value: p[key] as number }));

      const addLine = (color: string, width: 1 | 2, style: number, data: LineData<Time>[]) => {
        const s = chartRef.current!.addSeries(LineSeries, {
          color,
          lineWidth: width,
          lineStyle: style,
          lastValueVisible: true,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        });
        s.setData(data);
        vwapSeriesRefs.current.push(s);
      };

      addLine('#06B6D4', 2, LineStyle.Solid, toLD(res.vwap, 'vwap'));       // VWAP
      addLine('rgba(6,182,212,0.5)', 1, LineStyle.Solid, toLD(res.vwap, 'sd1_u'));  // +1SD
      addLine('rgba(6,182,212,0.5)', 1, LineStyle.Solid, toLD(res.vwap, 'sd1_l'));  // -1SD
      addLine('rgba(6,182,212,0.25)', 1, LineStyle.Dashed, toLD(res.vwap, 'sd2_u')); // +2SD
      addLine('rgba(6,182,212,0.25)', 1, LineStyle.Dashed, toLD(res.vwap, 'sd2_l')); // -2SD
    }).catch(err => console.warn('Failed to load VWAP:', err));

    return () => { cancelled = true; };
  }, [session]);

  // Static reference lines: IB, PDH/PDL, dPOC (these are flat — correct for structural levels)
  useEffect(() => {
    const series = priceSeriesRef.current;
    if (!series) return;

    Object.values(priceLineRefs.current).forEach(line => {
      try { series.removePriceLine(line); } catch {}
    });
    priceLineRefs.current = {};

    if (!session) return;
    const s = session.session;
    const p = session.profiles;

    const add = (key: string, price: number | undefined | null, color: string, title: string, style = LineStyle.Dashed, width: 1 | 2 = 1) => {
      if (price == null || price === 0) return;
      priceLineRefs.current[key] = series.createPriceLine({ price, color, lineWidth: width, lineStyle: style, axisLabelVisible: true, title });
    };

    // Initial Balance
    add('ibh', s.ib_high, '#F59E0B', 'IBH', LineStyle.Dotted);
    add('ibl', s.ib_low,  '#F59E0B', 'IBL', LineStyle.Dotted);

    // Prior Day High/Low
    add('pdh', s.pdh, '#FB923C', 'PDH', LineStyle.Dashed);
    add('pdl', s.pdl, '#FB923C', 'PDL', LineStyle.Dashed);

    // Daily Volume Profile
    add('d_poc', p?.session?.poc, '#A855F7', 'dPOC', LineStyle.Solid, 2);
    add('d_vah', p?.session?.vah, '#A855F7', 'dVAH', LineStyle.Dashed, 1);
    add('d_val', p?.session?.val, '#A855F7', 'dVAL', LineStyle.Dashed, 1);

    // Weekly Volume Profile
    add('w_poc', p?.weekly?.poc, '#EC4899', 'wPOC', LineStyle.Solid, 2);
    add('w_vah', p?.weekly?.vah, '#EC4899', 'wVAH', LineStyle.Dashed, 1);
    add('w_val', p?.weekly?.val, '#EC4899', 'wVAL', LineStyle.Dashed, 1);

    // Monthly Volume Profile
    add('m_poc', p?.monthly?.poc, '#F59E0B', 'mPOC', LineStyle.Solid, 2);
    add('m_vah', p?.monthly?.vah, '#F59E0B', 'mVAH', LineStyle.Dashed, 1);
    add('m_val', p?.monthly?.val, '#F59E0B', 'mVAL', LineStyle.Dashed, 1);
  }, [session]);

  return (
    <div className="relative w-full h-full">
      <div ref={containerRef} className="w-full h-full" />
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full pointer-events-none"
        style={{ zIndex: 1 }}
      />
      {noData && !lastCandle && !session && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none" style={{ zIndex: 2 }}>
          <span className="text-muted2 text-[10px] font-mono">No candle data available</span>
        </div>
      )}
    </div>
  );
}
