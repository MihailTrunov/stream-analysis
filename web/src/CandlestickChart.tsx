import type { ReplayBar } from './replay';

interface Props { bars: ReplayBar[]; cursorTime: string | null }

export function CandlestickChart({ bars, cursorTime }: Props) {
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
  return <div className="chart-viewport">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Candlestick chart with ${bars.length} visible bars through ${cursorTime ?? 'no cursor'}`}>
      <line x1={pad} x2={width - pad} y1={height - pad} y2={height - pad} stroke="#8b9aa9" />
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
    </svg>
    <p>Current visible bar: <time>{bars.at(-1)?.timestamp}</time> · {bars.length} candles in viewport</p>
  </div>;
}
