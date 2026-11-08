import { useRef } from 'react';
import type { ReplayBar, ReplayDetectorEvent, ReplayObservation } from './replay';

export interface AnalysisEvidence { observations: ReplayObservation[]; events: ReplayDetectorEvent[] }
const empty: AnalysisEvidence = { observations: [], events: [] };

export function mergeObservations(previous: ReplayObservation[], next: ReplayObservation[]): ReplayObservation[] {
  return [...new Map([...previous, ...next].map((frame) => [frame.timestamp, frame])).values()]
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
}

/** Retain known results alongside the bounded price buffer, including ongoing pre-window states. */
export function retainEvidence(previous: AnalysisEvidence, next: AnalysisEvidence, prices: ReplayBar[]): AnalysisEvidence {
  const first = prices[0]?.timestamp;
  const last = prices.at(-1);
  if (!first || !last) return empty;
  const end = Date.parse(last.available_at ?? new Date(Date.parse(last.timestamp) + 60_000).toISOString());
  const events = [...new Map([...previous.events, ...next.events].map((event) => [event.event_id, event])).values()]
    .filter((event) => Date.parse(event.available_at ?? event.detection_time) <= end)
    .sort((a, b) => Date.parse(a.available_at ?? a.detection_time) - Date.parse(b.available_at ?? b.detection_time) || a.sequence - b.sequence);
  const preceding = new Map<string, ReplayDetectorEvent>();
  for (const event of events) {
    if (Date.parse(event.available_at ?? event.detection_time) < Date.parse(first)) preceding.set(`${event.run_id}:${event.instance_id}`, event);
  }
  return {
    observations: mergeObservations(previous.observations, next.observations)
      .filter((frame) => frame.timestamp >= first && frame.timestamp <= last.timestamp),
    events: events.filter((event) => Date.parse(event.available_at ?? event.detection_time) >= Date.parse(first)
      || preceding.get(`${event.run_id}:${event.instance_id}`) === event
        && !['COMPLETED', 'INVALIDATED', 'EXPIRED'].includes(event.to_state)
        && !(event.to_state === 'CONFIRMED' && event.pattern_id !== 'RANGE_COMPRESSION_V1')),
  };
}

export function useAnalysisEvidence(key: string, observations: ReplayObservation[], events: ReplayDetectorEvent[], prices: ReplayBar[]): AnalysisEvidence {
  const cache = useRef<{ key: string; value: AnalysisEvidence; observations: ReplayObservation[]; events: ReplayDetectorEvent[]; prices: ReplayBar[] } | null>(null);
  if (!cache.current || cache.current.key !== key || cache.current.observations !== observations
    || cache.current.events !== events || cache.current.prices !== prices) {
    cache.current = { key, observations, events, prices,
      value: retainEvidence(cache.current?.key === key ? cache.current.value : empty, { observations, events }, prices) };
  }
  return cache.current.value;
}
