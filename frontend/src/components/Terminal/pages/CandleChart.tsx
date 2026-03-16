import { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, HistogramSeries, CrosshairMode, LineStyle, type IChartApi, type ISeriesApi } from 'lightweight-charts';
import type { CandleData } from '@/types/market';
import type { LadderLevel } from './TradingIntradayPage';

// ─── Level color/style mapping ───────────────────────────────────────────────

interface SignalLevels {
  entry?: number;
  stop?: number;
  target?: number;
}

const LEVEL_STYLE: Record<string, { color: string; lineStyle: LineStyle; lineWidth: number }> = {
  vwap:      { color: '#60A5FA', lineStyle: LineStyle.Solid,  lineWidth: 2 },
  sd:        { color: '#60A5FA', lineStyle: LineStyle.Dashed, lineWidth: 1 },
  poc:       { color: '#FACC15', lineStyle: LineStyle.Solid,  lineWidth: 2 },
  vah:       { color: '#FACC15', lineStyle: LineStyle.Dashed, lineWidth: 1 },
  val:       { color: '#FACC15', lineStyle: LineStyle.Dashed, lineWidth: 1 },
  ib:        { color: '#22D3EE', lineStyle: LineStyle.Dashed, lineWidth: 1 },
  pdh:       { color: '#4ADE80', lineStyle: LineStyle.Dotted, lineWidth: 1 },
  pdl:       { color: '#F87171', lineStyle: LineStyle.Dotted, lineWidth: 1 },
  swing:     { color: '#A78BFA', lineStyle: LineStyle.Dotted, lineWidth: 1 },
  ob:        { color: '#FB923C', lineStyle: LineStyle.Dashed, lineWidth: 1 },
  fvg:       { color: '#FBBF24', lineStyle: LineStyle.Dashed, lineWidth: 1 },
  overnight: { color: '#71717A', lineStyle: LineStyle.Dotted, lineWidth: 1 },
  naked:     { color: '#FB923C', lineStyle: LineStyle.Dotted, lineWidth: 1 },
  session:   { color: '#71717A', lineStyle: LineStyle.Dotted, lineWidth: 1 },
  default:   { color: '#52525B', lineStyle: LineStyle.Dotted, lineWidth: 1 },
};

function getLevelStyle(category: string) {
  return LEVEL_STYLE[category] ?? LEVEL_STYLE.default;
}

// ─── Component ───────────────────────────────────────────────────────────────

export function CandleChart({ candles, levels, signalLevels, lastCandle }: {
  candles: CandleData[];
  levels: LadderLevel[];
  signalLevels: SignalLevels | null;
  lastCandle: CandleData | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);

  // ── Create chart on mount ──
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: '#09090b' },
        textColor: '#a1a1aa',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1c1c22' },
        horzLines: { color: '#1c1c22' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: '#27272a',
      },
      rightPriceScale: {
        borderColor: '#27272a',
      },
      autoSize: true,
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#4CAF50',
      downColor: '#EF5350',
      wickUpColor: '#4CAF50',
      wickDownColor: '#EF5350',
      borderVisible: false,
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    });

    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
    };
  }, []);

  // ── Load candle data ──
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || candles.length === 0) return;

    const candleData = candles.map(c => ({
      time: c.t as any,
      open: c.o,
      high: c.h,
      low: c.l,
      close: c.c,
    }));

    const volumeData = candles.map(c => ({
      time: c.t as any,
      value: c.v,
      color: c.c >= c.o ? 'rgba(76,175,80,0.3)' : 'rgba(239,83,80,0.3)',
    }));

    candleSeriesRef.current.setData(candleData);
    volumeSeriesRef.current.setData(volumeData);
  }, [candles]);

  // ── Real-time candle updates from SSE ──
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current || !lastCandle) return;

    candleSeriesRef.current.update({
      time: lastCandle.t as any,
      open: lastCandle.o,
      high: lastCandle.h,
      low: lastCandle.l,
      close: lastCandle.c,
    });

    volumeSeriesRef.current.update({
      time: lastCandle.t as any,
      value: lastCandle.v,
      color: lastCandle.c >= lastCandle.o ? 'rgba(76,175,80,0.3)' : 'rgba(239,83,80,0.3)',
    });
  }, [lastCandle]);

  // ── Level overlays ──
  useEffect(() => {
    if (!candleSeriesRef.current) return;
    const series = candleSeriesRef.current;
    const lineRefs: any[] = [];

    for (const level of levels) {
      const style = getLevelStyle(level.category);

      if (level.zone && level.priceHigh != null) {
        // Zone levels: two lines (top + bottom)
        lineRefs.push(series.createPriceLine({
          price: level.priceHigh,
          color: style.color,
          lineWidth: style.lineWidth as any,
          lineStyle: style.lineStyle,
          axisLabelVisible: true,
          title: `${level.label} Top`,
        }));
        lineRefs.push(series.createPriceLine({
          price: level.price,
          color: style.color,
          lineWidth: style.lineWidth as any,
          lineStyle: style.lineStyle,
          axisLabelVisible: false,
          title: `${level.label} Bot`,
        }));
      } else {
        lineRefs.push(series.createPriceLine({
          price: level.price,
          color: style.color,
          lineWidth: style.lineWidth as any,
          lineStyle: style.lineStyle,
          axisLabelVisible: true,
          title: level.label,
        }));
      }
    }

    return () => {
      for (const line of lineRefs) {
        try { series.removePriceLine(line); } catch { /* already removed */ }
      }
    };
  }, [levels]);

  // ── Signal E/S/T overlays ──
  useEffect(() => {
    if (!candleSeriesRef.current || !signalLevels) return;
    const series = candleSeriesRef.current;
    const lineRefs: any[] = [];

    if (signalLevels.entry != null) {
      lineRefs.push(series.createPriceLine({
        price: signalLevels.entry,
        color: '#06B6D4',
        lineWidth: 1,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: `E ${signalLevels.entry.toFixed(0)}`,
      }));
    }
    if (signalLevels.stop != null) {
      lineRefs.push(series.createPriceLine({
        price: signalLevels.stop,
        color: '#EF5350',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `S ${signalLevels.stop.toFixed(0)}`,
      }));
    }
    if (signalLevels.target != null) {
      lineRefs.push(series.createPriceLine({
        price: signalLevels.target,
        color: '#4CAF50',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `T ${signalLevels.target.toFixed(0)}`,
      }));
    }

    return () => {
      for (const line of lineRefs) {
        try { series.removePriceLine(line); } catch { /* already removed */ }
      }
    };
  }, [signalLevels]);

  return (
    <div ref={containerRef} className="w-full h-full" />
  );
}
