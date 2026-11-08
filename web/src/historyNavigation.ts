import { apiJson, type AnalysisStream, type ReplayBar, type ReplaySource, type Timeframe, TIMEFRAMES } from './replay';

export interface NavigationPlan {
  request_ids: string[]; instrument_id: string; target: string; start: string; end: string;
  timeframe: Timeframe; intent: 'move' | 'replay'; initialization: string;
  live_status?: 'ready' | 'market_closed' | 'calendar_unverified';
  live_detail?: string;
}
export interface NavigationInput {
  dataset_revision_id: string; source_dataset_id: string; instrument_id: string;
  range_start: string; range_end: string; bar_count: number; canonical_checksum: string;
  calendar_version: string;
}
export interface NavigationOptions {
  source: ReplaySource; target: string; timeframe: Timeframe; intent: 'move' | 'replay';
  streams: AnalysisStream[]; end?: string;
}
interface Coverage { request_id: string; status: string; source_manifest_hash: string }
const periods: Record<Timeframe, number> = { '1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440 };
export function localWindowEnd(source: ReplaySource, target: string, timeframe: Timeframe): string {
  return new Date(Math.min(Date.parse(source.range_end), Date.parse(target) + periods[timeframe] * 26 * 60_000)).toISOString();
}
export function candleAt(bars: ReplayBar[], target: string): ReplayBar | undefined {
  const time = Date.parse(target);
  return bars.find((bar) => Date.parse(bar.timestamp) <= time && time < Date.parse(bar.available_at ?? new Date(Date.parse(bar.timestamp) + 60_000).toISOString()));
}
export function isNavigationPlan(value: unknown): value is NavigationPlan {
  if (!value || typeof value !== 'object') return false;
  const plan = value as NavigationPlan;
  return Array.isArray(plan.request_ids) && plan.request_ids.length > 0 && plan.request_ids.length <= 32
    && plan.request_ids.every((id) => typeof id === 'string' && /^[a-zA-Z0-9-]{1,64}$/.test(id))
    && ['US30', 'DAX'].includes(plan.instrument_id) && TIMEFRAMES.includes(plan.timeframe)
    && (plan.live_status === undefined || ['ready', 'market_closed', 'calendar_unverified'].includes(plan.live_status))
    && (plan.live_detail === undefined || typeof plan.live_detail === 'string')
    && ['move', 'replay'].includes(plan.intent) && [plan.start, plan.end, plan.target].every((time) => typeof time === 'string' && Number.isFinite(Date.parse(time)))
    && Date.parse(plan.start) < Date.parse(plan.end) && Date.parse(plan.start) <= Date.parse(plan.target) && Date.parse(plan.target) <= Date.parse(plan.end);
}
export async function prepareNavigation(options: NavigationOptions, signal: AbortSignal): Promise<NavigationPlan> {
  const result = await apiJson<NavigationPlan>('/history/navigation', { method: 'POST', signal,
    body: JSON.stringify({ source_revision_id: options.source.dataset_revision_id, instrument_id: options.source.instrument_id,
      target: options.target, timeframe: options.timeframe, streams: options.streams, intent: options.intent, end: options.end }) });
  if (!isNavigationPlan(result) || result.instrument_id !== options.source.instrument_id || result.intent !== options.intent
    || result.timeframe !== options.timeframe || Date.parse(result.target) !== Date.parse(options.target)) throw new Error('History preparation returned a different chart target.');
  return result;
}
export async function navigationCoverage(plan: NavigationPlan, signal: AbortSignal) {
  const results = await Promise.all(plan.request_ids.map((id) => apiJson<Coverage>(`/history/requests/${encodeURIComponent(id)}`, { signal })));
  if (results.some((result, index) => result.request_id !== plan.request_ids[index])) throw new Error('History coverage returned a different request.');
  const terminal = results.find((result) => !['available', 'pending'].includes(result.status));
  if (terminal) {
    const messages: Record<string, string> = { unavailable: 'History is unavailable for part of this period.', failed: 'Historical data loading failed. Check Diagnostics or explicitly resume/retry the import.',
      unexpected_gap: 'Historical data has an unexpected gap. Repair that period before continuing.', conflict: 'Retained history has conflicting prices. Resolve the source conflict before continuing.' };
    throw new Error(messages[terminal.status] ?? 'History coverage is unavailable.');
  }
  return { ready: results.every((result) => result.status === 'available'),
    completed: results.filter((result) => result.status === 'available').length,
    requests: Object.fromEntries(results.map((result) => [result.request_id, result.source_manifest_hash])) };
}
export async function publishNavigation(plan: NavigationPlan, requests: Record<string, string>, signal: AbortSignal): Promise<ReplaySource> {
  const input = await apiJson<NavigationInput>('/history/inputs', { method: 'POST', signal, body: JSON.stringify({ requests }) });
  if (input.instrument_id !== plan.instrument_id || Date.parse(input.range_start) > Date.parse(plan.start)
    || Date.parse(input.range_end) < Date.parse(plan.end)) throw new Error('Published history does not cover the requested chart.');
  return { ...input, timeframe: '1m', non_research_grade: false, suggested_start: plan.target,
    suggested_end: plan.end, config_id: '', config_version: '' };
}
export function waitForHistory(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
    const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(new DOMException('Aborted', 'AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, 1000);
    signal.addEventListener('abort', abort, { once: true });
  });
}
