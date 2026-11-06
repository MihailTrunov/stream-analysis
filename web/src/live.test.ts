import { describe, expect, it } from 'vitest';
import { validateLiveState, validateLiveView } from './live';
import { restoreWorkspace } from './workspace';

const id = '10000000-0000-0000-0000-000000000001';
const state = { subscription_id: id, run_id: 'session', input_id: 'live-input', bootstrap_revision_id: 'seed',
  instrument_id: 'US30', available_at: '2026-01-05T10:01:00Z', history_start: '2026-01-05T09:00:00Z',
  status: 'connected', detail: null, streams: [], forming: null };
const view = { run_id: 'session', cursor_index: 1, cursor_time: '2026-01-05T10:00:00Z',
  available_at: state.available_at, view_end: state.available_at, timeframe: '1m', observations: [], events: [],
  bars: [{ timestamp: '2026-01-05T10:00:00Z', available_at: state.available_at,
    open: '100', high: '102', low: '99', close: '101' }], live: state };
describe('live snapshot ownership and restoration', () => {
  it('accepts coherent confirmed data and keeps forming display separate', () => {
    expect(validateLiveView(view, id, 'US30', '1m')).toBe(view);
    expect(validateLiveState({ ...state, forming: { timestamp: state.available_at, open: '101',
      high: '103', low: '100', close: '102', is_complete: false } }, id, 'US30').forming?.is_complete).toBe(false);
  });
  it('rejects another session, instrument, timeframe or availability boundary', () => {
    expect(() => validateLiveState(state, 'other', 'US30')).toThrow();
    expect(() => validateLiveState(state, id, 'DAX')).toThrow();
    expect(() => validateLiveView(view, id, 'US30', '5m')).toThrow();
    expect(() => validateLiveView({ ...view, run_id: 'other' }, id, 'US30', '1m')).toThrow();
    expect(() => validateLiveView(view, id, 'US30', '1m', '2026-01-05T10:00:00Z')).toThrow();
  });
  it('rejects future completed candles and events attributed to another input', () => {
    expect(() => validateLiveView({ ...view, bars: [{ ...view.bars[0], available_at: '2026-01-05T10:02:00Z' }] }, id, 'US30', '1m')).toThrow();
    expect(() => validateLiveView({ ...view, events: [{ event_id: 'event', emission_order: 0,
      detection_time: '2026-01-05T10:00:00Z', available_at: state.available_at, dataset_revision_id: 'other-input' }] }, id, 'US30', '1m')).toThrow();
  });
  it('rejects forming data incorrectly marked completed', () => {
    expect(() => validateLiveState({ ...state, forming: { timestamp: state.available_at,
      open: '101', high: '103', low: '100', close: '102', is_complete: true } }, id, 'US30')).toThrow();
  });
  it('restores live intent independently of whether the tab is focused', () => {
    const tab = { id, instrument: 'US30', sourceId: 'seed', timezone: 'Europe/London',
      mode: 'live', liveConnect: true, liveConnectionId: id };
    const restored = restoreWorkspace(JSON.stringify({ version: 3, activeId: id,
      tabs: [tab, { ...tab, id: '10000000-0000-0000-0000-000000000002' }] }));
    expect(restored.tabs.every((item) => item.mode === 'live' && item.liveConnect)).toBe(true);
    expect(restored.tabs[0].liveConnectionId).toBe(id);
    const invalid = restoreWorkspace(JSON.stringify({ version: 3, activeId: id, tabs: [{ ...tab, liveConnectionId: '../bad' }] }));
    expect(invalid.tabs[0].liveConnectionId).toBeUndefined();
  });
});
