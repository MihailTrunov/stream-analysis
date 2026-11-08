import { apiJson } from './replay';
import { waitForHistory } from './historyNavigation';

interface Task { job_id?: string; status: string; result?: unknown; error?: string }
export async function chartCalculation<T>(endpoint: string, body: string, signal?: AbortSignal): Promise<T> {
  const first = await apiJson<Task | T>(endpoint, { method: 'POST', body, signal,
    headers: { Prefer: 'respond-async' } });
  if (!first || typeof first !== 'object' || !('job_id' in first) || typeof first.job_id !== 'string') return first as T;
  const id = first.job_id;
  const controller = new AbortController();
  const usedSignal = signal ?? controller.signal;
  let ready = false;
  try {
    let task = first as Task;
    while (task.status === 'pending' || task.status === 'running') {
      await waitForHistory(usedSignal);
      task = await apiJson<Task>(`/analysis/tasks/${id}`, { signal: usedSignal });
      if (task.job_id !== id) throw new Error('Chart calculation identity differs.');
    }
    if (task.status !== 'ready' || !task.result) throw new Error(task.error ?? 'Chart calculation was cancelled.');
    ready = true;
    return task.result as T;
  } finally {
    if (!ready) void apiJson(`/analysis/tasks/${id}`, { method: 'DELETE' }).catch(() => undefined);
  }
}
