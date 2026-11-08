import { expect, it, vi } from 'vitest';
import { readHistoryWindow } from './useHistoryWindow';
import type { ReplaySource } from './replay';

it('shares immutable windows across subscribers and retries failed requests', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ bars: [] }) });
  vi.stubGlobal('fetch', fetcher);
  const source = { dataset_revision_id: 'shared-test', instrument_id: 'US30', range_start: '2026-01-01T00:00:00Z' } as ReplaySource;
  try {
    await Promise.all([readHistoryWindow(source, '2026-01-02T00:00:00Z'), readHistoryWindow(source, '2026-01-02T00:00:00Z')]);
    expect(fetcher).toHaveBeenCalledTimes(1);
    fetcher.mockRejectedValueOnce(new Error('offline'));
    await expect(readHistoryWindow(source, '2026-01-03T00:00:00Z')).rejects.toThrow('offline');
    await readHistoryWindow(source, '2026-01-03T00:00:00Z');
    expect(fetcher).toHaveBeenCalledTimes(3);
  } finally { vi.unstubAllGlobals(); }
});

it('loads 24 trading hours using bounded pages without assuming continuous wall-clock minutes', async () => {
  const times = Array.from({ length: 1600 }, (_, index) => Date.UTC(2026, 0, 5, 10) + index * 60_000 + (index >= 800 ? 2 * 86400_000 : 0));
  const requests: URL[] = [];
  const fetcher = vi.fn().mockImplementation(async (input: string) => {
    const url = new URL(input, 'http://localhost'); requests.push(url);
    return { ok: true, json: async () => ({ bars: times.filter((time) => time < Date.parse(url.searchParams.get('end')!))
      .slice(-Number(url.searchParams.get('limit'))).map((time) => ({ timestamp: new Date(time).toISOString(), open: '100', high: '102', low: '99', close: '101' })) }) };
  });
  vi.stubGlobal('fetch', fetcher);
  try {
    const source = { dataset_revision_id: 'paged-24-hours', instrument_id: 'US30', range_start: new Date(times[0]).toISOString() } as ReplaySource;
    const bars = await readHistoryWindow(source, new Date(times.at(-1)! + 60_000).toISOString());
    expect(bars).toHaveLength(1440);
    expect(requests.map((url) => Number(url.searchParams.get('limit')))).toEqual([500, 500, 440]);
    expect(new Set(bars.map((bar) => bar.timestamp)).size).toBe(1440);
    expect(Date.parse(bars.at(-1)!.timestamp) - Date.parse(bars[0].timestamp)).toBeGreaterThan(86400_000);
  } finally { vi.unstubAllGlobals(); }
});
