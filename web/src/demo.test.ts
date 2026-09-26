import { describe, expect, it } from 'vitest';
import { visibleBars, type DemoBar } from './demo';

const bars: DemoBar[] = [
  { timestamp: 'first', open: '1', high: '2', low: '1', close: '2' },
  { timestamp: 'second', open: '2', high: '3', low: '2', close: '3' },
];

describe('installation walkthrough', () => {
  it('reveals bars only through the current cursor', () => {
    expect(visibleBars(bars, 0)).toEqual([bars[0]]);
    expect(visibleBars(bars, 1)).toEqual(bars);
    expect(visibleBars(bars, 99)).toEqual(bars);
  });
});
