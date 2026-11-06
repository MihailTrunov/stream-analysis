import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { containsEnd, hasInitializedResult, coversWindow, type StreamCoverage } from './analysisCoverage';
import { apiJson, type AnalysisStream, type ReplayDetectorEvent, type ReplaySource, type ReplayState, type ReplayViewResponse, type Timeframe } from './replay';

export function useHistoricalAnalysis(source: ReplaySource | undefined, streams: AnalysisStream[] | null,
  timeframe: Timeframe, start: string | null, end: string | null, automatic: boolean, onError: (message: string) => void) {
  const [results, setResults] = useState<Record<string, { events: ReplayDetectorEvent[]; bounds: StreamCoverage; identity: string }>>({});
  const currentResults = useRef(results);
  currentResults.current = results;
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const sessionIds = useRef(new Set<string>());
  const keySessions = useRef(new Map<string, string>());
  const identity = source ? `${source.dataset_revision_id}:${JSON.stringify(streams)}` : '';
  const identityRef = useRef(identity);
  identityRef.current = identity;
  useEffect(() => {
    generation.current += 1; setResults({}); setBusy(false); keySessions.current.clear();
    for (const id of sessionIds.current) void apiJson(`/analysis/${id}/stop`, { method: 'POST' }).catch(() => undefined);
    sessionIds.current.clear();
  }, [identity]);
  const calculate = useCallback(async (targetTimeframe?: string, targetPattern?: string, auto = false) => {
    if (!source || !streams || !start || !end || Date.parse(start) >= Date.parse(end)) return;
    const requestIdentity = identity;
    const selected = streams.map((stream) => ({ ...stream, config: { ...stream.config,
      patterns: stream.config.patterns.map((pattern) => ({ ...pattern,
        enabled: pattern.enabled !== false && (!targetPattern || stream.config.timeframe === targetTimeframe && pattern.pattern_id === targetPattern)
          && (!auto || stream.policies[pattern.pattern_id!] === 'always'),
      })) } }));
    const keys = selected.flatMap((stream) => stream.config.patterns.filter((p) => p.enabled !== false).map((p) => `${stream.config.timeframe}:${p.pattern_id}`));
    if (!keys.length || keys.every((key) => {
      const cached = currentResults.current[key];
      return cached?.identity === requestIdentity && containsEnd(cached.bounds, end);
    })) return;
    const requestGeneration = ++generation.current;
    setBusy(true);
    let id: string | undefined;
    try {
      const state = await apiJson<ReplayState>('/analysis', { method: 'POST', body: JSON.stringify({
        dataset_revision_id: source.dataset_revision_id, instrument_id: source.instrument_id,
        selected_start: start < source.range_start ? source.range_start : start,
        selected_end: end > source.range_end ? source.range_end : end,
        chart_timeframe: timeframe, streams: selected, mode: 'history', automatic: auto,
      }) });
      id = state.run_id;
      const params = new URLSearchParams({ start, end, timeframe, limit: '500' });
      const result = await apiJson<ReplayViewResponse>(`/analysis/${id}/view?${params}`);
      if (requestIdentity !== identityRef.current || requestGeneration !== generation.current) {
        await apiJson(`/analysis/${id}/stop`, { method: 'POST' }); return;
      }
      sessionIds.current.add(id);
      for (const key of keys) keySessions.current.set(key, id);
      for (const previous of sessionIds.current) {
        if (![...keySessions.current.values()].includes(previous)) {
          sessionIds.current.delete(previous);
          void apiJson(`/analysis/${previous}/stop`, { method: 'POST' }).catch(() => undefined);
        }
      }
      const entries = keys.flatMap((key) => {
        const bounds = result.stream_coverage?.find((item) => `${item.timeframe}:${item.pattern_id}` === key);
        return bounds ? [[key, { events: result.events.filter((event) => `${event.timeframe}:${event.pattern_id}` === key), bounds, identity: requestIdentity }]] : [];
      });
      setResults((current) => ({ ...current, ...Object.fromEntries(entries) }));
    } catch (error) { onError(String(error)); if (id) void apiJson(`/analysis/${id}/stop`, { method: 'POST' }).catch(() => undefined); }
    finally { if (requestGeneration === generation.current) setBusy(false); }
  }, [source, streams, start, end, timeframe, identity, onError]);
  useEffect(() => {
    if (!automatic) return;
    const timer = setTimeout(() => void calculate(undefined, undefined, true), 300);
    return () => clearTimeout(timer);
  }, [automatic, calculate]);
  useEffect(() => {
    const ids = sessionIds.current;
    return () => { generation.current += 1; for (const id of ids) void apiJson(`/analysis/${id}/stop`, { method: 'POST' }).catch(() => undefined); };
  }, []);
  const projection = useMemo(() => {
  const covered = Object.entries(results).filter(([, value]) => value.identity === identity && end && containsEnd(value.bounds, end));
  const expected = streams?.flatMap((stream) => stream.config.patterns.filter((pattern) => pattern.enabled !== false)
    .map((pattern) => `${stream.config.timeframe}:${pattern.pattern_id}`)) ?? [];
  const events = covered.flatMap(([, value]) => end && hasInitializedResult(value.bounds, end) ? value.events : []).sort((a, b) => Date.parse(a.available_at ?? a.detection_time) - Date.parse(b.available_at ?? b.detection_time))
    .map((event, emission_order) => ({ ...event, emission_order }));
  const full = covered.filter(([, value]) => start && end && coversWindow(value.bounds, start, end));
  const loaded = expected.length > 0 && expected.every((key) => full.some(([coveredKey]) => key === coveredKey));
  const partial = covered.some(([, value]) => end && hasInitializedResult(value.bounds, end));
  const status = loaded ? 'calculated' : partial ? 'partial' : covered.length ? 'initialization_unavailable' : 'not_requested';
    return { events, loaded, status, calculatedKeys: covered.filter(([, value]) => end && hasInitializedResult(value.bounds, end)).map(([key]) => key), coverage: `${full.length} of ${expected.length} detector streams calculated` };
  }, [results, identity, start, end, streams]);
  return { ...projection, busy, calculate };
}
