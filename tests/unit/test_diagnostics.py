from market_analysis.api.app import diagnostics_snapshot


def test_diagnostics_does_not_expose_oanda_token(monkeypatch) -> None:
    monkeypatch.setenv("OANDA_TOKEN", "super-secret")
    snapshot = diagnostics_snapshot()
    payload = snapshot.model_dump()
    assert payload["oanda_import_available"] is True
    assert "super-secret" not in repr(payload)
