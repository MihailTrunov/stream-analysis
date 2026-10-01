import type { ReplayBar, ReplayObservation } from './replay';

export interface OverlayVisibility { ema: boolean; trend: boolean; swing: boolean; range: boolean; session: boolean }
interface Props { bars: ReplayBar[]; observations: ReplayObservation[]; cursorTime: string | null; overlays: OverlayVisibility }

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

export function CandlestickChart({ bars, observations, cursorTime, overlays }: Props) {
  if (!bars.length) return <p aria-live="polite">No visible candles yet. Step once to process warm-up and reveal the first selected bar.</p>;
  const prices = bars.flatMap((bar) => [Number(bar.high), Number(bar.low)]);
  const low = Math.min(...prices);
  const high = Math.max(...prices);
  const span = high - low || 1;
  const width = 840;
  const height = 300;
  const pad = 24;
  const candleWidth = Math.max(2, Math.min(10, (width - 2 * pad) / bars.length * 0.65));
  const x = (index: number) => pad + (index + .5) * (width - 2 * pad) / bars.length;
  const y = (price: string) => height - pad - ((Number(price) - low) / span) * (height - 2 * pad);
  const barIndex = new Map(bars.map((bar, index) => [bar.timestamp, index]));
  const ema = new Map<string, Array<{ index: number; value: string; period: string }>>();
  const legs: Array<{ index: number; direction: string; id: string }> = [];
  const ranges: Array<{ index: number; regime: string; score: string }> = [];
  const sessions: Array<{ index: number; name: string; local: string }> = [];
  const swings: Array<{ key: string; index: number; price: string; kind: string; detection: string }> = [];
  const structures: Array<{ key: string; index: number; label: string; detection: string }> = [];
  let priorSession = '';
  for (const frame of observations) {
    const index = barIndex.get(frame.timestamp);
    if (index === undefined) continue;
    for (const [instance, values] of Object.entries(frame.components)) {
      if (values.component_id === 'ema' && values.value_ready === true && values.ema != null) {
        const series = ema.get(instance) ?? [];
        series.push({ index, value: String(values.ema), period: String(values.period) });
        ema.set(instance, series);
      }
      if (values.component_id === 'trend_leg') {
        const leg = object(values.active_leg);
        if (leg) legs.push({ index, direction: String(leg.direction), id: String(leg.leg_index) });
      }
      if (values.component_id === 'range_state' && values.compression_score != null) {
        ranges.push({ index, regime: String(values.compression_regime), score: String(values.compression_score) });
      }
    }
    const session = frame.components.session;
    if (session) {
      const identity = `${session.trading_date}:${session.session_name}`;
      if (identity !== priorSession) {
        sessions.push({ index, name: String(session.session_name), local: String(session.local_timestamp) });
        priorSession = identity;
      }
    }
    for (const event of frame.market_events) {
      if (event.detection_time !== frame.timestamp || event.detection_time > (cursorTime ?? '')) continue;
      const eventIndex = barIndex.get(event.event_time) ?? index;
      if (event.event_type === 'SWING_POINT_CONFIRMED') {
        swings.push({ key: `${index}:${event.ordinal}`, index: eventIndex,
          price: String(event.evidence.event_price), kind: String(event.evidence.swing_type), detection: event.detection_time });
      }
      if (event.event_type === 'SWING_STRUCTURE_CLASSIFIED') {
        structures.push({ key: `${index}:${event.ordinal}`, index: eventIndex,
          label: String(event.evidence.label), detection: event.detection_time });
      }
    }
  }
  return <div className="chart-viewport">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Candlestick chart with ${bars.length} visible bars through ${cursorTime ?? 'no cursor'}`}>
      <line x1={pad} x2={width - pad} y1={height - pad} y2={height - pad} stroke="#8b9aa9" />
      {overlays.trend && legs.map(({ index, direction, id }) =>
        <rect key={`leg:${index}`} data-testid="trend-leg-overlay" data-leg={id} data-direction={direction}
          x={x(index) - (width - 2 * pad) / bars.length / 2} y={pad} width={(width - 2 * pad) / bars.length}
          height={height - 2 * pad} fill={direction === 'UP' ? '#31a88b' : '#d97872'} opacity="0.10" />)}
      {overlays.session && sessions.map(({ index, name, local }) =>
        <g key={`session:${index}`} data-testid="session-marker" data-session={name}>
          <title>{`${name} · ${local}`}</title>
          <line x1={x(index)} x2={x(index)} y1={pad} y2={height - pad} stroke="#8093a5" strokeDasharray="3 4" />
        </g>)}
      {overlays.range && ranges.map(({ index, regime, score }) =>
        <rect key={`range:${index}`} data-testid="range-band" data-regime={regime} data-score={score}
          x={x(index) - candleWidth / 2} y={height - pad + 3} width={candleWidth} height="5"
          fill={regime === 'COMPRESSED' ? '#7867bd' : '#9ca9b5'} />)}
      {bars.map((bar, index) => {
        const rising = Number(bar.close) >= Number(bar.open);
        const color = rising ? '#087f5b' : '#bc3c48';
        const top = Math.min(y(bar.open), y(bar.close));
        const bodyHeight = Math.max(2, Math.abs(y(bar.open) - y(bar.close)));
        return <g key={bar.timestamp} data-testid="candle" data-time={bar.timestamp}>
          <title>{`${bar.timestamp} O ${bar.open} H ${bar.high} L ${bar.low} C ${bar.close}`}</title>
          <line x1={x(index)} x2={x(index)} y1={y(bar.high)} y2={y(bar.low)} stroke={color} strokeWidth="2" />
          <rect x={x(index) - candleWidth / 2} y={top} width={candleWidth} height={bodyHeight} fill={color} />
        </g>;
      })}
      {overlays.ema && [...ema.entries()].map(([instance, series]) =>
        <polyline key={instance} data-testid="ema-overlay" data-instance={instance} data-period={series[0].period}
          points={series.map((point) => `${x(point.index)},${y(point.value)}`).join(' ')}
          fill="none" stroke="#3269b5" strokeWidth="1.5" />)}
      {overlays.swing && swings.map((marker) =>
        <g key={marker.key} data-testid="swing-marker" data-kind={marker.kind} data-detection-time={marker.detection}>
          <title>{`${marker.kind} confirmed ${marker.detection}`}</title>
          <circle cx={x(marker.index)} cy={y(marker.price)} r="5" fill="#9f6a10" />
        </g>)}
      {overlays.swing && structures.map((marker) =>
        <text key={marker.key} data-testid="structure-marker" data-detection-time={marker.detection}
          x={x(marker.index)} y={pad - 5} textAnchor="middle" fill="#64539a" fontSize="10">{marker.label}</text>)}
    </svg>
    <p>Current visible bar: <time>{bars.at(-1)?.timestamp}</time> · {bars.length} candles in viewport</p>
  </div>;
}
