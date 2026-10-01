import { useEffect, useState } from 'react';
import { CandlestickChart, type OverlayVisibility } from './CandlestickChart';
import {
  apiJson,
  type ConfigSelection,
  type DetectionConfig,
  type ReplayConfig,
  type ReplayBar,
  type ReplayObservation,
  type ReplayViewResponse,
  type ReplaySource,
  type ReplayState,
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
  const [overlays, setOverlays] = useState<OverlayVisibility>({ ema: true, trend: true, swing: true, range: true, session: true });
  const [speed, setSpeed] = useState(1);
  const [seekTime, setSeekTime] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selected = sources.find((source) => source.dataset_revision_id === selectedId);

  useEffect(() => {
    void Promise.all([
      apiJson<{ sources: ReplaySource[] }>('/replay/sources'),
      apiJson<{ active: ReplayState | null }>('/replay/active'),
    ])
      .then(([catalog, active]) => {
        setSources(catalog.sources);
        const first = catalog.sources.find((item) => item.instrument_id === 'US30') ?? catalog.sources[0];
        if (first) setSelectedId(first.dataset_revision_id);
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
    if (!state) { setChartBars([]); setObservations([]); return; }
    const controller = new AbortController();
    const params = new URLSearchParams({
      start: state.selected_start,
      end: state.selected_end,
      limit: '100',
    });
    void apiJson<ReplayViewResponse>(`/replay/${state.run_id}/view?${params}`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) { setChartBars(result.bars); setObservations(result.observations); } })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => controller.abort();
  }, [state?.run_id, state?.cursor_index]);

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
        setChartBars([]); setObservations([]);
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
      setChartBars([]); setObservations([]);
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
      <label>Dataset revision <select aria-label="Dataset revision" value={selectedId} onChange={(event) => setSelectedId(event.target.value)} disabled={busy}>
        {sources.map((source) => <option key={source.dataset_revision_id} value={source.dataset_revision_id}>{source.instrument_id} · {source.dataset_revision_id}</option>)}
      </select></label>
      {selected && <p>Source {selected.source_dataset_id} · {selected.timeframe} · {selected.bar_count ?? 'published'} bars · calendar {selected.calendar_version}</p>}
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
      <CandlestickChart bars={chartBars} observations={observations} cursorTime={state.cursor_time} overlays={overlays} />
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
