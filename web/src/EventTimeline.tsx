import { useState } from 'react';
import type { ReplayDetectorEvent } from './replay';

export function filterTimelineEvents(
  events: readonly ReplayDetectorEvent[], pattern: string, state: string,
): ReplayDetectorEvent[] {
  return events.filter((event) =>
    (pattern === '' || `${event.pattern_id}@${event.pattern_version}` === pattern)
    && (state === '' || event.to_state === state)
  ).sort((left, right) => left.emission_order - right.emission_order);
}

function summary(event: ReplayDetectorEvent): string {
  const compact = JSON.stringify(event.rationale);
  return compact.length > 120 ? `${compact.slice(0, 117)}…` : compact;
}

interface Props {
  events: ReplayDetectorEvent[];
  selectedEventOrder: number | null;
  onSelectEvent: (event: ReplayDetectorEvent) => void;
}

export function EventTimeline({ events, selectedEventOrder, onSelectEvent }: Props) {
  const [pattern, setPattern] = useState('');
  const [state, setState] = useState('');
  const patterns = [...new Set(events.map((event) => `${event.pattern_id}@${event.pattern_version}`))].sort();
  const states = [...new Set(events.map((event) => event.to_state))].sort();
  const visible = filterTimelineEvents(events, pattern, state);
  return <section aria-label="Detector event timeline" className="event-timeline">
    <h3>Detector event timeline</h3>
    <p>{events.length} emitted events · {visible.length} shown · ordered by backend emission</p>
    <div className="form-row">
      <label>Pattern <select aria-label="Timeline pattern" value={pattern} onChange={(event) => setPattern(event.target.value)}>
        <option value="">All patterns</option>
        {patterns.map((value) => <option key={value} value={value}>{value}</option>)}
      </select></label>
      <label>State <select aria-label="Timeline state" value={state} onChange={(event) => setState(event.target.value)}>
        <option value="">All states</option>
        {states.map((value) => <option key={value} value={value}>{value}</option>)}
      </select></label>
    </div>
    {visible.length ? <ol>
      {visible.map((event) => <li key={event.emission_order}>
        <button type="button" data-testid="timeline-event" data-emission-order={event.emission_order}
          aria-current={selectedEventOrder === event.emission_order ? 'true' : undefined}
          onClick={() => onSelectEvent(event)}>
          <time>{event.detection_time}</time> · {event.pattern_id}@{event.pattern_version} · {event.from_state} → {event.to_state}
          <small> {event.trigger_id} · {summary(event)}</small>
        </button>
      </li>)}
    </ol> : <p>No emitted events match the filters.</p>}
  </section>;
}
