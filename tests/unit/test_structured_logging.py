from __future__ import annotations

import io
import json
import logging
import os
import stat
from datetime import UTC, datetime
from decimal import Decimal
from logging.handlers import RotatingFileHandler

from market_analysis.application.logging import (
    ResearchLogger,
    StructuredJsonFormatter,
    configure_logging,
    research_logger,
)
from market_analysis.domain import Bar, Timeframe


def _capture(logger: ResearchLogger) -> tuple[io.StringIO, logging.StreamHandler]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredJsonFormatter())
    logger.logger.addHandler(handler)
    return stream, handler


def test_structured_record_contains_research_context() -> None:
    base = configure_logging(enable_file=False)
    logger = research_logger(run_id="run-1", instrument="US30", component="ema")
    stream, handler = _capture(logger)
    logger.info("component updated", extra={"bars_processed": 42})
    base.removeHandler(handler)
    payload = json.loads(stream.getvalue())
    assert payload["run_id"] == "run-1"
    assert payload["instrument"] == "US30"
    assert payload["component"] == "ema"
    assert payload["bars_processed"] == 42


def test_sensitive_keys_and_environment_secret_values_are_redacted(monkeypatch) -> None:
    monkeypatch.setenv("OANDA_TOKEN", "top-secret-token")
    base = configure_logging(enable_file=False)
    logger = research_logger(run_id="run-2")
    stream, handler = _capture(logger)
    logger.error(
        "provider rejected top-secret-token",
        extra={"access_token": "top-secret-token", "nested": {"password": "hidden"}},
    )
    base.removeHandler(handler)
    text = stream.getvalue()
    assert "top-secret-token" not in text
    assert "hidden" not in text
    assert text.count("[REDACTED]") >= 3


def test_unknown_extra_values_do_not_break_json_formatting() -> None:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord("market_analysis", logging.INFO, __file__, 1, "ok", (), None)
    record.custom = Decimal("1.2")
    payload = json.loads(formatter.format(record))
    assert payload["custom"] == "1.2"


def test_reconfiguration_is_idempotent_and_file_logging_is_bounded(tmp_path) -> None:
    first = configure_logging(data_root=tmp_path, enable_file=True)
    second = configure_logging(data_root=tmp_path, enable_file=True)
    assert first is second
    assert len(second.handlers) == 2
    second.info("hello")
    assert (tmp_path / "logs" / "application.jsonl").exists()


def test_rotated_log_remains_private(tmp_path) -> None:
    logger = configure_logging(data_root=tmp_path, enable_file=True)
    handler = next(item for item in logger.handlers if isinstance(item, RotatingFileHandler))
    handler.maxBytes = 128
    original_umask = os.umask(0o022)
    try:
        logger.info("first log record")
        logger.info("x" * 200)
    finally:
        os.umask(original_umask)

    active_log = tmp_path / "logs" / "application.jsonl"
    assert stat.S_IMODE(active_log.stat().st_mode) == 0o600


def test_logging_does_not_mutate_domain_state() -> None:
    bar = Bar(
        "US30", Timeframe.M1, datetime(2026, 1, 1, tzinfo=UTC),
        Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"),
    )
    before = hash(bar)
    configure_logging(enable_file=False)
    research_logger(run_id="run").info("bar", extra={"bar": bar})
    assert hash(bar) == before


def test_non_finite_float_and_set_extras_remain_valid_deterministic_json() -> None:
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord("market_analysis", logging.INFO, __file__, 1, "ok", (), None)
    record.non_finite = float("nan")
    record.tags = {"b", "a"}
    payload = json.loads(formatter.format(record))
    assert payload["non_finite"] == "nan"
    assert payload["tags"] == ["a", "b"]
