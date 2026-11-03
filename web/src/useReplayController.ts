import { useCallback, useEffect, useRef, useState } from 'react';
import { validateReplayState, validateReplayView } from './validation';
import { apiJson, type ReplayState, type ReplayViewResponse, type ValidationAnnotation } from './replay';

export type ReplayAction = 'play' | 'pause' | 'step' | 'tick' | 'reset' | 'next-event' | 'stop';
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

/** Own the command queue and coherent rendered snapshot for one analysis tab. */
export function useReplayController(initial: ReplayState | null, active: boolean, speed: number,
  onChange: (state: ReplayState | null) => void, onClear: (stop: boolean) => void,
  onError: (error: string) => void) {
  const [state, updateState] = useState(initial);
  const stateRef = useRef(state);
  const [snapshot, setSnapshot] = useState<ReplayViewResponse | null>(null);
  const [annotations, setAnnotations] = useState<ValidationAnnotation[]>([]);
  const [pending, setPending] = useState(0);
  const [commandError, setCommandError] = useState<string | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const callbacks = useRef({ onChange, onClear, onError });
  callbacks.current = { onChange, onClear, onError };
  const queue = useRef<Promise<void>>(Promise.resolve());
  const setState = useCallback((next: ReplayState | null) => {
    stateRef.current = next; updateState(next); callbacks.current.onChange(next);
  }, []);
  const clearSnapshot = useCallback(() => { setSnapshot(null); setAnnotations([]); }, []);
  const control = useCallback((action: ReplayAction) => {
    setPending((count) => count + 1);
    queue.current = queue.current.then(async () => {
      const current = stateRef.current;
      if (!current) return;
      try {
        const next = validateReplayState(await apiJson<ReplayState>(`/replay/${current.run_id}/${action}`, { method: 'POST' }));
        if (action === 'stop' || action === 'reset') {
          callbacks.current.onClear(action === 'stop'); clearSnapshot();
        }
        setState(action === 'stop' ? null : next);
      } catch (error) { setCommandError(message(error)); callbacks.current.onError(message(error)); }
    }).finally(() => setPending((count) => count - 1));
    return queue.current;
  }, [clearSnapshot, setState]);
  const runId = state?.run_id;
  const start = state?.selected_start;
  const end = state?.selected_end;
  const cursor = state?.cursor_index;
  useEffect(() => {
    if (!active || !runId || !start || !end) return;
    const controller = new AbortController();
    setReadError(null);
    const params = new URLSearchParams({ start, end, limit: '500' });
    void apiJson<ReplayViewResponse>(`/replay/${runId}/view?${params}`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setSnapshot(validateReplayView(result)); })
      .catch((error: unknown) => { if (!controller.signal.aborted) setReadError(message(error)); });
    return () => controller.abort();
  }, [active, runId, start, end, cursor, retry]);
  useEffect(() => {
    if (!active || !runId) return;
    const controller = new AbortController();
    void apiJson<{ annotations: ValidationAnnotation[] }>(`/replay/${runId}/annotations`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setAnnotations(result.annotations); })
      .catch((error: unknown) => { if (!controller.signal.aborted) callbacks.current.onError(message(error)); });
    return () => controller.abort();
  }, [active, runId]);
  const ready = !commandError && !readError && snapshot?.run_id === runId && snapshot?.cursor_index === cursor;
  useEffect(() => {
    if (!active && state?.status === 'running') void control('pause');
  }, [active, state?.status, control]);
  useEffect(() => {
    const pause = () => { if (document.hidden && stateRef.current?.status === 'running') void control('pause'); };
    document.addEventListener('visibilitychange', pause);
    return () => document.removeEventListener('visibilitychange', pause);
  }, [control]);
  useEffect(() => {
    if (!active || pending || !ready || state?.status !== 'running' || !state.has_next) return;
    const timer = setTimeout(() => void control('tick'), 1000 / speed);
    return () => clearTimeout(timer);
  }, [active, pending, ready, state?.status, state?.has_next, cursor, speed, control]);
  return { state, stateRef, setState, snapshot, annotations, setAnnotations, control,
    clearSnapshot, busy: pending > 0, ready, readError: readError ?? commandError, retrySnapshot: () => { setCommandError(null); setRetry((value) => value + 1); } };
}
