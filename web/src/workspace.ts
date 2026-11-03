import type { DetectionConfig, ReplaySource } from './replay';
import type { DisplayTimezone } from './time';

export interface AnalysisTab {
  id: string;
  instrument: string;
  sourceId: string;
  timezone: DisplayTimezone;
  runId?: string | null;
  start?: string;
  end?: string;
  config?: DetectionConfig;
  appliedConfig?: DetectionConfig;
  viewport?: { from: number; to: number; origin: string };
}
export interface Workspace { version: 1; tabs: AnalysisTab[]; activeId: string }
export const WORKSPACE_KEY = 'stream-analysis.workspace.v1';

export function newTab(instrument = 'US30'): AnalysisTab {
  return { id: crypto.randomUUID(), instrument, sourceId: '', timezone: 'Europe/London' };
}

export function restoreWorkspace(raw: string | null): Workspace {
  try {
    const parsed = JSON.parse(raw ?? 'null') as Workspace | null;
    if (parsed?.version === 1 && Array.isArray(parsed.tabs) && parsed.tabs.length) {
      const ids = new Set<string>();
      const tabs = parsed.tabs.filter((tab) => {
        if (!tab || typeof tab.id !== 'string' || ids.has(tab.id)
          || !['US30', 'DAX'].includes(tab.instrument) || typeof tab.sourceId !== 'string'
          || !['Europe/London', 'UTC'].includes(tab.timezone)) return false;
        ids.add(tab.id);
        return true;
      }).slice(0, 20).map((tab) => ({ ...tab, viewport: tab.viewport
        && Number.isFinite(tab.viewport.from) && Number.isFinite(tab.viewport.to)
        && Math.abs(tab.viewport.from) < 1_000_000 && Math.abs(tab.viewport.to) < 1_000_000
        && tab.viewport.to > tab.viewport.from && typeof tab.viewport.origin === 'string'
        ? tab.viewport : undefined }));
      if (tabs.length) return { version: 1, tabs,
        activeId: tabs.some((tab) => tab.id === parsed.activeId) ? parsed.activeId : tabs[0].id };
    }
  } catch { /* A damaged or obsolete local snapshot must not block the chart. */ }
  const first = newTab();
  return { version: 1, tabs: [first], activeId: first.id };
}

export function preferredSource(sources: ReplaySource[], instrument: string): ReplaySource | undefined {
  const matching = sources.filter((source) => source.instrument_id === instrument);
  return matching.filter((source) => !source.non_research_grade)
    .sort((left, right) => Date.parse(right.range_end) - Date.parse(left.range_end))[0]
    ?? matching.find((source) => source.dataset_revision_id.endsWith('-v3')) ?? matching.at(-1);
}
