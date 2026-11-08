import { afterEach, expect, it, vi } from 'vitest';
import { readProviderPrices, providerWindowEnd } from './providerPrices';

const bar = { timestamp: '2022-01-03T15:00:00Z', available_at: '2022-01-03T15:01:00Z',
  open: '100', high: '103', low: '99', close: '101' };
const window = { instrument_id: 'US30', timeframe: '1m', end: bar.available_at,
  calendar_status: 'unverified', analysis_ready: false, aggregation: 'display_only', detail: null, bars: [bar] };
afterEach(() => vi.unstubAllGlobals());
it('accepts unverified calendar prices and requests the selected instrument and historical end', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => window });
  vi.stubGlobal('fetch', fetch);
  expect((await readProviderPrices('US30', '1m', window.end)).bars).toEqual([bar]);
  expect(fetch.mock.calls[0][0]).toContain('/US30?timeframe=1m&end=2022-');
});
it.each([
  { instrument_id: 'DAX' }, { analysis_ready: true }, { bars: [bar, bar] },
  { bars: [{ ...bar, available_at: '2022-01-03T15:02:00Z' }] },
  { bars: [{ ...bar, high: '99' }] },
])('rejects mismatched or noncausal provider prices', async change => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...window, ...change }) }));
  await expect(readProviderPrices('US30', '1m')).rejects.toThrow('Invalid provider');
});
it('keeps higher-timeframe price jumps near the target and rejects future targets', () => {
  expect(providerWindowEnd('2022-01-03T15:00:00Z', '1d')).toBe('2022-01-03T16:00:00.000Z');
  expect(() => providerWindowEnd('2099-01-01T00:00:00Z', '1m')).toThrow('completed candle');
});
