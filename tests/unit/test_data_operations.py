"""Safety guards around the manual Compose backup/restore entry points."""

from __future__ import annotations

from pathlib import Path

import pytest

from market_analysis.persistence.backup_archive import BackupArchiveError
from scripts import _data_operations as operations


def test_restore_refuses_existing_root_before_archive_or_docker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    root.mkdir()
    (root / "existing.txt").write_text("keep")
    monkeypatch.setattr(operations, "data_root", lambda: root)

    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("restore crossed the preflight boundary")

    monkeypatch.setattr(operations, "verify_archive", unexpected)
    monkeypatch.setattr(operations, "compose", unexpected)
    with pytest.raises(BackupArchiveError, match="empty data root"):
        operations.restore(str(tmp_path / "archive.zip"))
    assert (root / "existing.txt").read_text() == "keep"


def test_restore_refuses_nonempty_database_before_extraction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    root = tmp_path / "empty-data"
    monkeypatch.setattr(operations, "data_root", lambda: root)
    monkeypatch.setattr(operations, "verify_archive", lambda path: {"postgres.dump": {}})
    calls: list[tuple[str, ...]] = []

    def fake_compose(path: Path, *args: str, **kwargs: object) -> str:
        calls.append(args)
        if args[:2] == ("ps", "--status"):
            return "" if len(calls) == 1 else "postgres"
        if args[:2] == ("exec", "-T"):
            return "1"
        return ""

    monkeypatch.setattr(operations, "compose", fake_compose)
    with pytest.raises(BackupArchiveError, match="empty target PostgreSQL database"):
        operations.restore(str(tmp_path / "archive.zip"))
    assert not any(args and args[0] == "build" for args in calls)
    assert not (root / ".restore-postgres.dump").exists()
