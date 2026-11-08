import { expect, it } from 'vitest';
import { retainEvidence } from './analysisEvidence';
import type { ReplayBar, ReplayDetectorEvent, ReplayObservation } from './replay';

const at = (minute: number) => new Date(Date.UTC(2026, 0, 5, 10, minute)).toISOString();
const prices = [10, 11, 12].map((minute) => ({ timestamp: at(minute), open: '100', high: '102', low: '99', close: '101' })) as ReplayBar[];
const frame = { timestamp: at(11), components: { ema: { component_id: 'ema', value_ready: true, ema: '101' } }, availability: {}, market_events: [] } as ReplayObservation;
const event = (minute: number, id: string, instance = 'one') => ({ event_id: id, run_id: 'live', instance_id: instance,
  available_at: at(minute), detection_time: at(minute), sequence: minute, timeframe: '1m', pattern_id: 'pattern' }) as ReplayDetectorEvent;

it('retains calculated component and detector markers when a new price window has no results', () => {
  const original = { observations: [frame], events: [event(12, 'hit')] };
  const kept = retainEvidence(original, { observations: [], events: [] }, prices);
  expect(kept).toEqual(original);
});

it('keeps ongoing pre-window state while excluding evidence beyond revealed prices', () => {
  const result = retainEvidence({ observations: [], events: [] }, {
    observations: [{ ...frame, timestamp: at(20) }, frame],
    events: [event(5, 'candidate'), event(8, 'active'), event(12, 'hit', 'two'), event(20, 'future')],
  }, prices);
  expect(result.events.map((item) => item.event_id)).toEqual(['active', 'hit']);
  expect(result.observations).toEqual([frame]);
});

it('does not duplicate immutable detector hits or component frames on repeated reads', () => {
  const original = { observations: [frame], events: [event(12, 'hit')] };
  const kept = retainEvidence(original, original, prices);
  expect(kept.events).toHaveLength(1);
  expect(kept.observations).toHaveLength(1);
});

it('clears visible evidence when replay has no revealed prices', () => {
  expect(retainEvidence({ observations: [frame], events: [event(12, 'hit')] }, { observations: [], events: [] }, []))
    .toEqual({ observations: [], events: [] });
});


it('evicts completed pre-window occurrences instead of retaining their history indefinitely', () => {
  const completed = { ...event(8, 'done'), to_state: 'COMPLETED' };
  expect(retainEvidence({ observations: [], events: [completed] }, { observations: [], events: [] }, prices).events).toEqual([]);
});
