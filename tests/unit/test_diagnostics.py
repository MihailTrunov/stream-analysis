from datetime import UTC, datetime, timedelta

from market_analysis.api.app import diagnostics_snapshot
from market_analysis.application.diagnostics import read_worker_heartbeat, write_worker_heartbeat


def test_diagnostics_does_not_expose_oanda_token(monkeypatch) -> None:
    monkeypatch.setenv("OANDA_TOKEN", "super-secret")
    snapshot = diagnostics_snapshot()
    payload = snapshot.model_dump()
    assert payload["oanda_import_available"] is True
    assert "super-secret" not in repr(payload)


def test_diagnostics_reports_unavailable_services_without_database_url(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("STREAM_ANALYSIS_DATABASE_URL", raising=False)
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(tmp_path))
    snapshot = diagnostics_snapshot()
    assert snapshot.database_healthy is False
    assert snapshot.schema_version is None
    assert snapshot.evaluation_worker_available is False
    assert snapshot.import_worker_available is False


def test_worker_heartbeat_is_fresh_then_stale(tmp_path) -> None:
    now = datetime(2026, 9, 26, 12, tzinfo=UTC)
    write_worker_heartbeat(tmp_path, "evaluation", now=now)
    assert read_worker_heartbeat(tmp_path, "evaluation", now=now + timedelta(seconds=10))
    assert not read_worker_heartbeat(tmp_path, "evaluation", now=now + timedelta(seconds=31))
    assert not read_worker_heartbeat(tmp_path, "import", now=now)


def test_diagnostics_reflects_worker_heartbeat(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("STREAM_ANALYSIS_DATABASE_URL", raising=False)
    write_worker_heartbeat(tmp_path, "evaluation")
    snapshot = diagnostics_snapshot()
    assert snapshot.evaluation_worker_available is True
    assert snapshot.import_worker_available is False
