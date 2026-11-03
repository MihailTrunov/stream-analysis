import { describe, expect, it } from 'vitest';
import { projectOccurrences } from './PatternGroups';
import type { ReplayDetectorEvent } from './replay';

function event(id: string, time: string, state: string, sequence = 0, pattern = 'RANGE_COMPRESSION_V1'): ReplayDetectorEvent {
  return { run_id: 'run', instance_id: id, event_id: `${id}:${sequence}`, detection_time: time,
    available_at: time, sequence, to_state: state, pattern_id: pattern } as ReplayDetectorEvent;
}
const start = '2026-01-05T10:00:00Z';
const end = '2026-01-05T11:00:00Z';
describe('as-of occurrence projection', () => {
  it('retains pre-window ongoing occurrences without leaking a future terminal state', () => {
    const candidate = event('one', '2026-01-05T09:00:00Z', 'CANDIDATE');
    const active = event('one', '2026-01-05T09:10:00Z', 'ACTIVE', 1);
    const completed = event('one', '2026-01-05T12:00:00Z', 'COMPLETED', 2);
    const rows = projectOccurrences([candidate, active, completed], start, end);
    expect(rows).toHaveLength(1);
    expect(rows[0].group).toBe('Active');
    expect(rows[0].transitions).toEqual([candidate, active]);
  });
  it('places confirmed reversal/continuation in Completed and preserves terminal reasons', () => {
    const rows = projectOccurrences([event('rev', end, 'CONFIRMED', 0, 'REVERSAL'),
      event('cont', end, 'CONFIRMED', 0, 'CONTINUATION'), event('expired', end, 'EXPIRED')], start, end);
    expect(rows.every((row) => row.group === 'Completed')).toBe(true);
    expect(rows.find((row) => row.latest.instance_id === 'expired')?.latest.to_state).toBe('EXPIRED');
  });
  it('excludes completed occurrences outside the window and sorts newest transitions first', () => {
    const rows = projectOccurrences([event('old', '2026-01-05T09:00:00Z', 'COMPLETED'),
      event('first', start, 'CANDIDATE'), event('last', end, 'CANDIDATE')], start, end);
    expect(rows.map((row) => row.latest.instance_id)).toEqual(['last', 'first']);
  });
  it('uses close availability instead of a higher-candle start label', () => {
    const higher = { ...event('five', start, 'ACTIVE'), available_at: '2026-01-05T10:05:00Z' };
    expect(projectOccurrences([higher], start, '2026-01-05T10:04:00Z')).toHaveLength(0);
  });
});
