"""Offline independent-session, lineage, scheduling and availability checks."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from market_analysis.api.app import app
from market_analysis.application.analysis_sessions import AnalysisSessionManager, automatic_policy
from market_analysis.application.replay_catalog import demo_detection_config
from market_analysis.demo.replay_seed import seed_replay_datasets
from market_analysis.domain import Timeframe
from market_analysis.persistence.analysis_sessions import analysis_session_revisions
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.runs import metadata


@pytest.fixture
def client(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    url = f"sqlite+pysqlite:///{tmp_path / 'sessions.sqlite'}"
    monkeypatch.setenv("STREAM_ANALYSIS_DATABASE_URL", url)
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(tmp_path / "data"))
    engine = create_engine(url)
    metadata.create_all(engine)
    with engine.begin() as connection:
        seed_replay_datasets(connection, DatasetStore(tmp_path / "data"))
    from market_analysis.api import analysis
    from market_analysis.api import app as api

    manager = AnalysisSessionManager()
    monkeypatch.setattr(analysis, "analysis_sessions", manager)
    monkeypatch.setattr(api, "analysis_sessions", manager)
    yield TestClient(app)
    engine.dispose()


def request(timeframes: tuple[Timeframe, ...] = (Timeframe.M1,), **changes):  # type: ignore[no-untyped-def]
    return {
        "dataset_revision_id": "offline-replay-us30-v3",
        "instrument_id": "US30",
        "selected_start": "2026-01-05T10:00:00Z",
        "selected_end": "2026-01-05T14:00:00Z",
        "streams": [
            {
                "config": demo_detection_config("US30")
                .model_copy(update={"timeframe": tf})
                .model_dump(mode="json")
            }
            for tf in timeframes
        ],
        **changes,
    }


def launch(client: TestClient, payload=None):  # type: ignore[no-untyped-def]
    response = client.post("/analysis", json=payload or request())
    assert response.status_code == 201, response.text
    return response.json()


def test_two_tabs_have_independent_cursors_and_stop(client: TestClient) -> None:
    one, two = launch(client), launch(client)
    assert one["run_id"] != two["run_id"]
    stepped = client.post(f"/analysis/{one['run_id']}/step").json()
    assert stepped["visible_bars"] == 1
    assert client.get(f"/analysis/{two['run_id']}").json()["visible_bars"] == 0
    assert client.post(f"/analysis/{one['run_id']}/stop").status_code == 200
    assert client.post(f"/analysis/{two['run_id']}/step").status_code == 200


def test_five_minute_step_processes_every_source_and_closed_stream(client: TestClient) -> None:
    state = launch(client, request((Timeframe.M1, Timeframe.M5), chart_timeframe="5m"))
    after = client.post(f"/analysis/{state['run_id']}/step").json()
    assert after["visible_bars"] == 5
    assert after["available_at"] == "2026-01-05T10:05:00Z"
    assert [item["processed"] for item in after["initialization"]] == [65, 13]
    view = client.get(
        f"/analysis/{state['run_id']}/view",
        params={"start": state["selected_start"], "end": state["selected_end"], "timeframe": "5m"},
    ).json()
    assert view["bars"][-1]["timestamp"] == "2026-01-05T10:00:00Z"
    assert len(view["bars"]) == 13
    assert client.get(f"/analysis/{state['run_id']}").json() == after


def test_display_change_does_not_enable_detectors_or_advance_cursor(client: TestClient) -> None:
    state = launch(client)
    for tf in ("1m", "5m", "15m", "30m", "1h", "4h", "1d"):
        response = client.get(
            f"/analysis/{state['run_id']}/view",
            params={
                "start": state["selected_start"],
                "end": state["selected_end"],
                "timeframe": tf,
            },
        )
        assert response.status_code == 200, response.text
    assert client.get(f"/analysis/{state['run_id']}").json() == state


def test_higher_stream_requires_its_own_initialization(client: TestClient) -> None:
    response = client.post("/analysis", json=request((Timeframe.M15,)))
    assert response.status_code == 422
    assert "insufficient 15m initialization" in response.text


def test_history_calculation_is_independent_and_persists_lineage(
    client: TestClient, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    active = launch(client)
    history = launch(client, request((Timeframe.M1, Timeframe.M5), mode="history"))
    assert history["status"] == "completed"
    assert client.get(f"/analysis/{active['run_id']}").json() == active
    import os

    engine = create_engine(os.environ["STREAM_ANALYSIS_DATABASE_URL"])
    with engine.connect() as connection:
        rows = connection.execute(select(analysis_session_revisions)).mappings().all()
    assert len(rows) == 2
    assert all("m1-aggregation-v1" in row["manifest_json"] for row in rows)
    engine.dispose()


@pytest.mark.parametrize(
    "policy,mode,expected",
    [
        ("live", "live", True),
        ("manual", "live", False),
        ("always", "live", True),
        ("live", "history", False),
        ("manual", "history", False),
        ("always", "history", True),
    ],
)
def test_policy_matrix(policy, mode, expected):  # type: ignore[no-untyped-def]
    assert automatic_policy(policy, mode) is expected


def test_live_dispatch_runs_only_eligible_detectors_without_any_chart() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from typing import cast

    from sqlalchemy import Connection

    from market_analysis.application.analysis_sessions import DetectorStream, dispatch_candle
    from market_analysis.demo.replay_seed import demo_replay_bars
    from market_analysis.domain.aggregation import AnalyticalCandle

    calls: list[str] = []
    streams = [
        cast(
            DetectorStream,
            SimpleNamespace(
                runtime=SimpleNamespace(config=SimpleNamespace(timeframe=Timeframe.M1)),
                policy=policy,
                process=lambda connection, candle, policy=policy: calls.append(policy),
            ),
        )
        for policy in ("live", "manual", "always")
    ]
    source = demo_replay_bars("US30")[0]
    candle = AnalyticalCandle(
        source,
        datetime(2026, 1, 5, 12, 1, tzinfo=UTC),
        source.timestamp,
        datetime(2026, 1, 5, 12, 1, tzinfo=UTC),
        1,
    )
    dispatch_candle(cast(Connection, None), candle, streams, "live", automatic=True)
    assert calls == ["live", "always"]
    calls.clear()
    dispatch_candle(cast(Connection, None), candle, streams, "live")
    assert calls == ["live", "always"]
    calls.clear()
    dispatch_candle(cast(Connection, None), candle, streams, "replay")
    assert calls == ["live", "manual", "always"]


def test_recorded_analysis_events_remain_reviewable(client: TestClient) -> None:
    state = launch(client)
    client.post(f"/analysis/{state['run_id']}/next-event")
    view = client.get(
        f"/analysis/{state['run_id']}/view",
        params={"start": state["selected_start"], "end": state["selected_end"]},
    ).json()
    assert view["events"]
    event = view["events"][-1]
    response = client.post(
        f"/replay/{event['run_id']}/annotations",
        json={
            "target_kind": "event",
            "event_id": event["event_id"],
            "label": "correct",
            "note": "review",
        },
    )
    assert response.status_code == 201, response.text
    records = client.get(f"/analysis/{state['run_id']}/annotations").json()["annotations"]
    assert records[0]["event_id"] == event["event_id"]


def test_automatic_history_respects_per_detector_policy(client: TestClient) -> None:
    payload = request(mode="history", automatic=True)
    payload["streams"][0]["policies"] = {"RANGE_COMPRESSION_V1": "manual"}
    manual = launch(client, payload)
    assert manual["initialization"] == []
    payload["streams"][0]["policies"] = {"RANGE_COMPRESSION_V1": "always"}
    auto = launch(client, payload)
    assert auto["initialization"][0]["processed"] == 300


def test_apply_preview_validates_component_wiring_without_creating_a_session(
    client: TestClient,
) -> None:
    config = client.get("/analysis-defaults", params={"instrument_id": "US30"}).json()[
        "detection_config"
    ]
    assert client.post("/analysis-config/preview", json=config).status_code == 200
    for component in config["components"]:
        if component["component_id"] == "atr":
            component["enabled"] = False
    invalid = client.post("/analysis-config/preview", json=config)
    assert invalid.status_code == 422
    assert client.get("/analysis/active").json()["sessions"] == []
