export interface DemoBar {
  timestamp: string;
  open: string;
  high: string;
  low: string;
  close: string;
}

export function visibleBars(bars: DemoBar[], cursor: number): DemoBar[] {
  return bars.slice(0, Math.max(0, Math.min(cursor + 1, bars.length)));
}
