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
from market_analysis.persistence.replay_runs import ReplayStatus, load_replay_run
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


def test_chart_payload_only_exposes_processed_selected_bars(client: TestClient):
    launched = client.post("/replay", json=_launch()).json()
    run_id = launched["run_id"]
    path = f"/replay/{run_id}/bars"
    params = {
        "start": DEMO_SELECTED_START.isoformat(),
        "end": DEMO_END.isoformat(),
        "limit": 2,
    }
    assert client.get(path, params=params).json()["bars"] == []
    first = client.post(f"/replay/{run_id}/step")
    assert first.status_code == 200, first.text
    assert first.json()["visible_bars"] == 1
    assert first.json()["warmup_processed"] == 11
    bars = client.get(path, params=params).json()["bars"]
    assert len(bars) == 1
    assert bars[0]["timestamp"] == DEMO_SELECTED_START.isoformat().replace("+00:00", "Z")
    assert "2026-01-05T12:12" not in str(bars)
    assert client.post(f"/replay/{run_id}/step").json()["visible_bars"] == 2
    assert len(client.get(path, params=params).json()["bars"]) == 2
    final = client.post(f"/replay/{run_id}/step")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == "completed"
    bounded = client.get(path, params=params).json()["bars"]
    assert len(bounded) == 2
    last_visible_time = (DEMO_END - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    assert bounded[-1]["timestamp"] == last_visible_time


def test_chart_rejects_future_or_unbounded_viewports(client: TestClient):
    launched = client.post("/replay", json=_launch()).json()
    run_id = launched["run_id"]
    path = f"/replay/{run_id}/bars"
    assert client.get(path, params={
        "start": DEMO_SELECTED_START.isoformat(),
        "end": (DEMO_END + timedelta(minutes=1)).isoformat(),
    }).status_code == 422
    assert client.get(path, params={
        "start": DEMO_SELECTED_START.isoformat(),
        "end": DEMO_END.isoformat(),
        "limit": 501,
    }).status_code == 422


def test_play_pause_ticks_and_end_state_are_causal(client: TestClient):
    run_id = client.post("/replay", json=_launch()).json()["run_id"]
    assert client.post(f"/replay/{run_id}/play").json()["status"] == "running"
    first = client.post(f"/replay/{run_id}/tick").json()
    assert first["visible_bars"] == 1
    assert first["status"] == "running"
    assert client.post(f"/replay/{run_id}/pause").json()["status"] == "paused"
    assert client.post(f"/replay/{run_id}/play").json()["status"] == "running"
    assert client.post(f"/replay/{run_id}/tick").json()["visible_bars"] == 2
    last = client.post(f"/replay/{run_id}/tick").json()
    assert last["status"] == "completed"
    assert last["has_next"] is False
    assert client.post(f"/replay/{run_id}/tick").status_code == 409
    assert client.post(f"/replay/{run_id}/play").status_code == 409


def test_next_event_stops_at_first_visible_transition(client: TestClient):
    run_id = client.post("/replay", json=_launch()).json()["run_id"]
    next_event = client.post(f"/replay/{run_id}/next-event")
    assert next_event.status_code == 200, next_event.text
    state = next_event.json()
    assert state["status"] == "paused"
    assert state["visible_bars"] == 2
    assert state["navigation"]["stopped_on_event"] is True
    assert state["navigation"]["matched_event"]["trigger_id"] == "persistence_confirmed"
    assert state["events"][0]["detection_time"] == state["cursor_time"]
    assert client.post(f"/replay/{run_id}/next-event").json()["status"] == "completed"


def test_reset_and_seek_make_fresh_runs_with_same_observable_output(client: TestClient):
    original = client.post("/replay", json=_launch()).json()
    first = client.post(f"/replay/{original['run_id']}/step").json()
    target = (DEMO_SELECTED_START + timedelta(minutes=1)).isoformat()
    sought = client.post(
        f"/replay/{original['run_id']}/seek", json={"target": target}
    )
    assert sought.status_code == 200, sought.text
    state = sought.json()
    assert state["run_id"] != original["run_id"]
    assert state["status"] == "paused"
    assert state["visible_bars"] == 2
    assert state["detection_config_hash"] == original["detection_config_hash"]
    assert state["events"][0]["trigger_id"] == "persistence_confirmed"
    assert client.get(f"/replay/{original['run_id']}").status_code == 404
    assert client.post(f"/replay/{original['run_id']}/step").status_code == 409
    reset = client.post(f"/replay/{state['run_id']}/reset")
    assert reset.status_code == 200, reset.text
    fresh = reset.json()
    assert fresh["run_id"] not in (state["run_id"], original["run_id"])
    assert fresh["cursor_index"] == -1
    assert fresh["visible_bars"] == 0
    assert fresh["events"] == []
    repeat = client.post(f"/replay/{fresh['run_id']}/step").json()
    assert repeat["cursor_time"] == first["cursor_time"]
    assert repeat["events"] == first["events"]
    engine = create_engine(os.environ["STREAM_ANALYSIS_DATABASE_URL"])
    from uuid import UUID
    with engine.connect() as connection:
        assert load_replay_run(connection, UUID(original["run_id"])).status is ReplayStatus.ABORTED
        assert load_replay_run(connection, UUID(state["run_id"])).status is ReplayStatus.ABORTED
    engine.dispose()


def test_seek_rejects_non_bar_target_without_replacing_active_run(client: TestClient):
    run_id = client.post("/replay", json=_launch()).json()["run_id"]
    wrong = (DEMO_SELECTED_START + timedelta(seconds=30)).isoformat()
    assert client.post(f"/replay/{run_id}/seek", json={"target": wrong}).status_code == 422
    assert client.get(f"/replay/{run_id}").json()["status"] == "created"


def test_manual_and_play_ticks_emit_same_event_sequence(client: TestClient):
    manual_id = client.post("/replay", json=_launch()).json()["run_id"]
    manual: list[tuple[str, list[tuple[str, str]]]] = []
    while True:
        state = client.post(f"/replay/{manual_id}/step").json()
        manual.append((
            state["cursor_time"],
            [(event["trigger_id"], event["to_state"]) for event in state["events"]],
        ))
        if not state["has_next"]:
            break
    client.post(f"/replay/{manual_id}/stop")
    played_id = client.post("/replay", json=_launch()).json()["run_id"]
    assert client.post(f"/replay/{played_id}/play").json()["status"] == "running"
    played: list[tuple[str, list[tuple[str, str]]]] = []
    while True:
        state = client.post(f"/replay/{played_id}/tick").json()
        played.append((
            state["cursor_time"],
            [(event["trigger_id"], event["to_state"]) for event in state["events"]],
        ))
        if not state["has_next"]:
            break
    assert played == manual
    assert [events for _, events in played if events] == [
        [("persistence_confirmed", "ACTIVE")],
        [("compression_released", "COMPLETED")],
    ]


def test_local_browser_can_reattach_without_replaying_or_changing_run_id(client: TestClient):
    assert client.get("/replay/active").json()["active"] is None
    launched = client.post("/replay", json=_launch()).json()
    stepped = client.post(f"/replay/{launched['run_id']}/step").json()
    attached = client.get("/replay/active").json()["active"]
    assert attached["run_id"] == launched["run_id"]
    assert attached["cursor_index"] == stepped["cursor_index"]
    assert attached["visible_bars"] == 1
