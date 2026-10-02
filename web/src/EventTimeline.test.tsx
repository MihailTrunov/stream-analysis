import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { EventTimeline, filterTimelineEvents } from './EventTimeline';
import type { ReplayDetectorEvent } from './replay';

const first: ReplayDetectorEvent = {
  event_id: 'event-0', runtime_instance_id: 'runtime-a-1',
  run_id: 'run', dataset_revision_id: 'dataset', instrument_id: 'US30', timeframe: '1m',
  detection_config_hash: 'hash', pattern_id: 'A', pattern_version: '1',
  pattern_name: 'Fixture A', definition_fingerprint: 'fingerprint', build_id: 'build',
  code_revision: 'revision', code_dirty: false, code_capture_status: 'captured', instance_id: 'a-1',
  sequence: 0, from_state: 'NEW', to_state: 'CANDIDATE', trigger_id: 'start',
  event_time: '2026-01-05T12:11:00Z', detection_time: '2026-01-05T12:12:00Z',
  rationale: { score: 80 }, emission_order: 0,
};
const second: ReplayDetectorEvent = { ...first, event_id: 'event-1', pattern_id: 'B', instance_id: 'b-1',
  to_state: 'INVALIDATED', trigger_id: 'failed', emission_order: 1 };
const third: ReplayDetectorEvent = { ...first, event_id: 'event-2', instance_id: 'a-2', sequence: 1,
  detection_time: '2026-01-05T12:13:00Z', to_state: 'ACTIVE', emission_order: 2 };

describe('detector event timeline', () => {
  it('preserves same-time backend emission ties and does not mutate its source', () => {
    const source = Object.freeze([third, second, first]);
    const ordered = filterTimelineEvents(source, '', '');
    expect(ordered.map((event) => event.emission_order)).toEqual([0, 1, 2]);
    expect(source.map((event) => event.emission_order)).toEqual([2, 1, 0]);
    expect(ordered.slice(0, 2).map((event) => event.detection_time)).toEqual([
      first.detection_time, second.detection_time,
    ]);
  });

  it('filters pattern and state without changing the canonical events', () => {
    const source = [first, second, third];
    expect(filterTimelineEvents(source, 'A@1', 'ACTIVE')).toEqual([third]);
    expect(filterTimelineEvents(source, 'B@1', '')).toEqual([second]);
    expect(source).toEqual([first, second, third]);
    const html = renderToStaticMarkup(<EventTimeline events={source} selectedEventOrder={1} onSelectEvent={() => undefined} />);
    expect(html.match(/data-testid="timeline-event"/g)).toHaveLength(3);
    expect(html.indexOf('data-emission-order="0"')).toBeLessThan(html.indexOf('data-emission-order="1"'));
    expect(html).toContain('aria-current="true"');
    expect(html).toContain('score');
  });
});
