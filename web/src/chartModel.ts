import type {
  CandlestickData, HistogramData, LineData, SeriesMarker, UTCTimestamp,
} from 'lightweight-charts';
import type { ReplayBar, ReplayDetectorEvent, ReplayObservation } from './replay';

export interface ChartModel {
  candles: CandlestickData<UTCTimestamp>[];
  ema: Map<string, { period: string; points: LineData<UTCTimestamp>[] }>;
  range: HistogramData<UTCTimestamp>[];
  markers: SeriesMarker<UTCTimestamp>[];
  timestamps: Map<number, string>;
}

export interface ChartLayers {
  ema: boolean;
  trend: boolean;
  swing: boolean;
  range: boolean;
  session: boolean;
}

function chartTime(iso: string): UTCTimestamp {
  const milliseconds = Date.parse(iso);
  if (!Number.isFinite(milliseconds) || milliseconds % 60_000 !== 0) {
    throw new Error(`Invalid completed-bar UTC timestamp: ${iso}`);
  }
  return (milliseconds / 1000) as UTCTimestamp;
}

function price(value: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`Invalid canonical chart price: ${value}`);
  }
  return parsed;
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

export function buildChartModel(
  bars: ReplayBar[],
  observations: ReplayObservation[],
  events: ReplayDetectorEvent[],
  cursorTime: string | null,
  layers: ChartLayers,
  selectedEventOrder: number | null,
  reviewInterval: { start: string; end: string } | null,
): ChartModel {
  const model: ChartModel = {
    candles: [], ema: new Map(), range: [], markers: [], timestamps: new Map(),
  };
  if (!cursorTime) return model;
  const visible = bars.filter((bar) => bar.timestamp <= cursorTime);
  let previous = -Infinity;
  for (const bar of visible) {
    const time = chartTime(bar.timestamp);
    if (time <= previous) throw new Error('Chart bars must be strictly ordered');
    previous = time;
    const open = price(bar.open);
    const high = price(bar.high);
    const low = price(bar.low);
    const close = price(bar.close);
    if (low > Math.min(open, close) || high < Math.max(open, close)) {
      throw new Error('Canonical OHLC is inconsistent');
    }
    model.candles.push({ time, open, high, low, close });
    model.timestamps.set(time, bar.timestamp);
    if (reviewInterval && bar.timestamp >= reviewInterval.start
      && bar.timestamp < reviewInterval.end) {
      model.markers.push({
        id: `review:${bar.timestamp}`, time, position: 'belowBar', shape: 'square',
        color: '#bd8420', text: 'Review',
      });
    }
  }
  const present = new Set(model.timestamps.values());
  let priorSession = '';
  const priorLeg = new Map<string, string>();
  for (const frame of observations) {
    if (frame.timestamp > cursorTime || !present.has(frame.timestamp)) continue;
    const time = chartTime(frame.timestamp);
    for (const [instance, values] of Object.entries(frame.components)) {
      if (layers.ema && values.component_id === 'ema'
        && values.value_ready === true && values.ema != null) {
        const series = model.ema.get(instance) ?? { period: String(values.period), points: [] };
        series.points.push({ time, value: price(String(values.ema)) });
        model.ema.set(instance, series);
      }
      if (layers.trend && values.component_id === 'trend_leg') {
        const leg = record(values.active_leg);
        const identity = leg ? String(leg.leg_index) : '';
        if (leg && identity !== priorLeg.get(instance)) {
          const up = leg.direction === 'UP';
          model.markers.push({
            id: `trend:${instance}:${identity}`, time,
            position: up ? 'belowBar' : 'aboveBar',
            shape: up ? 'arrowUp' : 'arrowDown',
            color: up ? '#16806a' : '#b74b53', text: `Leg ${identity}`,
          });
        }
        priorLeg.set(instance, identity);
      }
      if (layers.range && values.component_id === 'range_state'
        && values.compression_score != null) {
        const score = Number(values.compression_score);
        if (Number.isFinite(score) && score >= 0 && score <= 100) {
          model.range.push({
            time, value: score,
            color: values.compression_regime === 'COMPRESSED' ? '#7056ad' : '#a5a6b8',
          });
        }
      }
    }
    if (layers.session) {
      const session = frame.components.session;
      if (session) {
        const identity = `${session.trading_date}:${session.session_name}`;
        if (identity !== priorSession) {
          model.markers.push({
            id: `session:${identity}`, time, position: 'belowBar', shape: 'circle',
            color: '#607d9b', text: String(session.session_name),
          });
          priorSession = identity;
        }
      }
    }
    if (layers.swing) {
      for (const event of frame.market_events) {
        if (event.detection_time !== frame.timestamp || event.detection_time > cursorTime) continue;
        const location = present.has(event.event_time) ? event.event_time : frame.timestamp;
        const markerTime = chartTime(location);
        if (event.event_type === 'SWING_POINT_CONFIRMED') {
          const high = event.evidence.swing_type === 'SWING_HIGH';
          model.markers.push({
            id: `swing:${frame.timestamp}:${event.ordinal}`, time: markerTime,
            position: high ? 'aboveBar' : 'belowBar', shape: 'circle',
            color: '#a56e13', text: high ? 'SH' : 'SL',
          });
        } else if (event.event_type === 'SWING_STRUCTURE_CLASSIFIED') {
          model.markers.push({
            id: `structure:${frame.timestamp}:${event.ordinal}`, time: markerTime,
            position: 'aboveBar', shape: 'square', color: '#64539a',
            text: String(event.evidence.label),
          });
        }
      }
    }
  }
  for (const event of events) {
    if (event.detection_time > cursorTime || !present.has(event.detection_time)) continue;
    const shortState = event.to_state === 'CANDIDATE' ? 'C'
      : event.to_state === 'ACTIVE' ? 'A'
      : event.to_state === 'COMPLETED' ? 'Done'
      : event.to_state === 'INVALIDATED' ? 'X' : event.to_state;
    model.markers.push({
      id: `event:${event.emission_order}`, time: chartTime(event.detection_time),
      position: 'aboveBar', shape: 'circle',
      color: selectedEventOrder === event.emission_order ? '#1b3554'
        : event.to_state === 'INVALIDATED' ? '#bc3c48' : '#7056ad',
      text: shortState,
      size: selectedEventOrder === event.emission_order ? 2 : 1,
    });
  }
  model.markers.sort((a, b) => Number(a.time) - Number(b.time)
    || String(a.id).localeCompare(String(b.id)));
  return model;
}
