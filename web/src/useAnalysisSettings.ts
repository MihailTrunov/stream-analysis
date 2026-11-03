import { useEffect, useRef, useState } from 'react';
import { apiJson, type AnalysisStream, type DetectionConfig, type Timeframe } from './replay';

export function useAnalysisSettings(instrument: string, saved: AnalysisStream[] | undefined,
  savedApplied: AnalysisStream[] | undefined, legacy: DetectionConfig | undefined,
  legacyApplied: DetectionConfig | undefined, onError: (message: string) => void) {
  const [drafts, setDrafts] = useState<AnalysisStream[]>(saved ?? (legacy ? [{ config: legacy, policies: {} }] : []));
  const [applied, setApplied] = useState<AnalysisStream[] | null>(savedApplied ?? (legacyApplied ? [{ config: legacyApplied, policies: {} }] : null));
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const values = useRef({ drafts, applied, onError });
  values.current = { drafts, applied, onError };
  useEffect(() => {
    const controller = new AbortController();
    setReady(false);
    void (async () => {
      const current = values.current;
      const existing = current.drafts.filter((item) => item.config.instrument_id === instrument);
      const defaults = existing.length ? null : await apiJson<{ detection_config: DetectionConfig }>(`/analysis-defaults?instrument_id=${instrument}`, { signal: controller.signal });
      const draft = defaults ? [{ config: defaults.detection_config, policies: {} }] : existing;
      await Promise.all(draft.map((stream) => apiJson('/analysis-config/preview', {
        method: 'POST', body: JSON.stringify(stream.config), signal: controller.signal,
      })));
      if (controller.signal.aborted) return;
      setDrafts(draft);
      if (defaults) setApplied(draft);
      setReady(true);
    })().catch((error: unknown) => { if (!controller.signal.aborted) values.current.onError(String(error)); });
    return () => controller.abort();
  }, [instrument]);
  const dirty = !ready || JSON.stringify(drafts) !== JSON.stringify(applied);
  async function apply() {
    setBusy(true);
    try {
      await Promise.all(drafts.map((stream) => apiJson('/analysis-config/preview', { method: 'POST', body: JSON.stringify(stream.config) })));
      setApplied(drafts); setReady(true);
    } catch (error) { onError(String(error)); }
    finally { setBusy(false); }
  }
  async function add(timeframe: Timeframe) {
    if (drafts.some((item) => item.config.timeframe === timeframe)) return;
    setBusy(true);
    try {
      const result = await apiJson<{ detection_config: DetectionConfig }>(`/analysis-defaults?instrument_id=${instrument}&timeframe=${timeframe}`);
      setDrafts((current) => [...current, { config: result.detection_config, policies: {} }]);
    } catch (error) { onError(String(error)); }
    finally { setBusy(false); }
  }
  async function addDetector(timeframe: string, patternId: string) {
    setBusy(true);
    try {
      const value = await apiJson<{ detection_config: DetectionConfig }>(`/analysis-defaults?instrument_id=${instrument}&timeframe=${timeframe}`);
      const pattern = value.detection_config.patterns.find((item) => item.pattern_id === patternId);
      if (!pattern) throw new Error('This detector version does not support the analytical timeframe.');
      setDrafts((current) => current.map((stream) => {
        if (stream.config.timeframe !== timeframe) return stream;
        const existing = new Set(stream.config.components.map((item) => item.component_id));
        const period = stream.config.components.find((item) => item.component_id === 'atr')?.parameters.find((p) => p.name === 'period')?.value ?? 2;
        const extra = value.detection_config.components.filter((item) => !existing.has(item.component_id)).map((item) => ({ ...item,
          parameters: item.parameters.map((p) => item.component_id === 'swing_point' && p.name === 'atr_period' ? { ...p, value: period } : p) }));
        return { ...stream, config: { ...stream.config, components: [...stream.config.components, ...extra].map((item) => ({ ...item, enabled: true })),
          patterns: [...stream.config.patterns, { ...pattern, enabled: true }] } };
      }));
    } catch (error) { onError(String(error)); }
    finally { setBusy(false); }
  }
  async function restore() {
    setBusy(true);
    try {
      const restored = await Promise.all(drafts.map(async (stream) => {
        const value = await apiJson<{ detection_config: DetectionConfig }>(`/analysis-defaults?instrument_id=${instrument}&timeframe=${stream.config.timeframe}`);
        return { config: value.detection_config, policies: stream.policies };
      }));
      setDrafts(restored);
    } catch (error) { onError(String(error)); }
    finally { setBusy(false); }
  }
  return { drafts, applied, setDrafts, apply, add, addDetector, restore, dirty, ready, busy };
}
