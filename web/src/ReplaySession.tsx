import { useEffect, useMemo, useRef, useState } from 'react';
import { validateReplayState } from './validation';
import { useHistoryWindow } from './useHistoryWindow';
import { PanelTabs } from './PanelTabs';
import { useAnalysisSettings } from './useAnalysisSettings';
import { AnalysisSettings } from './AnalysisSettings';
import { useHistoricalAnalysis } from './useHistoricalAnalysis';
import { PatternGroups, projectOccurrences } from './PatternGroups';
import { useReplayController } from './useReplayController';
import { CandlestickChart, type OverlayVisibility } from './CandlestickChart';
import { EventInspector } from './EventInspector';
import { EventReview, MissedPatternReview } from './ReviewAnnotations';
import { apiJson, TIMEFRAMES, type Timeframe, type ReplayBar,
  type ReplayDetectorEvent, type ReplayObservation, type ReplaySource,
  type ReplayState, type ReviewPattern } from './replay';
import { dateTimeInput, displayTime, inputInstant, type DisplayTimezone } from './time';
import { preferredSource, type AnalysisTab } from './workspace';

interface Props {
  tab: AnalysisTab;
  active: boolean;
  sources: ReplaySource[];
  patterns: ReviewPattern[];
  activeRun: ReplayState | null;
  onTabChange: (id: string, patch: Partial<AnalysisTab>) => void;
  onRunChange: (id: string, state: ReplayState | null) => void;
}
const defaultLayers: OverlayVisibility = { ema: true, trend: true, swing: true, range: true, session: true };
const emptyBars: ReplayBar[] = [];
const emptyObservations: ReplayObservation[] = [];
const emptyEvents: ReplayDetectorEvent[] = [];
const errorMessage = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

