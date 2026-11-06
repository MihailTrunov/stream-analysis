import { useEffect, useRef, useState } from 'react';
import { apiJson, type AnalysisStream, type ReplaySource, type Timeframe } from './replay';
import { useHistoricalAnalysis } from './useHistoricalAnalysis';

/** Historical requests get their own frozen input; never rewind a connected live runtime. */
export function useLiveHistory(id: string, streams: AnalysisStream[] | null, timeframe: Timeframe,
  start: string, end: string, active: boolean, browsing: boolean, onError: (message: string) => void, externalSource?: ReplaySource) {
  const identity = JSON.stringify([id, streams, start, end]);
  // Freezing a full live window removes its forming candle and restores one
  // older completed candle. Keep the explicit action through that left-edge
  // adjustment; a different cutoff, connection or configuration cancels it.
  const manualScope = JSON.stringify([id, streams, end]);
  const [source, setSource] = useState<{ identity: string; value: ReplaySource } | null>(null);
  const [manual, setManual] = useState<{ identity: string; timeframe: string; pattern: string } | null>(null);
  const [request, setRequest] = useState(0);
  const callbacks = useRef(onError); callbacks.current = onError;
  const automatic = browsing && Boolean(streams?.some((stream) => stream.config.patterns.some((pattern) =>
    pattern.enabled !== false && stream.policies[pattern.pattern_id!] === 'always')));
  const selected = externalSource ?? (source?.identity === identity ? source.value : undefined);
  const history = useHistoricalAnalysis(selected, streams, timeframe, start || null, end || null,
    active && automatic, onError);
  useEffect(() => {
    if (!active || !start || !end || Date.parse(start) >= Date.parse(end)
      || selected || (!automatic && manual?.identity !== manualScope)) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void apiJson<ReplaySource>(`/live/analysis/${id}/history`, { method: 'POST',
        body: JSON.stringify({ start, end }), signal: controller.signal,
      }).then((value) => { if (!controller.signal.aborted) setSource({ identity, value }); })
        .catch((error: unknown) => { if (!controller.signal.aborted) callbacks.current(String(error)); });
    }, 300);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [id, identity, start, end, active, automatic, selected, manual, request, manualScope]);
  const calculateHistory = history.calculate;
  useEffect(() => {
    if (!active || !selected || !manual || manual.identity !== manualScope) return;
    const target = manual;
    setManual(null);
    void calculateHistory(target.timeframe, target.pattern);
  }, [active, selected, manual, manualScope, calculateHistory]);
  return { ...history, calculate: (targetTimeframe: string, pattern: string) => {
    setManual({ identity: manualScope, timeframe: targetTimeframe, pattern }); setRequest((value) => value + 1);
  } };
}
