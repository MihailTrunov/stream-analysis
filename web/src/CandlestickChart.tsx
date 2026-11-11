import { useEffect, useMemo, useRef, useState } from 'react';
import {
  CandlestickSeries, CrosshairMode, HistogramSeries, LineSeries,
  TickMarkType, createChart, createSeriesMarkers,
  type IChartApi, type ISeriesApi, type ISeriesMarkersPluginApi,
  type Time,
} from 'lightweight-charts';
import { MAX_PRICE_BARS } from './priceBuffer';
import { needsEarlierHistory, rebaseViewport, type LogicalRange } from './chartViewport';
import { buildChartModel, type ChartLayers, type ChartModel } from './chartModel';
import type { ReplayBar, ReplayDetectorEvent, ReplayObservation } from './replay';
import { dateTimeInput, displayTime, type DisplayTimezone } from './time';

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
  timezone?: DisplayTimezone;
  resetKey?: string;
  initialRange?: { from: number; to: number } | null;
  onRangeChange?: (range: { from: number; to: number }) => void;
  focusVersion?: number;
  onReturnLatest?: () => void;
  onLoadEarlier?: (automatic?: boolean) => void;
  earlierDisabled?: boolean;
  canReturnLatest?: boolean;
  historyStatus?: string | null;
  autoLoadEarlier?: boolean;
  onNavigate?: () => void;
}

