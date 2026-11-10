"""Bounded in-memory chart initialization; polling never waits on detector runtime locks."""

from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event, RLock
from typing import Any
from uuid import uuid4


@dataclass
class ChartTask:
    cleanup: Callable[[dict[str, Any]], object]
    status: str = 'pending'
    result: dict[str, Any] | None = None
    error: str | None = None
    future: Future[None] | None = None
    cancelled: Event = field(default_factory=Event)
    progress: tuple[int, int] | None = None

    def checkpoint(self, processed: int, total: int) -> None:
        if self.cancelled.is_set():
            raise CancelledError
        self.progress = processed, total


class ChartTasks:
    def __init__(self) -> None:
        self.lock = RLock()
        self.tasks: OrderedDict[str, ChartTask] = OrderedDict()
        self.executor: ThreadPoolExecutor | None = None

    def submit(self, operation: Callable[[ChartTask], dict[str, Any]],
               cleanup: Callable[[dict[str, Any]], object]) -> dict[str, str]:
        with self.lock:
            if len(self.tasks) >= 64:
                old = next((key for key, task in self.tasks.items()
                            if task.status not in {'pending', 'running'}), None)
                if old is None:
                    raise ValueError('Chart calculation capacity reached; wait for current work.')
                del self.tasks[old]
            if self.executor is None:
                self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='chart-init')
            id = str(uuid4())
            task = ChartTask(cleanup)
            self.tasks[id] = task
            task.future = self.executor.submit(self._execute, task, operation)
            return {'job_id': id, 'status': 'pending'}

    def _execute(self, task: ChartTask, operation: Callable[[ChartTask], dict[str, Any]]) -> None:
        with self.lock:
            if task.status == 'cancelled':
                return
            task.status = 'running'
        try:
            result = operation(task)
        except Exception as error:
            with self.lock:
                if task.status != 'cancelled':
                    task.status = 'failed'
                    task.error = str(error) if isinstance(error, ValueError) else (
                        'Chart calculation failed; available prices are retained.')
            return
        with self.lock:
            cancelled = task.status == 'cancelled'
            if not cancelled:
                task.result, task.status = result, 'ready'
        if cancelled:
            self._cleanup(task, result)

    @staticmethod
    def _cleanup(task: ChartTask, result: dict[str, Any]) -> None:
        try:
            task.cleanup(result)
        except Exception:
            pass  # A session may already have been stopped or evicted.

    def state(self, id: str) -> dict[str, Any]:
        with self.lock:
            if id not in self.tasks:
                raise ValueError('Chart calculation is unavailable; retry it.')
            task = self.tasks[id]
            progress = task.progress
            return {
                'job_id': id, 'status': task.status, 'result': task.result, 'error': task.error,
                'progress': {'processed': progress[0], 'total': progress[1]} if progress else None,
            }

    def cancel(self, id: str) -> None:
        with self.lock:
            task = self.tasks.get(id)
            if task is None or task.status == 'cancelled':
                return
            task.status = 'cancelled'
            task.cancelled.set()
            if task.future:
                task.future.cancel()
            if task.result is not None and self.executor is not None:
                self.executor.submit(self._cleanup, task, task.result)
            task.result = None

    def close(self) -> None:
        with self.lock:
            executor, self.executor = self.executor, None
        if executor:
            executor.shutdown(wait=True, cancel_futures=True)
        with self.lock:
            self.tasks.clear()


chart_tasks = ChartTasks()
