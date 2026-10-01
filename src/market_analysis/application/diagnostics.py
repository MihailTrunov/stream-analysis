from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

_HEARTBEAT_MAX_AGE = timedelta(seconds=30)


def database_status(database_url: str | None) -> tuple[bool, str | None]:
    """Check database reachability and read the applied migration, without changing it."""
    if not database_url:
        return False, None
    engine = None
    try:
        options = {"connect_args": {"connect_timeout": 2}} if database_url.startswith(
            "postgresql"
        ) else {}
        engine = create_engine(database_url, **options)
        with engine.connect() as connection:
            if engine.dialect.name == "sqlite":
                exists = connection.scalar(text(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='alembic_version'"
                ))
            else:
                exists = connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
            if not exists:
                return True, None
            version = connection.scalar(text("SELECT version_num FROM alembic_version"))
            return True, str(version) if version is not None else None
    except SQLAlchemyError:
        return False, None
    finally:
        if engine is not None:
            engine.dispose()


def write_worker_heartbeat(
    data_root: str | Path,
    kind: str,
    *,
    now: datetime | None = None,
) -> None:
    _validate_kind(kind)
    root = Path(data_root).expanduser().resolve() / "runtime"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{kind}-worker.json"
    temporary = root / f".{kind}-worker-{os.getpid()}.tmp"
    current = now or datetime.now(UTC)
    try:
        with temporary.open("w", encoding="utf-8") as file:
            json.dump({"updated_at": current.astimezone(UTC).isoformat()}, file)
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_worker_heartbeat(
    data_root: str | Path,
    kind: str,
    *,
    now: datetime | None = None,
) -> bool:
    _validate_kind(kind)
    path = Path(data_root).expanduser().resolve() / "runtime" / f"{kind}-worker.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        updated_at = datetime.fromisoformat(payload["updated_at"])
        if updated_at.tzinfo is None:
            return False
        age = (now or datetime.now(UTC)) - updated_at.astimezone(UTC)
        return timedelta(0) <= age <= _HEARTBEAT_MAX_AGE
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _validate_kind(kind: str) -> None:
    if kind not in {"evaluation", "import"}:
        raise ValueError(f"unsupported worker kind: {kind}")
