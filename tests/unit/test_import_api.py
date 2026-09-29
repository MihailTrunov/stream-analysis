from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from market_analysis.api.app import app
from market_analysis.persistence.import_jobs import (
    ImportStatus,
    claim_import_job,
    interrupt_running_imports,
    load_import_job,
)
from market_analysis.persistence.market_data import load_instrument
from market_analysis.persistence.runs import metadata


def _configure(monkeypatch, tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'api.sqlite'}"
    engine = create_engine(url)
    metadata.create_all(engine)
    engine.dispose()
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("OANDA_KEY", "private-test-token")
    monkeypatch.setenv("OANDA_ACCOUNT", "private-account")
    monkeypatch.setenv("OANDA_ENV", "live")
    monkeypatch.setenv("OANDA_REGION", "UK")
    return url


def _payload(dataset_id: str = "research-us30") -> dict[str, object]:
    return {
        "dataset_id": dataset_id,
        "instrument_id": "US30",
        "start": "2026-09-28T14:00:00Z",
        "end": "2026-09-28T14:03:00Z",
    }


def test_create_status_list_and_explicit_resume(monkeypatch, tmp_path: Path) -> None:
    url = _configure(monkeypatch, tmp_path)
    with TestClient(app) as client:
        created = client.post("/imports", json=_payload())
        assert created.status_code == 202
        job_id = created.json()["job_id"]
        assert created.json()["status"] == "queued"
        assert created.json()["bar_count"] == 0
        assert "private-test-token" not in created.text
        assert "private-account" not in created.text
        assert client.get(f"/imports/{job_id}").json() == created.json()
        assert client.get("/imports").json()[0]["job_id"] == job_id
        assert client.post("/imports", json=_payload()).json()["job_id"] == job_id
        assert client.post("/imports", json=_payload("other")).status_code == 409
        assert client.post(f"/imports/{job_id}/resume").status_code == 409

        engine = create_engine(url)
        with engine.begin() as connection:
            instrument = load_instrument(connection, "US30")
            assert instrument is not None
            assert instrument.price_precision == 1
            assert instrument.point_size == 1
            assert instrument.provider_symbol("oanda", environment="live") == "US30_USD"
            assert claim_import_job(connection, at=datetime.now(UTC)) is not None
            assert interrupt_running_imports(connection, at=datetime.now(UTC)) == 1
            assert load_import_job(connection, job_id).status is ImportStatus.INTERRUPTED
        engine.dispose()

        monkeypatch.setenv("OANDA_ACCOUNT", "other-account")
        assert client.post(f"/imports/{job_id}/resume").status_code == 409
        monkeypatch.setenv("OANDA_ACCOUNT", "private-account")
        resumed = client.post(f"/imports/{job_id}/resume")
        assert resumed.status_code == 202
        assert resumed.json()["status"] == "queued"
        assert client.post(f"/imports/{job_id}/resume").status_code == 409


def test_import_api_rejects_missing_config_unsupported_and_out_of_coverage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, tmp_path)
    with TestClient(app) as client:
        monkeypatch.delenv("OANDA_KEY")
        assert client.post("/imports", json=_payload()).status_code == 503
        monkeypatch.setenv("OANDA_KEY", "private-test-token")
        assert (
            client.post(
                "/imports",
                json=_payload()
                | {
                    "instrument_id": "EURUSD",
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/imports",
                json=_payload()
                | {
                    "start": "2026-09-30T14:00:00Z",
                    "end": "2026-09-30T14:03:00Z",
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/imports",
                json=_payload()
                | {
                    "end": "2026-09-28T13:00:00Z",
                },
            ).status_code
            == 422
        )
        assert client.get("/imports/not-a-job").status_code == 404
