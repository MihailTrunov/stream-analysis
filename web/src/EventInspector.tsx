import { Fragment } from 'react';
import type { ReplayDetectorEvent } from './replay';

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

function exact(value: unknown): string {
  if (value === null || value === undefined) return 'Not provided';
  if (typeof value === 'string') return value;
  if (typeof value === 'boolean' || typeof value === 'number') return String(value);
  return JSON.stringify(value);
}

function typed(value: unknown): { value: string; type: string | null; missing: boolean } {
  const item = asRecord(value);
  if (item && 'type' in item && 'value' in item) {
    return { value: exact(item.value), type: typeof item.type === 'string' ? item.type : null,
      missing: item.value === null || item.value === undefined };
  }
  return { value: exact(value), type: null, missing: value === null || value === undefined };
}

function featureUnit(name: string): string | null {
  if (name.endsWith('_points')) return 'points';
  if (name.endsWith('_bars')) return 'bars';
  if (name.endsWith('_seconds')) return 'seconds';
  if (name.endsWith('_pct')) return '%';
  return null;
}

function EvidenceValue({ value, units }: { value: unknown; units?: string | null }) {
  const displayed = typed(value);
  return <><code>{displayed.value}</code>{!displayed.missing && units && <span> {units}</span>}
    {!displayed.missing && displayed.type && <small> · {displayed.type}</small>}</>;
}

function SourceRefs({ value }: { value: unknown }) {
  const refs = Array.isArray(value) ? value : [];
  return refs.length ? <ul>{refs.map((ref, index) => <li key={`${index}:${exact(ref)}`}><code>{exact(ref)}</code></li>)}</ul>
    : <span>None recorded</span>;
}

function ConditionEvidence({ item, index }: { item: Record<string, unknown>; index: number }) {
  const features = asRecord(item.features) ?? {};
  const units = typeof item.units === 'string' ? item.units : null;
  return <section className="evidence-condition" aria-label={`Recorded condition ${index + 1}`}>
    <h5>{exact(item.condition_id)}</h5>
    <dl>
      <dt>Status</dt><dd>{exact(item.status)}</dd>
      <dt>Observed value</dt><dd><EvidenceValue value={item.value} units={units} /></dd>
      <dt>Operator</dt><dd><code>{exact(item.operator)}</code></dd>
      <dt>Threshold</dt><dd><EvidenceValue value={item.threshold} units={units} /></dd>
      <dt>Units</dt><dd>{units ?? 'Not specified'}</dd>
      <dt>Source refs</dt><dd><SourceRefs value={item.source_refs} /></dd>
    </dl>
    <h6>Recorded features</h6>
    {Object.keys(features).length ? <dl>
      {Object.entries(features).sort(([left], [right]) => left.localeCompare(right)).map(([name, value]) => {
        const unit = featureUnit(name);
        return <Fragment key={name}><dt><span>{name.replaceAll('_', ' ')}</span> <small><code>{name}</code></small></dt>
          <dd><EvidenceValue value={value} units={unit} /></dd></Fragment>;
      })}
    </dl> : <p>None recorded</p>}
  </section>;
}

export function EventInspector({ event }: { event: ReplayDetectorEvent }) {
  const rationale = asRecord(event.rationale);
  const items = rationale?.schema === 'detector-evidence-v1' && Array.isArray(rationale.items)
    ? rationale.items.map(asRecord) : null;
  const known = items !== null && items.every((item) => item !== null);
  return <aside aria-label="Selected pattern event" className="event-inspector">
    <h3>Event explanation</h3>
    <p>Recorded detector evidence; no browser-side re-evaluation.</p>
    <h4>Pattern and lineage</h4>
    <dl>
      <dt>Pattern name</dt><dd>{event.pattern_name ?? 'Name unavailable'}</dd>
      <dt>Pattern ID</dt><dd><code>{event.pattern_id}</code></dd>
      <dt>Detector version</dt><dd><code>{event.pattern_version}</code></dd>
      <dt>Definition fingerprint</dt><dd><code>{event.definition_fingerprint ?? 'Unavailable'}</code></dd>
      <dt>PatternInstance ID</dt><dd><code>{event.instance_id}</code></dd>
      <dt>Event sequence</dt><dd>{event.sequence}</dd>
      <dt>Run ID</dt><dd><code>{event.run_id}</code></dd>
      <dt>Dataset revision</dt><dd><code>{event.dataset_revision_id}</code></dd>
      <dt>Instrument/timeframe</dt><dd>{event.instrument_id} · {event.timeframe}</dd>
      <dt>Detection config hash</dt><dd><code>{event.detection_config_hash}</code></dd>
      <dt>Build ID</dt><dd><code>{event.build_id}</code></dd>
      <dt>Code revision</dt><dd><code>{event.code_revision ?? 'Unavailable'}</code></dd>
      <dt>Code capture</dt><dd>{event.code_capture_status} · dirty: {event.code_dirty === null ? 'Unknown' : event.code_dirty ? 'Yes' : 'No'}</dd>
    </dl>
    <h4>Transition and timing</h4>
    <dl>
      <dt>Previous state</dt><dd>{event.from_state}</dd>
      <dt>New state</dt><dd>{event.to_state}</dd>
      <dt>Trigger</dt><dd><code>{event.trigger_id}</code></dd>
      <dt>Event time (underlying behaviour)</dt><dd><time>{event.event_time}</time></dd>
      <dt>Detection time (first observable)</dt><dd><time>{event.detection_time}</time></dd>
    </dl>
    <h4>Structured rationale</h4>
    <dl>
      <dt>Schema</dt><dd><code>{exact(rationale?.schema)}</code></dd>
      <dt>Condition</dt><dd><code>{exact(rationale?.condition)}</code></dd>
      <dt>Market event refs</dt><dd><SourceRefs value={rationale?.source_market_event_refs} /></dd>
    </dl>
    {known ? items.length ? items.map((item, index) =>
      <ConditionEvidence key={`${index}:${exact(item?.condition_id)}`} item={item!} index={index} />)
      : <p>No condition items recorded.</p>
      : <><p>Evidence schema is not supported by this inspector; raw evidence is shown unchanged.</p>
        <pre>{JSON.stringify(event.rationale, null, 2)}</pre></>}
  </aside>;
}
