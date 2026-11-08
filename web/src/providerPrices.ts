import { apiJson, type ReplayBar, type Timeframe } from './replay';
import { isRecord } from './validation';

export interface ProviderPriceWindow {
  instrument_id: string; timeframe: Timeframe; end: string;
  calendar_status: 'covered' | 'unverified'; analysis_ready: false;
  aggregation: 'display_only'; detail: string | null; bars: ReplayBar[];
}
export function providerWindowEnd(target: string, timeframe: Timeframe): string {
  const minutes: Record<Timeframe, number> = { '1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440 };
  if (!Number.isFinite(Date.parse(target)) || Date.parse(target) >= Math.floor(Date.now() / 60_000) * 60_000) throw new Error('Choose a completed candle.');
  // The price-only source window contains 1440 M1 bars, even on a daily chart.
  return new Date(Math.floor(Math.min(Date.now(), Date.parse(target) + Math.min(60, minutes[timeframe] * 26) * 60_000) / 60_000) * 60_000).toISOString();
}
export async function readProviderPrices(instrument: string, timeframe: Timeframe,
  end?: string, signal?: AbortSignal): Promise<ProviderPriceWindow> {
  const query = new URLSearchParams({ timeframe });
  if (end) query.set('end', new Date(Math.floor(Date.parse(end) / 60_000) * 60_000).toISOString());
  const result = await apiJson<unknown>(`/live/prices/${instrument}?${query}`, { signal });
  if (!isRecord(result) || result.instrument_id !== instrument || result.timeframe !== timeframe
    || !['covered', 'unverified'].includes(String(result.calendar_status)) || result.analysis_ready !== false
    || result.aggregation !== 'display_only' || typeof result.end !== 'string' || !Number.isFinite(Date.parse(result.end))
    || end && Date.parse(result.end) > Date.parse(end) || !Array.isArray(result.bars) || result.bars.length > 1440
    || !(result.detail === null || typeof result.detail === 'string')) throw new Error('Invalid provider price window.');
  let previous = -Infinity;
  for (const bar of result.bars) {
    if (!isRecord(bar) || typeof bar.timestamp !== 'string' || !Number.isFinite(Date.parse(bar.timestamp))
      || Date.parse(bar.timestamp) <= previous || typeof bar.available_at !== 'string'
      || !Number.isFinite(Date.parse(bar.available_at)) || Date.parse(bar.available_at) <= Date.parse(bar.timestamp)
      || Date.parse(bar.available_at) > Date.parse(result.end)
      || !['open', 'high', 'low', 'close'].every(key => typeof bar[key] === 'string' && Number.isFinite(Number(bar[key])))
      || Number(bar.low) > Math.min(Number(bar.open), Number(bar.close))
      || Number(bar.high) < Math.max(Number(bar.open), Number(bar.close))) throw new Error('Invalid provider prices.');
    previous = Date.parse(bar.timestamp);
  }
  return result as unknown as ProviderPriceWindow;
}
