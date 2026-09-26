from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_RESERVED = frozenset(logging.makeLogRecord({}).__dict__)
_SENSITIVE_MARKERS = (
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "authorization",
)


class StructuredJsonFormatter(logging.Formatter):
    """Stable JSON-line formatter for local operational diagnostics."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = (
            datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
        payload: dict[str, object] = {
            "timestamp": timestamp,
            "severity": record.levelname,
            "logger": record.name,
            "message": _redact_text(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = _safe_value(key, value)
        if record.exc_info:
            payload["exception"] = _redact_text(self.formatException(record.exc_info))
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )


class ResearchLogger(logging.LoggerAdapter[logging.Logger]):
    """Logger adapter carrying reproducibility and component context."""

    def process(self, msg: object, kwargs: Any) -> tuple[object, Any]:
        supplied = dict(kwargs.get("extra") or {})
        supplied.update(self.extra or {})
        kwargs["extra"] = supplied
        return msg, kwargs


def configure_logging(
    *,
    data_root: str | Path | None = None,
    level: int = logging.INFO,
    enable_file: bool = True,
) -> logging.Logger:
    """Configure bounded local JSON logging and return the application logger."""
    configured_root = data_root or os.getenv("STREAM_ANALYSIS_DATA_ROOT", "./data")
    root = Path(configured_root).expanduser().resolve()
    logger = logging.getLogger("market_analysis")
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = StructuredJsonFormatter()
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.setLevel(level)
    logger.addHandler(stream)

    if enable_file:
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        rotating = RotatingFileHandler(
            log_dir / "application.jsonl",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        rotating.setFormatter(formatter)
        rotating.setLevel(level)
        try:
            os.chmod(log_dir / "application.jsonl", 0o600)
        except OSError:
            pass
        logger.addHandler(rotating)
    return logger


def research_logger(
    *,
    run_id: str | None = None,
    dataset_id: str | None = None,
    instrument: str | None = None,
    component: str | None = None,
    detector_id: str | None = None,
    build_id: str | None = None,
) -> ResearchLogger:
    context = {
        key: value
        for key, value in {
            "run_id": run_id,
            "dataset_id": dataset_id,
            "instrument": instrument,
            "component": component,
            "detector_id": detector_id,
            "build_id": build_id,
        }.items()
        if value is not None
    }
    return ResearchLogger(logging.getLogger("market_analysis"), context)


def _safe_value(key: str, value: object) -> object:
    if _is_sensitive_key(key):
        return "[REDACTED]"
    if value is None or isinstance(value, str | int | bool):
        return _redact_text(value) if isinstance(value, str) else value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): _safe_value(str(item_key), item_value)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list | tuple):
        return [_safe_value(key, item) for item in value]
    if isinstance(value, set | frozenset):
        normalized = [_safe_value(key, item) for item in value]
        return sorted(normalized, key=repr)
    return _redact_text(str(value))


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return any(marker in lowered for marker in _SENSITIVE_MARKERS)


def _redact_text(value: str) -> str:
    redacted = value
    for key, secret in os.environ.items():
        if secret and len(secret) >= 4 and _is_sensitive_key(key):
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted
