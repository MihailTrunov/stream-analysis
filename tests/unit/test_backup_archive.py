from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from market_analysis.persistence.backup_archive import (
    BACKUP_FORMAT_VERSION,
    BackupArchiveError,
    create_archive,
    extract_verified_archive,
    require_empty_data_root,
    verify_archive,
)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    (source / "postgres").mkdir(parents=True)
    (source / "postgres" / "PG_VERSION").write_text("16")
    (source / "datasets/rev-1").mkdir(parents=True)
    (source / "datasets/rev-1/bars.parquet").write_bytes(b"parquet bytes")
    (source / "artifacts").mkdir()
    (source / "artifacts/result.json").write_text('{"count":1}')
    (source / "imports/job-1").mkdir(parents=True)
    (source / "imports/job-1/batch-0.jsonl").write_text('{"bar":1}\n')
    dump = tmp_path / "dump.bin"
    dump.write_bytes(b"PGDMP\x00example")
    archive = tmp_path / "backup.zip"
    return source, dump, archive


def test_create_verify_extract_and_exclude_postgres_volume(tmp_path: Path) -> None:
    source, dump, archive = _fixture(tmp_path)
    assert create_archive(source, dump, archive) == archive
    files = verify_archive(archive)
    assert set(files) == {
        "postgres.dump",
        "data/datasets/rev-1/bars.parquet",
        "data/artifacts/result.json",
        "data/imports/job-1/batch-0.jsonl",
    }
    with zipfile.ZipFile(archive) as opened:
        assert json.loads(opened.read("backup-manifest.json"))["format_version"] == (
            BACKUP_FORMAT_VERSION
        )
    target = tmp_path / "target"
    require_empty_data_root(target)
    target.mkdir()
    extracted_dump = extract_verified_archive(archive, target, files)
    assert extracted_dump.read_bytes() == dump.read_bytes()
    assert (target / "datasets/rev-1/bars.parquet").read_bytes() == b"parquet bytes"
    assert (target / "imports/job-1/batch-0.jsonl").read_text() == '{"bar":1}\n'
    assert not (target / "postgres").exists()
    with pytest.raises(BackupArchiveError, match="empty data root"):
        require_empty_data_root(target)
    with pytest.raises(FileExistsError):
        create_archive(source, dump, archive)
    assert verify_archive(archive) == files


def test_corrupt_archive_rejected_before_extraction(tmp_path: Path) -> None:
    source, dump, archive = _fixture(tmp_path)
    create_archive(source, dump, archive)
    with zipfile.ZipFile(archive, "a") as opened:
        opened.writestr("data/exports/extra.csv", b"unlisted")
    with pytest.raises(BackupArchiveError, match="does not match"):
        verify_archive(archive)
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize(
    "name",
    ["../escape", "data/../../escape", "/absolute", "data/exports/x/../y"],
)
def test_malicious_archive_member_rejected(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "malicious.zip"
    manifest = {
        "format_version": BACKUP_FORMAT_VERSION,
        "files": {
            "postgres.dump": {"size": 1, "sha256": "a" * 64},
            name: {"size": 1, "sha256": "a" * 64},
        },
    }
    with zipfile.ZipFile(archive, "w") as opened:
        opened.writestr("postgres.dump", b"x")
        opened.writestr(name, b"x")
        opened.writestr("backup-manifest.json", json.dumps(manifest))
    with pytest.raises(BackupArchiveError, match="unsafe archive member"):
        verify_archive(archive)


def test_input_symlink_and_nonempty_restore_target_rejected(tmp_path: Path) -> None:
    source, dump, archive = _fixture(tmp_path)
    (source / "exports").mkdir()
    (source / "exports/link").symlink_to(source / "datasets/rev-1/bars.parquet")
    with pytest.raises(BackupArchiveError, match="symlink"):
        create_archive(source, dump, archive)
    target = tmp_path / "target"
    target.mkdir()
    (target / "existing").write_text("do not overwrite")
    with pytest.raises(BackupArchiveError, match="empty data root"):
        require_empty_data_root(target)
    assert (target / "existing").read_text() == "do not overwrite"


def test_possible_credentials_are_not_archived(tmp_path: Path) -> None:
    source, dump, archive = _fixture(tmp_path)
    (source / "exports").mkdir()
    (source / "exports/.env").write_text("secret")
    with pytest.raises(BackupArchiveError, match="possible credential"):
        create_archive(source, dump, archive)
    assert not archive.exists()