export function ReplaySession({ tab, active, sources, patterns, activeRun, onTabChange, onRunChange }: Props) {
  const restored = useRef(tab);
  const initialRun = activeRun?.run_id === tab.runId ? activeRun : null;
  const [selectedId, setSelectedId] = useState(initialRun?.dataset_revision_id
    ?? (sources.some((source) => source.dataset_revision_id === tab.sourceId) ? tab.sourceId
      : preferredSource(sources, tab.instrument)?.dataset_revision_id ?? ''));
  const selected = sources.find((source) => source.dataset_revision_id === selectedId);
  const [start, setStart] = useState(tab.start ?? (selected ? dateTimeInput(selected.suggested_start, tab.timezone) : ''));
  const [end, setEnd] = useState(tab.end ?? (selected ? dateTimeInput(selected.suggested_end, tab.timezone) : ''));
  const [chartTimeframe, setChartTimeframe] = useState<Timeframe>(tab.chartTimeframe ?? '1m');
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
    (next) => onRunChange(tab.id, next), (stop) => {
      if (stop) setHistory(visible);
      clearRunView(!stop);
    }, setError);
  const { state, stateRef, setState, control, annotations, setAnnotations } = replay;
  const chartBars = replay.snapshot?.run_id === state?.run_id && (!state?.analysis_session || replay.snapshot?.timeframe === chartTimeframe) ? replay.snapshot?.bars ?? emptyBars : emptyBars;
  const observations = replay.snapshot?.run_id === state?.run_id && (!state?.analysis_session || replay.snapshot?.timeframe === chartTimeframe) ? replay.snapshot?.observations ?? emptyObservations : emptyObservations;
  const replayEvents = replay.snapshot?.run_id === state?.run_id && (!state?.analysis_session || replay.snapshot?.timeframe === chartTimeframe) ? replay.snapshot?.events ?? emptyEvents : emptyEvents;
  const { history, setHistory, loadingHistory } = useHistoryWindow(active, selected,
    state?.selected_start ?? selected?.range_end, setError, chartTimeframe);
  const working = busy || replay.busy || settings.busy;
  const visibleHistory = useMemo(() => state ? history.filter((bar) => bar.timestamp < state.selected_start) : history,
    [history, state]);
  const visible = useMemo(() => state ? state.analysis_session ? chartBars : [...visibleHistory, ...chartBars] : history, [state, visibleHistory, chartBars, history]);
  const cursor = state ? (state.analysis_session ? chartBars.at(-1)?.timestamp : replay.snapshot?.cursor_time) ?? visibleHistory.at(-1)?.timestamp ?? null : history.at(-1)?.timestamp ?? null;

  const periodStart = viewedPeriod?.start ?? visible[0]?.timestamp ?? null;
  const periodEnd = viewedPeriod?.end ?? visible.at(-1)?.available_at
    ?? (visible.at(-1) ? new Date(Date.parse(visible.at(-1)!.timestamp) + 60000).toISOString() : null);
  const historical = useHistoricalAnalysis(selected, settings.applied, chartTimeframe,
    periodStart, periodEnd, active && !state, setError);
  const events = state ? replayEvents : historical.events;
  const selectedEvent = events.find((event) => event.event_id === selectedEventId);
  const selectedOrder = selectedEvent?.emission_order ?? null;
  const chartEvents = useMemo(() => events.filter((event) => (event.timeframe === chartTimeframe || event.emission_order === selectedOrder) && (!periodEnd || Date.parse(event.available_at ?? event.detection_time) <= Date.parse(periodEnd))), [events, chartTimeframe, selectedOrder, periodEnd]);

  const startupRun = useRef(initialRun);
  useEffect(() => {
    if (restored.current.runId && !startupRun.current) onTabChange(tab.id, { runId: null });
  }, [onTabChange, tab.id]);

  useEffect(() => {
    if (!selected) return;
    onTabChange(tab.id, { sourceId: selectedId, instrument: selected.instrument_id, start, end,
      streams: settings.drafts.length ? settings.drafts : undefined, appliedStreams: settings.applied ?? undefined, chartTimeframe });
  }, [selectedId, start, end, settings.drafts, settings.applied, chartTimeframe, onTabChange, tab.id, selected]);

  function clearRunView(resetViewport = true) {
    replay.clearSnapshot();
    setSelectedEventId(null); setFocusTime(null); setReviewAnchor(null); setReviewInterval(null);
    setDetails(null); setPinned(false);
    if (resetViewport) viewport.current = null;
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

  async function launch() {
    if (!selected || settings.dirty || !settings.applied || state) return;
    setBusy(true); setError(null);
    try {
      const next = validateReplayState(await apiJson<ReplayState>('/analysis', { method: 'POST', body: JSON.stringify({
        dataset_revision_id: selectedId, instrument_id: selected.instrument_id, chart_timeframe: chartTimeframe,
        selected_start: inputInstant(start, tab.timezone), selected_end: inputInstant(end, tab.timezone),
        streams: settings.applied,
      }) }));
      clearRunView(); setState(next);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  }

  function moveTo() {
    try {
      const instant = inputInstant(start, tab.timezone);
      const target = visible.find((bar) => Date.parse(bar.timestamp) === Date.parse(instant))?.timestamp;
      if (!target) throw new Error(state
        ? 'Choose a candle already revealed by this replay.' : 'This time is outside the loaded local history.');
      setError(null); setFocusTime(target); setFocusVersion((version) => version + 1);
    } catch (reason) { setError(errorMessage(reason)); }
  }

  function chooseSource(id: string) {
    if (saveViewport.current) clearTimeout(saveViewport.current);
    onTabChange(tab.id, { viewport: undefined });
    const source = sources.find((item) => item.dataset_revision_id === id);
    if (source) {
      setStart(dateTimeInput(source.suggested_start, tab.timezone));
      setEnd(dateTimeInput(source.suggested_end, tab.timezone));
    }
    setSelectedId(id); setHistory([]); setViewedPeriod(null); clearRunView(); setError(null);
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
    if (!state || timestamp < state.selected_start) return;
    if (!reviewAnchor) {
      setReviewAnchor(timestamp);
      setReviewInterval({ start: timestamp, end: new Date(Date.parse(timestamp) + 60_000).toISOString() });
    } else {
      setReviewInterval({ start: new Date(Math.min(Date.parse(reviewAnchor), Date.parse(timestamp))).toISOString(),
        end: new Date(Math.max(Date.parse(reviewAnchor), Date.parse(timestamp)) + 60_000).toISOString() });
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
          setChartTimeframe(event.target.value as Timeframe); setViewedPeriod(null); setHistory([]);
        }}>{TIMEFRAMES.map((tf) => <option key={tf} value={tf} disabled={Boolean(state && !state.analysis_session && tf !== '1m')}>{tf === '1d' ? '1D' : tf}</option>)}</select>
        <span className={`source-badge ${selected?.non_research_grade ? 'synthetic' : ''}`}>{selected?.non_research_grade ? 'Synthetic data' : 'Local history'}</span>
      </div>
      {loadingHistory && <span className="history-loading" role="status">Loading candles…</span>}
      <CandlestickChart key={`${selectedId}:${state?.run_id ?? 'history'}:${chartTimeframe}`} resetKey={`${selectedId}:${state?.run_id ?? 'history'}:${chartTimeframe}`}
        bars={visible} observations={state ? observations : []} events={chartEvents}
        selectedEventOrder={selectedOrder} onSelectEvent={(order) => { const event = events.find((item) => item.emission_order === order); if (event) selectEvent(event); }}
        onSelectBar={state && replay.ready ? selectBar : undefined} reviewInterval={reviewInterval}
        cursorTime={cursor} focusTime={focusTime} overlays={layers} timezone={tab.timezone}
        focusVersion={focusVersion} onReturnLatest={() => setFocusTime(null)}
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
      <div className="source-footer"><span>{selected?.instrument_id} · {chartTimeframe} · {state ? `${state.visible_bars} replay bars revealed` : `${history.length} local candles`}</span>
        <span>{tab.timezone === 'Europe/London' ? 'UK time · GMT / BST' : 'UTC'}{selected?.non_research_grade ? ' · Not research-grade' : ''}</span></div>
    </div>
    <aside className="workspace-panel" aria-label="Analysis controls">
      <div className="panel-controls">
        <div className="panel-heading"><h2>{state ? 'Historical replay' : 'Historical exploration'}</h2><span className="connection-status">LOCAL</span></div>
        <div className="mode-control" aria-label="Analysis mode"><button type="button" className="selected">History</button>
          <button type="button" disabled title="Live market connections are not available in this build">Live <span>Coming next</span></button></div>
        <label className="input-label">Go to date & time <input aria-label="Chart date and time" type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} /></label>
        <div className="navigation-actions"><button type="button" onClick={moveTo} disabled={working || !start}>Move to</button>
          <button className="primary" type="button" onClick={() => void launch()} disabled={working || Boolean(state) || !selected || settings.dirty}>Start replay from</button></div>
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
          <button type="button" onClick={() => setFocusTime(null)}>Return to replay cursor</button></div>}
        {state && !replay.ready && <p role="status">Updating chart to replay position…</p>}
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
              <MissedPatternReview runId={state.initialization?.[0]?.run_id ?? state.run_id} cursorTime={state.cursor_time} selectedInterval={reviewInterval}
                patterns={patterns} annotations={annotations} timezone={tab.timezone}
                onSaved={(record) => setAnnotations((current) => [...current.filter((item) => item.annotation_id !== record.annotation_id), record])} />
            </details>
            {state.navigation && <p role="status">{state.navigation.stopped_on_event
              ? `Stopped on ${state.navigation.matched_event?.trigger_id} after ${state.navigation.processed_bars} bars`
              : `No further detector event; reached end after ${state.navigation.processed_bars} bars`}</p>}
            {observations.at(-1) && <details><summary>Current market state</summary><p>Current market state: {Object.entries(observations.at(-1)!.availability).map(([name, status]) => `${name} ${status}`).join(' · ')}</p></details>}
          </>}
        </> : <>
          <AnalysisSettings streams={settings.drafts} patterns={patterns} onAddDetector={settings.addDetector} disabled={Boolean(state) || working || historical.busy}
            dirty={settings.dirty} onChange={settings.setDrafts} onAdd={settings.add} onApply={() => { setError(null); void settings.apply(); }}
            onCalculate={(tf, pattern) => {
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
          <label className="input-label">Replay end (exclusive) <input aria-label="Replay end" type="datetime-local" value={end} disabled={Boolean(state)} onChange={(event) => setEnd(event.target.value)} /></label>
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