export function CandlestickChart({ bars, observations, events = [], selectedEventOrder = null,
  onSelectEvent, onSelectBar, reviewInterval = null, cursorTime, focusTime = null, overlays,
  timezone = 'Europe/London', resetKey = '', initialRange = null, onRangeChange,
  focusVersion = 0, onReturnLatest, onLoadEarlier, earlierDisabled = false, canReturnLatest = false, historyStatus, autoLoadEarlier = true, onNavigate }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markers = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const ema = useRef(new Map<string, ISeriesApi<'Line'>>());
  const range = useRef<ISeriesApi<'Histogram'> | null>(null);
  const priorCandles = useRef<ChartModel['candles']>([]);
  const priorCount = useRef(0);
  const priorFocus = useRef<string | null>(null);
  const priorFocusVersion = useRef(0);
  const [chartError, setChartError] = useState<string | null>(null);
  const callbacks = useRef({ onSelectEvent, onSelectBar, onRangeChange, onLoadEarlier, earlierDisabled, autoLoadEarlier });
  const fitRequested = useRef(false);
  const geometry = useRef<{ times: number[]; range: LogicalRange } | null>(null);
  const fillFrame = useRef<number | null>(null);
  const initialViewport = useRef(initialRange);
  initialViewport.current = initialRange;
  const following = useRef(initialRange === null);
  const userNavigation = useRef(false);
  const [isFollowing, setIsFollowing] = useState(initialRange === null);
  const requestedFocus = useRef({ focusTime, focusVersion });
  requestedFocus.current = { focusTime, focusVersion };
  const timestamps = useRef(new Map<number, string>());
  callbacks.current = { onSelectEvent, onSelectBar, onRangeChange, onLoadEarlier, earlierDisabled, autoLoadEarlier };

  function fillHistory() {
    if (fillFrame.current !== null) cancelAnimationFrame(fillFrame.current);
    fillFrame.current = requestAnimationFrame(() => {
      fillFrame.current = null;
      const value = chart.current?.timeScale().getVisibleLogicalRange();
      const current = callbacks.current;
      if (value && userNavigation.current && priorCount.current > 0 && !current.earlierDisabled && current.autoLoadEarlier
        && needsEarlierHistory({ from: Number(value.from), to: Number(value.to) }, MAX_PRICE_BARS)) current.onLoadEarlier?.(true);
    });
  }

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
          borderColor: '#a5b2bf', barSpacing: 8, rightOffset: 4, shiftVisibleRangeOnNewBar: false,
          tickMarkFormatter: (value: Time, kind: TickMarkType) => {
            const local = dateTimeInput(new Date(Number(value) * 1000).toISOString(), timezone);
            return kind <= TickMarkType.DayOfMonth ? local.slice(0, 10) : local.slice(11, 16);
          },
        },
        crosshair: { mode: CrosshairMode.Normal },
        localization: {
          timeFormatter: (value: Time) => displayTime(new Date(Number(value) * 1000).toISOString(), timezone),
        },
      });
      const series = instance.addSeries(CandlestickSeries, {
        upColor: '#087f5b', downColor: '#bc3c48',
        wickUpColor: '#087f5b', wickDownColor: '#bc3c48',
        borderVisible: false, priceFormat: { type: 'price', precision: 1, minMove: 0.1 },
      });
      const markerPlugin = createSeriesMarkers(series, []);
      geometry.current = null; priorCandles.current = [];
      let priorWidth = 0;
      const resize = new ResizeObserver(([entry]) => {
        if (priorWidth && Math.abs(entry.contentRect.width - priorWidth) > 1) userNavigation.current = true;
        priorWidth = entry.contentRect.width;
        fillHistory();
      });
      resize.observe(container.current);
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
      priorFocus.current = null;
      priorFocusVersion.current = requestedFocus.current.focusTime ? -1 : requestedFocus.current.focusVersion;
      const emaSeries = ema.current;
      instance.timeScale().subscribeVisibleLogicalRangeChange((value) => {
        if (value) {
          if (fitRequested.current && Number(value.from) <= 1 && Number(value.to) >= priorCount.current - 2) fitRequested.current = false;
          callbacks.current.onRangeChange?.({ from: Number(value.from), to: Number(value.to) });
          fillHistory();
        }
      });
      return () => {
        resize.disconnect();
        if (fillFrame.current !== null) cancelAnimationFrame(fillFrame.current);
        markerPlugin.detach();
        instance.remove();
        chart.current = null;
        candles.current = null;
        markers.current = null;
        emaSeries.clear();
        range.current = null;
        priorCount.current = 0;
        priorFocus.current = null;
      };
    } catch (reason) {
      setChartError(reason instanceof Error ? reason.message : String(reason));
      return;
    }
  }, [timezone, resetKey]);

  useEffect(() => {
    const instance = chart.current;
    const series = candles.current;
    const model = result.model;
    if (!instance || !series || !model) {
      series?.setData([]);
      return;
    }
    try {
      const before = instance.timeScale().getVisibleLogicalRange();
      if (before && geometry.current?.times.length) geometry.current.range = { from: Number(before.from), to: Number(before.to) };
      const saved = geometry.current;
      timestamps.current = model.timestamps;
      const old = priorCandles.current;
      const equal = (bar: ChartModel['candles'][number], previous: ChartModel['candles'][number]) =>
        bar.time === previous.time && bar.open === previous.open && bar.high === previous.high
        && bar.low === previous.low && bar.close === previous.close && bar.color === previous.color
        && bar.borderColor === previous.borderColor && bar.wickColor === previous.wickColor;
      const samePrefix = old.length === model.candles.length
        && model.candles.slice(0, -1).every((bar, index) => equal(bar, old[index]));
      const priceChanged = !samePrefix || Boolean(model.candles.length && !equal(model.candles.at(-1)!, old.at(-1)!));
      if (priceChanged) {
        if (samePrefix && model.candles.length && old.at(-1)?.time === model.candles.at(-1)?.time) series.update(model.candles.at(-1)!);
        else series.setData(model.candles);
      }
      priorCandles.current = model.candles;
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
      if (model.candles.length && (priceChanged || !saved || fitRequested.current)) {
        const times = model.candles.map((bar) => Number(bar.time));
        if (fitRequested.current) {
          instance.timeScale().fitContent(); fitRequested.current = false;
        } else if (!saved) {
          if (initialViewport.current) { following.current = false; setIsFollowing(false); }
          instance.timeScale().setVisibleLogicalRange(initialViewport.current ?? {
            from: model.candles.length - 90, to: model.candles.length + 4,
          });
        } else if (following.current) instance.timeScale().scrollToPosition(4, false);
        else instance.timeScale().setVisibleLogicalRange(rebaseViewport(saved.times, times, saved.range));
        const visible = instance.timeScale().getVisibleLogicalRange();
        if (visible) geometry.current = { times, range: { from: Number(visible.from), to: Number(visible.to) } };
      }
      priorCount.current = model.candles.length;
      fillHistory();
      setChartError(null);
    } catch (reason) {
      setChartError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [result, timezone, resetKey]);

  useEffect(() => {
    const instance = chart.current;
    const model = result.model;
    if (!instance || !model?.candles.length || (focusTime === priorFocus.current
      && focusVersion === priorFocusVersion.current)) return;
    // Version zero describes restored focus metadata, not a new navigation.
    // Keep a matching restored viewport until an explicit focus action occurs.
    if (focusTime && focusVersion === 0 && initialViewport.current) {
      priorFocus.current = focusTime; priorFocusVersion.current = focusVersion;
      return;
    }
    if (focusTime) {
      userNavigation.current = false;
      following.current = false;
      setIsFollowing(false);
      const index = model.candles.findIndex((bar) => model.timestamps.get(bar.time) === focusTime);
      if (index < 0) return;
      const visible = instance.timeScale().getVisibleLogicalRange();
      const span = visible ? Number(visible.to) - Number(visible.from) : 94;
      instance.timeScale().setVisibleLogicalRange({
        from: index - span / 2,
        to: index + span / 2,
      });
    } else {
      following.current = true;
      setIsFollowing(true);
      instance.timeScale().scrollToRealTime();
    }
    priorFocus.current = focusTime;
    priorFocusVersion.current = focusVersion;
  }, [focusTime, focusVersion, result, timezone, resetKey]);

  useEffect(() => { if (!earlierDisabled && autoLoadEarlier) fillHistory(); }, [earlierDisabled, autoLoadEarlier]);

  function navigate() { fitRequested.current = false; userNavigation.current = true; following.current = false; setIsFollowing(false); onNavigate?.(); }
  function zoom(factor: number) {
    const scale = chart.current?.timeScale();
    if (!scale) return;
    navigate();
    scale.applyOptions({ barSpacing: Math.max(Math.max(0.25, (container.current?.clientWidth ?? 1000) / 4500), Math.min(50, scale.options().barSpacing * factor)) });
  }

  return <div className="chart-viewport">
    {(result.error || chartError) && <p role="alert">Chart unavailable: {result.error || chartError}. Replay controls and the event timeline remain available.</p>}
    <div ref={container} className="trading-chart" role="img" onPointerDown={navigate} onWheel={navigate}
      aria-label={`Interactive ${timezone === 'Europe/London' ? 'UK time' : 'UTC'} candlestick chart with price and time scales; ${result.model?.candles.length ?? 0} visible bars through ${cursorTime ?? 'no cursor'}`} />
    <div className="chart-navigation" aria-label="Chart navigation">
      <button type="button" aria-label="Zoom in" onClick={() => zoom(1.3)}>＋</button>
      <button type="button" aria-label="Zoom out" onClick={() => zoom(1 / 1.3)}>−</button>
      <button type="button" onClick={() => { navigate(); fitRequested.current = true; userNavigation.current = false; chart.current?.timeScale().fitContent(); }}>Fit candles</button>
      {onLoadEarlier && <button type="button" onClick={() => { navigate(); onLoadEarlier(); }} disabled={earlierDisabled}>Load earlier candles</button>}
      <button type="button" onClick={() => {
        following.current = true; setIsFollowing(true); chart.current?.timeScale().scrollToRealTime();
        userNavigation.current = false;
        onReturnLatest?.();
      }} disabled={isFollowing && !focusTime && !canReturnLatest}>Return to latest</button>
    </div>
    {historyStatus && <p role="status" className="chart-history-status">{historyStatus}</p>}
    <p className="chart-caption">
      {bars.length
        ? <><time>{displayTime(bars.at(-1)!.timestamp, timezone)}</time> · {result.model?.candles.length ?? 0} candles · Drag to pan · Scroll to zoom</>
        : 'Waiting for candle data.'}
      {' '}Charting by <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a>.
    </p>
    {bars.length > 0 && onSelectBar && <button type="button"
      onClick={() => onSelectBar(bars.at(-1)!.timestamp)}>
      Select current candle for missed-pattern review
    </button>}
    {reviewInterval && <p aria-live="polite">Selected review interval: {reviewInterval.start} to {reviewInterval.end}</p>}
  </div>;
}
