import { useEffect, useMemo, useRef, useState } from 'react';
import { replayWatermark } from './replayWindow';
import { chartCalculation } from './chartCalculation';
import { useAnalysisEvidence } from './analysisEvidence';
import { usePriceBuffer } from './priceBuffer';
import { validateReplayState } from './validation';
import { useHistoryWindow } from './useHistoryWindow';
import { useHistoryNavigation } from './useHistoryNavigation';
import { candleAt } from './historyNavigation';
import { PanelTabs } from './PanelTabs';
import { useAnalysisSettings } from './useAnalysisSettings';
import { AnalysisSettings } from './AnalysisSettings';
import { useHistoricalAnalysis } from './useHistoricalAnalysis';
import { PatternGroups, projectOccurrences } from './PatternGroups';
import { useReplayController } from './useReplayController';
import { CandlestickChart, type OverlayVisibility } from './CandlestickChart';
import { EventInspector } from './EventInspector';
import { EventReview, MissedPatternReview } from './ReviewAnnotations';
import { TIMEFRAMES, type Timeframe, type ReplayBar,
  type ReplayDetectorEvent, type ReplayObservation, type ReplaySource,
  type ReplayState, type ReviewPattern } from './replay';
import { dateTimeInput, displayTime, inputInstant, type DisplayTimezone } from './time';
import { preferredSource, type AnalysisTab } from './workspace';
import { providerWindowEnd, type ProviderPriceWindow } from './providerPrices';
import { useProviderPrices, useProviderNavigation } from './useProviderPrices';
import { useProviderAnalysisSource } from './useProviderAnalysisSource';

interface Props {
  tab: AnalysisTab;
  active: boolean;
  sources: ReplaySource[];
  patterns: ReviewPattern[];
  activeRun: ReplayState | null;
  onTabChange: (id: string, patch: Partial<AnalysisTab>) => void;
  onRunChange: (id: string, state: ReplayState | null) => void;
  onSourceAdded: (source: ReplaySource) => void;
}
const defaultLayers: OverlayVisibility = { ema: true, trend: true, swing: true, range: true, session: true };
const emptyBars: ReplayBar[] = [];
const emptyObservations: ReplayObservation[] = [];
const emptyEvents: ReplayDetectorEvent[] = [];
const errorMessage = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

