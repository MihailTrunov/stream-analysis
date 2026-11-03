import { useState } from 'react';
import type { ReplayDetectorEvent, ValidationAnnotation } from './replay';
import { displayTime, type DisplayTimezone } from './time';

export function filterTimelineEvents(
  events: readonly ReplayDetectorEvent[], pattern: string, state: string,
): ReplayDetectorEvent[] {
  return events.filter((event) =>
    (pattern === '' || `${event.pattern_id}@${event.pattern_version}` === pattern)
    && (state === '' || event.to_state === state)
  ).sort((left, right) => left.emission_order - right.emission_order);
}

interface Props {
  events: ReplayDetectorEvent[];
  selectedEventOrder: number | null;
  onSelectEvent: (event: ReplayDetectorEvent) => void;
  annotations?: ValidationAnnotation[];
  timezone?: DisplayTimezone;
  onInspect?: (event: ReplayDetectorEvent, pin: boolean) => void;
  onDismissPreview?: () => void;
}

export function EventTimeline({ events, selectedEventOrder, onSelectEvent, annotations = [], timezone = 'UTC', onInspect, onDismissPreview }: Props) {
  const [pattern, setPattern] = useState('');
  const [state, setState] = useState('');
  const patterns = [...new Set(events.map((event) => `${event.pattern_id}@${event.pattern_version}`))].sort();
  const states = [...new Set(events.map((event) => event.to_state))].sort();
  const visible = filterTimelineEvents(events, pattern, state);
  return <section aria-label="Detector event timeline" className="event-timeline">
    <h3>Detector event timeline</h3>
    <p>{events.length} transitions · {visible.length} shown</p>
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
          <time dateTime={event.detection_time}>{displayTime(event.detection_time, timezone)}</time> · {event.pattern_name ?? event.pattern_id} · {event.from_state} → {event.to_state}
          <small>{event.trigger_id}</small>
          {annotations.some((item) => item.event_id === event.event_id
            || (item.target_kind === 'instance' && item.instance_id === event.instance_id))
            && <small> · Reviewed</small>}
        </button>
        {onInspect && <button type="button" className="event-info" aria-label="Pattern details" aria-haspopup="dialog"
          onMouseEnter={() => onInspect(event, false)} onMouseLeave={onDismissPreview}
          onFocus={() => onInspect(event, false)} onBlur={onDismissPreview}
          onClick={() => onInspect(event, true)}>ⓘ</button>}
      </li>)}
    </ol> : <p>No emitted events match the filters.</p>}
  </section>;
}
