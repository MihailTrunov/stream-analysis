import { afterEach, expect, it, vi } from 'vitest';
import { chartCalculation } from './chartCalculation';

vi.mock('./historyNavigation', () => ({ waitForHistory: async (signal: AbortSignal) => {
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
} }));
afterEach(() => vi.unstubAllGlobals());
const response = (value: unknown) => ({ ok: true, json: async () => value });
it('polls pending initialization until the detector session is ready', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response({ job_id: 'one', status: 'pending' }))
    .mockResolvedValueOnce(response({ job_id: 'one', status: 'running' }))
    .mockResolvedValueOnce(response({ job_id: 'one', status: 'ready', result: { run_id: 'ready' } }));
  vi.stubGlobal('fetch', fetch);
  expect(await chartCalculation('/analysis', '{}')).toEqual({ run_id: 'ready' });
  expect(fetch.mock.calls[0][1].headers.Prefer).toBe('respond-async');
  expect(fetch.mock.calls[1][0]).toBe('/api/analysis/tasks/one');
  expect(fetch).toHaveBeenCalledTimes(3);
});
it('cancels abandoned work after polling fails', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response({ job_id: 'one', status: 'pending' }))
    .mockResolvedValueOnce(response({ job_id: 'one', status: 'failed', error: 'No candles' }))
    .mockResolvedValue(response({ status: 'cancelled' }));
  vi.stubGlobal('fetch', fetch);
  await expect(chartCalculation('/analysis', '{}')).rejects.toThrow('No candles');
  expect(fetch.mock.calls.at(-1)?.[1].method).toBe('DELETE');
});
it('accepts synchronous initialization responses', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ run_id: 'legacy' })));
  expect(await chartCalculation('/analysis', '{}')).toEqual({ run_id: 'legacy' });
});
