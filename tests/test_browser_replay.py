"""Offline browser launch preflight and pinned run identity."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from market_analysis.api.app import app
from market_analysis.application import browser_replay as replay_module
from market_analysis.demo.replay_seed import DEMO_END, DEMO_SELECTED_START, seed_replay_datasets
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.market_data import dataset_revisions
from market_analysis.persistence.runs import metadata


@pytest.fixture
def client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    url = f"sqlite+pysqlite:///{tmp_path / 'replay.sqlite'}"
    data_root = tmp_path / "data"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(data_root))
    engine = create_engine(url)
    metadata.create_all(engine)
    with engine.begin() as connection:
        seed_replay_datasets(connection, DatasetStore(data_root))
    engine.dispose()
    replay_module.browser_replay = replay_module.BrowserReplayManager()
    from market_analysis.api import app as api_module
    monkeypatch.setattr(api_module, "browser_replay", replay_module.browser_replay)
    return TestClient(app)


def _launch(instrument: str = "US30") -> dict[str, object]:
    return {
        "dataset_revision_id": f"offline-replay-{instrument.lower()}-v1",
        "instrument_id": instrument,
        "timeframe": "1m",
        "selected_start": DEMO_SELECTED_START.isoformat(),
        "selected_end": DEMO_END.isoformat(),
    }


@pytest.mark.parametrize("instrument", ["US30", "DAX"])
def test_offline_launch_pins_verified_run_without_future_bars(client: TestClient, instrument: str):
    sources = client.get("/replay/sources")
    assert sources.status_code == 200
    assert {item["instrument_id"] for item in sources.json()["sources"]} == {"US30", "DAX"}
    response = client.post("/replay", json=_launch(instrument))
    assert response.status_code == 201, response.text
    state = response.json()
    assert state["status"] == "created"
    assert state["cursor_index"] == -1
    assert state["visible_bars"] == 0
    assert state["warmup_required"] == 11
    assert state["events"] == []
    assert len(state["detection_config_hash"]) == 64
    assert client.post(f"/replay/{state['run_id']}/stop").status_code == 200


def test_launch_rejects_insufficient_warmup_and_overlapping_run(client: TestClient):
    short = _launch()
    short["selected_start"] = (DEMO_SELECTED_START - timedelta(minutes=1)).isoformat()
    response = client.post("/replay", json=short)
    assert response.status_code == 422
    assert "insufficient warm-up" in response.json()["detail"]
    first = client.post("/replay", json=_launch()).json()
    response = client.post("/replay", json=_launch())
    assert response.status_code == 409
    assert "active walkthrough" in response.json()["detail"]
    client.post(f"/replay/{first['run_id']}/stop")


def test_launch_rejects_missing_revision_and_outside_calendar(client: TestClient):
    missing = _launch()
    missing["dataset_revision_id"] = "missing"
    assert client.post("/replay", json=missing).status_code == 422
    invalid = _launch()
    invalid["selected_start"] = datetime(2026, 1, 5, 9, tzinfo=UTC).isoformat()
    assert client.post("/replay", json=invalid).status_code == 422


def test_edited_parameters_preview_and_launch_pin_same_hash(client: TestClient):
    default = client.get("/replay/config-default?instrument_id=US30")
    assert default.status_code == 200
    config = default.json()["detection_config"]
    atr = next(item for item in config["components"] if item["component_id"] == "atr")
    next(item for item in atr["parameters"] if item["name"] == "period")["value"] = 1
    preview = client.post("/config/preview", json=config)
    assert preview.status_code == 200, preview.text
    assert preview.json()["detection_config_hash"] != default.json()["detection_config_hash"]
    request = _launch()
    request["detection_config"] = config
    launched = client.post("/replay", json=request)
    assert launched.status_code == 201, launched.text
    assert launched.json()["detection_config_hash"] == preview.json()["detection_config_hash"]


def test_launch_fails_closed_on_corrupted_revision(client: TestClient):
    parquet = (
        Path(os.environ["STREAM_ANALYSIS_DATA_ROOT"])
        / "datasets" / "offline-replay-us30-v1" / "bars.parquet"
    )
    with parquet.open("ab") as stream:
        stream.write(b"tampered")
    response = client.post("/replay", json=_launch())
    assert response.status_code == 422
    assert "checksum" in response.json()["detail"].lower()


def test_launch_fails_closed_on_unregistered_calendar_version(client: TestClient):
    engine = create_engine(os.environ["STREAM_ANALYSIS_DATABASE_URL"])
    with engine.begin() as connection:
        connection.execute(
            dataset_revisions.update()
            .where(dataset_revisions.c.dataset_revision_id == "offline-replay-us30-v1")
            .values(calendar_version="unregistered-calendar")
        )
    engine.dispose()
    response = client.post("/replay", json=_launch())
    assert response.status_code == 422
    assert "calendar" in response.json()["detail"].lower()
