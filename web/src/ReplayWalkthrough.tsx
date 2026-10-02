import { useEffect, useState } from 'react';
import { CandlestickChart, type OverlayVisibility } from './CandlestickChart';
import { EventTimeline } from './EventTimeline';
import { EventInspector } from './EventInspector';
import { EventReview, MissedPatternReview } from './ReviewAnnotations';
import {
  apiJson,
  type ConfigSelection,
  type DetectionConfig,
  type ReplayConfig,
  type ReplayBar,
  type ReplayDetectorEvent,
  type ReplayObservation,
  type ReplayViewResponse,
  type ReplaySource,
  type ReplayState,
  type ReviewPattern,
  type ValidationAnnotation,
} from './replay';

function localInput(iso: string): string {
  return iso.slice(0, 16);
}

function utcInput(local: string): string {
  return new Date(`${local}:00Z`).toISOString();
}

export function ReplayWalkthrough() {
  const [sources, setSources] = useState<ReplaySource[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [config, setConfig] = useState<ReplayConfig | null>(null);
  const [editedConfig, setEditedConfig] = useState<DetectionConfig | null>(null);
  const [previewHash, setPreviewHash] = useState<string | null>(null);
  const [state, setState] = useState<ReplayState | null>(null);
  const [chartBars, setChartBars] = useState<ReplayBar[]>([]);
  const [observations, setObservations] = useState<ReplayObservation[]>([]);
  const [detectorEvents, setDetectorEvents] = useState<ReplayDetectorEvent[]>([]);
  const [annotations, setAnnotations] = useState<ValidationAnnotation[]>([]);
  const [reviewPatterns, setReviewPatterns] = useState<ReviewPattern[]>([]);
  const [selectedEventOrder, setSelectedEventOrder] = useState<number | null>(null);
  const [reviewAnchor, setReviewAnchor] = useState<string | null>(null);
  const [reviewInterval, setReviewInterval] = useState<{ start: string; end: string } | null>(null);
  const [focusTime, setFocusTime] = useState<string | null>(null);
  const [overlays, setOverlays] = useState<OverlayVisibility>({ ema: true, trend: true, swing: true, range: true, session: true });
  const [speed, setSpeed] = useState(1);
  const [seekTime, setSeekTime] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selected = sources.find((source) => source.dataset_revision_id === selectedId);
  const selectedEvent = detectorEvents.find((event) => event.emission_order === selectedEventOrder);
  const viewportEnd = state && focusTime
    ? new Date(Math.min(Date.parse(state.selected_end), Date.parse(focusTime) + 60_000)).toISOString()
    : state?.selected_end;

  useEffect(() => {
    void Promise.all([
      apiJson<{ sources: ReplaySource[] }>('/replay/sources'),
      apiJson<{ active: ReplayState | null }>('/replay/active'),
      apiJson<{ patterns: ReviewPattern[] }>('/pattern-definitions'),
    ])
      .then(([catalog, active, definitions]) => {
        setSources(catalog.sources);
        setReviewPatterns(definitions.patterns);
        const first = catalog.sources.find((item) => item.dataset_revision_id === 'offline-replay-us30-v3')
          ?? catalog.sources.find((item) => item.instrument_id === 'US30') ?? catalog.sources[0];
        if (first) setSelectedId((current) => current || first.dataset_revision_id);
        if (active.active) {
          setState(active.active);
          setSeekTime(localInput(active.active.selected_start));
        }
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  useEffect(() => {
    if (!selected || state) return;
    setStart(localInput(selected.suggested_start));
    setEnd(localInput(selected.suggested_end));
    setConfig(null);
    setEditedConfig(null);
    void apiJson<ReplayConfig>(`/replay/config-default?instrument_id=${encodeURIComponent(selected.instrument_id)}`)
      .then((value) => { setConfig(value); setEditedConfig(value.detection_config); setPreviewHash(value.detection_config_hash); })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, [selectedId, selected?.instrument_id, state]);

  useEffect(() => {
    if (!state) { setChartBars([]); setObservations([]); setDetectorEvents([]); setSelectedEventOrder(null); setFocusTime(null); return; }
    const controller = new AbortController();
    const params = new URLSearchParams({
      start: state.selected_start,
      end: viewportEnd ?? state.selected_end,
      limit: '500',
    });
    void apiJson<ReplayViewResponse>(`/replay/${state.run_id}/view?${params}`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) { setChartBars(result.bars); setObservations(result.observations); setDetectorEvents(result.events); } })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => controller.abort();
  }, [state?.run_id, state?.cursor_index, viewportEnd]);

  useEffect(() => {
    if (!state) { setAnnotations([]); return; }
    const controller = new AbortController();
    void apiJson<{ annotations: ValidationAnnotation[] }>(`/replay/${state.run_id}/annotations`, {
      signal: controller.signal,
    }).then((result) => {
      if (!controller.signal.aborted) setAnnotations(result.annotations);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => controller.abort();
  }, [state?.run_id, state?.cursor_index]);

  function annotationSaved(record: ValidationAnnotation) {
    setAnnotations((current) => [...current.filter((item) => item.annotation_id !== record.annotation_id), record]);
  }

  function selectReviewBar(timestamp: string) {
    if (reviewAnchor === null) {
      setReviewAnchor(timestamp);
      setReviewInterval({ start: timestamp, end: new Date(Date.parse(timestamp) + 60_000).toISOString() });
      return;
    }
    const first = Math.min(Date.parse(reviewAnchor), Date.parse(timestamp));
    const last = Math.max(Date.parse(reviewAnchor), Date.parse(timestamp));
    setReviewInterval({ start: new Date(first).toISOString(), end: new Date(last + 60_000).toISOString() });
    setReviewAnchor(null);
  }

  useEffect(() => {
    if (!state || state.status !== 'running' || !state.has_next || busy) return;
    const timer = window.setTimeout(() => {
      setBusy(true);
      void apiJson<ReplayState>(`/replay/${state.run_id}/tick`, { method: 'POST' })
        .then(setState)
        .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)))
        .finally(() => setBusy(false));
    }, 1000 / speed);
    return () => window.clearTimeout(timer);
  }, [state?.run_id, state?.status, state?.cursor_index, state?.has_next, speed, busy]);

  function changeParameter(group: 'components' | 'patterns', index: number, name: string, text: string) {
    if (!editedConfig) return;
    const selections = editedConfig[group].map((selection: ConfigSelection, position: number) => position !== index ? selection : {
      ...selection,
      parameters: selection.parameters.map((parameter) => parameter.name !== name ? parameter : {
        ...parameter,
        value: typeof parameter.value === 'number' ? Number(text) : text,
      }),
    });
    setEditedConfig({ ...editedConfig, [group]: selections });
    setPreviewHash(null);
  }

  async function preview() {
    if (!editedConfig) return;
    setBusy(true); setError(null);
    try {
      const result = await apiJson<{ detection_config_hash: string }>('/config/preview', {
        method: 'POST', body: JSON.stringify(editedConfig),
      });
      setPreviewHash(result.detection_config_hash);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setBusy(false); }
  }

  async function launch() {
    if (!selected || !editedConfig || !config) return;
    setBusy(true); setError(null);
    try {
      const launched = await apiJson<ReplayState>('/replay', {
        method: 'POST',
        body: JSON.stringify({
          dataset_revision_id: selected.dataset_revision_id,
          instrument_id: selected.instrument_id,
          timeframe: selected.timeframe,
          selected_start: utcInput(start),
          selected_end: utcInput(end),
          config_id: config.config_id,
          config_version: config.config_version,
          detection_config: editedConfig,
        }),
      });
      setState(launched);
      setSelectedEventOrder(null);
      setAnnotations([]);
      setReviewAnchor(null); setReviewInterval(null);
      setFocusTime(null);
      setSeekTime(localInput(launched.selected_start));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setBusy(false); }
  }

  async function stop() {
    if (!state) return;
    setBusy(true); setError(null);
    try {
      await apiJson(`/replay/${state.run_id}/stop`, { method: 'POST' });
      setState(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setBusy(false); }
  }

  async function step() {
    if (!state) return;
    setBusy(true); setError(null);
    try {
      setState(await apiJson<ReplayState>(`/replay/${state.run_id}/step`, { method: 'POST' }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setBusy(false); }
  }

  async function control(action: 'play' | 'pause' | 'next-event' | 'reset') {
    if (!state) return;
    setBusy(true); setError(null);
    try {
      const next = await apiJson<ReplayState>(`/replay/${state.run_id}/${action}`, {
        method: 'POST',
      });
      if (action === 'reset') {
        setChartBars([]); setObservations([]); setDetectorEvents([]); setAnnotations([]); setSelectedEventOrder(null); setFocusTime(null);
        setReviewAnchor(null); setReviewInterval(null);
        setSeekTime(localInput(next.selected_start));
      }
      setState(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setBusy(false); }
  }

  async function seek() {
    if (!state || !seekTime) return;
    setBusy(true); setError(null);
    try {
      const next = await apiJson<ReplayState>(`/replay/${state.run_id}/seek`, {
        method: 'POST', body: JSON.stringify({ target: utcInput(seekTime) }),
      });
      setChartBars([]); setObservations([]); setDetectorEvents([]); setAnnotations([]); setSelectedEventOrder(null); setFocusTime(null);
      setReviewAnchor(null); setReviewInterval(null);
      setState(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setBusy(false); }
  }

  return <section aria-labelledby="replay-heading">
    <h2 id="replay-heading">Detector walkthrough</h2>
    <p>Local, single-user replay over a pinned immutable dataset. The offline samples are non-research-grade.</p>
    {error && <p role="alert">{error}</p>}
    {!state ? <>
      <label>Dataset revision <select aria-label="Dataset revision" value={selectedId} onChange={(event) => {
        setSelectedId(event.target.value);
        setConfig(null);
        setEditedConfig(null);
        setPreviewHash(null);
        setStart('');
        setEnd('');
      }} disabled={busy}>
        {sources.map((source) => <option key={source.dataset_revision_id} value={source.dataset_revision_id}>{source.instrument_id} · {source.dataset_revision_id}</option>)}
      </select></label>
      {selected && <p>Source {selected.source_dataset_id} · {selected.timeframe} · {selected.bar_count ?? 'published'} bars · calendar {selected.calendar_version}
        {selected.non_research_grade && <> · <strong>Synthetic demo only — not research-grade</strong></>}</p>}
      <div className="form-row">
        <label>Start (UTC) <input aria-label="Start UTC" type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} /></label>
        <label>End (UTC, exclusive) <input aria-label="End UTC" type="datetime-local" value={end} onChange={(event) => setEnd(event.target.value)} /></label>
      </div>
      {config && editedConfig && <>
        <p>Configuration {config.config_id}@{config.config_version} · required warm-up {config.warmup_bars} bars</p>
        <details><summary>Edit detector parameters</summary>
          {(['components', 'patterns'] as const).map((group) => editedConfig[group].map((selection, index) => <fieldset key={`${group}-${index}`}>
            <legend>{selection.component_id ?? selection.pattern_id}</legend>
            {selection.parameters.filter((parameter) => typeof parameter.value !== 'boolean').map((parameter) => <label key={parameter.name}>{parameter.name} <input
              aria-label={`${selection.component_id ?? selection.pattern_id} ${parameter.name}`}
              type={typeof parameter.value === 'number' ? 'number' : 'text'}
              value={String(parameter.value)}
              onChange={(event) => changeParameter(group, index, parameter.name, event.target.value)}
            /></label>)}
          </fieldset>))}
        </details>
        <p>Resolved config hash: <code>{previewHash ?? 'Changed — preview before launch'}</code></p>
        <button type="button" onClick={() => void preview()} disabled={busy}>Preview configuration</button>
      </>}
      <button type="button" onClick={() => void launch()} disabled={busy || !selected || !editedConfig || !previewHash}>Launch walkthrough</button>
    </> : <>
      <p aria-live="polite">Replay {state.status} · {state.instrument_id} · {state.timeframe} · cursor {state.cursor_index}</p>
      <dl><dt>Run ID</dt><dd><code>{state.run_id}</code></dd><dt>Source</dt><dd>{state.source_dataset_id}</dd>
        <dt>Dataset checksum</dt><dd><code>{state.canonical_checksum}</code></dd>
        <dt>Config hash</dt><dd><code>{state.detection_config_hash}</code></dd>
        <dt>Warm-up</dt><dd>{state.warmup_processed} / {state.warmup_required} bars</dd></dl>
      <div className="form-row overlay-controls" aria-label="Chart overlays">
        {(Object.keys(overlays) as Array<keyof OverlayVisibility>).map((key) => <label key={key}>
          <input type="checkbox" checked={overlays[key]} onChange={(event) => setOverlays({ ...overlays, [key]: event.target.checked })} />
          {key === 'ema' ? 'EMA' : key === 'trend' ? 'Trend legs' : key === 'swing' ? 'Swings and structure' : key === 'range' ? 'Range/compression' : 'Sessions'}
        </label>)}
      </div>
      {focusTime && <p aria-live="polite">Chart focused on event detected at <time>{focusTime}</time>. Replay cursor remains at {state.cursor_time}.
        <button type="button" onClick={() => setFocusTime(null)}>Return to live cursor</button></p>}
      <p>Select one visible candle, or two to span an interval, for a missed-pattern review.
        {reviewAnchor && ' First candle selected; choose another to extend the interval.'}</p>
      <div id="replay-chart"><CandlestickChart bars={chartBars} observations={observations} events={detectorEvents}
        selectedEventOrder={selectedEventOrder} onSelectEvent={setSelectedEventOrder}
        onSelectBar={selectReviewBar} reviewInterval={reviewInterval}
        cursorTime={state.cursor_time} focusTime={focusTime} overlays={overlays} /></div>
      {selectedEvent && <><EventInspector event={selectedEvent} />
        <EventReview runId={state.run_id} event={selectedEvent} annotations={annotations} onSaved={annotationSaved} /></>}
      <EventTimeline key={state.run_id} events={detectorEvents} selectedEventOrder={selectedEventOrder}
        annotations={annotations}
        onSelectEvent={(event) => {
          setSelectedEventOrder(event.emission_order);
          setFocusTime(event.detection_time);
          document.getElementById('replay-chart')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }} />
      <MissedPatternReview runId={state.run_id} cursorTime={state.cursor_time} selectedInterval={reviewInterval}
        patterns={reviewPatterns}
        annotations={annotations} onSaved={annotationSaved} />
      {observations.at(-1) && <p aria-live="polite">Current market state: {Object.entries(observations.at(-1)!.availability).map(([name, status]) => `${name} ${status}`).join(' · ')}</p>}
      <p aria-live="polite">{state.visible_bars} visible bars · {state.has_next ? 'more bars available' : 'end of selected interval'}</p>
      {state.events.length > 0 && <p aria-live="polite">Current detector event: {state.events.map((event) => `${event.pattern_id} ${event.trigger_id} → ${event.to_state}`).join('; ')}</p>}
      {state.navigation && <p aria-live="polite">{state.navigation.stopped_on_event
        ? `Stopped on ${state.navigation.matched_event?.trigger_id} after ${state.navigation.processed_bars} bars`
        : `No further detector event; reached end after ${state.navigation.processed_bars} bars`}</p>}
      <div className="form-row replay-controls">
        <button type="button" onClick={() => void control('play')} disabled={busy || !state.has_next || state.status === 'running'}>Play</button>
        <button type="button" onClick={() => void control('pause')} disabled={busy || state.status !== 'running'}>Pause</button>
        <button type="button" onClick={() => void step()} disabled={busy || !state.has_next || state.status === 'running'}>Step one visible bar</button>
        <label>Speed <select aria-label="Playback speed" value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
          <option value={0.5}>0.5 bars/s</option><option value={1}>1 bar/s</option><option value={2}>2 bars/s</option><option value={4}>4 bars/s</option>
        </select></label>
        <button type="button" onClick={() => void control('next-event')} disabled={busy || !state.has_next || state.status === 'running'}>Next detector event</button>
        <button type="button" onClick={() => void control('reset')} disabled={busy}>Reset replay</button>
      </div>
      <div className="form-row">
        <label>Seek to completed bar (UTC) <input aria-label="Seek time UTC" type="datetime-local" value={seekTime} onChange={(event) => setSeekTime(event.target.value)} /></label>
        <button type="button" onClick={() => void seek()} disabled={busy || !seekTime || state.status === 'running'}>Seek</button>
      </div>
      <button type="button" onClick={() => void stop()} disabled={busy}>Stop walkthrough</button>
    </>}
  </section>;
}
