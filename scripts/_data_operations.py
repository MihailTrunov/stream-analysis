"""Docker Compose orchestration for explicit local PostgreSQL backup and restore."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from market_analysis.persistence.backup_archive import (  # noqa: E402
    BackupArchiveError,
    create_archive,
    extract_verified_archive,
    require_empty_data_root,
    verify_archive,
)


def data_root() -> Path:
    value = os.environ.get("STREAM_ANALYSIS_DATA_ROOT", str(PROJECT_ROOT / "data"))
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if path.is_symlink():
        raise BackupArchiveError("data root must not be a symlink")
    path = path.resolve()
    if path in {Path("/"), Path.home().resolve(), PROJECT_ROOT}:
        raise BackupArchiveError("data root must be a dedicated directory")
    return path


def _environment(root: Path) -> dict[str, str]:
    return {**os.environ, "STREAM_ANALYSIS_DATA_ROOT": str(root)}


def compose(
    root: Path, *arguments: str, stdin_path: Path | None = None,
    stdout_path: Path | None = None,
) -> str:
    command = ["docker", "compose", *arguments]
    with (stdin_path.open("rb") if stdin_path else open(os.devnull, "rb")) as source:
        if stdout_path is not None:
            with stdout_path.open("xb") as output:
                subprocess.run(
                    command, cwd=PROJECT_ROOT, env=_environment(root), stdin=source,
                    stdout=output, check=True,
                )
            return ""
        result = subprocess.run(
            command, cwd=PROJECT_ROOT, env=_environment(root), stdin=source,
            stdout=subprocess.PIPE, check=True, text=True,
        )
        return result.stdout.strip()


def require_stopped_application(root: Path, *, require_postgres: bool) -> None:
    active = set(compose(root, "ps", "--status", "running", "--services").splitlines())
    if active - {"postgres"}:
        raise BackupArchiveError(
            "stop API, workers, web and other application services before backup/restore"
        )
    if require_postgres and "postgres" not in active:
        raise BackupArchiveError("start PostgreSQL only: docker compose up -d postgres")


def backup(archive_arg: str | None) -> Path:
    root = data_root()
    if not root.is_dir():
        raise BackupArchiveError("backup data root does not exist")
    require_stopped_application(root, require_postgres=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = (Path(archive_arg).expanduser() if archive_arg else
               PROJECT_ROOT / "backups" / f"stream-analysis-{stamp}.zip")
    if not archive.is_absolute():
        archive = PROJECT_ROOT / archive
    with tempfile.TemporaryDirectory(prefix="stream-analysis-backup-") as directory:
        dump = Path(directory) / "postgres.dump"
        compose(
            root, "exec", "-T", "postgres", "pg_dump", "-U", "market_analysis",
            "-d", "market_analysis", "-Fc", stdout_path=dump,
        )
        return create_archive(root, dump, archive)


def restore(archive_arg: str) -> None:
    root = data_root()
    archive = Path(archive_arg).expanduser().resolve()
    require_empty_data_root(root)
    files = verify_archive(archive)
    require_stopped_application(root, require_postgres=False)
    root.mkdir(parents=True, exist_ok=True)
    compose(root, "up", "-d", "--wait", "postgres")
    require_stopped_application(root, require_postgres=True)
    table_count = compose(
        root, "exec", "-T", "postgres", "psql", "-U", "market_analysis",
        "-d", "market_analysis", "-Atqc",
        "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%'",
    )
    if table_count != "0":
        raise BackupArchiveError("restore requires an empty target PostgreSQL database")
    dump = extract_verified_archive(archive, root, files)
    compose(
        root, "exec", "-T", "postgres", "pg_restore", "-U", "market_analysis",
        "-d", "market_analysis", "--no-owner", "--no-acl", "--exit-on-error",
        stdin_path=dump,
    )
    compose(root, "build", "api")
    print(compose(root, "run", "--rm", "--no-deps", "api", "python", "-m",
                  "market_analysis.persistence.verify_datasets"))
    dump.unlink()
