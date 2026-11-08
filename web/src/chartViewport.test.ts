import { describe, expect, it } from 'vitest';
import { needsEarlierHistory, rebaseViewport } from './chartViewport';
import { historyBufferBars, mergePriceBars, MAX_PRICE_BARS } from './priceBuffer';
import type { ReplayBar } from './replay';

describe('continuous chart geometry', () => {
  it('keeps the same time anchor and visible width after prepending history', () => {
    const result = rebaseViewport([100, 101, 102], [98, 99, 100, 101, 102], { from: -0.5, to: 2.5 });
    expect(result).toEqual({ from: 1.5, to: 4.5 });
    expect(result.to - result.from).toBe(3);
  });
  it('does not reset zoom when a forming candle disappears', () => {
    expect(rebaseViewport([100, 101, 102], [100, 101], { from: 0.2, to: 5.2 }))
      .toEqual({ from: 0.2, to: 5.2 });
  });
  it('compensates for eviction of earlier candles in a rolling window', () => {
    expect(rebaseViewport([100, 101, 102, 103], [101, 102, 103, 104], { from: 1.5, to: 4.5 }))
      .toEqual({ from: 0.5, to: 3.5 });
  });
  it('merges overlapping history without duplicate candles and keeps closed data authoritative', () => {
    const at = (minute: number, close = '101'): ReplayBar => ({ timestamp: new Date(minute * 60_000).toISOString(), open: '100', high: '105', low: '99', close });
    expect(mergePriceBars([at(2), at(3)], [at(1), at(2, '102')]))
      .toEqual([at(1), at(2, '102'), at(3)]);
  });
});


it('bounds retained prices while keeping the side currently being explored', () => {
  const prices = Array.from({ length: MAX_PRICE_BARS + 10 }, (_, index) => ({ timestamp: new Date(index * 60_000).toISOString(), open: '100', high: '102', low: '99', close: '101' }));
  const recent = mergePriceBars([], prices);
  const older = mergePriceBars([], prices, true);
  expect(recent).toHaveLength(MAX_PRICE_BARS);
  expect(recent[0].timestamp).toBe(prices[10].timestamp);
  expect(older).toHaveLength(MAX_PRICE_BARS);
  expect(older[0].timestamp).toBe(prices[0].timestamp);
});

it('sizes higher-timeframe reads for a trading day or a useful initial screen', () => {
  expect(historyBufferBars('1m')).toBe(1440);
  expect(historyBufferBars('5m')).toBe(288);
  expect(historyBufferBars('1h')).toBe(90);
});


it('keeps the visible time interval when new history exceeds cache capacity', () => {
  const prices = Array.from({ length: 7000 }, (_, index) => ({ timestamp: new Date(index * 60_000).toISOString(), open: '100', high: '102', low: '99', close: '101' }));
  const previous = prices.slice(1000);
  const viewport = { from: 5900, to: 5990 };
  const kept = mergePriceBars(previous, prices.slice(0, 1000), true, viewport);
  const rebased = rebaseViewport(previous.map((bar) => Date.parse(bar.timestamp)), kept.map((bar) => Date.parse(bar.timestamp)), viewport);
  expect(kept).toHaveLength(MAX_PRICE_BARS);
  expect(kept[Math.ceil(rebased.from)].timestamp).toBe(previous[5900].timestamp);
  expect(rebased.to).toBeLessThan(MAX_PRICE_BARS);
});

it('fills empty space at capacity without repeatedly requesting an impossible extra buffer', () => {
  expect(needsEarlierHistory({ from: -10, to: 5990 }, 6000)).toBe(true);
  expect(needsEarlierHistory({ from: 0, to: 6000 }, 6000)).toBe(false);
  expect(needsEarlierHistory({ from: 500, to: 5000 }, 6000)).toBe(true);
  expect(needsEarlierHistory({ from: 750, to: 5250 }, 6000)).toBe(false);
});
