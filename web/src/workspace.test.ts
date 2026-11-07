import { describe, expect, it } from 'vitest';
import { dateTimeInput, displayTime, inputInstant } from './time';
import { normalizeApiInstants } from './replay';
import { newLiveTab, restoreLiveWorkspace, restoreWorkspace, workspaceStorageKey, WORKSPACE_KEY, LIVE_WORKSPACE_KEY } from './workspace';
import type { ReplaySource } from './replay';

describe('workspace restoration', () => {
  it('restores independent tabs and the focused tab, rejecting invalid or duplicate entries', () => {
    const tab = { id: 'one', instrument: 'US30', sourceId: 'us30', timezone: 'Europe/London' };
    const restored = restoreWorkspace(JSON.stringify({ version: 1, activeId: 'two', tabs: [
      tab, tab, { ...tab, id: 'two', instrument: 'DAX' }, { ...tab, id: 'bad', instrument: 'unknown' },
    ] }));
    expect(restored.tabs).toHaveLength(2);
    expect(restored.activeId).toBe('two');
    expect(restored.tabs[0].sourceId).toBe('us30');
  });

  it('migrates legacy drafts and rejects malformed optional settings', () => {
    const restored = restoreWorkspace(JSON.stringify({ version: 1, activeId: 'one', tabs: [
      { id: 'one', instrument: 'US30', sourceId: 'source', timezone: 'Europe/London',
        start: 42, end: {}, runId: [], config: { instrument_id: 'US30' }, appliedConfig: {} },
    ] }));
    expect(restored.version).toBe(3);
    expect(restored.tabs[0].config).toBeUndefined();
    expect(restored.tabs[0].appliedConfig).toBeUndefined();
    expect(restored.tabs[0].start).toBeUndefined();
    expect(restored.tabs[0].end).toBeUndefined();
    expect(restored.tabs[0].runId).toBeNull();
  });

  it('recovers from invalid local storage without blocking the initial chart', () => {
    const restored = restoreWorkspace('{broken');
    expect(restored.tabs).toHaveLength(1);
    expect(restored.tabs[0].instrument).toBe('US30');
    expect(restored.tabs[0].timezone).toBe('Europe/London');
    expect(restored.activeId).toBe(restored.tabs[0].id);
  });

  it('retains finite viewport state and ignores invalid restored chart ranges', () => {
    const tab = { id: 'one', instrument: 'US30', sourceId: 'us30', timezone: 'Europe/London',
      viewport: { from: 10, to: 80, origin: '2026-01-05T09:00:00Z' } };
    const raw = (value: unknown) => JSON.stringify({ version: 1, activeId: 'one', tabs: [value] });
    expect(restoreWorkspace(raw(tab)).tabs[0].viewport).toEqual(tab.viewport);
    expect(restoreWorkspace(raw({ ...tab, viewport: { ...tab.viewport, to: -10 } })).tabs[0].viewport).toBeUndefined();
  });
});

describe('UK display time leaves analytical instants in UTC', () => {
  it('uses GMT in winter and BST in summer, independent of browser timezone', () => {
    expect(dateTimeInput('2026-01-05T12:11:00Z', 'Europe/London')).toBe('2026-01-05T12:11');
    expect(dateTimeInput('2026-07-05T12:11:00Z', 'Europe/London')).toBe('2026-07-05T13:11');
    expect(inputInstant('2026-07-05T13:11', 'Europe/London')).toBe('2026-07-05T12:11:00.000Z');
    expect(displayTime('2026-07-05T12:11:00Z')).toContain('BST');
    expect(dateTimeInput('2026-07-05T12:11:00Z', 'UTC')).toBe('2026-07-05T12:11');
  });

  it('rejects ambiguous and nonexistent UK inputs rather than silently choosing a different instant', () => {
    expect(() => inputInstant('2026-03-29T01:30', 'Europe/London')).toThrow('does not exist');
    expect(() => inputInstant('2026-10-25T01:30', 'Europe/London')).toThrow('occurs twice');
    expect(inputInstant('2026-10-25T01:30', 'UTC')).toBe('2026-10-25T01:30:00.000Z');
    expect(() => inputInstant('2026-02-30T12:00', 'Europe/London')).toThrow('does not exist');
  });

  it('normalizes API offset spellings before causal comparison, preserving evidence and prices', () => {
    expect(normalizeApiInstants({ timestamp: '2026-07-05T13:11:00+01:00', close: '150.00',
      rationale: { timestamp: '2026-07-05T13:11:00+01:00', value: '42' },
      observations: [{ detection_time: '2026-07-05T12:11:00.000Z' }] })).toEqual({
      timestamp: '2026-07-05T12:11:00Z', close: '150.00',
      rationale: { timestamp: '2026-07-05T13:11:00+01:00', value: '42' },
      observations: [{ detection_time: '2026-07-05T12:11:00Z' }],
    });
  });
});


