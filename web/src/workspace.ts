import { isDetectionConfig } from './validation';
import type { AnalysisStream, DetectionConfig, ReplaySource, Timeframe } from './replay';
import type { DisplayTimezone } from './time';
import { isNavigationPlan, type NavigationPlan } from './historyNavigation';

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
  streams?: AnalysisStream[];
  appliedStreams?: AnalysisStream[];
  chartTimeframe?: Timeframe;
  historyEnd?: string;
  navigationPlan?: NavigationPlan;
  replayEndExplicit?: boolean;
  viewport?: { from: number; to: number; origin: string };
}
export interface Workspace { version: 3; tabs: AnalysisTab[]; activeId: string }
export const WORKSPACE_KEY = 'stream-analysis.workspace.v1';

export function newTab(instrument = 'US30'): AnalysisTab {
  return { id: crypto.randomUUID(), instrument, sourceId: '', timezone: 'Europe/London' };
}

export function restoreWorkspace(raw: string | null): Workspace {
  try {
    const parsed = JSON.parse(raw ?? 'null') as Workspace | null;
    if (parsed && [1, 2, 3].includes(parsed.version) && Array.isArray(parsed.tabs) && parsed.tabs.length) {
      const ids = new Set<string>();
      const tabs = parsed.tabs.filter((tab) => {
        if (!tab || typeof tab.id !== 'string' || ids.has(tab.id)
          || !['US30', 'DAX'].includes(tab.instrument) || typeof tab.sourceId !== 'string'
          || !['Europe/London', 'UTC'].includes(tab.timezone)) return false;
        ids.add(tab.id);
        return true;
      }).slice(0, 20).map((tab) => ({ id: tab.id, instrument: tab.instrument, sourceId: tab.sourceId, timezone: tab.timezone,
        runId: typeof tab.runId === 'string' ? tab.runId : null,
        start: typeof tab.start === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(tab.start) ? tab.start : undefined,
        end: typeof tab.end === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(tab.end) ? tab.end : undefined,
        config: isDetectionConfig(tab.config) ? tab.config : undefined,
        appliedConfig: parsed.version >= 2 && isDetectionConfig(tab.appliedConfig) ? tab.appliedConfig : undefined,
        streams: validStreams(tab.streams) ? tab.streams : undefined,
        appliedStreams: validStreams(tab.appliedStreams) ? tab.appliedStreams : undefined,
        chartTimeframe: ['1m', '5m', '15m', '30m', '1h', '4h', '1d'].includes(tab.chartTimeframe ?? '') ? tab.chartTimeframe : '1m' as Timeframe,
        historyEnd: typeof tab.historyEnd === 'string' && Number.isFinite(Date.parse(tab.historyEnd)) ? tab.historyEnd : undefined,
        navigationPlan: isNavigationPlan(tab.navigationPlan) && tab.navigationPlan.instrument_id === tab.instrument ? tab.navigationPlan : undefined,
        replayEndExplicit: tab.replayEndExplicit === true,
        viewport: tab.viewport
        && Number.isFinite(tab.viewport.from) && Number.isFinite(tab.viewport.to)
        && Math.abs(tab.viewport.from) < 1_000_000 && Math.abs(tab.viewport.to) < 1_000_000
        && tab.viewport.to > tab.viewport.from && typeof tab.viewport.origin === 'string'
        ? tab.viewport : undefined }));
      if (tabs.length) return { version: 3, tabs,
        activeId: tabs.some((tab) => tab.id === parsed.activeId) ? parsed.activeId : tabs[0].id };
    }
  } catch { /* A damaged or obsolete local snapshot must not block the chart. */ }
  const first = newTab();
  return { version: 3, tabs: [first], activeId: first.id };
}

export function preferredSource(sources: ReplaySource[], instrument: string): ReplaySource | undefined {
  const matching = sources.filter((source) => source.instrument_id === instrument);
  return matching.filter((source) => !source.non_research_grade)
    .sort((left, right) => Date.parse(right.range_end) - Date.parse(left.range_end))[0]
    ?? matching.find((source) => source.dataset_revision_id.endsWith('-v3')) ?? matching.at(-1);
}

function validStreams(value: unknown): value is AnalysisStream[] {
  return Array.isArray(value) && value.length > 0 && value.length <= 7 && value.every((item) =>
    item && isDetectionConfig(item.config) && item.policies && typeof item.policies === 'object'
    && Object.values(item.policies).every((mode) => ['live', 'manual', 'always'].includes(String(mode))));
}
