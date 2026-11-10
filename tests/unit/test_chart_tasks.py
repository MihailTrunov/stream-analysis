"""Initialization polling remains responsive and abandoned sessions are cleaned up."""

from threading import Event
from time import monotonic, sleep

import pytest

from market_analysis.application.chart_tasks import ChartTasks


def until(predicate):
    deadline = monotonic() + 3
    while not predicate():
        assert monotonic() < deadline
        sleep(.01)


def test_polling_and_cancellation_do_not_wait_for_detector_initialization():
    tasks = ChartTasks()
    entered, release, cleaned = Event(), Event(), Event()

    def operation(_):
        entered.set()
        assert release.wait(3)
        return {'run_id': 'created'}

    try:
        job = tasks.submit(operation, lambda result: cleaned.set())['job_id']
        assert entered.wait(3)
        assert tasks.state(job)['status'] == 'running'
        tasks.cancel(job)
        assert tasks.state(job)['status'] == 'cancelled'
        release.set()
        assert cleaned.wait(3)
        assert tasks.state(job)['result'] is None
    finally:
        release.set()
        tasks.close()


def test_ready_result_and_failure_are_reported_without_exposing_internal_errors():
    tasks = ChartTasks()
    try:
        job = tasks.submit(lambda _: {'run_id': 'ready'}, lambda _: None)['job_id']
        until(lambda: tasks.state(job)['status'] == 'ready')
        assert tasks.state(job)['result'] == {'run_id': 'ready'}
        for error, message in [(ValueError('No completed candles'), 'No completed candles'),
                               (RuntimeError('private connection details'),
                                'Chart calculation failed')]:
            def fail(_, error=error):
                raise error
            failed = tasks.submit(fail, lambda _: None)['job_id']
            until(lambda failed=failed: tasks.state(failed)['status'] == 'failed')
            assert message in tasks.state(failed)['error']
            assert 'private' not in tasks.state(failed)['error']
        with pytest.raises(ValueError, match='unavailable'):
            tasks.state('missing')
    finally:
        tasks.close()


def test_analysis_http_async_protocol_returns_before_initialization(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from market_analysis.api import analysis
    from market_analysis.application.replay_catalog import demo_detection_config

    tasks = ChartTasks()
    release = Event()

    def launch(_, *, checkpoint):
        checkpoint(2, 5)
        assert release.wait(3)
        return {'run_id': 'ready'}

    monkeypatch.setattr(analysis, 'chart_tasks', tasks)
    monkeypatch.setattr(analysis.analysis_sessions, 'launch', launch)
    app = FastAPI()
    app.include_router(analysis.router)
    try:
        with TestClient(app) as client:
            response = client.post('/analysis', headers={'Prefer': 'respond-async'}, json={
                'dataset_revision_id': 'source', 'instrument_id': 'US30',
                'selected_start': '2026-11-10T12:00:00Z',
                'selected_end': '2026-11-10T13:00:00Z',
                'streams': [{'config': demo_detection_config('US30').model_dump(mode='json')}],
            })
            assert response.status_code == 202
            id = response.json()['job_id']
            until(lambda: tasks.state(id)['progress'] is not None)
            progress = client.get(f'/analysis/tasks/{id}').json()
            assert progress['status'] == 'running'
            assert progress['progress'] == {'processed': 2, 'total': 5}
            release.set()
            until(lambda: tasks.state(id)['status'] == 'ready')
            assert client.get(f'/analysis/tasks/{id}').json()['result'] == {'run_id': 'ready'}
    finally:
        release.set()
        tasks.close()
