import { useEffect, useState } from 'react';
import { visibleBars, type DemoBar } from './demo';
import './style.css';

interface DemoResponse {
  non_research_grade: boolean;
  bars: DemoBar[];
}

interface DiagnosticsResponse {
  app_version: string;
  build_id: string;
  schema_version: string | null;
  database_healthy: boolean;
  evaluation_worker_available: boolean;
  import_worker_available: boolean;
  oanda_import_available: boolean;
  data_root: string;
}

export function App() {
  const [bars, setBars] = useState<DemoBar[]>([]);
  const [diagnostics, setDiagnostics] = useState<DiagnosticsResponse | null>(null);
  const [cursor, setCursor] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const [demoResponse, diagnosticsResponse] = await Promise.all([
          fetch('/api/demo/bars', { signal: controller.signal }),
          fetch('/api/diagnostics', { signal: controller.signal }),
        ]);
        if (!demoResponse.ok || !diagnosticsResponse.ok) {
          throw new Error('The local API is unavailable.');
        }
        const demo = (await demoResponse.json()) as DemoResponse;
        const health = (await diagnosticsResponse.json()) as DiagnosticsResponse;
        if (!demo.non_research_grade || !Array.isArray(demo.bars)) {
          throw new Error('The installation demo is invalid.');
        }
        setBars(demo.bars);
        setDiagnostics(health);
      } catch (reason) {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : 'Unable to load demo.');
        }
      }
    }
    void load();
    return () => controller.abort();
  }, []);

  const revealed = visibleBars(bars, cursor);

  return (
    <main>
      <header>
        <h1>Stream Analysis</h1>
        <p>Local installation check · seeded demo data · not research-grade</p>
      </header>
      {error && <p role="alert">{error}</p>}
      <section aria-labelledby="demo-heading">
        <h2 id="demo-heading">Offline bar walkthrough</h2>
        <p>This verifies that the API and browser can read and step through local bars. Detector evaluation is not implemented in this installation demo.</p>
        <p aria-live="polite">{bars.length ? `Bar ${revealed.length} of ${bars.length}` : 'Loading bars…'}</p>
        <button onClick={() => setCursor((value) => Math.min(value + 1, bars.length - 1))} disabled={!bars.length || cursor >= bars.length - 1}>Step one bar</button>
        <button onClick={() => setCursor(0)} disabled={!bars.length || cursor === 0}>Reset</button>
        <table>
          <caption>Bars visible through the replay cursor</caption>
          <thead><tr><th>UTC</th><th>Open</th><th>High</th><th>Low</th><th>Close</th></tr></thead>
          <tbody>{revealed.map((bar) => <tr key={bar.timestamp}><td>{bar.timestamp}</td><td>{bar.open}</td><td>{bar.high}</td><td>{bar.low}</td><td>{bar.close}</td></tr>)}</tbody>
        </table>
      </section>
      <section aria-labelledby="diagnostics-heading">
        <h2 id="diagnostics-heading">Local diagnostics</h2>
        {diagnostics ? (
          <dl>
            <dt>App version</dt><dd>{diagnostics.app_version}</dd>
            <dt>Build ID</dt><dd>{diagnostics.build_id}</dd>
            <dt>Schema version</dt><dd>{diagnostics.schema_version ?? 'Not migrated'}</dd>
            <dt>Database</dt><dd>{diagnostics.database_healthy ? 'Healthy' : 'Unavailable'}</dd>
            <dt>Evaluation worker</dt><dd>{diagnostics.evaluation_worker_available ? 'Available' : 'Unavailable'}</dd>
            <dt>Import worker</dt><dd>{diagnostics.import_worker_available ? 'Available' : 'Unavailable'}</dd>
            <dt>OANDA import</dt><dd>{diagnostics.oanda_import_available ? 'Configured' : 'Unavailable without credentials'}</dd>
            <dt>Local data root</dt><dd>{diagnostics.data_root}</dd>
          </dl>
        ) : <p>Loading diagnostics…</p>}
      </section>
    </main>
  );
}
