import type { AnalysisStream, ReplayBar, ReplayViewResponse, Timeframe } from './replay';
import { isRecord, validateReplayView } from './validation';

export interface LiveState {
  subscription_id: string; run_id: string; input_id: string; bootstrap_revision_id: string;
  instrument_id: string; available_at: string; history_start: string;
  status: 'connecting' | 'catching_up' | 'connected' | 'gap' | 'failed' | 'conflict' | 'stale';
  detail: string | null; streams: AnalysisStream[];
  forming: (ReplayBar & { is_complete: false }) | null;
}
export interface LiveView extends ReplayViewResponse { live: LiveState }
const instant = (value: unknown) => typeof value === 'string' && Number.isFinite(Date.parse(value));
export function validateLiveState(value: unknown, id: string, instrument: string): LiveState {
  const forming = isRecord(value) ? value.forming : undefined;
  if (!isRecord(value) || value.subscription_id !== id || value.instrument_id !== instrument
    || !['run_id', 'input_id', 'bootstrap_revision_id'].every((key) => typeof value[key] === 'string' && value[key])
    || !instant(value.available_at) || !instant(value.history_start)
    || !['connecting', 'catching_up', 'connected', 'gap', 'failed', 'conflict', 'stale'].includes(String(value.status))
    || !(value.detail === null || typeof value.detail === 'string') || !Array.isArray(value.streams)
    || forming !== null && (!isRecord(forming) || forming.is_complete !== false
      || !instant(forming.timestamp)
      || !['open', 'high', 'low', 'close'].every((key) => typeof forming[key] === 'string'
        && Number.isFinite(Number(forming[key]))))) throw new Error('The API returned invalid live session state.');
  return value as unknown as LiveState;
}
export function validateLiveView(value: unknown, id: string, instrument: string, timeframe: Timeframe, end?: string): LiveView {
  const view = validateReplayView(value);
  const live = validateLiveState((value as LiveView).live, id, instrument);
  const cutoff = Math.min(Date.parse(live.available_at), end ? Date.parse(end) : Infinity);
  if (view.run_id !== live.run_id || view.timeframe !== timeframe || view.available_at !== live.available_at
    || view.view_end && Date.parse(view.view_end) > cutoff
    || view.bars.some((bar) => !bar.available_at || Date.parse(bar.available_at) > cutoff)
    || view.events.some((event) => event.dataset_revision_id !== live.input_id
      || !event.available_at || Date.parse(event.available_at) > cutoff)) throw new Error('The live snapshot does not match its confirmed input window.');
  return value as LiveView;
}
