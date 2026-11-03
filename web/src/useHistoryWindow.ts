import { useEffect, useState } from 'react';
import { apiJson, type ReplayBar, type Timeframe, type ReplaySource } from './replay';

// Published revisions are immutable. Bound both candle count and cached windows.
const windows = new Map<string, Promise<ReplayBar[]>>();
export function readHistoryWindow(source: ReplaySource, end: string, timeframe: Timeframe = '1m'): Promise<ReplayBar[]> {
  const params = new URLSearchParams({ instrument_id: source.instrument_id,
    start: source.range_start, end, limit: '500', timeframe });
  const key = timeframe === '1m' ? `/replay/sources/${encodeURIComponent(source.dataset_revision_id)}/bars?${params}` : `/analysis/history/${encodeURIComponent(source.dataset_revision_id)}?${params}`;
  const cached = windows.get(key);
  if (cached) { windows.delete(key); windows.set(key, cached); return cached; }
  const request = apiJson<{ bars: ReplayBar[] }>(key).then((result) => result.bars).catch((error: unknown) => {
    if (windows.get(key) === request) windows.delete(key);
    throw error;
  });
  windows.set(key, request);
  if (windows.size > 16) windows.delete(windows.keys().next().value!);
  return request;
}
export function useHistoryWindow(active: boolean, source: ReplaySource | undefined, end: string | undefined,
  onError: (message: string) => void, timeframe: Timeframe = '1m') {
  const [history, setHistory] = useState<ReplayBar[]>([]);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!active || !source || !end) return;
    let cancelled = false;
    if (end <= source.range_start) { setHistory([]); setLoading(false); return; }
    setLoading(true);
    void readHistoryWindow(source, end, timeframe).then((bars) => { if (!cancelled) setHistory(bars); })
      .catch((error: unknown) => {
        if (!cancelled) { setHistory([]); onError(error instanceof Error ? error.message : String(error)); }
      }).finally(() => { if (!cancelled) setLoading(false); });
    // Subscribers cancel independently; one tab cannot abort a shared request.
    return () => { cancelled = true; };
  }, [active, source, end, onError, timeframe]);
  return { history, setHistory, loadingHistory: loading };
}
