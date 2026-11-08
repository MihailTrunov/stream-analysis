import { useCallback, useEffect, useState } from 'react';
import { historyBufferBars, mergePriceBars } from './priceBuffer';
import { apiJson, type ReplayBar, type Timeframe, type ReplaySource } from './replay';

// Published revisions are immutable. Bound both candle count and cached windows.
const emptyHistory: ReplayBar[] = [];
const windows = new Map<string, Promise<ReplayBar[]>>();
export function readHistoryWindow(source: ReplaySource, end: string, timeframe: Timeframe = '1m', count = historyBufferBars(timeframe)): Promise<ReplayBar[]> {
  const params = new URLSearchParams({ instrument_id: source.instrument_id,
    start: source.range_start, end, limit: '500', timeframe });
  const key = `${count}:` + (timeframe === '1m' ? `/replay/sources/${encodeURIComponent(source.dataset_revision_id)}/bars?${params}` : `/analysis/history/${encodeURIComponent(source.dataset_revision_id)}?${params}`);
  const cached = windows.get(key);
  if (cached) { windows.delete(key); windows.set(key, cached); return cached; }
  const request = (async () => {
    let prices: ReplayBar[] = [];
    let before = end;
    while (prices.length < count && Date.parse(before) > Date.parse(source.range_start)) {
      const query = new URLSearchParams(params);
      query.set('end', before); query.set('limit', String(Math.min(500, count - prices.length)));
      const path = timeframe === '1m' ? `/replay/sources/${encodeURIComponent(source.dataset_revision_id)}/bars` : `/analysis/history/${encodeURIComponent(source.dataset_revision_id)}`;
      const result = await apiJson<{ bars: ReplayBar[] }>(`${path}?${query}`);
      const first = result.bars[0]?.timestamp;
      if (!first || Date.parse(first) >= Date.parse(before)) break;
      prices = mergePriceBars(result.bars, prices);
      if (result.bars.length < Number(query.get('limit'))) break;
      before = first;
    }
    return prices;
  })().catch((error: unknown) => {
    if (windows.get(key) === request) windows.delete(key);
    throw error;
  });
  windows.set(key, request);
  if (windows.size > 16) windows.delete(windows.keys().next().value!);
  return request;
}
export function useHistoryWindow(active: boolean, source: ReplaySource | undefined, end: string | undefined,
  onError: (message: string) => void, timeframe: Timeframe = '1m') {
  const identity = `${source?.dataset_revision_id}:${timeframe}`;
  const [loaded, setLoaded] = useState<{ identity: string; bars: ReplayBar[] } | null>(null);
  const setHistory = useCallback((bars: ReplayBar[]) => setLoaded({ identity, bars }), [identity]);
  const history = loaded?.identity === identity ? loaded.bars : emptyHistory;
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!active || !source || !end) return;
    let cancelled = false;
    if (Date.parse(end) <= Date.parse(source.range_start)) { setHistory([]); setLoading(false); return; }
    setLoading(true);
    void readHistoryWindow(source, end, timeframe).then((bars) => { if (!cancelled) setHistory(bars); })
      .catch((error: unknown) => {
        if (!cancelled) { setHistory([]); onError(error instanceof Error ? error.message : String(error)); }
      }).finally(() => { if (!cancelled) setLoading(false); });
    // Subscribers cancel independently; one tab cannot abort a shared request.
    return () => { cancelled = true; };
  }, [active, source, end, onError, timeframe, setHistory]);
  return { history, setHistory, loadingHistory: loading };
}
