export interface LogicalRange { from: number; to: number }

/** Keep a timestamp at the same pixel when prices are prepended or evicted. */
export function rebaseViewport(previous: number[], next: number[], range: LogicalRange): LogicalRange {
  if (!previous.length || !next.length) return range;
  const anchor = Math.max(0, Math.min(previous.length - 1, Math.ceil(range.from)));
  const time = previous[anchor];
  let index = next.findIndex((value) => value >= time);
  if (index < 0) index = next.length - 1;
  const shift = index - anchor;
  return { from: range.from + shift, to: range.to + shift };
}


export function needsEarlierHistory(range: LogicalRange, capacity: number): boolean {
  const span = range.to - range.from;
  const buffer = Math.max(0, Math.min(Math.max(15, span * 0.15), (capacity - span) / 2 - 1));
  return range.from < buffer;
}
