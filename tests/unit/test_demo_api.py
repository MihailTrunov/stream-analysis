import json

from fastapi.testclient import TestClient

from market_analysis.api.app import app
from market_analysis.demo.data import load_demo_bars


def test_seeded_demo_is_a_valid_non_research_installation_fixture() -> None:
    bars = load_demo_bars()
    assert len(bars) == 2
    assert all(bar.source_id == "seeded-demo" for bar in bars)
    response = TestClient(app).get("/demo/bars")
    assert response.status_code == 200
    payload = response.json()
    assert payload["non_research_grade"] is True
    assert payload["bars"] == [dict(bar.to_canonical_dict()) for bar in bars]
    assert "events" not in payload


def test_ema_definition_is_ui_discoverable_and_preview_is_server_validated() -> None:
    client = TestClient(app)
    response = client.get("/component-definitions")
    assert response.status_code == 200
    definition = response.json()["components"][0]
    assert definition["component_id"] == "ema"
    assert definition["parameters"][0]["default"] == 45
    assert definition["parameters"][0]["minimum"] == "1"
    proposed = {
        "instrument_id": "US30", "calendar_id": "cal",
        "components": [
            {"component_id": "ema", "component_version": "1", "instance_id": "trend_ema"},
            {"component_id": "ema", "component_version": "1", "instance_id": "fast_ema",
             "parameters": [{"name": "period", "value": 9}]},
        ],
    }
    preview = client.post("/config/preview", json=proposed)
    assert preview.status_code == 200
    result = preview.json()
    assert [item["instance_id"] for item in result["detection_config"]["components"]] == [
        "fast_ema", "trend_ema"
    ]
    periods = [
        item["parameters"][0]["value"] for item in result["detection_config"]["components"]
    ]
    assert periods == [
        9, 45
    ]
    assert len(result["detection_config_hash"]) == 64
    bad = proposed | {"components": [
        {"component_id": "ema", "component_version": "1", "instance_id": "bad",
         "parameters": [{"name": "period", "value": 0}]},
    ]}
    rejected = client.post("/config/preview", json=bad)
    assert rejected.status_code == 422
    duplicate = proposed | {"components": [proposed["components"][0]] * 2}
    assert client.post("/config/preview", json=duplicate).status_code == 422
    unknown = proposed | {"components": [
        {"component_id": "unregistered", "component_version": "1"}
    ]}
    assert client.post("/config/preview", json=unknown).status_code == 422


def test_api_lifecycle_and_demo_access_emit_structured_logs(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STREAM_ANALYSIS_DATA_ROOT", str(tmp_path))
    with TestClient(app) as client:
        assert client.get("/demo/bars").status_code == 200
    records = [
        json.loads(line)
        for line in (tmp_path / "logs" / "application.jsonl").read_text().splitlines()
    ]
    assert [record["message"] for record in records] == [
        "API started",
        "seeded demo bars served",
        "API stopped",
    ]
    assert records[1]["instrument"] == "US30"
    assert records[1]["non_research_grade"] is True
