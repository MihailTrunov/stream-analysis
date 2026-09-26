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
