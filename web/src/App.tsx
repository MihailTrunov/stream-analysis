import { useRef } from 'react';
import { AnalysisWorkspace } from './AnalysisWorkspace';
import { DiagnosticsContent } from './DiagnosticsContent';
import './style.css';

export function App() {
  const diagnostics = useRef<HTMLDialogElement>(null);
  return <main className="application">
    <header className="app-header">
      <div className="brand"><span className="brand-mark" aria-hidden="true">▥</span>
        <h1>Stream <span>Analysis</span></h1><span className="workspace-label">RESEARCH WORKSPACE</span></div>
      <nav aria-label="Application">
        <button type="button" disabled title="Background evaluation management is not available in this build">Runs <span className="quiet">Coming next</span></button>
        <button type="button" onClick={() => diagnostics.current?.showModal()}>Diagnostics</button>
      </nav>
    </header>
    <AnalysisWorkspace />
    <dialog ref={diagnostics} className="diagnostics-dialog" aria-label="Local diagnostics">
      <form method="dialog"><button className="dialog-close" aria-label="Close diagnostics">✕</button></form>
      <DiagnosticsContent />
    </dialog>
  </main>;
}
