import { useEffect, useState } from 'react';
import {
  apiJson,
  type ConfigSelection,
  type DetectionConfig,
  type ReplayConfig,
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selected = sources.find((source) => source.dataset_revision_id === selectedId);

  useEffect(() => {
    void apiJson<{ sources: ReplaySource[] }>('/replay/sources')
      .then((result) => {
        setSources(result.sources);
        const first = result.sources.find((item) => item.instrument_id === 'US30') ?? result.sources[0];
        if (first) setSelectedId(first.dataset_revision_id);
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
      <button type="button" onClick={() => void stop()} disabled={busy}>Stop walkthrough</button>
    </>}
  </section>;
}
