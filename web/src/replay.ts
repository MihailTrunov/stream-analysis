export interface ReplaySource {
  dataset_revision_id: string;
  source_dataset_id: string;
  instrument_id: string;
  timeframe: string;
  range_start: string;
  range_end: string;
  bar_count: number;
  canonical_checksum: string;
  calendar_version: string;
  non_research_grade: boolean;
  suggested_start: string;
  suggested_end: string;
  config_id: string;
  config_version: string;
}

export interface ConfigParameter { name: string; value: string | number | boolean }
export interface ConfigSelection {
  component_id?: string;
  pattern_id?: string;
  parameters: ConfigParameter[];
  [key: string]: unknown;
}
export interface DetectionConfig {
  schema_version: string;
  instrument_id: string;
  timeframe: string;
  calendar_id: string;
  components: ConfigSelection[];
  patterns: ConfigSelection[];
}
export interface ReplayConfig {
  config_id: string;
  config_version: string;
  detection_config: DetectionConfig;
  detection_config_hash: string;
  warmup_bars: number;
}
export interface ReplayState {
  run_id: string;
  status: string;
  dataset_revision_id: string;
  source_dataset_id: string;
  canonical_checksum: string;
  instrument_id: string;
  timeframe: string;
  selected_start: string;
  selected_end: string;
  detection_config_hash: string;
  calendar_version: string;
  warmup_required: number;
  warmup_processed: number;
  visible_bars: number;
  cursor_index: number;
  cursor_time: string | null;
  has_next: boolean;
  events: Array<{ pattern_id: string; trigger_id: string; to_state: string; detection_time: string }>;
  navigation?: {
    processed_bars: number;
    stopped_on_event: boolean;
    matched_event: { pattern_id: string; trigger_id: string; to_state: string } | null;
  };
}
export interface ReplayBar {
  timestamp: string;
  open: string;
  high: string;
  low: string;
  close: string;
}
export interface ReplayBarsResponse {
  run_id: string;
  cursor_index: number;
  cursor_time: string | null;
  bars: ReplayBar[];
  limit: number;
}

export interface ReplayObservation {
  timestamp: string;
  availability: Record<string, string>;
  components: Record<string, Record<string, unknown>>;
  market_events: Array<{
    ordinal: number;
    event_type: string;
    event_time: string;
    detection_time: string;
    source_instance_id: string;
    evidence: Record<string, unknown>;
  }>;
}
export interface ReplayDetectorEvent {
  event_id: string;
  run_id: string;
  dataset_revision_id: string;
  instrument_id: string;
  timeframe: string;
  detection_config_hash: string;
  pattern_id: string;
  pattern_version: string;
  pattern_name: string | null;
  definition_fingerprint: string | null;
  build_id: string;
  code_revision: string | null;
  code_dirty: boolean | null;
  code_capture_status: string;
  instance_id: string;
  runtime_instance_id: string;
  sequence: number;
  from_state: string;
  to_state: string;
  trigger_id: string;
  event_time: string;
  detection_time: string;
  rationale: Record<string, unknown>;
  emission_order: number;
}
export interface ValidationAnnotationRevision {
  revision: number;
  label: string;
  note: string | null;
  reviewer_id: string | null;
  changed_at: string;
}
export interface ValidationAnnotation {
  annotation_id: string;
  target_kind: 'event' | 'instance' | 'missed_pattern';
  event_id: string | null;
  instance_id: string | null;
  run_id: string | null;
  dataset_revision_id: string;
  instrument_id: string;
  timeframe: string;
  pattern_id: string;
  pattern_version: string;
  interval_start: string | null;
  interval_end: string | null;
  created_at: string;
  history: ValidationAnnotationRevision[];
}
export interface ReviewPattern {
  pattern_id: string;
  pattern_version: string;
  name: string;
}
export interface ReplayViewResponse extends ReplayBarsResponse {
  observations: ReplayObservation[];
  events: ReplayDetectorEvent[];
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!response.ok) {
    let message = `API request failed (${response.status})`;
    try {
      const payload = await response.json() as { detail?: string };
      if (payload.detail) message = payload.detail;
    } catch { /* Preserve the HTTP status. */ }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}
