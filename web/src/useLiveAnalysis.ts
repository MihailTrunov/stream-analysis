import { useEffect, useRef, useState } from 'react';
import { apiJson, type AnalysisStream, type Timeframe, type ReplaySource } from './replay';
import { validateLiveState, validateLiveView, type LiveState, type LiveView } from './live';
import { isNavigationPlan, navigationCoverage, publishNavigation, waitForHistory } from './historyNavigation';
import type { AnalysisTab } from './workspace';
import { providerAnalysisSource } from './useProviderAnalysisSource';
import { chartCalculation } from './chartCalculation';

interface Callbacks {
  onError: (message: string) => void;
  onSource: (source: ReplaySource) => void;
  onPlan: (plan: AnalysisTab['livePreparation']) => void;
}

export function useLiveAnalysis(id: string, instrument: string, bootstrap: string | undefined,
  streams: AnalysisStream[] | null, active: boolean, wanted: boolean, timeframe: Timeframe,
  end: string | undefined, initialPlan: AnalysisTab['livePreparation'], handlers: Callbacks) {
  const [state, setState] = useState<LiveState | null>(null);
  const [bootstrapSource, setBootstrapSource] = useState<{ id: string; source: ReplaySource } | null>(null);
  const initialized = useRef<string | null>(null);
  const [view, setView] = useState<LiveView | null>(null);
  const [readKey, setReadKey] = useState('');
  const identity = JSON.stringify([id, timeframe, end]);
  const [visible, setVisible] = useState(!document.hidden);
  const [connecting, setConnecting] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const connection = useRef<string | null>(null);
  const callbacks = useRef(handlers); callbacks.current = handlers;
  const savedPlan = useRef(initialPlan);
  const params = JSON.stringify({ subscription_id: id, bootstrap_revision_id: bootstrap,
    streams, chart_timeframe: '1m', calendar_mode: bootstrap?.startsWith('provider-observed-') ? 'provider_observed' : 'verified' });
  useEffect(() => {
    const changed = () => setVisible(!document.hidden);
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  useEffect(() => {
    if (!active || !wanted || !streams || connection.current === id || initialized.current === id) return;
    const controller = new AbortController();
    setConnecting(true);
    let preparedSource: ReplaySource | undefined;
    void (async () => {
      let plan = savedPlan.current?.key === params ? savedPlan.current.plan : undefined;
      if (!plan) {
        setStage('Preparing live initialization history…');
        const prepared = await apiJson<{ state?: unknown; plan?: unknown; source?: ReplaySource }>(`/live/prepare/${instrument}`,
          { method: 'POST', body: params, signal: controller.signal });
        if (prepared.state) {
          const restored = validateLiveState(prepared.state, id, instrument);
          if (restored.bootstrap_revision_id !== bootstrap) {
            const catalog = await apiJson<{ sources: ReplaySource[] }>('/replay/sources', { signal: controller.signal });
            preparedSource = catalog.sources.find((item) => item.dataset_revision_id === restored.bootstrap_revision_id);
            if (!preparedSource) throw new Error('The live initialization dataset is unavailable.');
          }
          return restored;
        }
        if (prepared.source) {
          const source = prepared.source;
          if (source.instrument_id !== instrument || !source.calendar_version.startsWith('provider-observed-v1:')) throw new Error('Invalid provider detector initialization.');
          preparedSource = source; setBootstrapSource({ id, source });
          setStage('Running detectors on provider candles; calendar coverage is unverified…');
          return validateLiveState(await chartCalculation(`/live/analysis/${instrument}`,
            JSON.stringify({ ...JSON.parse(params), bootstrap_revision_id: source.dataset_revision_id,
              calendar_mode: 'provider_observed' })), id, instrument);
        }
        if (!isNavigationPlan(prepared.plan) || prepared.plan.instrument_id !== instrument
          || prepared.plan.intent !== 'replay' || prepared.plan.timeframe !== '1m') throw new Error('Invalid live initialization plan.');
        plan = prepared.plan;
        savedPlan.current = { plan, key: params }; callbacks.current.onPlan(savedPlan.current);
      }
      let observedSource: ReplaySource | undefined;
      if (plan.live_status === 'calendar_unverified') {
        setStage('Preparing provider candles for detectors; calendar coverage is unverified…');
        try { observedSource = await providerAnalysisSource(instrument, undefined, true); }
        catch { if (controller.signal.aborted) return; }
      }
      let coverage = observedSource ? { ready: true, completed: 0, requests: {} } : await navigationCoverage(plan, controller.signal);
      while (!coverage.ready) {
        setStage(`Waiting for live initialization history… ${coverage.completed} ranges ready.`);
        await waitForHistory(controller.signal);
        coverage = await navigationCoverage(plan, controller.signal);
      }
      const source = observedSource ?? await publishNavigation(plan, coverage.requests, controller.signal);
      if (controller.signal.aborted) return;
      preparedSource = source;
      setBootstrapSource({ id, source });
      if (plan.live_status === 'calendar_unverified' && !observedSource) {
        initialized.current = id;
        setStage(plan.live_detail ?? 'Showing completed history; live calendar coverage is unavailable.');
        callbacks.current.onSource(source);
        savedPlan.current = undefined; callbacks.current.onPlan(undefined);
        return null;
      }
      setStage(plan.live_detail || 'History ready; connecting live monitoring…');
      const body = { ...JSON.parse(params), bootstrap_revision_id: source.dataset_revision_id,
        calendar_mode: source.calendar_version.startsWith('provider-observed-v1:') ? 'provider_observed' : 'verified' };
      const next = validateLiveState(await chartCalculation(`/live/analysis/${instrument}`,
        JSON.stringify(body)), id, instrument);
      preparedSource = source;
      return next;
    })().then((next) => {
        if (controller.signal.aborted) return;
        if (!next) return;
        connection.current = id; initialized.current = id; setState(next);
        if (preparedSource) callbacks.current.onSource(preparedSource);
        savedPlan.current = undefined; callbacks.current.onPlan(undefined);
        setConnecting(false); setStage(null);
      }).catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (preparedSource) {
          initialized.current = id; callbacks.current.onSource(preparedSource);
          setStage('Showing completed history; live monitoring is unavailable.');
        } else setStage(null);
        callbacks.current.onError(String(error));
      })
      .finally(() => { if (!controller.signal.aborted) setConnecting(false); });
    return () => { controller.abort(); setConnecting(false); };
  }, [id, active, wanted, bootstrap, streams, instrument, params]);
  const connected = connection.current === id && state?.subscription_id === id;
  useEffect(() => {
    if (!active || !visible || !connected) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const query = new URLSearchParams({ timeframe });
        if (end) query.set('end', end);
        const result = validateLiveView(await apiJson(`/live/analysis/${id}/view?${query}`,
          { signal: controller.signal }), id, instrument, timeframe, end);
        if (controller.signal.aborted) return;
        setState(result.live); setView(result); setReadKey(identity);
      } catch (error) { if (!controller.signal.aborted) callbacks.current.onError(String(error)); }
      if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 1000);
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [id, active, visible, connected, timeframe, end, instrument, identity]);
  async function disconnect() {
    connection.current = null; initialized.current = null; setState(null); setView(null); setConnecting(false); setStage(null);
    await apiJson(`/live/subscriptions/${id}`, { method: 'DELETE' });
  }
  return { state: state?.subscription_id === id ? state : null,
    view: connected && (readKey === identity || end === undefined && view?.view_end === view?.live.available_at || Boolean(end && view?.view_end
      && Date.parse(view.view_end) === Date.parse(end))) && view?.timeframe === timeframe
      && view.live.subscription_id === id ? view : null,
    bootstrapSource: bootstrapSource?.id === id ? bootstrapSource.source : undefined,
    connecting, connected, disconnect, stage };
}
