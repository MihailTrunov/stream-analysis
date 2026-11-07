import { useCallback, useEffect, useState } from 'react';
import { validateReplayState } from './validation';
import { SessionBoundary } from './SessionBoundary';
import { ReplaySession } from './ReplaySession';
import { LiveSession } from './LiveSession';
import { apiJson, sessionPath, type ReplaySource, type ReplayState, type ReviewPattern } from './replay';
import { newTab, newLiveTab, restoreWorkspace, restoreLiveWorkspace, workspaceStorageKey, WORKSPACE_KEY, type AnalysisTab, type Workspace } from './workspace';

interface Bootstrap { sources: ReplaySource[]; patterns: ReviewPattern[]; sessions: ReplayState[]; demoMode: boolean }

export function AnalysisWorkspace() {
  const [workspace, setWorkspace] = useState<Workspace>(() => {
    try { return restoreWorkspace(localStorage.getItem(WORKSPACE_KEY)); }
    catch { return restoreWorkspace(null); }
  });
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [storageError, setStorageError] = useState(false);
  const [closing, setClosing] = useState(false);
  const [closeError, setCloseError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      apiJson<{ sources: ReplaySource[] }>('/replay/sources', { signal: controller.signal }),
      apiJson<{ patterns: ReviewPattern[] }>('/pattern-definitions', { signal: controller.signal }),
      apiJson<{ active: ReplayState | null }>('/replay/active', { signal: controller.signal }),
      apiJson<{ sessions: ReplayState[] }>('/analysis/active', { signal: controller.signal }),
      apiJson<{ synthetic_demo_enabled?: boolean }>('/diagnostics', { signal: controller.signal }),
    ]).then(async ([catalog, definitions, legacy, analyses, diagnostics]) => {
      const demoMode = diagnostics.synthetic_demo_enabled === true;
      const sources = demoMode ? catalog.sources : catalog.sources.filter((source) => !source.non_research_grade);
      const syntheticIds = new Set(catalog.sources.filter((source) => source.non_research_grade).map((source) => source.dataset_revision_id));
      const running = [...analyses.sessions, ...(legacy.active ? [legacy.active] : [])]
        .filter((session) => demoMode || !syntheticIds.has(session.dataset_revision_id));
      const sessions = await Promise.all(running.map(async (item) => {
        validateReplayState(item);
        return item.status === 'running' ? apiJson<ReplayState>(`${sessionPath(item)}/pause`, {
          method: 'POST', signal: controller.signal }) : item;
      }));
      if (controller.signal.aborted) return;
      let raw: string | null = null;
      let previous: string | null = null;
      try {
        raw = localStorage.getItem(workspaceStorageKey(demoMode));
        previous = localStorage.getItem(WORKSPACE_KEY);
      } catch { setStorageError(true); }
      const current = demoMode ? restoreWorkspace(raw) : restoreLiveWorkspace(raw, catalog.sources, previous);
      const missing = sessions.filter((item) => !current.tabs.some((tab) => tab.runId === item.run_id));
      setWorkspace(missing.length ? { ...current, tabs: [...current.tabs, ...missing.map((item) => ({
        ...newTab(item.instrument_id), sourceId: item.dataset_revision_id, runId: item.run_id,
      }))].slice(0, 20) } : current);
      setBootstrap({ sources, patterns: definitions.patterns, sessions, demoMode });
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!bootstrap) return;
    try { localStorage.setItem(workspaceStorageKey(bootstrap.demoMode), JSON.stringify(workspace)); setStorageError(false); }
    catch { setStorageError(true); }
  }, [workspace, bootstrap]);

  const updateTab = useCallback((id: string, patch: Partial<AnalysisTab>) => {
    setWorkspace((current) => {
      const target = current.tabs.find((tab) => tab.id === id);
      if (!target || Object.entries(patch).every(([key, value]) => target[key as keyof AnalysisTab] === value)) return current;
      return { ...current, tabs: current.tabs.map((tab) => tab.id === id ? { ...tab, ...patch } : tab) };
    });
  }, []);

  const addSource = useCallback((source: ReplaySource) => {
    setBootstrap((current) => current ? { ...current, sources: [...current.sources.filter(
      (item) => item.dataset_revision_id !== source.dataset_revision_id), source] } : current);
  }, []);

  const updateRun = useCallback((id: string, state: ReplayState | null) => {
    updateTab(id, { runId: state?.run_id ?? null });
    const previous = workspace.tabs.find((tab) => tab.id === id)?.runId;
    setBootstrap((current) => current ? { ...current, sessions: [
      ...current.sessions.filter((item) => item.run_id !== previous && item.run_id !== state?.run_id),
      ...(state ? [state] : []),
    ] } : current);
  }, [updateTab, workspace.tabs]);

  async function closeTab(id: string) {
    if (closing || workspace.tabs.length === 1) return;
    const closed = workspace.tabs.find((tab) => tab.id === id);
    setClosing(true); setCloseError(null);
    try {
      if (closed?.mode === 'live' && closed.liveConnectionId) {
        await apiJson(`/live/subscriptions/${closed.liveConnectionId}`, { method: 'DELETE' });
      }
      setWorkspace((current) => {
      const tabs = current.tabs.filter((tab) => tab.id !== id);
      if (!tabs.length) return current;
      return { ...current, tabs, activeId: current.activeId === id ? tabs.at(-1)!.id : current.activeId };
      });
    } catch (reason) { setCloseError(`Could not disconnect and close the tab: ${String(reason)}`); }
    finally { setClosing(false); }
  }

  async function resetTab(tab: AnalysisTab) {
    try {
      if (tab.mode === 'live' && tab.liveConnectionId) {
        await apiJson(`/live/subscriptions/${tab.liveConnectionId}`, { method: 'DELETE' });
      }
      setWorkspace((current) => {
        const replacement = { ...newTab(tab.instrument), sourceId: tab.sourceId, runId: tab.runId,
          timezone: tab.timezone, mode: tab.mode, liveConnect: tab.liveConnect,
          liveConnectionId: tab.mode === 'live' ? crypto.randomUUID() : undefined };
        return { ...current, tabs: current.tabs.map((item) => item.id === tab.id ? replacement : item),
          activeId: current.activeId === tab.id ? replacement.id : current.activeId };
      });
    } catch (reason) { setCloseError(`Could not disconnect and reset the tab: ${String(reason)}`); }
  }

  return <>
    <div className="workspace-tabs" role="tablist" aria-label="Analysis sessions">
      {workspace.tabs.map((tab) => <div className={`workspace-tab ${tab.id === workspace.activeId ? 'selected' : ''}`} key={tab.id}>
        <button type="button" role="tab" id={`tab-${tab.id}`} aria-controls={`session-${tab.id}`}
          aria-selected={tab.id === workspace.activeId} tabIndex={tab.id === workspace.activeId ? 0 : -1}
          onKeyDown={(event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const index = workspace.tabs.findIndex((item) => item.id === tab.id);
            const next = event.key === 'Home' ? workspace.tabs[0] : event.key === 'End' ? workspace.tabs.at(-1)!
              : workspace.tabs[(index + (event.key === 'ArrowRight' ? 1 : workspace.tabs.length - 1)) % workspace.tabs.length];
            setWorkspace((current) => ({ ...current, activeId: next.id }));
            document.getElementById(`tab-${next.id}`)?.focus();
          }} onClick={() => setWorkspace((current) => ({ ...current, activeId: tab.id }))}>
          <span className="tab-status" aria-hidden="true" />{tab.instrument}<span className="tab-mode">{tab.mode === 'live' ? 'Live' : tab.runId ? 'Replay' : 'History'}</span>
        </button>
        <button type="button" className="close-tab" aria-label={`Close ${tab.instrument} tab`}
          title={tab.runId && bootstrap?.sessions.some((item) => item.run_id === tab.runId) ? 'Stop this replay before closing its tab' : 'Close tab'}
          disabled={closing || workspace.tabs.length === 1 || (Boolean(tab.runId) && bootstrap?.sessions.some((item) => item.run_id === tab.runId))}
          onClick={() => void closeTab(tab.id)}>×</button>
      </div>)}
      <button type="button" className="add-tab" aria-label="New analysis tab" disabled={!bootstrap || workspace.tabs.length >= 20}
        onClick={() => { const tab = bootstrap?.demoMode ? newTab() : newLiveTab(); setWorkspace((current) => ({ ...current, tabs: [...current.tabs, tab], activeId: tab.id })); }}>＋</button>
      <span className="workspace-local">LOCAL WORKSPACE</span>
    </div>
    {storageError && <p role="status" className="workspace-warning">Tab restoration is unavailable because browser storage could not be saved.</p>}
    {closeError && <p role="alert" className="workspace-warning">{closeError}</p>}
    {error && <div className="workspace-loading" role="alert"><h2>Unable to load the workspace</h2><p>{error}</p><p>Check the local API in Diagnostics, then reload.</p></div>}
    {!bootstrap && !error && <div className="workspace-loading" role="status">Loading local instrument history…</div>}
    {bootstrap && workspace.tabs.map((tab) => <SessionBoundary key={tab.id} active={tab.id === workspace.activeId}
      onReset={() => void resetTab(tab)}>{tab.mode === 'live' ? <LiveSession tab={tab} active={tab.id === workspace.activeId}
        sources={bootstrap.sources} patterns={bootstrap.patterns} onTabChange={updateTab} onSourceAdded={addSource} /> : <ReplaySession tab={tab}
      active={tab.id === workspace.activeId} sources={bootstrap.sources} patterns={bootstrap.patterns} onSourceAdded={addSource}
      activeRun={bootstrap.sessions.find((item) => item.run_id === tab.runId) ?? null} onTabChange={updateTab} onRunChange={updateRun} />}</SessionBoundary>)}
  </>;
}