export function ReplaySession({ tab, active, sources, patterns, activeRun, onTabChange, onRunChange, onSourceAdded }: Props) {
  const restored = useRef(tab);
  const initialRun = activeRun?.run_id === tab.runId ? activeRun : null;
  const [selectedId, setSelectedId] = useState(initialRun?.dataset_revision_id
    ?? (sources.some((source) => source.dataset_revision_id === tab.sourceId) ? tab.sourceId
      : preferredSource(sources, tab.instrument)?.dataset_revision_id ?? ''));
  const selected = sources.find((source) => source.dataset_revision_id === selectedId);
  const [start, setStart] = useState(tab.start ?? (initialRun ? dateTimeInput(initialRun.selected_start, tab.timezone)
    : selected ? dateTimeInput(selected.suggested_start, tab.timezone) : ''));
  const [end, setEnd] = useState(initialRun ? dateTimeInput(initialRun.selected_end, tab.timezone)
    : tab.end ?? (selected ? dateTimeInput(selected.suggested_end, tab.timezone) : ''));
  const [chartTimeframe, setChartTimeframe] = useState<Timeframe>(tab.chartTimeframe ?? '1m');
  const [historyEnd, setHistoryEnd] = useState<string | undefined>(tab.historyEnd);
  const [pricePreview, setPricePreview] = useState<ProviderPriceWindow | null>(null);
  const [endExplicit, setEndExplicit] = useState(tab.replayEndExplicit ?? false);
  const [navigationNote, setNavigationNote] = useState<string | null>(null);
  const extending = useRef(false);
  const [bufferVersion, setBufferVersion] = useState(0);
  const edgeAttempts = useRef(new Set<string>());
  const focusedReplayWindow = useRef<string | null>(null);
  const [viewedPeriod, setViewedPeriod] = useState<{ start: string; end: string } | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [focusTime, setFocusTime] = useState<string | null>(null);
  const [focusVersion, setFocusVersion] = useState(0);
  const [reviewAnchor, setReviewAnchor] = useState<string | null>(null);
  const [reviewInterval, setReviewInterval] = useState<{ start: string; end: string } | null>(null);
  const [layers, setLayers] = useState(defaultLayers);
  const [panel, setPanel] = useState<'patterns' | 'settings'>('patterns');
  const [speed, setSpeed] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(tab.runId && !initialRun
    ? 'The previous replay is no longer active. Its saved chart settings are restored; start a new replay to continue.' : null);
  const settings = useAnalysisSettings(selected?.instrument_id ?? tab.instrument,
    initialRun?.streams ?? tab.streams, initialRun?.streams ?? tab.appliedStreams,
    initialRun?.detection_config ?? tab.config, initialRun?.detection_config ?? tab.appliedConfig, setError);
  const [details, setDetails] = useState<ReplayDetectorEvent | null>(null);
  const [pinned, setPinned] = useState(false);
  const closePreview = useRef<ReturnType<typeof setTimeout> | null>(null);
  const detailOpener = useRef<HTMLElement | null>(null);
  const restoringFocus = useRef(false);
  const popover = useRef<HTMLDivElement>(null);
  const viewport = useRef<AnalysisTab['viewport'] | null>(tab.viewport ?? null);
  const saveViewport = useRef<ReturnType<typeof setTimeout> | null>(null);
  const replay = useReplayController(initialRun, active, speed, chartTimeframe,
    (next) => onRunChange(tab.id, next), (stop, snapshot) => {
      if (stop) {
        const prices = visible.length ? visible : snapshot?.bars ?? [];
        const last = prices.at(-1);
        const retainedEnd = last?.available_at
          ?? (last ? new Date(Date.parse(last.timestamp) + 60_000).toISOString() : snapshot?.view_end ?? state?.selected_start);
        setHistory(prices); setHistoryEnd(retainedEnd); setError(null); setNavigationNote(null);
        edgeAttempts.current.clear();
        if (saveViewport.current) clearTimeout(saveViewport.current);
        onTabChange(tab.id, { historyEnd: retainedEnd, viewport: viewport.current ?? undefined, replayWindow: undefined });
      }
      clearRunView(!stop);
    }, setError, selected?.range_start, tab.replayWindow);
  const restoredReplayWindow = useRef(replay.window);
  const { state, stateRef, setState, control, annotations, setAnnotations } = replay;
  const provider = useProviderPrices(tab.instrument, chartTimeframe, active,
    !state && !selected?.non_research_grade && (!selected || tab.priceOnly === true), historyEnd, false);
  const rawWindow = pricePreview?.timeframe === chartTimeframe ? pricePreview : provider.window;
  const rawPrices = !state && Boolean(rawWindow?.bars.length && (pricePreview || tab.priceOnly || !selected));
  const priceNavigation = useProviderNavigation(tab.instrument, chartTimeframe, active, setError);
  const chartBars = replay.snapshot?.run_id === state?.run_id && (!state?.analysis_session || replay.snapshot?.timeframe === chartTimeframe) ? replay.snapshot?.bars ?? emptyBars : emptyBars;
  const snapshotObservations = replay.snapshot?.run_id === state?.run_id && (!state?.analysis_session || replay.snapshot?.timeframe === chartTimeframe) ? replay.snapshot?.observations ?? emptyObservations : emptyObservations;
  const snapshotEvents = replay.snapshot?.run_id === state?.run_id && (!state?.analysis_session || replay.snapshot?.timeframe === chartTimeframe) ? replay.snapshot?.events ?? emptyEvents : emptyEvents;
  const { history, setHistory, loadingHistory } = useHistoryWindow(active, selected,
    state?.selected_start ?? historyEnd ?? selected?.range_end, setError, chartTimeframe);
  const navigation = useHistoryNavigation(active, initialRun ? undefined : tab.navigationPlan, {
    onPersist: (navigationPlan) => onTabChange(tab.id, { navigationPlan }),
    onError: setError,
    onReady: async (source, bars, target, windowEnd, intent, initialization) => {
      if (stateRef.current) return;
      setPricePreview(null);
      onTabChange(tab.id, { priceOnly: false });
      onSourceAdded(source); setSelectedId(source.dataset_revision_id);
      setHistoryEnd(windowEnd); setHistory(bars); setViewedPeriod(null);
      if (!extending.current) { setBufferVersion((value) => value + 1); clearRunView(); }
      if (intent === 'replay') { await launchSource(source, target, windowEnd); return; }
      const candle = candleAt(bars, target) ?? [...bars].reverse().find((bar) => Date.parse(bar.timestamp) <= Date.parse(target));
      if (!candle) throw new Error('No completed trading candle is available at this time.');
      setNavigationNote(initialization === 'unavailable' ? 'Chart loaded; earlier verified history is insufficient to initialize all detectors.'
        : candleAt(bars, target) ? null : 'No completed candle at the requested time; showing the preceding trading candle.');
      if (!extending.current) { setFocusTime(candle.timestamp); setFocusVersion((version) => version + 1); }
      extending.current = false; setError(null);
    },
    onPrices: (window, target) => {
      if (stateRef.current) return;
      setPricePreview(window); setHistoryEnd(window.end); setViewedPeriod(null); setError(null);
      onTabChange(tab.id, { priceOnly: true });
      setNavigationNote('Provider candles loaded. Calendar/session coverage is unverified.');
      if (!extending.current) { setBufferVersion(value => value + 1); clearRunView(); setFocusTime(target); setFocusVersion(value => value + 1); }
      extending.current = false;
    },
  });
  const working = busy || replay.busy || settings.busy || navigation.busy || priceNavigation.busy;
  const visibleHistory = useMemo(() => state ? history.filter((bar) => bar.timestamp < state.selected_start) : history,
    [history, state]);
  const incomingPrices = useMemo(() => state ? state.analysis_session ? chartBars : [...visibleHistory, ...chartBars].slice(-500) : rawPrices ? rawWindow!.bars : history, [state, visibleHistory, chartBars, history, rawPrices, rawWindow]);
  const buffered = usePriceBuffer(`${selected?.instrument_id ?? tab.instrument}:${state?.run_id ?? 'history'}:${chartTimeframe}:${bufferVersion}:${rawPrices ? 'provider' : 'validated'}`, incomingPrices, Boolean(historyEnd || replay.window), viewport.current);
  const visible = useMemo(() => state ? buffered.filter((bar) => Date.parse(bar.available_at ?? new Date(Date.parse(bar.timestamp) + 60000).toISOString()) <= replayWatermark(state)) : buffered, [buffered, state]);
  const cursor = visible.at(-1)?.timestamp ?? null;
  const evidence = useAnalysisEvidence(`${state?.run_id ?? selectedId}:${chartTimeframe}`, snapshotObservations, snapshotEvents, visible);

  const periodStart = viewedPeriod?.start ?? visible[0]?.timestamp ?? null;
  const periodEnd = viewedPeriod?.end ?? visible.at(-1)?.available_at
    ?? (visible.at(-1) ? new Date(Date.parse(visible.at(-1)!.timestamp) + 60000).toISOString() : null);
  const providerInput = useProviderAnalysisSource(tab.instrument, periodEnd ?? '',
    active && rawPrices, onSourceAdded, setError);
  const historical = useHistoricalAnalysis(rawPrices ? providerInput.source : selected, settings.applied, chartTimeframe,
    periodStart, periodEnd, active && !state, setError);
  const events = useMemo(() => state ? evidence.events.map((event, emission_order) => ({ ...event, emission_order })) : historical.events, [state, evidence.events, historical.events]);
  const selectedEvent = events.find((event) => event.event_id === selectedEventId);
  const selectedOrder = selectedEvent?.emission_order ?? null;
  const chartEvents = useMemo(() => events.filter((event) => (event.timeframe === chartTimeframe || event.emission_order === selectedOrder) && (!periodEnd || Date.parse(event.available_at ?? event.detection_time) <= Date.parse(periodEnd))), [events, chartTimeframe, selectedOrder, periodEnd]);

  const startupRun = useRef(initialRun);
  useEffect(() => {
    if (rawPrices && !tab.priceOnly) onTabChange(tab.id, { priceOnly: true });
  }, [rawPrices, tab.priceOnly, onTabChange, tab.id]);
  useEffect(() => {
    if (restored.current.runId && !startupRun.current) onTabChange(tab.id, { runId: null });
  }, [onTabChange, tab.id]);

  useEffect(() => {
    if (!selected) return;
    onTabChange(tab.id, { sourceId: selectedId, instrument: selected.instrument_id, start, end,
      streams: settings.drafts.length ? settings.drafts : undefined, appliedStreams: settings.applied ?? undefined, chartTimeframe,
      historyEnd, replayEndExplicit: endExplicit, replayWindow: state ? replay.window ?? undefined : undefined });
  }, [selectedId, start, end, settings.drafts, settings.applied, chartTimeframe, historyEnd, endExplicit, replay.window, state, onTabChange, tab.id, selected]);

  useEffect(() => {
    if (!state || !replay.window) { focusedReplayWindow.current = null; return; }
    if (!replay.ready) return;
    const selectedWindow = replay.window;
    const key = `${selectedWindow.run_id}:${selectedWindow.target}:${selectedWindow.end}:${chartTimeframe}`;
    if (focusedReplayWindow.current === key) return;
    const candle = candleAt(chartBars, selectedWindow.target)
      ?? [...chartBars].reverse().find((bar) => Date.parse(bar.timestamp) <= Date.parse(selectedWindow.target));
    if (!candle) { setError('No completed candle is available at this point in the recorded replay.'); return; }
    focusedReplayWindow.current = key;
    const preserveViewport = restoredReplayWindow.current === selectedWindow
      && restored.current.chartTimeframe === chartTimeframe && viewport.current?.origin === chartBars[0]?.timestamp;
    restoredReplayWindow.current = null;
    if (!extending.current) {
      setFocusTime(candle.timestamp);
      if (!preserveViewport) setFocusVersion((version) => version + 1);
    }
    extending.current = false;
    setNavigationNote(candleAt(chartBars, selectedWindow.target) ? null : 'Showing the preceding completed replay candle.');
  }, [state, replay.window, replay.ready, chartBars, chartTimeframe]);

  function clearRunView(resetViewport = true) {
    replay.clearSnapshot();
    setSelectedEventId(null); setFocusTime(null); setReviewAnchor(null); setReviewInterval(null);
    setDetails(null); setPinned(false);
    if (resetViewport) { viewport.current = null; onTabChange(tab.id, { viewport: undefined }); }
  }

  useEffect(() => () => {
    if (closePreview.current) clearTimeout(closePreview.current);
    if (saveViewport.current) clearTimeout(saveViewport.current);
  }, []);

  useEffect(() => {
    if (!pinned) return;
    if (closePreview.current) clearTimeout(closePreview.current);
    popover.current?.focus();
  }, [pinned, details?.event_id]);

  async function launchSource(source: ReplaySource, target: string, finish: string) {
    if (!settings.applied || stateRef.current) return;
    setBusy(true); setError(null);
    try {
      const next = validateReplayState(await chartCalculation<ReplayState>('/analysis', JSON.stringify({
        dataset_revision_id: source.dataset_revision_id, instrument_id: source.instrument_id, chart_timeframe: chartTimeframe,
        selected_start: target, selected_end: finish,
        streams: settings.applied,
      })));
      onSourceAdded(source); setSelectedId(source.dataset_revision_id);
      onTabChange(tab.id, { priceOnly: false });
      clearRunView(); setEnd(dateTimeInput(next.selected_end, tab.timezone)); setState(next);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  }

  async function launch() {
    if (settings.dirty || !settings.applied || state) return;
    try {
      setError(null);
      const target = inputInstant(start, tab.timezone);
      if (rawPrices && providerInput.source) {
        await launchSource(providerInput.source, target, providerInput.source.range_end); return;
      }
      if (!selected) return;
      if (selected.non_research_grade) await launchSource(selected, target, inputInstant(end, tab.timezone));
      else await navigation.begin({ source: selected, target, timeframe: chartTimeframe, streams: settings.applied,
        intent: 'replay', end: endExplicit ? inputInstant(end, tab.timezone) : undefined });
    } catch (reason) { setError(errorMessage(reason)); }
  }

  async function moveTo() {
    extending.current = false;
    try {
      const instant = inputInstant(start, tab.timezone);
      const candle = candleAt(visible, instant);
      if (!candle) {
        if (state) {
          setBufferVersion((value) => value + 1); replay.browse(instant); setFocusTime(null); setNavigationNote(null); setError(null); return;
        }
        if (!selected || rawPrices) {
          const window = await priceNavigation.load(providerWindowEnd(instant, chartTimeframe));
          if (window) {
            if (!window.bars.length) { setError('No provider prices were returned for this period.'); return; }
            setPricePreview(window); setHistoryEnd(window.end); setViewedPeriod(null);
            onTabChange(tab.id, { priceOnly: true });
            setBufferVersion(value => value + 1); setFocusTime(instant); setFocusVersion(value => value + 1); setError(null);
          }
          return;
        }
        setError(null); setNavigationNote(null);
        await navigation.begin({ source: selected, target: instant, timeframe: chartTimeframe,
          streams: settings.applied ?? [], intent: 'move' });
        return;
      }
      if (state) replay.focusLoaded(instant);
      setError(null); setNavigationNote(null); setFocusTime(candle.timestamp); setFocusVersion((version) => version + 1);
    } catch (reason) { setError(errorMessage(reason)); }
  }

  function returnToCursor() {
    if (saveViewport.current) clearTimeout(saveViewport.current);
    viewport.current = null; onTabChange(tab.id, { viewport: undefined });
    setFocusTime(null); setFocusVersion((version) => version + 1);
    setNavigationNote(null); setError(null); replay.returnToCursor();
  }

  function chooseSource(id: string) {
    priceNavigation.cancel();
    setPricePreview(null);
    onTabChange(tab.id, { priceOnly: false });
    setBufferVersion((value) => value + 1);
    navigation.cancel(); setHistoryEnd(undefined); setEndExplicit(false); setNavigationNote(null); edgeAttempts.current.clear();
    if (saveViewport.current) clearTimeout(saveViewport.current);
    onTabChange(tab.id, { viewport: undefined });
    const source = sources.find((item) => item.dataset_revision_id === id);
    if (source) {
      setStart(dateTimeInput(source.suggested_start, tab.timezone));
      setEnd(dateTimeInput(source.suggested_end, tab.timezone));
    }
    setSelectedId(id); setHistory([]); setViewedPeriod(null); clearRunView(); setError(null);
  }

  function loadEarlier(automatic = false) {
    if (working || loadingHistory || !visible.length || state && !replay.ready) return;
    const first = visible[0].timestamp;
    const key = `${selectedId}:${chartTimeframe}:${first}`;
    if (automatic && edgeAttempts.current.has(key)) return;
    edgeAttempts.current.add(key);
    if (rawPrices || !selected) {
      void priceNavigation.load(first).then(window => {
        if (window?.bars.length) { setPricePreview(window); setHistoryEnd(window.end); setViewedPeriod(null); setError(null); onTabChange(tab.id, { priceOnly: true }); }
        else if (window) setNavigationNote('No earlier provider prices were returned.');
      });
      return;
    }
    if (state) {
      try {
        extending.current = true;
        replay.browse(new Date(Date.parse(first) - 60_000).toISOString());
        setNavigationNote(null); setError(null);
      } catch (reason) { setNavigationNote(errorMessage(reason)); }
      return;
    }
    if (selected.non_research_grade && Date.parse(first) <= Date.parse(selected.range_start)) {
      setNavigationNote('Reached the beginning of this synthetic dataset.'); return;
    }
    extending.current = true;
    const target = new Date(Date.parse(first) - 60_000).toISOString();
    setError(null); setNavigationNote(null);
    void navigation.begin({ source: selected, target, timeframe: chartTimeframe, streams: settings.applied ?? [], intent: 'move' });
  }

  function chooseTimezone(timezone: DisplayTimezone) {
    let needsReview = false;
    const convert = (value: string) => {
      if (!value) return value;
      try { return dateTimeInput(inputInstant(value, tab.timezone), timezone); }
      catch { needsReview = true; return value; }
    };
    setStart(convert(start)); setEnd(convert(end));
    onTabChange(tab.id, { timezone });
    setError(needsReview ? 'Timezone changed. Check the date/time before starting replay.' : null);
  }

  function selectEvent(event: ReplayDetectorEvent) {
    setSelectedEventId(event.event_id);
    const matched = visible.find((bar) => bar.timestamp === event.detection_time)
      ?? (event.timeframe !== chartTimeframe ? [...visible].reverse().find((bar) => Date.parse(bar.timestamp) <= Date.parse(event.detection_time)) : undefined);
    if (!matched) {
      setFocusTime(null);
      setError('This event is outside the loaded candle window. Its evidence is available through Pattern details.');
      if (stateRef.current?.status === 'running') void control('pause');
      return;
    }
    setError(null); setFocusTime(matched.timestamp);
    setFocusVersion((version) => version + 1);
    if (stateRef.current?.status === 'running') void control('pause');
  }

  function selectBar(timestamp: string) {
    const selectedCandle = visible.find((bar) => bar.timestamp === timestamp);
    if (!state || timestamp < state.selected_start && (!state.analysis_session
      || !selectedCandle?.available_at || Date.parse(selectedCandle.available_at) <= Date.parse(state.selected_start))) return;
    if (!reviewAnchor) {
      setReviewAnchor(timestamp);
      setReviewInterval({ start: timestamp, end: visible.find((bar) => bar.timestamp === timestamp)?.available_at ?? new Date(Date.parse(timestamp) + 60_000).toISOString() });
    } else {
      setReviewInterval({ start: new Date(Math.min(Date.parse(reviewAnchor), Date.parse(timestamp))).toISOString(),
        end: visible.find((bar) => Date.parse(bar.timestamp) === Math.max(Date.parse(reviewAnchor), Date.parse(timestamp)))?.available_at
          ?? new Date(Math.max(Date.parse(reviewAnchor), Date.parse(timestamp)) + 60_000).toISOString() });
      setReviewAnchor(null);
    }
  }

  function inspect(event: ReplayDetectorEvent, pin: boolean) {
    if (restoringFocus.current) return;
    if (closePreview.current) clearTimeout(closePreview.current);
    if (pinned && !pin) return;
    if (pin) detailOpener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setDetails(event); setPinned(pin);
  }
  function closeDetails() {
    setDetails(null); setPinned(false);
    if (pinned && detailOpener.current?.isConnected) {
      restoringFocus.current = true;
      detailOpener.current.focus();
      restoringFocus.current = false;
    }
  }
  function dismissPreview() {
    if (!pinned) closePreview.current = setTimeout(() => setDetails(null), 200);
  }

  if (!active) return <section hidden role="tabpanel" id={`session-${tab.id}`} aria-labelledby={`tab-${tab.id}`} />;
  return <section className="analysis-session" role="tabpanel" id={`session-${tab.id}`} aria-labelledby={`tab-${tab.id}`}>
    <div className="chart-stage">
      <div className="chart-overlay">
        <label className="sr-only" htmlFor={`instrument-${tab.id}`}>Instrument</label>
        <select id={`instrument-${tab.id}`} aria-label="Instrument" value={selected?.instrument_id ?? tab.instrument} disabled={Boolean(state) || working}
          onChange={(event) => { const source = preferredSource(sources, event.target.value); if (source) chooseSource(source.dataset_revision_id); }}>
          <option value="US30">US30</option><option value="DAX">DAX</option>
        </select>
        <select aria-label="Chart timeframe" value={chartTimeframe} onChange={(event) => {
          navigation.cancel(); edgeAttempts.current.clear(); setNavigationNote(null);
          setChartTimeframe(event.target.value as Timeframe); setViewedPeriod(null); setHistory([]);
        }}>{TIMEFRAMES.map((tf) => <option key={tf} value={tf} disabled={Boolean(state && !state.analysis_session && tf !== '1m')}>{tf === '1d' ? '1D' : tf}</option>)}</select>
        <span className={`source-badge ${selected?.non_research_grade ? 'synthetic' : ''}`}>{selected?.non_research_grade ? 'Synthetic data' : rawPrices ? 'Provider history' : 'Local history'}</span>
      </div>
      {loadingHistory && <span className="history-loading" role="status">Loading candles…</span>}
      <CandlestickChart key={`${selected?.instrument_id}:${state?.run_id ?? 'history'}:${chartTimeframe}:${bufferVersion}`} resetKey={`${selected?.instrument_id}:${state?.run_id ?? 'history'}:${chartTimeframe}:${bufferVersion}`}
        bars={visible} observations={state ? evidence.observations : historical.observations} events={chartEvents}
        selectedEventOrder={selectedOrder} onSelectEvent={(order) => { const event = events.find((item) => item.emission_order === order); if (event) selectEvent(event); }}
        onSelectBar={state && replay.ready && !working ? selectBar : undefined} reviewInterval={reviewInterval}
        cursorTime={cursor} focusTime={focusTime} overlays={layers} timezone={tab.timezone}
        focusVersion={focusVersion} onReturnLatest={() => {
          setFocusTime(null);
          if (state) returnToCursor();
          if (!state) {
            priceNavigation.cancel();
            setPricePreview(null);
            onTabChange(tab.id, { priceOnly: false });
            navigation.cancel(); setHistoryEnd(undefined); setViewedPeriod(null); setNavigationNote(null);
            const latest = preferredSource(sources, selected?.instrument_id ?? tab.instrument);
            if (latest && latest.dataset_revision_id !== selectedId) {
              setHistory([]); viewport.current = null; setSelectedId(latest.dataset_revision_id);
            }
          }
        }} onLoadEarlier={loadEarlier} earlierDisabled={working || loadingHistory || !visible.length || Boolean(state && !replay.ready)}
        autoLoadEarlier={!navigation.failed && !error && !navigationNote}
        historyStatus={navigation.message ?? navigationNote ?? (loadingHistory ? 'Loading candles…' : null)}
        canReturnLatest={state ? Boolean(replay.window) : Boolean(pricePreview || historyEnd && selected && Date.parse(historyEnd) < Date.parse(selected.range_end))}
        initialRange={viewport.current?.origin === visible[0]?.timestamp ? viewport.current : null}
        onRangeChange={(value) => {
          if (!visible.length) return;
          const left = visible[Math.max(0, Math.min(visible.length - 1, Math.floor(value.from)))];
          const right = visible[Math.max(0, Math.min(visible.length - 1, Math.ceil(value.to)))];
          const period = { start: left.timestamp, end: right.available_at ?? new Date(Date.parse(right.timestamp) + 60000).toISOString() };
          setViewedPeriod((current) => current?.start === period.start && current.end === period.end ? current : period);
          const saved = { ...value, origin: visible[0].timestamp };
          if (viewport.current?.from === saved.from && viewport.current.to === saved.to
            && viewport.current.origin === saved.origin) return;
          viewport.current = saved;
          if (saveViewport.current) clearTimeout(saveViewport.current);
          saveViewport.current = setTimeout(() => onTabChange(tab.id, { viewport: saved }), 200);
        }} />
      <div className="source-footer"><span>{selected?.instrument_id} · {chartTimeframe} · {state ? `${state.visible_bars} replay bars revealed` : `${visible.length} ${rawPrices ? 'provider' : 'local'} candles`}</span>
        <span>{tab.timezone === 'Europe/London' ? 'UK time · GMT / BST' : 'UTC'}{selected?.non_research_grade ? ' · Not research-grade' : ''}</span></div>
    </div>
    <aside className="workspace-panel" aria-label="Analysis controls">
      {(rawPrices || selected?.calendar_version.startsWith('provider-observed-v1:')) && <p role="status">Provider prices loaded independently of calendar validation. Detectors can run on available completed candles; calendar/session coverage remains unverified.</p>}
      {rawPrices && rawWindow?.detail && <p role="status">{rawWindow.detail}</p>}
      <div className="panel-controls">
        <div className="panel-heading"><h2>{state ? 'Historical replay' : 'Historical exploration'}</h2><span className="connection-status">LOCAL</span></div>
        <div className="mode-control" aria-label="Analysis mode"><button type="button" className="selected">History</button>
          <button type="button" disabled={Boolean(state) || busy || replay.busy || settings.busy} onClick={() => {
            navigation.cancel(); onTabChange(tab.id, { mode: 'live', liveConnect: true, liveConnectionId: crypto.randomUUID() });
          }}>Live</button></div>
        <label className="input-label">Go to date & time <input aria-label="Chart date and time" type="datetime-local" value={start} disabled={working} onChange={(event) => setStart(event.target.value)} /></label>
        <div className="navigation-actions"><button type="button" onClick={moveTo} disabled={working || !start}>Move to</button>
          <button className="primary" type="button" onClick={() => void launch()} disabled={working || providerInput.busy || Boolean(state) || (!selected && !providerInput.source) || settings.dirty}>Start replay from</button></div>
        <div className="transport" aria-label="Replay controls">
          <button type="button" aria-label="Play" title="Play" onClick={() => void control('play')} disabled={working || !state?.has_next || !replay.ready || state.status === 'running'}>▶</button>
          <button type="button" aria-label="Pause" title="Pause" onClick={() => void control('pause')} disabled={working || state?.status !== 'running'}>Ⅱ</button>
          <button type="button" aria-label="Stop replay" title="Stop replay" onClick={() => void control('stop')} disabled={working || !state}>■</button>
          <button type="button" aria-label="Step one visible bar" title="Step one candle" onClick={() => void control('step')} disabled={working || !state?.has_next || !replay.ready || state.status === 'running'}>▹│</button>
          <select aria-label="Playback speed" value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
            {[0.5, 1, 2, 4].map((value) => <option value={value} key={value}>{value}×</option>)}
          </select>
        </div>
        {state && <><p className="replay-status" aria-live="polite">Replay {state.status} · {state.instrument_id} · {state.timeframe} · cursor {state.cursor_index}</p>
          <div className="secondary-actions"><button type="button" onClick={() => void control('next-event')} disabled={working || !replay.ready || !state.has_next || state.status === 'running'}>Next detector event</button>
            <button type="button" onClick={() => void control('reset')} disabled={working}>Reset replay</button></div></>}
        {focusTime && <div className="focus-notice">Viewing {displayTime(focusTime, tab.timezone)}
          <button type="button" onClick={() => {
            if (state) returnToCursor(); else { setFocusTime(null); setNavigationNote(null); }
          }}>{state ? 'Return to replay cursor' : 'Clear chart focus'}</button></div>}
        {navigation.busy && <button type="button" onClick={navigation.cancel}>Cancel navigation</button>}
        {navigation.failed && <button type="button" onClick={() => { setError(null); void navigation.retry(); }}>Retry navigation</button>}
        {state && !replay.ready && <p role="status">{replay.window ? 'Loading recorded replay history…' : 'Updating chart to replay position…'}</p>}
        {state && replay.window && !focusTime && <button type="button" onClick={returnToCursor}>Return to replay cursor</button>}
        {state && replay.window && replay.ready && <p role="status">Browsing recorded history independently of the replay cursor.</p>}
        {state && replay.snapshot?.coverage === 'initialization_unavailable' && <p role="status">This context precedes detector initialization.</p>}
        {replay.readError && <p role="alert">Chart update failed: {replay.readError} <button type="button" onClick={replay.retrySnapshot}>Retry chart update</button></p>}
        {error && <p role="alert" className="inline-error">{error}<button type="button" aria-label="Dismiss error" onClick={() => setError(null)}>×</button></p>}
      </div>
      <PanelTabs id={tab.id} panel={panel} count={projectOccurrences(events, periodStart ?? '', periodEnd ?? '').length} onChange={setPanel} />
      <div className="panel-body" id={`panel-${tab.id}`} role="tabpanel" aria-labelledby={`${panel}-${tab.id}`}>
        {panel === 'patterns' ? <>
          {!state && <p role="status">{historical.busy ? 'Calculating historical detections…' : historical.loaded ? 'Historical detections loaded for this view.' : historical.status === 'partial' ? 'Partially calculated: earlier candles lack sufficient initialization history.' : historical.status === 'initialization_unavailable' ? 'Insufficient initialization history for this analytical timeframe.' : `Use Detector settings to calculate this view. ${historical.coverage}`}</p>}
          <PatternGroups events={events} start={periodStart ?? ''} end={periodEnd ?? ''} timeframe={chartTimeframe}
            loaded={Boolean(state) || historical.loaded} selected={selectedOrder} annotations={annotations} timezone={tab.timezone}
            onSelect={selectEvent} onInspect={inspect} onDismiss={dismissPreview} />
          {selectedEvent && !state && <EventReview runId={selectedEvent.run_id} event={selectedEvent} annotations={annotations}
            onSaved={(record) => setAnnotations((current) => [...current.filter((item) => item.annotation_id !== record.annotation_id), record])} />}
          {state && <>
            {selectedEvent && <EventReview runId={selectedEvent.run_id} event={selectedEvent} annotations={annotations}
              onSaved={(record) => setAnnotations((current) => [...current.filter((item) => item.annotation_id !== record.annotation_id), record])} />}
            <details className="review-tools"><summary>Record a missed pattern</summary>
              <MissedPatternReview targets={state.initialization} chartTimeframe={chartTimeframe} runId={state.run_id} cursorTime={state.cursor_time} selectedInterval={reviewInterval}
                patterns={patterns} annotations={annotations} timezone={tab.timezone}
                onSaved={(record) => setAnnotations((current) => [...current.filter((item) => item.annotation_id !== record.annotation_id), record])} />
            </details>
            {state.navigation && <p role="status">{state.navigation.stopped_on_event
              ? `Stopped on ${state.navigation.matched_event?.trigger_id} after ${state.navigation.processed_bars} bars`
              : `No further detector event; reached end after ${state.navigation.processed_bars} bars`}</p>}
            {evidence.observations.at(-1) && <details><summary>Current market state</summary><p>Current market state: {Object.entries(evidence.observations.at(-1)!.availability).map(([name, status]) => `${name} ${status}`).join(' · ')}</p></details>}
          </>}
        </> : <>
          <AnalysisSettings streams={settings.drafts} patterns={patterns} onAddDetector={settings.addDetector} disabled={Boolean(state) || working || historical.busy}
            dirty={settings.dirty} onChange={settings.setDrafts} onAdd={settings.add} onApply={() => { setError(null); void settings.apply(); }}
            onCalculate={(tf, pattern) => {
              if (rawPrices && !providerInput.source) { setError('Preparing provider candles for detectors. Please try again shortly.'); return; }
              if (!periodStart || !periodEnd) { setError('No completed candles are available in this view. Select a shorter chart timeframe or load more history.'); return; }
              setError(null); void historical.calculate(tf, pattern);
            }} />
          <button type="button" disabled={Boolean(state) || working} onClick={() => void settings.restore()}>Restore default settings</button>
          {state && <p className="availability-note">Stop replay to change its detector settings.</p>}
          <details><summary>Chart layers</summary><div className="overlay-controls">{(Object.keys(layers) as Array<keyof OverlayVisibility>).map((key) => <label key={key}>
            <input type="checkbox" checked={layers[key]} onChange={(event) => setLayers({ ...layers, [key]: event.target.checked })} />
            {key === 'ema' ? 'EMA' : key === 'trend' ? 'Trend legs' : key === 'swing' ? 'Swings and structure' : key === 'range' ? 'Range/compression' : 'Sessions'}</label>)}</div></details>
        </>}
        <details className="source-settings"><summary>Source & run information</summary>
          <label className="input-label">Dataset revision <select aria-label="Dataset revision" value={selectedId} onChange={(event) => chooseSource(event.target.value)} disabled={Boolean(state) || working}>
            {sources.filter((source) => source.instrument_id === (selected?.instrument_id ?? tab.instrument)).map((source) => <option key={source.dataset_revision_id} value={source.dataset_revision_id}>{source.dataset_revision_id}</option>)}
          </select></label>
          <label className="input-label">Display timezone <select aria-label="Display timezone" value={tab.timezone} onChange={(event) => chooseTimezone(event.target.value as DisplayTimezone)}><option value="Europe/London">UK · GMT / BST</option><option value="UTC">UTC</option></select></label>
          <label className="input-label">Replay end (exclusive) <input aria-label="Replay end" type="datetime-local" value={!state && !selected?.non_research_grade && !endExplicit ? '' : end} disabled={Boolean(state) || working} onChange={(event) => { setEnd(event.target.value); setEndExplicit(Boolean(event.target.value)); }} /></label>
          {!state && !selected?.non_research_grade && <p>Leave blank to use the latest available completed history.</p>}
          {selected && <p>{selected.bar_count} published bars · {selected.non_research_grade ? 'Synthetic demo only — not research-grade' : 'Provider history'}</p>}
          <dl><dt>Run ID</dt><dd><code>{state?.run_id ?? 'Not started'}</code></dd><dt>Source</dt><dd><code>{selected?.source_dataset_id}</code></dd>
            <dt>Dataset checksum</dt><dd><code>{selected?.canonical_checksum}</code></dd><dt>Config hash</dt><dd><code>{state?.detection_config_hash ?? (settings.dirty ? 'Changes need Apply' : 'Settings applied')}</code></dd>
            <dt>Warm-up</dt><dd>{state ? `${state.warmup_processed} / ${state.warmup_required}` : 'Per analytical timeframe'} bars</dd></dl>
        </details>
      </div>
    </aside>
    {details && <div className="evidence-popover" ref={popover} role="dialog" aria-label="Pattern details" tabIndex={-1}
      onMouseEnter={() => { if (closePreview.current) clearTimeout(closePreview.current); }} onMouseLeave={dismissPreview}
      onKeyDown={(event) => { if (event.key === 'Escape') closeDetails(); }}>
      <div className="popover-heading"><span>{pinned ? 'Pinned evidence' : 'Evidence preview'}</span><button type="button" aria-label="Close pattern details" onClick={closeDetails}>×</button></div>
      <EventInspector event={details} timezone={tab.timezone} />
    </div>}
  </section>;
}
