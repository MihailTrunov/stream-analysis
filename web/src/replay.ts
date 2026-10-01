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
