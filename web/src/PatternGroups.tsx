import { useState } from 'react';
import type { ReplayDetectorEvent, ValidationAnnotation } from './replay';
import { displayTime, type DisplayTimezone } from './time';

export type PatternGroup = 'Developing' | 'Active' | 'Completed';
export interface Occurrence { latest: ReplayDetectorEvent; transitions: ReplayDetectorEvent[]; group: PatternGroup }
export function projectOccurrences(events: ReplayDetectorEvent[], start: string, end: string): Occurrence[] {
  const occurrences = new Map<string, ReplayDetectorEvent[]>();
  for (const event of events) {
    if (Date.parse(event.available_at ?? event.detection_time) > Date.parse(end)) continue;
    const key = `${event.run_id}:${event.instance_id}`;
    occurrences.set(key, [...(occurrences.get(key) ?? []), event]);
  }
  return [...occurrences.values()].flatMap((transitions) => {
    transitions.sort((a, b) => a.sequence - b.sequence);
    const latest = transitions.at(-1)!;
    const group: PatternGroup = ['INVALIDATED', 'EXPIRED', 'COMPLETED'].includes(latest.to_state)
      || latest.to_state === 'CONFIRMED' && latest.pattern_id !== 'RANGE_COMPRESSION_V1' ? 'Completed'
      : ['ACTIVE', 'CONFIRMED'].includes(latest.to_state) ? 'Active' : 'Developing';
    if (group === 'Completed' && Date.parse(latest.available_at ?? latest.detection_time) < Date.parse(start)) return [];
    return [{ latest, transitions, group }];
  }).sort((a, b) => Date.parse(b.latest.available_at ?? b.latest.detection_time) - Date.parse(a.latest.available_at ?? a.latest.detection_time)
    || a.latest.event_id.localeCompare(b.latest.event_id));
}
interface Props {
  events: ReplayDetectorEvent[]; start: string; end: string; timeframe: string; loaded: boolean;
  selected: number | null; annotations: ValidationAnnotation[]; timezone: DisplayTimezone;
  onSelect: (event: ReplayDetectorEvent) => void; onInspect: (event: ReplayDetectorEvent, pin: boolean) => void;
  onDismiss: () => void;
}
export function PatternGroups({ events, start, end, timeframe, loaded, selected, annotations, timezone, onSelect, onInspect, onDismiss }: Props) {
  const [filter, setFilter] = useState('');
  const [state, setState] = useState('');
  const occurrences = projectOccurrences(events, start, end).filter((item) => (!filter || item.latest.timeframe === filter) && (!state || item.latest.to_state === state));
  return <section aria-label="Detected patterns" className="event-timeline">
    <label>Timeframe filter <select aria-label="Pattern timeframe filter" value={filter} onChange={(event) => setFilter(event.target.value)}>
      <option value="">All analytical timeframes</option>{[...new Set(events.map((event) => event.timeframe))].map((tf) => <option key={tf}>{tf}</option>)}
    </select></label>
    <label>State <select aria-label="Timeline state" value={state} onChange={(event) => setState(event.target.value)}><option value="">All states</option>
      {[...new Set(events.map((event) => event.to_state))].map((value) => <option key={value}>{value}</option>)}
    </select></label>
    {!loaded && <p role="status">Detections have not been calculated for this view.</p>}
    {(['Developing', 'Active', 'Completed'] as const).map((group) => <section key={group} aria-label={`${group} patterns`}>
      <h3>{group} <span>{occurrences.filter((item) => item.group === group).length}</span></h3>
      <ol>{occurrences.filter((item) => item.group === group).map(({ latest: event, transitions }) => <li key={`${event.run_id}:${event.instance_id}`}>
        <div className="occurrence-row">
          <button type="button" data-testid="timeline-event" data-emission-order={event.emission_order}
            aria-current={selected === event.emission_order ? 'true' : undefined} onClick={() => onSelect(event)}>
            {event.timeframe !== timeframe && <strong className="timeframe-label">{event.timeframe}</strong>}
            <time dateTime={event.detection_time}>{displayTime(event.available_at ?? event.detection_time, timezone)}</time> · {event.pattern_name ?? event.pattern_id} · {event.to_state}
            <small>{event.trigger_id}</small>
            {annotations.some((item) => item.event_id === event.event_id || item.instance_id === event.instance_id) && <small>Reviewed</small>}
          </button>
          <button type="button" className="event-info" aria-label="Pattern details" aria-haspopup="dialog"
            onMouseEnter={() => onInspect(event, false)} onMouseLeave={onDismiss} onFocus={() => onInspect(event, false)} onBlur={onDismiss}
            onClick={() => onInspect(event, true)}>ⓘ</button>
        </div>
        <details><summary>Transition history ({transitions.length})</summary>{transitions.map((transition) => <p key={transition.event_id}>
          {displayTime(transition.available_at ?? transition.detection_time, timezone)} · {transition.from_state} → {transition.to_state} · {transition.trigger_id}
        </p>)}</details>
      </li>)}</ol>
      {loaded && !occurrences.some((item) => item.group === group) && <p>No {group.toLowerCase()} patterns in this view.</p>}
    </section>)}
  </section>;
}
