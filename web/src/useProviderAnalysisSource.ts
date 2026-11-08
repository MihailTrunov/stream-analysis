import { useEffect, useRef, useState } from 'react';
import { apiJson, type ReplaySource } from './replay';

const requests = new Map<string, Promise<ReplaySource>>();
export function providerAnalysisSource(instrument: string, end?: string, live = false): Promise<ReplaySource> {
  const key = `${instrument}:${end ?? (live ? Math.floor(Date.now() / 60_000) : 'latest')}:${live}`;
  const cached = requests.get(key);
  if (cached) return cached;
  const request = apiJson<ReplaySource>(`/live/prices/${instrument}/source`, {
    method: 'POST', body: JSON.stringify({ end, live }),
  }).then(source => {
    if (source.instrument_id !== instrument || !source.dataset_revision_id
      || !source.calendar_version.startsWith('provider-observed-v1:')) throw new Error('Invalid provider detector input.');
    return source;
  }).catch(error => { requests.delete(key); throw error; });
  requests.set(key, request);
  if (requests.size > 16) requests.delete(requests.keys().next().value!);
  return request;
}

export function useProviderAnalysisSource(instrument: string, end: string, enabled: boolean,
  onSource: (source: ReplaySource) => void, onError: (message: string) => void) {
  const [loaded, setLoaded] = useState<{ key: string; source: ReplaySource } | null>(null);
  const [busy, setBusy] = useState(false);
  const callbacks = useRef({ onSource, onError }); callbacks.current = { onSource, onError };
  const key = `${instrument}:${end}`;
  useEffect(() => {
    if (!enabled || !end) { setBusy(false); return; }
    let cancelled = false;
    setBusy(true);
    void providerAnalysisSource(instrument, end).then(source => {
      if (!cancelled) { setLoaded({ key, source }); callbacks.current.onSource(source); }
    }).catch(reason => { if (!cancelled) callbacks.current.onError(String(reason)); })
      .finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, [instrument, end, key, enabled]);
  return { source: loaded?.key === key ? loaded.source : undefined, busy };
}