describe('normal startup workspace', () => {
  const sources = [
    { dataset_revision_id: 'offline-replay-us30-v3', non_research_grade: true },
    { dataset_revision_id: 'provider-us30', non_research_grade: false },
  ] as ReplaySource[];
  const tab = { id: 'one', instrument: 'US30', sourceId: 'provider-us30', timezone: 'Europe/London' };
  const snapshot = (tabs: unknown[], activeId = 'one') => JSON.stringify({ version: 3, tabs, activeId });

  it('starts live and keeps demo storage separate', () => {
    expect(newLiveTab()).toMatchObject({ mode: 'live', liveConnect: true, sourceId: '' });
    expect(restoreLiveWorkspace(null, sources).tabs[0]).toMatchObject({ mode: 'live', liveConnect: true });
    expect(workspaceStorageKey(false)).toBe(LIVE_WORKSPACE_KEY);
    expect(workspaceStorageKey(true)).toBe(WORKSPACE_KEY);
    expect(LIVE_WORKSPACE_KEY).not.toBe(WORKSPACE_KEY);
  });

  it('migrates real history and live tabs while dropping synthetic history', () => {
    const legacy = snapshot([tab, { ...tab, id: 'demo', sourceId: 'offline-replay-us30-v3' },
      { ...tab, id: 'live', mode: 'live', liveConnect: false }], 'live');
    const restored = restoreLiveWorkspace(null, sources, legacy);
    expect(restored.tabs.map((item) => item.id)).toEqual(['one', 'live']);
    expect(restored.activeId).toBe('live');
    expect(restored.tabs[0].mode).toBe('history');
    expect(restored.tabs[1].liveConnect).toBe(false);
  });

  it('uses the normal snapshot before the legacy snapshot', () => {
    const restored = restoreLiveWorkspace(snapshot([tab]), sources,
      snapshot([{ ...tab, id: 'other', mode: 'live' }], 'other'));
    expect(restored.activeId).toBe('one');
  });

  it('opens live for the last instrument when only demo history was saved', () => {
    const restored = restoreLiveWorkspace(null, sources, snapshot([
      { ...tab, instrument: 'DAX', chartTimeframe: '5m', sourceId: 'offline-replay-dax-v2' },
    ]));
    expect(restored.tabs[0]).toMatchObject({ instrument: 'DAX', chartTimeframe: '5m', mode: 'live', sourceId: '' });
  });

  it('clears synthetic bootstrap references from a saved live tab', () => {
    const restored = restoreLiveWorkspace(snapshot([
      { ...tab, sourceId: 'offline-replay-us30-v3', mode: 'live', liveConnect: true,
        liveHistorySourceId: 'offline-replay-us30-v3', liveWindowEnd: '2026-01-05T12:00:00Z' },
    ]), sources);
    expect(restored.tabs[0]).toMatchObject({ id: 'one', mode: 'live', sourceId: '', liveConnect: true });
    expect(restored.tabs[0].liveHistorySourceId).toBeUndefined();
    expect(restored.tabs[0].liveWindowEnd).toBeUndefined();
  });
});
