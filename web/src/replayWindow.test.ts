import { expect, it } from 'vitest';
import { coherentReplayView, isReplayWindow, replayWatermark, revealedWindow } from './replayWindow';
import { restoreWorkspace } from './workspace';
import type { ReplayState } from './replay';

const state = { run_id: 'run-one', analysis_session: true, available_at: '2026-01-05T11:00:00Z',
  cursor_index: 119, cursor_time: '2026-01-05T10:59:00Z', selected_start: '2026-01-05T10:00:00Z',
  selected_end: '2026-01-05T14:00:00Z' } as ReplayState;
const bar = { timestamp: '2026-01-05T09:10:00Z', available_at: '2026-01-05T09:15:00Z', open: '100', high: '102', low: '99', close: '101' };
const snapshot = { run_id: state.run_id, cursor_index: state.cursor_index, cursor_time: state.cursor_time, timeframe: '5m',
  available_at: state.available_at, view_end: '2026-01-05T09:20:00Z', history_start: '2026-01-05T09:00:00Z',
  bars: [bar], observations: [], events: [], limit: 500 };

it('selects old recorded context with no change to the replay state and caps padding at the watermark', () => {
  const before = JSON.stringify(state);
  const selected = revealedWindow(state, '1m', '2026-01-05T09:10:00Z', snapshot.history_start);
  expect(selected.end).toBe('2026-01-05T09:36:00.000Z');
  expect(revealedWindow(state, '5m', '2026-01-05T10:45:00Z', snapshot.history_start).end).toBe('2026-01-05T11:00:00.000Z');
  expect(JSON.stringify(state)).toBe(before);
  expect(() => revealedWindow(state, '1m', state.available_at!, snapshot.history_start)).toThrow('already revealed');
  expect(() => revealedWindow(state, '1m', '2026-01-05T08:59:00Z', snapshot.history_start)).toThrow('beginning');
});
it('uses close time for legacy watermark and initialization time before the first selected candle', () => {
  expect(replayWatermark({ ...state, available_at: undefined })).toBe(Date.parse(state.available_at!));
  expect(replayWatermark({ ...state, available_at: undefined, cursor_time: null })).toBe(Date.parse(state.selected_start));
});
it('restores only a browsing window belonging to the saved run', () => {
  const selected = revealedWindow(state, '1m', '2026-01-05T09:10:00Z', snapshot.history_start);
  expect(isReplayWindow(selected)).toBe(true);
  expect(isReplayWindow({ ...selected, end: selected.target })).toBe(false);
  const tab = { id: 'tab', instrument: 'US30', sourceId: 'source', timezone: 'UTC', runId: state.run_id, replayWindow: selected };
  expect(restoreWorkspace(JSON.stringify({ version: 3, tabs: [tab], activeId: 'tab' })).tabs[0].replayWindow).toEqual(selected);
  expect(restoreWorkspace(JSON.stringify({ version: 3, tabs: [{ ...tab, runId: 'different-run' }], activeId: 'tab' })).tabs[0].replayWindow).toBeUndefined();
});
it('accepts an earlier coherent snapshot while rejecting wrong run, cursor and timeframe ownership', () => {
  expect(coherentReplayView(snapshot, state, '5m', snapshot.view_end)).toEqual(snapshot);
  for (const change of [{ run_id: 'other' }, { cursor_index: 120 }, { timeframe: '1m' }]) {
    expect(() => coherentReplayView({ ...snapshot, ...change }, state, '5m', snapshot.view_end)).toThrow('does not match');
  }
});
it.each(['candle', 'event', 'observation', 'metadata', 'watermark'])('rejects a future %s in an earlier viewed window', (kind) => {
  const changes = {
    candle: { bars: [{ ...bar, available_at: '2026-01-05T09:25:00Z' }] },
    event: { events: [{ event_id: 'event', emission_order: 0, detection_time: bar.timestamp, available_at: '2026-01-05T09:21:00Z' }] },
    observation: { observations: [{ timestamp: '2026-01-05T09:21:00Z', components: {}, availability: {}, market_events: [] }] },
    metadata: { view_end: '2026-01-05T09:21:00Z' },
    watermark: { available_at: '2026-01-05T11:01:00Z' },
  }[kind];
  expect(() => coherentReplayView({ ...snapshot, ...changes }, state, '5m', snapshot.view_end)).toThrow('beyond the revealed');
});
