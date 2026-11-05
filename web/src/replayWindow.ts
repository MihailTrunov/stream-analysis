import { TIMEFRAMES, type ReplayState, type ReplayViewResponse, type Timeframe } from './replay';
import { validateReplayView } from './validation';

export interface ReplayWindow { run_id: string; target: string; end: string; timeframe: Timeframe }
const periods: Record<Timeframe, number> = { '1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440 };
export function replayWatermark(state: ReplayState): number {
  return state.available_at ? Date.parse(state.available_at)
    : state.cursor_time ? Date.parse(state.cursor_time) + 60_000 : Date.parse(state.selected_start);
}
export function isReplayWindow(value: unknown): value is ReplayWindow {
  if (!value || typeof value !== 'object') return false;
  const window = value as ReplayWindow;
  return typeof window.run_id === 'string' && /^[a-zA-Z0-9-]{1,64}$/.test(window.run_id)
    && [window.target, window.end].every((time) => typeof time === 'string' && Number.isFinite(Date.parse(time)))
    && Date.parse(window.target) < Date.parse(window.end) && TIMEFRAMES.includes(window.timeframe);
}
export function revealedWindow(state: ReplayState, timeframe: Timeframe, target: string, first: string): ReplayWindow {
  const time = Date.parse(target);
  if (!Number.isFinite(time) || time >= replayWatermark(state)) throw new Error('Choose a candle already revealed by this replay.');
  if (time < Date.parse(first)) throw new Error('Reached the beginning of this replay’s recorded history.');
  return { run_id: state.run_id, target, timeframe,
    end: new Date(Math.min(replayWatermark(state), time + periods[timeframe] * 26 * 60_000)).toISOString() };
}
export function coherentReplayView(value: unknown, state: ReplayState, timeframe: Timeframe, end: string): ReplayViewResponse {
  const view = validateReplayView(value);
  if (view.run_id !== state.run_id || view.cursor_index !== state.cursor_index
    || state.analysis_session && view.timeframe !== timeframe) throw new Error('The chart response does not match the requested replay position. Retry the chart update.');
  const cutoff = Math.min(replayWatermark(state), Date.parse(end));
  if (view.available_at && Date.parse(view.available_at) > replayWatermark(state)
    || view.view_end && Date.parse(view.view_end) > cutoff
    || view.bars.some((bar) => Date.parse(bar.available_at ?? new Date(Date.parse(bar.timestamp) + 60_000).toISOString()) > cutoff)
    || view.observations.some((frame) => Date.parse(frame.timestamp) >= cutoff)
    || view.events.some((event) => Date.parse(event.available_at ?? new Date(Date.parse(event.detection_time) + 60_000).toISOString()) > cutoff)) {
    throw new Error('The chart response includes data beyond the revealed replay window.');
  }
  return view;
}
