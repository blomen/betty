import { useRef, useEffect, useState, useCallback } from 'react';
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type HistogramData,
  type LineData,
  type Time,
  ColorType,
} from 'lightweight-charts';
import { api } from '@/services/api';
import type { CandleData, ExpandedSession } from '@/types/market';

const INTERVAL = '1m';
const INITIAL_DAYS = 1;
const SCROLL_DAYS = 1;

// VP overlay config: which timeframes to show, with colors
// Only daily VP overlay for now — verified
const VP_OVERLAYS = [
  { tf: 'session', color: [168, 85, 247],  label: 'D' },   // purple
] as const;

type VPData = { levels: Array<{ price: number; volume: number }>; poc: number; vah: number; val: number };

interface Props {
  lastCandle: CandleData | null;
  session: ExpandedSession | null;
}

function toCandlestick(c: CandleData): CandlestickData<Time> {
  return { time: c.t as Time, open: c.o, high: c.h, low: c.l, close: c.c };
}

function toVolume(c: CandleData): HistogramData<Time> {
  const color = c.c >= c.o ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)';
  return { time: c.t as Time, value: c.v, color };
}

function epochToDateStr(epoch: number): string {
  return new Date(epoch * 1000).toISOString().slice(0, 10);
}

export function CandleChart({ lastCandle, session }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const [noData, setNoData] = useState(false);
  const priceLineRefs = useRef<Record<string, any>>({});
  const anchorSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  // Scroll-back state
  const candlesRef = useRef<CandleData[]>([]);
  const fetchingRef = useRef(false);
  const exhaustedRef = useRef(false);

  // VP overlay data
  const vpDataRef = useRef<Map<string, VPData>>(new Map());
  const [vpLoaded, setVpLoaded] = useState(0); // trigger redraws

  // Draw VP histograms on canvas
  const drawVPOverlay = useCallback(() => {
    const canvas = canvasRef.current;
    const chart = chartRef.current;
    const series = candleSeriesRef.current;
    if (!canvas || !chart || !series) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, rect.width, rect.height);

    const vpMap = vpDataRef.current;
    if (vpMap.size === 0) return;

    // Draw session VP only as a single histogram on the right edge
    const vp = vpMap.get('session');
    if (!vp || !vp.levels.length) return;

    const maxVol = Math.max(...vp.levels.map(l => l.volume));
    if (maxVol === 0) return;

    const maxBarWidth = 80;
    const priceScaleWidth = 65;
    const xRight = rect.width - priceScaleWidth;

    for (const level of vp.levels) {
      const y = series.priceToCoordinate(level.price);
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

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderUpColor: '#26a69a',
      borderDownColor: '#ef5350',
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
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
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    anchorSeriesRef.current = anchorSeries;

    // Load candles immediately after chart is created
    (async () => {
      try {
        const res = await api.getCandles('NQ', INTERVAL, undefined, INITIAL_DAYS);
        if (res.candles?.length) {
          candlesRef.current = res.candles;
          candleSeries.setData(res.candles.map(toCandlestick));
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
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      anchorSeriesRef.current = null;
    };
  }, []);

  // Subscribe VP overlay redraws to chart events (separate from init)
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const redraw = () => drawVPOverlay();
    chart.timeScale().subscribeVisibleLogicalRangeChange(redraw);

    const observer = new ResizeObserver(() => requestAnimationFrame(redraw));
    if (containerRef.current) observer.observe(containerRef.current);

    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(redraw);
      observer.disconnect();
    };
  }, [drawVPOverlay]);

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
        drawVPOverlay();
      }
    });
    return () => { cancelled = true; };
  }, [session, drawVPOverlay]); // refetch when session updates

  // Redraw when VP data loads
  useEffect(() => { drawVPOverlay(); }, [vpLoaded, drawVPOverlay]);

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
          candleSeriesRef.current?.setData(candlesRef.current.map(toCandlestick));
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
    if (!lastCandle || !candleSeriesRef.current || !volumeSeriesRef.current) return;
    candleSeriesRef.current.update(toCandlestick(lastCandle));
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

  // Reference lines: VWAP, IB, and all volume profile POC/VAH/VAL
  useEffect(() => {
    const series = candleSeriesRef.current;
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

    // --- Verified levels only ---

    // VWAP + SD bands
    add('vwap', s.vwap, '#06B6D4', 'VWAP', LineStyle.Solid, 2);
    add('vwap_1sd_u', s.vwap_1sd_upper, '#06B6D4', '+1SD', LineStyle.Dashed, 1);
    add('vwap_1sd_l', s.vwap_1sd_lower, '#06B6D4', '-1SD', LineStyle.Dashed, 1);
    add('vwap_2sd_u', s.vwap_2sd_upper, '#0891B2', '+2SD', LineStyle.Dotted, 1);
    add('vwap_2sd_l', s.vwap_2sd_lower, '#0891B2', '-2SD', LineStyle.Dotted, 1);

    // Initial Balance
    add('ibh', s.ib_high, '#F59E0B', 'IBH', LineStyle.Dotted);
    add('ibl', s.ib_low,  '#F59E0B', 'IBL', LineStyle.Dotted);

    // Prior Day High/Low
    add('pdh', s.pdh, '#FB923C', 'PDH', LineStyle.Dashed);
    add('pdl', s.pdl, '#FB923C', 'PDL', LineStyle.Dashed);

    // Daily Volume Profile POC only
    add('d_poc', p?.session?.poc, '#A855F7', 'dPOC', LineStyle.Solid, 1);
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
