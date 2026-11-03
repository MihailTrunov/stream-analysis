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
