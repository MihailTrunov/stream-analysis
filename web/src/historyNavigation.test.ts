import { afterEach, expect, it, vi } from 'vitest';
import { candleAt, isNavigationPlan, localWindowEnd, navigationCoverage, prepareNavigation,
  publishNavigation, waitForHistory, type NavigationPlan } from './historyNavigation';
import { restoreWorkspace } from './workspace';
import type { ReplaySource } from './replay';

const plan: NavigationPlan = { request_ids: ['request-a'], instrument_id: 'US30', target: '2026-01-05T10:00:00Z',
  start: '2026-01-05T09:00:00Z', end: '2026-01-05T10:01:00Z', timeframe: '1m', intent: 'move', initialization: 'available' };
const source = { dataset_revision_id: 'parent', instrument_id: 'US30', range_start: plan.start,
  range_end: '2026-01-05T14:00:00Z', non_research_grade: false } as ReplaySource;
function respond(value: unknown) { return { ok: true, json: async () => value }; }
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it('bounds local windows and selects the containing higher-timeframe candle', () => {
  expect(localWindowEnd(source, plan.target, '1m')).toBe('2026-01-05T10:26:00.000Z');
  expect(localWindowEnd(source, plan.target, '1d')).toBe('2026-01-05T14:00:00.000Z');
  const bar = { timestamp: plan.target, available_at: '2026-01-05T10:05:00Z', open: '100', high: '102', low: '99', close: '101' };
  expect(candleAt([bar], '2026-01-05T10:03:00Z')).toBe(bar);
  expect(candleAt([bar], bar.available_at)).toBeUndefined();
});
it('persists only valid same-instrument pending plans for focused restoration', () => {
  expect(isNavigationPlan(plan)).toBe(true);
  expect(isNavigationPlan({ ...plan, request_ids: ['../../secrets'] })).toBe(false);
  expect(isNavigationPlan({ ...plan, target: '2040-01-01T00:00:00Z' })).toBe(false);
  const tab = { id: 'tab', instrument: 'US30', sourceId: 'parent', timezone: 'UTC', navigationPlan: plan,
    historyEnd: plan.end, replayEndExplicit: true };
  const restored = restoreWorkspace(JSON.stringify({ version: 3, tabs: [tab], activeId: 'tab' }));
  expect(restored.tabs[0].navigationPlan).toEqual(plan);
  expect(restored.tabs[0].historyEnd).toBe(plan.end);
  expect(restored.tabs[0].replayEndExplicit).toBe(true);
  expect(restoreWorkspace(JSON.stringify({ version: 3, tabs: [{ ...tab, instrument: 'DAX' }], activeId: 'tab' })).tabs[0].navigationPlan).toBeUndefined();
});
it('rejects a different navigation target and coverage response identity', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(respond({ ...plan, target: '2026-01-05T10:01:00Z' }))
    .mockResolvedValueOnce(respond({ request_id: 'different', status: 'available' })));
  const signal = new AbortController().signal;
  await expect(prepareNavigation({ source, target: plan.target, timeframe: '1m', intent: 'move', streams: [] }, signal)).rejects.toThrow('different chart');
  await expect(navigationCoverage(plan, signal)).rejects.toThrow('different request');
});
it('distinguishes waiting coverage from a complete reviewed publication', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(respond({ request_id: 'request-a', status: 'pending' }))
    .mockResolvedValueOnce(respond({ request_id: 'request-a', status: 'available', source_manifest_hash: 'pinned-hash' }))
    .mockResolvedValueOnce(respond({ dataset_revision_id: 'input', source_dataset_id: 'manifest', instrument_id: 'US30',
      range_start: plan.start, range_end: plan.end, bar_count: 61, calendar_version: 'calendar', canonical_checksum: 'hash' }));
  vi.stubGlobal('fetch', fetcher);
  const signal = new AbortController().signal;
  expect((await navigationCoverage(plan, signal)).ready).toBe(false);
  const coverage = await navigationCoverage(plan, signal);
  expect(coverage.ready).toBe(true);
  const published = await publishNavigation(plan, coverage.requests, signal);
  expect(published.non_research_grade).toBe(false);
  expect(published.dataset_revision_id).toBe('input');
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({ requests: { 'request-a': 'pinned-hash' } });
});
it.each(['failed', 'unavailable', 'unexpected_gap', 'conflict'])('exposes %s coverage instead of publishing partial history', async (status) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond({ request_id: 'request-a', status })));
  await expect(navigationCoverage(plan, new AbortController().signal)).rejects.toThrow();
});
it('cancels polling promptly without leaving a timer behind', async () => {
  vi.useFakeTimers();
  const controller = new AbortController();
  const waiting = waitForHistory(controller.signal);
  controller.abort();
  await expect(waiting).rejects.toMatchObject({ name: 'AbortError' });
  expect(vi.getTimerCount()).toBe(0);
});
