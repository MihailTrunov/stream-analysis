import { useEffect, useRef, useState } from 'react';
import { apiJson, type DetectionConfig, type ReplayConfig } from './replay';

/** Draft edits and the last explicitly applied configuration have distinct identities. */
export function useDetectorSettings(instrument: string | undefined, initialDraft: DetectionConfig | null,
  initialApplied: DetectionConfig | null, onError: (error: string) => void) {
  const [config, setConfig] = useState<ReplayConfig | null>(null);
  const [editedConfig, setEditedConfig] = useState<DetectionConfig | null>(null);
  const [appliedConfig, setAppliedConfig] = useState(initialApplied);
  const [previewHash, setPreviewHash] = useState<string | null>(null);
  const draft = useRef(initialDraft);
  const applied = useRef(appliedConfig);
  const error = useRef(onError);
  error.current = onError;
  applied.current = appliedConfig;
  if (editedConfig) draft.current = editedConfig;
  useEffect(() => {
    if (!instrument) return;
    const controller = new AbortController();
    setConfig(null); setEditedConfig(null); setPreviewHash(null);
    void apiJson<ReplayConfig>(`/replay/config-default?instrument_id=${instrument}`, { signal: controller.signal })
      .then(async (value) => {
        if (controller.signal.aborted) return;
        setConfig(value);
        const previous = draft.current;
        const isDefault = !previous || previous.instrument_id !== instrument;
        const editable = isDefault ? value.detection_config : previous;
        const validation = await apiJson<{ detection_config_hash: string }>('/config/preview', {
          method: 'POST', body: JSON.stringify(editable), signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        const isApplied = isDefault || JSON.stringify(editable) === JSON.stringify(applied.current);
        if (isDefault) setAppliedConfig(editable);
        setEditedConfig(editable); setPreviewHash(isApplied ? validation.detection_config_hash : null);
      }).catch((reason: unknown) => {
        if (!controller.signal.aborted) error.current(reason instanceof Error ? reason.message : String(reason));
      });
    return () => controller.abort();
  }, [instrument]);
  return { config, editedConfig, appliedConfig, previewHash, setEditedConfig, setAppliedConfig, setPreviewHash };
}
