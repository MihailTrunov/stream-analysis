import { describe, expect, it } from 'vitest';
import { dateTimeInput, displayTime, inputInstant } from './time';
import { normalizeApiInstants } from './replay';
import { restoreWorkspace } from './workspace';

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
