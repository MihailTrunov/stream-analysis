import { TIMEFRAMES, type AnalysisStream, type DetectorPolicy, type Timeframe, type ReviewPattern } from './replay';

interface Props {
  streams: AnalysisStream[];
  patterns: ReviewPattern[];
  onAddDetector: (timeframe: string, patternId: string) => void;
  disabled: boolean;
  dirty: boolean;
  onChange: (streams: AnalysisStream[]) => void;
  onAdd: (timeframe: Timeframe) => void;
  onApply: () => void;
  onCalculate: (timeframe: string, pattern: string) => void;
}
export function AnalysisSettings({ streams, patterns, onAddDetector, disabled, dirty, onChange, onAdd, onApply, onCalculate }: Props) {
  function update(index: number, stream: AnalysisStream) {
    onChange(streams.map((item, i) => index === i ? stream : item));
  }
  return <>
    <label className="input-label">Add analytical timeframe
      <select aria-label="Add analytical timeframe" value="" disabled={disabled}
        onChange={(event) => { if (event.target.value) onAdd(event.target.value as Timeframe); }}>
        <option value="">Select timeframe…</option>
        {TIMEFRAMES.filter((tf) => !streams.some((item) => item.config.timeframe === tf)).map((tf) => <option key={tf} value={tf}>{tf === '1d' ? '1D' : tf}</option>)}
      </select>
    </label>
    {streams.map((stream, index) => <fieldset key={stream.config.timeframe} disabled={disabled} className="detector-configuration">
      <legend>{stream.config.timeframe} analytical settings</legend>
      <label className="input-label">Add registered detector
        <select aria-label={`Add detector ${stream.config.timeframe}`} value="" onChange={(event) => { if (event.target.value) onAddDetector(stream.config.timeframe, event.target.value); }}>
          <option value="">Select detector…</option>
          {patterns.filter((pattern) => !stream.config.patterns.some((item) => item.pattern_id === pattern.pattern_id)
            && (stream.config.timeframe === '1m' || pattern.pattern_id === 'RANGE_COMPRESSION_V1')).map((pattern) => <option key={pattern.pattern_id} value={pattern.pattern_id}>{pattern.name}</option>)}
        </select>
      </label>
      {stream.config.timeframe !== '1m' && <p className="availability-note">Reversal and continuation v1 require 1m qualification. Compression is supported here.</p>}
      {streams.length > 1 && <button type="button" onClick={() => onChange(streams.filter((_, i) => i !== index))}>Remove {stream.config.timeframe}</button>}
      {stream.config.patterns.map((pattern, patternIndex) => <details key={pattern.pattern_id} open>
        <summary>{pattern.pattern_id} <span>v{String(pattern.pattern_version)}</span></summary>
        <label><input aria-label={`${stream.config.timeframe} ${pattern.pattern_id} enabled`} type="checkbox" checked={pattern.enabled !== false}
          onChange={(event) => {
            const enabled = event.target.checked;
            const config = { ...stream.config, patterns: stream.config.patterns.map((item, i) => i === patternIndex ? { ...item, enabled } : item) };
            // Structural v1 detectors require the registered M1 component chain.
            if (enabled && pattern.pattern_id !== 'RANGE_COMPRESSION_V1') config.components = config.components.map((item) => ({ ...item, enabled: true }));
            update(index, { ...stream, config });
          }} />Enabled</label>
        <label>Operation mode <select aria-label={`${stream.config.timeframe} ${pattern.pattern_id} operation mode`} value={stream.policies[pattern.pattern_id!] ?? 'live'}
          onChange={(event) => update(index, { ...stream, policies: { ...stream.policies, [pattern.pattern_id!]: event.target.value as DetectorPolicy } })}>
          <option value="live">Live</option><option value="manual">Manual</option><option value="always">Always on</option>
        </select></label>
        {pattern.parameters.map((parameter) => <label className="parameter" key={parameter.name}>{parameter.name.replaceAll('_', ' ')}
          <input aria-label={`${stream.config.timeframe} ${pattern.pattern_id} ${parameter.name}`} value={String(parameter.value)}
            onChange={(event) => update(index, { ...stream, config: { ...stream.config,
              patterns: stream.config.patterns.map((item, i) => i !== patternIndex ? item : { ...item,
                parameters: item.parameters.map((p) => p.name !== parameter.name ? p : { ...p, value: typeof p.value === 'number' ? Number(event.target.value) : event.target.value }) }) } })} />
        </label>)}
        <button type="button" aria-label={`Calculate ${pattern.pattern_id} for current view · ${stream.config.timeframe}`} disabled={dirty || pattern.enabled === false}
          onClick={() => onCalculate(stream.config.timeframe, pattern.pattern_id!)}>Calculate current view · {stream.config.timeframe}</button>
      </details>)}
      <details><summary>Component parameters</summary>{stream.config.components.map((component, componentIndex) => <details key={component.component_id}>
        <summary>{component.component_id}</summary>
        {component.parameters.map((parameter) => <label className="parameter" key={parameter.name}>{parameter.name.replaceAll('_', ' ')}
          <input aria-label={`${stream.config.timeframe} ${component.component_id} ${parameter.name}`}
            type={typeof parameter.value === 'boolean' ? 'checkbox' : typeof parameter.value === 'number' ? 'number' : 'text'}
            {...(typeof parameter.value === 'boolean' ? { checked: parameter.value } : { value: String(parameter.value) })}
            onChange={(event) => update(index, { ...stream, config: { ...stream.config,
              components: stream.config.components.map((item, i) => i !== componentIndex ? item : { ...item,
                parameters: item.parameters.map((p) => p.name !== parameter.name ? p : { ...p, value: typeof p.value === 'boolean' ? event.target.checked : typeof p.value === 'number' ? Number(event.target.value) : event.target.value }) }) } })} />
        </label>)}
      </details>)}</details>
    </fieldset>)}
    <button type="button" disabled={disabled} onClick={onApply}>Apply settings</button>
    {dirty && <p role="status">Settings have unapplied changes.</p>}
    <p className="availability-note">Live: live closes and manual history. Manual: requested history only. Always on: live closes and visible history. Replay evaluates enabled detectors in every mode.</p>
  </>;
}
