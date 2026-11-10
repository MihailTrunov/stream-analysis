import { useEffect, useMemo, useRef, useState } from 'react';
import { coversWindow, hasInitializedResult } from './analysisCoverage';
import { mergeObservations, useAnalysisEvidence } from './analysisEvidence';
import { AnalysisSettings } from './AnalysisSettings';
import { CandlestickChart, type OverlayVisibility } from './CandlestickChart';
import { EventInspector } from './EventInspector';
import { PanelTabs } from './PanelTabs';
import { PatternGroups, projectOccurrences } from './PatternGroups';
import { usePriceBuffer } from './priceBuffer';
import { candleAt, localWindowEnd } from './historyNavigation';
import { TIMEFRAMES, type ReplayBar, type ReplayObservation, type ReplayDetectorEvent, type ReplaySource, type ReviewPattern, type Timeframe } from './replay';
import { dateTimeInput, displayTime, inputInstant } from './time';
import { useAnalysisSettings } from './useAnalysisSettings';
import { useLiveAnalysis } from './useLiveAnalysis';
import { useLiveHistory } from './useLiveHistory';
import { useHistoryNavigation } from './useHistoryNavigation';
import { readHistoryWindow } from './useHistoryWindow';
import { useProviderPrices, useProviderNavigation } from './useProviderPrices';
import { providerWindowEnd, type ProviderPriceWindow } from './providerPrices';
import { preferredSource, type AnalysisTab } from './workspace';
import { useProviderAnalysisSource } from './useProviderAnalysisSource';

