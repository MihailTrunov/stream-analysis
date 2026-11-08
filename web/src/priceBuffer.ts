import { useRef } from 'react';
import { rebaseViewport, type LogicalRange } from './chartViewport';
import type { ReplayBar, Timeframe } from './replay';

export const INITIAL_PRICE_BARS = 1440;
export const MAX_PRICE_BARS = 6000;
const minutes: Record<Timeframe, number> = { '1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440 };
export function historyBufferBars(timeframe: Timeframe): number {
  return Math.max(90, Math.ceil(INITIAL_PRICE_BARS / minutes[timeframe]));
}

/** Overlap is expected when reading adjacent immutable price pages. */
export function mergePriceBars(previous: ReplayBar[], next: ReplayBar[], older = false, viewport?: LogicalRange): ReplayBar[] {
  const prices = new Map(previous.map((bar) => [bar.timestamp, bar]));
  for (const bar of next) {
    const old = prices.get(bar.timestamp);
    if (!old || old.open !== bar.open || old.high !== bar.high || old.low !== bar.low
      || old.close !== bar.close || old.available_at !== bar.available_at || old.is_complete !== bar.is_complete) prices.set(bar.timestamp, bar);
  }
  const ordered = [...prices.values()].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  let bounded: ReplayBar[];
  if (ordered.length > MAX_PRICE_BARS && previous.length && viewport) {
    const rebased = rebaseViewport(previous.map((bar) => Date.parse(bar.timestamp)), ordered.map((bar) => Date.parse(bar.timestamp)), viewport);
    const spare = Math.max(0, MAX_PRICE_BARS - (rebased.to - rebased.from));
    const offset = Math.max(0, Math.min(ordered.length - MAX_PRICE_BARS, Math.floor(rebased.from - spare / 2)));
    bounded = ordered.slice(offset, offset + MAX_PRICE_BARS);
  } else bounded = older ? ordered.slice(0, MAX_PRICE_BARS) : ordered.slice(-MAX_PRICE_BARS);
  return bounded.length === previous.length && bounded.every((bar, index) => bar === previous[index]) ? previous : bounded;
}

export function usePriceBuffer(key: string, incoming: ReplayBar[], older = false, viewport?: (LogicalRange & { origin: string }) | null): ReplayBar[] {
  const cache = useRef<{ key: string; input: ReplayBar[]; bars: ReplayBar[] } | null>(null);
  if (cache.current?.key !== key) cache.current = { key, input: incoming, bars: incoming };
  else if (cache.current.input !== incoming) {
    cache.current = { key, input: incoming, bars: mergePriceBars(cache.current.bars, incoming, older, viewport?.origin === cache.current.bars[0]?.timestamp ? viewport : undefined) };
  }
  return cache.current.bars;
}
