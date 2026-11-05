import { useCallback, useEffect, useRef, useState } from 'react';
import { validateReplayState } from './validation';
import { coherentReplayView, replayWatermark, revealedWindow, type ReplayWindow } from './replayWindow';
import { apiJson, sessionPath, type Timeframe, type ReplayState, type ReplayViewResponse, type ValidationAnnotation } from './replay';

export type ReplayAction = 'play' | 'pause' | 'step' | 'tick' | 'reset' | 'next-event' | 'stop';
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

/** Own the command queue and coherent rendered snapshot for one analysis tab. */
export function useReplayController(initial: ReplayState | null, active: boolean, speed: number, timeframe: Timeframe,
  onChange: (state: ReplayState | null) => void, onClear: (stop: boolean, snapshot: ReplayViewResponse | null) => void,
  onError: (error: string) => void, sourceStart?: string, initialWindow?: ReplayWindow) {
  const [state, updateState] = useState(initial);
  const stateRef = useRef(state);
  const [snapshot, setSnapshot] = useState<ReplayViewResponse | null>(null);
  const lastSnapshot = useRef<ReplayViewResponse | null>(null);
  const [window, setWindow] = useState<ReplayWindow | null>(initial && initialWindow?.run_id === initial.run_id
    && Date.parse(initialWindow.target) < replayWatermark(initial) && Date.parse(initialWindow.end) <= replayWatermark(initial) ? initialWindow : null);
  const windowRef = useRef(window); windowRef.current = window;
  const historyStart = useRef<string | null>(null);
  const readVersion = useRef(0);
  const [viewRevision, setViewRevision] = useState(0);
  const [snapshotIdentity, setSnapshotIdentity] = useState<string | null>(null);
  const [annotations, setAnnotations] = useState<ValidationAnnotation[]>([]);
  const [pending, setPending] = useState(0);
  const [commandError, setCommandError] = useState<string | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const callbacks = useRef({ onChange, onClear, onError });
  callbacks.current = { onChange, onClear, onError };
  const queue = useRef<Promise<void>>(Promise.resolve());
  const setState = useCallback((next: ReplayState | null) => {
    if (next?.run_id !== stateRef.current?.run_id) {
      setWindow(null); windowRef.current = null; historyStart.current = null;
      lastSnapshot.current = null; setSnapshot(null); setSnapshotIdentity(null);
      ++readVersion.current;
    }
    stateRef.current = next; updateState(next); callbacks.current.onChange(next);
  }, []);
  const clearSnapshot = useCallback(() => {
    ++readVersion.current; setSnapshot(null); setSnapshotIdentity(null); setAnnotations([]);
  }, []);
  const changeWindow = useCallback((next: ReplayWindow | null) => {
    ++readVersion.current; windowRef.current = next; setWindow(next);
    setViewRevision((value) => value + 1);
    setSnapshot(null); setSnapshotIdentity(null); setReadError(null);
  }, []);
  const browse = useCallback((target: string) => {
    const current = stateRef.current;
    if (!current) return;
    changeWindow(revealedWindow(current, timeframe, target, historyStart.current ?? sourceStart ?? current.selected_start));
  }, [changeWindow, timeframe, sourceStart]);
  const control = useCallback((action: ReplayAction) => {
    setPending((count) => count + 1);
    queue.current = queue.current.then(async () => {
      const current = stateRef.current;
      if (!current) return;
      try {
        const next = validateReplayState(await apiJson<ReplayState>(`${sessionPath(current)}/${action}${current.analysis_session ? `?timeframe=${timeframe}` : ''}`, { method: 'POST' }));
        if (action === 'stop' || action === 'reset') {
          callbacks.current.onClear(action === 'stop', lastSnapshot.current);
          changeWindow(null); clearSnapshot();
        }
        setState(action === 'stop' ? null : next);
      } catch (error) { setCommandError(message(error)); callbacks.current.onError(message(error)); }
    }).finally(() => setPending((count) => count - 1));
    return queue.current;
  }, [clearSnapshot, changeWindow, setState, timeframe]);
  const prefix = state ? sessionPath(state) : '';
  const runId = state?.run_id;
  const start = state?.selected_start;
  const end = state?.selected_end;
  const cursor = state?.cursor_index;
  const watermark = state ? replayWatermark(state) : null;
  const requestedEnd = window && window.run_id === runId ? window.end : end;
  const requestedStart = window && window.run_id === runId ? historyStart.current ?? sourceStart ?? start : start;
  const identity = `${runId}:${cursor}:${watermark}:${timeframe}:${requestedStart}:${requestedEnd}:${viewRevision}`;
  useEffect(() => {
    const current = stateRef.current;
    const selected = windowRef.current;
    if (current && selected && selected.timeframe !== timeframe) {
      changeWindow(revealedWindow(current, timeframe, selected.target, historyStart.current ?? sourceStart ?? current.selected_start));
    }
  }, [timeframe, changeWindow, sourceStart]);
  useEffect(() => {
    if (!active || !runId || !requestedStart || !requestedEnd) return;
    const controller = new AbortController();
    const version = readVersion.current;
    const current = stateRef.current!;
    setReadError(null);
    const params = new URLSearchParams({ start: requestedStart, end: requestedEnd, limit: '500', timeframe });
    if (!current.analysis_session && windowRef.current) params.set('as_of_end', 'true');
    void apiJson<ReplayViewResponse>(`${prefix}/view?${params}`, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted || version !== readVersion.current || stateRef.current?.run_id !== runId
          || stateRef.current?.cursor_index !== cursor || replayWatermark(stateRef.current) !== watermark) return;
        const next = coherentReplayView(result, current, timeframe, requestedEnd);
        historyStart.current = next.history_start ?? historyStart.current;
        lastSnapshot.current = next;
        setSnapshot(next); setSnapshotIdentity(identity);
      })
      .catch((error: unknown) => { if (!controller.signal.aborted && version === readVersion.current) setReadError(message(error)); });
    return () => controller.abort();
  }, [active, runId, requestedStart, requestedEnd, cursor, watermark, retry, prefix, timeframe, identity]);
  useEffect(() => {
    if (!active || !runId) return;
    const controller = new AbortController();
    void apiJson<{ annotations: ValidationAnnotation[] }>(`${prefix}/annotations`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setAnnotations(result.annotations); })
      .catch((error: unknown) => { if (!controller.signal.aborted) callbacks.current.onError(message(error)); });
    return () => controller.abort();
  }, [active, runId, prefix]);
  const ready = !commandError && !readError && snapshotIdentity === identity && snapshot?.run_id === runId
    && snapshot?.cursor_index === cursor && (!state?.analysis_session || snapshot?.timeframe === timeframe);
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
  async function retrySnapshot() {
    const current = stateRef.current;
    if (!current) return;
    setPending((count) => count + 1);
    try {
      const recovered = validateReplayState(await apiJson<ReplayState>(sessionPath(current)));
      if (stateRef.current?.run_id !== current.run_id) return;
      setCommandError(null); setState(recovered); setRetry((value) => value + 1);
    } catch (error) { setCommandError(message(error)); }
    finally { setPending((count) => count - 1); }
  }
  return { state, stateRef, setState, snapshot, annotations, setAnnotations, control,
    clearSnapshot, busy: pending > 0, ready, readError: readError ?? commandError, retrySnapshot,
    browse, returnToCursor: () => changeWindow(null), window };
}