interface Props {
  tab: AnalysisTab; active: boolean; sources: ReplaySource[]; patterns: ReviewPattern[];
  onTabChange: (id: string, patch: Partial<AnalysisTab>) => void;
  onSourceAdded: (source: ReplaySource) => void;
}
const emptyObservations: ReplayObservation[] = [];
const emptyEvents: ReplayDetectorEvent[] = [];
const emptyPrices: ReplayBar[] = [];
const layers: OverlayVisibility = { ema: true, trend: true, swing: true, range: true, session: true };
export function LiveSession({ tab, active, sources, patterns, onTabChange, onSourceAdded }: Props) {
  const providerSources = sources.filter((source) => !source.non_research_grade && source.instrument_id === tab.instrument);
  const selected = providerSources.find((source) => source.dataset_revision_id === tab.sourceId)
    ?? preferredSource(providerSources, tab.instrument);
  const [timeframe, setTimeframe] = useState<Timeframe>(tab.chartTimeframe ?? '1m');
  const [end, setEnd] = useState<string | undefined>(tab.liveWindowEnd);
  const [date, setDate] = useState(tab.start ?? '');
  const [focus, setFocus] = useState<string | null>(null);
  const [focusVersion, setFocusVersion] = useState(0);
  const [panel, setPanel] = useState<'patterns' | 'settings'>('patterns');
  const [error, setError] = useState<string | null>(null);
  const [details, setDetails] = useState<ReplayDetectorEvent | null>(null);
  const [pinned, setPinned] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reinitializing, setReinitializing] = useState(false);
  const [logicalRange, setLogicalRange] = useState<{ from: number; to: number } | null>(null);
  const [external, setExternal] = useState<{ source: ReplaySource; bars: ReplayBar[]; timeframe: Timeframe; end: string } | null>(null);
  const viewport = useRef(tab.viewport);
  const extending = useRef(false);
  const [bufferVersion, setBufferVersion] = useState(0);
  const [historyNote, setHistoryNote] = useState<string | null>(null);
  const earlierRequest = useRef<string | null>(null);
  const save = useRef<ReturnType<typeof setTimeout> | null>(null);
  const settings = useAnalysisSettings(tab.instrument, tab.streams, tab.appliedStreams,
    tab.config, tab.appliedConfig, setError);
  const [connectionId, setConnectionId] = useState(tab.liveConnectionId ?? crypto.randomUUID());
  const live = useLiveAnalysis(connectionId, tab.instrument, selected?.dataset_revision_id,
    settings.applied, active, tab.liveConnect !== false && settings.ready && !reinitializing,
    timeframe, external || tab.liveHistorySourceId || tab.navigationPlan ? undefined : end, tab.livePreparation, { onError: setError,
      onSource: (source) => { onSourceAdded(source); onTabChange(tab.id, { sourceId: source.dataset_revision_id }); },
      onPlan: (livePreparation) => onTabChange(tab.id, { livePreparation }),
    });
  const initialSource = live.bootstrapSource ?? selected;
  const beforeLiveHistory = Boolean(end && live.state && Date.parse(end) <= Date.parse(live.state.history_start));
  const prices = useProviderPrices(tab.instrument, timeframe, active,
    (!live.connected || beforeLiveHistory) && tab.liveConnect !== false, end, !live.connected);
  const [pricePreview, setPricePreview] = useState<ProviderPriceWindow | null>(null);
  const rawWindow = pricePreview?.timeframe === timeframe && end === pricePreview.end ? pricePreview : prices.window;
  const view = live.view;
  const rawPrices = Boolean(rawWindow?.bars.length && (!view || beforeLiveHistory) && !external);
  const priceNavigation = useProviderNavigation(tab.instrument, timeframe, active, setError);
  const initialKey = `${connectionId}:${timeframe}:${initialSource?.dataset_revision_id}`;
  const [initialError, setInitialError] = useState<{ key: string; message: string } | null>(null);
  const [initialHistory, setInitialHistory] = useState<{ key: string; bars: ReplayBar[] } | null>(null);
  useEffect(() => {
    if (!active || !initialSource || live.connected) return;
    let cancelled = false;
    setInitialError(null);
    void readHistoryWindow(initialSource, initialSource.range_end, timeframe).then((prices) => {
      if (!cancelled) setInitialHistory({ key: initialKey, bars: prices });
    }).catch((reason: unknown) => { if (!cancelled) setInitialError({ key: initialKey, message: `Could not load retained prices: ${String(reason)}` }); });
    return () => { cancelled = true; };
  }, [active, initialSource, initialKey, timeframe, live.connected]);
  useEffect(() => {
    const source = sources.find((item) => item.dataset_revision_id === tab.liveHistorySourceId);
    if (!active || !source || !end || (external?.source.dataset_revision_id === source.dataset_revision_id
      && external.timeframe === timeframe && external.end === end)) return;
    let cancelled = false;
    void readHistoryWindow(source, end, timeframe).then((historyBars) => {
      if (!cancelled) setExternal({ source, bars: historyBars, timeframe, end });
    }).catch((reason: unknown) => { if (!cancelled) setError(String(reason)); });
    return () => { cancelled = true; };
  }, [active, sources, tab.liveHistorySourceId, timeframe, end, external]);
  const externalBars = external?.timeframe === timeframe ? external.bars : [];
  const completed = usePriceBuffer(`${connectionId}:${timeframe}:${bufferVersion}:${rawPrices ? 'provider' : 'validated'}`, external ? externalBars : rawPrices ? rawWindow!.bars : view?.bars ?? (initialHistory?.key === initialKey ? initialHistory.bars : emptyPrices), Boolean(end), viewport.current);
  const navigation = useHistoryNavigation(active, tab.navigationPlan, {
    onPersist: (navigationPlan) => onTabChange(tab.id, { navigationPlan }),
    onError: (message) => { earlierRequest.current = null; setError(message); },
    onReady: (source, historyBars, target, windowEnd) => {
      setPricePreview(null);
      setError(null);
      onSourceAdded(source); setExternal({ source, bars: historyBars, timeframe, end: windowEnd }); setEnd(windowEnd);
      onTabChange(tab.id, { liveHistorySourceId: source.dataset_revision_id });
      if (!extending.current) { setBufferVersion((value) => value + 1); setFocus(target); setFocusVersion((value) => value + 1); setLogicalRange(null); }
      extending.current = false;
    },
    onPrices: (window, target) => {
      setExternal(null); setPricePreview(window); setEnd(window.end); setError(null);
      if (!extending.current) { setBufferVersion(value => value + 1); setFocus(target); setFocusVersion(value => value + 1); setLogicalRange(null); }
      extending.current = false;
    },
  });
  const bars = useMemo(() => view?.live.forming && !end && !external && view.live.status === 'connected'
    && (!completed.length || view.live.forming.timestamp > completed.at(-1)!.timestamp)
    ? [...completed, view.live.forming] : completed, [view, end, external, completed]);
  // Logical indices can stay unchanged while the bounded live window rolls.
  // Derive timestamps from the current bars on every snapshot.
  const left = logicalRange ? Math.max(0, Math.min(bars.length - 1, Math.floor(logicalRange.from))) : 0;
  const right = logicalRange ? Math.max(0, Math.min(bars.length - 1, Math.ceil(logicalRange.to))) : bars.length - 1;
  const start = bars[left]?.timestamp ?? '';
  const finish = bars[right]?.available_at ?? (bars[right]?.is_complete === false ? view?.view_end ?? ''
    : bars[right] ? new Date(Date.parse(bars[right].timestamp) + 60_000).toISOString() : '');
  const liveCoverage = rawPrices ? undefined : view?.stream_coverage;
  const initializedLiveStreams = liveCoverage?.filter((stream) => hasInitializedResult(stream, finish)).length ?? 0;
  const liveCoversWindow = Boolean(liveCoverage?.length && liveCoverage.every((stream) => coversWindow(stream, start, finish)));
  const providerInput = useProviderAnalysisSource(tab.instrument, finish,
    active && rawPrices && !live.connecting && (!live.connected || beforeLiveHistory), onSourceAdded, setError);
  const historySource = rawPrices ? providerInput.source : external?.source ?? (!live.connected && !live.connecting ? initialSource : undefined);
  const historical = useLiveHistory(connectionId, settings.applied, timeframe, start, finish,
    active && (Boolean(historySource) || live.connected && !rawPrices),
    !liveCoversWindow && (Boolean(end) || Boolean(historySource && !live.connected)), setError, historySource);
  const evidence = useAnalysisEvidence(`${connectionId}:${timeframe}:${rawPrices ? 'provider' : 'validated'}`, rawPrices ? emptyObservations : view?.observations ?? emptyObservations, rawPrices ? emptyEvents : view?.events ?? emptyEvents, completed);
  const observations = useMemo(() => mergeObservations(evidence.observations, historical.observations), [evidence.observations, historical.observations]);
  const events = useMemo(() => {
    const calculated = new Set(historical.calculatedKeys);
    return [...evidence.events.filter((event) => !calculated.has(`${event.timeframe}:${event.pattern_id}`)),
      ...historical.events].sort((a, b) => Date.parse(a.available_at ?? a.detection_time) - Date.parse(b.available_at ?? b.detection_time))
      .map((event, emission_order) => ({ ...event, emission_order }));
  }, [evidence.events, historical.events, historical.calculatedKeys]);
  const selectedOrder = events.find((event) => event.event_id === selectedId)?.emission_order ?? null;
  const visibleEvents = useMemo(() => events.filter((event) => (event.timeframe === timeframe || event.emission_order === selectedOrder)
    && Date.parse(event.available_at ?? event.detection_time) <= Date.parse(finish)), [events, timeframe, selectedOrder, finish]);

  useEffect(() => {
    onTabChange(tab.id, { mode: 'live', sourceId: selected?.dataset_revision_id ?? tab.sourceId,
      liveConnectionId: connectionId, chartTimeframe: timeframe, liveWindowEnd: end,
      streams: settings.drafts, appliedStreams: settings.applied ?? undefined });
  }, [onTabChange, tab.id, tab.sourceId, selected, connectionId, timeframe, end, settings.drafts, settings.applied]);
  useEffect(() => () => { if (save.current) clearTimeout(save.current); }, []);

  async function disconnect(history = false) {
    priceNavigation.cancel();
    onTabChange(tab.id, { liveConnect: false }); setError(null);
    try {
      await live.disconnect();
      if (history) {
        navigation.cancel();
        onTabChange(tab.id, { mode: 'history', liveWindowEnd: undefined,
          liveHistorySourceId: undefined, navigationPlan: undefined, priceOnly: rawPrices });
      }
    } catch (reason) { setError(String(reason)); }
  }
  async function reconnect() {
    setError(null);
    try {
      await live.disconnect();
      setConnectionId(crypto.randomUUID()); onTabChange(tab.id, { liveConnect: true });
    } catch (reason) { setError(String(reason)); }
  }
  async function apply() {
    setReinitializing(true); setError(null);
    try {
      if (!await settings.apply()) return;
      await live.disconnect();
      setConnectionId(crypto.randomUUID());
      onTabChange(tab.id, { liveConnect: true });
    } catch (reason) { setError(String(reason)); }
    finally { setReinitializing(false); }
  }
  function moveTo() {
    extending.current = false;
    try {
      const target = inputInstant(date, tab.timezone);
      if (rawPrices) {
        void priceNavigation.load(providerWindowEnd(target, timeframe)).then(window => {
          if (!window) return;
          if (!window.bars.length) { setError('No provider prices were returned for this period.'); return; }
          setPricePreview(window); setEnd(window.end); setBufferVersion(value => value + 1);
          setFocus(target); setFocusVersion(value => value + 1); setLogicalRange(null); setError(null);
        });
        return;
      }
      if (!live.state) {
        if (initialSource) void navigation.begin({ source: external?.source ?? initialSource, target, timeframe,
          streams: settings.applied ?? [], intent: 'move' });
        return;
      }
      if (!selected) return;
      if (Date.parse(target) >= Date.parse(live.state.available_at)) throw new Error('Choose a completed candle.');
      if (Date.parse(target) < Date.parse(live.state.history_start) || external) {
        void navigation.begin({ source: external?.source ?? selected, target, timeframe,
          streams: settings.applied ?? [], intent: 'move' }); return;
      }
      const candle = candleAt(completed, target);
      setFocus(candle?.timestamp ?? target); setFocusVersion((value) => value + 1);
      setEnd(localWindowEnd({ ...selected, range_end: live.state.available_at }, target, timeframe));
      setError(null);
    } catch (reason) { setError(String(reason)); }
  }
  function latest() {
    priceNavigation.cancel();
    setPricePreview(null);
    setBufferVersion((value) => value + 1);
    setHistoryNote(null); extending.current = false;
    if (save.current) clearTimeout(save.current);
    earlierRequest.current = null; navigation.cancel(); setExternal(null); viewport.current = undefined; setEnd(undefined); setFocus(null); setLogicalRange(null);
    setFocusVersion((value) => value + 1); onTabChange(tab.id, { viewport: undefined, liveHistorySourceId: undefined });
  }
  function earlier() {
    if (!completed.length || navigation.busy || priceNavigation.busy) return;
    if (rawPrices) {
      const before = completed[0].timestamp;
      if (earlierRequest.current === before) return;
      earlierRequest.current = before; setHistoryNote('Loading older candles…');
      void priceNavigation.load(before).then(window => {
        if (!window) { earlierRequest.current = null; return; }
        if (!window.bars.length) { setHistoryNote('No earlier provider prices were returned.'); return; }
        setPricePreview(window); setEnd(window.end); setHistoryNote(window.bars.length ? null : 'No earlier provider prices were returned.');
      });
      return;
    }
    if (!live.state) {
      if (!initialSource) return;
      extending.current = true;
      void navigation.begin({ source: external?.source ?? initialSource,
        target: new Date(Date.parse(completed[0].timestamp) - 60_000).toISOString(),
        timeframe, streams: settings.applied ?? [], intent: 'move' });
      return;
    }
    if (!selected) return;
    const key = `${timeframe}:${completed[0].timestamp}`;
    if (earlierRequest.current === key) return;
    earlierRequest.current = key;
    if (!external && Date.parse(completed[0].timestamp) > Date.parse(live.state.history_start)) {
      setHistoryNote('Loading older candles…');
      setEnd(completed[Math.min(90, completed.length - 1)].available_at ?? completed[0].timestamp);
      return;
    }
    extending.current = true; setHistoryNote(null);
    const target = new Date(Date.parse(completed[0].timestamp) - 60_000).toISOString();
    void navigation.begin({ source: external?.source ?? selected, target, timeframe,
      streams: settings.applied ?? [], intent: 'move' });
  }
  function select(event: ReplayDetectorEvent) {
    setSelectedId(event.event_id);
    setDate(dateTimeInput(event.event_time, tab.timezone));
    if (external && settings.applied) {
      void navigation.begin({ source: external.source, target: event.event_time, timeframe,
        streams: settings.applied, intent: 'move' }); return;
    }
    if (selected && live.state) {
      setEnd(localWindowEnd({ ...selected, range_end: live.state.available_at }, event.event_time, timeframe));
      setFocus(event.event_time); setFocusVersion((value) => value + 1);
    }
  }

  if (!active) return <section hidden role="tabpanel" id={`session-${tab.id}`} aria-labelledby={`tab-${tab.id}`} />;
  return <section className="analysis-session" role="tabpanel" id={`session-${tab.id}`} aria-labelledby={`tab-${tab.id}`}>
    <div className="chart-stage">
      <div className="chart-overlay"><span>{tab.instrument}</span>
        <select aria-label="Chart timeframe" value={timeframe} disabled={navigation.busy} onChange={(event) => {
          viewport.current = undefined; onTabChange(tab.id, { viewport: undefined });
          setTimeframe(event.target.value as Timeframe); setLogicalRange(null);
        }}>{TIMEFRAMES.map((tf) => <option key={tf}>{tf}</option>)}</select>
        <span className="source-badge">{end ? 'Provider history' : live.connected || rawPrices ? 'Live provider' : 'Latest available history'}</span>
      </div>
      <CandlestickChart key={timeframe} bars={bars} observations={observations}
        events={visibleEvents} selectedEventOrder={selectedOrder}
        onSelectEvent={(order) => { const event = events.find((item) => item.emission_order === order); if (event) select(event); }}
        cursorTime={bars.at(-1)?.timestamp ?? null} focusTime={focus ? candleAt(completed, focus)?.timestamp ?? focus : null} focusVersion={focusVersion}
        timezone={tab.timezone} overlays={layers} onReturnLatest={latest} canReturnLatest={Boolean(end)}
        onLoadEarlier={earlier} earlierDisabled={navigation.busy || priceNavigation.busy || !completed.length || Boolean(end && !external && !view && !rawPrices)}
        autoLoadEarlier={!navigation.failed && !error}
        historyStatus={navigation.message ?? (navigation.failed ? error : historyNote && !view ? historyNote : null)}
        onNavigate={() => {
          if (!end && rawPrices && rawWindow) { setPricePreview(rawWindow); setEnd(rawWindow.end); }
          else if (!end && live.state) setEnd(live.state.available_at);
        }}
        initialRange={viewport.current?.origin === bars[0]?.timestamp ? viewport.current : null}
        onRangeChange={(range) => {
          if (!bars.length) return;
          setLogicalRange((previous) => previous?.from === range.from && previous.to === range.to ? previous : range);
          const next = { ...range, origin: bars[0].timestamp };
          if (viewport.current?.origin === next.origin && viewport.current.from === next.from && viewport.current.to === next.to) return;
          viewport.current = next;
          if (save.current) clearTimeout(save.current);
          save.current = setTimeout(() => onTabChange(tab.id, { viewport: next }), 200);
        }} />
      <div className="source-footer"><span>{tab.instrument} · {timeframe} · {completed.length} {rawPrices ? 'provider price' : 'completed'} candles</span>
        <span>{rawPrices ? 'Provider candles · detectors evaluate completed bars' : live.state?.forming && !end ? 'Forming candle: display only' : live.connected ? 'Detectors evaluate candle close' : 'Completed history · live monitoring unavailable'}</span></div>
    </div>
    <aside className="workspace-panel" aria-label="Analysis controls">
      <div className="panel-controls">
        <div className="panel-heading"><h2>Live monitoring</h2><span className="connection-status">{live.state?.status ?? (prices.registered ? 'prices connected' : live.connecting ? 'connecting' : 'disconnected')}</span></div>
        <div className="mode-control" aria-label="Analysis mode"><button type="button" onClick={() => void disconnect(true)}>History</button><button type="button" className="selected">Live</button></div>
        {!selected && <p role="status">Initialization history will be prepared from the configured provider.</p>}
        <button type="button" onClick={() => void disconnect()} disabled={!live.connected && !live.connecting && !prices.registered}>Disconnect live</button>
        <button type="button" onClick={() => void reconnect()} disabled={live.connecting || live.connected}>Reconnect live</button>
        {live.state?.detail && <p role="status">{live.state.detail}</p>}
        {live.state && <p role="status">Confirmed through: {displayTime(live.state.available_at, tab.timezone)}</p>}
        {live.stage && <p role="status">{live.stage}</p>}
        {rawPrices && <p role="status">Provider prices loaded independently of calendar validation. Detectors can run on available completed candles; calendar/session coverage remains unverified.</p>}
        {rawPrices && rawWindow?.detail && <p role="status">{rawWindow.detail}</p>}
        {prices.error && !completed.length && <p role="alert">{prices.error}</p>}
        {navigation.failed && <button type="button" onClick={() => { setError(null); void navigation.retry(); }}>Retry history loading</button>}
        <label className="input-label">Go to date & time<input aria-label="Chart date and time" type="datetime-local" value={date} onChange={(event) => setDate(event.target.value)} /></label>
        <button type="button" onClick={moveTo} disabled={(!live.state && !initialSource && !rawPrices) || !date}>Move to</button>
        {focus && <p className="focus-notice">Viewing {displayTime(focus, tab.timezone)}</p>}
        {!completed.length && !live.connecting && initialError?.key === initialKey && <p role="alert">{initialError.message}</p>}
        {error && <p role="alert" className="inline-error">{error}<button type="button" onClick={() => setError(null)} aria-label="Dismiss error">×</button></p>}
      </div>
      <PanelTabs id={tab.id} panel={panel} count={projectOccurrences(events, start, finish).length} onChange={setPanel} />
      <div className="panel-body" id={`panel-${tab.id}`} role="tabpanel" aria-labelledby={`${panel}-${tab.id}`}>
        {panel === 'patterns' ? <>
          <p role="status">{historical.busy ? 'Calculating historical detectors…'
            : historical.loaded || historical.status === 'partial' ? `Historical calculations: ${historical.coverage}.`
            : initializedLiveStreams > 0 ? `Live detector results available for ${initializedLiveStreams} ${initializedLiveStreams === 1 ? 'stream' : 'streams'} in this view.`
            : live.connecting ? 'Initializing live detectors…'
            : end || !live.connected && completed.length ? 'Detector results are unavailable for this period. Use Calculate current view in Detector settings.'
            : 'Live policies evaluate completed candles. Manual detectors require Calculate current view.'}</p>
          <PatternGroups events={events} start={start} end={finish} timeframe={timeframe}
          loaded={initializedLiveStreams > 0 || (!rawPrices && view?.coverage === 'calculated' && Boolean(live.state && Date.parse(start) >= Date.parse(live.state.history_start))) || historical.loaded || historical.status === 'partial'} selected={selectedOrder} annotations={[]} timezone={tab.timezone} onSelect={select}
          onInspect={(event, pin) => { setDetails(event); setPinned(pin); }} onDismiss={() => { if (!pinned) setDetails(null); }} /></>
          : <AnalysisSettings streams={settings.drafts} patterns={patterns} onAddDetector={settings.addDetector}
            disabled={live.connecting || reinitializing || settings.busy || historical.busy || providerInput.busy} dirty={settings.dirty} onChange={settings.setDrafts}
            onAdd={settings.add} onApply={() => void apply()} onCalculate={(tf, pattern) => {
              if (rawPrices && !providerInput.source) { setError('Preparing provider candles for detectors. Please try again shortly.'); return; }
              if (!start || !finish || settings.dirty) { setError('Apply settings and select completed history before calculating.'); return; }
              if (!end && live.state) setEnd(live.state.available_at);
              historical.calculate(tf, pattern);
            }} />}
        <details className="source-settings"><summary>Source & run information</summary>
          <label className="input-label">Bootstrap dataset<select aria-label="Bootstrap dataset" value={selected?.dataset_revision_id ?? ''} disabled={live.connected || live.connecting}
            onChange={(event) => onTabChange(tab.id, { sourceId: event.target.value })}><option value="">Choose recent provider history…</option>{providerSources.map((source) => <option key={source.dataset_revision_id}>{source.dataset_revision_id}</option>)}</select></label>
          <p>Input: <code>{live.state?.input_id ?? 'Not connected'}</code></p>
          <label className="input-label">Display timezone<select aria-label="Display timezone" value={tab.timezone} onChange={(event) => onTabChange(tab.id, { timezone: event.target.value as AnalysisTab['timezone'] })}><option value="Europe/London">UK · GMT / BST</option><option value="UTC">UTC</option></select></label>
        </details>
      </div>
    </aside>
    {details && <div className="evidence-popover" role="dialog" aria-label="Pattern evidence preview" tabIndex={-1}>
      <div className="popover-heading">Recorded pattern evidence<button type="button" aria-label="Close pattern details" onClick={() => { setDetails(null); setPinned(false); }}>×</button></div>
      <EventInspector event={details} timezone={tab.timezone} />
    </div>}
  </section>;
}
