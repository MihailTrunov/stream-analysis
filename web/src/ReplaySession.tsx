import { useEffect, useRef, useState } from 'react';
import { CandlestickChart, type OverlayVisibility } from './CandlestickChart';
import { EventTimeline } from './EventTimeline';
import { EventInspector } from './EventInspector';
import { EventReview, MissedPatternReview } from './ReviewAnnotations';
import { apiJson, type DetectionConfig, type ReplayBar, type ReplayConfig,
  type ReplayDetectorEvent, type ReplayObservation, type ReplaySource,
  type ReplayState, type ReplayViewResponse, type ReviewPattern, type ValidationAnnotation } from './replay';
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
const errorMessage = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

export function ReplaySession({ tab, active, sources, patterns, activeRun, onTabChange, onRunChange }: Props) {
  const restored = useRef(tab);
  const initialRun = activeRun?.run_id === tab.runId ? activeRun : null;
  const draftConfig = useRef<DetectionConfig | null>(initialRun?.detection_config ?? tab.config ?? null);
  const [selectedId, setSelectedId] = useState(initialRun?.dataset_revision_id
    ?? (sources.some((source) => source.dataset_revision_id === tab.sourceId) ? tab.sourceId
      : preferredSource(sources, tab.instrument)?.dataset_revision_id ?? ''));
  const selected = sources.find((source) => source.dataset_revision_id === selectedId);
  const [state, setState] = useState<ReplayState | null>(initialRun);
  const stateRef = useRef(state);
  stateRef.current = state;
  const [start, setStart] = useState(tab.start ?? '');
  const [end, setEnd] = useState(tab.end ?? '');
  const [config, setConfig] = useState<ReplayConfig | null>(null);
  const [editedConfig, setEditedConfig] = useState<DetectionConfig | null>(null);
  const [previewHash, setPreviewHash] = useState<string | null>(null);
  const [history, setHistory] = useState<ReplayBar[]>([]);
  const [chartBars, setChartBars] = useState<ReplayBar[]>([]);
  const [observations, setObservations] = useState<ReplayObservation[]>([]);
  const [events, setEvents] = useState<ReplayDetectorEvent[]>([]);
  const [annotations, setAnnotations] = useState<ValidationAnnotation[]>([]);
  const [selectedOrder, setSelectedOrder] = useState<number | null>(null);
  const [focusTime, setFocusTime] = useState<string | null>(null);
  const [focusVersion, setFocusVersion] = useState(0);
  const [reviewAnchor, setReviewAnchor] = useState<string | null>(null);
  const [reviewInterval, setReviewInterval] = useState<{ start: string; end: string } | null>(null);
  const [layers, setLayers] = useState(defaultLayers);
  const [panel, setPanel] = useState<'patterns' | 'settings'>('patterns');
  const [speed, setSpeed] = useState(1);
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [error, setError] = useState<string | null>(tab.runId && !initialRun
    ? 'The previous replay is no longer active. Its saved chart settings are restored; start a new replay to continue.' : null);
  const [details, setDetails] = useState<ReplayDetectorEvent | null>(null);
  const [pinned, setPinned] = useState(false);
  const closePreview = useRef<ReturnType<typeof setTimeout> | null>(null);
  const popover = useRef<HTMLDivElement>(null);
  const viewport = useRef<AnalysisTab['viewport'] | null>(tab.viewport ?? null);
  const saveViewport = useRef<ReturnType<typeof setTimeout> | null>(null);
  const queue = useRef<Promise<void>>(Promise.resolve());
  const otherReplay = activeRun && activeRun.run_id !== state?.run_id;
  const selectedEvent = events.find((event) => event.emission_order === selectedOrder);
  const visibleHistory = state ? history.filter((bar) => bar.timestamp < state.selected_start) : history;
  const visible = state ? [...visibleHistory, ...chartBars] : history;
  const cursor = state ? state.cursor_time ?? visibleHistory.at(-1)?.timestamp ?? null : history.at(-1)?.timestamp ?? null;

  useEffect(() => {
    if (tab.runId && !initialRun) onTabChange(tab.id, { runId: null });
  }, []);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    const saved = restored.current.sourceId === selectedId;
    if (!saved || !restored.current.start) setStart(dateTimeInput(selected.suggested_start, tab.timezone));
    if (!saved || !restored.current.end) setEnd(dateTimeInput(selected.suggested_end, tab.timezone));
    setConfig(null); setEditedConfig(null); setPreviewHash(null);
    void apiJson<ReplayConfig>(`/replay/config-default?instrument_id=${selected.instrument_id}`, { signal: controller.signal })
      .then(async (value) => {
        if (controller.signal.aborted) return;
        setConfig(value);
        const previous = draftConfig.current;
        const editable = previous?.instrument_id === selected.instrument_id ? previous : value.detection_config;
        const validation = await apiJson<{ detection_config_hash: string }>('/config/preview', {
          method: 'POST', body: JSON.stringify(editable), signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setConfig(value); setEditedConfig(editable); setPreviewHash(validation.detection_config_hash);
      }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(errorMessage(reason)); });
    return () => controller.abort();
  }, [selectedId]);

  useEffect(() => {
    if (!selected) return;
    if (editedConfig) draftConfig.current = editedConfig;
    onTabChange(tab.id, { sourceId: selectedId, instrument: selected.instrument_id, start, end,
      ...(editedConfig ? { config: editedConfig } : {}) });
  }, [selectedId, start, end, editedConfig, onTabChange, tab.id, selected]);

  useEffect(() => {
    if (!active || !selected) return;
    const controller = new AbortController();
    const previewEnd = state?.selected_start ?? selected.range_end;
    if (previewEnd <= selected.range_start) { setHistory([]); return; }
    setLoadingHistory(true);
    const params = new URLSearchParams({ instrument_id: selected.instrument_id,
      start: selected.range_start, end: previewEnd, limit: '500' });
    void apiJson<{ bars: ReplayBar[] }>(`/replay/sources/${encodeURIComponent(selectedId)}/bars?${params}`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setHistory(result.bars); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) { setHistory([]); setError(errorMessage(reason)); } })
      .finally(() => { if (!controller.signal.aborted) setLoadingHistory(false); });
    return () => controller.abort();
  }, [active, selectedId, state?.run_id]);

  useEffect(() => {
    if (!active || !state) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ start: state.selected_start, end: state.selected_end, limit: '500' });
    void apiJson<ReplayViewResponse>(`/replay/${state.run_id}/view?${params}`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) {
        setChartBars(result.bars); setObservations(result.observations); setEvents(result.events);
      } }).catch((reason: unknown) => { if (!controller.signal.aborted) setError(errorMessage(reason)); });
    void apiJson<{ annotations: ValidationAnnotation[] }>(`/replay/${state.run_id}/annotations`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) setAnnotations(result.annotations); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(errorMessage(reason)); });
    return () => controller.abort();
  }, [active, state?.run_id, state?.cursor_index]);

  function clearRunView(resetViewport = true) {
    setChartBars([]); setObservations([]); setEvents([]); setAnnotations([]);
    setSelectedOrder(null); setFocusTime(null); setReviewAnchor(null); setReviewInterval(null);
    setDetails(null); setPinned(false);
    if (resetViewport) viewport.current = null;
  }

  function control(action: 'play' | 'pause' | 'step' | 'tick' | 'reset' | 'next-event' | 'stop') {
    setBusy(true);
    queue.current = queue.current.then(async () => {
      const current = stateRef.current;
      if (!current) return;
      try {
        const next = await apiJson<ReplayState>(`/replay/${current.run_id}/${action}`, { method: 'POST' });
        if (action === 'stop') { setHistory(visible); clearRunView(false); }
        if (action === 'reset') clearRunView();
        stateRef.current = action === 'stop' ? null : next;
        setState(stateRef.current); onRunChange(tab.id, stateRef.current);
      } catch (reason) { setError(errorMessage(reason)); }
    }).finally(() => setBusy(false));
    return queue.current;
  }

  useEffect(() => {
    if (!active && state?.status === 'running') void control('pause');
  }, [active, state?.status]);

  useEffect(() => {
    const pauseHidden = () => { if (document.hidden && stateRef.current?.status === 'running') void control('pause'); };
    document.addEventListener('visibilitychange', pauseHidden);
    return () => document.removeEventListener('visibilitychange', pauseHidden);
  }, []);

  useEffect(() => {
    if (!active || busy || state?.status !== 'running' || !state.has_next) return;
    const timer = window.setTimeout(() => void control('tick'), 1000 / speed);
    return () => window.clearTimeout(timer);
  }, [active, busy, state?.status, state?.cursor_index, state?.has_next, speed]);

  useEffect(() => () => {
    if (closePreview.current) clearTimeout(closePreview.current);
    if (saveViewport.current) clearTimeout(saveViewport.current);
  }, []);

  useEffect(() => {
    if (!pinned) return;
    if (closePreview.current) clearTimeout(closePreview.current);
    popover.current?.focus();
  }, [pinned, details?.event_id]);

  async function preview() {
    if (!editedConfig) return;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<{ detection_config_hash: string }>('/config/preview', {
        method: 'POST', body: JSON.stringify(editedConfig),
      });
      setPreviewHash(result.detection_config_hash);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  }

  async function launch() {
    if (!selected || !config || !editedConfig || !previewHash || state) return;
    setBusy(true); setError(null);
    try {
      const next = await apiJson<ReplayState>('/replay', { method: 'POST', body: JSON.stringify({
        dataset_revision_id: selectedId, instrument_id: selected.instrument_id, timeframe: '1m',
        selected_start: inputInstant(start, tab.timezone), selected_end: inputInstant(end, tab.timezone),
        config_id: config.config_id, config_version: config.config_version, detection_config: editedConfig,
      }) });
      clearRunView(); stateRef.current = next; setState(next); onRunChange(tab.id, next);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  }

  function moveTo() {
    try {
      const target = inputInstant(start, tab.timezone);
      if (!visible.some((bar) => bar.timestamp === target)) throw new Error(state
        ? 'Choose a candle already revealed by this replay.' : 'This time is outside the loaded local history.');
      setFocusTime(target); setFocusVersion((version) => version + 1);
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
    setSelectedId(id); setHistory([]); clearRunView(); setError(null);
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
    setSelectedOrder(event.emission_order); setFocusTime(event.detection_time);
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
    if (closePreview.current) clearTimeout(closePreview.current);
    if (pinned && !pin) return;
    setDetails(event); setPinned(pin);
  }
  function dismissPreview() {
    if (!pinned) closePreview.current = setTimeout(() => setDetails(null), 200);
  }

  if (!active) return <section hidden role="tabpanel" id={`session-${tab.id}`} aria-labelledby={`tab-${tab.id}`} />;
  return <section className="analysis-session" role="tabpanel" id={`session-${tab.id}`} aria-labelledby={`tab-${tab.id}`}>
    <div className="chart-stage">
      <div className="chart-overlay">
        <label className="sr-only" htmlFor={`instrument-${tab.id}`}>Instrument</label>
        <select id={`instrument-${tab.id}`} aria-label="Instrument" value={selected?.instrument_id ?? tab.instrument} disabled={Boolean(state) || busy}
          onChange={(event) => { const source = preferredSource(sources, event.target.value); if (source) chooseSource(source.dataset_revision_id); }}>
          <option value="US30">US30</option><option value="DAX">DAX</option>
        </select>
        <select aria-label="Chart timeframe" value="1m">
          <option value="1m">1m</option>{['5m', '15m', '30m', '1h', '4h', '1D'].map((value) => <option disabled key={value}>{value} · coming next</option>)}
        </select>
        <span className={`source-badge ${selected?.non_research_grade ? 'synthetic' : ''}`}>{selected?.non_research_grade ? 'Synthetic data' : 'Local history'}</span>
      </div>
      {loadingHistory && <span className="history-loading" role="status">Loading candles…</span>}
      <CandlestickChart key={`${selectedId}:${state?.run_id ?? 'history'}`} resetKey={`${selectedId}:${state?.run_id ?? 'history'}`}
        bars={visible} observations={state ? observations : []} events={state ? events : []}
        selectedEventOrder={selectedOrder} onSelectEvent={(order) => { const event = events.find((item) => item.emission_order === order); if (event) selectEvent(event); }}
        onSelectBar={state?.cursor_time && chartBars.at(-1)?.timestamp === state.cursor_time ? selectBar : undefined} reviewInterval={reviewInterval}
        cursorTime={cursor} focusTime={focusTime} overlays={layers} timezone={tab.timezone}
        focusVersion={focusVersion} onReturnLatest={() => setFocusTime(null)}
        initialRange={viewport.current?.origin === visible[0]?.timestamp ? viewport.current : null}
        onRangeChange={(value) => {
          if (!visible.length) return;
          const saved = { ...value, origin: visible[0].timestamp };
          if (viewport.current?.from === saved.from && viewport.current.to === saved.to
            && viewport.current.origin === saved.origin) return;
          viewport.current = saved;
          if (saveViewport.current) clearTimeout(saveViewport.current);
          saveViewport.current = setTimeout(() => onTabChange(tab.id, { viewport: saved }), 200);
        }} />
      <div className="source-footer"><span>{selected?.instrument_id} · {selected?.timeframe} · {state ? `${state.visible_bars} replay bars revealed` : `${history.length} local candles`}</span>
        <span>{tab.timezone === 'Europe/London' ? 'UK time · GMT / BST' : 'UTC'}{selected?.non_research_grade ? ' · Not research-grade' : ''}</span></div>
    </div>
    <aside className="workspace-panel" aria-label="Analysis controls">
      <div className="panel-controls">
        <div className="panel-heading"><h2>{state ? 'Historical replay' : 'Historical exploration'}</h2><span className="connection-status">LOCAL</span></div>
        <div className="mode-control" aria-label="Analysis mode"><button type="button" className="selected">History</button>
          <button type="button" disabled title="Live market connections are not available in this build">Live <span>Coming next</span></button></div>
        <label className="input-label">Go to date & time <input aria-label="Chart date and time" type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} /></label>
        <div className="navigation-actions"><button type="button" onClick={moveTo} disabled={busy || !start}>Move to</button>
          <button className="primary" type="button" onClick={() => void launch()} disabled={busy || Boolean(state) || Boolean(otherReplay) || !selected || !previewHash}>Start replay from</button></div>
        {otherReplay && <p className="availability-note">Another tab has a replay. Stop it there before starting another.</p>}
        <div className="transport" aria-label="Replay controls">
          <button type="button" aria-label="Play" title="Play" onClick={() => void control('play')} disabled={busy || !state?.has_next || state.status === 'running'}>▶</button>
          <button type="button" aria-label="Pause" title="Pause" onClick={() => void control('pause')} disabled={busy || state?.status !== 'running'}>Ⅱ</button>
          <button type="button" aria-label="Stop replay" title="Stop replay" onClick={() => void control('stop')} disabled={busy || !state}>■</button>
          <button type="button" aria-label="Step one visible bar" title="Step one candle" onClick={() => void control('step')} disabled={busy || !state?.has_next || state.status === 'running'}>▹│</button>
          <select aria-label="Playback speed" value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
            {[0.5, 1, 2, 4].map((value) => <option value={value} key={value}>{value}×</option>)}
          </select>
        </div>
        {state && <><p className="replay-status" aria-live="polite">Replay {state.status} · {state.instrument_id} · {state.timeframe} · cursor {state.cursor_index}</p>
          <div className="secondary-actions"><button type="button" onClick={() => void control('next-event')} disabled={busy || !state.has_next || state.status === 'running'}>Next detector event</button>
            <button type="button" onClick={() => void control('reset')} disabled={busy}>Reset replay</button></div></>}
        {focusTime && <div className="focus-notice">Viewing {displayTime(focusTime, tab.timezone)}
          <button type="button" onClick={() => setFocusTime(null)}>Return to replay cursor</button></div>}
        {error && <p role="alert" className="inline-error">{error}<button type="button" aria-label="Dismiss error" onClick={() => setError(null)}>×</button></p>}
      </div>
      <div className="panel-tabs" role="tablist" aria-label="Analysis panel">
        <button type="button" role="tab" id={`patterns-${tab.id}`} aria-selected={panel === 'patterns'} onClick={() => setPanel('patterns')}>Patterns <span>{events.length}</span></button>
        <button type="button" role="tab" id={`settings-${tab.id}`} aria-selected={panel === 'settings'} onClick={() => setPanel('settings')}>Detector settings</button>
      </div>
      <div className="panel-body" role="tabpanel" aria-labelledby={`${panel}-${tab.id}`}>
        {panel === 'patterns' ? <>
          {!state && <div className="panel-empty"><span className="empty-symbol" aria-hidden="true">⌁</span><h3>Explore first. Replay when ready.</h3>
            <p>Browse the loaded candles, choose a starting point, then step or play to see detector events.</p>
            <p className="quiet">On-demand historical detection will be available in a later increment.</p></div>}
          <EventTimeline events={state ? events : []} selectedEventOrder={selectedOrder} onSelectEvent={selectEvent}
            annotations={annotations} timezone={tab.timezone} onInspect={inspect} onDismissPreview={dismissPreview} />
          {state && <>
            {selectedEvent && <EventReview runId={state.run_id} event={selectedEvent} annotations={annotations}
              onSaved={(record) => setAnnotations((current) => [...current.filter((item) => item.annotation_id !== record.annotation_id), record])} />}
            <details className="review-tools"><summary>Record a missed pattern</summary>
              <MissedPatternReview runId={state.run_id} cursorTime={state.cursor_time} selectedInterval={reviewInterval}
                patterns={patterns} annotations={annotations}
                onSaved={(record) => setAnnotations((current) => [...current.filter((item) => item.annotation_id !== record.annotation_id), record])} />
            </details>
            {state.navigation && <p role="status">{state.navigation.stopped_on_event
              ? `Stopped on ${state.navigation.matched_event?.trigger_id} after ${state.navigation.processed_bars} bars`
              : `No further detector event; reached end after ${state.navigation.processed_bars} bars`}</p>}
            {observations.at(-1) && <details><summary>Current market state</summary><p>Current market state: {Object.entries(observations.at(-1)!.availability).map(([name, status]) => `${name} ${status}`).join(' · ')}</p></details>}
          </>}
        </> : <>
          <div className="settings-heading"><span className="timeframe-label">1 minute</span><span className="quiet">Analytical timeframe</span></div>
          <p className="availability-note">Additional analytical timeframes and automatic historical detection are not available yet.</p>
          {editedConfig && <fieldset disabled={Boolean(state) || busy} className="detector-configuration">
            <legend>Detectors & components</legend>
            {(['patterns', 'components'] as const).map((group) => editedConfig[group].map((selection, index) => <details key={`${group}-${index}`} open={group === 'patterns'}>
              <summary>{selection.pattern_id ?? selection.component_id} <span>v{String(selection.pattern_version ?? selection.component_version ?? '1')}</span></summary>
              <label><input type="checkbox" checked={selection.enabled !== false} onChange={(event) => {
                setEditedConfig({ ...editedConfig, [group]: editedConfig[group].map((item, i) => i === index ? { ...item, enabled: event.target.checked } : item) }); setPreviewHash(null);
              }} />Enabled</label>
              {selection.parameters.map((parameter) => <label className="parameter" key={parameter.name}>{parameter.name.replaceAll('_', ' ')}
                <input aria-label={`${selection.component_id ?? selection.pattern_id} ${parameter.name}`} type={typeof parameter.value === 'boolean' ? 'checkbox' : typeof parameter.value === 'number' ? 'number' : 'text'}
                  {...(typeof parameter.value === 'boolean' ? { checked: parameter.value } : { value: String(parameter.value) })}
                  onChange={(event) => {
                    const value = typeof parameter.value === 'boolean' ? event.target.checked : typeof parameter.value === 'number' ? Number(event.target.value) : event.target.value;
                    setEditedConfig({ ...editedConfig, [group]: editedConfig[group].map((item, i) => i === index ? {
                      ...item, parameters: item.parameters.map((p) => p.name === parameter.name ? { ...p, value } : p),
                    } : item) }); setPreviewHash(null);
                  }} />
              </label>)}
            </details>))}
            <button type="button" onClick={() => void preview()}>Apply settings</button>
          </fieldset>}
          {state && <p className="availability-note">Stop replay to change its detector settings.</p>}
          {config && <button type="button" disabled={Boolean(state) || busy} onClick={() => {
            setEditedConfig(config.detection_config); setPreviewHash(config.detection_config_hash); setError(null);
          }}>Restore default settings</button>}
          <details><summary>Chart layers</summary><div className="overlay-controls">{(Object.keys(layers) as Array<keyof OverlayVisibility>).map((key) => <label key={key}>
            <input type="checkbox" checked={layers[key]} onChange={(event) => setLayers({ ...layers, [key]: event.target.checked })} />
            {key === 'ema' ? 'EMA' : key === 'trend' ? 'Trend legs' : key === 'swing' ? 'Swings and structure' : key === 'range' ? 'Range/compression' : 'Sessions'}</label>)}</div></details>
        </>}
        <details className="source-settings"><summary>Source & run information</summary>
          <label className="input-label">Dataset revision <select aria-label="Dataset revision" value={selectedId} onChange={(event) => chooseSource(event.target.value)} disabled={Boolean(state) || busy}>
            {sources.filter((source) => source.instrument_id === (selected?.instrument_id ?? tab.instrument)).map((source) => <option key={source.dataset_revision_id} value={source.dataset_revision_id}>{source.dataset_revision_id}</option>)}
          </select></label>
          <label className="input-label">Display timezone <select aria-label="Display timezone" value={tab.timezone} onChange={(event) => chooseTimezone(event.target.value as DisplayTimezone)}><option value="Europe/London">UK · GMT / BST</option><option value="UTC">UTC</option></select></label>
          <label className="input-label">Replay end (exclusive) <input aria-label="Replay end" type="datetime-local" value={end} disabled={Boolean(state)} onChange={(event) => setEnd(event.target.value)} /></label>
          {selected && <p>{selected.bar_count} published bars · {selected.non_research_grade ? 'Synthetic demo only — not research-grade' : 'Provider history'}</p>}
          <dl><dt>Run ID</dt><dd><code>{state?.run_id ?? 'Not started'}</code></dd><dt>Source</dt><dd><code>{selected?.source_dataset_id}</code></dd>
            <dt>Dataset checksum</dt><dd><code>{selected?.canonical_checksum}</code></dd><dt>Config hash</dt><dd><code>{state?.detection_config_hash ?? previewHash ?? 'Changes need Apply'}</code></dd>
            <dt>Warm-up</dt><dd>{state ? `${state.warmup_processed} / ${state.warmup_required}` : config?.warmup_bars ?? 'Loading'} bars</dd></dl>
        </details>
      </div>
    </aside>
    {details && <div className="evidence-popover" ref={popover} role="dialog" aria-label="Pattern details" tabIndex={-1}
      onMouseEnter={() => { if (closePreview.current) clearTimeout(closePreview.current); }} onMouseLeave={dismissPreview}
      onKeyDown={(event) => { if (event.key === 'Escape') { setDetails(null); setPinned(false); } }}>
      <div className="popover-heading"><span>{pinned ? 'Pinned evidence' : 'Evidence preview'}</span><button type="button" aria-label="Close pattern details" onClick={() => { setDetails(null); setPinned(false); }}>×</button></div>
      <EventInspector event={details} timezone={tab.timezone} />
    </div>}
  </section>;
}
