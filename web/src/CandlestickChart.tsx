import { useEffect, useMemo, useRef, useState } from 'react';
import {
  CandlestickSeries, CrosshairMode, HistogramSeries, LineSeries,
  TickMarkType, createChart, createSeriesMarkers,
  type IChartApi, type ISeriesApi, type ISeriesMarkersPluginApi,
  type Time,
} from 'lightweight-charts';
import { buildChartModel, type ChartLayers } from './chartModel';
import type { ReplayBar, ReplayDetectorEvent, ReplayObservation } from './replay';

export type OverlayVisibility = ChartLayers;
interface Props {
  bars: ReplayBar[];
  observations: ReplayObservation[];
  events?: ReplayDetectorEvent[];
  selectedEventOrder?: number | null;
  onSelectEvent?: (order: number) => void;
  onSelectBar?: (timestamp: string) => void;
  reviewInterval?: { start: string; end: string } | null;
  cursorTime: string | null;
  focusTime?: string | null;
  overlays: OverlayVisibility;
}

export function CandlestickChart({ bars, observations, events = [], selectedEventOrder = null,
  onSelectEvent, onSelectBar, reviewInterval = null, cursorTime, focusTime = null, overlays }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const ema = useRef(new Map<string, ISeriesApi<'Line'>>());
  const range = useRef<ISeriesApi<'Histogram'> | null>(null);
  const priorCount = useRef(0);
  const priorFocus = useRef<string | null>(null);
  const [chartError, setChartError] = useState<string | null>(null);
  const callbacks = useRef({ onSelectEvent, onSelectBar });
  const timestamps = useRef(new Map<number, string>());
  callbacks.current = { onSelectEvent, onSelectBar };

  const result = useMemo(() => {
    try {
      return { model: buildChartModel(
        bars, observations, events, cursorTime, overlays, selectedEventOrder, reviewInterval,
      ), error: null };
    } catch (reason) {
      return { model: null, error: reason instanceof Error ? reason.message : String(reason) };
    }
  }, [bars, observations, events, cursorTime, overlays, selectedEventOrder, reviewInterval]);

  useEffect(() => {
    if (!container.current) return;
    try {
      const instance = createChart(container.current, {
        autoSize: true,
        height: 520,
        layout: {
          background: { color: '#fbfcfd' },
          textColor: '#344457',
          fontFamily: 'system-ui, sans-serif',
        },
        grid: {
          vertLines: { color: '#edf0f4' },
          horzLines: { color: '#e7ebef' },
        },
        rightPriceScale: { visible: true, borderColor: '#a5b2bf' },
        timeScale: {
          visible: true, timeVisible: true, secondsVisible: false,
          borderColor: '#a5b2bf', barSpacing: 8, rightOffset: 4,
          tickMarkFormatter: (value: Time, kind: TickMarkType) => {
            const utc = new Date(Number(value) * 1000).toISOString();
            return kind <= TickMarkType.DayOfMonth ? utc.slice(0, 10) : utc.slice(11, 16);
          },
        },
        crosshair: { mode: CrosshairMode.Normal },
        localization: {
          timeFormatter: (value: Time) => new Date(Number(value) * 1000)
            .toISOString().slice(0, 16).replace('T', ' ') + ' UTC',
        },
      });
      const series = instance.addSeries(CandlestickSeries, {
        upColor: '#087f5b', downColor: '#bc3c48',
        wickUpColor: '#087f5b', wickDownColor: '#bc3c48',
        borderVisible: false, priceFormat: { type: 'price', precision: 1, minMove: 0.1 },
      });
      const markerPlugin = createSeriesMarkers(series, []);
      instance.subscribeClick((point) => {
        const markerId = point.hoveredInfo?.objectId;
        if (typeof markerId === 'string' && markerId.startsWith('event:')) {
          callbacks.current.onSelectEvent?.(Number(markerId.slice(6)));
          return;
        }
        if (typeof point.time === 'number') {
          const timestamp = timestamps.current.get(point.time);
          if (timestamp) callbacks.current.onSelectBar?.(timestamp);
        }
      });
      chart.current = instance;
      candles.current = series;
      markers.current = markerPlugin;
      return () => {
        markerPlugin.detach();
        instance.remove();
        chart.current = null;
        candles.current = null;
        markers.current = null;
        ema.current.clear();
        range.current = null;
        priorCount.current = 0;
      };
    } catch (reason) {
      setChartError(reason instanceof Error ? reason.message : String(reason));
      return;
    }
  }, []);

  useEffect(() => {
    const instance = chart.current;
    const series = candles.current;
    const model = result.model;
    if (!instance || !series || !model) {
      series?.setData([]);
      return;
    }
    try {
      timestamps.current = model.timestamps;
      series.setData(model.candles);
      markers.current?.setMarkers(model.markers);
      for (const [id, value] of model.ema) {
        let line = ema.current.get(id);
        if (!line) {
          line = instance.addSeries(LineSeries, {
            color: '#3269b5', lineWidth: 2, title: `EMA ${value.period}`,
            priceLineVisible: false, lastValueVisible: true,
          });
          ema.current.set(id, line);
        }
        line.setData(value.points);
      }
      for (const [id, line] of ema.current) {
        if (!model.ema.has(id)) {
          instance.removeSeries(line);
          ema.current.delete(id);
        }
      }
      if (model.range.length) {
        if (!range.current) {
          range.current = instance.addSeries(HistogramSeries, {
            title: 'Compression score', color: '#7056ad',
            priceFormat: { type: 'price', precision: 0, minMove: 1 },
            priceLineVisible: false,
          }, 1);
          instance.panes()[1]?.setHeight(110);
        }
        range.current.setData(model.range);
      } else if (range.current) {
        instance.removeSeries(range.current);
        range.current = null;
      }
      if (model.candles.length && (priorCount.current === 0
        || model.candles.length < priorCount.current)) {
        instance.timeScale().fitContent();
      }
      priorCount.current = model.candles.length;
      setChartError(null);
    } catch (reason) {
      setChartError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [result]);

  useEffect(() => {
    const instance = chart.current;
    const model = result.model;
    if (!instance || !model?.candles.length || focusTime === priorFocus.current) return;
    if (focusTime) {
      const index = model.candles.findIndex((bar) => model.timestamps.get(bar.time) === focusTime);
      if (index < 0) return;
      instance.timeScale().setVisibleLogicalRange({
        from: Math.max(0, index - 25),
        to: Math.min(model.candles.length - 1, index + 25),
      });
    } else {
      instance.timeScale().scrollToRealTime();
    }
    priorFocus.current = focusTime;
  }, [focusTime, result]);

  return <div className="chart-viewport">
    {(result.error || chartError) && <p role="alert">Chart unavailable: {result.error || chartError}. Replay controls and the event timeline remain available.</p>}
    <div ref={container} className="trading-chart" role="img"
      aria-label={`Interactive UTC candlestick chart with price and time scales; ${result.model?.candles.length ?? 0} visible bars through ${cursorTime ?? 'no cursor'}`} />
    <p className="chart-caption">
      {bars.length
        ? <>Current visible bar: <time>{bars.at(-1)?.timestamp}</time> · {result.model?.candles.length ?? 0} candles loaded. Scroll to zoom; drag to pan; click a candle to select it for a missed-pattern review.</>
        : 'No visible candles yet. Step once to process warm-up and reveal the first selected bar.'}
      {' '}Charting by <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a>.
    </p>
    {bars.length > 0 && onSelectBar && <button type="button"
      onClick={() => onSelectBar(bars.at(-1)!.timestamp)}>
      Select current candle for missed-pattern review
    </button>}
    {reviewInterval && <p aria-live="polite">Selected review interval: {reviewInterval.start} to {reviewInterval.end}</p>}
  </div>;
}
