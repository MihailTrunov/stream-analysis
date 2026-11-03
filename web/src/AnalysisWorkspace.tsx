import { useCallback, useEffect, useState } from 'react';
import { validateReplayState } from './validation';
import { SessionBoundary } from './SessionBoundary';
import { ReplaySession } from './ReplaySession';
import { apiJson, sessionPath, type ReplaySource, type ReplayState, type ReviewPattern } from './replay';
import { newTab, restoreWorkspace, WORKSPACE_KEY, type AnalysisTab, type Workspace } from './workspace';

interface Bootstrap { sources: ReplaySource[]; patterns: ReviewPattern[]; sessions: ReplayState[] }

export function AnalysisWorkspace() {
  const [workspace, setWorkspace] = useState<Workspace>(() => {
    try { return restoreWorkspace(localStorage.getItem(WORKSPACE_KEY)); }
    catch { return restoreWorkspace(null); }
  });
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [storageError, setStorageError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      apiJson<{ sources: ReplaySource[] }>('/replay/sources', { signal: controller.signal }),
      apiJson<{ patterns: ReviewPattern[] }>('/pattern-definitions', { signal: controller.signal }),
      apiJson<{ active: ReplayState | null }>('/replay/active', { signal: controller.signal }),
      apiJson<{ sessions: ReplayState[] }>('/analysis/active', { signal: controller.signal }),
    ]).then(async ([catalog, definitions, legacy, analyses]) => {
      const running = [...analyses.sessions, ...(legacy.active ? [legacy.active] : [])];
      const sessions = await Promise.all(running.map(async (item) => {
        validateReplayState(item);
        return item.status === 'running' ? apiJson<ReplayState>(`${sessionPath(item)}/pause`, {
          method: 'POST', signal: controller.signal }) : item;
      }));
      if (controller.signal.aborted) return;
      setWorkspace((current) => {
        const missing = sessions.filter((item) => !current.tabs.some((tab) => tab.runId === item.run_id));
        return missing.length ? { ...current, tabs: [...current.tabs, ...missing.map((item) => ({
          ...newTab(item.instrument_id), sourceId: item.dataset_revision_id, runId: item.run_id,
        }))] } : current;
      });
      setBootstrap({ sources: catalog.sources, patterns: definitions.patterns, sessions });
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    try { localStorage.setItem(WORKSPACE_KEY, JSON.stringify(workspace)); setStorageError(false); }
    catch { setStorageError(true); }
  }, [workspace]);

  const updateTab = useCallback((id: string, patch: Partial<AnalysisTab>) => {
    setWorkspace((current) => {
      const target = current.tabs.find((tab) => tab.id === id);
      if (!target || Object.entries(patch).every(([key, value]) => target[key as keyof AnalysisTab] === value)) return current;
      return { ...current, tabs: current.tabs.map((tab) => tab.id === id ? { ...tab, ...patch } : tab) };
    });
  }, []);

  const updateRun = useCallback((id: string, state: ReplayState | null) => {
    updateTab(id, { runId: state?.run_id ?? null });
    const previous = workspace.tabs.find((tab) => tab.id === id)?.runId;
    setBootstrap((current) => current ? { ...current, sessions: [
      ...current.sessions.filter((item) => item.run_id !== previous && item.run_id !== state?.run_id),
      ...(state ? [state] : []),
    ] } : current);
  }, [updateTab, workspace.tabs]);

  function closeTab(id: string) {
    setWorkspace((current) => {
      const tabs = current.tabs.filter((tab) => tab.id !== id);
      if (!tabs.length) return current;
      return { ...current, tabs, activeId: current.activeId === id ? tabs.at(-1)!.id : current.activeId };
    });
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
          <span className="tab-status" aria-hidden="true" />{tab.instrument}<span className="tab-mode">{tab.runId ? 'Replay' : 'History'}</span>
        </button>
        <button type="button" className="close-tab" aria-label={`Close ${tab.instrument} tab`}
          title={tab.runId && bootstrap?.sessions.some((item) => item.run_id === tab.runId) ? 'Stop this replay before closing its tab' : 'Close tab'}
          disabled={workspace.tabs.length === 1 || (Boolean(tab.runId) && bootstrap?.sessions.some((item) => item.run_id === tab.runId))}
          onClick={() => closeTab(tab.id)}>×</button>
      </div>)}
      <button type="button" className="add-tab" aria-label="New analysis tab" disabled={workspace.tabs.length >= 20}
        onClick={() => { const tab = newTab(); setWorkspace((current) => ({ ...current, tabs: [...current.tabs, tab], activeId: tab.id })); }}>＋</button>
      <span className="workspace-local">LOCAL WORKSPACE</span>
    </div>
    {storageError && <p role="status" className="workspace-warning">Tab restoration is unavailable because browser storage could not be saved.</p>}
    {error && <div className="workspace-loading" role="alert"><h2>Unable to load the workspace</h2><p>{error}</p><p>Check the local API in Diagnostics, then reload.</p></div>}
    {!bootstrap && !error && <div className="workspace-loading" role="status">Loading local instrument history…</div>}
    {bootstrap && workspace.tabs.map((tab) => <SessionBoundary key={tab.id} active={tab.id === workspace.activeId}
      onReset={() => setWorkspace((current) => {
        const replacement = { ...newTab(tab.instrument), sourceId: tab.sourceId, runId: tab.runId, timezone: tab.timezone };
        return { ...current, tabs: current.tabs.map((item) => item.id === tab.id ? replacement : item),
          activeId: current.activeId === tab.id ? replacement.id : current.activeId };
      })}><ReplaySession tab={tab}
      active={tab.id === workspace.activeId} sources={bootstrap.sources} patterns={bootstrap.patterns}
      activeRun={bootstrap.sessions.find((item) => item.run_id === tab.runId) ?? null} onTabChange={updateTab} onRunChange={updateRun} /></SessionBoundary>)}
  </>;
}
