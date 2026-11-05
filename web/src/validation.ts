import type { DetectionConfig, ReplayState, ReplayViewResponse } from './replay';

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
const text = (value: unknown) => typeof value === 'string' && value.length > 0;
const instant = (value: unknown) => typeof value === 'string' && Number.isFinite(Date.parse(value)) && /(?:Z|[+-]\d\d:\d\d)$/.test(value);
export function isDetectionConfig(value: unknown): value is DetectionConfig {
  if (!isRecord(value) || !['schema_version', 'instrument_id', 'timeframe', 'calendar_id'].every((key) => text(value[key]))) return false;
  return ['components', 'patterns'].every((group) => Array.isArray(value[group]) && value[group].every((item: unknown) =>
    isRecord(item) && text(item[group === 'components' ? 'component_id' : 'pattern_id'])
    && (item.enabled === undefined || typeof item.enabled === 'boolean')
    && Array.isArray(item.parameters) && item.parameters.every((parameter: unknown) => isRecord(parameter)
      && text(parameter.name) && (typeof parameter.value === 'string' || typeof parameter.value === 'boolean'
        || typeof parameter.value === 'number' && Number.isFinite(parameter.value)))));
}
export function validateReplayState(value: unknown): ReplayState {
  if (!isRecord(value) || !['created', 'running', 'paused', 'completed', 'failed', 'aborted'].includes(String(value.status))
    || !['run_id', 'dataset_revision_id', 'instrument_id', 'timeframe', 'detection_config_hash'].every((key) => text(value[key]))
    || !instant(value.selected_start) || !instant(value.selected_end)
    || !(value.cursor_time === null || instant(value.cursor_time))
    || !Number.isInteger(value.cursor_index) || typeof value.has_next !== 'boolean'
    || !Array.isArray(value.events) || value.detection_config !== undefined && !isDetectionConfig(value.detection_config)) {
    throw new Error('The API returned an invalid replay state.');
  }
  return value as unknown as ReplayState;
}
export function validateReplayView(value: unknown): ReplayViewResponse {
  if (!isRecord(value) || !text(value.run_id) || !Number.isInteger(value.cursor_index)
    || ['available_at', 'view_end', 'history_start'].some((key) => value[key] !== undefined && value[key] !== null && !instant(value[key]))
    || !(value.cursor_time === null || instant(value.cursor_time))
    || !Array.isArray(value.bars) || !value.bars.every((bar: unknown) => isRecord(bar)
      && instant(bar.timestamp) && (bar.available_at === undefined || instant(bar.available_at)) && ['open', 'high', 'low', 'close'].every((key) => typeof bar[key] === 'string' && Number.isFinite(Number(bar[key]))))
    || !Array.isArray(value.observations) || !value.observations.every((frame: unknown) => isRecord(frame)
      && instant(frame.timestamp) && isRecord(frame.availability) && isRecord(frame.components) && Array.isArray(frame.market_events))
    || !Array.isArray(value.events) || !value.events.every((event: unknown) => isRecord(event)
      && text(event.event_id) && instant(event.detection_time) && (event.available_at === undefined || instant(event.available_at)) && Number.isInteger(event.emission_order))) {
    throw new Error('The API returned an invalid chart snapshot.');
  }
  return value as unknown as ReplayViewResponse;
}
